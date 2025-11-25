"""
ADK API Server Bootstrap

Entry point for the Nodus ADK runtime server.
Initializes FastAPI app, configures routes, and starts the ADK server.
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
import structlog

from .config import settings
from .api import assistant
from .middleware.auth import get_current_user, UserContext
from .observability import setup_telemetry

logger = structlog.get_logger()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # Setup observability BEFORE creating FastAPI app
    # This ensures all operations are traced from the start
    telemetry_enabled = setup_telemetry()
    if telemetry_enabled:
        logger.info("🔍 OpenTelemetry + Langfuse observability enabled")
    else:
        logger.warning("⚠️  Observability not configured - traces will not be collected")
    
    app = FastAPI(
        title="Nodus ADK Runtime",
        description="ADK-based assistant runtime for Nodus OS",
        version="0.1.0",
    )

    # CORS middleware - specific origins for Llibreta and Backoffice
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    app.include_router(assistant.router)
    
    # HITL router
    from .api import hitl
    app.include_router(hitl.router)

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "service": "nodus-adk-runtime"}

    @app.get("/")
    async def root():
        """Root endpoint."""
        return {
            "service": "nodus-adk-runtime",
            "version": "0.1.0",
            "docs": "/docs",
        }

    @app.get("/v1/debug/me", response_model=dict)
    async def debug_me(user_ctx: UserContext = Depends(get_current_user)):
        """Debug endpoint to validate token and return user context."""
        return {
            "sub": user_ctx.sub,
            "tenant_id": user_ctx.tenant_id,
            "scopes": user_ctx.scopes,
            "role_name": user_ctx.role_name,
            "client_id": user_ctx.client_id,
        }

    logger.info("FastAPI app created", cors_origins=settings.cors_origins_list)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "nodus_adk_runtime.server:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )


