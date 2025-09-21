# Inky pHAT Optional Add-on

The Pimoroni Inky pHAT e-ink display can show a rotating snapshot of your PiMonitor system status.

## Prerequisites

1. Follow Pimoroni's official Inky pHAT setup guide to install the Inky Python library and enable SPI on your Raspberry Pi. The installer script is available at `https://get.pimoroni.com/inky`.
2. Install `psutil`, which the status script uses to read CPU and memory details:

   ```bash
   sudo apt-get install -y python3-psutil
   ```

## Install the status script

1. Create `~/Pimoroni/inky/examples/show-info.py` and copy in the version from this repository (`optional-features/inky-pHAT/show-info.py`). The service created below expects the file to live in that location.
2. Make sure the script is executable:

   ```bash
   chmod +x ~/Pimoroni/inky/examples/show-info.py
   ```

## Systemd service and timer

Create `/etc/systemd/system/inky-info.service` with:

```
[Unit]
Description=Update Inky pHAT with system info

[Service]
Type=oneshot
ExecStart=/home/pi/.virtualenvs/pimoroni/bin/python /home/pi/Pimoroni/inky/examples/show-info.py
```

Create `/etc/systemd/system/inky-info.timer` with:

```
[Unit]
Description=Run Inky info update every 5 minutes

[Timer]
OnBootSec=30
OnUnitActiveSec=5min
Unit=inky-info.service

[Install]
WantedBy=timers.target
```

Reload systemd and enable the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now inky-info.timer
```

The timer runs the service once every five minutes, refreshing the data shown on the display. Adjust the interval in `OnUnitActiveSec` if you prefer a different update cadence.

## Updating the script

You can edit `show-info.py` to tweak the layout or add metrics (for example, disk usage or network statistics). After updating the file, call `sudo systemctl start inky-info.service` to test your changes immediately.
