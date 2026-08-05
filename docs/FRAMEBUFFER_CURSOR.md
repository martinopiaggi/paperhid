# On-screen cursor: QML overlay, not framebuffer injection

Despite this file's historical name, PaperPointer's supported cursor does not write into the framebuffer. It is a version-gated QML item in xochitl's scene graph, fed by a private FIFO. Clicks remain synthetic multitouch events from `paperpointerd` and never use the cursor rendering path.

## Supported configuration

The cursor patch is validated only for:

| Component | Required value |
| --- | --- |
| Product | reMarkable Paper Pro (`imx8mm-ferrari`) |
| `/etc/os-release` `IMG_VERSION` | `3.28.0.164` |
| QMLDiff declaration | `VERSION 3.28.0.164` |
| QML target | `/qml/device/view/main/MainView.qml` |
| Target object | `Background` |

`device/enable_cursor.sh` compares the live `IMG_VERSION` with the supported version before mutating the XOVI installation. Do not remove or weaken this gate after an OTA: regenerate and validate the QMLDiff against that exact firmware instead.

## Architecture

```text
/dev/input/eventN
        |
  paperpointerd
        |  latest position, rate-limited
        |  "normalized_x,normalized_y,visible\n"
        v
/home/root/.paperpointer/cursor.fifo  (FIFO, mode 0600)
        |
        | /bin/cat kept alive by AsyncCommandExecutor
        v
paperpointer-cursor.qmd
        |
        | QML Item, clipped to Background, high z, no MouseArea
        v
Epaper.ScreenModeItem.Pen crosshair
        |
        v
xochitl's normal scene graph and e-ink update path
```

The coordinates are normalized to `0..1`, then mapped across the QML layer's current width and height. Synthetic touch injection uses the Elan multitouch coordinate range separately. Keeping the cursor protocol normalized avoids hard-coding the panel's pixel geometry in the transport.

The daemon uses a persistent nonblocking FIFO publisher with latest-position semantics and a configurable rate cap. Through `qt-command-executor`, the QML side starts a fixed `/bin/sh` command that first verifies the path is a FIFO, opens one inherited read/write descriptor to prevent EOF during daemon restarts, and then replaces itself with `/bin/cat`. If that process exits, a timer retries it. Cursor visibility is owned by the daemon via the FIFO `visible` flag (the QML layer has no local auto-hide timer). With `cursor_hide_ms=0` (default) the crosshair stays painted for as long as a pointer input source is open, including idle; a positive value auto-hides after that many idle milliseconds. The daemon always publishes `visible=0` when the source goes away or the publisher stops.

Cursor **skins** are independent of the motion protocol (still `nx,ny,visible`). `cursor_style=cross` draws the geometry crosshair; `cursor_style=win95` shows `/home/root/.paperpointer/cursors/win95.png` with a tip hotspot. The daemon writes a one-line `cursor_style` status file that the QML layer polls; missing PNG falls back to the crosshair. Clicks never depend on the skin.

The optional on-device controls are a separate QMLDiff targeting the exact 3.28 `Help.qml` tree. They are never merged into the cursor's `MainView.qml` patch and are managed by `enable-settings-ui` / `disable-settings-ui`. The earlier MainView overlay was reverted after it crash-looped xochitl; re-running `enable-cursor` therefore removes the optional settings QMD as a conservative reset.

The settings QML uses fixed `AsyncCommandExecutor` instances whose commands and arguments are defined at parse time. The invoked scripts under `device/ui-actions/` accept only explicit allow-listed values. `enable-settings-ui` atomically installs the Help-page QMD, starts XOVI, waits for the replacement xochitl process to remain stable, exercises the cursor FIFO and cursor broker health endpoint, and rolls the previous settings QMD back only if that stability or a compatibility canary fails. Because `Help.qml` is lazy-loaded, open **Settings > Help** and run `settings-ui-check` to verify its separate broker endpoint.

`xovi-message-broker` is used only for a small, bounded readiness ping. It is deliberately **not** used for motion packets because the tested upstream broker input loop leaks a xochitl file descriptor per command. Sending every pointer sample through that broker would eventually destabilize the UI.

## Why the native framebuffer approach was abandoned

Earlier experiments found that writing pixels into the mapped framebuffer changed RAM but did not request an e-ink waveform update. A later native `pp-cursor.so` experiment hooked `EPFramebuffer::swapBuffers`; mutating private Qt arguments crash-looped xochitl on 3.28, and even a passthrough/replay variant depended on an unverified private ABI.

Those experiments are not the production design:

