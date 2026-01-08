"""
Stream validator for radio stations.
Validates that a URL points to an actual audio stream before auto-approval.
"""
import asyncio
from dataclasses import dataclass
from typing import Optional

try:
    import aiohttp
    from aiohttp_socks import ProxyConnector
except ImportError:
    aiohttp = None
    ProxyConnector = None

from config import TOR_SOCKS_PROXY, I2P_HTTP_PROXY

# Timeout for stream validation (shorter than health check)
VALIDATION_TIMEOUT = 10  # seconds

# Content-Types that indicate audio streams
AUDIO_CONTENT_TYPES = frozenset([
    # Standard audio types
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/aac",
    "audio/aacp",
    "audio/flac",
    "audio/opus",
    "audio/wav",
    "audio/x-wav",
    "audio/x-ms-wma",
    "audio/vorbis",
    # Playlist types (HLS, etc.)
    "application/x-mpegurl",
    "application/mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
    "application/vnd.apple.mpegurl",
    "application/vnd.apple.mpegurl.audio",
    # DASH
    "application/dash+xml",
    # Generic (needs icy headers)
    "application/ogg",
])

# Content-Types that should always be rejected
DANGEROUS_CONTENT_TYPES = frozenset([
    "text/html",
    "text/plain",
    "application/javascript",
    "application/json",
    "application/xml",
    "application/x-msdownload",
    "application/x-executable",
    "application/zip",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/webm",
    "video/x-msvideo",
])

# ICY headers that indicate a radio stream
ICY_HEADERS = [
    "icy-name",
    "icy-genre",
    "icy-br",
    "icy-sr",
    "icy-description",
    "icy-url",
    "icy-pub",
    "icy-metaint",
]


@dataclass
class StreamValidationResult:
    """Result of stream validation"""
    is_valid: bool
    reason: str
    content_type: Optional[str] = None
    has_icy_headers: bool = False
    detected_codec: Optional[str] = None
    icy_name: Optional[str] = None
    icy_bitrate: Optional[int] = None


def _extract_content_type(content_type_header: Optional[str]) -> str:
    """Extract just the MIME type from Content-Type header (strip charset etc.)"""
    if not content_type_header:
        return ""
    return content_type_header.split(";")[0].strip().lower()


def _has_icy_headers(headers: dict) -> bool:
    """Check if response has any ICY headers"""
    headers_lower = {k.lower(): v for k, v in headers.items()}
    return any(h in headers_lower for h in ICY_HEADERS)


def _extract_icy_info(headers: dict) -> tuple[Optional[str], Optional[int]]:
    """Extract ICY name and bitrate from headers"""
    headers_lower = {k.lower(): v for k, v in headers.items()}
    icy_name = headers_lower.get("icy-name")
    icy_br = headers_lower.get("icy-br")
    bitrate = None
    if icy_br:
        try:
            bitrate = int(icy_br)
        except ValueError:
            pass
    return icy_name, bitrate


def _detect_codec_from_content_type(content_type: str) -> Optional[str]:
    """Detect codec from content-type"""
    ct = content_type.lower()
    if "mpeg" in ct or "mp3" in ct:
        return "MP3"
    elif "aac" in ct:
        return "AAC"
    elif "ogg" in ct or "vorbis" in ct:
        return "OGG"
    elif "opus" in ct:
        return "OPUS"
    elif "flac" in ct:
        return "FLAC"
    elif "wav" in ct:
        return "WAV"
    elif "wma" in ct:
        return "WMA"
    return None


