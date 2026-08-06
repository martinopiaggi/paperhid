# Optional on-screen cursor and Settings UI

Enabled only on firmware **3.28.0.164** with XOVI (`qt-resource-rebuilder`, `qt-command-executor`, `xovi-message-broker`).

```bash
python cli.py pointer enable-cursor
python cli.py pointer stock-ui            # remove cursor overlay only (keeps Settings)
python cli.py pointer enable-settings-ui  # Settings → Help PaperHid controls
python cli.py pointer disable-settings-ui # remove Settings panel only
```

Clicks always use multitouch injection; the cursor is visual only (QML overlay). Settings → Help is independent of the cursor: re-enabling the cursor does not delete Settings.

**Settings → Help** (after `enable-settings-ui`): status, Bluetooth, keyboard reconnect, language layout (US/UK/DE/FR/IT/ES), pointer knobs. Pairing stays on the host CLI (`scan` / `pair`).

**Save work first** — xochitl restarts.

## Why Help can look empty

The PaperHid block is a **QMD patch on stock Help**, loaded only while **XOVI is tethered** to xochitl. Files under `~/.paperpointer` and the QMD under XOVI’s `exthome` can still exist while Help looks stock if:

- xochitl restarted **without** XOVI (crash, freeze, hard reboot, or a path that only ran `systemctl start xochitl`), or
- XOVI never started after boot.

That is recovery territory, not “uninstall.”

## Recovery: Settings → Help missing or tablet froze

1. **Power-cycle** the Paper Pro if it is frozen (hold power until off, then on).
2. Connect **USB**, wait ~30 seconds.
3. From the PC (password as usual):

```powershell
# Windows
$env:PAPERHID_PASSWORD = "your-root-password"
python cli.py pointer enable-settings-ui
```

```bash
# macOS / Linux
export PAPERHID_PASSWORD='your-root-password'
python cli.py pointer enable-settings-ui
```

That reinstalls the settings QMD, starts **XOVI**, and canaries the UI.

4. On the tablet: open **Settings → Help** again (fresh open).

Optional check after recovery:

```bash
python cli.py pointer settings-ui-check
```

Empty response is OK until Help has been opened once after XOVI start (the health ping is loaded with that page).

If SSH still times out after a power-cycle, re-seat the USB cable and wait for `10.11.99.1` before retrying.
