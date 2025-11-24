"""
Authentication Middleware

Validates JWT tokens from Backoffice and extracts user context.
Uses JWKS endpoint for Ed25519 token validation.
"""

from fastapi import HTTPException, Security, status, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from typing import Optional, List
from jose import jwt as jose_jwt, jwk
from jose.utils import base64url_decode
from jose.exceptions import JWTError, ExpiredSignatureError, JWTClaimsError
import jwt as pyjwt  # PyJWT for EdDSA support (better EdDSA handling)
import httpx
import structlog

from ..config import settings

logger = structlog.get_logger()
security = HTTPBearer()


class UserContext(BaseModel):
    """User context extracted from JWT token."""
    
    sub: str = Field(..., description="User ID (subject)")
    tenant_id: Optional[str] = Field(None, description="Tenant identifier")
    scopes: List[str] = Field(default_factory=list, description="User scopes")
    raw_token: str = Field(..., description="Original JWT token")
    role_name: Optional[str] = Field(None, description="User role name")
    client_id: Optional[str] = Field(None, description="Client ID if applicable")


async def fetch_jwks() -> dict:
    """
    Fetch JWKS from Backoffice endpoint.
    
    Returns:
        JWKS dictionary with keys
        
    Raises:
        HTTPException: If JWKS cannot be fetched
    """
    jwks_url = f"{settings.backoffice_url}/.well-known/jwks.json"
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        logger.error("Failed to fetch JWKS", error=str(e), url=jwks_url)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWKS endpoint unavailable",
        )
    except httpx.HTTPStatusError as e:
        logger.error("JWKS endpoint returned error", status=e.response.status_code, url=jwks_url)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWKS endpoint error",
        )


async def validate_token(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> UserContext:
    """
    Validate JWT token and extract user context.

    Args:
        credentials: HTTP authorization credentials

    Returns:
        UserContext with user information

    Raises:
        HTTPException: If token is invalid
    """
    token = credentials.credentials

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # Fetch JWKS
        jwks_data = await fetch_jwks()
        
        # Decode token header to get kid
        unverified_header = jose_jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        
        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing key ID",
            )
        
        # Find matching key in JWKS
        jwk_key = None
        for key in jwks_data.get("keys", []):
            if key.get("kid") == kid:
                jwk_key = key
                break
        
        if not jwk_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Key not found in JWKS",
            )
        
        # Convert JWK to format expected by python-jose for EdDSA
        # python-jose doesn't support EdDSA well, so we use cryptography directly
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.hazmat.primitives import serialization
            import base64
            
            # Extract public key from JWK (EdDSA uses "x" parameter)
            if jwk_key.get("kty") != "OKP" or jwk_key.get("crv") != "Ed25519":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unsupported key type for EdDSA",
                )
            
            # Decode the public key from base64url
            x = jwk_key.get("x")
            if not x:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing public key data in JWK",
                )
            
            # Decode base64url to bytes
            public_key_bytes = base64.urlsafe_b64decode(x + '==')  # Add padding
            
            # Create Ed25519PublicKey from bytes
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            
            # Convert to PEM format for python-jose
            public_key_pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            
        except Exception as e:
            logger.error("Failed to construct EdDSA key from JWK", error=str(e), error_type=type(e).__name__)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Failed to process JWK: {str(e)}",
            )
        
        # Verify token
        # Extract issuer and audience from settings
        # Backoffice uses "backoffice" as issuer and audience
        issuer = "backoffice"
        audience = "backoffice"
        
        # Decode and verify token using PyJWT (supports EdDSA better than python-jose)
        try:
            # Use PyJWT with cryptography Ed25519PublicKey directly
            payload = pyjwt.decode(
                token,
                public_key,  # Use Ed25519PublicKey directly, PyJWT supports it
                algorithms=["EdDSA"],
                issuer=issuer,
                audience=audience,
                options={"verify_signature": True, "verify_exp": True, "verify_aud": True, "verify_iss": True},
            )
        except pyjwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired",
            )
        except pyjwt.InvalidAudienceError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid audience",
            )
        except pyjwt.InvalidIssuerError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid issuer",
            )
        except pyjwt.InvalidTokenError as e:
            logger.warning("JWT validation error", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
            )
        
        # Extract user context
        return UserContext(
            sub=payload.get("sub", ""),
            tenant_id=payload.get("tenant_id") or payload.get("tenant"),
            scopes=payload.get("scopes", []),
            raw_token=token,
            role_name=payload.get("role_name"),
            client_id=payload.get("client_id"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Token validation error", error=str(e), error_type=type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token validation failed",
        )


async def get_current_user(
    user_ctx: UserContext = Depends(validate_token),
) -> UserContext:
    """
    FastAPI dependency to get current user context.
    
    Args:
        user_ctx: User context from token validation
        
    Returns:
        UserContext instance
    """
    return user_ctx


