"""
Radio Registry API
FastAPI application with both JSON API and server-rendered HTML
"""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from pathlib import Path
import hashlib
import hmac
import secrets
import time
import pyotp
import qrcode
import qrcode.image.svg
import io
import base64

from fastapi import FastAPI, HTTPException, Query, Request, Form, Cookie, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

import database as db
from models import (
    StationSubmit, StationResponse, StationListResponse,
    StationDetailResponse, SubmitResponse, StatsResponse,
    HealthResponse, ErrorResponse
)
from config import (
    CORS_ORIGINS, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE,
    TEMPLATES_DIR, STATIC_DIR, ADMIN_PASSWORD, ADMIN_SECRET_KEY,
    COVERS_DIR, MIRRORS
)
from stream_validator import validate_stream
from csrf import generate_csrf_token, verify_csrf_token, set_csrf_cookie


# ============== App Setup ==============

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    db.init_db()
    print("Radio Registry API started")
    print(f"Database: {db.get_db_path()}")
    yield
    # Shutdown (nothing to clean up currently)


app = FastAPI(
    title="Radio Registry",
    description="A lightweight radio station directory for Tor and I2P networks",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware - configure allowed origins in config.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Content Security Policy - prevents XSS and other injection attacks
        # Relax CSP for admin pages that need inline JS (covers, 2fa)
        if request.url.path.startswith("/admin/covers") or request.url.path.startswith("/admin/2fa"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https: http:; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            )

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Disable browser features we don't need
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )

        return response


app.add_middleware(SecurityHeadersMiddleware)


# Templates for server-rendered HTML
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Mount static files if directory exists
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============== Helper Functions ==============

def detect_network_from_host(request: Request) -> str:
    """Detect which network the user is accessing from based on Host header"""
    host = request.headers.get("host", "").lower()
    if ".onion" in host:
        return "tor"
    elif ".i2p" in host:
        return "i2p"
    return "clearnet"


def get_mirror_context(request: Request) -> dict:
    """Get mirror links context for templates"""
    current_network = detect_network_from_host(request)
    # Build list of available mirrors (excluding current network)
    mirrors = []
    for network_id, mirror in MIRRORS.items():
        if mirror["url"] and network_id != current_network:
            mirrors.append({
                "name": mirror["name"],
                "url": mirror["url"],
            })
    return {
        "current_network": current_network,
        "mirrors": mirrors,
    }


def detect_network(url: str) -> str:
    """Detect network from URL"""
    if ".onion" in url:
        return "tor"
    elif ".b32.i2p" in url:
        return "i2p"
    raise ValueError("URL must be a .onion or .b32.i2p address")


def validate_url_network_match(url: str, network: Optional[str]) -> str:
    """Validate that URL matches declared network, return detected network"""
    detected = detect_network(url)
    if network and network.lower() != detected:
        raise ValueError(f"URL is a .{detected} address but network was declared as {network}")
    return detected


# ============== JSON API Endpoints ==============

@app.get("/api/stations", response_model=StationListResponse)
@limiter.limit("60/minute")
async def list_stations(
    request: Request,
    network: Optional[str] = Query(None, description="Filter by network: tor or i2p"),
    genre: Optional[str] = Query(None, description="Filter by genre"),
    online_only: bool = Query(False, description="Only show online stations"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0)
):
    """List all approved stations (online first, then offline)"""
    if network and network.lower() not in ("tor", "i2p"):
        raise HTTPException(400, "Network must be 'tor' or 'i2p'")

    stations = db.get_stations(
        network=network,
        genre=genre,
        online_only=online_only,
        limit=limit,
        offset=offset
    )
    
    stats = db.get_stats()
    
    return StationListResponse(
        stations=[StationResponse(**s) for s in stations],
        total=stats["total_stations"],
        online=stats["online_stations"]
    )


