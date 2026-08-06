# Optional on-screen cursor and Settings UI

Enabled only on firmware **3.28.0.164** with XOVI (`qt-resource-rebuilder`, `qt-command-executor`, `xovi-message-broker`).

```bash
python cli.py pointer enable-cursor
python cli.py pointer stock-ui            # remove cursor overlay only (keeps Settings)
python cli.py pointer enable-settings-ui  # Settings → Help PaperHid controls
python cli.py pointer disable-settings-ui # remove Settings panel only
```

Clicks always use multitouch injection; the cursor is visual only (QML overlay). Settings → Help is independent of the cursor: re-enabling the cursor does not delete Settings.

**Save work first** — xochitl restarts.
