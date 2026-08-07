# Optional on-screen cursor and Settings UI

Enabled only on firmware **3.28.0.164** with XOVI (`qt-resource-rebuilder`, `qt-command-executor`, `xovi-message-broker`).

```bash
python cli.py pointer enable-cursor
python cli.py pointer stock-ui            # remove cursor overlay only (keeps Settings)
python cli.py settings-ui                 # Settings → Help (first-time or re-enable)
python cli.py pointer enable-settings-ui  # same as settings-ui (nested name)
python cli.py pointer disable-settings-ui # remove Settings panel only
python cli.py repair-ui                   # alias for settings-ui
```

Clicks always use multitouch injection; the cursor is visual only (QML overlay). Settings → Help is independent of the cursor: re-enabling the cursor does not delete Settings.

**Settings → Help** (after `settings-ui`): status, Bluetooth, keyboard reconnect, language layout (US / US Intl / UK / DE / FR / IT / ES), pointer knobs. Pairing stays on the host CLI (`scan` / `pair`).

**Save work first** — xochitl restarts.

## Average user: first-time Settings UI

After keyboard/pointer install and XOVI is available on the tablet:

```bash
python cli.py settings-ui
```

Then open **Settings → Help** once. You should see the **PaperHid** section.

`python cli.py status` reports a `=== settings_ui ===` block. If it needs re-enable, run `settings-ui` again (same command as first-time).

## Why Help can look empty

The PaperHid block is a **QMD patch on stock Help**, loaded only while **XOVI is tethered** to xochitl. Files under `~/.paperpointer` and the QMD under XOVI’s `exthome` can still exist while Help looks stock if:

- xochitl restarted **without** XOVI (crash, freeze, hard reboot, or a path that only ran `systemctl start xochitl`), or
- XOVI never started after boot.

That is recovery territory, not “uninstall.” The fix is always the same one-liner below.

## Recovery: Settings → Help missing or tablet froze

**What most users need (copy-paste):**

```powershell
# Windows — after USB is connected and the tablet is responsive
$env:PAPERHID_PASSWORD = "your-root-password"
python cli.py settings-ui
```

```bash
# macOS / Linux
export PAPERHID_PASSWORD='your-root-password'
python cli.py settings-ui
```

Then open **Settings → Help** again (fresh open).

### Full checklist

1. **Power-cycle** the Paper Pro if it is frozen (hold power until off, then on).
2. Connect **USB**, wait ~30 seconds (SSH `10.11.99.1`).
3. Run **`python cli.py settings-ui`** (same as first enable; aliases: `repair-ui`, `pointer enable-settings-ui`).
4. On the tablet: **Settings → Help** again.

Optional check:

```bash
python cli.py pointer settings-ui-check
```

Empty response is OK until Help has been opened once after XOVI start (the health ping is loaded with that page).

If SSH still times out after a power-cycle, re-seat the USB cable and wait for `10.11.99.1` before retrying.

### Repair pointer daemon (if mouse also broken)

```bash
python cli.py install --pointer
python cli.py settings-ui
```

## BT keyboard and landscape

By default PaperHid does **not** spoof Type Folio (`osk_suppress=0`), so a Bluetooth keyboard should **not** force landscape.

If you set `osk_suppress=1` in `~/.paperpointer/pointer.conf` to hide the on-screen keyboard, landscape may return (same as a real Folio). Set `osk_suppress=0` and restart the pointer service to undo that.