def _validate_response(status: int, headers: dict) -> StreamValidationResult:
    """Validate the HTTP response to determine if it's a valid audio stream"""

    # Check status code
    if status < 200 or status >= 400:
        return StreamValidationResult(
            is_valid=False,
            reason=f"HTTP error: status {status}"
        )

    content_type_raw = headers.get("Content-Type", headers.get("content-type", ""))
    content_type = _extract_content_type(content_type_raw)
    has_icy = _has_icy_headers(headers)
    icy_name, icy_bitrate = _extract_icy_info(headers)
    detected_codec = _detect_codec_from_content_type(content_type)

    # Check Content-Length - static files have small/fixed sizes, streams don't
    content_length = headers.get("Content-Length", headers.get("content-length"))
    if content_length:
        try:
            length = int(content_length)
            # Reject files smaller than 50MB (likely static file, not a stream)
            # Real streams either have no Content-Length or very large/infinite
            if length < 50 * 1024 * 1024:  # 50MB threshold
                return StreamValidationResult(
                    is_valid=False,
                    reason=f"Content-Length {length} bytes indicates static file, not a stream",
                    content_type=content_type
                )
        except ValueError:
            pass  # Invalid Content-Length, continue with other checks

    # IMPORTANT: Check dangerous content-types FIRST - reject regardless of ICY headers
    # This prevents spoofing by adding icy-* headers to HTML/images/etc.
    if content_type in DANGEROUS_CONTENT_TYPES:
        return StreamValidationResult(
            is_valid=False,
            reason=f"Invalid content type: {content_type} (not an audio stream)",
            content_type=content_type
        )

    # Case 1: Audio content-type - accept
    if content_type in AUDIO_CONTENT_TYPES:
        return StreamValidationResult(
            is_valid=True,
            reason=f"Valid audio stream ({content_type})",
            content_type=content_type,
            has_icy_headers=has_icy,
            detected_codec=detected_codec,
            icy_name=icy_name if has_icy else None,
            icy_bitrate=icy_bitrate if has_icy else None
        )

    # Case 2: application/octet-stream - only accept WITH ICY headers
    if content_type == "application/octet-stream":
        if has_icy:
            return StreamValidationResult(
                is_valid=True,
                reason="Valid radio stream (octet-stream with ICY headers)",
                content_type=content_type,
                has_icy_headers=True,
                detected_codec=detected_codec or "UNKNOWN",
                icy_name=icy_name,
                icy_bitrate=icy_bitrate
            )
        return StreamValidationResult(
            is_valid=False,
            reason="application/octet-stream without ICY headers - cannot verify as radio stream",
            content_type=content_type
        )

    # Case 3: Unknown/missing content-type - only accept WITH ICY headers
    if has_icy:
        return StreamValidationResult(
            is_valid=True,
            reason="Valid radio stream (ICY headers detected)",
            content_type=content_type,
            has_icy_headers=True,
            detected_codec=detected_codec or "UNKNOWN",
            icy_name=icy_name,
            icy_bitrate=icy_bitrate
        )

    # Case 4: Unknown content-type without ICY - reject to be safe
    return StreamValidationResult(
        is_valid=False,
        reason=f"Unknown content type: {content_type or 'none'} (no ICY headers)",
        content_type=content_type
    )


async def validate_stream(url: str, network: str) -> StreamValidationResult:
    """
    Validate that a URL points to a valid audio stream.

    Args:
        url: The stream URL to validate
        network: "tor" or "i2p"

    Returns:
        StreamValidationResult with validation outcome
    """
    if aiohttp is None:
        return StreamValidationResult(
            is_valid=False,
            reason="aiohttp not installed - cannot validate stream"
        )

    try:
        if network == "tor":
            connector = ProxyConnector.from_url(TOR_SOCKS_PROXY)
            async with aiohttp.ClientSession(connector=connector) as session:
                return await _check_url(session, url)
        else:  # i2p
            async with aiohttp.ClientSession() as session:
                return await _check_url(session, url, proxy=I2P_HTTP_PROXY)
    except asyncio.TimeoutError:
        return StreamValidationResult(
            is_valid=False,
            reason="Connection timeout - stream not reachable"
        )
    except aiohttp.ClientError as e:
        return StreamValidationResult(
            is_valid=False,
            reason=f"Connection error: {type(e).__name__}"
        )
    except Exception as e:
        return StreamValidationResult(
            is_valid=False,
            reason=f"Validation error: {type(e).__name__}: {str(e)}"
        )


async def _check_url(
    session: "aiohttp.ClientSession",
    url: str,
    proxy: Optional[str] = None
) -> StreamValidationResult:
    """Check a URL and validate the response headers"""

    # Build request kwargs
    kwargs = {
        "timeout": aiohttp.ClientTimeout(total=VALIDATION_TIMEOUT),
        "allow_redirects": True,
        "headers": {"Icy-Metadata": "1"}  # Request ICY metadata
    }
    if proxy:
        kwargs["proxy"] = proxy

    async with session.get(url, **kwargs) as response:
        # Convert headers to dict for easier handling
        headers = dict(response.headers)
        return _validate_response(response.status, headers)
