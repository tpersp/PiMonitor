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

import os
import re
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
NGINX_SITE_PATH = os.environ.get('NGINX_SITE_PATH', '/etc/nginx/sites-available/pimonitor')
NGINX_AUTH_FILE = os.environ.get('NGINX_AUTH_FILE', '/etc/nginx/.pimonitor_htpasswd')
NGINX_RELOAD_COMMAND = os.environ.get('NGINX_RELOAD_COMMAND', 'reload')

# Serialise access to config/record operations
lock = Lock()

RESOLUTION_RE = re.compile(r'^\d+x\d+$')


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


def run_command(cmd: list[str], *, check: bool = True, capture_output: bool = False,
                text: bool = True, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    """Small subprocess wrapper."""
    kwargs = {
        'check': check,
        'text': text,
    }
    if capture_output:
        kwargs['capture_output'] = True
    else:
        kwargs['stdout'] = subprocess.DEVNULL
        kwargs['stderr'] = subprocess.DEVNULL
    if input_text is not None:
        kwargs['input'] = input_text
    return subprocess.run(cmd, **kwargs)


def validate_config_payload(data: dict[str, object], current_cfg: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Validate config updates and return normalized config plus any errors."""
    cfg = dict(current_cfg)
    errors: list[str] = []
    for raw_key, raw_value in data.items():
        key = str(raw_key)
        value = '' if raw_value is None else str(raw_value).strip()
        cfg[key] = value

    resolution = cfg.get('RESOLUTION', '')
    if resolution and not RESOLUTION_RE.match(resolution):
        errors.append('Resolution must look like WIDTHxHEIGHT, for example 1280x720.')

    for port_key in ('HTTP_PORT', 'RTSP_PORT', 'SITE_PORT'):
        port_value = cfg.get(port_key, '').strip()
        if not port_value:
            continue
        try:
            port_number = int(port_value)
        except ValueError:
            errors.append(f'{port_key} must be a number.')
            continue
        if port_number < 1 or port_number > 65535:
            errors.append(f'{port_key} must be between 1 and 65535.')

    fps_value = cfg.get('FPS', '').strip()
    if fps_value:
        try:
            fps = int(fps_value)
            if fps < 1 or fps > 120:
                errors.append('FPS must be between 1 and 120.')
        except ValueError:
            errors.append('FPS must be a number.')

    stream_mode = cfg.get('STREAM_MODE', 'MJPEG')
    if stream_mode not in ('MJPEG', 'H264_RTSP'):
        errors.append('STREAM_MODE must be MJPEG or H264_RTSP.')

    input_format = cfg.get('INPUT_FORMAT', 'AUTO').upper() or 'AUTO'
    cfg['INPUT_FORMAT'] = input_format
    if input_format not in ('AUTO', 'MJPG', 'JPEG', 'YUYV', 'UYVY', 'H264', 'HEVC'):
        errors.append('INPUT_FORMAT must be AUTO, MJPG, JPEG, YUYV, UYVY, H264, or HEVC.')

    enable_hls = cfg.get('ENABLE_HLS', '0')
    cfg['ENABLE_HLS'] = '1' if str(enable_hls).strip() in ('1', 'true', 'yes', 'on') else '0'
    hls_segment_duration = cfg.get('HLS_SEGMENT_DURATION', '2').strip() or '2'
    cfg['HLS_SEGMENT_DURATION'] = hls_segment_duration
    try:
        hls_duration = int(hls_segment_duration)
        if hls_duration < 1 or hls_duration > 30:
            errors.append('HLS_SEGMENT_DURATION must be between 1 and 30 seconds.')
    except ValueError:
        errors.append('HLS_SEGMENT_DURATION must be a number.')

    enable_auth = cfg.get('ENABLE_AUTH', '0')
    cfg['ENABLE_AUTH'] = '1' if str(enable_auth).strip() in ('1', 'true', 'yes', 'on') else '0'
    if cfg['ENABLE_AUTH'] == '1':
        if not cfg.get('AUTH_USERNAME', '').strip():
            errors.append('AUTH_USERNAME is required when authentication is enabled.')
        if not cfg.get('AUTH_PASSWORD', '').strip():
            errors.append('AUTH_PASSWORD is required when authentication is enabled.')

    device = cfg.get('DEVICE', '').strip()
    if device and not os.path.exists(device):
        errors.append(f'Device does not exist: {device}')

    return cfg, errors


def render_nginx_config(cfg: dict[str, str]) -> str:
    """Render nginx site config from the current PiMonitor configuration."""
    site_port = cfg.get('SITE_PORT', '80')
    config_port = cfg.get('CONFIG_PORT', str(APP_PORT))
    stream_mode = cfg.get('STREAM_MODE', 'MJPEG')
    auth_directives = '# Authentication disabled'
    if cfg.get('ENABLE_AUTH') == '1':
        auth_directives = (
            '    auth_basic "PiMonitor";\n'
            f'    auth_basic_user_file {NGINX_AUTH_FILE};'
        )

    if stream_mode == 'MJPEG':
        http_port = cfg.get('HTTP_PORT', '8080')
        stream_directives = (
            'proxy_http_version 1.1;\n'
            f'        proxy_pass http://127.0.0.1:{http_port}/stream;\n'
            '        proxy_buffering off;\n'
            '        proxy_request_buffering off;'
        )
        hls_directives = '    location /hls/ { return 404; }'
    else:
        stream_directives = f'return 302 rtsp://$host:{cfg.get("RTSP_PORT", "8554")}/stream;'
        if cfg.get('ENABLE_HLS') == '1':
            rtsp_port = cfg.get('RTSP_PORT', '8554')
            hls_directives = (
                '    location /hls/ {\n'
                f'        proxy_pass http://127.0.0.1:{rtsp_port}/;\n'
                '        proxy_set_header Host $host;\n'
                '        add_header Access-Control-Allow-Origin *;\n'
                '    }'
            )
        else:
            hls_directives = '    location /hls/ { return 404; }'

    return f"""server {{
    listen {site_port};
    server_name _;

    root /var/www/pimonitor;
    index index.html;

{auth_directives}

    location / {{
        try_files $uri $uri/ =404;
    }}

    location /stream {{
        {stream_directives}
    }}

{hls_directives}

    location /api/ {{
        proxy_pass http://127.0.0.1:{config_port}/api/;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
"""


def apply_nginx_config(cfg: dict[str, str]) -> tuple[bool, Optional[str]]:
    """Update auth and nginx config to match the runtime config."""
    try:
        if cfg.get('ENABLE_AUTH') == '1':
            run_command(
                ['htpasswd', '-cb', NGINX_AUTH_FILE, cfg.get('AUTH_USERNAME', ''), cfg.get('AUTH_PASSWORD', '')],
                check=True,
            )
        with open(NGINX_SITE_PATH, 'w', encoding='utf-8') as f:
            f.write(render_nginx_config(cfg))
        run_command(['nginx', '-t'], check=True, capture_output=True)
        run_command(['systemctl', NGINX_RELOAD_COMMAND, 'nginx'], check=True)
        return True, None
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or '').strip() if hasattr(exc, 'stderr') else ''
        return False, stderr or str(exc)
    except OSError as exc:
        return False, str(exc)


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


def list_wpa_cli_networks() -> list[dict[str, str]]:
    """Read known networks from wpa_cli."""
    try:
        result = run_command(
            ['wpa_cli', '-i', WPA_INTERFACE, 'list_networks'],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    networks: list[dict[str, str]] = []
    lines = result.stdout.splitlines()
    for line in lines[1:]:
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        networks.append({
            'id': parts[0].strip(),
            'ssid': parts[1].strip(),
            'bssid': parts[2].strip() if len(parts) > 2 else '',
            'flags': parts[3].strip() if len(parts) > 3 else '',
        })
    return networks


def connect_wifi_network(ssid: str) -> tuple[bool, str]:
    """Select and reconnect to an already-saved Wi-Fi network."""
    reconfigure_wifi()
    network_id: Optional[str] = None
    for network in list_wpa_cli_networks():
        if network.get('ssid') == ssid:
            network_id = network.get('id')
            break
    if not network_id:
        return False, f'No saved network found for SSID "{ssid}".'

    commands = [
        ['wpa_cli', '-i', WPA_INTERFACE, 'enable_network', network_id],
        ['wpa_cli', '-i', WPA_INTERFACE, 'select_network', network_id],
        ['wpa_cli', '-i', WPA_INTERFACE, 'reconnect'],
        ['wpa_cli', '-i', WPA_INTERFACE, 'save_config'],
    ]
    for cmd in commands:
        try:
            result = run_command(cmd, capture_output=True, check=True)
        except FileNotFoundError:
            return False, 'wpa_cli is not installed on this system.'
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or '').strip()
            stdout = (exc.stdout or '').strip()
            return False, stderr or stdout or 'Failed to connect to the selected network.'
        output = (result.stdout or '').strip()
        if output and 'FAIL' in output.upper():
            return False, output
    return True, 'Wi-Fi reconnection requested.'


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
        cfg, errors = validate_config_payload(data, cfg)
        if errors:
            return jsonify({"status": "error", "error": ' '.join(errors)}), 400
        save_config(cfg)
        nginx_ok, nginx_error = apply_nginx_config(cfg)
        if not nginx_ok:
            return jsonify({"status": "error", "error": f'Configuration saved but nginx update failed: {nginx_error}'}), 500
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
                f.write('\n' + block + '\n')
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500
        reconfigured = reconfigure_wifi()
        return jsonify({"status": "ok", "ssid": ssid, "reconfigured": reconfigured})


@app.route('/api/wifi/connect', methods=['POST'])
def api_wifi_connect():
    """Connect to one of the already-saved Wi-Fi networks."""
    with lock:
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        ssid = (data.get('ssid') or '').strip()
        if not ssid:
            return jsonify({"status": "error", "error": "SSID is required"}), 400
        connected, message = connect_wifi_network(ssid)
        if not connected:
            return jsonify({"status": "error", "error": message}), 400
        return jsonify({"status": "ok", "ssid": ssid, "message": message})

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
