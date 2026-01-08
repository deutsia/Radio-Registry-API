# Radio Registry API

A privacy-focused radio station directory designed for Tor and I2P networks. This FastAPI application provides both a JSON API and a server-rendered HTML interface for discovering, submitting, and managing radio stations accessible through anonymous networks.

**This is the standard version** - cover art URLs are submitted for manual review and embededd directly from their original sources for review. No automatic downloading or local mirroring of cover art without manual review.

> **Note:** If you need automatic cover art mirroring with NSFW detection, see the automatic mirroring release instead.

## Features

- **Radio Station Directory** - Browse and search stations by network (Tor/I2P), genre, and online status
- **Station Submission** - Submit new stations with automatic stream validation and approval
- **Stream Validation** - Validates that URLs point to actual audio streams (not HTML, images, etc.)
- **Health Monitoring** - Periodic checks to track station online/offline status
- **Admin Dashboard** - Web-based admin panel for station moderation
- **Admin CLI** - Command-line tools for bulk operations (import, export, approve, reject)
- **Network-Aware Routing** - Automatic proxy routing through Tor SOCKS5 or I2P HTTP
- **Rate Limiting** - API protection with slowapi (60/min for listings, 5/min for submissions)
- **No JavaScript** - (Optional minimal JS in the Admin Panel, can be stripped in the backend)

## Technology Stack

- **FastAPI** - Modern async web framework
- **Uvicorn** - ASGI server
- **Pydantic** - Data validation
- **SQLite** - Embedded database
- **Jinja2** - HTML templating
- **aiohttp** - Async HTTP client with SOCKS proxy support
- **slowapi** - Rate limiting
- **ntfy.sh** - Push notifications for admin alerts (cover art review)

## Installation

### Prerequisites

- Python 3.8+
- Tor service running (for Tor station validation)
- I2P router with HTTP proxy (for I2P station validation)

### Quick Setup

```bash
# Clone the repository
git clone <repository-url>
cd Radio-Registry-API

# Run the setup script
./setup.sh

# Or manually:
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -c "import database; database.init_db()"
```

### Configuration

Edit `config.py` to customize settings:

```python
# Server settings
HOST = "127.0.0.1"
PORT = 8080

# Proxy settings (adjust to your Tor/I2P configuration)
TOR_SOCKS_PROXY = "socks5://127.0.0.1:9050"
I2P_HTTP_PROXY = "http://127.0.0.1:4444"

# IMPORTANT: Change these in production!
ADMIN_PASSWORD = "changeme"
ADMIN_SECRET_KEY = "super-secret-key-change-me"
```

### Running the Server

```bash
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8080
```

## API Reference

### JSON API Endpoints

| Method | Endpoint | Description | Rate Limit |
|--------|----------|-------------|------------|
| GET | `/api/stations` | List approved stations (paginated) | 60/min |
| GET | `/api/stations/{id}` | Get station details | - |
| GET | `/api/stations/{id}/cover` | Get station  | - |
| POST | `/api/submit` | Submit a new station | 5/min |
| GET | `/api/stats` | Get directory statistics | - |
| GET | `/api/genres` | List available genres | - |
| GET | `/api/health` | API health check | - |

### Query Parameters

**GET /api/stations**

| Parameter | Type | Description |
|-----------|------|-------------|
| `network` | string | Filter by network: `tor` or `i2p` |
| `genre` | string | Filter by genre |
| `online_only` | boolean | Only show online stations |
| `page` | integer | Page number (default: 1) |
| `per_page` | integer | Items per page (default: 50, max: 200) |

### Example Requests

```bash
# List all Tor stations
curl "http://localhost:8080/api/stations?network=tor"

# List online electronic stations
curl "http://localhost:8080/api/stations?genre=Electronic&online_only=true"

# Get directory stats
curl "http://localhost:8080/api/stats"

# Submit a new station
curl -X POST "http://localhost:8080/api/submit" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Radio Station",
    "stream_url": "http://example.onion:8000/stream",
    "network": "tor",
    "genre": "Electronic"
  }'
```

### Response Examples

**Station List Response**
```json
{
  "stations": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Example Radio",
      "stream_url": "http://example.onion:8000/stream",
      "homepage": "http://example.onion",
      "network": "tor",
      "genre": "Electronic",
      "codec": "MP3",
      "bitrate": 128,
      "language": "English",
      "country": "Unknown",
      "is_online": true,
      "last_check_time": "2024-01-15T12:00:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 50,
  "pages": 1
}
```

