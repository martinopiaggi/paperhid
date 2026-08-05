# Cursor skins

Optional pointer skins for PaperHid:

| Style | Asset | Hotspot |
| --- | --- | --- |
| `cross` | Geometry crosshair | center |
| `win95` | `win95.png` + `win95.meta` | tip from meta |

```bash
python cli.py pointer cursor-style win95
python cli.py pointer cursor-style cross
```

## Using your own PNG

1. Prefer a **small** (24–48 px) high-contrast image: black fill, white outline, transparent background.
2. Windows `.cur` / `.ani` files are **not** loaded by QML. Convert offline to PNG.
3. Replace `win95.png` on the tablet (or in this folder before install).
4. Edit `win95.meta` so `hotspot_x` / `hotspot_y` match the tip (pixels from the top-left of the PNG). Wrong hotspot makes clicks feel offset.
5. Set `cursor_style=win95` and restart `paperpointer.service`, or use the CLI / Help buttons.

If `win95.png` is missing or fails to load, the QML layer falls back to the crosshair.

## License

The shipped `win95.png` is an original simple arrow drawn for PaperPointer (public domain / CC0 intent), not a rip of Microsoft Windows cursors. Replace it with your own asset if you prefer.
