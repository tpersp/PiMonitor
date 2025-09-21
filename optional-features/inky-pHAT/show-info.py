#!/usr/bin/env python3

from inky.auto import auto
from PIL import Image, ImageFont, ImageDraw
import socket, os, psutil, subprocess

# Init
inky = auto()
inky.set_border(inky.WHITE)

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = None
    finally:
        s.close()
    return ip

def get_uptime():
    with open("/proc/uptime", "r") as f:
        seconds = float(f.readline().split()[0])
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{d}d {h}h {m}m"

def get_ssid():
    try:
        out = subprocess.check_output(
            ["iwgetid", "-r"], stderr=subprocess.DEVNULL
        ).decode().strip()
        return out if out else None
    except Exception:
        return None

hostname = os.uname()[1]
ip = get_ip()
uptime = get_uptime()
ssid = get_ssid()

# Stats
cpu = psutil.cpu_percent(interval=1)
mem = psutil.virtual_memory()
mem_used = mem.used // (1024 * 1024)
mem_total = mem.total // (1024 * 1024)

# Fonts
try:
    font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
except IOError:
    font_big = font_small = ImageFont.load_default()

img = Image.new("P", (inky.WIDTH, inky.HEIGHT))
draw = ImageDraw.Draw(img)

# --- Layout ---

y = 8

# IP or big error message (centered)
if ip:
    top_text = ip
else:
    top_text = "No Network"

bbox = draw.textbbox((0, 0), top_text, font=font_big)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (inky.WIDTH - w) // 2
draw.text((x, y), top_text, inky.BLACK, font=font_big)
y += h + 6

# Hostname (left) + Uptime (right) in small font
draw.text((10, y), hostname, inky.BLACK, font=font_small)
bbox = draw.textbbox((0, 0), uptime, font=font_small)
w = bbox[2] - bbox[0]
draw.text((inky.WIDTH - w - 10, y), uptime, inky.BLACK, font=font_small)
y += 20

# SSID line (always show, small font, centered)
ssid_text = f"SSID: {ssid}" if ssid else "SSID: ????"
bbox = draw.textbbox((0, 0), ssid_text, font=font_small)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (inky.WIDTH - w) // 2
draw.text((x, y), ssid_text, inky.BLACK, font=font_small)

# CPU left, MEM right (bottom line, small font)
cpu_text = f"CPU: {cpu:.0f}%"
mem_text = f"MEM: {mem_used}/{mem_total}MB"

bbox = draw.textbbox((0, 0), cpu_text, font=font_small)
h = bbox[3] - bbox[1]
y_bottom = inky.HEIGHT - h - 8
draw.text((10, y_bottom), cpu_text, inky.BLACK, font=font_small)

bbox = draw.textbbox((0, 0), mem_text, font=font_small)
w = bbox[2] - bbox[0]
draw.text((inky.WIDTH - w - 10, y_bottom), mem_text, inky.BLACK, font=font_small)

inky.set_image(img)
inky.show()
