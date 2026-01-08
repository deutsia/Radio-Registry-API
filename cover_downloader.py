"""
Cover art downloader for radio stations.
Downloads cover art from external URLs and saves locally for Tor-accessible serving.
SVG files are sanitized and converted to PNG before storing.
"""
import asyncio
import hashlib
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

from config import COVERS_DIR, TOR_BASE_URL, TOR_SOCKS_PROXY, I2P_HTTP_PROXY
from svg_sanitizer import sanitize_svg


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
    "image/svg+xml",  # Accepted but converted to PNG
])

# Extension mapping for content types
EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    # Note: SVG is not here - it gets converted to PNG
}


@dataclass
class DownloadResult:
    """Result of cover art download"""
    success: bool
    message: str
    local_path: Optional[str] = None  # Path relative to static dir (e.g. /static/covers/abc.jpg)
    local_url: Optional[str] = None   # Full Tor-accessible URL
    error: Optional[str] = None       # Error message if failed


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


def _convert_svg_to_png(svg_content: bytes) -> Optional[bytes]:
    """
    Convert sanitized SVG content to PNG.

    Args:
        svg_content: Sanitized SVG bytes

    Returns:
        PNG bytes, or None if conversion fails
    """
    try:
        import cairosvg

        # Convert SVG to PNG at 512x512 - good size for cover art
        png_bytes = cairosvg.svg2png(
            bytestring=svg_content,
            output_width=512,
            output_height=512
        )
        return png_bytes
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to convert SVG to PNG: {e}")
        return None


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
            message="aiohttp not installed - cannot download cover art",
            error="aiohttp not installed"
        )

    if not url or not url.strip():
        return DownloadResult(
            success=False,
            message="No URL provided",
            error="No URL provided"
        )

    url = url.strip()

    # Validate URL format
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return DownloadResult(
                success=False,
                message="Invalid URL scheme - must be http or https",
                error="Invalid URL scheme"
            )
    except Exception:
        return DownloadResult(
            success=False,
            message="Invalid URL format",
            error="Invalid URL format"
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
            message="Download timeout - URL not reachable",
            error="Timeout"
        )
    except aiohttp.ClientError as e:
        return DownloadResult(
            success=False,
            message=f"Download error: {type(e).__name__}",
            error=str(e)
        )
    except Exception as e:
        return DownloadResult(
            success=False,
            message=f"Download failed: {type(e).__name__}: {str(e)}",
            error=str(e)
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
                message=f"HTTP error: status {response.status}",
                error=f"HTTP {response.status}"
            )

        # Check content type
        content_type = _extract_content_type(
            response.headers.get("Content-Type", "")
        )
        if content_type not in ALLOWED_CONTENT_TYPES:
            return DownloadResult(
                success=False,
                message=f"Invalid content type: {content_type} (not an image)",
                error="Invalid content type"
            )

        # Check content length if available
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_FILE_SIZE:
            return DownloadResult(
                success=False,
                message=f"File too large: {int(content_length)} bytes (max {MAX_FILE_SIZE})",
                error="File too large"
            )

        # Read content with size limit
        chunks = []
        total_size = 0
        async for chunk in response.content.iter_chunked(8192):
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                return DownloadResult(
                    success=False,
                    message=f"File too large (max {MAX_FILE_SIZE} bytes)",
                    error="File too large"
                )
            chunks.append(chunk)

        content = b"".join(chunks)

        # For SVG: sanitize, then convert to PNG
        if content_type == "image/svg+xml":
            sanitized = sanitize_svg(content)
            if sanitized is None:
                return DownloadResult(
                    success=False,
                    message="Invalid SVG: file is malformed and cannot be processed",
                    error="Invalid SVG"
                )
            # Convert sanitized SVG to PNG
            png_content = _convert_svg_to_png(sanitized)
            if png_content is None:
                return DownloadResult(
                    success=False,
                    message="Failed to convert SVG to PNG",
                    error="SVG conversion failed"
                )
            content = png_content
            content_type = "image/png"  # Now it's a PNG

        # Generate filename and save
        filename = _generate_filename(url, content_type)
        file_path = COVERS_DIR / filename

        # Write file
        file_path.write_bytes(content)

        # Generate URLs
        local_path = f"/static/covers/{filename}"
        local_url = f"{TOR_BASE_URL}/static/covers/{filename}"

        return DownloadResult(
            success=True,
            message="Cover art downloaded successfully",
            local_path=local_path,
            local_url=local_url
        )


def get_cover_tor_url(local_path: str) -> str:
    """Convert a local path to a Tor-accessible URL"""
    if local_path.startswith("/static/"):
        return f"{TOR_BASE_URL}{local_path}"
    return f"{TOR_BASE_URL}/static/covers/{local_path}"
