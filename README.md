# PaperHid

Bluetooth **keyboard** and **mouse / touchpad** for the [reMarkable Paper Pro](https://remarkable.com/).

> Pair once from your computer. After that, the tablet keeps the connection on its own, across sleep, reboots, and Bluetooth glitches. You never need to leave an app running on the host.

Host: **Python 3.10+**. Tablet: Developer Mode + USB. Not affiliated with reMarkable.

## What it does

PaperHid installs small services **on the tablet**. The host CLI is only a setup tool: connect over USB, install once, pair your keyboard or mouse, then close the app and unplug.

From then on the tablet owns Bluetooth:

* **Bluetooth keyboard**: layouts with automatic reconnect after sleep and BT dropouts
* **Optional mouse / touchpad**: pointer motion and clicks (requires [XOVI](https://github.com/asivery/xovi))
* **Optional Settings → Help** panel on device for status, BT controls, and keyboard layout switches without a computer (requires [XOVI](https://github.com/asivery/xovi))

Pair new devices from the host; day-to-day use only needs the Paper Pro itself.

## Quick start

Requires a Paper Pro with Developer Mode, the tablet root password, and USB (tablet Wi‑Fi if you want mouse / Entware bootstrap).

Root password is under **Settings → Help → Copyrights and licenses** (or your Developer Mode docs).

```bash
git clone https://github.com/martinopiaggi/paperhid.git
cd paperhid
pip install -r requirements.txt
```

```powershell
# Windows
$env:PAPERHID_PASSWORD = "your-root-ssh-password"
# optional — only if the tablet is not at the USB default
$env:PAPERHID_IP = "10.11.99.1"
```

```bash
# macOS / Linux
export PAPERHID_PASSWORD='your-root-ssh-password'
# optional — only if the tablet is not at the USB default
export PAPERHID_IP='10.11.99.1'
```

```bash
python cli.py detect
# install --all also bootstraps tablet Python if missing (Wi-Fi, ~2–5 min)
python cli.py install --all
```

- **`--all`**: keyboard BT service, then mouse daemon.
- Keyboard-only: `python cli.py install --keyboard`. Mouse later: `python cli.py install --pointer`.
- Password: set **`PAPERHID_PASSWORD`** (recommended), or pass `--password`.
- Tablet IP: default **`10.11.99.1`** (USB). Override with **`PAPERHID_IP`**, or `--ip` / `--host` (for example Wi‑Fi).

```bash
# Put the keyboard/mouse in pairing mode, then:
python cli.py scan
python cli.py pair --name "YourKeyboardName"
python cli.py pair --name "YourMouseName"
python cli.py status
python cli.py set-layout --layout it          # us, uk, de, fr, it, es, us_intl (international accents: ' + e → é)
```

Install once, pair, then unplug. **Keyboard and mouse can stay paired together**: pairing one does not remove the other.

```bash
python cli.py uninstall --all
```

## Optional cursor & Settings UI

**Not required** for keyboard or click-to-touch mouse.

Requirement: [XOVI](https://github.com/asivery/xovi) (third-party extension framework for reMarkable). QML compatibility is checked during installation with automatic rollback on startup failure. Follow the official install instructions, or use the brief steps below.

**macOS / Linux:**

```bash
# 1) Download the Paper Pro (aarch64) asset from the latest release
wget https://github.com/asivery/rm-xovi-extensions/releases/latest/download/xovi-aarch64.tar.gz
# 2) Copy it onto the tablet
scp xovi-aarch64.tar.gz root@10.11.99.1:/tmp/xovi.tar.gz
# 3) Extract on the tablet
ssh root@10.11.99.1 'tar -xzvf /tmp/xovi.tar.gz -C /home/root'
# 4) Activate PaperHid extensions and start XOVI (restarts the tablet UI)
ssh root@10.11.99.1 'cp -a /home/root/xovi/inactive-extensions/xovi-message-broker.so /home/root/xovi/extensions.d/ && cp -a /home/root/xovi/inactive-extensions/qt-command-executor.so /home/root/xovi/extensions.d/ && /home/root/xovi/start'
```

**Windows:**

```powershell
# 1) Download the Paper Pro (aarch64) release asset
Invoke-WebRequest -Uri "https://github.com/asivery/rm-xovi-extensions/releases/latest/download/xovi-aarch64.tar.gz" -OutFile "xovi-aarch64.tar.gz"
# 2) Copy it onto the tablet
scp xovi-aarch64.tar.gz root@10.11.99.1:/tmp/xovi.tar.gz
# 3) Extract on the tablet
ssh root@10.11.99.1 "tar -xzvf /tmp/xovi.tar.gz -C /home/root"
# 4) Activate PaperHid extensions and start XOVI (restarts the tablet UI)
ssh root@10.11.99.1 "cp -a /home/root/xovi/inactive-extensions/xovi-message-broker.so /home/root/xovi/extensions.d/ ; cp -a /home/root/xovi/inactive-extensions/qt-command-executor.so /home/root/xovi/extensions.d/ ; /home/root/xovi/start"
```

### Enable PaperHid UI or cursor (after XOVI is on the tablet)

```bash
python cli.py settings-ui                     # Settings → Help panel (needs XOVI)
python cli.py pointer enable-cursor           # optional on-screen crosshair (needs XOVI)
python cli.py pointer stock-ui                # remove cursor overlay only
```

`settings-ui` works for both **first enable** (after XOVI) and **re-enable** after a freeze, stock reboot, or empty Help.
Settings → Help is independent of the cursor: re-enabling the cursor does not remove the panel.

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| SSH fails | USB, Developer mode, root password, IP `10.11.99.1` (or set **`PAPERHID_IP`** / `--ip`) |
| `settings-ui`: no XOVI | Install XOVI (above), then `settings-ui` |
| Mouse not moving | `status`; re-pair; wake HID after sleep |
| Keyboard dead after sleep | Key press; `status`; re-run `install --keyboard` if needed |
| Help empty / freeze | Power-cycle if needed → USB (~30s) → `python cli.py settings-ui` → open **Settings → Help** |
| BT keyboard forces landscape | Default is off (`osk_suppress=0`). If you set `1` in `~/.paperpointer/pointer.conf` to hide OSK, set `0` and restart pointer |

## License

[Unlicense](LICENSE.md) (public domain).

Keyboard Bluetooth work is based on [MoveWriter](https://github.com/vikboyechko/movewriter) by Vik Boyechko.
