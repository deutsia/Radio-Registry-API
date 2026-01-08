"""
Configuration for Radio Registry API
"""
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).parent
DATABASE_PATH = BASE_DIR / "stations.db"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
COVERS_DIR = STATIC_DIR / "covers"

# Tor API/file server base URL (for serving cover art - you can use the same Tor url as the mirror)
TOR_BASE_URL = "http://your-onion-address.onion"

# Mirror URLs for different networks
# IMPORTANT: Update these with your actual .onion and .i2p addresses after deployment
MIRRORS = {
    "tor": {
        "name": "Tor",
        "url": "http://your-onion-address.onion",
        "host": "your-onion-address.onion",
    },
    "i2p": {
        "name": "I2P",
        "url": "http://your-i2p-address.b32.i2p",
        "host": "your-i2p-address.b32.i2p",
    },
    # Add clearnet mirror here if needed:
    # "clearnet": {
    #     "name": "Clearnet",
    #     "url": "https://your-domain.com",
    #     "host": "your-domain.com",
    # },
}

# Server settings
HOST = "127.0.0.1"
PORT = 8080

# Proxy settings for health checks
TOR_SOCKS_PROXY = "socks5://127.0.0.1:9050"
I2P_HTTP_PROXY = "http://127.0.0.1:4444"

# Health check settings
HEALTH_CHECK_TIMEOUT = 30  # seconds
HEALTH_CHECK_INTERVAL_HOURS = 4

# Recheck intervals for failed stations (in minutes)
# After a station fails, it gets rechecked at these intervals before being confirmed offline
RECHECK_INTERVALS_MINUTES = [5, 15, 60]  # 5 min, 15 min, 1 hour

# Time thresholds for health status (in hours)
DEAD_THRESHOLD_HOURS = 12  # No successful check for 12 hours = "dead"

# API settings
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# CORS origins - add your frontend URL(s) here
# Examples:
#   - GitHub Pages: "https://yourusername.github.io"
#   - Custom domain: "https://your-domain.com"
#   - Local dev: "http://localhost:3000"
CORS_ORIGINS = [
    "http://localhost:8000",
    "http://localhost:3000",
    "http://127.0.0.1:8000",
    # Add your production frontend URL here, e.g.:
    # "https://yourusername.github.io",
]

# Field validation
MAX_NAME_LENGTH = 100
MAX_URL_LENGTH = 500
MAX_GENRE_LENGTH = 50
MAX_COUNTRY_LENGTH = 100

# Supported codecs
VALID_CODECS = ["MP3", "AAC", "OGG", "OPUS", "FLAC", "UNKNOWN"]

# Default genres
DEFAULT_GENRES = [
    "Pop", "Rock", "Electronic", "Jazz", "Classical", "Hip-Hop",
    "Metal", "Ambient", "Experimental", "Talk", "News", "Mixed", "Other"
]

# Default languages
DEFAULT_LANGUAGES = [
    "Unknown", "English", "Spanish", "French", "German", "Portuguese",
    "Russian", "Chinese", "Japanese", "Korean", "Arabic", "Hindi",
    "Italian", "Dutch", "Polish", "Swedish", "Norwegian", "Finnish",
    "Danish", "Greek", "Turkish", "Hebrew", "Indonesian", "Vietnamese",
    "Thai", "Czech", "Hungarian", "Romanian", "Ukrainian", "Other"
]

# Admin panel settings
ADMIN_PASSWORD = "changeme"  # Change this in production!
ADMIN_SECRET_KEY = "super-secret-key-change-me"  # For session signing

# Notification settings (ntfy.sh push notifications)
NTFY_TOPIC = "your-radio-covers"  # Your private ntfy.sh topic
