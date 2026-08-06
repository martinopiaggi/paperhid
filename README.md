# PaperHid

Bluetooth **keyboard** and **mouse / touchpad** for the [reMarkable Paper Pro](https://remarkable.com/).

Host: **Python 3.10+**. Tablet: Developer Mode + USB (`root@10.11.99.1`). Not affiliated with reMarkable.

- **Keyboard** reconnect service (survives sleep / BT glitches)
- **Mouse / touchpad** → multitouch clicks via `paperpointerd`
- **Optional cursor** + **Settings → Help** controls (firmware **3.28.0.164** + XOVI)
- **First-time setup is host CLI only** — pair with `scan` / `pair`; day-to-day knobs live on the tablet

## Quick start

Requires a Paper Pro with Developer Mode, the tablet root password, and USB (Wi‑Fi on the tablet if you want mouse / Entware bootstrap).

```bash
git clone https://github.com/martinopiaggi/paperhid.git
cd paperhid
pip install -r requirements.txt
```

```powershell
# Windows
$env:PAPERHID_PASSWORD = "your-root-password"
```

```bash
# macOS / Linux
export PAPERHID_PASSWORD='your-root-password'
```

```bash
python cli.py detect
python cli.py install --all --save-password
python cli.py scan
python cli.py pair --name YourKeyboardOrMouse
python cli.py status
```

- **`--all`**: keyboard BT service, then mouse daemon.
- Missing tablet Python: install **auto-bootstraps Entware + python3** (tablet internet, a few minutes, ~80 MB free on `/home`).
- Keyboard-only: `python cli.py install --keyboard`. Mouse later: `python cli.py install --pointer`.

## Day-to-day

| Need | Where |
|------|--------|
| Pair a new keyboard / mouse | Host: `python cli.py scan` then `pair` |
| Status / BT on-off-restart / reconnect | Tablet: **Settings → Help** (after `enable-settings-ui`) |
| Language layout (US, **US Intl**, UK, DE, FR, IT, ES) | Tablet: **Settings → Help**, or host `set-layout` |
| US International accents (`'` then `e` → é) | Tablet: **US Intl**, or `set-layout --layout us_intl` |
| Other layouts (full list) | Host: `python cli.py set-layout --layout it` |
| Cursor overlay | Host: `pointer enable-cursor` / `stock-ui` (docs/cursor.md) |

```bash
python cli.py status
python cli.py set-layout --layout it
python cli.py repair-ui                    # Settings → Help (3.28.0.164 + XOVI); also recovery
python cli.py pointer enable-cursor        # optional visual cursor
python cli.py pointer test-tap
python cli.py uninstall --all
```

Password order: `--password` → `PAPERHID_PASSWORD` → legacy env aliases → saved config (`--save-password` after a successful connect).

On-device paths such as `~/.paperwriter` and `paperpointer.service` are intentional runtime locations; the product name is PaperHid.

## Optional cursor & Settings UI

Firmware **3.28.0.164** + XOVI — see [docs/cursor.md](docs/cursor.md). **Save work first** (xochitl restarts). Not required for keyboard or click-to-touch mouse.

Settings → Help is independent of the cursor: re-enabling the cursor does not remove the panel.

**Settings recovery (average user):** if Help is empty after a freeze or reboot, connect USB and run:

```bash
python cli.py repair-ui
```

Then open **Settings → Help** once. Details: [docs/cursor.md](docs/cursor.md#recovery-settings--help-missing-or-tablet-froze).

`python cli.py status` prints a `=== settings_ui ===` section; if `state: needs_repair`, run `repair-ui`.

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| SSH fails | USB, Developer mode, root password, IP `10.11.99.1` |
| Pointer bootstrap failed | Tablet Wi‑Fi; free `/home` space; `python cli.py bootstrap-python` |
| Mouse not moving | `python cli.py status`; re-pair; wake HID after sleep |
| Keyboard dead after sleep | Press a key; `status`; re-run `install --keyboard` if the unit is gone |
| Settings → Help has no PaperHid block | `python cli.py repair-ui` then open Help — [docs/cursor.md](docs/cursor.md#recovery-settings--help-missing-or-tablet-froze) |
| BT keyboard forces landscape | Default off. If you set `osk_suppress=1` in `~/.paperpointer/pointer.conf`, set `0` and restart pointer |

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

[Unlicense](LICENSE.md) (public domain).

Keyboard Bluetooth work is based on [MoveWriter](https://github.com/vikboyechko/movewriter) by Vik Boyechko.
