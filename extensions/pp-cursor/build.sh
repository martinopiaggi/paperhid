#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export PATH="${HOME}/scoop/apps/gcc-aarch64-none-linux-gnu/current/bin:/c/Users/batman/scoop/apps/gcc-aarch64-none-linux-gnu/current/bin:${PATH}"
CC="${CC:-aarch64-none-linux-gnu-gcc}"
python xovigen.py -o xovi.c -H xovi.h pp-cursor.xovi
"$CC" -shared -fPIC -O2 -Wall -I. -include types.h \
  -o pp-cursor.so pp_cursor.c xovi.c -ldl -lpthread
echo "built $(ls -la pp-cursor.so)"
"$CC" -print-file-name=libc.so.6 2>/dev/null || true
aarch64-none-linux-gnu-nm -D pp-cursor.so | grep ' U ' || true
