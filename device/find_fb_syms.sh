#!/bin/sh
XO=
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  cmd=$(cat "$d/cmdline" 2>/dev/null | tr '\0' ' ') || continue
  case "$cmd" in
    /usr/bin/xochitl*)
      XO=$pid
      echo "PID=$pid"
      break
      ;;
  esac
done
echo "XO=$XO"
echo "=== dynsym xochitl ==="
readelf -Ws /usr/bin/xochitl 2>/dev/null | grep -i EPFramebuffer | head -n 40
echo "=== nm D xochitl ==="
nm -D /usr/bin/xochitl 2>/dev/null | grep -i EPFramebuffer | head -n 40
echo "=== nm all (may be huge) ==="
nm /usr/bin/xochitl 2>/dev/null | grep swapBuffers | head -n 20
echo "=== libepaper ==="
nm -D /usr/lib/plugins/platforms/libepaper.so 2>/dev/null | grep -iE 'swap|Framebuffer|Update' | head -n 40
strings /usr/lib/plugins/platforms/libepaper.so | grep -i EPFramebuffer | head -n 20
echo "=== maps epaper/scene ==="
if [ -n "$XO" ]; then
  grep -E 'epaper|scenegraph|qsg' /proc/$XO/maps || true
fi
echo "=== dlopen test from python ==="
/opt/bin/python3 - <<'P'
import ctypes, ctypes.util
# try find symbol in loaded process via /proc/pid/mem - hard
# dlopen plugin
try:
  c=ctypes.CDLL('/usr/lib/plugins/scenegraph/libqsgepaper.so', mode=ctypes.RTLD_GLOBAL)
  print('dlopen plugin ok', c)
  print('swap', ctypes.c_void_p.in_dll.__func__)
except Exception as e:
  print('dlopen plugin', e)
try:
  c=ctypes.CDLL(None) # main
except Exception as e:
  print(e)
P
