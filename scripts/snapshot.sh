#!/usr/bin/env bash
#
# Capture a single frame from the PiMonitor stream and save it as a JPEG image.
#
# Usage: pimonitor-snapshot [FILENAME]
#   FILENAME – name of the JPEG file to write (default snapshot.jpg)

set -euo pipefail

FILENAME="${1:-snapshot.jpg}"

CONFIG_FILE="/etc/pimonitor.conf"
if [ ! -r "$CONFIG_FILE" ]; then
  echo "Configuration file not found: $CONFIG_FILE" >&2
  exit 1
fi

# Load configuration
eval $(grep -E '^[A-Z_]+=.*' "$CONFIG_FILE" | sed 's/^/export /')

# Determine destination directory
if [ -z "${RECORD_DIR}" ]; then
  USERNAME="${SUDO_USER:-${USER:-pi}}"
  RECORD_DIR="/home/${USERNAME}/pimonitor-recordings"
fi
mkdir -p "$RECORD_DIR"
FILEPATH="${RECORD_DIR}/${FILENAME}"

# Choose stream URL based on mode
if [ "$STREAM_MODE" = "MJPEG" ]; then
  URL="http://127.0.0.1:${HTTP_PORT}/stream"
else
  URL="rtsp://127.0.0.1:${RTSP_PORT}/stream"
fi

ffmpeg -y -i "$URL" -vframes 1 "$FILEPATH"

echo "Saved snapshot: $FILEPATH"