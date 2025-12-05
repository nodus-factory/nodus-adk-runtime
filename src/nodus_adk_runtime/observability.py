"""
Observability Integration - OpenTelemetry + Langfuse

Configures OpenTelemetry to send traces to Langfuse using OTLP protocol.
Leverages Google ADK's built-in OpenTelemetry instrumentation for automatic
tracing of agent invocations, tool calls, and LLM requests.

Architecture:
    Application Code
         ↓
    ADK Instrumentation (automatic)
         ↓
    OpenTelemetry SDK
         ↓
    OTLP Exporter (HTTP)
         ↓
    Langfuse Backend

Usage:
    from nodus_adk_runtime.observability import setup_telemetry
    
    # At application startup
    setup_telemetry()
    
    # Now all ADK operations are automatically traced!
"""

import logging
import os
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor

# Import ADK's telemetry setup for advanced configuration
try:
    from google.adk.telemetry.setup import maybe_set_otel_providers
    ADK_TELEMETRY_AVAILABLE = True
except ImportError:
    ADK_TELEMETRY_AVAILABLE = False
    logging.warning("ADK telemetry module not available - using basic OpenTelemetry setup")

from .config import settings

logger = logging.getLogger(__name__)


def setup_telemetry() -> bool:
    """
    Setup OpenTelemetry with Langfuse backend via OTLP.
    
    This function configures OpenTelemetry to automatically instrument
    all Google ADK operations (agent invocations, tool calls, LLM requests)
    and send traces to Langfuse for observability.
    
    Features:
        - Automatic instrumentation via ADK built-in telemetry
        - OTLP protocol for standard trace export
        - Langfuse ingestion endpoint
        - Configurable sampling
        - PII protection via environment variables
        - Graceful degradation if Langfuse is not configured
    
    Environment Variables:
        LANGFUSE_ENABLED: Enable/disable Langfuse integration (default: true)
        LANGFUSE_HOST: Langfuse server URL
        LANGFUSE_PUBLIC_KEY: Langfuse API public key
        LANGFUSE_SECRET_KEY: Langfuse API secret key
        OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint (default: {LANGFUSE_HOST}/api/public/ingestion)
        OTEL_SERVICE_NAME: Service name for traces
        OTEL_TRACES_SAMPLER: Sampling strategy
        OTEL_TRACES_SAMPLER_ARG: Sampling rate (0.0-1.0)
        ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS: Include message content in spans
    
    Returns:
        True if setup successful, False otherwise
    """
    if not settings.langfuse_enabled:
        logger.info("Langfuse observability disabled (LANGFUSE_ENABLED=false)")
        return False
    
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning(
            "⚠️  Langfuse credentials not configured. "
            "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY. "
            "Traces will not be sent to Langfuse."
        )
        return False
    
    try:
        # Option 1: Use ADK's advanced setup if available (recommended)
        if ADK_TELEMETRY_AVAILABLE:
            logger.info("Using ADK's telemetry setup (advanced configuration)")
            return _setup_with_adk_telemetry()
        
        # Option 2: Basic OpenTelemetry setup
        else:
            logger.info("Using basic OpenTelemetry setup")
            return _setup_basic_telemetry()
            
    except Exception as e:
        logger.error(f"❌ Failed to initialize observability: {e}", exc_info=True)
        return False


def _setup_with_adk_telemetry() -> bool:
    """
    Setup telemetry using ADK's maybe_set_otel_providers.
    
    This leverages ADK's built-in telemetry configuration which
    auto-detects OTEL_* environment variables and sets up providers.
    """
    # Create resource with service metadata
    resource = Resource.create({
        SERVICE_NAME: settings.otel_service_name,
        SERVICE_VERSION: "1.0.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        "service.namespace": "nodus-adk",
        "service.instance.id": os.getenv("HOSTNAME", "localhost"),
    })
    
    # ADK's setup automatically detects OTEL_EXPORTER_OTLP_ENDPOINT
    # and other OTEL_* environment variables
    maybe_set_otel_providers(otel_resource=resource)
    
    # Instrument common libraries
    _setup_instrumentation()
    
    logger.info(
        f"✅ OpenTelemetry + Langfuse initialized successfully (ADK mode)\n"
        f"   - Service: {settings.otel_service_name}\n"
        f"   - Endpoint: {settings.otel_exporter_otlp_endpoint or 'auto-detected'}\n"
        f"   - Sampling: {settings.otel_traces_sampler} ({settings.otel_traces_sampler_arg})\n"
        f"   - PII Protection: {'enabled' if not settings.adk_capture_message_content_in_spans else 'disabled'}"
    )
    
    return True


