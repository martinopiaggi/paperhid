# Optional on-screen cursor

Enabled only on firmware **3.28.0.164** with XOVI (`qt-resource-rebuilder`, `qt-command-executor`, `xovi-message-broker`).

```bash
python cli.py pointer enable-cursor
python cli.py pointer stock-ui      # back to stock UI
```

Clicks always use multitouch injection; the cursor is visual only (QML overlay, not framebuffer hacks). Save work before enabling — xochitl restarts.