@app.get("/api/stations/{station_id}", response_model=StationDetailResponse)
async def get_station(station_id: str):
    """Get a single station by ID"""
    station = db.get_station_by_id(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    return StationDetailResponse(**station)


@app.get("/api/stations/{station_id}/cover")
async def get_station_cover(station_id: str):
    """Get cover art for a station (redirects to the image)"""
    station = db.get_station_by_id(station_id)
    if not station:
        raise HTTPException(404, "Station not found")

    favicon_url = station.get("favicon_url")
    if not favicon_url:
        raise HTTPException(404, "Station has no cover art")

    return RedirectResponse(url=favicon_url, status_code=302)


@app.post("/api/submit", response_model=SubmitResponse)
@limiter.limit("5/minute")
async def submit_station(request: Request, station: StationSubmit, background_tasks: BackgroundTasks):
    """Submit a new station - auto-approved if valid audio stream"""
    # Validate and detect network
    try:
        network = validate_url_network_match(station.stream_url, station.network)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Check for duplicates
    existing = db.get_station_by_url(station.stream_url)
    if existing:
        raise HTTPException(
            400,
            f"Station with this URL already exists (status: {existing['status']})"
        )

    # Validate stream - must be a real audio stream
    validation = await validate_stream(station.stream_url, network)
    if not validation.is_valid:
        raise HTTPException(
            400,
            f"Invalid stream: {validation.reason}"
        )

    # Auto-approve valid streams
    try:
        # Use detected codec if not provided
        codec = station.codec or validation.detected_codec
        bitrate = station.bitrate or validation.icy_bitrate

        station_id = db.create_station(
            name=station.name,
            stream_url=station.stream_url,
            network=network,
            homepage=station.homepage,
            genre=station.genre,
            codec=codec,
            bitrate=bitrate,
            status="approved"  # Auto-approve valid streams
        )

        # Queue cover art for human approval if provided (not downloaded yet)
        if station.favicon_url:
            from notifications import notify_cover_pending
            db.create_cover_approval(station_id, station.favicon_url)
            background_tasks.add_task(notify_cover_pending, station_id, station.name)

        # Mark as online since we just verified it
        db.update_health_status(station_id, is_online=True)

        return SubmitResponse(
            success=True,
            message="Station verified and added to the directory.",
            station_id=station_id
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to submit station: {str(e)}")


@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """Get directory statistics"""
    return StatsResponse(**db.get_stats())


@app.get("/api/genres")
async def get_genres():
    """Get list of available genres"""
    return {"genres": db.get_genres()}


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """API health check"""
    try:
        # Test database connection
        db.get_stats()
        db_ok = True
    except Exception:
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        timestamp=datetime.utcnow().isoformat() + "Z"
    )


# ============== JSON Download Endpoints for Users ==============

@app.get("/api/download/all")
@limiter.limit("10/minute")
async def download_all_stations(request: Request):
    """Download all stations as JSON (including dead)"""
    stations = db.get_stations_for_download(include_dead=True, online_only=False)
    return JSONResponse(
        content={"stations": stations, "total": len(stations)},
        headers={"Content-Disposition": "attachment; filename=stations_all.json"}
    )


@app.get("/api/download/active")
@limiter.limit("10/minute")
async def download_active_stations(request: Request):
    """Download alive + offline stations as JSON (excludes dead)"""
    stations = db.get_stations_for_download(include_dead=False, online_only=False)
    return JSONResponse(
        content={"stations": stations, "total": len(stations)},
        headers={"Content-Disposition": "attachment; filename=stations_active.json"}
    )


@app.get("/api/download/online")
@limiter.limit("10/minute")
async def download_online_stations(request: Request):
    """Download online stations only as JSON"""
    stations = db.get_stations_for_download(include_dead=False, online_only=True)
    return JSONResponse(
        content={"stations": stations, "total": len(stations)},
        headers={"Content-Disposition": "attachment; filename=stations_online.json"}
    )


# ============== Server-Rendered HTML Pages ==============
# These work without JavaScript for Tor/I2P users

@app.get("/", response_class=HTMLResponse)
async def index_page(
    request: Request,
    network: Optional[str] = None,
    genre: Optional[str] = None,
    q: Optional[str] = None,
    sort: Optional[str] = None,
    page: int = Query(1, ge=1)
):
    """Main page - station list (no JS required)"""
    per_page = 50
    offset = (page - 1) * per_page

    # Validate sort parameter
    valid_sorts = ["newest", "health", "alphabetical"]
    if sort not in valid_sorts:
        sort = "newest"

    stations = db.get_stations(network=network, genre=genre, search=q, sort=sort, limit=per_page, offset=offset)
    total_count = db.count_stations(network=network, genre=genre, search=q)
    total_pages = (total_count + per_page - 1) // per_page  # Ceiling division

    stats = db.get_stats()
    genres = db.get_genres()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "stations": stations,
        "stats": stats,
        "genres": genres,
        "filter_network": network,
        "current_genre": genre,
        "search_query": q,
        "current_sort": sort,
        "current_page": page,
        "total_pages": total_pages,
        **get_mirror_context(request),
    })