- no `process_vm_writev` cursor drawing;
- no framebuffer pointer discovery for cursor rendering;
- no `swapBuffers` trampoline or replay;
- no fake Elan marker/hover input;
- no AppLoad application.

`enable-cursor` moves `pp-cursor.so` and `framebuffer-spy.so` out of `extensions.d` if either is present, preserving them only as disabled forensic artifacts. Do not manually re-enable them alongside the QML cursor. The repository's `enable-fb` and `fb-config` commands are legacy diagnostics, not cursor setup steps.

## Enable transaction and canary

Run from the host after saving active work:

```bash
python -m paperpointer.cli enable-cursor
```

The command uploads the current device files and runs `enable_cursor.sh`, which performs read-only preflight checks first:

1. Require exact image `3.28.0.164`.
2. Require XOVI and an active `qt-resource-rebuilder.so`.
3. Require `xovi-message-broker.so` and `qt-command-executor.so`, either active or available in `inactive-extensions`.
4. Refuse to replace `/home/root/.paperpointer/cursor.fifo` if that path exists but is not a FIFO.

Only then does it arm rollback, create the FIFO with mode `0600`, activate the two supporting extensions if needed, disable the abandoned native extensions, atomically install the QMLDiff, and start tethered XOVI.

The live canary then verifies:

1. xochitl starts and maps the resource rebuilder, broker, and command executor;
2. the QML reader consumes one invisible FIFO packet within the bounded startup window;
3. the QML layer returns `paperpointer-qml-3.28.0.164` to a bounded broker health ping;
4. xochitl's PID remains unchanged from module detection through the FIFO, broker, and service canaries;
5. after setting `cursor=1`, both `paperpointer.service` and the FIFO reader stay active.

If any armed step fails or the script receives a termination signal, its trap disables cursor publishing, removes the installed QML patch and FIFO, undoes only the supporting-extension activations it made, and returns xochitl to stock. XOVI remains deliberately tethered, so reboot is also a conservative return to the stock session.

## Refresh behavior

The visible cursor layer contains a full-scene `Epaper.ScreenModeItem` in `Pen` mode. This ensures both the old crosshair erase and new draw use the low-latency waveform while the cursor is visible. It asks xochitl's ordinary e-ink path to favor latency, but it cannot make the panel behave like a desktop display. A higher FIFO publish cap can make motion feel more immediate while also producing more ghosting; xochitl or the panel may coalesce updates.

Set the current build's supported cap with:

```bash
python -m paperpointer.cli cursor-rate HZ
```

The shipped default is 30 Hz and the accepted range is 1-40 Hz. `cursor-rate` validates and transactionally deploys the checked-out daemon before changing the rate; a failed restart restores the previous daemon and configuration. The cursor coordinate shown on the panel can temporarily trail the daemon's newest coordinate during rapid motion. The default `cursor_lag_ms=50` therefore anchors a moving click to a recent visible cursor sample; after motion stops, the exact final position is used. Accepted values are 0-250 ms. Distinguish transient refresh lag from a repeatable calibration offset by holding the pointer still before measuring.

## Stock recovery

The supported recovery command is:

```bash
python -m paperpointer.cli stock-ui
```

It performs and verifies a complete UI rollback:

- set `cursor=0` and restart `paperpointer.service`;
- remove the installed QMLDiff and cursor FIFO;
- run `/home/root/xovi/stock`;
- require a live xochitl process;
- fail if the new xochitl still maps a library from `/home/root/xovi/`.

Touch injection intentionally continues after `stock-ui`. Use `python -m paperpointer.cli uninstall` as a separate step if the input daemon should also stop.

If the enable script itself fails, read its error before retrying. `python -m paperpointer.cli probe` collects the relevant XOVI mappings, transport files, process state, and recent xochitl logs without modifying the tablet.

To remove only the optional Settings > Help panel while keeping the cursor session, run `python -m paperpointer.cli disable-settings-ui`. Use `stock-ui` for the broader recovery that removes both QML patches and returns xochitl to stock.

## Security boundary

The FIFO is created as root with mode `0600`, and the QML patch invokes only a fixed guarded shell command that opens and reads `/home/root/.paperpointer/cursor.fifo`; it accepts no packet text as a command. There is no cursor network listener. Nevertheless, XOVI and `qt-command-executor` run inside the privileged xochitl environment and are powerful: install only trusted extension binaries and QMLDiff files, and inspect local changes before running `enable-cursor`.

Host deployment uses the tablet's root SSH password. Supply it through the temporary `PAPERPOINTER_PASSWORD` environment variable, never commit it, and avoid a literal `--password` on shared systems because command arguments and shell history can expose it. The current client automatically accepts unknown SSH host keys, so use the direct trusted USB link.
