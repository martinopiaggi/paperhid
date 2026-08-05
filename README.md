# PaperHid

Bluetooth **keyboard** and **mouse / touchpad** for [reMarkable Paper Pro](https://remarkable.com/).

Developer Mode + USB (`root@10.11.99.1`). Not affiliated with reMarkable.

## Install

```bash
git clone https://github.com/martinopiaggi/paperhid.git
cd paperhid
pip install -r requirements.txt
```

```powershell
$env:PAPERHID_PASSWORD = "tablet-ssh-password"   # Settings → Help → About
```

| Want | Command |
|------|---------|
| Keyboard | `python cli.py install --keyboard` then `pair` / GUI |
| Mouse | Entware Python on tablet (`/opt/bin/python3`), then `install --keyboard` + `install --pointer` |
| Both | `python cli.py install --all` |

```bash
python cli.py detect
python cli.py install --all          # or --keyboard / --pointer
python cli.py status
python cli.py scan
python cli.py pair --name YourDevice
python main.py                       # optional desktop GUI
```

**Mouse notes:** needs tablet Python (Entware / `opkg install python3`). Install preflights and will not upload without it. Clicks become multitouch in the stock UI. Optional on-screen cursor: firmware **3.28.0.164** + XOVI — `python cli.py pointer enable-cursor`.

**After deep sleep:** wake the keyboard/mouse (press a key). OTA can drop services — re-run `install`.

## Common commands

```bash
python cli.py status
python cli.py uninstall --keyboard|--pointer|--all
python cli.py pointer test-tap
python cli.py pointer stock-ui          # remove cursor overlay
```

Password: `--password` → `PAPERHID_PASSWORD` → legacy env aliases → saved config (`--save-password` after a successful connect).

## License

[Unlicense](LICENSE.md) (public domain).

Keyboard Bluetooth work is based on [MoveWriter](https://github.com/vikboyechko/movewriter) by Vik Boyechko.