def _setup_basic_telemetry() -> bool:
    """
    Basic OpenTelemetry setup without ADK's advanced features.
    
    Used as fallback if ADK telemetry module is not available.
    """
    # Create resource
    resource = Resource.create({
        SERVICE_NAME: settings.otel_service_name,
        SERVICE_VERSION: "1.0.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })
    
    # Create tracer provider
    tracer_provider = TracerProvider(resource=resource)
    
    # Configure OTLP exporter to Langfuse
    otlp_endpoint = (
        settings.otel_exporter_otlp_endpoint 
        or f"{settings.langfuse_host}/api/public/ingestion"
    )
    
    # Langfuse authentication via headers (OTLP standard)
    headers = {
        "Authorization": f"Bearer {settings.langfuse_public_key}:{settings.langfuse_secret_key}",
        "Content-Type": "application/json",
    }
    
    otlp_exporter = OTLPSpanExporter(
        endpoint=otlp_endpoint,
        headers=headers,
    )
    
    # Add batch span processor for performance
    span_processor = BatchSpanProcessor(otlp_exporter)
    tracer_provider.add_span_processor(span_processor)
    
    # Set as global tracer provider
    trace.set_tracer_provider(tracer_provider)
    
    # Instrument common libraries
    _setup_instrumentation()
    
    logger.info(
        f"✅ OpenTelemetry + Langfuse initialized successfully (basic mode)\n"
        f"   - Service: {settings.otel_service_name}\n"
        f"   - Endpoint: {otlp_endpoint}\n"
        f"   - Sampling: {settings.otel_traces_sampler} ({settings.otel_traces_sampler_arg})"
    )
    
    return True


def _setup_instrumentation():
    """Setup automatic instrumentation for common libraries."""
    try:
        # Instrument HTTP client (for MCP calls, etc.)
        HTTPXClientInstrumentor().instrument()
        logger.debug("✅ HTTPX instrumentation enabled")
    except Exception as e:
        logger.warning(f"Failed to instrument HTTPX: {e}")
    
    try:
        # Instrument asyncio tasks
        AsyncioInstrumentor().instrument()
        logger.debug("✅ Asyncio instrumentation enabled")
    except Exception as e:
        logger.warning(f"Failed to instrument asyncio: {e}")


def get_tracer(name: str) -> trace.Tracer:
    """
    Get a tracer for manual instrumentation.
    
    Args:
        name: Name of the tracer (usually __name__)
    
    Returns:
        OpenTelemetry Tracer instance
    
    Usage:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("my_operation"):
            # Your code here
            pass
    """
    return trace.get_tracer(name)


def add_span_attributes(attributes: dict):
    """
    Add custom attributes to the current span.
    
    Args:
        attributes: Dictionary of attribute name -> value
    
    Usage:
        add_span_attributes({
            "tenant_id": "acme",
            "user_id": "user-123",
            "custom_metadata": "important_value"
        })
    """
    span = trace.get_current_span()
    if span.is_recording():
        for key, value in attributes.items():
            span.set_attribute(key, value)


# Decorator for easy function tracing
def traced(span_name: Optional[str] = None, attributes: Optional[dict] = None):
    """
    Decorator to automatically trace a function with custom attributes.
    
    Args:
        span_name: Optional custom span name (default: module.function)
        attributes: Optional attributes to add to span
    
    Usage:
        @traced("process_message", {"operation": "user_input"})
        async def process_message(message: str):
            return result
    """
    def decorator(func):
        import functools
        import inspect
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            name = span_name or f"{func.__module__}.{func.__name__}"
            
            with tracer.start_as_current_span(name) as span:
                # Add custom attributes
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                
                # Add function parameters as attributes
                sig = inspect.signature(func)
                try:
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    
                    for param_name, param_value in bound.arguments.items():
                        if not param_name.startswith("_"):
                            # Truncate long values
                            str_value = str(param_value)[:200]
                            span.set_attribute(f"function.param.{param_name}", str_value)
                except Exception:
                    pass  # Skip if binding fails
                
                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("function.status", "success")
                    return result
                except Exception as e:
                    span.set_attribute("error", True)
                    span.set_attribute("error.type", type(e).__name__)
                    span.set_attribute("error.message", str(e))
                    span.record_exception(e)
                    raise
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            name = span_name or f"{func.__module__}.{func.__name__}"
            
            with tracer.start_as_current_span(name) as span:
                # Add custom attributes
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("function.status", "success")
                    return result
                except Exception as e:
                    span.set_attribute("error", True)
                    span.set_attribute("error.type", type(e).__name__)
                    span.set_attribute("error.message", str(e))
                    span.record_exception(e)
                    raise
        
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator



