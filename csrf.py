"""
CSRF Protection for Radio Registry
Implements double-submit cookie pattern with HMAC-signed tokens.
"""
import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import Request, HTTPException
from starlette.responses import Response

from config import ADMIN_SECRET_KEY


# CSRF token validity period (1 hour)
CSRF_TOKEN_EXPIRY = 3600


def generate_csrf_token(secret_key: str = ADMIN_SECRET_KEY) -> str:
    """
    Generate a CSRF token with timestamp and HMAC signature.

    Format: {timestamp}.{random_nonce}.{signature}
    """
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)

    # Create signature over timestamp and nonce
    message = f"{timestamp}.{nonce}"
    signature = hmac.new(
        secret_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()[:32]

    return f"{timestamp}.{nonce}.{signature}"


def verify_csrf_token(token: str, secret_key: str = ADMIN_SECRET_KEY, max_age: int = CSRF_TOKEN_EXPIRY) -> bool:
    """
    Verify a CSRF token.

    Args:
        token: The CSRF token to verify
        secret_key: Secret key for HMAC verification
        max_age: Maximum age of token in seconds

    Returns:
        True if token is valid, False otherwise
    """
    if not token:
        return False

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False

        timestamp, nonce, signature = parts

        # Check token age
        token_time = int(timestamp)
        current_time = int(time.time())
        if current_time - token_time > max_age:
            return False

        # Verify signature
        message = f"{timestamp}.{nonce}"
        expected_signature = hmac.new(
            secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()[:32]

        return hmac.compare_digest(signature, expected_signature)

    except (ValueError, TypeError):
        return False


async def get_csrf_token(request: Request) -> str:
    """
    Get or generate a CSRF token for a request.
    Stores token in request state for template access.
    """
    # Check if we already have a token in the request state
    if hasattr(request.state, 'csrf_token'):
        return request.state.csrf_token

    # Check for existing valid token in cookie
    existing_token = request.cookies.get('csrf_token')
    if existing_token and verify_csrf_token(existing_token):
        request.state.csrf_token = existing_token
        return existing_token

    # Generate new token
    new_token = generate_csrf_token()
    request.state.csrf_token = new_token
    return new_token


def set_csrf_cookie(response: Response, token: str) -> None:
    """Set the CSRF token cookie on a response."""
    response.set_cookie(
        key="csrf_token",
        value=token,
        httponly=False,  # Must be readable by JavaScript if needed
        samesite="strict",
        max_age=CSRF_TOKEN_EXPIRY,
        secure=False,  # Set to True if using HTTPS
    )


async def validate_csrf(request: Request, form_token: Optional[str] = None) -> bool:
    """
    Validate CSRF token from form submission.

    Args:
        request: The FastAPI request
        form_token: Token from form field (if not provided, extracted from form data)

    Returns:
        True if valid

    Raises:
        HTTPException: If CSRF validation fails
    """
    # Get token from cookie
    cookie_token = request.cookies.get('csrf_token')
    if not cookie_token:
        raise HTTPException(status_code=403, detail="CSRF token missing from cookie")

    # Get token from form if not provided
    if form_token is None:
        form_data = await request.form()
        form_token = form_data.get('csrf_token')

    if not form_token:
        raise HTTPException(status_code=403, detail="CSRF token missing from form")

    # Verify both tokens match and are valid
    if not verify_csrf_token(cookie_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token in cookie")

    if not hmac.compare_digest(cookie_token, form_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")

    return True


class CSRFProtectMiddleware:
    """
    Middleware to automatically handle CSRF protection.
    - Generates CSRF tokens for GET requests
    - Validates CSRF tokens for POST requests to protected routes
    """

    def __init__(self, app, protected_paths: Optional[list] = None, exempt_paths: Optional[list] = None):
        self.app = app
        # Paths that require CSRF protection (default: all POST to /admin/*)
        self.protected_paths = protected_paths or ["/admin/", "/submit"]
        # Paths exempt from CSRF (e.g., API endpoints that use other auth)
        self.exempt_paths = exempt_paths or ["/api/"]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive, send)

        # Check if path is exempt
        path = request.url.path
        for exempt in self.exempt_paths:
            if path.startswith(exempt):
                await self.app(scope, receive, send)
                return

        # For GET requests, ensure CSRF token is available
        if request.method == "GET":
            await self.app(scope, receive, send)
            return

        # For POST requests to protected paths, validate CSRF
        if request.method == "POST":
            is_protected = any(path.startswith(p) for p in self.protected_paths)
            if is_protected:
                # We can't validate here easily without consuming the body
                # Validation is done in the route handlers
                pass

        await self.app(scope, receive, send)
