"""
Dual-Write Memory Service.
Writes to BOTH DatabaseMemoryService AND Qdrant (direct, for semantic memory).
Reads only from DatabaseMemoryService (fast for PreloadMemoryTool).

Qdrant writes are batched and processed in background every 5 minutes.
"""

import structlog
import asyncio
from typing import Any, Optional, List
from uuid import uuid4
from datetime import datetime
from google.adk.memory.base_memory_service import (
    BaseMemoryService,
    SearchMemoryResponse
)
from google.adk.sessions.session import Session
from ..middleware.auth import UserContext
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams
from ..config import settings

logger = structlog.get_logger()


class DualWriteMemoryService(BaseMemoryService):
    """
    Dual-write memory service:
    - WRITE: Both ADK Memory (Postgres) AND Qdrant (direct, semantic memory)
    - READ: Only ADK Memory (fast, for PreloadMemoryTool)
    
    Qdrant writes are batched and processed every 5 minutes in background.
    
    Features:
    - Direct Qdrant access (no JWT issues)
    - Semantic search with embeddings
    - Temporal metadata for time-based queries
    - Background batch processing to avoid blocking the agent
    """
    
    def __init__(
        self,
        adk_memory: BaseMemoryService,
        qdrant_url: str,
        qdrant_api_key: Optional[str],
        user_context: UserContext,
        batch_interval_seconds: int = 300,  # 5 minutes default
    ):
        """
        Args:
            adk_memory: Primary memory service (DatabaseMemoryService)
            qdrant_url: Qdrant service URL
            qdrant_api_key: Optional Qdrant API key
            user_context: User context for tenant/user isolation
            batch_interval_seconds: Interval for batch processing (default 300s = 5min)
        """
        self.adk_memory = adk_memory
        self.user_context = user_context
        self.batch_interval = batch_interval_seconds
        
        # Initialize Qdrant client for direct memory storage
        self.qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        
        # Initialize LiteLLM client for embeddings
        self.llm_client = AsyncOpenAI(
            api_key=settings.litellm_proxy_api_key,
            base_url=f"{settings.litellm_proxy_api_base}/v1",
        )
        
        # Queue for batching Qdrant writes
        self.memory_queue: asyncio.Queue[Session] = asyncio.Queue()
        self._batch_task: Optional[asyncio.Task] = None
        self._shutdown = False
        
        logger.info(
            "DualWriteMemoryService initialized with direct Qdrant",
            batch_interval_seconds=batch_interval_seconds,
            qdrant_url=qdrant_url,
            tenant_id=user_context.tenant_id,
            user_id=user_context.sub,
        )
    
    def start_background_processor(self):
        """Start the background batch processor."""
        if self._batch_task is None or self._batch_task.done():
            self._batch_task = asyncio.create_task(self._background_batch_processor())
            logger.info("Background batch processor started")
    
    async def stop_background_processor(self):
        """Stop the background batch processor gracefully."""
        self._shutdown = True
        if self._batch_task and not self._batch_task.done():
            await self._batch_task
            logger.info("Background batch processor stopped")
    
    async def _background_batch_processor(self):
        """
        Background task that processes the memory queue every 5 minutes.
        Runs continuously until shutdown.
        """
        logger.info("Background batch processor running", interval_seconds=self.batch_interval)
        
        while not self._shutdown:
            try:
                # Esperar l'interval (5 minuts)
                await asyncio.sleep(self.batch_interval)
                
                # Processar totes les sessions encuades
                sessions_to_process: List[Session] = []
                while not self.memory_queue.empty():
                    try:
                        session = self.memory_queue.get_nowait()
                        sessions_to_process.append(session)
                    except asyncio.QueueEmpty:
                        break
                
                if sessions_to_process:
                    logger.info(
                        "Processing batch of sessions for Qdrant memory",
                        batch_size=len(sessions_to_process)
                    )
                    await self._process_batch(sessions_to_process)
                else:
                    logger.debug("No sessions to process in this batch")
                    
            except asyncio.CancelledError:
                logger.info("Background processor cancelled")
                break
            except Exception as e:
                logger.error(
                    "Error in background batch processor (will retry)",
                    error=str(e),
                    error_type=type(e).__name__
                )
                # No morir, continuar processant
                await asyncio.sleep(10)  # Backoff curt
    
    async def _process_batch(self, sessions: List[Session]):
        """Process a batch of sessions to Qdrant memory."""
        success_count = 0
        error_count = 0
        
        for session in sessions:
            try:
                await self._save_to_qdrant(session)
                success_count += 1
            except Exception as e:
                error_count += 1
                logger.warning(
                    "Failed to save session to Qdrant memory (non-fatal)",
                    error=str(e),
                    session_id=session.id
                )
        
        logger.info(
            "Batch processing completed",
            total=len(sessions),
            success=success_count,
            errors=error_count
        )
    
    async def add_session_to_memory(self, session: Session):
        """
        DUAL WRITE: Save to both ADK Memory AND Qdrant memory.
        
        ADK Memory saves always (critical path, blocking).
        Qdrant memory saves in background (non-blocking, batched every 5 minutes).
        """
        # 1. Save to ADK Memory (Postgres) - MUST succeed (blocking)
        await self.adk_memory.add_session_to_memory(session)
        logger.info(
            "Session saved to ADK Memory",
            session_id=session.id,
            events=len(session.events)
        )
        
        # 2. Enqueue for Qdrant memory background processing (non-blocking)
        await self.memory_queue.put(session)
        logger.info(
            "Session enqueued for Qdrant memory background processing",
            session_id=session.id,
            queue_size=self.memory_queue.qsize()
        )
    
    def _get_collection_name(self) -> str:
        """Generate memory collection name with tenant and user awareness."""
        tenant_name = self.user_context.tenant_id.replace("t_", "") if self.user_context.tenant_id.startswith("t_") else self.user_context.tenant_id
        return f"memory_t_{tenant_name}_{self.user_context.sub}"
    
    async def _ensure_collection_exists(self):
        """Ensure the memory collection exists in Qdrant."""
        collection_name = self._get_collection_name()
        
        try:
            collections = self.qdrant_client.get_collections()
            collection_names = [c.name for c in collections.collections]
            
            if collection_name not in collection_names:
                logger.info("Creating new memory collection", collection=collection_name)
                self.qdrant_client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=1536,  # text-embedding-3-small
                        distance=Distance.COSINE
                    )
                )
                logger.info("Memory collection created", collection=collection_name)
        except Exception as e:
            logger.error("Failed to ensure collection exists", collection=collection_name, error=str(e))
            raise
    
    async def _generate_embedding(self, text: str) -> list[float]:
        """Generate embedding using LiteLLM."""
        try:
            response = await self.llm_client.embeddings.create(
                model="text-embedding-3-small",
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error("Failed to generate embedding", error=str(e))
            raise
    
    async def _save_to_qdrant(self, session: Session):
        """Save session events to Qdrant as semantic memory vectors."""
        collection_name = self._get_collection_name()
        
        # Ensure collection exists
        await self._ensure_collection_exists()
        
        points = []
        
        for event in session.events:
            if not event.content or not event.content.parts:
                continue
            
            # Extract text
            text_parts = []
            for part in event.content.parts:
                if hasattr(part, 'text') and part.text:
                    text_parts.append(part.text)
            
            if not text_parts:
                continue
            
            content = ' '.join(text_parts)
            
            # Skip very short messages (< 10 chars)
            if len(content.strip()) < 10:
                continue
            
            # Format with timestamp for temporal context
            timestamp = datetime.fromtimestamp(event.timestamp)
            display_time = timestamp.strftime("%Y-%m-%d %H:%M")
            content_with_time = f"[{display_time}] {content}"
            
            # Generate embedding
            try:
                vector = await self._generate_embedding(content_with_time)
            except Exception as e:
                logger.warning("Failed to generate embedding for event", error=str(e))
                continue
            
            # Create Qdrant point
            point_id = str(uuid4())
            point = PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "content": content,  # Original without timestamp
                    "content_with_time": content_with_time,  # With timestamp for display
                    "created_at": int(event.timestamp * 1000),  # milliseconds
                    "session_id": session.id,
                    "app_name": session.app_name,
                    "author": event.author or 'unknown',
                    "user_id": self.user_context.sub,
                    "tenant_id": self.user_context.tenant_id,
                    "source": "adk",
                }
            )
            points.append(point)
        
        if not points:
            logger.info("No meaningful content to save to Qdrant", session_id=session.id)
            return
        
        # Batch upsert all points
        self.qdrant_client.upsert(
            collection_name=collection_name,
            points=points
        )
        
        logger.info(
            "Session saved to Qdrant memory",
            session_id=session.id,
            collection=collection_name,
            points_saved=len(points)
        )
    
    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
        limit: int = 3,
    ) -> SearchMemoryResponse:
        """
        READ: Only from ADK Memory (fast for PreloadMemoryTool).
        
        Qdrant memory reads are via QueryMemoryTool invoked by the Agent.
        """
        return await self.adk_memory.search_memory(
            app_name=app_name,
            user_id=user_id,
            query=query,
            limit=limit,
        )
    
    async def close(self):
        """Cleanup resources and process remaining queue."""
        # Process remaining sessions in queue before closing
        if not self.memory_queue.empty():
            logger.info("Processing remaining sessions before shutdown", queue_size=self.memory_queue.qsize())
            sessions_to_process: List[Session] = []
            while not self.memory_queue.empty():
                try:
                    session = self.memory_queue.get_nowait()
                    sessions_to_process.append(session)
                except asyncio.QueueEmpty:
                    break
            if sessions_to_process:
                await self._process_batch(sessions_to_process)
        
        # Stop background processor
        await self.stop_background_processor()
        
        # Close ADK memory
        if hasattr(self.adk_memory, 'close'):
            await self.adk_memory.close()
