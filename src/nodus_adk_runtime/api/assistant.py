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
    from nodus_adk_runtime.prompts.memory_instructions import TRICAPA_MEMORY_INSTRUCTIONS
    from nodus_adk_agents.root_agent import build_root_agent
    
    # Initialize MCP adapter
    mcp_adapter = MCPAdapter(gateway_url=settings.mcp_gateway_url)
    
    # 1. ADK Memory (Postgres - short-term conversation)
    adk_memory = DatabaseMemoryService(
        database_url=settings.database_url
    )
    
    # 2. Wrap with DualWriteMemoryService (writes to OpenMemory via MCP)
    # OpenMemory 2.0 is now accessed via MCP Gateway using the 'store' tool
    memory_service = DualWriteMemoryService(
        adk_memory=adk_memory,
        mcp_adapter=mcp_adapter,
        user_context=user_ctx,
    )
    # memory_service = adk_memory  # Old fallback disabled
    
    # 3. Qdrant tool (on-demand documents)
    knowledge_tool = QueryKnowledgeBaseTool(
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        openai_api_key=settings.openai_api_key,
        tenant_id=user_ctx.tenant_id or "default",
        user_id=user_ctx.sub,
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
        },
        knowledge_tool=knowledge_tool,  # Qdrant knowledge base
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
        from google.genai import types
        
        # 🔥 FIX: Use shared persistent session service to maintain conversation context
        session_service = get_session_service()
        
        runner = Runner(
            app_name="personal_assistant",
            agent=agent,
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
        
        async for event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session.id,
            new_message=user_content,
        ):
            event_count += 1
            
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
                    session_id=session.id
                )
                break
        
        # If HITL is required, create confirmation request and wait for user decision
        if hitl_required and hitl_data:
            from nodus_adk_runtime.services.hitl_service import get_hitl_service
            import uuid as uuid_lib
            
            hitl_service = get_hitl_service()
            event_id = f"hitl_{uuid_lib.uuid4().hex[:12]}"
            
            logger.info(
                "Creating HITL confirmation request",
                event_id=event_id,
                user_id=user_ctx.sub,
                action=hitl_data.get('action_description')
            )
            
            # Request confirmation (this waits for user decision via SSE)
            try:
                # Merge original metadata from agent with session metadata
                merged_metadata = {
                    'agent': hitl_data.get('agent'),
                    'method': hitl_data.get('method'),
                    'session_id': session.id,
                    'original_message': request.message,
                }
                # Add agent's metadata (tool, input_type, etc.)
                if hitl_data.get('metadata'):
                    merged_metadata.update(hitl_data.get('metadata'))
                
                decision = await hitl_service.request_confirmation(
                    user_id=user_ctx.sub,
                    event_id=event_id,
                    action_description=hitl_data.get('action_description', 'Unknown action'),
                    action_data=hitl_data.get('action_data', {}),
                    metadata=merged_metadata,
                    timeout=300.0  # 5 minutes
                )
                
                logger.info(
                    "HITL decision received",
                    event_id=event_id,
                    approved=decision.approved,
                    reason=decision.reason
                )
                
                if decision.approved:
                    # HYBRID APPROACH: Execute action directly + feed result to Root Agent
                    # Step 1: Execute the HITL-approved action directly via A2A
                    agent_name = hitl_data.get('agent')
                    original_method = hitl_data.get('method')
                    action_data = hitl_data.get('action_data', {})
                    
                    # Determine execution method name
                    # Convention: {action}_with_confirmation → execute_{action}
                    # Special cases for specific agents
                    if agent_name == "hitl_math_agent" and original_method == "multiply_with_confirmation":
                        execution_method = "execute_multiplication"
                    else:
                        # Generic fallback: remove _with_confirmation and add execute_ prefix
                        execution_method = original_method.replace('_with_confirmation', '')
                        if execution_method == original_method:
                            execution_method = f"execute_{original_method}"
                        else:
                            execution_method = f"execute_{execution_method}"
                    
                    logger.info(
                        "Executing HITL-approved action directly",
                        agent=agent_name,
                        original_method=original_method,
                        execution_method=execution_method,
                        params=action_data
                    )
                    
                    try:
                        # Get agent endpoint from A2A config
                        from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import get_agent_config
                        agent_config = get_agent_config(agent_name)
                        
                        if not agent_config:
                            raise ValueError(f"Agent {agent_name} not found in A2A config")
                        
                        endpoint = agent_config.endpoint
                        
                        # Call execution method directly via A2A
                        from nodus_adk_agents.a2a_client import A2AClient
                        client = A2AClient(endpoint, timeout=30.0)
                        
                        # Filter action_data to only include parameters accepted by the execution method
                        # For hitl_math_agent: only base_number and factor
                        if agent_name == "hitl_math_agent" and execution_method == "execute_multiplication":
                            # If user provided input (number via reason), use it as factor
                            factor = action_data.get("factor", 2.0)
                            if decision.reason:
                                try:
                                    factor = float(decision.reason)
                                    logger.info(
                                        "Using user-provided factor from HITL input",
                                        user_input=decision.reason,
                                        parsed_factor=factor
                                    )
                                except (ValueError, TypeError):
                                    logger.warning(
                                        "Failed to parse user input as number, using default",
                                        user_input=decision.reason,
                                        default_factor=factor
                                    )
                            
                            execution_params = {
                                "base_number": action_data.get("base_number"),
                                "factor": factor
                            }
                        else:
                            # Generic fallback: pass all action_data
                            execution_params = action_data
                        
                        execution_result = await client.call(execution_method, execution_params)
                        
                        logger.info(
                            "HITL action executed successfully",
                            agent=agent_name,
                            method=execution_method,
                            result=execution_result
                        )
                        
                        # Step 2: Feed result back to Root Agent for final response
                        result_value = execution_result.get('result', execution_result)
                        result_explanation = execution_result.get('explanation', f"Result: {result_value}")
                        
                        continuation_message = types.Content(
                            role="user",
                            parts=[types.Part.from_text(
                                text=(
                                    f"The action has been completed successfully. "
                                    f"The result is: {result_explanation}. "
                                    f"Please provide a final response to the user with this result."
                                )
                            )],
                        )
                        
                        # Clear previous response parts
                        response_parts = []
                        
                        # Step 3: Continue Root Agent with the result
                        logger.info("Continuing Root Agent with execution result", session_id=session.id)
                        
                        async for event in runner.run_async(
                            user_id=user_ctx.sub,
                            session_id=session.id,
                            new_message=continuation_message,
                        ):
                            if hasattr(event, 'content') and event.content:
                                for part in event.content.parts:
                                    if hasattr(part, 'text') and part.text:
                                        response_parts.append(part.text)
                        
                        # Step 4: Final reply with result
                        reply = " ".join(response_parts) if response_parts else result_explanation
                        logger.info("HITL flow completed successfully", event_id=event_id)
                        
                    except Exception as exec_error:
                        logger.error(
                            "HITL action execution failed",
                            error=str(exec_error),
                            agent=agent_name,
                            method=execution_method
                        )
                        reply = f"Action could not be executed: {str(exec_error)}"
                else:
                    # User rejected
                    reply = f"Action cancelled: {decision.reason or 'User declined the request.'}"
                    logger.info("HITL action rejected by user", event_id=event_id, reason=decision.reason)
            
            except Exception as hitl_error:
                logger.error("HITL confirmation failed", error=str(hitl_error), event_id=event_id)
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
        
        return SessionResponse(
            session_id=session_id,
            conversation_id=conversation_id,
            reply=reply,
            metadata=request.metadata,
            memories=memories,
            citations=citations,
            structured_data=structured_data,
            intent=intent,
            tool_calls=tool_calls,
        )
        
    except Exception as e:
        logger.error("Failed to create session", error=str(e), user_id=user_ctx.sub)
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
        from google.genai import types
        
        # 🔥 FIX: Use shared persistent session service
        session_service = get_session_service()
        
        runner = Runner(
            app_name="personal_assistant",
            agent=agent,
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
        
        async for event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session.id,
            new_message=user_content,
        ):
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
                    session_id=session.id
                )
                break
        
        # If HITL is required, create confirmation request and wait for user decision
        if hitl_required and hitl_data:
            from nodus_adk_runtime.services.hitl_service import get_hitl_service
            import uuid as uuid_lib
            
            hitl_service = get_hitl_service()
            event_id = f"hitl_{uuid_lib.uuid4().hex[:12]}"
            
            logger.info(
                "Creating HITL confirmation request",
                event_id=event_id,
                user_id=user_ctx.sub,
                action=hitl_data.get('action_description')
            )
            
            # Request confirmation (this waits for user decision via SSE)
            try:
                # Merge original metadata from agent with session metadata
                merged_metadata = {
                    'agent': hitl_data.get('agent'),
                    'method': hitl_data.get('method'),
                    'session_id': session.id,
                    'original_message': request.message,
                }
                # Add agent's metadata (tool, input_type, etc.)
                if hitl_data.get('metadata'):
                    merged_metadata.update(hitl_data.get('metadata'))
                
                decision = await hitl_service.request_confirmation(
                    user_id=user_ctx.sub,
                    event_id=event_id,
                    action_description=hitl_data.get('action_description', 'Unknown action'),
                    action_data=hitl_data.get('action_data', {}),
                    metadata=merged_metadata,
                    timeout=300.0  # 5 minutes
                )
                
                logger.info(
                    "HITL decision received",
                    event_id=event_id,
                    approved=decision.approved,
                    reason=decision.reason
                )
                
                if decision.approved:
                    # HYBRID APPROACH: Execute action directly + feed result to Root Agent
                    # Step 1: Execute the HITL-approved action directly via A2A
                    agent_name = hitl_data.get('agent')
                    original_method = hitl_data.get('method')
                    action_data = hitl_data.get('action_data', {})
                    
                    # Determine execution method name
                    # Convention: {action}_with_confirmation → execute_{action}
                    # Special cases for specific agents
                    if agent_name == "hitl_math_agent" and original_method == "multiply_with_confirmation":
                        execution_method = "execute_multiplication"
                    else:
                        # Generic fallback: remove _with_confirmation and add execute_ prefix
                        execution_method = original_method.replace('_with_confirmation', '')
                        if execution_method == original_method:
                            execution_method = f"execute_{original_method}"
                        else:
                            execution_method = f"execute_{execution_method}"
                    
                    logger.info(
                        "Executing HITL-approved action directly",
                        agent=agent_name,
                        original_method=original_method,
                        execution_method=execution_method,
                        params=action_data
                    )
                    
                    try:
                        # Get agent endpoint from A2A config
                        from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import get_agent_config
                        agent_config = get_agent_config(agent_name)
                        
                        if not agent_config:
                            raise ValueError(f"Agent {agent_name} not found in A2A config")
                        
                        endpoint = agent_config.endpoint
                        
                        # Call execution method directly via A2A
                        from nodus_adk_agents.a2a_client import A2AClient
                        client = A2AClient(endpoint, timeout=30.0)
                        
                        # Filter action_data to only include parameters accepted by the execution method
                        # For hitl_math_agent: only base_number and factor
                        if agent_name == "hitl_math_agent" and execution_method == "execute_multiplication":
                            # If user provided input (number via reason), use it as factor
                            factor = action_data.get("factor", 2.0)
                            if decision.reason:
                                try:
                                    factor = float(decision.reason)
                                    logger.info(
                                        "Using user-provided factor from HITL input",
                                        user_input=decision.reason,
                                        parsed_factor=factor
                                    )
                                except (ValueError, TypeError):
                                    logger.warning(
                                        "Failed to parse user input as number, using default",
                                        user_input=decision.reason,
                                        default_factor=factor
                                    )
                            
                            execution_params = {
                                "base_number": action_data.get("base_number"),
                                "factor": factor
                            }
                        else:
                            # Generic fallback: pass all action_data
                            execution_params = action_data
                        
                        execution_result = await client.call(execution_method, execution_params)
                        
                        logger.info(
                            "HITL action executed successfully",
                            agent=agent_name,
                            method=execution_method,
                            result=execution_result
                        )
                        
                        # Step 2: Feed result back to Root Agent for final response
                        result_value = execution_result.get('result', execution_result)
                        result_explanation = execution_result.get('explanation', f"Result: {result_value}")
                        
                        continuation_message = types.Content(
                            role="user",
                            parts=[types.Part.from_text(
                                text=(
                                    f"The action has been completed successfully. "
                                    f"The result is: {result_explanation}. "
                                    f"Please provide a final response to the user with this result."
                                )
                            )],
                        )
                        
                        # Clear previous response parts
                        response_parts = []
                        
                        # Step 3: Continue Root Agent with the result
                        logger.info("Continuing Root Agent with execution result", session_id=session.id)
                        
                        async for event in runner.run_async(
                            user_id=user_ctx.sub,
                            session_id=session.id,
                            new_message=continuation_message,
                        ):
                            if hasattr(event, 'content') and event.content:
                                for part in event.content.parts:
                                    if hasattr(part, 'text') and part.text:
                                        response_parts.append(part.text)
                        
                        # Step 4: Final reply with result
                        reply = " ".join(response_parts) if response_parts else result_explanation
                        logger.info("HITL flow completed successfully", event_id=event_id)
                        
                    except Exception as exec_error:
                        logger.error(
                            "HITL action execution failed",
                            error=str(exec_error),
                            agent=agent_name,
                            method=execution_method
                        )
                        reply = f"Action could not be executed: {str(exec_error)}"
                else:
                    # User rejected
                    reply = f"Action cancelled: {decision.reason or 'User declined the request.'}"
                    logger.info("HITL action rejected by user", event_id=event_id, reason=decision.reason)
            
            except Exception as hitl_error:
                logger.error("HITL confirmation failed", error=str(hitl_error), event_id=event_id)
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
        
        return SessionResponse(
            session_id=session_id,
            conversation_id=session_id,
            reply=reply,
            metadata=request.metadata,
            memories=memories,
            citations=citations,
            structured_data=structured_data,
            intent=intent,
            tool_calls=tool_calls,
        )
        
    except Exception as e:
        import traceback
        logger.error("Failed to add message", error=str(e), session_id=session_id, traceback=traceback.format_exc())
        # Fallback to stub response
        return SessionResponse(
            session_id=session_id,
            conversation_id=session_id,
            reply=f"Received your message: {request.message}. There was an error processing it.",
            metadata={**request.metadata, "error": str(e)},
        )

