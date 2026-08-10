# PaperHid

Bluetooth **keyboard** and **mouse / touchpad** for the [reMarkable Paper Pro](https://remarkable.com/).

> Pair once from your computer. After that, the tablet keeps the connection on its own — across sleep, reboots, and Bluetooth glitches. You never need to leave an app running on the host.

Host: **Python 3.10+**. Tablet: Developer Mode + USB (`root@10.11.99.1`). Not affiliated with reMarkable.

## What it does

PaperHid installs small services **on the tablet**. The host CLI is only a setup tool: you connect over USB, install once, pair your keyboard or mouse, then close the app and unplug.

From then on the tablet owns Bluetooth:

* **Bluetooth keyboard** : keyboard layouts with automatic reconnect after sleep and BT dropouts
* **Optional mouse / touchpad** : pointer motion and clicks ([XOVI](https://github.com/asivery/xovi) requirement)
* **Optional Settings -> Help** panel on device for status, BT controls, and keyboard layout switches without a computer ([XOVI](https://github.com/asivery/xovi) requirement) 

Pair a new device with host otherwise normal use does not require a device apart the rmpp.

## Quick start

Requires a Paper Pro with Developer Mode, the tablet root password, and USB (Wi‑Fi on the tablet if you want mouse / Entware bootstrap).

Root password is under **Settings → Help → Copyrights and licenses** (or your Developer Mode docs).

```bash
git clone https://github.com/martinopiaggi/paperhid.git
cd paperhid
pip install -r requirements.txt
```

```powershell
# Windows
$env:PAPERHID_PASSWORD = "your-root-ssh-password"
```

```bash
# macOS / Linux
export PAPERHID_PASSWORD='your-root-ssh-password'
```

```bash
python cli.py detect
# install --all also bootstraps tablet Python if missing (Wi-Fi, ~2–5 min)
python cli.py install --all
```

- **`--all`**: keyboard BT service, then mouse daemon.
- Keyboard-only: `python cli.py install --keyboard`. Mouse later: `python cli.py install --pointer`.
- Password: set **`PAPERHID_PASSWORD`** (recommended), or pass `--password`.

```bash
# Put the keyboard/mouse in pairing mode, then:
python cli.py scan
python cli.py pair --name "YourKeyboardName"
python cli.py pair --name "YourMouseName"
python cli.py status
python cli.py set-layout --layout it          # us, uk, de, fr, it, es, us_intl supports international accents (' + e -> é )
```

Install once, pair, then unplug. **Keyboard and mouse can stay paired together** : pairing a mouse does not remove the keyboard (and vice versa).

```bash
python cli.py uninstall --all
```

## Optional cursor & Settings UI

**Not required** for keyboard or click-to-touch mouse is it possible to 

Requirements: firmware **3.28.0.164** + [XOVI](https://github.com/asivery/xovi) is a third-party extension framework for reMarkable tablets. 

Follow instructions to install KOVI from the official repository, otherwise here a brief steps:

```bash
# 1) Download aarch64 asset from the latest release, then:
wget
# 2) Copy onto the tablet 
scp xovi-aarch64.tar.gz root@10.11.99.1:/tmp/xovi.tar.gz
# 3)
ssh root@10.11.99.1 'tar -xzvf /tmp/xovi.tar.gz -C /home/root'
# 4)
ssh root@10.11.99.1 'cp -a /home/root/xovi/inactive-extensions/xovi-message-broker.so /home/root/xovi/extensions.d/ && cp -a /home/root/xovi/inactive-extensions/qt-command-executor.so /home/root/xovi/extensions.d/ && /home/root/xovi/start'
```

Or if on windows:

```powershell
# 1) Download the Paper Pro (aarch64) release asset
Invoke-WebRequest -Uri "https://github.com/asivery/rm-xovi-extensions/releases/latest/download/xovi-aarch64.tar.gz" -OutFile "xovi-aarch64.tar.gz"
# 2) Copy onto the tablet 
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

`settings-ui` is the normal command both for **first enable** (after XOVI) and for **re-enable** after a freeze, stock reboot, or empty Help. 
Settings → Help is independent of the cursor: re-enabling the cursor does not remove the panel.


## Troubleshooting

**If Help is empty** after a freeze or reboot, connect USB and run:

```bash
python cli.py settings-ui
```

Then open **Settings → Help** once. Details: [docs/cursor.md](docs/cursor.md#recovery-settings--help-missing-or-tablet-froze).


| Symptom | Fix |
|--------|-----|
| SSH fails | USB, Developer mode, root password, IP `10.11.99.1` |
| Host key changed (`REMOTE HOST IDENTIFICATION HAS CHANGED`) | `ssh-keygen -R 10.11.99.1` then reconnect |
| `scp: … xovi.tar.gz: No such file` | Use the real filename, e.g. `scp xovi-aarch64.tar.gz root@10.11.99.1:/tmp/xovi.tar.gz` |
| PowerShell `wget …` errors | Use `Invoke-WebRequest -Uri … -OutFile …` (Windows `wget` is not GNU wget) |
| `settings-ui`: XOVI not installed | Install XOVI first (Windows steps above), then `python cli.py settings-ui` |
| `settings-ui`: message-broker not active | Activate extensions (step 4 above), or re-run `settings-ui` after updating paperhid |
| Pointer bootstrap failed | Tablet Wi‑Fi; free `/home` space; `python cli.py bootstrap-python` |
| Mouse not moving | `python cli.py status`; re-pair; wake HID after sleep |
| Keyboard dead after sleep | Press a key; `status`; re-run `install --keyboard` if the unit is gone |
| Settings → Help has no PaperHid block | `python cli.py settings-ui` then open Help — [docs/cursor.md](docs/cursor.md#recovery-settings--help-missing-or-tablet-froze) |
| BT keyboard forces landscape | Default off. If you set `osk_suppress=1` in `~/.paperpointer/pointer.conf`, set `0` and restart pointer |

## License

[Unlicense](LICENSE.md) (public domain).

Keyboard Bluetooth work is based on [MoveWriter](https://github.com/vikboyechko/movewriter) by Vik Boyechko.
