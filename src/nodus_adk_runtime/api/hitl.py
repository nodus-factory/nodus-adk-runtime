"""
HITL (Human-In-The-Loop) API

Endpoints for managing human confirmations and decisions.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional, Dict, Any
import structlog
import asyncio
import json
from collections import defaultdict

from .schemas import HITLEvent, HITLDecisionRequest, HITLDecisionResponse
from ..middleware.auth import get_current_user, UserContext

logger = structlog.get_logger()

router = APIRouter(prefix="/v1/hitl", tags=["hitl"])

# In-memory store for pending HITL events (in production, use Redis or similar)
pending_events: Dict[str, Dict[str, Any]] = {}
event_subscribers: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)


@router.get("/events")
async def hitl_events_stream(
    user_ctx: UserContext = Depends(get_current_user),
):
    """
    Server-Sent Events (SSE) stream for HITL events.
    
    Clients should connect to this endpoint to receive real-time
    HITL confirmation requests.
    """
    logger.info("Client connected to HITL events stream", user_id=user_ctx.sub)
    
    async def event_generator():
        """Generate SSE events for this user."""
        queue = event_subscribers[user_ctx.sub]
        
        try:
            # Send initial connection event
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            
            while True:
                # Wait for new events
                event_data = await asyncio.wait_for(queue.get(), timeout=30.0)
                
                # Send event to client
                yield f"data: {json.dumps(event_data)}\n\n"
                
        except asyncio.TimeoutError:
            # Send keepalive ping
            yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except asyncio.CancelledError:
            logger.info("HITL stream cancelled", user_id=user_ctx.sub)
        except Exception as e:
            logger.error("Error in HITL stream", error=str(e), user_id=user_ctx.sub)
        finally:
            # Cleanup on disconnect
            if user_ctx.sub in event_subscribers:
                del event_subscribers[user_ctx.sub]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{event_id}/decision", response_model=HITLDecisionResponse)
async def submit_hitl_decision(
    event_id: str,
    decision: HITLDecisionRequest,
    user_ctx: UserContext = Depends(get_current_user),
):
    """
    Submit a decision for a pending HITL event.
    
    Args:
        event_id: The ID of the HITL event
        decision: The user's decision (approve/reject + optional reason)
    """
    logger.info(
        "HITL decision received",
        event_id=event_id,
        user_id=user_ctx.sub,
        approved=decision.approved,
    )
    
    # Verify event exists and belongs to this user
    if event_id not in pending_events:
        raise HTTPException(status_code=404, detail="HITL event not found")
    
    event_data = pending_events[event_id]
    if event_data.get("user_id") != user_ctx.sub:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to decide on this event"
        )
    
    # Store decision
    event_data["decision"] = {
        "approved": decision.approved,
        "reason": decision.reason,
        "decided_at": asyncio.get_event_loop().time(),
    }
    event_data["status"] = "decided"
    
    logger.info(
        "HITL decision processed",
        event_id=event_id,
        approved=decision.approved,
    )
    
    return HITLDecisionResponse(
        event_id=event_id,
        status="decided",
        message="Decision received successfully",
    )


async def publish_hitl_event(
    user_id: str,
    event_id: str,
    event_type: str,
    action_description: str,
    action_data: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Publish a HITL event to the user's event stream.
    
    This function should be called by agents when they need
    human confirmation before proceeding with an action.
    """
    event_data = {
        "type": "hitl_request",
        "event_id": event_id,
        "event_type": event_type,
        "action_description": action_description,
        "action_data": action_data,
        "metadata": metadata or {},
        "timestamp": asyncio.get_event_loop().time(),
    }
    
    # Store pending event
    pending_events[event_id] = {
        "user_id": user_id,
        "status": "pending",
        **event_data,
    }
    
    # Send to user's event stream if connected
    if user_id in event_subscribers:
        await event_subscribers[user_id].put(event_data)
        logger.info("HITL event published", event_id=event_id, user_id=user_id)
    else:
        logger.warning(
            "User not connected to HITL stream",
            event_id=event_id,
            user_id=user_id,
        )


async def wait_for_hitl_decision(
    event_id: str,
    timeout: float = 300.0,
) -> Dict[str, Any]:
    """
    Wait for a HITL decision to be made.
    
    Args:
        event_id: The ID of the HITL event
        timeout: Maximum time to wait for decision (seconds)
    
    Returns:
        The decision data
    
    Raises:
        asyncio.TimeoutError: If timeout is reached
        KeyError: If event doesn't exist
    """
    start_time = asyncio.get_event_loop().time()
    
    while True:
        if event_id not in pending_events:
            raise KeyError(f"HITL event {event_id} not found")
        
        event_data = pending_events[event_id]
        
        if event_data.get("status") == "decided":
            return event_data["decision"]
        
        # Check timeout
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed >= timeout:
            raise asyncio.TimeoutError(f"HITL decision timeout after {timeout}s")
        
        # Wait a bit before checking again
        await asyncio.sleep(0.5)

