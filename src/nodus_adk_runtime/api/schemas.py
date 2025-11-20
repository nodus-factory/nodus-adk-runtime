"""
API Schemas

Pydantic models for request/response validation.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from enum import Enum


class SessionCreateRequest(BaseModel):
    """Request schema for creating a new session."""
    
    message: str = Field(..., description="User message")
    conversation_id: Optional[str] = Field(None, description="Existing conversation ID to reuse")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class MessageRequest(BaseModel):
    """Request schema for adding a message to an existing session."""
    
    message: str = Field(..., description="User message")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class Citation(BaseModel):
    """Citation or source reference."""
    
    source_type: str = Field(..., description="Type of source: mcp_tool, memory, document, etc.")
    source_id: Optional[str] = Field(None, description="ID of the source")
    title: Optional[str] = Field(None, description="Title or name of the source")
    url: Optional[str] = Field(None, description="URL to the source if available")
    snippet: Optional[str] = Field(None, description="Relevant text snippet")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class StructuredData(BaseModel):
    """Structured data from tool execution."""
    
    type: str = Field(..., description="Type: table, list, card, etc.")
    title: Optional[str] = Field(None, description="Title for the structured data")
    data: Dict[str, Any] = Field(..., description="The actual structured data")


class HITLEvent(BaseModel):
    """HITL (Human-In-The-Loop) event."""
    
    event_id: str = Field(..., description="Unique identifier for the event")
    event_type: str = Field(..., description="Type of HITL request: confirmation, approval, etc.")
    action_description: str = Field(..., description="Human-readable description of the action")
    action_data: Dict[str, Any] = Field(..., description="Data about the action to be taken")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class HITLDecisionRequest(BaseModel):
    """Request to submit a HITL decision."""
    
    approved: bool = Field(..., description="Whether the action is approved")
    reason: Optional[str] = Field(None, description="Optional reason for the decision")


class HITLDecisionResponse(BaseModel):
    """Response after submitting a HITL decision."""
    
    event_id: str = Field(..., description="The event ID")
    status: str = Field(..., description="Status after decision")
    message: str = Field(..., description="Confirmation message")


class SessionResponse(BaseModel):
    """Response schema for session operations."""
    
    session_id: str = Field(..., description="Session identifier")
    conversation_id: str = Field(..., description="Conversation identifier")
    reply: str = Field(..., description="Agent reply")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")
    memories: List[Dict[str, Any]] = Field(
        default_factory=list, description="List of retrieved memories"
    )
    citations: List[Citation] = Field(
        default_factory=list, description="List of citations/sources"
    )
    structured_data: List[StructuredData] = Field(
        default_factory=list, description="Structured results from tools"
    )
    intent: Optional[str] = Field(None, description="Detected user intent")
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list, description="List of tool calls made during execution"
    )

