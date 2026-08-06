# PaperHid

Bluetooth **keyboard** and **mouse / touchpad** for [reMarkable Paper Pro](https://remarkable.com/).

Host: **Python 3.10+**. Tablet: Developer Mode + USB (`root@10.11.99.1`). Not affiliated with reMarkable.

## From zero (vanilla Paper Pro)

### 1. Tablet

1. **Settings → Security → enable Developer mode** (reboot if asked).
2. Note the **root password**: Settings → Help → Copyrights and licenses / About (SSH password).
3. Connect **USB** to your PC. Leave **Wi‑Fi on** if you want mouse support (first-time Entware download).

### 2. Host (once)

```bash
git clone https://github.com/martinopiaggi/paperhid.git
cd paperhid
pip install -r requirements.txt
```

```powershell
# Windows — password from the tablet
$env:PAPERHID_PASSWORD = "your-root-password"
```

```bash
# macOS / Linux
export PAPERHID_PASSWORD='your-root-password'
```

### 3. Install and pair

```bash
python cli.py detect
python cli.py install --all --save-password
python cli.py status
python cli.py scan
python cli.py pair --name YourKeyboardOrMouse
```

- **`--all`** installs the keyboard BT service, then the mouse daemon.
- If the tablet has no Python yet, install **auto-bootstraps Entware + python3** (needs tablet internet, a few minutes, ~80 MB free on `/home`).
- Keyboard-only: `python cli.py install --keyboard` (no Entware).
- Mouse-only after keyboard: `python cli.py install --pointer`.

### 4. Smoke check

```bash
python cli.py status          # keyboard + pointer should be active
python cli.py pointer test-tap   # synthetic click (pointer installed)
```

Wake the keyboard/mouse after deep sleep (press a key). After a tablet OTA, re-run `python cli.py install --all`.

## Common commands

```bash
python cli.py status
python cli.py uninstall --all
python cli.py bootstrap-python   # Entware + python3 only (if install failed offline)
python cli.py set-layout --layout it
python cli.py pointer test-tap
python cli.py pointer stock-ui           # remove optional cursor overlay
python cli.py pointer enable-settings-ui # Settings → Help controls (firmware 3.28.0.164)
python cli.py install-native-app         # optional AppLoad app (pairing UX on tablet)
```

**On-device UI:** prefer **Settings → Help** after `enable-settings-ui` (status, Bluetooth, keyboard reconnect, pointer knobs). Pairing stays on the host CLI for now (`scan` / `pair`). Optional **AppLoad** app via `install-native-app` if you want the older full on-tablet keyboard UI.

Password order: `--password` → `PAPERHID_PASSWORD` → legacy env aliases → saved config (`--save-password` after a successful connect).

Device paths such as `~/.paperwriter` and `paperpointer.service` are intentional; the product name is PaperHid.

## Optional cursor

Firmware **3.28.0.164** + XOVI — see [docs/cursor.md](docs/cursor.md). Save work first; xochitl restarts. Not required for keyboard or click-to-touch mouse.

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| SSH fails | USB cable, Developer mode, correct password, IP `10.11.99.1` |
| Pointer install: bootstrap failed | Turn on tablet Wi‑Fi; free space on `/home`; `python cli.py bootstrap-python` |
| Mouse not moving | `python cli.py status`; re-pair; wake HID after sleep |
| Keyboard dead after sleep | Press a key; `python cli.py status`; re-run `install --keyboard` if service missing |

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

[Unlicense](LICENSE.md) (public domain).

Keyboard Bluetooth work is based on [MoveWriter](https://github.com/vikboyechko/movewriter) by Vik Boyechko.
