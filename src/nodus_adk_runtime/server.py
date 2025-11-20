"""
ADK API Server Bootstrap

Entry point for the Nodus ADK runtime server.
Initializes FastAPI app, configures routes, and starts the ADK server.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

logger = structlog.get_logger()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Nodus ADK Runtime",
        description="ADK-based assistant runtime for Nodus OS",
        version="0.1.0",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Configure from settings
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    logger.info("FastAPI app created")
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

