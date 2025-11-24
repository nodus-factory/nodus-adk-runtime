"""
HITL Service for managing confirmation requests and decisions
"""

from typing import Optional, Dict
import asyncio
from pydantic import BaseModel, Field
from datetime import datetime
import structlog

logger = structlog.get_logger()


class HITLEvent(BaseModel):
    """HITL Event model for SSE streaming"""
    event_id: str
    event_type: str  # "confirmation_required"
    action_description: str
    action_data: dict
    metadata: Optional[dict] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class HITLDecision(BaseModel):
    """User decision for HITL confirmation"""
    approved: bool
    reason: Optional[str] = None


class HITLService:
    """Service for managing HITL events and decisions"""
    
    def __init__(self):
        # In-memory storage (use Redis in production)
        self.pending_decisions: Dict[str, asyncio.Future] = {}
        self._instance_id = id(self)
        logger.info("HITLService initialized", instance_id=self._instance_id)
    
    async def request_confirmation(
        self,
        user_id: str,
        event_id: str,
        action_description: str,
        action_data: dict,
        metadata: Optional[dict] = None,
        timeout: float = 300.0
    ) -> HITLDecision:
        """
        Request human confirmation for an action
        
        This method:
        1. Creates an event
        2. Sends it via SSE to the user
        3. Waits for user decision
        4. Returns the decision
        
        Args:
            user_id: User ID to request confirmation from
            event_id: Unique event ID
            action_description: Human-readable description of the action
            action_data: Action parameters
            metadata: Optional metadata
            timeout: Timeout in seconds (default 5 minutes)
            
        Returns:
            HITLDecision with user's approval/rejection
        """
        from nodus_adk_runtime.api.hitl import get_user_queue
        
        logger.info(
            "HITL confirmation requested",
            user_id=user_id,
            event_id=event_id,
            action=action_description
        )
        
        # Create event
        event = HITLEvent(
            event_id=event_id,
            event_type="confirmation_required",
            action_description=action_description,
            action_data=action_data,
            metadata=metadata
        )
        
        # Create future for decision
        decision_future: asyncio.Future[HITLDecision] = asyncio.Future()
        self.pending_decisions[event_id] = decision_future
        
        # Send event to user via SSE
        queue = get_user_queue(user_id)
        await queue.put(event)
        
        logger.info("HITL event queued for SSE", event_id=event_id, user_id=user_id)
        
        # Wait for decision (with timeout)
        try:
            decision = await asyncio.wait_for(
                decision_future,
                timeout=timeout
            )
            logger.info(
                "HITL decision received",
                event_id=event_id,
                approved=decision.approved
            )
            return decision
            
        except asyncio.TimeoutError:
            logger.warning("HITL decision timeout", event_id=event_id)
            # Auto-reject on timeout
            return HITLDecision(approved=False, reason="Timeout - no response within 5 minutes")
        finally:
            # Cleanup
            if event_id in self.pending_decisions:
                del self.pending_decisions[event_id]
    
    async def store_decision(
        self,
        event_id: str,
        decision: HITLDecision,
        user_id: str
    ):
        """
        Store user decision and resolve waiting future
        
        Args:
            event_id: Event ID
            decision: User's decision (approve/reject)
            user_id: User ID who made the decision
        """
        logger.info(
            "Storing HITL decision",
            event_id=event_id,
            approved=decision.approved,
            user_id=user_id
        )
        
        if event_id in self.pending_decisions:
            future = self.pending_decisions[event_id]
            if not future.done():
                future.set_result(decision)
                logger.info("HITL decision future resolved", event_id=event_id)
            else:
                logger.warning("HITL decision future already resolved", event_id=event_id)
        else:
            logger.warning(
                "Decision for unknown event",
                event_id=event_id,
                available_events=list(self.pending_decisions.keys())
            )


# Singleton instance
_hitl_service_instance: Optional[HITLService] = None


def get_hitl_service() -> HITLService:
    """Get singleton HITL service instance"""
    global _hitl_service_instance
    if _hitl_service_instance is None:
        _hitl_service_instance = HITLService()
        logger.info("HITLService singleton created")
    return _hitl_service_instance


