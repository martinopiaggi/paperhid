# PaperHid

Bluetooth **keyboard** and **mouse / touchpad** for the [reMarkable Paper Pro](https://remarkable.com/).

> Pair once from your computer. After that, the tablet keeps the connection on its own — across sleep, reboots, and Bluetooth glitches. You never need to leave an app running on the host.

Host: **Python 3.10+**. Tablet: Developer Mode + USB (`root@10.11.99.1`). Not affiliated with reMarkable.

## What it does

PaperHid installs small services **on the tablet**. The host CLI is only a setup tool: you connect over USB, install once, pair your keyboard or mouse, then close the app and unplug.

From then on the tablet owns Bluetooth:

* **Keyboard** — HID layouts with automatic reconnect after sleep and BT dropouts
* **Mouse / touchpad** — pointer motion and multitouch clicks via `paperpointerd`
* **Optional cursor** and **Settings → Help** panel (firmware **3.28.0.164** + XOVI) for status, BT controls, and layout switches without a computer

Pair a new device or change advanced options only when you need to; normal use does not require the host.

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
python cli.py install --all
python cli.py scan
python cli.py pair --name YourKeyboard
python cli.py pair --name YourMouse
python cli.py status
```

Install the tablet services once, pair your devices, then unplug. **Keyboard and mouse can stay paired together** — pairing a mouse no longer removes or overwrites the keyboard (and vice versa). The keyboard reconnect service only tracks the keyboard MAC; the pointer daemon uses any connected mouse/touchpad HID.

- **`--all`**: keyboard BT service, then mouse daemon.
- Missing tablet Python: install **auto-bootstraps Entware + python3** (tablet internet, a few minutes, ~80 MB free on `/home`).
- Keyboard-only: `python cli.py install --keyboard`. Mouse later: `python cli.py install --pointer`.

Password: set **`PAPERHID_PASSWORD`** (recommended), or pass `--password`. Legacy env aliases still work.

## Commands

```bash
python cli.py status
python cli.py scan
python cli.py pair --name YourDevice
python cli.py set-layout --layout it          # us, us_intl, uk, de, fr, it, es, …
python cli.py settings-ui                     # Settings → Help (first-time or re-enable; 3.28.0.164 + XOVI)
python cli.py pointer enable-cursor           # optional visual cursor
python cli.py pointer test-tap
python cli.py uninstall --all
```

| Task | How |
|------|-----|
| Pair another keyboard / mouse | Host: `scan` then `pair` |
| Status, BT restart, reconnect | Tablet: **Settings → Help** (after `settings-ui`) |
| Layout (US, **US Intl**, UK, DE, FR, IT, ES, …) | **Settings → Help**, or host `set-layout` |
| US International accents (`'` then `e` → é) | **US Intl**, or `set-layout --layout us_intl` |
| Cursor overlay | `pointer enable-cursor` / `stock-ui` — [docs/cursor.md](docs/cursor.md) |

On-device paths such as `~/.paperwriter` and `paperpointer.service` are intentional runtime locations; the product name is PaperHid.

## Optional cursor & Settings UI

Firmware **3.28.0.164** + [XOVI](https://github.com/asivery/xovi) — see [docs/cursor.md](docs/cursor.md). **Save work first** (xochitl restarts). **Not required** for keyboard or click-to-touch mouse.

### XOVI (optional dependency)

[XOVI](https://github.com/asivery/xovi) is a third-party extension framework for reMarkable tablets. PaperHid uses it only for the on-screen cursor and the **Settings → Help** panel. Keyboard and mouse install via `cli.py install` do **not** install or need XOVI.

| | |
|--|--|
| Path on tablet | `/home/root/xovi` |
| Install / extensions | [asivery/rm-xovi-extensions](https://github.com/asivery/rm-xovi-extensions) (Paper Pro: **aarch64** release) |
| After install | `xovi/start` over SSH (XOVI is tethered; reboot returns to stock UI until started again) |
| Factory reset | Wipes `/home` — reinstall XOVI if you want cursor / Help again |

**Paper Pro needs the `aarch64` archive** (not arm32). GitHub names the file `xovi-aarch64.tar.gz`; you can keep that name or rename it to `xovi.tar.gz` — either works as long as `scp` and `tar` use the same path.

**Tablet paths are Linux paths** — always use `/home/root`, never `C:\home\root`.

Stock XOVI leaves some extensions under `inactive-extensions/`. PaperHid needs these **active** (in `extensions.d/`):

- `qt-resource-rebuilder.so` (usually already active)
- `xovi-message-broker.so`
- `qt-command-executor.so`

`python cli.py settings-ui` / `enable-cursor` will activate the last two if they are only under `inactive-extensions/`.

#### Install XOVI on Windows (PowerShell)

USB to the tablet, Developer Mode on, root password ready. From the folder that contains the downloaded archive (e.g. this repo):

```powershell
# 1) Download the Paper Pro (aarch64) release asset from:
#    https://github.com/asivery/rm-xovi-extensions/releases/latest
#    Pick the file named like xovi-aarch64.tar.gz (browser download is fine).
#    Or in PowerShell (do not use wget --no-check-certificate; wget is not GNU wget):
Invoke-WebRequest -Uri "https://github.com/asivery/rm-xovi-extensions/releases/latest/download/xovi-aarch64.tar.gz" -OutFile "xovi-aarch64.tar.gz"

# 2) Copy onto the tablet (use the real local filename)
scp xovi-aarch64.tar.gz root@10.11.99.1:/tmp/xovi.tar.gz

# 3) Extract on the tablet — path must be /home/root (Linux), NOT C:\home\root
ssh root@10.11.99.1 "tar -xzvf /tmp/xovi.tar.gz -C /home/root"

# 4) Activate PaperHid extensions and start XOVI (restarts the tablet UI — save work first)
ssh root@10.11.99.1 "cp -a /home/root/xovi/inactive-extensions/xovi-message-broker.so /home/root/xovi/extensions.d/ ; cp -a /home/root/xovi/inactive-extensions/qt-command-executor.so /home/root/xovi/extensions.d/ ; /home/root/xovi/start"
```

If `scp`/`ssh` fail with **REMOTE HOST IDENTIFICATION HAS CHANGED**, the tablet was re-flashed or reinstalled. Remove the old key, then retry:

```powershell
ssh-keygen -R 10.11.99.1
```

If OpenSSH is missing, install **OpenSSH Client** (Windows Optional Features) or use WSL and run the bash steps below.

#### Install XOVI on macOS / Linux

```bash
# Download aarch64 asset from the latest release, then:
scp xovi-aarch64.tar.gz root@10.11.99.1:/tmp/xovi.tar.gz
ssh root@10.11.99.1 'tar -xzvf /tmp/xovi.tar.gz -C /home/root'
ssh root@10.11.99.1 'cp -a /home/root/xovi/inactive-extensions/xovi-message-broker.so /home/root/xovi/extensions.d/ && cp -a /home/root/xovi/inactive-extensions/qt-command-executor.so /home/root/xovi/extensions.d/ && /home/root/xovi/start'
```

Official source: [rm-xovi-extensions install notes](https://github.com/asivery/rm-xovi-extensions#to-install-xovi).  
Or with [Vellum](https://github.com/asivery/rm-xovi-extensions#with-vellum): `vellum add xovi`. PaperHid’s cursor / Help need `qt-resource-rebuilder`, `qt-command-executor`, and `xovi-message-broker` (see [docs/cursor.md](docs/cursor.md)).

#### Enable PaperHid UI (after XOVI is on the tablet)

From the paperhid repo (with `PAPERHID_PASSWORD` set):

```powershell
python cli.py settings-ui                 # Settings → Help (first-time or re-enable; activates broker/executor if needed)
python cli.py pointer enable-cursor       # optional visual cursor
```

`settings-ui` is the normal command both for **first enable** (after XOVI) and for **re-enable** after a freeze, stock reboot, or empty Help. `repair-ui` is the same command (alias).

Common errors:

| Message | Meaning |
|---------|---------|
| `XOVI is not installed at /home/root/xovi` | Extract failed or wrong `-C` path (use `/home/root`, not `C:\home\root`) |
| `xovi-message-broker.so is not active` | Extensions still inactive — run step 4 above, or re-run `settings-ui` with a current paperhid (auto-activates) |

Settings → Help is independent of the cursor: re-enabling the cursor does not remove the panel.

**If Help is empty** after a freeze or reboot, connect USB and run:

```bash
python cli.py settings-ui
```

Then open **Settings → Help** once. Details: [docs/cursor.md](docs/cursor.md#recovery-settings--help-missing-or-tablet-froze).

`python cli.py status` prints a `=== settings_ui ===` section; if it needs re-enable, run `settings-ui`.

## Troubleshooting

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

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

[Unlicense](LICENSE.md) (public domain).

Keyboard Bluetooth work is based on [MoveWriter](https://github.com/vikboyechko/movewriter) by Vik Boyechko.
