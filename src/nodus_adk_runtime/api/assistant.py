"""
Assistant API Routes

Endpoints for managing assistant sessions and messages.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, Dict, Any
import structlog
import uuid

from .schemas import SessionCreateRequest, MessageRequest, SessionResponse
from ..middleware.auth import get_current_user, UserContext
from ..config import settings
from ..langfuse_tracer import start_trace, end_trace

logger = structlog.get_logger()

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])

# 🔥 CRITICAL FIX: Use shared persistent session service instead of InMemorySessionService
# This ensures conversation context is maintained across messages
_session_service: Optional[Any] = None

def get_session_service():
    """Get or create the shared persistent session service."""
    global _session_service
    if _session_service is None:
        from google.adk.sessions.database_session_service import DatabaseSessionService
        # ADK DatabaseSessionService uses SQLAlchemy async, which requires +asyncpg in the URL
        # Our database_memory_service uses asyncpg.create_pool() directly, which doesn't accept +asyncpg
        adk_db_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        logger.info("Initializing DatabaseSessionService", database_url=adk_db_url)
        _session_service = DatabaseSessionService(db_url=adk_db_url)
    return _session_service


async def _build_agent_for_user(user_ctx: UserContext) -> tuple[Any, Any]:
    """
    Build Root Agent with tricapa memory architecture:
    1. ADK Memory (Postgres) - automatic via PreloadMemoryTool
    2. OpenMemory (MCP) - on-demand episodic/semantic via nodus-memory
    3. Qdrant (direct) - documents/knowledge base
    
    Args:
        user_ctx: User context with tenant_id and user_id
        
    Returns:
        Tuple of (agent, memory_service)
    """
    from nodus_adk_runtime.adapters.mcp_adapter import MCPAdapter
    from nodus_adk_runtime.adapters.database_memory_service import DatabaseMemoryService
    from nodus_adk_runtime.adapters.dual_write_memory_service import DualWriteMemoryService
    from nodus_adk_runtime.tools.query_knowledge_tool import QueryKnowledgeBaseTool
    from nodus_adk_runtime.tools.query_memory_tool import QueryMemoryTool
    from nodus_adk_runtime.tools.query_pages_tool import QueryPagesTool
    from nodus_adk_runtime.prompts.memory_instructions import TRICAPA_MEMORY_INSTRUCTIONS
    from nodus_adk_agents.root_agent import build_root_agent
    
    # Initialize MCP adapter
    mcp_adapter = MCPAdapter(gateway_url=settings.mcp_gateway_url)
    
    # 1. ADK Memory (Postgres - short-term conversation)
    adk_memory = DatabaseMemoryService(
        database_url=settings.database_url
    )
    
    # 2. Wrap with DualWriteMemoryService (writes to Qdrant directly)
    # Direct Qdrant access avoids JWT expiration issues
    memory_service = DualWriteMemoryService(
        adk_memory=adk_memory,
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        user_context=user_ctx,
        batch_interval_seconds=300,  # 5 minutes
    )
    # Start background processor for batching Qdrant memory writes
    memory_service.start_background_processor()
    # memory_service = adk_memory  # Old fallback disabled
    
    # 3. Query Memory Tool (CAPA 2: Semantic memory from Qdrant)
    memory_tool = QueryMemoryTool(
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        openai_api_key=settings.openai_api_key,
        tenant_id=user_ctx.tenant_id or "default",
        user_id=user_ctx.sub,
    )
    
    # 4. Query Knowledge Base Tool (CAPA 3: Documents from Qdrant)
    knowledge_tool = QueryKnowledgeBaseTool(
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        openai_api_key=settings.openai_api_key,
        tenant_id=user_ctx.tenant_id or "default",
        user_id=user_ctx.sub,
    )
    
    # 5. Query Pages Tool (CAPA 4: Page-specific documents from Qdrant)
    pages_tool = QueryPagesTool(
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        openai_api_key=settings.openai_api_key,
        tenant_id=user_ctx.tenant_id or "default",
        user_id=user_ctx.sub,
    )
    logger.info(
        "QueryPagesTool initialized",
        tenant=user_ctx.tenant_id,
        user=user_ctx.sub,
    )
    
    # Load A2A tools BEFORE building the agent (to avoid event loop conflicts)
    a2a_tools = []
    try:
        from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import get_a2a_tools
        a2a_tools = await get_a2a_tools()
        
        if a2a_tools:
            logger.info(
                "A2A tools loaded for agent",
                count=len(a2a_tools),
                tools=[t.name if hasattr(t, 'name') else getattr(t, '__name__', str(t)) for t in a2a_tools],
            )
    except Exception as e:
        logger.warning("Failed to load A2A tools in assistant.py", error=str(e))
    
    # Build root agent with tricapa memory + instructions
    agent = build_root_agent(
        mcp_adapter=mcp_adapter,
        memory_service=memory_service,  # DualWriteMemoryService
        user_context=user_ctx,
        config={
            "model": settings.adk_model,
            "instructions": TRICAPA_MEMORY_INSTRUCTIONS,  # Memory system instructions
            "runtime_url": settings.runtime_url or "http://localhost:8080",
            "recorder_url": settings.recorder_url or "http://localhost:5005",
        },
        knowledge_tool=knowledge_tool,  # CAPA 3: Knowledge base (Qdrant)
        pages_tool=pages_tool,  # CAPA 4: Page documents (Qdrant)
        a2a_tools=a2a_tools,  # A2A tools
    )
    
    logger.info(
        "Agent built with tricapa memory",
        tenant_id=user_ctx.tenant_id,
        user_id=user_ctx.sub,
        memory_backend="dual_write",
        openmemory_enabled=settings.openmemory_enabled
    )
    
    return agent, memory_service


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    user_ctx: UserContext = Depends(get_current_user),
):
    """
    Create a new assistant session or reuse an existing conversation.
    
    If conversation_id is provided, reuses that conversation.
    Otherwise, creates a new session.
    """
    # Start Langfuse trace
    session_id_for_trace = request.conversation_id or f"session_{user_ctx.sub}"
    trace = start_trace(
        "create_session",
        user_ctx=user_ctx,
        session_id=session_id_for_trace,
        input_data={"message": request.message[:100] if request.message else None}
    )
    
    logger.info(
        "Creating session",
        user_id=user_ctx.sub,
        tenant_id=user_ctx.tenant_id,
        conversation_id=request.conversation_id,
    )
    
    try:
        # Build agent for user
        agent, memory_service = await _build_agent_for_user(user_ctx)
        
        # Create session ID
        session_id = request.conversation_id or f"session_{user_ctx.sub}_{uuid.uuid4().hex[:8]}"
        conversation_id = request.conversation_id or session_id
        
        # Run agent with user message
        from google.adk.runners import Runner
        from google.adk.apps.app import App, ResumabilityConfig
        from google.genai import types
        
        # 🔥 FIX: Use shared persistent session service to maintain conversation context
        session_service = get_session_service()
        
        # Enable ADK resumability for HITL support (allows pause/resume on long-running tools)
        resumability_config = ResumabilityConfig(is_resumable=True)
        
        # Create App instance with resumability config (required for Runner)
        app = App(
            name="personal_assistant",
            root_agent=agent,
            resumability_config=resumability_config,
        )
        
        runner = Runner(
            app=app,
            session_service=session_service,
            memory_service=memory_service,
        )
        
        # 🔥 FIX: Try to get existing session first, create only if it doesn't exist
        logger.info("Session lookup", 
                   requested_session_id=session_id, 
                   conversation_id=conversation_id,
                   user_id=user_ctx.sub)
        
        try:
            session = await runner.session_service.get_session(
                app_name="personal_assistant",
                user_id=user_ctx.sub,
                session_id=session_id,
            )
            if session:
                logger.info("✅ Reusing existing session", 
                           requested_session_id=session_id,
                           actual_session_id=session.id,
                           user_id=user_ctx.sub)
            else:
                raise ValueError("Session not found")
        except Exception as e:
            # Session doesn't exist, create it
            logger.info("Creating new session", 
                       requested_session_id=session_id, 
                       user_id=user_ctx.sub,
                       error=str(e))
            session = await runner.session_service.create_session(
                app_name="personal_assistant",
                user_id=user_ctx.sub,
                session_id=session_id,
                state={'tenant_id': user_ctx.tenant_id or 'default'},
            )
            logger.info("Session created", 
                       requested_session_id=session_id,
                       actual_session_id=session.id,
                       match=session.id == session_id)
        
        # Add user message
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=request.message)],
        )
        
        # Run agent and collect response data
        response_parts = []
        citations = []
        tool_calls = []
        memories = []
        intent = None
        structured_data = []
        
        logger.info("🔄 Starting agent run_async", session_id=session.id, message=request.message[:50])
        event_count = 0
        last_event = None
        invocation_id = None  # Will be captured from events
        
        async for event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session.id,
            new_message=user_content,
        ):
            event_count += 1
            last_event = event  # Keep track of last event for invocation_id
            
            # Capture invocation_id from event (ADK sets this automatically)
            if hasattr(event, 'invocation_id') and event.invocation_id:
                invocation_id = event.invocation_id
                logger.debug("Captured invocation_id from event", invocation_id=invocation_id)
            
            # 🔍 DEBUG: Log event details
            event_type = type(event).__name__
            logger.info(f"📨 Event #{event_count}", event_type=event_type)
            
            # Extract text content
            if hasattr(event, 'content') and event.content:
                logger.info(f"  📦 Content with {len(event.content.parts)} parts")
                for idx, part in enumerate(event.content.parts):
                    part_info = []
                    if hasattr(part, 'text') and part.text:
                        part_info.append(f"text({len(part.text)} chars)")
                    if hasattr(part, 'function_call'):
                        part_info.append(f"function_call({part.function_call.name})")
                    if hasattr(part, 'function_response'):
                        part_info.append(f"function_response({part.function_response.name})")
                    logger.info(f"    Part {idx}: {', '.join(part_info)}")
                
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_parts.append(part.text)
                        logger.info(f"  ✅ Added text response", text_preview=part.text[:100])
                    
                    # 🔥 NEW: Check for HITL markers in function responses
                    if hasattr(part, 'function_response') and part.function_response:
                        try:
                            response_data = part.function_response.response
                            if isinstance(response_data, dict) and response_data.get('_hitl_required'):
                                logger.info(
                                    "HITL marker detected in function response",
                                    agent=response_data.get('agent'),
                                    action=response_data.get('action_description')
                                )
                                tool_calls.append(response_data)
                        except Exception as e:
                            logger.debug("Error parsing function_response", error=str(e))
            
            # Extract tool calls and citations from custom_metadata
            if hasattr(event, 'custom_metadata') and event.custom_metadata:
                metadata = event.custom_metadata
                
                # Tool calls
                if 'tool_calls' in metadata:
                    tool_calls.extend(metadata['tool_calls'])
                
                # Citations/sources
                if 'citations' in metadata:
                    citations.extend(metadata['citations'])
                
                # Memories
                if 'memories' in metadata:
                    memories.extend(metadata['memories'])
                
                # Intent
                if 'intent' in metadata:
                    intent = metadata['intent']
                
                # Structured data from tools
                if 'structured_data' in metadata:
                    structured_data.extend(metadata['structured_data'])
        
        reply = " ".join(response_parts) if response_parts else "I received your message."
        
        # 🔥 NEW: Check if any tool returned HITL requirement
        hitl_required = False
        hitl_data = None
        
        for tool_call in tool_calls:
            if isinstance(tool_call, dict) and tool_call.get('_hitl_required'):
                hitl_required = True
                hitl_data = tool_call
                logger.info(
                    "HITL required detected in tool response",
                    agent=hitl_data.get('agent'),
                    action=hitl_data.get('action_description'),
                    action_type=hitl_data.get('action'),
                    action_keys=list(hitl_data.keys()) if isinstance(hitl_data, dict) else [],
                    has_recorder_url=bool(hitl_data.get('recorder_url')),
                    session_id=session.id
                )
                break
        
        # Special handling for RecorderTool: Pass data directly without confirmation
        # Check for both 'action' === 'start_recording' and presence of 'recorder_url'
        is_recorder_tool = (
            hitl_required and hitl_data and (
                hitl_data.get('action') == 'start_recording' or
                bool(hitl_data.get('recorder_url')) or
                (isinstance(hitl_data.get('ui_action'), dict) and hitl_data.get('ui_action', {}).get('type') == 'open_recorder')
            )
        )
        
        if is_recorder_tool:
            logger.info(
                "RecorderTool detected - passing data directly to frontend",
                recording_id=hitl_data.get('recording_id'),
                recorder_url=hitl_data.get('recorder_url'),
                action=hitl_data.get('action'),
                ui_action_type=hitl_data.get('ui_action', {}).get('type') if isinstance(hitl_data.get('ui_action'), dict) else None
            )
            # Use the message from the tool or generate a default one
            reply = hitl_data.get('message_to_user', reply)
        # If HITL is required (for other tools), create confirmation request WITHOUT blocking
        # ADK will pause automatically because the tool is marked as long_running
        elif hitl_required and hitl_data:
            from nodus_adk_runtime.services.hitl_service import get_hitl_service
            import uuid as uuid_lib
            
            hitl_service = get_hitl_service()
            event_id = f"hitl_{uuid_lib.uuid4().hex[:12]}"
            
            # Get invocation_id from last event (ADK sets this when pausing)
            # If not available from event, try to get from session
            if not invocation_id and last_event and hasattr(last_event, 'invocation_id'):
                invocation_id = last_event.invocation_id
            
            # If still not available, reload session to get latest invocation_id
            if not invocation_id:
                try:
                    reloaded_session = await session_service.get_session(
                        app_name="personal_assistant",
                        user_id=user_ctx.sub,
                        session_id=session.id,
                    )
                    if reloaded_session and reloaded_session.events:
                        # Get invocation_id from last event in session
                        last_session_event = reloaded_session.events[-1]
                        if hasattr(last_session_event, 'invocation_id'):
                            invocation_id = last_session_event.invocation_id
                except Exception as e:
                    logger.warning("Could not get invocation_id from session", error=str(e))
            
            logger.info(
                "Creating HITL event (non-blocking, ADK will pause automatically)",
                event_id=event_id,
                user_id=user_ctx.sub,
                invocation_id=invocation_id,
                action=hitl_data.get('action_description')
            )
            
            # Create HITL event WITHOUT waiting (non-blocking)
            # ADK has already paused the invocation because the tool is long_running
            try:
                # Merge original metadata from agent with session metadata
                merged_metadata = {
                    'agent': hitl_data.get('agent'),
                    'method': hitl_data.get('method'),
                    'session_id': session.id,
                    'original_message': request.message,
                    'invocation_id': invocation_id,  # ← Critical: Save for resuming
                }
                # Add agent's metadata (tool, input_type, etc.)
                if hitl_data.get('metadata'):
                    merged_metadata.update(hitl_data.get('metadata'))
                
                # Create event WITHOUT waiting (non-blocking)
                await hitl_service.create_event_async(
                    user_id=user_ctx.sub,
                    event_id=event_id,
                    action_description=hitl_data.get('action_description', 'Unknown action'),
                    action_data=hitl_data.get('action_data', {}),
                    metadata=merged_metadata,
                )
                
                # Return immediately with HITL pending status
                # ADK has already paused the invocation
                reply = hitl_data.get('message_to_user', reply)
                
                logger.info(
                    "HITL event created, returning immediately (invocation paused by ADK)",
                    event_id=event_id,
                    invocation_id=invocation_id,
                    session_id=session.id
                )
                
                # Skip the rest of the HITL handling (execution will happen after user confirms)
                # This will be handled in Phase 3 (resume after confirmation)
                # For now, just return the intermediate reply
                # NOTE: The old blocking code is removed for Phase 2
                # Phase 3 will implement resume logic
            
            except Exception as hitl_error:
                logger.error("HITL event creation failed", error=str(hitl_error), event_id=event_id)
                reply = f"Action could not be completed: {str(hitl_error)}"
        
        # Save session to memory after processing
        try:
            # RELOAD session to get latest events
            session = await session_service.get_session(
                app_name="personal_assistant",
                user_id=user_ctx.sub,
                session_id=session.id,
            )
            
            await memory_service.add_session_to_memory(session)
            logger.info("Session saved to memory (create)", session_id=session.id, tenant_id=user_ctx.tenant_id)
        except Exception as e:
            logger.error("Failed to save session to memory", error=str(e), session_id=session.id)
        
        # Build response metadata - include hitl_data if it's a RecorderTool
        response_metadata = {**request.metadata}
        is_recorder_tool_response = (
            hitl_required and hitl_data and (
                hitl_data.get('action') == 'start_recording' or
                bool(hitl_data.get('recorder_url')) or
                (isinstance(hitl_data.get('ui_action'), dict) and hitl_data.get('ui_action', {}).get('type') == 'open_recorder')
            )
        )
        if is_recorder_tool_response:
            # Include all RecorderTool fields in metadata for Llibreta
            logger.info(
                "Including RecorderTool fields in response metadata",
                recording_id=hitl_data.get('recording_id'),
                recorder_url=hitl_data.get('recorder_url')
            )
            response_metadata.update({
                '_hitl_required': True,
                'ui_action': hitl_data.get('ui_action'),
                'recording_id': hitl_data.get('recording_id'),
                'recorder_url': hitl_data.get('recorder_url'),
                'recording_type': hitl_data.get('recording_type'),
                'title': hitl_data.get('title'),
                'duration_minutes': hitl_data.get('duration_minutes'),
                'auto_transcribe': hitl_data.get('auto_transcribe'),
            })
        
        # End Langfuse trace (success)
        end_trace(trace, success=True)
        
        return SessionResponse(
            session_id=session_id,
            conversation_id=conversation_id,
            reply=reply,
            metadata=response_metadata,
            memories=memories,
            citations=citations,
            structured_data=structured_data,
            intent=intent,
            tool_calls=tool_calls,
        )
        
    except Exception as e:
        logger.error("Failed to create session", error=str(e), user_id=user_ctx.sub)
        
        # End Langfuse trace (error)
        end_trace(trace, success=False, error=str(e))
        
        # Fallback to stub response
        session_id = request.conversation_id or f"session_{user_ctx.sub}_{uuid.uuid4().hex[:8]}"
        conversation_id = request.conversation_id or session_id
        
        return SessionResponse(
            session_id=session_id,
            conversation_id=conversation_id,
            reply="Hello! I'm your assistant. There was an error processing your request.",
            metadata={**request.metadata, "error": str(e)},
        )


@router.post("/sessions/{session_id}/messages", response_model=SessionResponse)
async def add_message(
    session_id: str,
    request: MessageRequest,
    user_ctx: UserContext = Depends(get_current_user),
):
    """
    Add a message to an existing session.
    """
    # Start Langfuse trace
    trace = start_trace(
        "add_message",
        user_ctx=user_ctx,
        session_id=session_id,
        input_data={"message": request.message[:100] if request.message else None}
    )
    
    logger.info(
        "Adding message to session",
        session_id=session_id,
        user_id=user_ctx.sub,
        tenant_id=user_ctx.tenant_id,
    )
    
    try:
        # Build agent for user
        agent, memory_service = await _build_agent_for_user(user_ctx)
        
        # Run agent with user message
        from google.adk.runners import Runner
        from google.adk.apps.app import App, ResumabilityConfig
        from google.genai import types
        
        # 🔥 FIX: Use shared persistent session service
        session_service = get_session_service()
        
        # Enable ADK resumability for HITL support (allows pause/resume on long-running tools)
        resumability_config = ResumabilityConfig(is_resumable=True)
        
        # Create App instance with resumability config (required for Runner)
        app = App(
            name="personal_assistant",
            root_agent=agent,
            resumability_config=resumability_config,
        )
        
        runner = Runner(
            app=app,
            session_service=session_service,
            memory_service=memory_service,
        )
        
        # 🔥 FIX: Get or create session using the session_id from path
        logger.info("Session lookup (add_message)", 
                   session_id=session_id,
                   user_id=user_ctx.sub)
        
        try:
            session = await runner.session_service.get_session(
                app_name="personal_assistant",
                user_id=user_ctx.sub,
                session_id=session_id,
            )
            if session:
                logger.info("✅ Reusing existing session (add_message)", 
                           session_id=session_id,
                           actual_session_id=session.id,
                           user_id=user_ctx.sub)
            else:
                raise ValueError("Session not found")
        except Exception as e:
            # Session doesn't exist, create it with the provided session_id
            logger.info("Creating new session (add_message)", 
                       session_id=session_id,
                       user_id=user_ctx.sub,
                       error=str(e))
            session = await runner.session_service.create_session(
                app_name="personal_assistant",
                user_id=user_ctx.sub,
                session_id=session_id,  # ← CRITICAL: Pass the session_id!
                state={'tenant_id': user_ctx.tenant_id or 'default'},
            )
            logger.info("Session created (add_message)", 
                       session_id=session_id,
                       actual_session_id=session.id,
                       match=session.id == session_id)
        
        # Add user message
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=request.message)],
        )
        
        # Run agent and collect response data
        response_parts = []
        citations = []
        tool_calls = []
        memories = []
        intent = None
        structured_data = []
        last_event = None
        invocation_id = None  # Will be captured from events
        
        async for event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session.id,
            new_message=user_content,
        ):
            last_event = event  # Keep track of last event for invocation_id
            
            # Capture invocation_id from event (ADK sets this automatically)
            if hasattr(event, 'invocation_id') and event.invocation_id:
                invocation_id = event.invocation_id
                logger.debug("Captured invocation_id from event", invocation_id=invocation_id)
            
            # Extract text content
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_parts.append(part.text)
                    
                    # 🔥 NEW: Check for HITL markers in function responses
                    if hasattr(part, 'function_response') and part.function_response:
                        try:
                            response_data = part.function_response.response
                            if isinstance(response_data, dict) and response_data.get('_hitl_required'):
                                logger.info(
                                    "HITL marker detected in function response",
                                    agent=response_data.get('agent'),
                                    action=response_data.get('action_description')
                                )
                                tool_calls.append(response_data)
                        except Exception as e:
                            logger.debug("Error parsing function_response", error=str(e))
            
            # Extract tool calls and citations from custom_metadata
            if hasattr(event, 'custom_metadata') and event.custom_metadata:
                metadata = event.custom_metadata
                
                # Tool calls
                if 'tool_calls' in metadata:
                    tool_calls.extend(metadata['tool_calls'])
                
                # Citations/sources
                if 'citations' in metadata:
                    citations.extend(metadata['citations'])
                
                # Memories
                if 'memories' in metadata:
                    memories.extend(metadata['memories'])
                
                # Intent
                if 'intent' in metadata:
                    intent = metadata['intent']
                
                # Structured data from tools
                if 'structured_data' in metadata:
                    structured_data.extend(metadata['structured_data'])
        
        reply = " ".join(response_parts) if response_parts else "I received your message."
        
        # 🔥 NEW: Check if any tool returned HITL requirement
        hitl_required = False
        hitl_data = None
        
        for tool_call in tool_calls:
            if isinstance(tool_call, dict) and tool_call.get('_hitl_required'):
                hitl_required = True
                hitl_data = tool_call
                logger.info(
                    "HITL required detected in tool response",
                    agent=hitl_data.get('agent'),
                    action=hitl_data.get('action_description'),
                    action_type=hitl_data.get('action'),
                    action_keys=list(hitl_data.keys()) if isinstance(hitl_data, dict) else [],
                    has_recorder_url=bool(hitl_data.get('recorder_url')),
                    session_id=session.id
                )
                break
        
        # Special handling for RecorderTool: Pass data directly without confirmation
        # Check for both 'action' === 'start_recording' and presence of 'recorder_url'
        is_recorder_tool = (
            hitl_required and hitl_data and (
                hitl_data.get('action') == 'start_recording' or
                bool(hitl_data.get('recorder_url')) or
                (isinstance(hitl_data.get('ui_action'), dict) and hitl_data.get('ui_action', {}).get('type') == 'open_recorder')
            )
        )
        
        if is_recorder_tool:
            logger.info(
                "RecorderTool detected - passing data directly to frontend",
                recording_id=hitl_data.get('recording_id'),
                recorder_url=hitl_data.get('recorder_url'),
                action=hitl_data.get('action'),
                ui_action_type=hitl_data.get('ui_action', {}).get('type') if isinstance(hitl_data.get('ui_action'), dict) else None
            )
            # Use the message from the tool or generate a default one
            reply = hitl_data.get('message_to_user', reply)
        # If HITL is required (for other tools), create confirmation request WITHOUT blocking
        # ADK will pause automatically because the tool is marked as long_running
        elif hitl_required and hitl_data:
            from nodus_adk_runtime.services.hitl_service import get_hitl_service
            import uuid as uuid_lib
            
            hitl_service = get_hitl_service()
            event_id = f"hitl_{uuid_lib.uuid4().hex[:12]}"
            
            # Get invocation_id from last event (ADK sets this when pausing)
            # If not available from event, try to get from session
            if not invocation_id and last_event and hasattr(last_event, 'invocation_id'):
                invocation_id = last_event.invocation_id
            
            # If still not available, reload session to get latest invocation_id
            if not invocation_id:
                try:
                    reloaded_session = await session_service.get_session(
                        app_name="personal_assistant",
                        user_id=user_ctx.sub,
                        session_id=session.id,
                    )
                    if reloaded_session and reloaded_session.events:
                        # Get invocation_id from last event in session
                        last_session_event = reloaded_session.events[-1]
                        if hasattr(last_session_event, 'invocation_id'):
                            invocation_id = last_session_event.invocation_id
                except Exception as e:
                    logger.warning("Could not get invocation_id from session", error=str(e))
            
            logger.info(
                "Creating HITL event (non-blocking, ADK will pause automatically)",
                event_id=event_id,
                user_id=user_ctx.sub,
                invocation_id=invocation_id,
                action=hitl_data.get('action_description')
            )
            
            # Create HITL event WITHOUT waiting (non-blocking)
            # ADK has already paused the invocation because the tool is long_running
            try:
                # Merge original metadata from agent with session metadata
                merged_metadata = {
                    'agent': hitl_data.get('agent'),
                    'method': hitl_data.get('method'),
                    'session_id': session.id,
                    'original_message': request.message,
                    'invocation_id': invocation_id,  # ← Critical: Save for resuming
                }
                # Add agent's metadata (tool, input_type, etc.)
                if hitl_data.get('metadata'):
                    merged_metadata.update(hitl_data.get('metadata'))
                
                # Create event WITHOUT waiting (non-blocking)
                await hitl_service.create_event_async(
                    user_id=user_ctx.sub,
                    event_id=event_id,
                    action_description=hitl_data.get('action_description', 'Unknown action'),
                    action_data=hitl_data.get('action_data', {}),
                    metadata=merged_metadata,
                )
                
                # Return immediately with HITL pending status
                # ADK has already paused the invocation
                reply = hitl_data.get('message_to_user', reply)
                
                logger.info(
                    "HITL event created, returning immediately (invocation paused by ADK)",
                    event_id=event_id,
                    invocation_id=invocation_id,
                    session_id=session.id
                )
                
                # Skip the rest of the HITL handling (execution will happen after user confirms)
                # This will be handled in Phase 3 (resume after confirmation)
                # For now, just return the intermediate reply
                # NOTE: The old blocking code is removed for Phase 2
                # Phase 3 will implement resume logic
            
            except Exception as hitl_error:
                logger.error("HITL event creation failed", error=str(hitl_error), event_id=event_id)
                reply = f"Action could not be completed: {str(hitl_error)}"
        
        # Save session to memory after processing
        try:
            # RELOAD session to get latest events (including the ones just generated)
            # The local 'session' object might be stale after run_async
            session = await session_service.get_session(
                app_name="personal_assistant",
                user_id=user_ctx.sub,
                session_id=session.id,
            )
            
            await memory_service.add_session_to_memory(session)
            logger.info("Session saved to memory (add_message)", session_id=session.id, tenant_id=user_ctx.tenant_id)
        except Exception as e:
            logger.error("Failed to save session to memory", error=str(e), session_id=session.id)
        
        # Build response metadata - include hitl_data if it's a RecorderTool
        response_metadata = {**request.metadata}
        is_recorder_tool_response = (
            hitl_required and hitl_data and (
                hitl_data.get('action') == 'start_recording' or
                bool(hitl_data.get('recorder_url')) or
                (isinstance(hitl_data.get('ui_action'), dict) and hitl_data.get('ui_action', {}).get('type') == 'open_recorder')
            )
        )
        if is_recorder_tool_response:
            # Include all RecorderTool fields in metadata for Llibreta
            logger.info(
                "Including RecorderTool fields in response metadata",
                recording_id=hitl_data.get('recording_id'),
                recorder_url=hitl_data.get('recorder_url')
            )
            response_metadata.update({
                '_hitl_required': True,
                'ui_action': hitl_data.get('ui_action'),
                'recording_id': hitl_data.get('recording_id'),
                'recorder_url': hitl_data.get('recorder_url'),
                'recording_type': hitl_data.get('recording_type'),
                'title': hitl_data.get('title'),
                'duration_minutes': hitl_data.get('duration_minutes'),
                'auto_transcribe': hitl_data.get('auto_transcribe'),
            })
        
        # End Langfuse trace (success)
        end_trace(trace, success=True)
        
        return SessionResponse(
            session_id=session_id,
            conversation_id=session_id,
            reply=reply,
            metadata=response_metadata,
            memories=memories,
            citations=citations,
            structured_data=structured_data,
            intent=intent,
            tool_calls=tool_calls,
        )
        
    except Exception as e:
        import traceback
        logger.error("Failed to add message", error=str(e), session_id=session_id, traceback=traceback.format_exc())
        
        # End Langfuse trace (error)
        end_trace(trace, success=False, error=str(e))
        
        # Fallback to stub response
        return SessionResponse(
            session_id=session_id,
            conversation_id=session_id,
            reply=f"Received your message: {request.message}. There was an error processing it.",
            metadata={**request.metadata, "error": str(e)},
        )

