"""
Configuration Management

Centralized configuration using pydantic-settings.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://nodus:nodus_dev_password@postgres:5432/nodus"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Backoffice
    backoffice_url: str = "http://backoffice:5001"
    backoffice_api_key: Optional[str] = None

    # MCP Gateway
    mcp_gateway_url: str = "http://mcp-gateway:7443"

    # Memory Layer - Qdrant (documents/RAG)
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: Optional[str] = None
    
    # Memory Layer - Tricapa Configuration
    adk_memory_backend: str = "database"  # database | inmemory
    adk_memory_preload_limit: int = 3  # Max memories loaded automatically
    
    # OpenMemory (via MCP - long-term episodic/semantic)
    openmemory_enabled: bool = True
    openmemory_url: str = "http://openmemory:8080"
    openmemory_api_key: Optional[str] = None

    # LiteLLM Proxy (unified AI gateway for ALL models)
    litellm_proxy_api_base: str = "http://litellm:4000"
    litellm_proxy_api_key: str = "sk-nodus-master-key"
    
    # Model selection (routed through LiteLLM)
    adk_model: str = "gemini-2.0-flash-exp"
    
    # Legacy keys (kept for backwards compatibility, but routed through LiteLLM)
    adk_project_id: Optional[str] = None
    google_api_key: Optional[str] = None  # Not used - goes via LiteLLM
    
    # OpenAI (for embeddings and ADK via LiteLLM)
    openai_api_key: str = "sk-nodus-master-key"  # LiteLLM master key
    openai_api_base: str = "http://litellm:4000/v1"  # Point to LiteLLM

    # Observability - Langfuse
    langfuse_enabled: bool = True
    langfuse_host: str = "http://langfuse:3000"
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    
    # OpenTelemetry Configuration
    otel_exporter_otlp_endpoint: Optional[str] = None
    otel_service_name: str = "nodus-adk-runtime"
    otel_traces_sampler: str = "parentbased_traceidratio"
    otel_traces_sampler_arg: float = 1.0
    
    # ADK Telemetry
    adk_capture_message_content_in_spans: bool = True

    # Logging
    log_level: str = "INFO"

    # CORS
    cors_origins: str = "http://localhost:5002,http://localhost:5001,http://localhost:3000"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()


