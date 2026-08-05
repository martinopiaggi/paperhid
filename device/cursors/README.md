# Cursor skins

PaperPointer draws the pointer in QML. Styles:

| Style | Asset | Hotspot |
| --- | --- | --- |
| `cross` | Geometry in `paperpointer-cursor.qmd` | center (16,16) of 33×33 |
| `win95` | `win95.png` + `win95.meta` | tip from meta (default 0,0) |

## Install path on the tablet

```text
/home/root/.paperpointer/cursors/win95.png
/home/root/.paperpointer/cursors/win95.meta
```

`install` and `enable-cursor` upload this directory. Choose the style with:

```bash
python -m paperpointer.cli cursor-style win95
python -m paperpointer.cli cursor-style cross
```

or **Settings > Help** after `enable-settings-ui`.

## Using your own PNG

1. Prefer a **small** (24–48 px) high-contrast image: black fill, white outline, transparent background.
2. Windows `.cur` / `.ani` files are **not** loaded by QML. Convert offline to PNG.
3. Replace `win95.png` on the tablet (or in this folder before install).
4. Edit `win95.meta` so `hotspot_x` / `hotspot_y` match the tip (pixels from the top-left of the PNG). Wrong hotspot makes clicks feel offset.
5. Set `cursor_style=win95` and restart `paperpointer.service`, or use the CLI / Help buttons.

If `win95.png` is missing or fails to load, the QML layer falls back to the crosshair.

## License

The shipped `win95.png` is an original simple arrow drawn for PaperPointer (public domain / CC0 intent), not a rip of Microsoft Windows cursors. Replace it with your own asset if you prefer.
