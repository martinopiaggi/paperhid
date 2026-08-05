<p align="center">
  <img src="images/movewriter-logo.png" alt="PaperHid" width="280">
</p>

<p align="center">
  <strong>PaperHid</strong> — Bluetooth keyboard <em>and</em> mouse/pointer for
  <strong>reMarkable Paper Pro</strong>
</p>

---

## Who is this for?

| I want… | Install |
|---------|---------|
| **Keyboard only** | `python cli.py install --keyboard` |
| **Mouse / touchpad only** | BT service + pointer (see [Mouse-only](#mouse--pointer-only-quickstart)) |
| **Keyboard + trackpad/mouse** | `python cli.py install --all` |

PaperHid is the **union** of [PaperWriter](https://github.com/martinopiaggi/paperwriter) (keyboard / NXP Bluetooth) and [PaperPointer](https://github.com/martinopiaggi/paperpointer) (pointer → multitouch + optional cursor). Source pins and layout: [PROVENANCE.md](PROVENANCE.md).

> **Not affiliated with reMarkable.** Requires Developer Mode. OTA updates can break units, binary layout offsets, and the firmware-locked cursor path. Save your work before enabling the cursor UI.

---

## Requirements

- reMarkable **Paper Pro** on USB (`root@10.11.99.1`), Developer Mode
- Python **3.10+** on the host (macOS or Windows)
- Host packages: `pip install -r requirements.txt`
- SSH password: Settings → Help → About → Copyrights and licenses  
  Prefer `PAPERWRITER_PASSWORD` (also accepts `PAPERPOINTER_PASSWORD` / `MOVEWRITER_PASSWORD`)

### Tablet Python for pointer (required for mouse path)

`paperpointerd` runs **on the tablet** and needs **`/opt/bin/python3`** (Entware).

On a **fresh tablet** this is **not** installed by `install --keyboard`. Pointer install **preflights** and **refuses to upload** until Python is present.

Supported ways to get tablet Python:

1. **rmpp-entware** for Paper Pro, then `opkg install python3` (home-backed `/home/root/.entware` bind-mounted on `/opt` is fine)
2. PaperHid / PaperWriter **native-app install path** (installs Entware + Python as a side effect; AppLoad may refuse on some OS versions — the BT service still works without the native app)
3. Any other method that leaves a working `/opt/bin/python3`

The keyboard BT service itself does **not** need Entware Python.

---

## Setup

```bash
git clone https://github.com/martinopiaggi/paperhid.git
cd paperhid
pip install -r requirements.txt
```

```powershell
# Password (PowerShell) — avoid history when possible
$env:PAPERWRITER_PASSWORD = "your_ssh_password"
```

### Keyboard-only quickstart

```bash
python cli.py detect
python cli.py install --keyboard
python cli.py status
python cli.py scan
python cli.py pair --name CLVX   # or --mac AA:BB:…
```

GUI (pairing / layout / service):

```bash
python main.py
```

### Mouse / pointer-only quickstart

Pointer needs (1) a working Bluetooth/HID path and (2) tablet Python.

```bash
# 0) Tablet Python must exist (see above). Preflight will block install otherwise.

# 1) Radio + pairing stack (same BT service as keyboard — layout not required)
python cli.py install --keyboard
python cli.py scan
python cli.py pair --mac AA:BB:CC:DD:EE:FF   # your mouse / combo device

# 2) Pointer daemon (preflights /opt/bin/python3, then uploads)
python cli.py install --pointer
python cli.py status
python -m paperpointer test-tap
```

Or both stacks in one go (keyboard first; on keyboard failure pointer is skipped;
on pointer failure keyboard remains — partial install):

```bash
python cli.py install --all
```

Optional on-screen cursor (firmware **3.28.0.164** only; needs XOVI modules — see PaperPointer docs):

```bash
python -m paperpointer enable-cursor
```

### Keyboard + trackpad (combo)

```bash
python cli.py install --all
python cli.py pair --name CLVX
python -m paperpointer status
```

---

## CLI surfaces

| Entry | Role |
|-------|------|
| `python cli.py …` | **Unified** PaperHid CLI (install modes, merged status/detect, keyboard + `pointer` subcommands) |
| `python -m paperpointer …` | Full pointer inventory (compat) |
| `python -m paperpointer.cli …` | Same as above |
| `python main.py` | Desktop GUI (keyboard / service / layout) |
| `python keyboard_cli.py …` | PaperWriter-shaped keyboard-only CLI (compat) |

### Password precedence

1. `--password`
2. `PAPERWRITER_PASSWORD`
3. `PAPERPOINTER_PASSWORD` then `MOVEWRITER_PASSWORD`
4. Host `~/.paperwriter/config.json` (if saved)

### Merged `status`

Prints `=== keyboard ===` and `=== pointer ===` with a shared state model:

| State | Meaning | Exit |
|-------|---------|------|
| `not_installed` | Component absent (optional) | 0 |
| `staged` | Residual home files only (e.g. after pointer uninstall keeps `~/.paperpointer`) | 0 |
| `active` | Unit present and running | 0 |
| `inactive` / `failed` | Unit present but not healthy | **1** |

Nonzero only when a **registered** unit is unhealthy or a probe fails — not merely because an optional component is missing.

### Pointer commands (compat + `cli.py pointer …`)

Full inventory (including legacy / diagnostic):

`detect`, `probe`, `bt-status`, `bt-recover`, `install`, `uninstall`, `status`, `restart`, `watch`, `reconnect`, `enable-fb`, `enable-cursor`, `enable-settings-ui`, `disable-settings-ui`, `settings-ui-check`, `fb-config`, `stock-ui`, `test-tap`, `cursor-rate`, `cursor-style`, `input-monitor`

Examples:

```bash
python -m paperpointer status
python -m paperpointer.cli probe
python cli.py pointer input-monitor 10
python cli.py pointer cursor-style win95
```

---

## How it fits together

```text
BT keyboard / mouse / touchpad
        │
 PaperWriter BT service (btnxpuart, uhid, reconnect)
        │
 /dev/input/eventN
        │
   ┌────┴────┐
 keys     paperpointerd
   │         ├── uinput multitouch → stock xochitl
   │         └── optional FIFO → XOVI QML cursor
 xochitl
```

- **Keyboard:** keys are consumed by xochitl; optional `libepaper.so` language patch.
- **Pointer:** relative mouse/touchpad is **not** a product cursor path in stock UI — PaperPointer injects multitouch and optionally paints a cursor.

SSH policy for combined commands: **sequential** connections — keyboard ops via `SSHClient`, then pointer ops via raw Paramiko (see `core/connection.py`).

---

## Tests

```bash
# Offline (no tablet)
python -m unittest discover -s tests -v

# Live Paper Pro (opt-in) — PowerShell
$env:PAPERWRITER_LIVE = "1"
$env:PAPERWRITER_PASSWORD = "..."
python -m unittest tests.test_live_paper_pro -v

# cmd.exe (if you prefer)
# set PAPERWRITER_LIVE=1
# set PAPERWRITER_PASSWORD=...
```

Live-device smoke for `install --all`, reconnect, rollback, and the firmware-locked cursor is a **release** gate, not required for offline merge.

---

## Status matrix (honest)

| Feature | Status | Notes |
|---------|--------|-------|
| Keyboard pair / reconnect | Working | NXP rules; wake keyboard after deep sleep |
| Layout patch | Working | Offset discovery; backup on device |
| Pointer → touch | Working | Needs tablet Python + HID node |
| On-screen cursor | Version-locked | `IMG_VERSION` **3.28.0.164** |
| Native AppLoad app | Experimental | May refuse on OS 3.28 AppLoad support table |

---

## Docs

- [PROVENANCE.md](PROVENANCE.md) — source pins
- [docs/FRAMEBUFFER_CURSOR.md](docs/FRAMEBUFFER_CURSOR.md) — cursor design
- [docs/NATIVE_APP_PAPER_PRO_PLAN.md](docs/NATIVE_APP_PAPER_PRO_PLAN.md) — native app notes
- Device-side scripts: `resources/` (BT), `device/` (pointer daemon)

## License

[The Unlicense](LICENSE.md) — public domain.

Upstream keyboard lineage: [MoveWriter](https://github.com/vikboyechko/movewriter).
