# pp-cursor (abandoned — do not install)

**Unsafe / abandoned.** This framebuffer swapBuffers trampoline is **not** the supported cursor path and must not be installed.

The supported cursor is a firmware-gated **QML overlay**:

```bash
python cli.py pointer enable-cursor
```

See [docs/cursor.md](../../docs/cursor.md).

`enable-cursor` actively **disables** any residual `pp-cursor.so` / `framebuffer-spy` install and moves them aside. Source here is retained only for archaeology.

**Do not** run `build.sh` or copy `pp-cursor.so` onto a device.
