#!/usr/bin/env bash
#
# PiMonitor installation and configuration script
#
# This script installs all necessary packages, detects a video capture device,
# builds the H.264 RTSP server if required, configures systemd services for
# streaming and the configuration API, writes an nginx site with optional
# authentication, and installs the web UI.  All settings are configurable via
# environment variables or later via the web UI.

set -euo pipefail

echo "[PiMonitor] Starting installation..."

#############################################
# User‑tunable defaults
# These values may be overridden via environment variables when running
# the installer.  They are also persisted to /etc/pimonitor.conf and can
# be modified later via the web UI.

RESOLUTION="${RESOLUTION:-1280x720}"          # e.g. 1920x1080 or 1280x720
FPS="${FPS:-30}"                            # safe values: 25 or 30
STREAM_MODE="${STREAM_MODE:-MJPEG}"          # MJPEG or H264_RTSP
HTTP_PORT="${HTTP_PORT:-8080}"              # port for uStreamer (MJPEG)
RTSP_PORT="${RTSP_PORT:-8554}"              # port for v4l2rtspserver (H.264)
SITE_PORT="${SITE_PORT:-80}"                # nginx port for landing/config pages
CONFIG_PORT="${CONFIG_PORT:-5000}"          # port for the Flask API server
DEVICE_INDEX="${DEVICE_INDEX:-0}"           # which capture device to use (0‑based)
ENABLE_AUTH="${ENABLE_AUTH:-0}"              # 1 = enable HTTP basic auth
AUTH_USERNAME="${AUTH_USERNAME:-admin}"      # username for basic auth
AUTH_PASSWORD="${AUTH_PASSWORD:-password}"    # password for basic auth
DISABLE_WIFI_POWERSAVE="${DISABLE_WIFI_POWERSAVE:-1}" # disable Wi‑Fi power save
RECORD_DIR="${RECORD_DIR:-}"                 # directory for recordings/snapshots

#############################################
# Helper functions

fatal() {
  echo "[ERROR] $*" >&2
  exit 1
}

# Determine the directory where this script resides.  We'll use this to copy
# supporting files (HTML, Python, helper scripts).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Find the user that invoked sudo or fallback to 'pi'
SERVICE_USER="${SUDO_USER:-pi}"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  SERVICE_USER="pi"
fi

# If RECORD_DIR wasn't provided, default to a subdirectory in the user's home
if [ -z "$RECORD_DIR" ]; then
  RECORD_DIR="/home/${SERVICE_USER}/pimonitor-recordings"
fi

#############################################
# Remove any old service that might conflict (mjpg‑streamer, ustreamer)

if systemctl list-unit-files | grep -q '^mjpg-streamer.service'; then
  echo "[0/12] Removing old mjpg-streamer service..."
  systemctl disable --now mjpg-streamer.service || true
  rm -f /etc/systemd/system/mjpg-streamer.service
  systemctl daemon-reload
fi
if systemctl list-unit-files | grep -q '^ustreamer.service'; then
  echo "[0/12] Removing old ustreamer service..."
  systemctl disable --now ustreamer.service || true
  rm -f /etc/systemd/system/ustreamer.service
  systemctl daemon-reload
fi
if systemctl list-unit-files | grep -q '^pimonitor-stream.service'; then
  echo "[0/12] Removing existing pimonitor-stream.service..."
  systemctl disable --now pimonitor-stream.service || true
  rm -f /etc/systemd/system/pimonitor-stream.service
  systemctl daemon-reload
fi
if systemctl list-unit-files | grep -q '^pimonitor-api.service'; then
  echo "[0/12] Removing existing pimonitor-api.service..."
  systemctl disable --now pimonitor-api.service || true
  rm -f /etc/systemd/system/pimonitor-api.service
  systemctl daemon-reload
fi

#############################################
# Update packages and install dependencies

echo "[1/12] Updating package index and installing dependencies..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  v4l-utils ustreamer nginx ffmpeg python3 python3-flask apache2-utils \
  git build-essential cmake liblog4cpp5-dev libv4l-dev

# Optionally install pip if python3-flask isn't available (fallback)
if ! python3 -c "import flask" >/dev/null 2>&1; then
  apt-get install -y python3-pip
  pip3 install flask
fi

#############################################
# Detect video devices