**Stats Response**
```json
{
  "total_stations": 100,
  "online_stations": 75,
  "tor_stations": 60,
  "i2p_stations": 40,
  "pending_submissions": 5
}
```

## HTML Interface

The server-rendered HTML interface is accessible at:

| Path | Description |
|------|-------------|
| `/` | Station listing with filters |
| `/station/{id}` | Station detail page |
| `/submit` | Station submission form |
| `/about` | About page |
| `/admin` | Admin login |
| `/admin/dashboard` | Admin dashboard |

## Admin CLI

The `admin.py` script provides command-line management tools:

```bash
# List all stations
python admin.py list

# List pending submissions
python admin.py pending

# Show statistics
python admin.py stats

# Approve a station
python admin.py approve <station-id>

# Reject a station
python admin.py reject <station-id> --reason "Invalid stream"

# Delete a station
python admin.py delete <station-id>

# Get station info
python admin.py info <station-id>

# Import stations from JSON
python admin.py import stations.json --network tor --approve

# Export stations to JSON
python admin.py export output.json --network tor --status approved
```

### Import JSON Format

```json
[
  {
    "name": "Station Name",
    "stream_url": "http://example.onion:8000/stream",
    "homepage": "http://example.onion",
    "genre": "Electronic",
    "codec": "MP3",
    "bitrate": 128,
    "language": "English",
    "country": "Unknown"
  }
]
```

## Health Checks

The `checker.py` script performs smart health checks with escalating rechecks to prevent false offline status from transient failures.

### Health Status

Stations have three health states:

| Status | Description |
|--------|-------------|
| **online** | Last check succeeded |
| **offline** | Failed recently, but was online within 12 hours (being rechecked) |
| **dead** | No successful response for 12+ hours |

### Escalating Rechecks

When a station fails a check, it doesn't immediately show as offline. Instead, the system performs escalating rechecks:

```
✓ Online     → next check in 4 hours
✗ Fail #1    → recheck in 5 minutes
✗ Fail #2    → recheck in 15 minutes
✗ Fail #3    → recheck in 1 hour
✗ Fail #4+   → confirmed offline, regular 4-hour checks resume
```

If a station remains unreachable for 12+ hours, it's marked as **dead**.

This prevents reliable stations from appearing offline due to temporary network issues on Tor/I2P.

## Customizations

All major settings can be customized in `config.py`. Here's a complete reference:

### Health Check Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `HEALTH_CHECK_TIMEOUT` | 30 | Seconds to wait for a station to respond before marking as failed |
| `HEALTH_CHECK_INTERVAL_HOURS` | 4 | Hours between regular health checks for online stations |
| `RECHECK_INTERVALS_MINUTES` | [5, 15, 60] | Escalating recheck intervals (in minutes) for failed stations |
| `DEAD_THRESHOLD_HOURS` | 12 | Hours without response before a station is marked "dead" |

**Example: Faster health checks (hourly)**
```python
HEALTH_CHECK_INTERVAL_HOURS = 1  # Check online stations every hour instead of 4
HEALTH_CHECK_TIMEOUT = 20  # Shorter timeout for faster checks
```

**Example: More aggressive failure detection**
```python
RECHECK_INTERVALS_MINUTES = [2, 5, 15]  # Faster escalation: 2min, 5min, 15min
DEAD_THRESHOLD_HOURS = 6  # Mark dead after 6 hours instead of 12
```

**Example: More lenient (for unreliable networks)**
```python
RECHECK_INTERVALS_MINUTES = [10, 30, 120]  # Slower escalation: 10min, 30min, 2hr
DEAD_THRESHOLD_HOURS = 24  # Wait 24 hours before marking dead
```

### Server Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `HOST` | "127.0.0.1" | Address to bind the server to |
| `PORT` | 8080 | Port to run the server on |
| `TOR_SOCKS_PROXY` | "socks5://127.0.0.1:9050" | Tor SOCKS5 proxy for validating .onion streams |
| `I2P_HTTP_PROXY` | "http://127.0.0.1:4444" | I2P HTTP proxy for validating .i2p streams |

### API Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `DEFAULT_PAGE_SIZE` | 50 | Default number of stations per page |
| `MAX_PAGE_SIZE` | 200 | Maximum stations per page (prevents abuse) |
| `CORS_ORIGINS` | localhost | Allowed CORS origins for API access |

