#!/usr/bin/env python3
"""
Simple Flask API for PiMonitor configuration and recording.

This service reads and writes a flat key=value configuration file (default
/etc/pimonitor.conf) and exposes endpoints for retrieving and updating the
configuration, listing available video devices, and starting recordings or
taking snapshots.  It is designed to run as a system service and will
restart the streaming service whenever settings change.

Endpoints:

  GET  /api/config      → return current configuration as JSON
  POST /api/config      → update configuration (JSON body) and restart stream
  GET  /api/devices     → list available V4L2 capture devices
  POST /api/record      → record the live stream for a given duration
  POST /api/snapshot    → take a single JPEG snapshot

The recording and snapshot endpoints use ffmpeg under the hood.  For MJPEG
streams, the recording is transcoded to H.264 for reasonable file sizes;
for H.264 RTSP streams the raw video is simply remuxed.
"""

import json
import os
import subprocess
from threading import Lock
from flask import Flask, jsonify, request

# Flask application
app = Flask(__name__)

# Where to read/write the configuration
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/etc/pimonitor.conf')
# Name of the systemd unit responsible for streaming
SERVICE_NAME = os.environ.get('SERVICE_NAME', 'pimonitor-stream.service')
# Port this Flask app listens on
APP_PORT = int(os.environ.get('CONFIG_PORT', os.environ.get('PORT', '5000')))

# Serialise access to config/record operations
lock = Lock()


def load_config():
    """Load configuration from CONFIG_FILE as a dict."""
    cfg: dict[str, str] = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    cfg[key.strip()] = val.strip()
    return cfg


def save_config(cfg: dict[str, str]) -> None:
    """Write configuration dictionary back to CONFIG_FILE."""
    lines = [f"{k}={v}" for k, v in cfg.items()]
    with open(CONFIG_FILE, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def restart_stream():
    """Restart the streaming service via systemctl."""
    try:
        subprocess.run(['systemctl', 'restart', SERVICE_NAME], check=False)
    except Exception:
        pass


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """Get or update configuration."""
    with lock:
        cfg = load_config()
        if request.method == 'GET':
            return jsonify(cfg)
        # POST: update
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        # Only update keys present in the POST payload
        for k, v in data.items():
            cfg[k] = str(v)
        save_config(cfg)
        # Restart streaming service to apply changes
        restart_stream()
        return jsonify({"status": "ok", "config": cfg})


@app.route('/api/devices', methods=['GET'])
def api_devices():
    """List available V4L2 video devices."""
    devices: list[str] = []
    by_id = '/dev/v4l/by-id'
    if os.path.isdir(by_id):
        for name in sorted(os.listdir(by_id)):
            if 'video-index0' in name:
                devices.append(os.path.join(by_id, name))
    if not devices:
        # Fallback to enumerating /dev/video*
        for name in sorted(os.listdir('/dev')):
            if name.startswith('video'):
                devices.append('/dev/' + name)
    return jsonify({"devices": devices})


def run_ffmpeg(cmd: list[str]) -> bool:
    """Execute ffmpeg command and return True on success."""
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False


@app.route('/api/record', methods=['POST'])
def api_record():
    """Record the live stream for a specified duration and save to a file."""
    with lock:
        cfg = load_config()
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        duration = str(data.get('duration', 10))
        filename = data.get('filename', 'record.mp4')
        # Determine output directory
        user = os.environ.get('SUDO_USER') or os.environ.get('USER', 'pi')
        record_dir = cfg.get('RECORD_DIR', f'/home/{user}/pimonitor-recordings')
        os.makedirs(record_dir, exist_ok=True)
        filepath = os.path.join(record_dir, filename)
        # Determine stream URL based on current mode
        mode = cfg.get('STREAM_MODE', 'MJPEG')
        if mode == 'MJPEG':
            http_port = cfg.get('HTTP_PORT', '8080')
            url = f'http://127.0.0.1:{http_port}/stream'
            cmd = ['ffmpeg', '-y', '-i', url, '-t', duration,
                   '-c:v', 'libx264', '-preset', 'veryfast', filepath]
        else:
            rtsp_port = cfg.get('RTSP_PORT', '8554')
            url = f'rtsp://127.0.0.1:{rtsp_port}/stream'
            cmd = ['ffmpeg', '-y', '-i', url, '-t', duration, '-c', 'copy', filepath]
        success = run_ffmpeg(cmd)
        status = 'ok' if success else 'error'
        return jsonify({"status": status, "file": filepath})


@app.route('/api/snapshot', methods=['POST'])
def api_snapshot():
    """Capture a single frame from the live stream and save to a JPEG."""
    with lock:
        cfg = load_config()
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        filename = data.get('filename', 'snapshot.jpg')
        user = os.environ.get('SUDO_USER') or os.environ.get('USER', 'pi')
        record_dir = cfg.get('RECORD_DIR', f'/home/{user}/pimonitor-recordings')
        os.makedirs(record_dir, exist_ok=True)
        filepath = os.path.join(record_dir, filename)
        mode = cfg.get('STREAM_MODE', 'MJPEG')
        if mode == 'MJPEG':
            http_port = cfg.get('HTTP_PORT', '8080')
            url = f'http://127.0.0.1:{http_port}/stream'
        else:
            rtsp_port = cfg.get('RTSP_PORT', '8554')
            url = f'rtsp://127.0.0.1:{rtsp_port}/stream'
        cmd = ['ffmpeg', '-y', '-i', url, '-vframes', '1', filepath]
        success = run_ffmpeg(cmd)
        status = 'ok' if success else 'error'
        return jsonify({"status": status, "file": filepath})


if __name__ == '__main__':
    # Run the Flask app; enable threaded mode to handle concurrent requests
    app.run(host='0.0.0.0', port=APP_PORT, threaded=True)