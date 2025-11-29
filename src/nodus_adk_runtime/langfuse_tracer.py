"""
Langfuse Tracing for ADK Agents (SDK-based)

Lightweight wrapper around Langfuse SDK for tracing agent executions.
This is a fallback when OpenTelemetry is not available or not working.

Usage:
    from nodus_adk_runtime.langfuse_tracer import trace_agent_execution
    
    @trace_agent_execution("create_session")
    async def create_session(...):
        # Your code here
        pass
"""

import logging
import functools
from typing import Optional, Any, Callable
from datetime import datetime

from .config import settings

logger = logging.getLogger(__name__)

# Global Langfuse client (lazy initialization)
_langfuse_client: Optional[Any] = None
_langfuse_available = False


def _get_langfuse_client():
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


def trace_agent_execution(operation_name: str):
    """
    Decorator to trace agent execution with Langfuse.
    
    This is a lightweight, non-invasive decorator that:
    - Creates a Langfuse trace for the operation
    - Captures input/output
    - Records timing and errors
    - Gracefully degrades if Langfuse is not available
    
    Args:
        operation_name: Name of the operation (e.g., "create_session", "add_message")
    
    Example:
        @trace_agent_execution("create_session")
        async def create_session(request, user_ctx):
            # Your code here
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            client = _get_langfuse_client()
            
            # If Langfuse not available, just execute the function
            if client is None:
                return await func(*args, **kwargs)
            
            # Extract user context if available
            user_ctx = kwargs.get('user_ctx')
            user_id = getattr(user_ctx, 'sub', None) if user_ctx else None
            tenant_id = getattr(user_ctx, 'tenant_id', None) if user_ctx else None
            
            # Extract request if available
            request = kwargs.get('request')
            session_id = kwargs.get('session_id')
            input_data = None
            if request:
                if hasattr(request, 'message'):
                    # Truncate message for privacy
                    input_data = {"message": request.message[:100] if request.message else None}
                elif hasattr(request, 'dict'):
                    try:
                        input_data = request.dict()
                    except:
                        pass
            
            # Create trace
            try:
                trace = client.trace(
                    name=operation_name,
                    user_id=user_id,
                    session_id=session_id,
                    metadata={
                        "tenant_id": tenant_id,
                        "operation": operation_name,
                        "service": "nodus-adk-runtime",
                    },
                    input=input_data,
                )
            except Exception as e:
                logger.debug(f"Failed to create Langfuse trace: {e}")
                trace = None
            
            start_time = datetime.now()
            
            try:
                # Execute the actual function
                result = await func(*args, **kwargs)
                
                # Record success
                if trace:
                    try:
                        trace.update(
                            output={"status": "success"},
                            end_time=datetime.now(),
                        )
                    except Exception as e:
                        logger.debug(f"Failed to update Langfuse trace: {e}")
                
                return result
                
            except Exception as e:
                # Record error
                if trace:
                    try:
                        trace.update(
                            output={"status": "error", "error": str(e)},
                            end_time=datetime.now(),
                            level="ERROR",
                        )
                    except Exception as trace_error:
                        logger.debug(f"Failed to update Langfuse trace with error: {trace_error}")
                raise
            
            finally:
                # Flush to ensure trace is sent
                if client:
                    try:
                        client.flush()
                    except:
                        pass  # Don't fail if flush fails
        
        # Return async wrapper (we only need async for ADK)
        return async_wrapper
    
    return decorator