echo "[2/12] Detecting available video devices..."
DEVICE_LIST=()
if [ -d /dev/v4l/by-id ]; then
  while IFS= read -r dev; do
    DEVICE_LIST+=("$dev")
  done < <(ls -1 /dev/v4l/by-id/*video-index0 2>/dev/null || true)
fi
# Fallback to enumerating /dev/video* if by-id is empty
if [ ${#DEVICE_LIST[@]} -eq 0 ]; then
  while IFS= read -r dev; do
    DEVICE_LIST+=("$dev")
  done < <(ls -1 /dev/video[0-9]* 2>/dev/null || true)
fi
if [ ${#DEVICE_LIST[@]} -eq 0 ]; then
  fatal "No video capture devices found. Plug a capture card or webcam and rerun."
fi

if [ "$DEVICE_INDEX" -ge "${#DEVICE_LIST[@]}" ]; then
  fatal "DEVICE_INDEX=$DEVICE_INDEX is out of range (${#DEVICE_LIST[@]} devices detected)."
fi

DEVICE="${DEVICE_LIST[$DEVICE_INDEX]}"
echo "Using device: $DEVICE"
echo
echo "Supported formats for $DEVICE:"
v4l2-ctl --device="$DEVICE" --list-formats-ext || true

# Parse width and height from resolution string (e.g. 1920x1080)
W="${RESOLUTION%x*}"
H="${RESOLUTION#*x}"

#############################################
# Ensure the service user can access video devices

echo "[3/12] Ensuring user '$SERVICE_USER' is in the 'video' group..."
usermod -aG video "$SERVICE_USER" || true

#############################################
# Build v4l2rtspserver if it's not already present.  Even if the initial stream
# mode is MJPEG, the user may switch to H.264 later via the web UI.  Having
# v4l2rtspserver available prevents failures on service restart.

if ! command -v v4l2rtspserver >/dev/null 2>&1; then
  echo "[4/12] Building v4l2rtspserver (H.264 RTSP)... this may take a while"
  rm -rf /tmp/v4l2rtspserver || true
  git clone --depth=1 https://github.com/mpromonet/v4l2rtspserver.git /tmp/v4l2rtspserver
  (cd /tmp/v4l2rtspserver && cmake . && make && make install)
  rm -rf /tmp/v4l2rtspserver
else
  echo "[4/12] v4l2rtspserver already installed"
fi

#############################################
# Write /etc/pimonitor.conf for the API and future reconfiguration

echo "[5/12] Writing configuration to /etc/pimonitor.conf..."
cat >/etc/pimonitor.conf <<EOF
RESOLUTION=$RESOLUTION
FPS=$FPS
STREAM_MODE=$STREAM_MODE
DEVICE=$DEVICE
HTTP_PORT=$HTTP_PORT
RTSP_PORT=$RTSP_PORT
SITE_PORT=$SITE_PORT
CONFIG_PORT=$CONFIG_PORT
ENABLE_AUTH=$ENABLE_AUTH
AUTH_USERNAME=$AUTH_USERNAME
AUTH_PASSWORD=$AUTH_PASSWORD
RECORD_DIR=$RECORD_DIR
EOF

#############################################
# Install web UI and helper scripts

echo "[6/12] Installing web UI and helper scripts..."
# Create the target directory for the web UI
install -d /var/www/pimonitor
# Copy index.html and config.html from the repository
cp "$SCRIPT_DIR/web/index.html" /var/www/pimonitor/index.html
cp "$SCRIPT_DIR/web/config.html" /var/www/pimonitor/config.html

# Copy helper scripts for recordings/snapshots into /usr/local/bin
install -m 0755 "$SCRIPT_DIR/scripts/record_stream.sh" /usr/local/bin/pimonitor-record
install -m 0755 "$SCRIPT_DIR/scripts/snapshot.sh" /usr/local/bin/pimonitor-snapshot

# Copy the Flask API server into /usr/local/bin
install -m 0755 "$SCRIPT_DIR/pimonitor_api.py" /usr/local/bin/pimonitor_api.py

#############################################
# Create systemd service for streaming

echo "[7/12] Creating pimonitor-stream.service..."
if [ "$STREAM_MODE" = "MJPEG" ]; then
  cat >/etc/systemd/system/pimonitor-stream.service <<EOF
[Unit]
Description=PiMonitor streaming service (MJPEG)
After=network.target

[Service]
ExecStartPre=/usr/bin/v4l2-ctl --device=$DEVICE --set-fmt-video=width=$W,height=$H,pixelformat=MJPG
ExecStartPre=/usr/bin/v4l2-ctl --device=$DEVICE --set-parm=$FPS
ExecStart=/usr/bin/ustreamer \
  --device=$DEVICE \
  --format=MJPEG \
  --resolution=$RESOLUTION \
  --desired-fps=$FPS \
  --host=0.0.0.0 \
  --port=$HTTP_PORT \
  --buffers=4
Restart=always
User=$SERVICE_USER
Group=$SERVICE_USER

[Install]
WantedBy=multi-user.target
EOF
else
  # H.264 RTSP mode
  cat >/etc/systemd/system/pimonitor-stream.service <<EOF
[Unit]
Description=PiMonitor streaming service (H.264 RTSP)
After=network.target

[Service]
ExecStart=/usr/local/bin/v4l2rtspserver -W $W -H $H -F $FPS -P $RTSP_PORT -u stream $DEVICE
Restart=always
User=$SERVICE_USER
Group=$SERVICE_USER

[Install]
WantedBy=multi-user.target
EOF
fi

#############################################
# Create systemd service for the API server

echo "[8/12] Creating pimonitor-api.service..."
cat >/etc/systemd/system/pimonitor-api.service <<EOF
[Unit]
Description=PiMonitor configuration API
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/pimonitor_api.py
Restart=always
User=root
Group=root
Environment=PYTHONUNBUFFERED=1
Environment=CONFIG_FILE=/etc/pimonitor.conf
Environment=SERVICE_NAME=pimonitor-stream.service
Environment=CONFIG_PORT=$CONFIG_PORT

[Install]
WantedBy=multi-user.target
EOF

#############################################
# Configure nginx

echo "[9/12] Configuring nginx..."
# Build auth directives if enabled
if [ "$ENABLE_AUTH" = "1" ]; then
  # Create htpasswd file with provided credentials
  htpasswd -cb /etc/nginx/.pimonitor_htpasswd "$AUTH_USERNAME" "$AUTH_PASSWORD"
  AUTH_DIRECTIVES=$(cat <<'AUTH'
    auth_basic "PiMonitor";
    auth_basic_user_file /etc/nginx/.pimonitor_htpasswd;
AUTH
  )
else
  AUTH_DIRECTIVES="# Authentication disabled"
fi

# Compute redirect directive for the /stream endpoint based on the stream mode.
if [ "$STREAM_MODE" = "MJPEG" ]; then
  # MJPEG uses an HTTP backend served by uStreamer
  # Use a single backslash to escape the dollar sign so that nginx sees $host
  STREAM_REDIRECT="return 302 http://\$host:${HTTP_PORT}/stream;"
else
  # H.264 uses an RTSP backend served by v4l2rtspserver
  STREAM_REDIRECT="return 302 rtsp://\$host:${RTSP_PORT}/stream;"
fi

# Write nginx site configuration
cat >/etc/nginx/sites-available/pimonitor <<NGX
server {
    listen $SITE_PORT;
    server_name _;

    root /var/www/pimonitor;
    index index.html;

    $AUTH_DIRECTIVES

    # Serve static files and index
    location / {
        try_files \$uri \$uri/ =404;
    }

    # Redirect /stream to the backend stream (MJPEG or RTSP)
    location /stream {
        $STREAM_REDIRECT
    }

    # Proxy API requests to the Flask server
    location /api/ {
        proxy_pass http://127.0.0.1:$CONFIG_PORT/api/;
        proxy_set_header Host \$host;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGX

# Enable the site and disable default
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/pimonitor /etc/nginx/sites-enabled/pimonitor
nginx -t
systemctl reload nginx

#############################################
# Optionally disable Wi‑Fi power saving

echo "[10/12] Configuring Wi‑Fi power save..."
if [ "$DISABLE_WIFI_POWERSAVE" = "1" ]; then
  cat >/etc/systemd/system/wlan-nosave.service <<'UNIT'
[Unit]
Description=Disable Wi-Fi powersave (helps streaming stability)
After=network-online.target

[Service]
Type=oneshot
ExecStart=/sbin/iw dev wlan0 set power_save off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
  systemctl enable --now wlan-nosave.service || true
fi

#############################################
# Enable and start services

echo "[11/12] Enabling and starting services..."
systemctl daemon-reload
systemctl enable --now pimonitor-stream.service
systemctl enable --now pimonitor-api.service

#############################################
# Display information to the user

IP_ADDR="$(hostname -I | awk '{print $1}')"
echo "[12/12] Done ✅"
echo "Landing page:   http://${IP_ADDR}:${SITE_PORT}/"
if [ "$STREAM_MODE" = "MJPEG" ]; then
  echo "Raw MJPEG stream: http://${IP_ADDR}:${HTTP_PORT}/stream"
else
  echo "RTSP stream:     rtsp://${IP_ADDR}:${RTSP_PORT}/stream"
fi
echo "Config page:    http://${IP_ADDR}:${SITE_PORT}/config/"
echo
echo "Configuration file: /etc/pimonitor.conf"
echo "Recordings & snapshots saved to: $RECORD_DIR"
