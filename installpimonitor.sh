#!/usr/bin/env bash
set -euo pipefail

# ===========================
# Settings you can tweak
# ===========================
RESOLUTION="${RESOLUTION:-1920x1080}"   # e.g. 1920x1080 or 1280x720
FPS="${FPS:-30}"                        # 25 or 30 are safe choices
HTTP_PORT="${HTTP_PORT:-8080}"          # ustreamer port
SITE_PORT="${SITE_PORT:-80}"            # nginx port for the simple webpage
DISABLE_WIFI_POWERSAVE="${DISABLE_WIFI_POWERSAVE:-1}"  # 1 = create service to disable Wi-Fi PS
# ===========================

echo "[0/10] (Optional) Remove old mjpg-streamer service if it exists..."
if systemctl list-unit-files | grep -q '^mjpg-streamer.service'; then
  systemctl disable --now mjpg-streamer || true
  rm -f /etc/systemd/system/mjpg-streamer.service
  systemctl daemon-reload
fi

# Figure out which user to run the service as (default 'pi' on RPi OS)
SERVICE_USER="${SUDO_USER:-pi}"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  SERVICE_USER="pi"
fi

echo "[1/10] Updating & installing packages..."
apt-get update
apt-get install -y v4l-utils ustreamer nginx

echo "[2/10] Detecting capture device node (stable by-id symlink preferred)..."
DEVICE=""
if [ -d /dev/v4l/by-id ]; then
  # pick the first *video-index0 symlink (the main stream)
  DEVICE="$(ls -1 /dev/v4l/by-id/*video-index0 2>/dev/null | head -n1 || true)"
fi
if [ -z "$DEVICE" ]; then
  # fallback to /dev/video0 if by-id isn't present yet
  DEVICE="/dev/video0"
fi

if [ ! -e "$DEVICE" ] && [ ! -L "$DEVICE" ]; then
  echo "ERROR: Device '$DEVICE' not found. Plug the capture card and rerun."
  exit 1
fi

echo "Using device: $DEVICE"
echo
echo "Supported formats for $DEVICE:"
v4l2-ctl --device="$DEVICE" --list-formats-ext || true

# Parse width/height from RESOLUTION "WxH"
W=${RESOLUTION%x*}
H=${RESOLUTION#*x}

echo "[3/10] Ensure user '$SERVICE_USER' can access video devices..."
usermod -aG video "$SERVICE_USER" || true

echo "[4/10] Create systemd service for ustreamer (pre-assert format + stable device path)..."
tee /etc/systemd/system/ustreamer.service >/dev/null <<EOF
[Unit]
Description=ustreamer for $DEVICE
After=network.target

[Service]
# Re-assert preferred format each start (non-fatal if unsupported)
ExecStartPre=/usr/bin/v4l2-ctl --device=$DEVICE --set-fmt-video=width=$W,height=$H,pixelformat=MJPG
ExecStartPre=/usr/bin/v4l2-ctl --device=$DEVICE --set-parm=$FPS

# ustreamer options:
# --format=MJPEG   : request MJPEG from the device (lower CPU/bandwidth on Pi Zero 2W)
# --desired-fps    : target FPS (device/USB may cap it)
# --buffers        : small buffer helps stability on low-power Pis
# --host 0.0.0.0   : listen on all interfaces
ExecStart=/usr/bin/ustreamer \
  --device=$DEVICE \
  --format=MJPEG \
  --resolution=${RESOLUTION} \
  --desired-fps=${FPS} \
  --host=0.0.0.0 \
  --port=${HTTP_PORT} \
  --buffers=4

Restart=always
User=$SERVICE_USER
Group=$SERVICE_USER
Environment=LD_PRELOAD=

[Install]
WantedBy=multi-user.target
EOF

echo "[5/10] Enable & start ustreamer..."
systemctl daemon-reload
systemctl enable --now ustreamer

echo "[6/10] Configure nginx: site on :$SITE_PORT with /stream redirect to :$HTTP_PORT (low-latency)"
# Site config (redirect, NOT proxy, to avoid buffering/latency)
tee /etc/nginx/sites-available/ustreamer >/dev/null <<NGX
server {
    listen ${SITE_PORT};
    server_name _;

    location / {
        root /var/www/html;
        index index.html;
    }

    # Redirect /stream so the browser hits ustreamer directly on :${HTTP_PORT}
    location /stream {
        return 302 http://\$host:${HTTP_PORT}/stream;
    }
}
NGX

# Enable the site, disable default
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/ustreamer /etc/nginx/sites-enabled/ustreamer
nginx -t
systemctl reload nginx

IP_ADDR="$(hostname -I | awk '{print $1}')"

echo "[7/10] Install a simple web page on nginx that embeds the stream via /stream..."
tee /var/www/html/index.html >/dev/null <<'HTML'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Pi Zero 2W Capture Feed</title>
  <style>
    :root { color-scheme: dark; }
    body { font-family: system-ui, sans-serif; margin: 0; background:#111; color:#eee;
           display:flex; min-height:100vh; align-items:center; justify-content:center; }
    .wrap { max-width: 96vw; }
    img { max-width: 96vw; max-height: 90vh; display:block; border-radius: 8px; }
    .hint { opacity:.7; margin-top: .75rem; font-size:.9rem; }
    a { color:#9cf; }
    details { margin-top: 1.25rem; }
    pre { white-space: pre-wrap; background:#222; color:#eee; padding:1rem; border-radius:8px; overflow:auto; }
  </style>
</head>
<body>
  <div class="wrap">
    <!-- Low-latency: /stream redirects to :8080/stream so the browser connects directly -->
    <img src="/stream" alt="Live stream" />
    <div class="hint">
      If nothing shows: ensure the HDMI source is active. Raw stream at <code>/stream</code>.
    </div>

    <hr style="margin:2rem 0; opacity:.3">

    <details>
      <summary style="cursor:pointer; font-weight:600;">Admin notes (change resolution/FPS & tips)</summary>
      <pre>
Change resolution/FPS later:

  sudo systemctl edit ustreamer
    # change numbers in the two ExecStartPre lines and the ExecStart line
  sudo systemctl daemon-reload && sudo systemctl restart ustreamer

Tip for Pi Zero 2W (Wi-Fi):
  If 1080p is choppy, use 1280x720 @ 30 or @ 25 fps.

Handy checks:
  systemctl status ustreamer --no-pager
  journalctl -u ustreamer -n 100 --no-pager

Stable device path (main stream):
  /dev/v4l/by-id/*video-index0

Raw stream (direct):
  http://&lt;pi-ip&gt;:8080/stream
      </pre>
    </details>
  </div>
</body>
</html>
HTML

# Fill in the <pi-ip> hint dynamically (optional cosmetic tweak)
sed -i "s#http://&lt;pi-ip&gt;:8080/stream#http://${IP_ADDR}:${HTTP_PORT}/stream#g" /var/www/html/index.html

echo "[8/10] (Optional) Disable Wi-Fi power save for stability..."
if [ "${DISABLE_WIFI_POWERSAVE}" = "1" ]; then
  tee /etc/systemd/system/wlan-nosave.service >/dev/null <<'UNIT'
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

echo "[9/10] Show current negotiated video settings..."
v4l2-ctl --device="$DEVICE" -V || true
v4l2-ctl --device="$DEVICE" --get-parm || true

echo "[10/10] Done ✅"
echo "Home page:   http://${IP_ADDR}:${SITE_PORT}/"
echo "Raw stream:  http://${IP_ADDR}:${HTTP_PORT}/stream"
echo
echo "Device path used: $DEVICE"
echo "Service user:     $SERVICE_USER"
