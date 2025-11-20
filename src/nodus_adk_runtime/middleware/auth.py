"""
Authentication Middleware

Validates JWT tokens from Backoffice and extracts user context.
"""

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import structlog

logger = structlog.get_logger()
security = HTTPBearer()


async def validate_token(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    """
    Validate JWT token and extract user context.

    Args:
        credentials: HTTP authorization credentials

    Returns:
        User context dict with tenant_id, user_id, scopes, etc.

    Raises:
        HTTPException: If token is invalid
    """
    token = credentials.credentials

    # TODO: Implement actual JWT validation with Backoffice
    logger.info("Validating token", token_prefix=token[:10])

    # Placeholder validation
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Mock user context
    return {
        "tenant_id": "tenant_placeholder",
        "user_id": "user_placeholder",
        "scopes": ["assistant:read", "assistant:write"],
    }

