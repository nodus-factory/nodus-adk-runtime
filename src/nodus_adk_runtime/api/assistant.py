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


def _build_agent_for_user(user_ctx: UserContext) -> Any:
    """
    Build Root Agent instance for a user.
    
    Args:
        user_ctx: User context
        
    Returns:
        Configured Root Agent instance
    """
    from nodus_adk_runtime.adapters.mcp_adapter import MCPAdapter
    from nodus_adk_runtime.adapters.qdrant_memory_service import QdrantMemoryService
    from nodus_adk_agents.root_agent import build_root_agent
    
    # Initialize adapters
    mcp_adapter = MCPAdapter(gateway_url=settings.mcp_gateway_url)
    memory_service = QdrantMemoryService(
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
    )
    
    # Build root agent
    agent = build_root_agent(
        mcp_adapter=mcp_adapter,
        memory_service=memory_service,
        user_context=user_ctx,
        config={
            "model": settings.adk_model,
        },
    )
    
    return agent


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
        agent = _build_agent_for_user(user_ctx)
        
        # Create session ID
        session_id = request.conversation_id or f"session_{user_ctx.sub}_{uuid.uuid4().hex[:8]}"
        conversation_id = request.conversation_id or session_id
        
        # Run agent with user message
        from google.adk.runners import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService
        from google.genai import types
        
        runner = Runner(
            app_name="personal_assistant",
            agent=agent,
            session_service=InMemorySessionService(),
        )
        
        # Create session
        session = await runner.session_service.create_session(
            app_name="personal_assistant",
            user_id=user_ctx.sub,
        )
        
        # Add user message
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=request.message)],
        )
        
        # Run agent
        response_parts = []
        async for event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session.id,
            new_message=user_content,
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_parts.append(part.text)
        
        reply = " ".join(response_parts) if response_parts else "I received your message."
        
        return SessionResponse(
            session_id=session_id,
            conversation_id=conversation_id,
            reply=reply,
            metadata=request.metadata,
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
        agent = _build_agent_for_user(user_ctx)
        
        # Run agent with user message
        from google.adk.runners import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService
        from google.genai import types
        
        runner = Runner(
            app_name="personal_assistant",
            agent=agent,
            session_service=InMemorySessionService(),
        )
        
        # Get or create session
        # TODO: Use persistent session service instead of InMemory
        session = await runner.session_service.create_session(
            app_name="personal_assistant",
            user_id=user_ctx.sub,
        )
        
        # Add user message
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=request.message)],
        )
        
        # Run agent
        response_parts = []
        async for event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session.id,
            new_message=user_content,
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_parts.append(part.text)
        
        reply = " ".join(response_parts) if response_parts else "I received your message."
        
        return SessionResponse(
            session_id=session_id,
            conversation_id=session_id,
            reply=reply,
            metadata=request.metadata,
        )
        
    except Exception as e:
        logger.error("Failed to add message", error=str(e), session_id=session_id)
        # Fallback to stub response
        return SessionResponse(
            session_id=session_id,
            conversation_id=session_id,
            reply=f"Received your message: {request.message}. There was an error processing it.",
            metadata={**request.metadata, "error": str(e)},
        )

