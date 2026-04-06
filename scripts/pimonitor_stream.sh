#!/usr/bin/env bash
#
# Start the PiMonitor stream based on the current runtime configuration.

set -euo pipefail

CONFIG_FILE="${CONFIG_FILE:-/etc/pimonitor.conf}"

if [ ! -r "$CONFIG_FILE" ]; then
  echo "Configuration file not found: $CONFIG_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$CONFIG_FILE"
set +a

STREAM_MODE="${STREAM_MODE:-MJPEG}"
DEVICE="${DEVICE:-/dev/video0}"
RESOLUTION="${RESOLUTION:-1280x720}"
FPS="${FPS:-30}"
HTTP_PORT="${HTTP_PORT:-8080}"
RTSP_PORT="${RTSP_PORT:-8554}"
INPUT_FORMAT="${INPUT_FORMAT:-AUTO}"
ENABLE_HLS="${ENABLE_HLS:-0}"
HLS_SEGMENT_DURATION="${HLS_SEGMENT_DURATION:-2}"
ENABLE_AUTH="${ENABLE_AUTH:-0}"
AUTH_USERNAME="${AUTH_USERNAME:-}"
AUTH_PASSWORD="${AUTH_PASSWORD:-}"

W="${RESOLUTION%x*}"
H="${RESOLUTION#*x}"

list_formats() {
  v4l2-ctl --device="$DEVICE" --list-formats-ext 2>/dev/null || true
}

supports_format() {
  local format="$1"
  list_formats | grep -Fq "'${format}'"
}

pick_mjpeg_input_format() {
  local requested="${INPUT_FORMAT^^}"
  if [ "$requested" != "AUTO" ]; then
    printf '%s\n' "$requested"
    return 0
  fi
  for format in MJPG JPEG YUYV UYVY; do
    if supports_format "$format"; then
      printf '%s\n' "$format"
      return 0
    fi
  done
  printf 'MJPG\n'
}

pick_rtsp_input_format() {
  local requested="${INPUT_FORMAT^^}"
  if [ "$requested" != "AUTO" ]; then
    printf '%s\n' "$requested"
    return 0
  fi
  for format in H264 HEVC MJPG JPEG YUYV UYVY; do
    if supports_format "$format"; then
      printf '%s\n' "$format"
      return 0
    fi
  done
  printf 'MJPG\n'
}

apply_v4l2_settings() {
  local format="$1"
  v4l2-ctl --device="$DEVICE" --set-fmt-video="width=${W},height=${H},pixelformat=${format}" >/dev/null 2>&1 || true
  v4l2-ctl --device="$DEVICE" --set-parm="$FPS" >/dev/null 2>&1 || true
}

if [ "$STREAM_MODE" = "MJPEG" ]; then
  CAPTURE_FORMAT="$(pick_mjpeg_input_format)"
  apply_v4l2_settings "$CAPTURE_FORMAT"
  exec /usr/bin/ustreamer \
    --device="$DEVICE" \
    --format="$CAPTURE_FORMAT" \
    --resolution="$RESOLUTION" \
    --desired-fps="$FPS" \
    --host=0.0.0.0 \
    --port="$HTTP_PORT" \
    --buffers=4
fi

CAPTURE_FORMAT="$(pick_rtsp_input_format)"
apply_v4l2_settings "$CAPTURE_FORMAT"
RTSP_ARGS=(
  -I 0.0.0.0
  "-f${CAPTURE_FORMAT}"
  -W "$W"
  -H "$H"
  -F "$FPS"
  -P "$RTSP_PORT"
)

if [ "$ENABLE_HLS" = "1" ]; then
  RTSP_ARGS+=("-S${HLS_SEGMENT_DURATION}")
fi

if [ "$ENABLE_AUTH" = "1" ] && [ -n "$AUTH_USERNAME" ] && [ -n "$AUTH_PASSWORD" ]; then
  RTSP_ARGS+=(-U "${AUTH_USERNAME}:${AUTH_PASSWORD}")
fi

exec /usr/local/bin/v4l2rtspserver \
  "${RTSP_ARGS[@]}" \
  -u stream \
  "$DEVICE"
