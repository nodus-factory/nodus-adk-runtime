"""
Test HITL Resumability Feature

Tests for Phase 1, 2, and 3 of HITL resumability implementation:
- Phase 1: Tools marked as long_running
- Phase 2: Non-blocking event creation
- Phase 3: Resume after decision
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from nodus_adk_runtime.services.hitl_service import (
    HITLService,
    HITLEvent,
    HITLDecision,
    get_hitl_service
)


class TestHITLService:
    """Test HITL Service functionality"""
    
    def test_service_initialization(self):
        """Test that HITLService initializes correctly"""
        service = HITLService()
        assert service.pending_decisions == {}
        assert service.pending_events == {}
        assert service._instance_id is not None
    
    def test_create_event_async_stores_event(self):
        """Test that create_event_async stores event for resumability"""
        service = HITLService()
        event_id = "test_event_123"
        metadata = {
            'invocation_id': 'inv_123',
            'session_id': 'session_123',
            'agent': 'test_agent',
            'method': 'test_method'
        }
        
        # Mock the queue
        with patch('nodus_adk_runtime.services.hitl_service.get_user_queue') as mock_queue:
            mock_queue_instance = AsyncMock()
            mock_queue.return_value = mock_queue_instance
            
            # Create event
            result = asyncio.run(service.create_event_async(
                user_id="user_123",
                event_id=event_id,
                action_description="Test action",
                action_data={"param": "value"},
                metadata=metadata
            ))
            
            # Verify event was stored
            assert event_id in service.pending_events
            stored_event = service.pending_events[event_id]
            assert stored_event.event_id == event_id
            assert stored_event.metadata == metadata
            assert result == event_id
    
    def test_get_event_retrieves_stored_event(self):
        """Test that get_event retrieves stored event"""
        service = HITLService()
        event_id = "test_event_456"
        
        # Create and store event manually
        event = HITLEvent(
            event_id=event_id,
            event_type="confirmation_required",
            action_description="Test",
            action_data={},
            metadata={'invocation_id': 'inv_456'}
        )
        service.pending_events[event_id] = event
        
        # Retrieve event
        retrieved = service.get_event(event_id)
        assert retrieved is not None
        assert retrieved.event_id == event_id
        assert retrieved.metadata['invocation_id'] == 'inv_456'
    
    def test_get_event_returns_none_for_missing_event(self):
        """Test that get_event returns None for non-existent event"""
        service = HITLService()
        result = service.get_event("non_existent")
        assert result is None
    
    def test_remove_event_cleans_up(self):
        """Test that remove_event removes event from storage"""
        service = HITLService()
        event_id = "test_event_789"
        
        # Store event
        event = HITLEvent(
            event_id=event_id,
            event_type="confirmation_required",
            action_description="Test",
            action_data={}
        )
        service.pending_events[event_id] = event
        
        # Verify it exists
        assert event_id in service.pending_events
        
        # Remove it
        service.remove_event(event_id)
        
        # Verify it's gone
        assert event_id not in service.pending_events
    
    def test_store_decision_resolves_future(self):
        """Test that store_decision resolves waiting future (legacy mode)"""
        service = HITLService()
        event_id = "test_event_future"
        
        # Create future
        future = asyncio.Future()
        service.pending_decisions[event_id] = future
        
        # Store decision
        decision = HITLDecision(approved=True, reason="Test reason")
        asyncio.run(service.store_decision(event_id, decision, "user_123"))
        
        # Verify future was resolved
        assert future.done()
        result = asyncio.run(asyncio.wrap_future(future))
        assert result.approved is True
        assert result.reason == "Test reason"


class TestHITLResumabilityFlow:
    """Test the complete HITL resumability flow"""
    
    @pytest.mark.asyncio
    async def test_complete_flow_create_and_resume(self):
        """Test complete flow: create event, then resume"""
        service = HITLService()
        event_id = "flow_test_123"
        invocation_id = "inv_flow_123"
        session_id = "session_flow_123"
        
        metadata = {
            'invocation_id': invocation_id,
            'session_id': session_id,
            'agent': 'test_agent',
            'method': 'test_method'
        }
        
        # Phase 2: Create event (non-blocking)
        with patch('nodus_adk_runtime.services.hitl_service.get_user_queue') as mock_queue:
            mock_queue_instance = AsyncMock()
            mock_queue.return_value = mock_queue_instance
            
            await service.create_event_async(
                user_id="user_123",
                event_id=event_id,
                action_description="Test action",
                action_data={"param": "value"},
                metadata=metadata
            )
        
        # Verify event stored
        assert event_id in service.pending_events
        
        # Phase 3: Retrieve event for resuming
        event = service.get_event(event_id)
        assert event is not None
        assert event.metadata['invocation_id'] == invocation_id
        assert event.metadata['session_id'] == session_id
        
        # Cleanup
        service.remove_event(event_id)
        assert event_id not in service.pending_events


def test_singleton_pattern():
    """Test that get_hitl_service returns singleton"""
    service1 = get_hitl_service()
    service2 = get_hitl_service()
    assert service1 is service2
    assert service1._instance_id == service2._instance_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