### Content Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_NAME_LENGTH` | 100 | Maximum characters for station names |
| `MAX_URL_LENGTH` | 500 | Maximum characters for URLs |
| `DEFAULT_GENRES` | [list] | Available genre options for submissions |
| `DEFAULT_LANGUAGES` | [list] | Available language options |

### Configuration

In `config.py`:

```python
# Recheck intervals for failed stations (in minutes)
RECHECK_INTERVALS_MINUTES = [5, 15, 60]  # 5 min, 15 min, 1 hour

# Time threshold for "dead" status (in hours)
DEAD_THRESHOLD_HOURS = 12

# Regular check interval for online stations
HEALTH_CHECK_INTERVAL_HOURS = 4
```

### Running the Checker

```bash
# Run manually
python checker.py

# Set up cron job (every 5 minutes - uses smart scheduling)
*/5 * * * * /path/to/venv/bin/python /path/to/checker.py >> /path/to/checker.log 2>&1
```

**Note:** The cron runs every 5 minutes, but the checker only checks stations that are actually due. Online stations won't be hammered - they're only checked every 4 hours. The frequent cron is to catch the escalating rechecks for recently-failed stations.

### Checker Output

```
============================================================
Health check run - 2024-01-15 12:00:00
============================================================
Stations due for check: 5
  Tor: 3, I2P: 2
  Regular: 2, Rechecks: 2, Dead: 1

Checking 3 Tor stations via socks5://127.0.0.1:9050...
  [regular] http://example.onion/stream
    ✓ online
  [recheck #2] http://failing.onion/stream
    ✗ offline
  [dead-check] http://dead.onion/stream
    ✗ offline

============================================================
Health check complete
  Checked: 5
  Online: 2, Offline: 3
  Recovered: 1 (were offline, now online)
============================================================
```

## Pre-Deployment Checklist

Before deploying to production, complete these steps:

### Security Configuration

- [ ] **Change admin password** - Update `ADMIN_PASSWORD` in `config.py` to a strong, unique password
- [ ] **Generate secret key** - Replace `ADMIN_SECRET_KEY` with a secure random value:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- [ ] **Review CORS origins** - Update `CORS_ORIGINS` to only allow your frontend domains

### Service URLs

- [ ] **Set Tor address** - Update `TOR_BASE_URL` and `MIRRORS["tor"]` with your .onion address
- [ ] **Set I2P address** (if using) - Update `MIRRORS["i2p"]` with your .b32.i2p address
- [ ] **Set clearnet URL** (if using) - Uncomment and configure `MIRRORS["clearnet"]`

### Network & Proxy

- [ ] **Verify Tor proxy** - Ensure Tor is running and `TOR_SOCKS_PROXY` points to correct address
- [ ] **Verify I2P proxy** (if using) - Ensure I2P router is running and `I2P_HTTP_PROXY` is correct
- [ ] **Configure firewall** - Block direct access to port 8080 from public networks

### Final Steps

- [ ] **Test stream validation** - Verify the API can reach Tor/I2P streams through configured proxies
- [ ] **Set up health checker** - Configure cron job for `checker.py`
- [ ] **Enable systemd service** - Install and enable the service file

---

## Production Deployment

### Systemd Service

A systemd service file is provided (`radio-api.service`):

```bash
# Copy and edit the service file
sudo cp radio-api.service /etc/systemd/system/
sudo nano /etc/systemd/system/radio-api.service

# Enable and start
sudo systemctl enable radio-api
sudo systemctl start radio-api

# Check status
sudo systemctl status radio-api
```

### Security Hardening

The service file includes security features:
- Memory limit (200MB)
- CPU quota (50%)
- No new privileges
- Read-only home directory
- Private /tmp

### Reverse Proxy

For HTTPS access, use a reverse proxy like nginx or Cloudflare Tunnel:

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Project Structure

```
.
├── main.py              # FastAPI application and routes
├── database.py          # SQLite database operations
├── models.py            # Pydantic models
├── config.py            # Configuration settings
├── stream_validator.py  # Audio stream validation
├── checker.py           # Health check script
├── admin.py             # CLI admin tool
├── requirements.txt     # Python dependencies
├── setup.sh             # Setup script
├── radio-api.service    # Systemd service file
├── stations.db          # SQLite database
├── templates/           # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── station.html
│   ├── submit.html
│   ├── about.html
│   ├── admin_login.html
│   ├── admin.html
│   ├── 404.html
│   └── 500.html
└── static/              # Static files
```

## Network Configuration

### Tor Setup

Ensure Tor is running with SOCKS proxy on port 9050:

