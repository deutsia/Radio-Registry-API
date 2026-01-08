"""
Notification system for cover approval workflow.
Sends push notifications via ntfy.sh.
"""
import logging

try:
    import aiohttp
except ImportError:
    aiohttp = None

from config import TOR_BASE_URL, NTFY_TOPIC

logger = logging.getLogger(__name__)


async def notify_cover_pending(station_id: str, station_name: str):
    """
    Send push notification when a cover needs review.
    """
    if not NTFY_TOPIC:
        logger.debug("ntfy topic not configured, skipping push notification")
        return False

    if aiohttp is None:
        logger.warning("aiohttp not installed, skipping push notification")
        return False

    admin_url = f"{TOR_BASE_URL}/admin/covers"

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Title": f"Cover Review: {station_name}",
                "Priority": "default",
                "Tags": "art,review",
                "Click": admin_url,
            }

            message = "New cover art needs approval"

            async with session.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=message.encode("utf-8"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    logger.info(f"Push notification sent for station {station_id}")
                    return True
                else:
                    logger.error(f"ntfy.sh returned status {response.status}")
                    return False

    except Exception as e:
        logger.error(f"Failed to send push notification: {e}")
        return False
