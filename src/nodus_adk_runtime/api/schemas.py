"""
API Schemas

Pydantic models for request/response validation.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    """Request schema for creating a new session."""
    
    message: str = Field(..., description="User message")
    conversation_id: Optional[str] = Field(None, description="Existing conversation ID to reuse")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class MessageRequest(BaseModel):
    """Request schema for adding a message to an existing session."""
    
    message: str = Field(..., description="User message")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class SessionResponse(BaseModel):
    """Response schema for session operations."""
    
    session_id: str = Field(..., description="Session identifier")
    conversation_id: str = Field(..., description="Conversation identifier")
    reply: str = Field(..., description="Agent reply")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")

