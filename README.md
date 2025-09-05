# PiMonitor v2

PiMonitor transforms a Raspberry Pi Zero 2W (or other Raspberry Pi) into a flexible HDMI capture and streaming appliance.  
It can serve a live preview page, expose an MJPEG or H.264 RTSP/HLS stream, protect the feed with basic authentication, and even provide a simple web‑based configuration and recording interface.

The original version of PiMonitor consisted of a single shell script that installed **uStreamer** and served an MJPEG stream via nginx.  
This rewrite keeps the simple one‑command installation but adds a number of useful features while preserving backward compatibility.

## Highlights

* **Multiple streaming modes:** Use lightweight MJPEG (default) for maximum compatibility or H.264 for lower bandwidth.  
  H.264 is served via an RTSP server and can optionally expose HLS/DASH for browser playback.
* **Multi‑device selection:** Automatically detects all V4L2 video devices (USB capture sticks, webcams, etc.) and lets you pick which one to stream.
* **HTTP authentication:** Optionally protect the landing page and stream with HTTP basic auth.  
  When disabled (the default) the stream remains public.
* **Recording & screenshots:** Trigger recordings or take a snapshot from the live stream directly from the web UI.  Recordings are saved as MP4 files and snapshots as JPEG images.
* **Configuration web UI:** A minimal interface served from the Pi allows you to change resolution, frame rate, streaming mode, and authentication settings without editing systemd units.  Changes are applied immediately.
* **One‑command installation:** Still a single script (`installpimonitor.sh`) that installs dependencies, builds optional components, creates systemd units and nginx configuration, and installs the web files.

## Installation

Clone or download this repository, copy the `PiMonitor` folder to your Pi and run the installer with `sudo`:

```bash
cd PiMonitor
sudo chmod +x installpimonitor.sh
sudo ./installpimonitor.sh
```

The script will:

1. Install dependencies (uStreamer, nginx, ffmpeg, Python Flask and build tools).
2. Detect all connected capture devices and pick one (or the one you specify).
3. Build and install **v4l2rtspserver** if H.264 streaming is selected.
4. Create a unified systemd service (`pimonitor‑stream.service`) that runs either uStreamer (for MJPEG) or v4l2rtspserver (for H.264).
5. Create a small Flask API server (`pimonitor-api.service`) used by the configuration UI for updating settings and triggering recordings or snapshots.
6. Configure nginx to serve the landing page, configuration page and proxy API requests.  Optional HTTP basic auth is enabled if configured.
7. Optionally disable Wi‑Fi power saving for improved streaming stability.

When finished, the installer prints the URLs for the landing page and the raw stream.

## Usage

* **Landing page:** `http://<pi-ip>/` – Displays the live stream (MJPEG or H.264) and provides links to the configuration page.
* **Configuration page:** `http://<pi-ip>/config/` – Adjust resolution, FPS, streaming mode, device, and authentication credentials.  You can also trigger recordings and snapshots here.
* **Raw stream:**
  * MJPEG: `http://<pi-ip>:<HTTP_PORT>/stream`
  * H.264 RTSP: `rtsp://<pi-ip>:<RTSP_PORT>/stream`
  * HLS (if enabled with `-S`): `http://<pi-ip>:<RTSP_PORT>/unicast.m3u8` (can be played in a modern browser using hls.js)

## Changing Settings

### Via Web UI

The recommended way to change resolution, FPS, streaming mode or authentication is via the **Configuration** page.  
The UI talks to a small API service running locally on the Pi and applies your changes automatically by editing the config file and restarting the streaming service.

### Via Environment Variables

If you prefer automation or scripting, most options can be overridden when running the installer:

* `RESOLUTION` – Set video resolution (e.g. `1920x1080`, default `1280x720`).
* `FPS` – Set frame rate (e.g. `30`, default `30`).
* `STREAM_MODE` – `MJPEG` or `H264_RTSP`.  MJPEG uses uStreamer over HTTP; H264_RTSP uses v4l2rtspserver and provides a lower bandwidth H.264 stream.
* `DEVICE_INDEX` – Zero‑based index of the detected capture device to use (default `0`).
* `HTTP_PORT` – Port used by uStreamer for MJPEG (default `8080`).
* `RTSP_PORT` – Port used by v4l2rtspserver for RTSP/HLS (default `8554`).
* `SITE_PORT` – Port where nginx serves the web UI (default `80`).
* `CONFIG_PORT` – Port used by the Flask API server (default `5000`).
* `ENABLE_AUTH` – Set to `1` to enable HTTP basic authentication (default `0`).
* `AUTH_USERNAME` / `AUTH_PASSWORD` – Credentials used when `ENABLE_AUTH=1` (default `admin`/`password`).
* `RECORD_DIR` – Directory where recordings and snapshots are stored (defaults to `$HOME/pimonitor-recordings`).
* `DISABLE_WIFI_POWERSAVE` – Set to `1` to disable Wi‑Fi power saving (default `1`).

### Manually Editing the Config File

All runtime settings are stored in `/etc/pimonitor.conf` as simple `KEY=value` pairs.  
You can edit this file directly and then run:

```bash
sudo systemctl restart pimonitor-stream.service
sudo systemctl restart pimonitor-api.service
```

to apply your changes.

## Recording and Screenshots

PiMonitor includes helper endpoints to record the live stream or take a snapshot without leaving the browser:

* **Record:** Click *Record* in the configuration page, enter a duration and filename, and an MP4 file will be saved under the recordings directory.
* **Snapshot:** Click *Snapshot* in the configuration page to capture a single frame as a JPEG.

You can also call the helper scripts directly from the command line:

```bash
./scripts/record_stream.sh 10 myrecord.mp4  # record 10 seconds
./scripts/snapshot.sh snapshot.jpg          # take a JPEG snapshot
```

## Authentication

To secure your feed, set `ENABLE_AUTH=1` during installation or enable it later via the configuration page.  
PiMonitor uses nginx's built‑in basic authentication.  The username and password you choose are stored hashed in `/etc/nginx/.pimonitor_htpasswd`.

When authentication is enabled, both the landing page and the raw stream(s) require credentials.  If you disable authentication, the stream is publicly accessible.

## Limitations & Notes

* H.264 streaming relies on the [`v4l2rtspserver`](https://github.com/mpromonet/v4l2rtspserver) project.  Not all capture devices output H.264 natively; in those cases `v4l2rtspserver` will use the raw frames and encode them on the Pi, which may increase CPU usage.  When no H.264 support is available, consider staying with MJPEG.
* The default web UI embeds MJPEG streams directly using an `<img>` element.  For H.264/RTSP, browsers generally cannot play RTSP directly; you can either use an external player (e.g. VLC) or enable HLS mode by adding `-S` to the v4l2rtspserver arguments via the configuration file.
* Recording and snapshot features use `ffmpeg` internally.  Long recordings may consume significant disk space, so ensure your Pi has sufficient storage.

## License

MIT