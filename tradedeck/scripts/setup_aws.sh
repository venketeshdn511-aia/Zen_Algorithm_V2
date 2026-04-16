#!/bin/bash

# --- TradeDeck v2 AWS Zero-Touch Setup ---
# Purpose: Install Docker, clean up old processes, and start the trading bot.

set -e

# Identify the project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
BASE_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "🚀 Starting TradeDeck AWS Setup..."
echo "📂 Project Root: $BASE_DIR"

cd "$BASE_DIR"

# Fix permissions for the project directory
echo "🛡️ Fixing directory permissions..."
sudo chmod -R u+rw .

# 1. Check for Docker
if ! [ -x "$(command -v docker)" ]; then
    echo "📦 Installing Docker..."
    sudo apt-get update
    sudo apt-get install -y docker.io
    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
    echo "✅ Docker installed."
else
    echo "✅ Docker is already installed."
fi

# 2. Check for Docker Compose (V2 Plugin)
if ! docker compose version &> /dev/null; then
    echo "📦 Installing Docker Compose V2 Plugin..."
    sudo apt-get update
    sudo apt-get install -y docker-compose-v2 || sudo apt-get install -y docker-compose
    echo "✅ Docker Compose plugin installed."
else
    echo "✅ Docker Compose V2 is already available."
fi

# Use 'docker compose' if available, fallback to 'docker-compose'
DOCKER_COMPOSE="docker compose"
if ! docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
fi

# 3. Stop existing process on Port 8000 (Cleanup)
echo "🧹 Checking for existing processes on Port 8000..."
OLD_PID=$(sudo lsof -t -i:8000 || true)
if [ -z "$OLD_PID" ]; then
    echo "   No existing processes found on port 8000."
else
    echo "   Stopping process $OLD_PID..."
    sudo kill -9 $OLD_PID || true
fi

# 4. Prepare Directories
echo "📂 Ensuring deployment directories exist..."
mkdir -p deploy/nginx/ssl
mkdir -p logs

# 5. Enable Swap (Crucial for T2/T3.micro instances during builds)
if [ ! -f /swapfile ]; then
    echo "💾 Creating 2GB Swap file for build stability..."
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
    echo "✅ Swap enabled."
else
    echo "✅ Swap is already enabled."
fi

# 6. Build and Start Containers
echo "🏗️ Building containers sequentially to save RAM (using $DOCKER_COMPOSE)..."
sudo $DOCKER_COMPOSE down --remove-orphans || true
# Build sequentially — parallel builds crash small instances
sudo $DOCKER_COMPOSE build --no-cache api
sudo $DOCKER_COMPOSE up -d

# 6. Run Database Migrations
echo "🗄️ Running database migrations (Pre-startup)..."
# Wait a bit longer for Postgres to be fully initialized
sleep 10
# Run migration in a separate container to ensure tables are created before the main app boots
sudo $DOCKER_COMPOSE run --rm api python migrate_remote.py || {
    echo "⚠️ Migration failed. Checking Postgres reachability..."
    sudo $DOCKER_COMPOSE exec -T postgres pg_isready -U trader
    exit 1
}
echo "✅ Database migrations completed."

# 7. Setup Systemd Persistence
echo "⚙️ Setting up Systemd service for auto-restart..."
SERVICE_FILE="/etc/systemd/system/tradedeck.service"
cat <<EOF | sudo tee $SERVICE_FILE
[Unit]
Description=TradeDeck v2 Docker Services
Requires=docker.service
After=docker.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=/usr/bin/$DOCKER_COMPOSE up
ExecStop=/usr/bin/$DOCKER_COMPOSE down
Restart=always
User=$USER
EnvironmentFile=$BASE_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tradedeck
echo "✅ Persistence enabled."

echo ""
echo "✨ SETUP COMPLETE ✨"
echo "Your trading bot is now running!"
echo "Check health at: http://$(curl -s ifconfig.me):8000/health"
echo "Check dashboard at: http://$(curl -s ifconfig.me):8000/"
