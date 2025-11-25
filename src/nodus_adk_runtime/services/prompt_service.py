"""
Langfuse Prompt Service with comprehensive observability.

This service provides centralized prompt management using Langfuse,
with full tracing, metrics, and automatic fallback support.
"""

from typing import Dict, Any, Optional
import structlog
from langfuse import Langfuse
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


class PromptService:
    """
    Manages prompts from Langfuse with full observability.
    
    Features:
    - Fetch prompts from Langfuse by name and label
    - Automatic fallback to hardcoded prompts on error
    - Full OpenTelemetry tracing integration
    - Caching for performance
    - Rich metrics and logging
    """
    
    def __init__(
        self,
        langfuse_public_key: str,
        langfuse_secret_key: str,
        langfuse_host: str,
        enable_cache: bool = True
    ):
        self.langfuse = Langfuse(
            public_key=langfuse_public_key,
            secret_key=langfuse_secret_key,
            host=langfuse_host
        )
        self.enable_cache = enable_cache
        self._cache: Dict[str, Dict[str, Any]] = {}
        
        logger.info(
            "PromptService initialized",
            langfuse_host=langfuse_host,
            cache_enabled=enable_cache
        )
    
    def get_prompt(
        self,
        name: str,
        fallback: str,
        label: str = "production",
        cache_key: Optional[str] = None
    ) -> str:
        """
        Fetch prompt from Langfuse with automatic fallback and full observability.
        
        This method creates a span in the current trace with detailed attributes
        about the prompt loading process, including source, version, and cache status.
        
        Args:
            name: Prompt name in Langfuse
            fallback: Hardcoded prompt to use if Langfuse fails
            label: Version label (production, staging, etc.)
            cache_key: Optional cache key (defaults to f"{name}:{label}")
        
        Returns:
            Prompt string from Langfuse or fallback
        """
        cache_key = cache_key or f"{name}:{label}"
        
        # Create observability span
        with tracer.start_as_current_span("prompt_service.get_prompt") as span:
            # Set base attributes
            span.set_attribute("prompt.name", name)
            span.set_attribute("prompt.label", label)
            span.set_attribute("prompt.cache_enabled", self.enable_cache)
            
            # Check cache first
            if self.enable_cache and cache_key in self._cache:
                cached_data = self._cache[cache_key]
                
                span.set_attribute("prompt.cache_hit", True)
                span.set_attribute("prompt.source", cached_data["source"])
                span.set_attribute("prompt.version", cached_data.get("version", "unknown"))
                span.set_attribute("prompt.length", len(cached_data["text"]))
                
                span.add_event("prompt_loaded_from_cache", {
                    "cache_key": cache_key,
                    "source": cached_data["source"]
                })
                
                logger.debug(
                    "Prompt loaded from cache",
                    prompt_name=name,
                    label=label,
                    source=cached_data["source"],
                    version=cached_data.get("version", "unknown"),
                    cache_hit=True
                )
                
                return cached_data["text"]
            
            span.set_attribute("prompt.cache_hit", False)
            
            # Try to fetch from Langfuse
            try:
                span.add_event("fetching_from_langfuse", {
                    "prompt_name": name,
                    "label": label
                })
                
                prompt_obj = self.langfuse.get_prompt(
                    name,
                    label=label,
                    type="text"
                )
                
                prompt_text = prompt_obj.prompt
                prompt_version = prompt_obj.version
                
                # Set success attributes
                span.set_attribute("prompt.source", "langfuse")
                span.set_attribute("prompt.version", prompt_version)
                span.set_attribute("prompt.length", len(prompt_text))
                span.set_attribute("prompt.fallback_used", False)
                span.set_status(Status(StatusCode.OK))
                
                span.add_event("prompt_loaded_from_langfuse", {
                    "version": prompt_version,
                    "length": len(prompt_text)
                })
                
                logger.info(
                    "✅ Prompt loaded from Langfuse",
                    prompt_name=name,
                    prompt_version=prompt_version,
                    label=label,
                    source="langfuse",
                    fallback_used=False,
                    cache_hit=False,
                    length=len(prompt_text)
                )
                
                # Cache the result with metadata
                if self.enable_cache:
                    self._cache[cache_key] = {
                        "text": prompt_text,
                        "source": "langfuse",
                        "version": prompt_version,
                        "label": label
                    }
                
                return prompt_text
                
            except Exception as e:
                # Fallback path
                span.set_attribute("prompt.source", "fallback")
                span.set_attribute("prompt.version", "hardcoded")
                span.set_attribute("prompt.length", len(fallback))
                span.set_attribute("prompt.fallback_used", True)
                span.set_attribute("prompt.error", str(e))
                
                span.add_event("langfuse_fetch_failed", {
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                
                span.add_event("using_hardcoded_fallback", {
                    "fallback_length": len(fallback)
                })
                
                # Warning level (no error) - fallback is expected behavior
                span.set_status(Status(StatusCode.OK, "Using fallback"))
                
                logger.warning(
                    "⚠️  Failed to load prompt from Langfuse, using hardcoded fallback",
                    prompt_name=name,
                    label=label,
                    source="fallback",
                    fallback_used=True,
                    cache_hit=False,
                    error=str(e),
                    error_type=type(e).__name__,
                    length=len(fallback)
                )
                
                # Cache the fallback too (to avoid repeated failures)
                if self.enable_cache:
                    self._cache[cache_key] = {
                        "text": fallback,
                        "source": "fallback",
                        "version": "hardcoded",
                        "label": label
                    }
                
                return fallback
    
    def get_prompt_metadata(
        self,
        name: str,
        label: str = "production"
    ) -> Dict[str, Any]:
        """
        Get metadata about a cached prompt without fetching it.
        
        Returns:
            Dict with source, version, label, length (empty if not cached)
        """
        cache_key = f"{name}:{label}"
        
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            return {
                "source": cached_data["source"],
                "version": cached_data.get("version", "unknown"),
                "label": cached_data["label"],
                "length": len(cached_data["text"]),
                "cached": True
            }
        
        return {"cached": False}
    
    def clear_cache(self, name: Optional[str] = None):
        """Clear cache for a specific prompt or all prompts."""
        with tracer.start_as_current_span("prompt_service.clear_cache") as span:
            if name:
                keys_to_remove = [k for k in self._cache if k.startswith(f"{name}:")]
                count = len(keys_to_remove)
                
                for key in keys_to_remove:
                    del self._cache[key]
                
                span.set_attribute("cache.cleared_count", count)
                span.set_attribute("cache.prompt_name", name)
                
                logger.info(
                    "Prompt cache cleared",
                    prompt_name=name,
                    cleared_count=count
                )
            else:
                count = len(self._cache)
                self._cache.clear()
                
                span.set_attribute("cache.cleared_count", count)
                span.set_attribute("cache.cleared_all", True)
                
                logger.info(
                    "All prompt cache cleared",
                    cleared_count=count
                )
    
    def get_prompt_config(
        self,
        name: str,
        label: str = "production"
    ) -> Dict[str, Any]:
        """
        Get config (model, temperature, etc.) from Langfuse prompt.
        
        Returns empty dict if not found or on error.
        """
        with tracer.start_as_current_span("prompt_service.get_config") as span:
            span.set_attribute("prompt.name", name)
            span.set_attribute("prompt.label", label)
            
            try:
                prompt_obj = self.langfuse.get_prompt(name, label=label, type="text")
                config = prompt_obj.config or {}
                
                span.set_attribute("config.found", True)
                span.set_attribute("config.keys", list(config.keys()))
                
                logger.info(
                    "Prompt config loaded",
                    prompt_name=name,
                    label=label,
                    config_keys=list(config.keys())
                )
                
                return config
                
            except Exception as e:
                span.set_attribute("config.found", False)
                span.set_attribute("config.error", str(e))
                
                logger.warning(
                    "Failed to load prompt config from Langfuse",
                    prompt_name=name,
                    label=label,
                    error=str(e)
                )
                
                return {}


