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
from typing import Optional
from flask import Flask, jsonify, request

# Flask application
app = Flask(__name__)

# Where to read/write the configuration
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/etc/pimonitor.conf')
# Name of the systemd unit responsible for streaming
SERVICE_NAME = os.environ.get('SERVICE_NAME', 'pimonitor-stream.service')
# Port this Flask app listens on
APP_PORT = int(os.environ.get('CONFIG_PORT', os.environ.get('PORT', '5000')))

WPA_SUPPLICANT_CONF = os.environ.get('WPA_SUPPLICANT_CONF', '/etc/wpa_supplicant/wpa_supplicant.conf')
WPA_INTERFACE = os.environ.get('WPA_INTERFACE', 'wlan0')

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


def wifi_network_exists(ssid: str) -> bool:
    """Return True if the SSID already exists in wpa_supplicant."""
    if not os.path.exists(WPA_SUPPLICANT_CONF):
        return False
    try:
        with open(WPA_SUPPLICANT_CONF, 'r', encoding='utf-8') as f:
            contents = f.read()
    except OSError:
        return False
    needle = f'ssid="{ssid}"'
    return needle in contents


def generate_network_block(ssid: str, psk: Optional[str], hidden: bool) -> str:
    """Build a wpa_supplicant network block."""
    lines: list[str] = ['network={', f'    ssid="{ssid}"']
    if hidden:
        lines.append('    scan_ssid=1')
    if psk:
        block_lines: list[str] = []
        try:
            result = subprocess.run(
                ['wpa_passphrase', ssid],
                input=f'{psk}\n',
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('network='):
                    continue
                block_lines.append(line)
        except Exception:
            block_lines = [f'psk="{psk}"']
        for line in block_lines:
            lines.append(f'    {line}')
    else:
        lines.append('    key_mgmt=NONE')
    lines.append('}')
    return '\n'.join(lines)


def reconfigure_wifi() -> bool:
    """Reload wpa_supplicant so new networks are picked up."""
    commands = [
        ['wpa_cli', '-i', WPA_INTERFACE, 'reconfigure'],
        ['systemctl', 'reload', f'wpa_supplicant@{WPA_INTERFACE}.service'],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError:
            continue
    return False


def list_wifi_networks() -> list[dict[str, object]]:
    """Return saved networks from wpa_supplicant."""
    networks: list[dict[str, object]] = []
    if not os.path.exists(WPA_SUPPLICANT_CONF):
        return networks
    try:
        with open(WPA_SUPPLICANT_CONF, 'r', encoding='utf-8') as f:
            block: Optional[dict[str, object]] = None
            for raw in f:
                line = raw.strip()
                if line.startswith('network={'):
                    block = {'hidden': False, 'security': 'unknown'}
                    continue
                if block is None:
                    continue
                if line == '}':
                    if 'ssid' in block:
                        block.setdefault('security', 'unknown')
                        block['hidden'] = bool(block.get('hidden'))
                        networks.append(block)
                    block = None
                    continue
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                value = value.strip('\"')
                if key == 'ssid':
                    block['ssid'] = value
                elif key == 'scan_ssid':
                    block['hidden'] = value == '1'
                elif key == 'key_mgmt':
                    block['security'] = 'open' if value == 'NONE' else value
                elif key == 'priority':
                    try:
                        block['priority'] = int(value)
                    except ValueError:
                        block['priority'] = value
                elif key == 'disabled':
                    block['disabled'] = value
    except OSError:
        return networks
    return networks


def current_wifi_ssid() -> Optional[str]:
    """Return the SSID of the active connection, if any."""
    try:
        result = subprocess.run(
            ['wpa_cli', '-i', WPA_INTERFACE, 'status'],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    for line in result.stdout.splitlines():
        if line.startswith('ssid='):
            ssid = line.split('=', 1)[1].strip()
            return ssid or None
    return None


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


@app.route('/api/wifi', methods=['GET', 'POST'])
def api_wifi():
    """List or add Wi-Fi networks."""
    if request.method == 'GET':
        with lock:
            networks = list_wifi_networks()
        current = current_wifi_ssid()
        for entry in networks:
            entry['hidden'] = bool(entry.get('hidden', False))
            entry['security'] = entry.get('security') or 'unknown'
            entry['is_current'] = bool(current and entry.get('ssid') == current)
            if 'disabled' in entry:
                entry['disabled'] = str(entry['disabled'])
        return jsonify({"status": "ok", "networks": networks, "current": current})
    with lock:
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        ssid = (data.get('ssid') or '').strip()
        psk = data.get('psk')
        hidden_value = data.get('hidden', False)
        if isinstance(hidden_value, str):
            hidden = hidden_value.lower() in ('1', 'true', 'yes', 'on')
        else:
            hidden = bool(hidden_value)
        if not ssid:
            return jsonify({"status": "error", "error": "SSID is required"}), 400
        if wifi_network_exists(ssid):
            return jsonify({"status": "exists", "ssid": ssid})
        if isinstance(psk, str):
            psk = psk.strip()
        if not psk:
            psk_value: Optional[str] = None
        else:
            psk_value = psk
        block = generate_network_block(ssid, psk_value, hidden)
        try:
            with open(WPA_SUPPLICANT_CONF, 'a', encoding='utf-8') as f:
                f.write('
' + block + '
')
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500
        reconfigured = reconfigure_wifi()
        return jsonify({"status": "ok", "ssid": ssid, "reconfigured": reconfigured})

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
