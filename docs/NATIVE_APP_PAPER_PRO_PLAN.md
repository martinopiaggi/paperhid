# Plan: On-device PaperWriter app on reMarkable Paper Pro

## 0. Goal

Give the Paper Pro the same on-device management surface that upstream MoveWriter has on Move:

- Pair / unpair / switch keyboards
- Change keyboard layout
- Enable / disable the Bluetooth keyboard service
- Do all of the above without a PC

Secondary (and more urgent) goal: make the already-paired keyboard reconnect after long standby without needing the PC.

---

## 1. What is actually wrong on this device (facts from live SSH)

Device probed 2026-08-01 over USB `root@10.11.99.1`:

| Fact | Value |
|------|-------|
| Model | Paper Pro (`imx8mm-ferrari`) |
| OS | `3.28.0.164` (xochitl `3.28-tentacruel`, built 2026-07-02) |
| Kernel | `6.12.49+git-imx8mm-ferrari` aarch64 |
| Saved keyboard | `C2:8B:E9:E7:7B:D5` (CLVX S, BLE static addr) + a second bond `C2:B3:C3:30:75:FF` |
| PaperWriter home | `/home/root/.paperwriter/` has script + units + `libepaper.so.orig` |
| BT service unit in systemd | **MISSING** from both `/etc` and `/usr/lib` |
| `remarkable-bt-keyboard` running | **No** |
| `bluetooth.service` | inactive; `btnxpuart` **not loaded** |
| XOVI tree | Present at `/home/root/xovi` (appload + qt-resource-rebuilder + message-broker) |
| XOVI active in xochitl | **No** (`LD_PRELOAD` not set) |
| hashtab | Empty (`/home/root/xovi/exthome/qt-resource-rebuilder/` has no file) |
| xovi-tripletap | Present under `/home/root/xovi-tripletap`, unit **not installed** |
| Python / entware / vellum / `/opt` | **None** |
| Rootfs free | 41.6 MB (91% full) — small unit writes OK |
| `/etc` | overlay → upperdir `/var/volatile/etc` (**wiped every reboot**) |
| `/` | ext4 **ro**, remount-rw works |
| `/home` + `/var/lib/bluetooth` | encrypted disk, **survives reboot + OTA** |
| Screen passcode | None (hashtab rebuild won't block on PIN) |
| Qt | 6.10.3 |
| `libepaper.so` | `/usr/lib/plugins/platforms/libepaper.so` (stock; layout not currently patched) |
| Keymap offset discovered | `0x26708` (score 380) |

### 1.1 Root cause of “keyboard dies after long standby”

Two independent failures stack:

**A. Service is not installed at all right now.**

PaperWriter’s units were last written to home on 2026-07-28. The OS was updated 2026-07-02 to 3.28. After OTA the rootfs A/B swap **wiped** everything under `/usr/lib/systemd/system/`. The bootstrap unit that is supposed to re-seed from home was itself on the wiped rootfs, so nothing ever comes back. Result today:

- no `remarkable-bt-keyboard.service`
- no `paperwriter-bt-bootstrap.service`
- `btnxpuart` not loaded, bluetoothd not running
- bonds still exist under `/var/lib/bluetooth` (home-backed) but nothing tries to connect them

That alone explains “I need the PC to reconnect.” The PC path re-runs install + pair; the on-device loop never starts.

**B. Even when the service *is* running, resume is broken.**

Stock firmware sleep hook `/lib/systemd/system-sleep/sleep-wifi.sh`:

```
before suspend:
  rmmod btnxpuart          # destroys hci0
  touch /run/reload-bt
  ifconfig wlan0 down
  rmmod iw61x_sdw61x

after resume (async via systemd-run):
  modprobe iw61x_sdw61x
  modprobe btnxpuart       # fresh hci0, power-off by default
```

`bt-keyboard.sh` only does power-on + NXP `BT_PS_DISABLE` (HCI `0xFC23`) **once at start**. The forever loop only runs `bluetoothctl connect $MAC`. After deep sleep:

1. `hci0` is destroyed and recreated
2. adapter comes up Powered: no
3. NXP auto-sleep is re-enabled
4. `connect` fails forever with timeout / InProgress
5. user must re-pair from PC

Journal confirms the device does deep suspend-then-hibernate (last cycle ~11:36 → 14:15).

**Both must be fixed.** The native app alone does not fix standby reconnect; it only removes the PC from the recovery path. The service/resume fix is the higher-ROI half.

### 1.2 Why the native app *is* feasible on this Paper Pro

Upstream MoveWriter gates native app to Move only (`supports_native_app = model == MODEL_MOVE`). That gate is product caution, not a hardware limit.

On this device XOVI + AppLoad are **already installed** (someone put them there around May 23 / Jul 14). AppLoad is the RMPP-first launcher (asivery/rm-appload). The only missing pieces are:

1. hashtab rebuild for xochitl 3.28 (empty today)
2. XOVI activation (autostart unit or manual `xovi/start`)
3. Python 3 for the backend (entware)
4. Paper Pro–aware backend (paths, NXP radio rules, paperwriter home dir)
5. Ungating the desktop installer

So the work is: **port + harden**, not invent.

---

## 2. Architecture (what gets installed)

```
┌─────────────────────────────────────────────────────────────┐
│ xochitl (with LD_PRELOAD=xovi.so)                           │
│   └─ AppLoad extension                                      │
│        └─ PaperWriter QML (resources.rcc)                   │
│             ⇄ Unix SEQPACKET socket                         │
│                  └─ backend/entry → python3 -m backend.main │
│                       ├ bluetooth.py  (pair/scan/reconnect) │
│                       ├ service.py    (install BT unit)     │
│                       └ layout_patcher.py (libepaper.so)    │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ remarkable-bt-keyboard.service                              │
│   └─ /home/root/.paperwriter/bt-keyboard.sh                 │
│        • load btnxpuart/uhid gently                         │
│        • BlueZ input.conf + NXP PS_DISABLE                  │
│        • reconnect loop                                     │
│        • NEW: resume re-init (system-sleep hook)            │
└─────────────────────────────────────────────────────────────┘
```

Persistence map (critical — wrong layer = disappears after reboot/OTA):

| Asset | Where | Survives reboot | Survives OTA |
|-------|-------|-----------------|--------------|
| App files, backend, python | `/home/root/...` | yes | yes |
| Bonds / MAC file | `/var/lib/bluetooth`, `/home/root/.paperwriter-keyboard` | yes | yes |
| BT unit + enable symlink | `/usr/lib/systemd/system/` (rw remount) | yes | **no** |
| XOVI autostart unit | `/usr/lib/systemd/system/paperwriter-xovi.service` | yes | **no** |
| Crash-protection drop-ins | `/usr/lib/systemd/system/*.service.d/` | yes | **no** |
| `/etc` copies | volatile overlay | **no** | n/a |
| Bootstrap re-seed script | `/home/root/.paperwriter/paperwriter-bt-bootstrap.sh` | yes | yes |
| Bootstrap *unit* | `/usr/lib/...` | yes | **no** |

OTA always requires a one-shot reinstall from the desktop app (or a future “self-heal on next PC connect”). That is already how upstream MoveWriter documents it. Do not try to survive OTA without a PC — there is no persistent boot trigger outside rootfs.

---

## 3. Workstreams (do in this order)

### Workstream A — Standby reconnect fix (do first, no native app needed)

**Why first:** unblocks the reported bug today; native app is useless if the service still dies after sleep.

#### A1. Resume re-init hook

Add `/home/root/.paperwriter/bt-resume.sh` and install a drop-in into the sleep path.

Preferred mechanism (does not fight stock `sleep-wifi.sh`):

```
# /usr/lib/systemd/system-sleep/zz-paperwriter-bt.sh
#!/bin/sh
# Runs after stock sleep-wifi.sh (name sorts last).
case "$1" in
  after)
    # Detach so we don't block resume.
    systemd-run --no-block /home/root/.paperwriter/bt-resume.sh
    ;;
esac
```

`bt-resume.sh` must:

1. Wait until `btnxpuart` is loaded and `/sys/class/bluetooth/hci0` exists (poll ≤30s). Stock async-after may still be running.
2. `chmod 555 /etc/bluetooth`
3. Ensure `bluetooth.service` is active (start, never restart if already up).
4. Re-take wake locks (`paperwriter.bt`, `user.lock`).
5. Force runtime PM on for hci0 device tree (`power/control=on`, `autosuspend_delay_ms=-1`).
6. `bluetoothctl power on` + `pairable on` (retry loop).
7. Re-send NXP PS_DISABLE: `hcitool cmd 0x3f 0x23 0x03 0x00 0x00`.
8. If MAC file present: `bluetoothctl connect $MAC` with a short retry (3–5 attempts, 3s apart).
9. Log to `/home/root/.paperwriter/resume.log` (rotate/truncate at 64KB).

Also teach the long-running loop in `bt-keyboard.sh` to detect “adapter went away”:

```
# every reconnect tick:
if ! [ -d /sys/class/bluetooth/hci0 ]; then
    # wait / re-init path (share code with bt-resume.sh)
    ...
    continue
fi
if ! bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
    # re-run power-on + PS_DISABLE
    ...
fi
ensure_connected "$MAC"
```

Extract shared helpers into `bt-lib.sh` sourced by both scripts so power-on / PS_DISABLE / connect live in one place.

#### A2. Make the service actually present after install

Current `service_installer.py` already seeds `/usr/lib` + home. On this device the seed is gone. Actions:

1. Desktop “Enable service” must always rewrite units (idempotent), not skip when home files exist.
2. Bootstrap unit must also re-seed **itself** into `/usr/lib` (today it only re-seeds the keyboard unit). If bootstrap is missing, nothing heals.
3. After install, verify with `systemctl is-enabled` + `is-active` and surface failure in UI (today silent).
4. Disk-space preflight: refuse install if `/` has < 1 MB free (units are tiny; 41 MB is fine, but fail loud if not).

#### A3. Live verification checklist (A alone)

On the device after A:

```
systemctl is-active remarkable-bt-keyboard     # active
lsmod | grep btnxpuart                        # loaded
bluetoothctl info C2:8B:E9:E7:7B:D5 | grep Connected
# put tablet to sleep 2+ minutes, wake:
# keyboard should reconnect ≤30s without PC
cat /home/root/.paperwriter/resume.log
```

Ship A as a desktop-only release before touching native app. Users get the bugfix immediately.

---

### Workstream B — Port native app to Paper Pro

#### B1. Ungate detection

`core/device.py`:

```python
info["supports_native_app"] = model in (MODEL_MOVE, MODEL_PAPER_PRO)
```

UI (`ui/main_screen.py`):

- Rename section: `"On-device App (Experimental)"` (drop “upstream Move only”).
- Button text: `"Install on device"` / `"Uninstall"` (not “Install on Move”).
- Keep the experimental warning; add Paper Pro–specific note about OTA + disable auto-update.
- Status string when unsupported only for unknown models.

#### B2. Branding / paths on device

Native app currently uses Move paths. Paper Pro fork must use PaperWriter paths so it coexists with any leftover MoveWriter install and matches the desktop service:

| Current (Move) | Paper Pro target |
|----------------|------------------|
| `/home/root/.movewriter/` | `/home/root/.paperwriter/` |
| `/home/root/.movewriter-keyboard` | `/home/root/.paperwriter-keyboard` |
| AppLoad id `movewriter` | keep `movewriter` **or** rename to `paperwriter` (see B2a) |
| Autostart `movewriter-xovi.service` | `paperwriter-xovi.service` (name free on this device) |
| DEST_DIR `.../appload/movewriter` | `.../appload/paperwriter` if renamed |

**B2a decision (pick one before coding):**

- **Option keep-id:** leave AppLoad id `movewriter`. Less QML/manifest churn. Confusing brand on Paper Pro.
- **Option rename (recommended):** id `paperwriter`, menu name “PaperWriter”. Touch: `manifest.json`, DEST_DIR, any hardcoded qml strings, installer paths. Cleaner.

Recommend **rename**.

#### B3. Backend: Paper Pro Bluetooth rules

`nativeapp/backend/bluetooth.py` is a Move port of the desktop stack. It is missing Paper Pro NXP hardening that already lives in `core/bluetooth.py` + `resources/bt-keyboard.sh`:

Must add (copy logic, don’t SSH):

1. Never `modprobe -r btnxpuart`, never `hciconfig hci0 down`.
2. Before scan/pair: send PS_DISABLE vendor HCI; take wake lock; set runtime PM on.
3. Soft-block WiFi briefly only during active scan (same IW61x combo), restore after.
4. `chmod 555 /etc/bluetooth` before power on.
5. Restart bluetoothd at most once after writing `input.conf` (stamp file).
6. Prefer address from **live scan on device**, not the Windows-shown MAC (BLE random/static mismatch — this keyboard already has two bonds).
7. After pair success: write MAC to `/home/root/.paperwriter-keyboard` and ensure service is running.
8. Expose a `reconnect_now` action for the QML “Reconnect” button (calls the same ensure_connected path the resume hook uses).

Also port layout list parity with desktop (include any Paper Pro–only layouts already in `resources/keymaps/`).

#### B4. Backend: service installer paths

`nativeapp/backend/service.py` currently installs to `.movewriter` and has **no bootstrap unit**. Change to:

- Script dir `/home/root/.paperwriter`
- MAC file `/home/root/.paperwriter-keyboard`
- Install both `remarkable-bt-keyboard.service` **and** `paperwriter-bt-bootstrap.service`
- Seed `/usr/lib` (rw remount) + `/etc` (volatile current-boot)
- Install the new `zz-paperwriter-bt.sh` sleep hook + `bt-resume.sh` (Workstream A)
- Use the Paper Pro `bt-keyboard.sh` from `nativeapp/resources/` (must be the same file the desktop ships — single source: copy from `resources/` at build/upload time, do not maintain two copies)

#### B5. Backend: layout patcher path

`libepaper.so` on Paper Pro is:

```
/usr/lib/plugins/platforms/libepaper.so
```

not `/usr/lib/libepaper.so` (Move). Desktop `core/layout_patcher.py` already discovers; native `nativeapp/backend/layout_patcher.py` must use the same discovery (or hardcode the plugins path with fallback). Offset on this device: `0x26708`. Backup stays at `/home/root/.paperwriter/libepaper.so.orig`.

Rootfs is ro → patch must `mount -o remount,rw /`, write, `sync`, remount ro. Same as desktop.

#### B6. Python runtime (entware)

Backend entry requires `python3`. Device has none.

Installer path (already sketched in `core/native_app_installer.py`):

1. Install entware via `rmpp-entware` (home-backed: `/home/root/.entware` bind-mounted on `/opt`).
2. Create `/opt` directory on rootfs (one-time rw remount; a few KB).
3. `opkg install python3` (~30 MB under `/home` — free space is fine).
4. Ensure bind-mount of `/opt` happens:
   - at install time
   - in XOVI autostart (unmount before `xovi/start`, remount after — already in upstream script; keep it)
   - in `backend/entry` (already has the bind-mount fallback)

**Do not put python on rootfs.** Root only has 41 MB free and OTA wipes it.

**Vellum:** preferred for XOVI package mgmt, but this device has a working manual XOVI tree and no vellum. Two sub-options:

- **B6-vellum (preferred long-term):** install vellum, `vellum add xovi appload` (or `upgrade`/`reenable`). Handles OS-version gating.
- **B6-reuse (faster, this device):** detect existing `/home/root/xovi` + `appload.so`, skip vellum, just rebuild hashtab + activate. Fall back to vellum only if XOVI missing or activation fails.

Recommend **B6-reuse with vellum fallback**. Avoids fighting the existing install and skips a large download when unnecessary.

#### B7. Hashtab rebuild + XOVI activation

Required for AppLoad menu injection. Empty hashtab today = AppLoad never appears.

Reuse the bounded rebuild already in `native_app_installer._rebuild_hashtable`:

- Stop xochitl
- Run `rebuild_hashtable` with timeout + kill orphans
- Retry once
- On failure: start stock xochitl, abort install, keep BT service intact

Then activate via autostart script (`paperwriter-xovi-autostart.sh`, renamed from movewriter one):

- Failsafe attempt counter (`MAX_ATTEMPTS=3`) under `/home/root/.paperwriter/`
- Unmount `/opt` during start, remount after
- 60s health check → reset counter or revert to `xovi/stock`

Install crash protection (already coded):

- `xochitl-nowatchdog.conf` → `WatchdogSec=0` only (no `[Unit]` deps)
- `rm-emergency-override.conf` → don’t reboot on xochitl hang during BT pair flicker

Sanity gates already present — keep them. A bad `[Unit] Requires=` drop-in factory-reset the device in upstream history.

#### B8. QML / resources.rcc

- `nativeapp/resources.rcc` is present (15 KB, qres v3). Qt on device is 6.10.3; qres v3 is fine.
- QML imports `QtQuick 2.15` — OK on Qt 6.
- Rebuild only if QML changes: need `rcc --binary`. On Windows dev machine there is currently no `rcc` / PySide6. Options:
  1. `pip install PySide6` → `pyside6-rcc --binary -o resources.rcc qml/application.qrc`
  2. Or rebuild on any machine with Qt 6 `rcc`
- Rename visible strings MoveWriter → PaperWriter in QML.
- Add a **Reconnect** button on the keyboard card (calls backend `reconnect_now`) — this is the on-device answer to “keyboard won’t come back.”
- Keep Passkey overlay; pair flicker (~10s) is expected; crash-protection drop-ins make it recoverable.

#### B9. Installer changes (`core/native_app_installer.py`)

| Change | Detail |
|--------|--------|
| Remove Move-only hard fail | `supports_device` already uses `supports_native_app`; B1 is enough |
| DEST_DIR | `/home/root/xovi/exthome/appload/paperwriter` (if renamed) |
| Paths under `.paperwriter` | attempts file, logs, autostart script name |
| Entware URL | already rmpp-entware — correct for Paper Pro |
| Reuse-existing-XOVI branch | if `test -d /home/root/xovi && test -f .../appload.so` → skip vellum add; still run hashtab + activate |
| Preflight disk | `/` ≥ 2 MB free; `/home` ≥ 80 MB free (python) |
| Post-install verify | (1) hashtab exists (2) xochitl environ contains `xovi.so` (3) `test -d DEST_DIR` (4) python3 runs a one-liner |
| Uninstall | remove app dir + autostart + drop-ins; leave XOVI/entware/python (shared); leave BT service unless user disables it separately |

#### B10. Desktop UI install flow

1. Connect → detect Paper Pro → native button enabled.
2. Click Install → progress callbacks (entware / python / hashtab ~1–2 min screen blank / activate).
3. On success: “Open ☰ → AppLoad → PaperWriter on the tablet.”
4. Uninstall button when installed.

Warn once: disable auto-update on the tablet (Settings). After any OTA: Uninstall native app from desktop → update → reinstall (same as upstream Move docs).

---

### Workstream C — Optional hardening (after A+B work)

1. **Self-heal on desktop connect.** When desktop connects and detects bonds + missing units, offer “Repair service” one-click (re-seed A without full re-pair).
2. **Tripletap integration.** Device already has `xovi-tripletap`. Optionally enable it as a no-autostart fallback (triple power button → `xovi/start`) for users who don’t want XOVI every boot. Default remains autostart-with-failsafe.
3. **Single keyboard bond cleanup.** Device has two CLVX bonds; UI should list both and let user pick/delete stale one.
4. **CLI:** `python cli.py install-native` / `uninstall-native` for headless.
5. **Tests:** extend `tests/test_live_paper_pro.py` with native-app install smoke (gated on `PAPERWRITER_LIVE=1` + explicit `PAPERWRITER_NATIVE=1` so normal live tests don’t rebuild hashtab).

---

## 4. Implementation order (concrete commits)

Do not mix A and B in one commit. A is the bugfix; B is the feature.

| # | Commit | Touches |
|---|--------|---------|
| 1 | **A1/A2 service + resume** | `resources/bt-keyboard.sh`, new `resources/bt-resume.sh`, new `resources/zz-paperwriter-bt.sh`, `resources/paperwriter-bt-bootstrap.sh`, `core/service_installer.py` |
| 2 | **A3 verify on device** | manual: reinstall service from desktop, sleep test |
| 3 | **B1 ungate + UI rename** | `core/device.py`, `ui/main_screen.py`, README |
| 4 | **B2–B5 backend Paper Pro port** | `nativeapp/backend/*`, `nativeapp/resources/*` (sync from `resources/`), `nativeapp/manifest.json`, QML strings |
| 5 | **B6–B9 installer** | `core/native_app_installer.py`, autostart script rename, resources drop-ins |
| 6 | **B8 rebuild rcc** | `pip install PySide6` once; commit new `resources.rcc` |
| 7 | **B10 live install on this tablet** | run installer over SSH; confirm AppLoad menu; pair/reconnect/layout |
| 8 | **C optional** | self-heal, CLI, tests |

---

## 5. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| XOVI/AppLoad qmldiff doesn’t match xochitl 3.28 | medium | AppLoad menu missing or xochitl crash-loop | hashtab rebuild is hard gate; failsafe MAX_ATTEMPTS=3 reverts to stock; never leave device dark |
| hashtab rebuild hangs | medium (known intermittent) | install freezes | bounded background rebuild + kill + one retry (already coded) |
| Rootfs full during unit write | low (41 MB free) | silent unit loss — **this is how the current bug happened after OTA** | preflight free-space check; post-install `systemctl is-enabled` verify |
| `/etc` volatile wipe each boot | certain | units only in `/etc` disappear | always seed `/usr/lib`; bootstrap re-seeds `/etc` for current boot |
| OTA wipes `/usr/lib` units | certain on update | service + XOVI autostart gone | document reinstall; desktop self-heal on next connect (C1) |
| Entware `/opt` glibc shadow breaks XOVI start | medium | xochitl fails to start with XOVI | autostart unmounts `/opt` around `xovi/start` (already in upstream script) |
| Pair flicker freezes UI | medium on 3.28 | looks bricked | watchdog drop-in + rm-emergency override (already coded); warn user |
| Wrong `libepaper.so` path | high if Move path reused | layout patch no-ops or bricks UI binary | use plugins path; backup first; restore on uninstall |
| Dual CLVX bonds confuse reconnect | medium on this device | connects wrong/stale MAC | UI lists bonds; reconnect uses MAC file only; offer cleanup |
| Writing forbidden `[Unit] Requires=` drop-in | low (we have guards) | factory reset (upstream incident) | keep forbidden-token checks; never add cross-unit deps |
| User enables auto-update | high over time | breaks XOVI after OTA | install-time warning; README; failsafe leaves stock UI usable |

---

## 6. Explicit non-goals

- Surviving OTA with zero PC involvement. Not possible without a persistent off-rootfs boot trigger, which the platform does not provide safely.
- Replacing XOVI/AppLoad with a custom launcher.
- Supporting RM1/RM2 (no BT).
- Shipping a static Python binary instead of entware (possible later if entware becomes painful; not needed now).
- Rewriting the backend in shell/C. Python + AppLoad SEQPACKET protocol already works upstream.

---

## 7. Success criteria

**Workstream A done when:**

1. After cold boot, without PC: `systemctl is-active remarkable-bt-keyboard` → active, keyboard connects ≤30s.
2. After deep sleep ≥5 min, without PC: keyboard reconnects ≤30s; `resume.log` shows power-on + PS_DISABLE + connect OK.
3. Desktop Enable/Disable is idempotent and verifies unit state.

**Workstream B done when:**

1. Desktop shows enabled “Install on device” for this Paper Pro.
2. Install completes; xochitl comes back with XOVI loaded; ☰ → AppLoad → PaperWriter opens.
3. From the tablet only: enable service, scan, pair a keyboard, set layout, reconnect, unpair.
4. Uninstall removes app + autostart + drop-ins; stock UI returns; BT service can remain if user wants.
5. Failsafe: force-break XOVI (empty hashtab) → within 3 boots device stays on stock UI, usable.

---

## 8. Recommended first action tomorrow

Do **not** start with the native app install. Sequence:

1. Implement Workstream A (resume hook + service seed fixes) in the repo.
2. From desktop: Disable service (if partial) → Enable service on this tablet.
3. Confirm units in `/usr/lib`, service active, keyboard connected.
4. Sleep tablet 5+ minutes, wake, confirm reconnect without PC.
5. Only then start Workstream B.

If A is solid, the original complaint is already fixed. B then removes the PC from day-to-day management (pair new keyboard, change layout, toggle service) which is the actual value of the upstream native app.

---

## 9. File checklist (expected diff surface)

```
core/device.py                          # supports_native_app for paper_pro
core/service_installer.py               # bootstrap self-seed, sleep hook, verify
core/native_app_installer.py            # paths, reuse-XOVI, paperwriter names, preflight
core/bluetooth.py                       # (maybe) share resume helpers docs only
resources/bt-keyboard.sh                # adapter-lost detection in loop
resources/bt-resume.sh                  # NEW
resources/bt-lib.sh                     # NEW shared helpers (optional but cleaner)
resources/zz-paperwriter-bt.sh          # NEW system-sleep drop-in
resources/paperwriter-bt-bootstrap.sh   # also re-seed bootstrap unit + sleep hook
resources/paperwriter-bt-bootstrap.service
resources/paperwriter-xovi-autostart.sh # rename from movewriter-*, .paperwriter paths
resources/paperwriter-xovi.service
resources/xochitl-nowatchdog.conf       # keep
resources/rm-emergency-override.conf    # keep
nativeapp/manifest.json                 # id/name paperwriter
nativeapp/backend/config.py             # .paperwriter
nativeapp/backend/service.py            # paperwriter paths + bootstrap + resume
nativeapp/backend/bluetooth.py          # NXP rules + reconnect_now
nativeapp/backend/layout_patcher.py     # plugins/platforms path
nativeapp/backend/entry                 # keep entware bind
nativeapp/backend/main.py               # reconnect action, layout list parity
nativeapp/resources/*                   # sync from resources/ (single source)
nativeapp/qml/*.qml                     # rename strings, Reconnect button
nativeapp/resources.rcc                 # rebuild
ui/main_screen.py                       # ungate, labels
README.md                               # Paper Pro native app section
docs/NATIVE_APP_PAPER_PRO_PLAN.md       # this file
tests/...                               # later (C)
```

---

## 10. One-paragraph summary

The keyboard dies after standby because (1) the BT service units were wiped by the 3.28 OTA and never came back, and (2) even when running, the script does not re-init the NXP radio after firmware’s `rmmod btnxpuart` on suspend. Fix that first with a resume hook and hardier service seeding — that alone removes the need for the PC on the common path. Then ungate and port the existing Move native app (XOVI/AppLoad/QML/Python) onto Paper Pro: this device already has XOVI+AppLoad installed but inactive and hashtab-less; add entware Python, rebuild hashtab for 3.28, point backend at `.paperwriter` paths and NXP rules, and install with the same crash-protection/failsafe autostart upstream uses. Native app then covers on-device pair/layout/service management; it is not a substitute for fixing resume.

## 11. Implementation status (2026-08-01)

| Stream | Status |
|--------|--------|
| **A** resume hook + service seed | **Done and verified on device** — sleep reconnect works |
| **B** code port (paths, backend, UI, installer, rcc) | **Done in repo** |
| **B** live AppLoad activation on OS 3.28.0.164 | **Blocked upstream** |

### Live install findings on this tablet

- Entware + Python 3.13 install OK under `/home/root/.entware` → `/opt`.
- Hashtab rebuild for 3.28 succeeded (`hashtab` ~674 KB).
- PaperWriter app tree uploaded to `.../appload/paperwriter`.
- Activating XOVI with the on-device `appload.so` **crash-looped xochitl**:
  `Couldn't resolve the hashed identifier ... required by AppLoad hooks in main UI`.
- Vellum: **no AppLoad package declares support for `remarkable-os` 3.28**.
  Latest `appload-0.5.3-r1` requires `remarkable-os>=3.26` and `<3.28`.
- Installer now **hard-gates** on Vellum AppLoad compatibility and refuses before
  touching the UI. Stale `appload.so` moved to `inactive-extensions` on device.
- Stock UI restored; BT keyboard service left **active**.

### Unblock path

When Vellum publishes AppLoad (and matching qt-resource-rebuilder hooks) for 3.28+:

1. `vellum add appload` succeeds on device.
2. Desktop → **Install on device** again.
3. Expect hashtab rebuild + XOVI activate + ☰ → AppLoad → PaperWriter.

Until then, day-to-day keyboard use is covered by Workstream A (no PC after sleep).