@app.get("/station/{station_id}", response_class=HTMLResponse)
async def station_page(request: Request, station_id: str):
    """Station detail page"""
    station = db.get_station_by_id(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    
    return templates.TemplateResponse("station.html", {
        "request": request,
        "station": station,
        **get_mirror_context(request),
    })


@app.get("/submit", response_class=HTMLResponse)
async def submit_page(request: Request):
    """Station submission form"""
    from config import DEFAULT_GENRES, DEFAULT_LANGUAGES

    # Generate CSRF token
    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse("submit.html", {
        "request": request,
        "genres": DEFAULT_GENRES,
        "languages": DEFAULT_LANGUAGES,
        "csrf_token": csrf_token,
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/submit", response_class=HTMLResponse)
@limiter.limit("5/minute")  # Rate limit public submissions
async def submit_form(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    stream_url: str = Form(...),
    homepage: Optional[str] = Form(None),
    favicon_url: Optional[str] = Form(None),
    genre: str = Form(""),
    language: str = Form(""),
    bitrate: Optional[int] = Form(None),
    csrf_token: str = Form(...),
):
    """Handle form submission - auto-approved if valid audio stream"""
    from config import DEFAULT_GENRES, DEFAULT_LANGUAGES

    error = None
    success = False

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        error = "Invalid security token. Please refresh the page and try again."
    else:
        try:
            # Validate URL and detect network
            network = detect_network(stream_url)

            # Check for duplicates
            existing = db.get_station_by_url(stream_url)
            if existing:
                error = f"Station with this URL already exists (status: {existing['status']})"
            else:
                # Validate stream - must be a real audio stream
                validation = await validate_stream(stream_url.strip(), network)
                if not validation.is_valid:
                    error = f"Invalid stream: {validation.reason}"
                else:
                    # Use detected values if not provided
                    final_bitrate = bitrate or validation.icy_bitrate
                    detected_codec = validation.detected_codec

                    # Create and auto-approve station
                    station_id = db.create_station(
                        name=name.strip(),
                        stream_url=stream_url.strip(),
                        network=network,
                        homepage=homepage.strip() if homepage else None,
                        genre=genre,
                        language=language,
                        bitrate=final_bitrate,
                        codec=detected_codec,
                        status="approved"  # Auto-approve valid streams
                    )
                    # Queue cover art for human approval if provided
                    if favicon_url and favicon_url.strip():
                        from notifications import notify_cover_pending
                        db.create_cover_approval(station_id, favicon_url.strip())
                        background_tasks.add_task(notify_cover_pending, station_id, name.strip())
                    # Mark as online since we just verified it
                    db.update_health_status(station_id, is_online=True)
                    success = True
        except ValueError as e:
            error = str(e)
        except Exception as e:
            error = f"Failed to submit: {str(e)}"

    # Generate new CSRF token for the response
    new_csrf = generate_csrf_token()

    response = templates.TemplateResponse("submit.html", {
        "request": request,
        "genres": DEFAULT_GENRES,
        "languages": DEFAULT_LANGUAGES,
        "success": success,
        "error": error,
        "csrf_token": new_csrf,
        # Preserve form values on error
        "form_name": name if error else "",
        "form_url": stream_url if error else "",
        "form_homepage": homepage if error else "",
        "form_favicon_url": favicon_url if error else "",
        "form_genre": genre if error else "",
        "form_language": language if error else "",
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, new_csrf)
    return response


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    """About page"""
    stats = db.get_stats()
    return templates.TemplateResponse("about.html", {
        "request": request,
        "stats": stats,
        **get_mirror_context(request),
    })


# ============== Error Handlers ==============

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Custom 404 handler"""
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=404,
            content={"error": "Not found", "detail": str(exc.detail)}
        )
    return templates.TemplateResponse("404.html", {
        "request": request,
        **get_mirror_context(request),
    }, status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, exc: Exception):
    """Custom 500 handler"""
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"}
        )
    return templates.TemplateResponse("500.html", {
        "request": request,
        **get_mirror_context(request),
    }, status_code=500)


# ============== Admin Panel ==============

# 2FA Configuration - stored in memory (in production, use database)
# Format: {"secret": "...", "enabled": True/False, "backup_codes": [...]}
_admin_2fa_config = {
    "secret": None,
    "enabled": False,
    "backup_codes": [],
}


def create_admin_token() -> str:
    """
    Create a signed admin session token with improved security.
    Format: {timestamp}.{nonce}.{signature}
    - Uses full HMAC-SHA256 signature (not truncated)
    - Includes random nonce for additional entropy
    """
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)  # 128 bits of randomness

    # Create signature over timestamp and nonce
    message = f"{timestamp}.{nonce}"
    signature = hmac.new(
        ADMIN_SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()  # Full 64 character hex signature

    return f"{timestamp}.{nonce}.{signature}"


def verify_admin_token(token: str) -> bool:
    """Verify admin session token with improved security"""
    if not token:
        return False
    try:
        parts = token.split(".")
        if len(parts) != 3:
            # Also accept old format for backward compatibility during transition
            if len(parts) == 2:
                return _verify_legacy_token(token)
            return False

        timestamp, nonce, signature = parts

        # Token expires after 24 hours
        if int(time.time()) - int(timestamp) > 86400:
            return False

        # Verify signature
        message = f"{timestamp}.{nonce}"
        expected_sig = hmac.new(
            ADMIN_SECRET_KEY.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected_sig)
    except (ValueError, TypeError):
        return False


def _verify_legacy_token(token: str) -> bool:
    """Verify old-format tokens during transition period"""
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return False
        timestamp, signature = parts
        if int(time.time()) - int(timestamp) > 86400:
            return False
        expected_sig = hmac.new(
            ADMIN_SECRET_KEY.encode(),
            timestamp.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        return hmac.compare_digest(signature, expected_sig)
    except (ValueError, TypeError):
        return False


# ============== 2FA Functions ==============

def generate_2fa_secret() -> str:
    """Generate a new TOTP secret for 2FA"""
    return pyotp.random_base32()


def get_2fa_qr_code(secret: str, issuer: str = "RadioRegistry") -> str:
    """Generate a QR code for 2FA setup as base64 SVG"""
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name="admin", issuer_name=issuer)

    # Generate QR code as SVG
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)

    # Create SVG image
    factory = qrcode.image.svg.SvgPathImage
    img = qr.make_image(image_factory=factory)

    # Convert to base64
    buffer = io.BytesIO()
    img.save(buffer)
    svg_data = buffer.getvalue()

    return base64.b64encode(svg_data).decode('utf-8')


def verify_2fa_code(secret: str, code: str) -> bool:
    """Verify a TOTP code"""
    if not secret or not code:
        return False
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)  # Allow 1 step tolerance
    except Exception:
        return False


def generate_backup_codes(count: int = 8) -> list:
    """Generate backup codes for 2FA recovery"""
    return [secrets.token_hex(4).upper() for _ in range(count)]


def verify_backup_code(code: str) -> bool:
    """Verify and consume a backup code"""
    code = code.upper().replace("-", "").replace(" ", "")
    if code in _admin_2fa_config["backup_codes"]:
        _admin_2fa_config["backup_codes"].remove(code)
        return True
    return False


def is_2fa_enabled() -> bool:
    """Check if 2FA is enabled"""
    return _admin_2fa_config["enabled"] and _admin_2fa_config["secret"]


def enable_2fa(secret: str) -> list:
    """Enable 2FA with the given secret, returns backup codes"""
    backup_codes = generate_backup_codes()
    _admin_2fa_config["secret"] = secret
    _admin_2fa_config["enabled"] = True
    _admin_2fa_config["backup_codes"] = backup_codes
    return backup_codes


def disable_2fa() -> None:
    """Disable 2FA"""
    _admin_2fa_config["secret"] = None
    _admin_2fa_config["enabled"] = False
    _admin_2fa_config["backup_codes"] = []


def get_2fa_secret() -> Optional[str]:
    """Get current 2FA secret if enabled"""
    return _admin_2fa_config["secret"] if _admin_2fa_config["enabled"] else None


@app.get("/admin", response_class=HTMLResponse)
async def admin_login_page(request: Request, admin_token: Optional[str] = Cookie(None)):
    """Admin login page"""
    if verify_admin_token(admin_token):
        return RedirectResponse("/admin/dashboard", status_code=302)

    # Generate CSRF token
    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse("admin_login.html", {
        "request": request,
        "error": None,
        "csrf_token": csrf_token,
        "needs_2fa": False,
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/admin", response_class=HTMLResponse)
@limiter.limit("5/minute")  # Rate limit: 5 attempts per minute
async def admin_login(
    request: Request,
    password: str = Form(...),
    totp_code: Optional[str] = Form(None),
    backup_code: Optional[str] = Form(None),
    csrf_token: str = Form(...),
):
    """Handle admin login with rate limiting and optional 2FA"""
    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        new_csrf = generate_csrf_token()
        response = templates.TemplateResponse("admin_login.html", {
            "request": request,
            "error": "Invalid security token. Please try again.",
            "csrf_token": new_csrf,
            "needs_2fa": False,
            **get_mirror_context(request),
        })
        set_csrf_cookie(response, new_csrf)
        return response

    # Verify password
    if password != ADMIN_PASSWORD:
        new_csrf = generate_csrf_token()
        response = templates.TemplateResponse("admin_login.html", {
            "request": request,
            "error": "Invalid password",
            "csrf_token": new_csrf,
            "needs_2fa": False,
            **get_mirror_context(request),
        })
        set_csrf_cookie(response, new_csrf)
        return response

    # Check if 2FA is required
    if is_2fa_enabled():
        # Try backup code first
        if backup_code and verify_backup_code(backup_code):
            pass  # Backup code valid, proceed
        elif totp_code:
            if not verify_2fa_code(get_2fa_secret(), totp_code):
                new_csrf = generate_csrf_token()
                response = templates.TemplateResponse("admin_login.html", {
                    "request": request,
                    "error": "Invalid 2FA code",
                    "csrf_token": new_csrf,
                    "needs_2fa": True,
                    "password_verified": True,
                    **get_mirror_context(request),
                })
                set_csrf_cookie(response, new_csrf)
                return response
        else:
            # Password correct but 2FA code needed
            new_csrf = generate_csrf_token()
            response = templates.TemplateResponse("admin_login.html", {
                "request": request,
                "error": None,
                "csrf_token": new_csrf,
                "needs_2fa": True,
                "password_verified": True,
                **get_mirror_context(request),
            })
            set_csrf_cookie(response, new_csrf)
            return response

    # Login successful
    response = RedirectResponse("/admin/dashboard", status_code=302)
    response.set_cookie(
        key="admin_token",
        value=create_admin_token(),
        httponly=True,
        max_age=86400,  # 24 hours
        samesite="strict"
    )
    return response


@app.get("/admin/logout")
async def admin_logout():
    """Logout from admin panel"""
    response = RedirectResponse("/admin", status_code=302)
    response.delete_cookie("admin_token")
    return response


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin_token: Optional[str] = Cookie(None),
    message: Optional[str] = None,
    error: Optional[str] = None
):
    """Admin dashboard"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Get all stations (not just approved)
    stations = db.get_stations(status="approved", limit=500)
    stats = db.get_stats()
    pending_covers_count = db.count_pending_covers()

    # Generate CSRF token
    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse("admin.html", {
        "request": request,
        "stations": stations,
        "stats": stats,
        "pending_covers_count": pending_covers_count,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        "check_results": {},
        "csrf_token": csrf_token,
        "is_2fa_enabled": is_2fa_enabled(),
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/admin/delete/{station_id}")
async def admin_delete_station(
    request: Request,
    station_id: str,
    admin_token: Optional[str] = Cookie(None),
    csrf_token: str = Form(...),
):
    """Delete a station"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/dashboard?error=Invalid+security+token", status_code=302)

    station = db.get_station_by_id(station_id)
    if not station:
        return RedirectResponse("/admin/dashboard?error=Station+not+found", status_code=302)

    # Delete cover art file if exists
    if station.get("faviconUrl"):
        try:
            # Extract filename from URL
            favicon_url = station["faviconUrl"]
            if "/static/covers/" in favicon_url:
                filename = favicon_url.split("/static/covers/")[-1]
                cover_path = COVERS_DIR / filename
                if cover_path.exists():
                    cover_path.unlink()
        except Exception:
            pass

    db.delete_station(station_id)
    return RedirectResponse(f"/admin/dashboard?message=Station+deleted", status_code=302)


@app.post("/admin/delete-cover/{station_id}")
async def admin_delete_cover(
    request: Request,
    station_id: str,
    admin_token: Optional[str] = Cookie(None),
    csrf_token: str = Form(...),
):
    """Delete cover art for a station"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/dashboard?error=Invalid+security+token", status_code=302)

    station = db.get_station_by_id(station_id)
    if not station:
        return RedirectResponse("/admin/dashboard?error=Station+not+found", status_code=302)

    if station.get("faviconUrl"):
        try:
            favicon_url = station["faviconUrl"]
            if "/static/covers/" in favicon_url:
                filename = favicon_url.split("/static/covers/")[-1]
                cover_path = COVERS_DIR / filename
                if cover_path.exists():
                    cover_path.unlink()
        except Exception:
            pass

    db.update_station(station_id, favicon_url=None)
    return RedirectResponse(f"/admin/dashboard?message=Cover+art+deleted", status_code=302)


@app.get("/admin/edit/{station_id}", response_class=HTMLResponse)
async def admin_edit_page(
    request: Request,
    station_id: str,
    admin_token: Optional[str] = Cookie(None)
):
    """Admin station edit page"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    station = db.get_station_by_id(station_id)
    if not station:
        return RedirectResponse("/admin/dashboard?error=Station+not+found", status_code=302)

    # Check for pending cover
    pending_cover = db.get_pending_cover_for_station(station_id)
    if pending_cover and pending_cover.get("submitted_at"):
        pending_cover["submitted_at_formatted"] = datetime.utcfromtimestamp(
            pending_cover["submitted_at"]
        ).strftime("%Y-%m-%d %H:%M UTC")

    # Generate CSRF token
    csrf_token = generate_csrf_token()

    from config import DEFAULT_GENRES, DEFAULT_LANGUAGES
    response = templates.TemplateResponse("admin_edit.html", {
        "request": request,
        "station": station,
        "pending_cover": pending_cover,
        "genres": DEFAULT_GENRES,
        "languages": DEFAULT_LANGUAGES,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        "csrf_token": csrf_token,
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/admin/edit/{station_id}", response_class=HTMLResponse)
async def admin_edit_station(
    request: Request,
    station_id: str,
    background_tasks: BackgroundTasks,
    admin_token: Optional[str] = Cookie(None),
    name: str = Form(...),
    genre: str = Form("Other"),
    language: str = Form("Unknown"),
    codec: Optional[str] = Form(None),
    bitrate: Optional[int] = Form(None),
    homepage: Optional[str] = Form(None),
    favicon_url: Optional[str] = Form(None),
    csrf_token: str = Form(...),
):
    """Handle admin station edit"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse(f"/admin/edit/{station_id}?error=Invalid+security+token", status_code=302)

    station = db.get_station_by_id(station_id)
    if not station:
        return RedirectResponse("/admin/dashboard?error=Station+not+found", status_code=302)

    try:
        # Update basic fields
        db.update_station(
            station_id,
            name=name.strip(),
            genre=genre.strip() if genre else "Other",
            language=language.strip() if language else "Unknown",
            codec=codec.strip().upper() if codec and codec.strip() else None,
            bitrate=bitrate if bitrate else None,
            homepage=homepage.strip() if homepage and homepage.strip() else None,
        )

        # Handle cover art URL change
        new_favicon_url = favicon_url.strip() if favicon_url and favicon_url.strip() else None
        old_favicon_url = station.get("faviconUrl")

        cover_message = ""
        if new_favicon_url and new_favicon_url != old_favicon_url:
            # Delete old cover file if exists
            if old_favicon_url and "/static/covers/" in old_favicon_url:
                try:
                    filename = old_favicon_url.split("/static/covers/")[-1]
                    cover_path = COVERS_DIR / filename
                    if cover_path.exists():
                        cover_path.unlink()
                except Exception:
                    pass
            # Clear the current favicon (new one is pending)
            db.update_station(station_id, favicon_url=None)
            # Queue for approval with external URL
            from notifications import notify_cover_pending
            db.create_cover_approval(station_id, new_favicon_url)
            background_tasks.add_task(notify_cover_pending, station_id, name.strip())
            cover_message = "+Cover+queued+for+approval."
        elif not new_favicon_url and old_favicon_url:
            # Clear cover art
            if "/static/covers/" in old_favicon_url:
                try:
                    filename = old_favicon_url.split("/static/covers/")[-1]
                    cover_path = COVERS_DIR / filename
                    if cover_path.exists():
                        cover_path.unlink()
                except Exception:
                    pass
            db.update_station(station_id, favicon_url=None)

        return RedirectResponse(
            f"/admin/edit/{station_id}?message=Station+updated+successfully{cover_message}",
            status_code=302
        )
    except Exception as e:
        return RedirectResponse(
            f"/admin/edit/{station_id}?error=Failed+to+update:+{type(e).__name__}",
            status_code=302
        )


@app.post("/admin/check/{station_id}")
async def admin_check_station(
    request: Request,
    station_id: str,
    admin_token: Optional[str] = Cookie(None),
    csrf_token: str = Form(...),
):
    """Check a single station"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/dashboard?error=Invalid+security+token", status_code=302)

    station = db.get_station_by_id(station_id)
    if not station:
        return RedirectResponse("/admin/dashboard?error=Station+not+found", status_code=302)

    # Perform health check
    try:
        validation = await validate_stream(station["streamUrl"], station["network"])
        is_online = validation.is_valid
        db.update_health_status(station_id, is_online=is_online)
        status_msg = "ONLINE" if is_online else "OFFLINE"
        return RedirectResponse(f"/admin/dashboard?message={status_msg}", status_code=302)
    except Exception as e:
        db.update_health_status(station_id, is_online=False)
        return RedirectResponse(f"/admin/dashboard?error=Check+failed:+{type(e).__name__}", status_code=302)


@app.post("/admin/check-all")
async def admin_check_all_stations(
    request: Request,
    admin_token: Optional[str] = Cookie(None),
    csrf_token: str = Form(...),
):
    """Check all stations"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/dashboard?error=Invalid+security+token", status_code=302)

    stations = db.get_stations_for_health_check()
    online_count = 0
    offline_count = 0

    for station in stations:
        try:
            validation = await validate_stream(station["stream_url"], station["network"])
            is_online = validation.is_valid
            db.update_health_status(station["id"], is_online=is_online)
            if is_online:
                online_count += 1
            else:
                offline_count += 1
        except Exception:
            db.update_health_status(station["id"], is_online=False)
            offline_count += 1

    db.set_last_health_check_time()
    return RedirectResponse(
        f"/admin/dashboard?message=Checked+{len(stations)}+stations:+{online_count}+online,+{offline_count}+offline",
        status_code=302
    )


# ============== 2FA Management Routes ==============

@app.get("/admin/2fa", response_class=HTMLResponse)
async def admin_2fa_page(
    request: Request,
    admin_token: Optional[str] = Cookie(None)
):
    """2FA settings page"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    csrf_token = generate_csrf_token()

    # Generate a new secret for setup if not enabled
    setup_secret = None
    qr_code = None
    if not is_2fa_enabled():
        setup_secret = generate_2fa_secret()
        qr_code = get_2fa_qr_code(setup_secret)

    response = templates.TemplateResponse("admin_2fa.html", {
        "request": request,
        "is_2fa_enabled": is_2fa_enabled(),
        "setup_secret": setup_secret,
        "qr_code": qr_code,
        "csrf_token": csrf_token,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/admin/2fa/enable", response_class=HTMLResponse)
async def admin_enable_2fa(
    request: Request,
    admin_token: Optional[str] = Cookie(None),
    secret: str = Form(...),
    totp_code: str = Form(...),
    csrf_token: str = Form(...),
):
    """Enable 2FA"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/2fa?error=Invalid+security+token", status_code=302)

    # Verify the TOTP code before enabling
    if not verify_2fa_code(secret, totp_code):
        return RedirectResponse("/admin/2fa?error=Invalid+verification+code.+Please+try+again.", status_code=302)

    # Enable 2FA
    backup_codes = enable_2fa(secret)

    # Generate new CSRF token for the backup codes page
    new_csrf = generate_csrf_token()

    response = templates.TemplateResponse("admin_2fa_backup.html", {
        "request": request,
        "backup_codes": backup_codes,
        "csrf_token": new_csrf,
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, new_csrf)
    return response


@app.post("/admin/2fa/disable", response_class=HTMLResponse)
async def admin_disable_2fa(
    request: Request,
    admin_token: Optional[str] = Cookie(None),
    totp_code: str = Form(None),
    backup_code: str = Form(None),
    csrf_token: str = Form(...),
):
    """Disable 2FA"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF token
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/2fa?error=Invalid+security+token", status_code=302)

    if not is_2fa_enabled():
        return RedirectResponse("/admin/2fa?error=2FA+is+not+enabled", status_code=302)

    # Verify with either TOTP code or backup code
    verified = False
    if totp_code and verify_2fa_code(get_2fa_secret(), totp_code):
        verified = True
    elif backup_code and verify_backup_code(backup_code):
        verified = True

    if not verified:
        return RedirectResponse("/admin/2fa?error=Invalid+verification+code", status_code=302)

    disable_2fa()
    return RedirectResponse("/admin/2fa?message=2FA+has+been+disabled", status_code=302)


# ============== Cover Approval Routes ==============

@app.get("/admin/covers", response_class=HTMLResponse)
async def admin_covers_page(
    request: Request,
    admin_token: Optional[str] = Cookie(None)
):
    """Cover approval queue page"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    csrf_token = generate_csrf_token()
    pending_covers = db.get_pending_cover_approvals()

    # Format timestamps for display
    for cover in pending_covers:
        if cover.get("submitted_at"):
            cover["submitted_at_formatted"] = datetime.utcfromtimestamp(
                cover["submitted_at"]
            ).strftime("%Y-%m-%d %H:%M UTC")

    response = templates.TemplateResponse("admin_covers.html", {
        "request": request,
        "pending_covers": pending_covers,
        "csrf_token": csrf_token,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        **get_mirror_context(request),
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/admin/covers/approve/{approval_id}")
async def admin_approve_cover(
    request: Request,
    approval_id: str,
    admin_token: Optional[str] = Cookie(None),
    csrf_token: str = Form(...),
):
    """Approve a cover - set the external URL as the station's favicon (no downloading)"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/covers?error=Invalid+security+token", status_code=302)

    approval = db.get_cover_approval_by_id(approval_id)
    if not approval:
        return RedirectResponse("/admin/covers?error=Approval+not+found", status_code=302)

    cover_url = approval.get("cover_url")
    if not cover_url:
        return RedirectResponse("/admin/covers?error=No+cover+URL+found", status_code=302)

    # Set the external URL directly as the station's favicon (no downloading)
    db.update_station(approval["station_id"], favicon_url=cover_url)

    # Mark approval as approved
    db.approve_cover(approval_id)

    station_name = approval.get("station_name", "Unknown")
    return RedirectResponse(
        f"/admin/covers?message=Cover+approved+for+{station_name}",
        status_code=302
    )


@app.post("/admin/covers/reject/{approval_id}")
async def admin_reject_cover(
    request: Request,
    approval_id: str,
    admin_token: Optional[str] = Cookie(None),
    csrf_token: str = Form(...),
):
    """Reject a cover"""
    if not verify_admin_token(admin_token):
        return RedirectResponse("/admin", status_code=302)

    # Verify CSRF
    cookie_csrf = request.cookies.get('csrf_token')
    if not cookie_csrf or not verify_csrf_token(cookie_csrf) or not hmac.compare_digest(cookie_csrf, csrf_token):
        return RedirectResponse("/admin/covers?error=Invalid+security+token", status_code=302)

    approval = db.get_cover_approval_by_id(approval_id)
    if not approval:
        return RedirectResponse("/admin/covers?error=Approval+not+found", status_code=302)

    # Mark approval as rejected
    db.reject_cover(approval_id)

    station_name = approval.get("station_name", "Unknown")
    return RedirectResponse(
        f"/admin/covers?message=Cover+rejected+for+{station_name}",
        status_code=302
    )


if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT
    uvicorn.run(app, host=HOST, port=PORT)
