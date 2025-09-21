#!/usr/bin/env python3

from inky.auto import auto
from PIL import Image, ImageFont, ImageDraw
import socket, os, psutil

# Init
inky = auto()
inky.set_border(inky.WHITE)

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "No network"
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

hostname = os.uname()[1]
ip = get_ip()
uptime = get_uptime()

# Stats
cpu = psutil.cpu_percent(interval=1)
mem = psutil.virtual_memory()
mem_used = mem.used // (1024 * 1024)
mem_total = mem.total // (1024 * 1024)

# Fonts
try:
    font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
except IOError:
    font_big = font_med = font_small = ImageFont.load_default()

img = Image.new("P", (inky.WIDTH, inky.HEIGHT))
draw = ImageDraw.Draw(img)

# Border (1 px black rectangle)
draw.rectangle([(0, 0), (inky.WIDTH-1, inky.HEIGHT-1)], outline=inky.BLACK, width=1)

# --- Layout ---

# IP centered, big
bbox = draw.textbbox((0, 0), ip, font=font_big)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (inky.WIDTH - w) // 2
y = 8
draw.text((x, y), ip, inky.BLACK, font=font_big)

# Hostname centered, smaller
bbox = draw.textbbox((0, 0), hostname, font=font_med)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (inky.WIDTH - w) // 2
y = y + 34
draw.text((x, y), hostname, inky.BLACK, font=font_med)

# Uptime centered below hostname
bbox = draw.textbbox((0, 0), uptime, font=font_small)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (inky.WIDTH - w) // 2
y = y + 26
draw.text((x, y), uptime, inky.BLACK, font=font_small)

# CPU left, MEM right (same line)
cpu_text = f"CPU: {cpu:.0f}%"
mem_text = f"MEM: {mem_used}/{mem_total}MB"

bbox = draw.textbbox((0, 0), cpu_text, font=font_small)
h = bbox[3] - bbox[1]
y = y + 26
draw.text((10, y), cpu_text, inky.BLACK, font=font_small)

bbox = draw.textbbox((0, 0), mem_text, font=font_small)
w = bbox[2] - bbox[0]
draw.text((inky.WIDTH - w - 10, y), mem_text, inky.BLACK, font=font_small)

inky.set_image(img)
inky.show()
