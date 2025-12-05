"""
HITL API endpoints for ADK Runtime
Provides Server-Sent Events for real-time HITL confirmations
"""

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
import asyncio
from typing import AsyncGenerator, Dict
import structlog
import json

from nodus_adk_runtime.middleware.auth import get_current_user, UserContext
from nodus_adk_runtime.services.hitl_service import (
    HITLService,
    HITLEvent,
    HITLDecision,
    get_hitl_service
)

logger = structlog.get_logger()

router = APIRouter(prefix="/v1/hitl", tags=["hitl"])

# In-memory event queue (per user)
# In production: use Redis pub/sub
hitl_event_queues: Dict[str, asyncio.Queue] = {}


def get_user_queue(user_id: str) -> asyncio.Queue:
    """Get or create event queue for user"""
    if user_id not in hitl_event_queues:
        hitl_event_queues[user_id] = asyncio.Queue()
        logger.info("Created event queue for user", user_id=user_id)
    return hitl_event_queues[user_id]


@router.get("/events")
async def hitl_events_stream(
    user_ctx: UserContext = Depends(get_current_user)
) -> EventSourceResponse:
    """
    Server-Sent Events stream for HITL confirmations
    
    Client connects and receives events when agents request human confirmation
    
    Event types:
    - connected: Initial connection confirmation
    - ping: Heartbeat to keep connection alive
    - confirmation_required: Agent needs human confirmation
    """
    user_id = user_ctx.sub
    logger.info("HITL SSE client connected", user_id=user_id)
    
    async def event_generator() -> AsyncGenerator[dict, None]:
        queue = get_user_queue(user_id)
        
        # Send initial connection event
        yield {
            "event": "connected",
            "data": json.dumps({"status": "connected", "user_id": user_id})
        
        try:
            while True:
                # Wait for events (with timeout for heartbeat)
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=30.0
                    )
                    
                    # Obtener event_type (puede ser HITLEvent o SSEEvent para grabaciones)
                    event_type = getattr(event, 'event_type', None)
                    if event_type is None:
                        # Si no tiene event_type, intentar como HITLEvent
                        if hasattr(event, 'event_type'):
                            event_type = event.event_type
                        else:
                            logger.warning("Event without event_type, skipping", user_id=user_id)
                            continue
                    
                    # Logging (solo para HITL events que tienen event_id)
                    if hasattr(event, 'event_id'):
                        logger.info(
                            "Sending HITL event via SSE",
                            user_id=user_id,
                            event_type=event_type,
                            event_id=event.event_id,
                            metadata=getattr(event, 'metadata', None),
                            action_data=getattr(event, 'action_data', None)
                        )
                    else:
                        logger.info(
                            "Sending recording event via SSE",
                            user_id=user_id,
                            event_type=event_type
                        )
                    
                    # Send event to client
                    yield {
                        "event": event_type,
                        "data": event.model_dump_json()
                    }
                    
                except asyncio.TimeoutError:
                    # Heartbeat ping to keep connection alive
                    yield {
                        "event": "ping",
                        "data": json.dumps({"type": "ping", "timestamp": asyncio.get_event_loop().time()})
                    
        except asyncio.CancelledError:
            logger.info("HITL SSE client disconnected", user_id=user_id)
            raise
        except Exception as e:
            logger.error("Error in HITL SSE stream", user_id=user_id, error=str(e))
            raise
    
    return EventSourceResponse(event_generator())


@router.post("/{event_id}/decision")
async def submit_hitl_decision(
    event_id: str,
    decision: HITLDecision,
    user_ctx: UserContext = Depends(get_current_user)
):
    """
    Submit user decision for a HITL confirmation and resume the paused invocation
    
    Args:
        event_id: Event ID from the confirmation_required event
        decision: User's decision (approved: true/false, reason: optional)
        
    Returns:
        Confirmation of decision acceptance and resume status
    """
    logger.info(
        "HITL decision received",
        event_id=event_id,
        approved=decision.approved,
        user_id=user_ctx.sub
    )
    
    hitl_service = get_hitl_service()
    
    # Get stored event to retrieve metadata (including invocation_id)
    event = hitl_service.get_event(event_id)
    
    if not event:
        logger.warning(
            "HITL event not found for resumability",
            event_id=event_id,
            user_id=user_ctx.sub
        )
        # Fallback: try to resolve future if exists (legacy blocking mode)
        await hitl_service.store_decision(event_id, decision, user_ctx.sub)
        return {
            "status": "accepted",
            "event_id": event_id,
            "decision": decision.approved,
            "resumed": False,
            "timestamp": asyncio.get_event_loop().time()
        }
    
    # Extract metadata for resuming
    metadata = event.metadata or {}
    invocation_id = metadata.get('invocation_id')
    session_id = metadata.get('session_id')
    agent_name = metadata.get('agent')
    method = metadata.get('method')
    action_data = event.action_data
    
    logger.info(
        "Resuming paused invocation after HITL decision",
        event_id=event_id,
        invocation_id=invocation_id,
        session_id=session_id,
        approved=decision.approved,
        agent=agent_name,
        method=method
    )
    
    # Resume the paused invocation
    
    if not invocation_id or not session_id:
        logger.error(
            "Missing invocation_id or session_id for resuming",
            event_id=event_id,
            invocation_id=invocation_id,
            session_id=session_id
        )
        return {
            "status": "error",
            "error": "Missing invocation_id or session_id"
        }
    
    # Build agent and resume invocation
    from nodus_adk_runtime.api.assistant import _build_agent_for_user, get_session_service
    from google.adk.runners import Runner
    from google.adk.apps.app import ResumabilityConfig
    from google.genai import types
    
    try:
        # Build agent for user
        agent, memory_service = await _build_agent_for_user(user_ctx)
        
        # Create runner with resumability enabled
        session_service = get_session_service()
        resumability_config = ResumabilityConfig(is_resumable=True)
        
        runner = Runner(
            app_name="personal_assistant",
            agent=agent,
            session_service=session_service,
            memory_service=memory_service,
            resumability_config=resumability_config,
        )
        
        # Prepare continuation message based on decision
        if decision.approved:
            # User approved: continue with action execution
            # The tool will execute automatically when resumed
            continuation_message = types.Content(
                role="user",
                parts=[types.Part.from_text(
                    text=f"User approved the action. Please proceed with execution."
                )],
            )
        else:
            # User rejected: cancel action
            continuation_message = types.Content(
                role="user",
                parts=[types.Part.from_text(
                    text=f"User rejected the action: {decision.reason or 'No reason provided'}. Please inform the user that the action was cancelled."
                )],
            )
        
        # Resume the paused invocation
        logger.info("Resuming invocation", invocation_id=invocation_id, session_id=session_id)
        
        response_parts = []
        async for resume_event in runner.run_async(
            user_id=user_ctx.sub,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=continuation_message,
        ):
            if hasattr(resume_event, 'content') and resume_event.content:
                for part in resume_event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_parts.append(part.text)
        
        final_reply = " ".join(response_parts) if response_parts else (
            "Action approved and executed successfully." if decision.approved 
            else f"Action cancelled: {decision.reason or 'User declined'}"
        )
        
        logger.info(
            "Invocation resumed successfully",
            event_id=event_id,
            invocation_id=invocation_id,
            approved=decision.approved,
            reply_length=len(final_reply)
        )
        
        # Cleanup: remove event from storage
        hitl_service.remove_event(event_id)
        
        return {
            "status": "accepted_and_resumed",
            "event_id": event_id,
            "decision": decision.approved,
            "invocation_id": invocation_id,
            "final_reply": final_reply,
            "timestamp": asyncio.get_event_loop().time()
        }
        
    except Exception as resume_error:
        logger.error(
            "Failed to resume invocation after HITL decision",
            error=str(resume_error),
            event_id=event_id,
            invocation_id=invocation_id,
            session_id=session_id
        )
        # Still store decision for legacy mode
        await hitl_service.store_decision(event_id, decision, user_ctx.sub)
        
        return {
            "status": "accepted_but_resume_failed",
            "event_id": event_id,
            "decision": decision.approved,
            "error": str(resume_error),
            "timestamp": asyncio.get_event_loop().time()
        }


@router.get("/health")
async def hitl_health_check():
    """Health check endpoint for HITL service"""
    return {
        "status": "healthy",
        "service": "hitl",
        "active_queues": len(hitl_event_queues)
    }
