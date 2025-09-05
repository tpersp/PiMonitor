#!/usr/bin/env bash
#
# Record the live PiMonitor stream for a given duration and save it as an MP4 file.
#
# Usage: pimonitor-record [DURATION] [FILENAME]
#   DURATION – recording length in seconds (default 10)
#   FILENAME – name of the output file (default record.mp4)

set -euo pipefail

DURATION="${1:-10}"
FILENAME="${2:-record.mp4}"

CONFIG_FILE="/etc/pimonitor.conf"

if [ ! -r "$CONFIG_FILE" ]; then
  echo "Configuration file not found: $CONFIG_FILE" >&2
  exit 1
fi

# Read configuration variables into the environment
eval $(grep -E '^[A-Z_]+=.*' "$CONFIG_FILE" | sed 's/^/export /')

# Determine recordings directory
if [ -z "${RECORD_DIR}" ]; then
  USERNAME="${SUDO_USER:-${USER:-pi}}"
  RECORD_DIR="/home/${USERNAME}/pimonitor-recordings"
fi
mkdir -p "$RECORD_DIR"
FILEPATH="${RECORD_DIR}/${FILENAME}"

# Choose ffmpeg command based on stream mode
if [ "$STREAM_MODE" = "MJPEG" ]; then
  URL="http://127.0.0.1:${HTTP_PORT}/stream"
  ffmpeg -y -i "$URL" -t "$DURATION" -c:v libx264 -preset veryfast "$FILEPATH"
else
  URL="rtsp://127.0.0.1:${RTSP_PORT}/stream"
  ffmpeg -y -i "$URL" -t "$DURATION" -c copy "$FILEPATH"
fi

echo "Saved recording: $FILEPATH"