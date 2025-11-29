"""
Langfuse Tracing for ADK Agents (SDK-based)

Lightweight wrapper around Langfuse SDK for tracing agent executions.
This is a fallback when OpenTelemetry is not available or not working.

Usage:
    from nodus_adk_runtime.langfuse_tracer import start_trace, end_trace
    
    async def create_session(...):
        trace = start_trace("create_session", user_ctx=user_ctx, session_id=session_id)
        try:
            # Your code here
            result = ...
            end_trace(trace, success=True)
            return result
        except Exception as e:
            end_trace(trace, success=False, error=str(e))
            raise
"""

import logging
from typing import Optional, Any, Dict
from datetime import datetime
from contextlib import contextmanager

from .config import settings

logger = logging.getLogger(__name__)

# Global Langfuse client (lazy initialization)
_langfuse_client: Optional[Any] = None
_langfuse_available = False


def get_langfuse_client():
    """Get or create Langfuse client (lazy initialization)."""
    global _langfuse_client, _langfuse_available
    
    if _langfuse_client is not None:
        return _langfuse_client
    
    if not settings.langfuse_enabled:
        logger.debug("Langfuse tracing disabled (LANGFUSE_ENABLED=false)")
        return None
    
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.debug("Langfuse credentials not configured, tracing disabled")
        return None
    
    try:
        from langfuse import Langfuse
        
        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        _langfuse_available = True
        logger.info(
            "✅ Langfuse SDK tracer initialized",
            extra={"host": settings.langfuse_host}
        )
        return _langfuse_client
        
    except ImportError:
        logger.warning("Langfuse SDK not installed, tracing disabled")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize Langfuse client: {e}")
        return None


def start_trace(
    operation_name: str,
    user_ctx: Optional[Any] = None,
    session_id: Optional[str] = None,
    input_data: Optional[Dict] = None,
) -> Optional[Any]:
    """
    Start a Langfuse trace for an agent operation.
    
    Args:
        operation_name: Name of the operation (e.g., "create_session", "add_message")
        user_ctx: User context object (with sub, tenant_id, email attributes)
        session_id: Session ID for grouping related operations
        input_data: Optional input data to log
    
    Returns:
        Langfuse trace object or None if tracing is disabled
    """
    client = get_langfuse_client()
    
    if client is None:
        return None
    
    # Extract user info
    user_id = getattr(user_ctx, 'sub', None) if user_ctx else None
    tenant_id = getattr(user_ctx, 'tenant_id', None) if user_ctx else None
    user_email = getattr(user_ctx, 'email', None) if user_ctx else None
    
    try:
        span = client.start_span(
            name=operation_name,
            user_id=user_id,
            session_id=session_id,
            metadata={
                "tenant_id": tenant_id,
                "user_email": user_email,
                "operation": operation_name,
                "service": "nodus-adk-runtime",
            },
            input=input_data,
        )
        logger.info(f"🔍 Langfuse trace started: {operation_name}", extra={
            "operation": operation_name,
            "user_id": user_id,
            "session_id": session_id,
        })
        return span
    except Exception as e:
        logger.warning(f"Failed to start Langfuse trace: {e}")
        return None


def end_trace(
    span: Optional[Any],
    success: bool = True,
    error: Optional[str] = None,
    output_data: Optional[Dict] = None,
) -> None:
    """
    End a Langfuse span.
    
    Args:
        span: Langfuse span object from start_trace()
        success: Whether the operation succeeded
        error: Error message if operation failed
        output_data: Optional output data to log
    """
    if span is None:
        return
    
    try:
        output = output_data or {}
        output["status"] = "success" if success else "error"
        if error:
            output["error"] = error
        
        span.end(
            output=output,
            level="ERROR" if not success else "DEFAULT",
        )
        logger.info(f"🔍 Langfuse span ended: {span.name} (success={success})")
    except Exception as e:
        logger.warning(f"Failed to end Langfuse span: {e}")
    
    # Flush to ensure span is sent
    try:
        client = get_langfuse_client()
        if client:
            client.flush()
    except:
        pass

