"""
Cover art downloader for radio stations.
Downloads cover art from external URLs and saves locally for Tor-accessible serving.
"""
import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

try:
    import aiohttp
    from aiohttp_socks import ProxyConnector
except ImportError:
    aiohttp = None
    ProxyConnector = None

from config import (
    COVERS_DIR, TOR_BASE_URL, TOR_SOCKS_PROXY, I2P_HTTP_PROXY
)


# Timeout for downloading cover art
DOWNLOAD_TIMEOUT = 15  # seconds

# Maximum file size (8MB)
MAX_FILE_SIZE = 8 * 1024 * 1024

# Allowed image content types
ALLOWED_CONTENT_TYPES = frozenset([
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
])

# Extension mapping for content types
EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


@dataclass
class DownloadResult:
    """Result of cover art download"""
    success: bool
    message: str
    local_path: Optional[str] = None  # Path relative to static dir
    tor_url: Optional[str] = None  # Full Tor-accessible URL


def _ensure_covers_dir():
    """Ensure the covers directory exists"""
    COVERS_DIR.mkdir(parents=True, exist_ok=True)


def _generate_filename(url: str, content_type: str) -> str:
    """Generate a unique filename based on URL hash"""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    ext = EXTENSION_MAP.get(content_type, ".jpg")
    return f"{url_hash}{ext}"


def _extract_content_type(content_type_header: Optional[str]) -> str:
    """Extract just the MIME type from Content-Type header"""
    if not content_type_header:
        return ""
    return content_type_header.split(";")[0].strip().lower()


def _detect_network(url: str) -> Optional[str]:
    """Detect if URL is Tor, I2P, or clearnet"""
    if ".onion" in url:
        return "tor"
    elif ".b32.i2p" in url or ".i2p" in url:
        return "i2p"
    return None  # Clearnet


async def download_cover_art(url: str) -> DownloadResult:
    """
    Download cover art from a URL and save locally.

    Supports:
    - Clearnet URLs (routed through Tor for network isolation)
    - Tor .onion URLs (via SOCKS proxy)
    - I2P .b32.i2p URLs (via HTTP proxy)

    Returns:
        DownloadResult with local path and Tor-accessible URL on success
    """
    if aiohttp is None:
        return DownloadResult(
            success=False,
            message="aiohttp not installed - cannot download cover art"
        )

    if not url or not url.strip():
        return DownloadResult(
            success=False,
            message="No URL provided"
        )

    url = url.strip()

    # Validate URL format
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return DownloadResult(
                success=False,
                message="Invalid URL scheme - must be http or https"
            )
    except Exception:
        return DownloadResult(
            success=False,
            message="Invalid URL format"
        )

    _ensure_covers_dir()
    network = _detect_network(url)

    try:
        if network == "tor":
            connector = ProxyConnector.from_url(TOR_SOCKS_PROXY)
            async with aiohttp.ClientSession(connector=connector) as session:
                return await _download_and_save(session, url)
        elif network == "i2p":
            async with aiohttp.ClientSession() as session:
                return await _download_and_save(session, url, proxy=I2P_HTTP_PROXY)
        else:
            # Clearnet - route through Tor for network isolation
            connector = ProxyConnector.from_url(TOR_SOCKS_PROXY)
            async with aiohttp.ClientSession(connector=connector) as session:
                return await _download_and_save(session, url)

    except asyncio.TimeoutError:
        return DownloadResult(
            success=False,
            message="Download timeout - URL not reachable"
        )
    except aiohttp.ClientError as e:
        return DownloadResult(
            success=False,
            message=f"Download error: {type(e).__name__}"
        )
    except Exception as e:
        return DownloadResult(
            success=False,
            message=f"Download failed: {type(e).__name__}: {str(e)}"
        )


async def _download_and_save(
    session: "aiohttp.ClientSession",
    url: str,
    proxy: Optional[str] = None
) -> DownloadResult:
    """Download image and save to covers directory"""

    kwargs = {
        "timeout": aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
        "allow_redirects": True,
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
        },
    }
    if proxy:
        kwargs["proxy"] = proxy

    async with session.get(url, **kwargs) as response:
        # Check status
        if response.status != 200:
            return DownloadResult(
                success=False,
                message=f"HTTP error: status {response.status}"
            )

        # Check content type
        content_type = _extract_content_type(
            response.headers.get("Content-Type", "")
        )
        if content_type not in ALLOWED_CONTENT_TYPES:
            return DownloadResult(
                success=False,
                message=f"Invalid content type: {content_type} (not an image)"
            )

        # Check content length if available
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_FILE_SIZE:
            return DownloadResult(
                success=False,
                message=f"File too large: {int(content_length)} bytes (max {MAX_FILE_SIZE})"
            )

        # Read content with size limit
        chunks = []
        total_size = 0
        async for chunk in response.content.iter_chunked(8192):
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                return DownloadResult(
                    success=False,
                    message=f"File too large (max {MAX_FILE_SIZE} bytes)"
                )
            chunks.append(chunk)

        content = b"".join(chunks)

        # Generate filename and save
        filename = _generate_filename(url, content_type)
        file_path = COVERS_DIR / filename

        # Write file
        file_path.write_bytes(content)

        # Generate URLs
        local_path = f"/static/covers/{filename}"
        tor_url = f"{TOR_BASE_URL}/static/covers/{filename}"

        return DownloadResult(
            success=True,
            message="Cover art downloaded successfully",
            local_path=local_path,
            tor_url=tor_url
        )


def get_cover_tor_url(local_path: str) -> str:
    """Convert a local path to a Tor-accessible URL"""
    if local_path.startswith("/static/"):
        return f"{TOR_BASE_URL}{local_path}"
    return f"{TOR_BASE_URL}/static/covers/{local_path}"