```bash
# Install Tor
sudo apt install tor

# Verify it's running
curl --socks5 127.0.0.1:9050 https://check.torproject.org/api/ip
```

### I2P Setup

Ensure I2P HTTP proxy is available on port 4444:

```bash
# Download I2P installer
wget https://geti2p.net/en/download/2.10.0/clearnet/https/files.i2p-projekt.de/i2pinstall_2.10.0.jar/download -O i2pinstall.jar

# Install I2P (follow the GUI installer prompts)
java -jar i2pinstall.jar

# Start I2P router
~/i2p/i2prouter start

# Verify proxy (should return I2P content)
curl --proxy http://127.0.0.1:4444 http://i2p-projekt.i2p
```

## Deploying Your Own Instance

### Configuration

After installation, you **must** configure `config.py`:

1. **Change default credentials** (CRITICAL for security):
```python
ADMIN_PASSWORD = "your-secure-password-here"
ADMIN_SECRET_KEY = "your-random-secret-key-here"  # Use secrets.token_hex(32)
```

2. **Set your service URLs:**
```python
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
    # Clearnet mirror (optional):
    # "clearnet": {
    #     "name": "Clearnet",
    #     "url": "https://your-domain.com",
    #     "host": "your-domain.com",
    # },
}
```

### Setting Up Tor Hidden Service

1. Edit `/etc/tor/torrc`:
```
HiddenServiceDir /var/lib/tor/radio-registry/
HiddenServicePort 80 127.0.0.1:8080
```

2. Restart Tor:
```bash
sudo systemctl restart tor
```

3. Get your .onion address:
```bash
sudo cat /var/lib/tor/radio-registry/hostname
```

### Setting Up I2P Tunnel (Optional)

1. Install I2P and start the router
2. Configure an I2P server tunnel pointing to `127.0.0.1:8080`
3. Get your `.b32.i2p` address from the I2P router console
4. Update `MIRRORS` in `config.py` with your .i2p address

### Push Notifications with ntfy

The API supports push notifications via [ntfy.sh](https://ntfy.sh) to alert admins when  needs review.

**Setup:**

1. Choose a unique, private topic name (e.g., `my-radio-covers-abc123`)
2. Configure in `config.py`:
```python
NTFY_TOPIC = "my-radio-covers-abc123"
```

3. Subscribe to your topic:
   - **Web:** Visit `https://ntfy.sh/my-radio-covers-abc123`
   - **Mobile:** Install the ntfy app and subscribe to your topic
   - **Desktop:** Use the PWA or CLI tool

Notifications are sent automatically when new  is submitted and needs approval.

## Supported Formats

### Audio Codecs
- MP3
- AAC
- OGG Vorbis
- Opus
- FLAC
- WAV
- WMA

### Streaming Protocols
- Direct streams (Icecast/Shoutcast with ICY metadata)
- HLS (HTTP Live Streaming / m3u8 playlists)
- DASH (Dynamic Adaptive Streaming)

## 

Cover art URLs submitted with stations are externally embedded for manual review with css blur and a JS toggle. While this version does download and mirror cover art locally, **it is only after manual review.**

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| `/api/stations` | 60 requests/minute |
| `/api/submit` | 5 requests/minute |
| Other endpoints | No limit |

## Versions

This project has multiple versions available:

- **This version (standard):** No automatic cover art mirroring - Manual review with external embedding + blur. 
- **Automatic mirroring version:** Full automatic mirroring with cover art downloading and NSFW detection (requires Torch/ML dependencies)

Choose the version that best fits your use case and resources.

## Security Considerations

- **Never expose port 8080 to the public internet** - only access via Tor/I2P
- **Change default passwords immediately** after installation
- **Enable 2FA** for admin panel access
- **Run as unprivileged user** with systemd service
- **Regular updates** - keep dependencies updated for security patches
- **Backup your database** regularly (`stations.db`)

## Contributing

Contributions are welcome! To contribute:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Test thoroughly
5. Commit with clear messages
6. Submit a pull request

Please ensure:
- Code follows existing style
- No hardcoded credentials or personal info
- Changes are well-documented
- Tests pass (if applicable)

## Support

- **Issues:** Report bugs or request features via GitHub Issues
- **Documentation:** See `/about` page for setup help
- **Security:** Report security issues privately via GitHub Security Advisories

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

You are free to:
- Use commercially
- Modify
- Distribute
- Use privately

Under the conditions:
- Include original license and copyright
- State changes made
- Include NOTICE file if present
