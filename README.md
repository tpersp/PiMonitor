# PiMonitor

This project turns a **Raspberry Pi Zero 2W** into a simple, low-latency HDMI capture streamer using a cheap USB capture dongle (e.g. Sandberg / MacroSilicon).

It sets up:

* **ustreamer** for MJPEG streaming from the USB capture stick
* **systemd service** to auto-start streaming
* **nginx** landing page on port 80 with embedded video
* **/stream** path redirecting directly to ustreamer (low latency)
* Optional **Wi-Fi power save disable** for stability
* Notes for changing resolution/FPS later

---

## Features

* Auto-detects capture card via stable `/dev/v4l/by-id/*video-index0` symlink (no random `/dev/videoX` issues)
* Default stream: **1920×1080 @ 30 fps MJPEG**
* Works well with Zero 2W + Wi-Fi (drop to **1280×720 @ 30 fps** if needed)
* Landing page with embedded player + collapsible **admin notes**
* Optional systemd unit to disable Wi-Fi powersave (helps streaming reliability)

---

## Installation

Clone/download this repo, copy the script to your Pi, then run:

```bash
sudo chmod +x installpimonitor.sh
sudo ./installpimonitor.sh
```

It will:

1. Install dependencies (`ustreamer`, `nginx`, `v4l-utils`)
2. Detect your capture card
3. Configure a `ustreamer.service`
4. Configure nginx to serve `/` (landing page) and `/stream` (redirect)
5. Install a landing page with embedded feed + notes
6. Optionally disable Wi-Fi power save

---

## Usage

* **Landing page:**

  ```
  http://<pi-ip>/
  ```
* **Raw MJPEG stream:**

  ```
  http://<pi-ip>:8080/stream
  ```

Default resolution: `1920x1080`, FPS: `30`

---

## Change Resolution / FPS

Edit the systemd unit:

```bash
sudo systemctl edit ustreamer
```

Change the numbers in the `ExecStartPre` and `ExecStart` lines.

Then reload + restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ustreamer
```

For Zero 2W Wi-Fi, **1280×720 @ 30 fps** or **@ 25 fps** is usually smoother.

---

## Handy Checks

```bash
systemctl status ustreamer --no-pager
journalctl -u ustreamer -n 100 --no-pager
```

---

## Notes

* Ensure capture dongle supports MJPEG (most MacroSilicon-based do).
* `/stream` path on nginx is a **redirect**, not a proxy, for lowest latency.
* Wi-Fi power save can be disabled automatically (see script).

---

## License

MIT
