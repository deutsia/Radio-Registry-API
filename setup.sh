#!/bin/bash
# Setup script for Tor/I2P Radio Directory API
# Run this on your server after copying the files

set -e

INSTALL_DIR="$HOME/Radio-Registry-API"

echo "=== Tor/I2P Radio Directory Setup ==="
echo ""

# Check Python version
python_version=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "Python version: $python_version"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
cd "$INSTALL_DIR"
python3 -m venv venv

# Activate and install dependencies
echo "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Initialize database
echo ""
echo "Initializing database..."
python -c "import database; database.init_db(); print('Database initialized at:', database.get_db_path())"

# Make scripts executable
chmod +x admin.py checker.py

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo ""
echo "1. Test the server locally:"
echo "   cd $INSTALL_DIR"
echo "   source venv/bin/activate"
echo "   uvicorn main:app --host 127.0.0.1 --port 8080"
echo ""
echo "2. Import your existing stations:"
echo "   python admin.py import /path/to/tor_stations.json --network tor --approve"
echo "   python admin.py import /path/to/i2p_stations.json --network i2p --approve"
echo ""
echo "3. Install systemd service:"
echo "   sudo cp radio-api.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable radio-api"
echo "   sudo systemctl start radio-api"
echo ""
echo "4. Set up health check cron (every 5 minutes - uses smart scheduling):"
echo "   crontab -e"
echo "   Add: */5 * * * * $INSTALL_DIR/venv/bin/python $INSTALL_DIR/checker.py >> $INSTALL_DIR/checker.log 2>&1"
echo ""
echo "   Note: The checker uses smart scheduling - it only checks stations that are due."
echo "   Online stations: rechecked every 4 hours"
echo "   Failed stations: rechecked at 5min, 15min, 1hr before marking offline"
echo "   Dead stations (12+ hrs offline): rechecked every 4 hours"
echo ""
echo "5. Set up Cloudflare Tunnel for HTTPS access"
echo ""
