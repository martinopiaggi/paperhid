#!/bin/sh
# Enable framebuffer-spy under XOVI (no AppLoad). Restarts xochitl.
set -e
XOVI=/home/root/xovi
EXT=$XOVI/extensions.d
INACT=$XOVI/inactive-extensions

mkdir -p "$EXT"
if [ -f "$INACT/framebuffer-spy.so" ] && [ ! -f "$EXT/framebuffer-spy.so" ]; then
    cp -a "$INACT/framebuffer-spy.so" "$EXT/framebuffer-spy.so"
fi
# NOTE: do NOT auto-install pp-cursor.so here — the swapBuffers trampoline
# crash-looped xochitl on 3.28 (wrong site/ABI). Keep it out of extensions.d
# until a verified hook lands. Touch-click does not need it.
rm -f "$EXT/pp-cursor.so"
# keep AppLoad disabled
ls -la "$EXT"

echo "starting xovi..."
"$XOVI/start"
sleep 6

XO=$(ps w | grep '[x]ochitl --system' | awk '{print $1}' | head -n 1)
echo "xochitl pid=$XO"
if [ -n "$XO" ]; then
    tr '\0' '\n' < "/proc/$XO/environ" | grep -E 'LD_PRELOAD|XOVI' || true
    grep -c xovi "/proc/$XO/maps" || true
fi

ls -la /run/xovi-mb /run/xovi-mb-out 2>&1 || true

# Query config string via message broker
CFG=""
if [ -p /run/xovi-mb ]; then
    rm -f /tmp/pp-fbcfg.out
    # reader
    cat /run/xovi-mb-out > /tmp/pp-fbcfg.out &
    RP=$!
    sleep 0.4
    printf '%s\n' '>eframebuffer-spy$getConfigString:' > /run/xovi-mb
    sleep 1.5
    kill $RP 2>/dev/null || true
    wait $RP 2>/dev/null || true
    echo "CFG_RAW:"
    cat /tmp/pp-fbcfg.out 2>/dev/null || true
fi

# Also scrape xochitl journal/stderr if any
logread 2>/dev/null | grep -i 'Found framebuffer' | tail -n 3 || true
journalctl -u xochitl -n 80 --no-pager 2>/dev/null | grep -iE 'Found framebuffer|Invoked image|xovi' | tail -n 20 || true

echo DONE
