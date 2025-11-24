# Nodus ADK Runtime

Integration layer for Google ADK with Nodus OS control plane.

## Overview

This is the runtime and server component that bridges Google's Agent Development Kit (ADK) with Nodus OS infrastructure:

- **Backoffice**: Authentication, tenants, secrets
- **MCP Gateway**: Tool execution and governance
- **Memory Layer**: Postgres + Qdrant for user memory
- **Llibreta**: User interface integration

## Architecture

```
┌─────────────┐
│   Llibreta  │  (UI)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ ADK Runtime │  ◄── This repo
│   (FastAPI) │
└──────┬──────┘
       │
       ├─────► Google ADK (agents, A2A)
       │
       ├─────► MCP Gateway (tools)
       │
       ├─────► Memory Layer (Qdrant)
       │
       └─────► Backoffice (auth, config)
```

## Components

### Adapters
- `mcp_adapter.py`: Integrates with MCP Gateway for tool execution
- `memory_adapter.py`: Integrates with Memory Layer (Qdrant)

### Middleware
- `auth.py`: JWT validation with Backoffice
- `logging.py`: Structured logging configuration

### Server
- `server.py`: FastAPI application and API routes
- `config.py`: Centralized configuration management

## Dependencies

- **Google ADK**: Via our fork at `nodus-factory/adk-python@nodus-main`
- **FastAPI**: Web framework
- **Structlog**: Structured logging
- **HTTPx**: Async HTTP client
- **Pydantic**: Settings management

## Development

### Local Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -e ".[dev]"

# Run server
python -m nodus_adk_runtime.server
```

### With Docker

```bash
# Build development image
docker build -f Dockerfile.dev -t nodus-adk-runtime:dev .

# Run container
docker run -p 8080:8080 \
  -v $(pwd)/src:/app/src \
  nodus-adk-runtime:dev
```

### With DEVSTACK

See `nodus-adk-infra` repository for complete local development stack.

## Configuration

Configuration via environment variables (see `.env.example`):

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `BACKOFFICE_URL`: Backoffice service URL
- `MCP_GATEWAY_URL`: MCP Gateway URL
- `QDRANT_URL`: Qdrant vector DB URL
- `ADK_MODEL`: Google ADK model to use
- `LOG_LEVEL`: Logging level

## API Endpoints

- `GET /health`: Health check
- `GET /`: Service information
- `GET /docs`: OpenAPI documentation

## Deployment

### Production

```bash
# Build production image
docker build -t nodus-adk-runtime:latest .

# Run with environment
docker run -p 8080:8080 \
  --env-file .env \
  nodus-adk-runtime:latest
```

### Staging

Deployed via GitHub Actions to Hetzner infrastructure.

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=nodus_adk_runtime
```

## License

Copyright © 2024 Nodus Factory

## Links

- [Nodus ADK Agents](../nodus-adk-agents)
- [Nodus ADK Infra](../nodus-adk-infra)
- [ADK Python Fork](../adk-python)


