# Inky pHAT Optional Add-on

The Pimoroni Inky pHAT e-ink display can show a rotating snapshot of your PiMonitor system status.

## Prerequisites

1. Follow Pimoroni's official Inky pHAT setup guide to install the Inky Python library and enable SPI on your Raspberry Pi. The installer script is available at `https://get.pimoroni.com/inky`.
2. Install `psutil`, which the status script uses to read CPU and memory details:

   ```bash
   sudo apt-get install -y python3-psutil
   ```

## Install the status script

1. Ensure the Pimoroni examples directory exists:

   ```bash
   mkdir -p ~/Pimoroni/inky/examples
   ```

2. Point the example the service uses at the copy in this repository so it tracks future `git pull` updates:

   ```bash
   ln -sf /home/pi/PiMonitor/optional-features/inky-pHAT/show-info.py ~/Pimoroni/inky/examples/show-info.py
   ```

   If your environment does not permit symbolic links, copy the file instead:

   ```bash
   cp /home/pi/PiMonitor/optional-features/inky-pHAT/show-info.py ~/Pimoroni/inky/examples/show-info.py
   ```

   Using a copy works, but you will need to repeat the `cp` after pulling new changes.

3. Mark the repository version executable (the symlink inherits the same permission):

   ```bash
   chmod +x /home/pi/PiMonitor/optional-features/inky-pHAT/show-info.py
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

You can edit `show-info.py` to tweak the layout or add metrics (for example, disk usage or network statistics). Because the service uses a symlink into this repository, running `git pull` brings those updates onto the display automatically after the next timer run. After changing the file, call `sudo systemctl start inky-info.service` to test your changes immediately.
