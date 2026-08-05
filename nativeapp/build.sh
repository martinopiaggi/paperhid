#!/bin/sh
# Build PaperHid native-app resources.rcc from QML.
# Prefer: pyside6-rcc --binary -o resources.rcc qml/application.qrc
set -e
cd "$(dirname "$0")"

echo "Compiling QML resources..."
if command -v pyside6-rcc >/dev/null 2>&1; then
  pyside6-rcc --binary -o resources.rcc qml/application.qrc
elif command -v rcc >/dev/null 2>&1; then
  rcc --binary -o resources.rcc qml/application.qrc
else
  echo "error: need pyside6-rcc or rcc" >&2
  exit 1
fi

echo "Built resources.rcc ($(wc -c < resources.rcc) bytes)"
