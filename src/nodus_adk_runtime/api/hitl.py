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
        }
        
        try:
            while True:
                # Wait for events (with timeout for heartbeat)
                try:
                    event: HITLEvent = await asyncio.wait_for(
                        queue.get(),
                        timeout=30.0
                    )
                    
                    logger.info(
                        "Sending HITL event via SSE",
                        user_id=user_id,
                        event_type=event.event_type,
                        event_id=event.event_id,
                        metadata=event.metadata,
                        action_data=event.action_data
                    )
                    
                    # Send event to client
                    yield {
                        "event": event.event_type,
                        "data": event.model_dump_json()
                    }
                    
                except asyncio.TimeoutError:
                    # Heartbeat ping to keep connection alive
                    yield {
                        "event": "ping",
                        "data": json.dumps({"type": "ping", "timestamp": asyncio.get_event_loop().time()})
                    }
                    
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
    Submit user decision for a HITL confirmation
    
    Args:
        event_id: Event ID from the confirmation_required event
        decision: User's decision (approved: true/false, reason: optional)
        
    Returns:
        Confirmation of decision acceptance
    """
    logger.info(
        "HITL decision received",
        event_id=event_id,
        approved=decision.approved,
        user_id=user_ctx.sub
    )
    
    # Store decision (resolves waiting future in HITLService)
    hitl_service = get_hitl_service()
    await hitl_service.store_decision(event_id, decision, user_ctx.sub)
    
    return {
        "status": "accepted",
        "event_id": event_id,
        "decision": decision.approved,
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
