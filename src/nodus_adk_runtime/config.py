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

    # Memory Layer
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: Optional[str] = None

    # Google ADK
    adk_model: str = "gemini-2.0-flash-exp"
    adk_project_id: Optional[str] = None
    google_api_key: Optional[str] = None  # Google AI Studio API key
    
    # OpenAI (for embeddings)
    openai_api_key: Optional[str] = None

    # Observability
    log_level: str = "INFO"

    # CORS
    cors_origins: str = "http://localhost:5002,http://localhost:5001"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()


