"""
Recording Handler - Endpoints para gestionar grabaciones

Según RECORDING_INTEGRATION_GUIDE.md
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Request, Query
from typing import Optional, Dict, Any
import structlog
import httpx
import asyncpg
import uuid
import json
from io import BytesIO
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from datetime import datetime
from pydantic import BaseModel, Field

from ..config import settings
from ..middleware.auth import get_current_user, UserContext, validate_token
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .hitl import get_user_queue

logger = structlog.get_logger()
router = APIRouter(prefix="/api/recordings", tags=["recordings"])
security = HTTPBearer(auto_error=False)


async def get_current_user_with_query_fallback(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    token: Optional[str] = Query(None, alias="token"),
) -> UserContext:
    """
    Get current user with fallback to query string token.
    
    This allows the token to be passed either in the Authorization header
    or as a query parameter (for FormData uploads from browser).
    """
    logger.info("Getting user context", 
                has_credentials=bool(credentials and credentials.credentials),
                has_query_token=bool(token),
                query_params=dict(request.query_params))
    
    # Try to get token from Authorization header first
    if credentials and credentials.credentials:
        logger.info("Using token from Authorization header")
        return await validate_token(credentials)
    
    # Fallback: try to get token from query string parameter
    if token:
        logger.info("Using token from query parameter")
        # Create a mock credentials object for validate_token
        class MockCredentials:
            def __init__(self, token: str):
                self.credentials = token
        
        mock_creds = MockCredentials(token)
        return await validate_token(mock_creds)
    
    # Also try to get from query string directly from request (fallback)
    query_token = request.query_params.get("token")
    if query_token:
        logger.info("Using token from request.query_params")
        class MockCredentials:
            def __init__(self, token: str):
                self.credentials = token
        
        mock_creds = MockCredentials(query_token)
        return await validate_token(mock_creds)
    
    # If no token found, raise error
    logger.warning("No token found in request", 
                   method=request.method,
                   path=request.url.path,
                   query_params=dict(request.query_params))
    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide token in Authorization header or 'token' query parameter.",
        headers={"WWW-Authenticate": "Bearer"},
    )

# Database connection pool (reuse from memory adapter pattern)
_db_pool: Optional[asyncpg.Pool] = None


async def _get_db_pool() -> asyncpg.Pool:
    """Get or create database connection pool."""
    global _db_pool
    if _db_pool is None:
        # Convert SQLAlchemy URL to asyncpg format
        # asyncpg uses postgresql:// not postgresql+asyncpg://
        db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _db_pool = await asyncpg.create_pool(db_url)
        logger.info("Database connection pool created", db_url=db_url.split("@")[1] if "@" in db_url else "***")
    return _db_pool


async def save_to_storage(recording_id: str, audio_content: bytes, filename: str, content_type: str, user_ctx: UserContext) -> str:
    """
    Guardar archivo de audio en MinIO.
    
    Args:
        recording_id: ID de la grabación
        audio_content: Contenido del archivo de audio (bytes)
        filename: Nombre del archivo (ya limpio)
        content_type: Tipo MIME del archivo
        user_ctx: Contexto del usuario para autenticación
        
    Returns:
        URL del archivo guardado en MinIO (s3://bucket/key)
    """
    logger.info("Saving audio to storage", recording_id=recording_id)
    
    try:
        # Construir la clave S3
        s3_key = f"recordings/{recording_id}/{filename}"
        
        # Configurar cliente S3 para MinIO
        # MinIO requiere use_path_style=True para path-style addressing
        s3_client = boto3.client(
            's3',
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.s3_region,
            config=Config(
                signature_version='s3v4',
                s3={'addressing_style': 'path'}
            )
        )
        
        # Asegurar que el bucket existe
        try:
            s3_client.head_bucket(Bucket=settings.s3_bucket)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == '404':
                # Bucket no existe, crearlo
                logger.info("Creating bucket", bucket=settings.s3_bucket)
                s3_client.create_bucket(Bucket=settings.s3_bucket)
            else:
                raise
        
        # Subir archivo a MinIO
        s3_client.put_object(
            Bucket=settings.s3_bucket,
            Key=s3_key,
            Body=audio_content,
            ContentType=content_type
        )
        
        audio_url = f"s3://{settings.s3_bucket}/{s3_key}"
        logger.info("Audio saved to storage", 
                   recording_id=recording_id, 
                   audio_url=audio_url,
                   file_size=len(audio_content),
                   s3_key=s3_key)
        
        return audio_url
        
    except Exception as e:
        logger.error("Failed to save audio to storage", recording_id=recording_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to save audio: {str(e)}")


async def transcribe_audio(audio_content: bytes, filename: str, content_type: str, user_ctx: UserContext) -> str:
    """
    Transcribir audio usando backoffice /api/transcribe.
    
    Args:
        audio_content: Contenido del archivo de audio (bytes)
        filename: Nombre del archivo (ya limpio)
        content_type: Tipo MIME del archivo
        user_ctx: Contexto del usuario para autenticación
        
    Returns:
        Transcripción del audio
    """
    logger.info("Transcribing audio", filename=filename)
    
    try:
        logger.info("Sending audio to backoffice", 
                   filename=filename,
                   content_type=content_type,
                   file_size=len(audio_content))
        
        # Llamar a backoffice /api/transcribe
        # El backoffice espera recibir el archivo como multipart/form-data
        # con el campo "audio" usando multer
        async with httpx.AsyncClient(timeout=120.0) as client:
            # httpx requiere que el contenido del archivo sea un objeto file-like
            # Usamos BytesIO que es compatible con httpx
            # El formato es: (filename, file_like_object, content_type)
            # IMPORTANTE: El objeto BytesIO debe estar en el inicio del stream
            audio_file = BytesIO(audio_content)
            audio_file.seek(0)  # Asegurar que estamos al inicio
            
            files = {
                "audio": (filename, audio_file, content_type)
            }
            headers = {
                "Authorization": f"Bearer {user_ctx.raw_token}"
            }
            
            logger.info("Sending transcription request to backoffice",
                       url=f"{settings.backoffice_url}/api/transcribe",
                       filename=filename,
                       content_type=content_type,
                       file_size=len(audio_content))
            
            # No establecer Content-Type manualmente, httpx lo hace automáticamente
            # para multipart/form-data
            response = await client.post(
                f"{settings.backoffice_url}/api/transcribe",
                files=files,
                headers=headers
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info("Backoffice transcription response", 
                       status_code=response.status_code,
                       result_keys=list(result.keys()) if isinstance(result, dict) else None,
                       result_preview=str(result)[:200] if result else None)
            
            # El backoffice devuelve 'text', no 'transcript'
            transcript = result.get("text", result.get("transcript", ""))
            logger.info("Audio transcribed", filename=filename, transcript_length=len(transcript))
            
            return transcript
            
    except httpx.HTTPStatusError as e:
        logger.error("Backoffice transcription failed", status=e.response.status_code, error=e.response.text)
        # Si es rate limit (429), propagar el error para que se maneje arriba
        if e.response.status_code == 429:
            raise HTTPException(status_code=429, detail=f"Transcription rate limit exceeded: {e.response.text}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e.response.text}")
    except Exception as e:
        logger.error("Failed to transcribe audio", error=str(e))
        raise HTTPException(status_code=500, detail=f"Transcription error: {str(e)}")


async def process_with_agent(
    recording_id: str,
    transcript: str,
    duration: int,
    user_ctx: UserContext,
) -> Dict[str, Any]:
    """
    Procesar transcripción con Meeting Processor Agent.
    
    Args:
        recording_id: ID de la grabación
        transcript: Transcripción del audio
        duration: Duración en segundos
        user_ctx: Contexto del usuario
        
    Returns:
        Diccionario con summary, action_items, topics
    """
    logger.info("Processing transcript with agent", recording_id=recording_id, transcript_length=len(transcript))
    
    try:
        # Construir prompt para el agente
        prompt = f"""Analiza esta transcripción de una reunión/grabación y extrae:

1. Un resumen conciso (2-3 frases)
2. Action items con formato: asignado, tarea, deadline
3. Temas clave discutidos

Transcripción:
{transcript}

Duración: {duration} segundos

Responde en formato JSON:
{{
  "summary": "resumen aquí",
  "action_items": [
    {{"assignee": "nombre", "task": "tarea", "deadline": "fecha"}}
  ],
  "topics": ["tema1", "tema2"]
}}"""

        # Invocar agente usando el mismo patrón que assistant.py
        from google.adk.runners import Runner
        from google.genai import types
        from ..api.assistant import _build_agent_for_user
        
        # Construir agente para el usuario
        agent, memory_service = await _build_agent_for_user(user_ctx)
        
        # Crear runner
        from ..api.assistant import get_session_service
        session_service = get_session_service()
        
        runner = Runner(
            app_name="personal_assistant",
            agent=agent,
            session_service=session_service,
            memory_service=memory_service,
        )
        
        # Crear sesión temporal para este procesamiento
        session_id = f"recording_{recording_id}_{uuid.uuid4().hex[:8]}"
        session = await runner.session_service.create_session(
            app_name="personal_assistant",
            user_id=user_ctx.sub,
            session_id=session_id,
            state={'tenant_id': user_ctx.tenant_id or 'default'},
        )
        
        # Ejecutar agente con el prompt
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        )
        
        # Ejecutar y obtener respuesta
        response_text = ""
        async for event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session.id,
            new_message=user_content,
        ):
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text
        
        # Parsear respuesta JSON
        logger.info("Agent response received", 
                   recording_id=recording_id,
                   response_length=len(response_text),
                   response_preview=response_text[:500] if response_text else None)
        
        try:
            # Intentar extraer JSON de la respuesta
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                logger.info("JSON parsed successfully", 
                           recording_id=recording_id,
                           has_summary="summary" in result,
                           has_action_items="action_items" in result,
                           has_topics="topics" in result)
            else:
                # Si no hay JSON, crear estructura básica
                logger.warning("No JSON found in response, using fallback", recording_id=recording_id)
                result = {
                    "summary": response_text[:200] if response_text else "No se pudo generar resumen",
                    "action_items": [],
                    "topics": []
                }
        except json.JSONDecodeError as e:
            # Si falla el parseo, crear estructura básica
            logger.warning("JSON parsing failed, using fallback", 
                         recording_id=recording_id,
                         error=str(e),
                         json_match_preview=json_match.group()[:200] if json_match else None)
            result = {
                "summary": response_text[:200] if response_text else "No se pudo generar resumen",
                "action_items": [],
                "topics": []
            }
        
        logger.info("Agent processing completed", 
                   recording_id=recording_id, 
                   has_summary=bool(result.get("summary")),
                   action_items_count=len(result.get("action_items", [])),
                   topics_count=len(result.get("topics", [])))
        
        return result
        
    except Exception as e:
        logger.error("Failed to process with agent", recording_id=recording_id, error=str(e))
        # Retornar estructura básica en caso de error
        return {
            "summary": f"Error al procesar: {str(e)}",
            "action_items": [],
            "topics": []
        }


async def save_to_database(
    recording_id: str,
    session_id: str,
    user_id: str,
    title: str,
    recording_type: str,
    duration_seconds: int,
    audio_url: Optional[str],
    transcript: Optional[str],
    summary: Optional[str],
    action_items: list,
    topics: list,
) -> None:
    """
    Guardar grabación en tabla recordings de PostgreSQL.
    
    Args:
        recording_id: ID de la grabación (se usa como UUID primary key)
        session_id: ID de la sesión (text/UUID)
        user_id: ID del usuario (text/UUID)
        title: Título de la grabación
        recording_type: Tipo (audio/video/screen)
        duration_seconds: Duración en segundos
        audio_url: URL del archivo de audio
        transcript: Transcripción
        summary: Resumen
        action_items: Lista de action items
        topics: Lista de temas
    """
    logger.info("Saving recording to database", recording_id=recording_id)
    
    try:
        pool = await _get_db_pool()
        
        # Validar que recording_id sea un UUID válido, si no, generar uno
        try:
            uuid.UUID(recording_id)
        except ValueError:
            # Si no es un UUID válido, generar uno nuevo
            logger.warning("recording_id is not a valid UUID, generating new one", recording_id=recording_id)
            recording_id = str(uuid.uuid4())
        
        # Validar que session_id sea un UUID válido, si no, generar uno
        try:
            uuid.UUID(session_id)
        except ValueError:
            # Si no es un UUID válido, generar uno nuevo
            logger.warning("session_id is not a valid UUID, generating new one", session_id=session_id)
            session_id = str(uuid.uuid4())
        
        # Validar que user_id sea un UUID válido, si no, generar uno
        try:
            uuid.UUID(user_id)
        except ValueError:
            # Si no es un UUID válido, generar uno nuevo
            logger.warning("user_id is not a valid UUID, generating new one", user_id=user_id)
            user_id = str(uuid.uuid4())
        
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO recordings (
                    id, session_id, user_id, title, recording_type,
                    duration_seconds, audio_url, transcript, summary,
                    action_items, topics, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    user_id = EXCLUDED.user_id,
                    title = EXCLUDED.title,
                    recording_type = EXCLUDED.recording_type,
                    duration_seconds = EXCLUDED.duration_seconds,
                    audio_url = EXCLUDED.audio_url,
                    transcript = EXCLUDED.transcript,
                    summary = EXCLUDED.summary,
                    action_items = EXCLUDED.action_items,
                    topics = EXCLUDED.topics,
                    updated_at = NOW()
            """,
                recording_id,
                session_id,
                user_id,
                title,
                recording_type,
                duration_seconds,
                audio_url,
                transcript,
                summary,
                json.dumps(action_items) if action_items else "[]",
                json.dumps(topics) if topics else "[]",
            )
        
        logger.info("Recording saved to database", recording_id=recording_id)
        
    except Exception as e:
        logger.error("Failed to save recording to database", recording_id=recording_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to save to database: {str(e)}")


async def notify_completion(
    session_id: str, 
    user_id: str,
    recording_id: str,
    title: str,
    result: Dict[str, Any]
) -> None:
    """
    Notificar a Llibreta vía SSE sobre la finalización de la grabación.
    
    Usa el mismo sistema SSE que HITL para enviar eventos en tiempo real.
    
    Args:
        session_id: ID de la sesión
        user_id: ID del usuario
        recording_id: ID de la grabación
        title: Título de la grabación
        result: Resultado del procesamiento (summary, action_items, topics)
    """
    logger.info(
        "Notifying recording completion via SSE",
        session_id=session_id,
        user_id=user_id,
        recording_id=recording_id
    )
    
    try:
        # Obtener la cola de eventos del usuario (mismo sistema que HITL)
        queue = get_user_queue(user_id)
        
        # Crear evento de grabación completada
        # El frontend espera un evento SSE con event="recording_complete" y data como JSON
        # Necesitamos crear un objeto similar a HITLEvent pero para grabaciones
        class RecordingCompleteEvent(BaseModel):
            """Evento de grabación completada para SSE"""
            type: str = "recording_complete"
            recording_id: str
            session_id: str
            title: str
            summary: str
            action_items: list
            topics: list
            timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
        
        event = RecordingCompleteEvent(
            recording_id=recording_id,
            session_id=session_id,
            title=title,
            summary=result.get("summary", ""),
            action_items=result.get("action_items", []),
            topics=result.get("topics", []),
        )
        
        # Crear un objeto compatible con el sistema SSE
        # El sistema SSE espera objetos con event_type y model_dump_json()
        class SSEEvent:
            def __init__(self, event_type: str, data: dict):
                self.event_type = event_type
                self._data = data
            
            def model_dump_json(self) -> str:
                return json.dumps(self._data)
        
        sse_event = SSEEvent(
            event_type="recording_complete",
            data=event.model_dump()
        )
        
        # Enviar evento a la cola (será entregado vía SSE)
        await queue.put(sse_event)
        
        logger.info(
            "Recording completion event queued for SSE",
            user_id=user_id,
            recording_id=recording_id,
            session_id=session_id
        )
        
    except Exception as e:
        logger.error(
            "Failed to notify recording completion",
            error=str(e),
            user_id=user_id,
            recording_id=recording_id,
            session_id=session_id
        )
        # No lanzar excepción - la notificación es opcional, no debe romper el flujo


@router.post("/complete")
async def recording_complete(
    request: Request,
    recording_id: str = Form(...),
    session_id: str = Form(...),
    user_id: str = Form(...),
    recording_type: str = Form(...),
    title: str = Form(...),
    duration_seconds: int = Form(...),
    audio_file: Optional[UploadFile] = File(None, alias="audio"),
    transcript: Optional[str] = Form(None),
):
    """
    Endpoint llamado por nodus-recorder-pwa cuando completa la grabación.
    
    Flujo:
    1. Guardar archivo en storage
    2. Transcribir si es necesario
    3. Procesar con agent
    4. Guardar en DB
    5. Notificar a Llibreta
    """
    # Get user context manually to handle FormData + query params
    # FastAPI may have issues reading query params when FormData is present
    try:
        user_ctx = await get_current_user_with_query_fallback(request, None, None)
    except HTTPException as e:
        logger.error("Authentication failed", status_code=e.status_code, detail=e.detail)
        raise
    
    logger.info(
        "Recording completed",
        recording_id=recording_id,
        session_id=session_id,
        duration_seconds=duration_seconds,
        has_audio_file=audio_file is not None,
        audio_filename=audio_file.filename if audio_file else None,
        audio_size=audio_file.size if audio_file else None,
    )
    
    try:
        # Leer contenido del archivo una sola vez (si existe)
        audio_content = None
        clean_filename = None
        content_type = None
        
        if audio_file:
            # Leer contenido
            audio_content = await audio_file.read()
            
            # Limpiar el nombre del archivo (remover codecs como ;codecs=opus)
            clean_filename = audio_file.filename or "audio.webm"
            if ";" in clean_filename:
                clean_filename = clean_filename.split(";")[0]
            
            # Determinar content type basado en la extensión
            content_type = audio_file.content_type or "audio/webm"
            if clean_filename.endswith('.webm'):
                content_type = "audio/webm"
            elif clean_filename.endswith('.mp3'):
                content_type = "audio/mpeg"
            elif clean_filename.endswith('.wav'):
                content_type = "audio/wav"
            elif clean_filename.endswith('.ogg'):
                content_type = "audio/ogg"
        
        # 1. Guardar archivo
        audio_url = None
        if audio_content:
            audio_url = await save_to_storage(recording_id, audio_content, clean_filename, content_type, user_ctx)
        
        # 2. Transcribir si necesario (manejar errores de rate limit)
        if not transcript and audio_content:
            try:
                transcript = await transcribe_audio(audio_content, clean_filename, content_type, user_ctx)
            except HTTPException as e:
                if e.status_code == 429 or "rate limit" in str(e.detail).lower():
                    logger.warning("Transcription rate limited, continuing without transcript", recording_id=recording_id)
                    transcript = None
                else:
                    raise
        
        # 3. Procesar con agent (solo si hay transcripción o si no es requerida)
        result = await process_with_agent(
            recording_id=recording_id,
            transcript=transcript or "",
            duration=duration_seconds,
            user_ctx=user_ctx,
        )
        
        # 4. Guardar en DB
        await save_to_database(
            recording_id=recording_id,
            session_id=session_id,
            user_id=user_id,
            title=title,
            recording_type=recording_type,
            duration_seconds=duration_seconds,
            audio_url=audio_url,
            transcript=transcript,
            summary=result.get("summary"),
            action_items=result.get("action_items", []),
            topics=result.get("topics", []),
        )
        
        # 5. Notificar vía SSE (mismo sistema que HITL)
        await notify_completion(
            session_id=session_id,
            user_id=user_id,
            recording_id=recording_id,
            title=title,
            result=result
        )
        
        return {
            "status": "success",
            "recording_id": recording_id,
            "summary": result.get("summary"),
            "action_items": result.get("action_items", []),
            "topics": result.get("topics", []),
        }
    
    except Exception as e:
        logger.error("Error processing recording", error=str(e), recording_id=recording_id)
        raise HTTPException(status_code=500, detail=str(e))

