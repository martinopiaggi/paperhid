# pp-cursor

XOVI extension: software cursor on reMarkable Paper Pro.

## How it works

On OS 3.28, `libqsgepaper.so` is **not** loaded. `EPFramebuffer::swapBuffers`
lives inside stripped `/usr/bin/xochitl`. This extension:

1. Finds the function via the `"swapBuffers:"` string xref + PACIBSP prologue
2. Installs an aarch64 trampoline that **never mutates arguments**
3. Calls original swap (UI compose + present)
4. Draws a crosshair into the FB (framebuffer-spy / `fb.cfg`)
5. While those args are **still live on the caller's stack**, re-presents once
   so the e-ink waveform includes the stamped pixels (when the dirty region
   covers the pointer)

> **Crash causes (fixed):**
> 1. Mutating args as `QRect` on a QRegion-style `swapBuffers` → crash-loop
> 2. Background poll thread replaying saved `QRegion*` after return →
>    dangling pointers → freeze then crash (cross visible once, then die)

## Build

```bash
# Windows (scoop): gcc-aarch64-none-linux-gnu
python xovigen.py -o xovi.c -H xovi.h pp-cursor.xovi
aarch64-none-linux-gnu-gcc -shared -fPIC -O2 -I. -include types.h \
  -o pp-cursor.so pp_cursor.c xovi.c -ldl -lpthread
```

## Install

```powershell
python -m paperpointer.cli enable-cursor --password $env:PAPERPOINTER_PASSWORD
```

Requires XOVI + `framebuffer-spy`. Sets `pointer.conf` `cursor=1`.

```bash
# recovery:
rm -f /home/root/xovi/extensions.d/pp-cursor.so
/home/root/xovi/stock
# or: python -m paperpointer.cli stock-ui
```

`enable-fb` still refuses to install this `.so` (spy only). Use `enable-cursor`.

## Limits

- Cursor appears when xochitl presents a dirty region that covers it (clicks,
  UI redraws, last-region replay on move). Pure air-moves over never-updated
  pixels can lag until the next UI present — e-ink partial update constraint.
- Not 60 Hz; poll capped ~12 Hz.

## Safety

If UI crash-loops: `rm -f .../pp-cursor.so && /home/root/xovi/stock`
