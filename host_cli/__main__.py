"""``python -m host_cli`` entry."""
from __future__ import annotations

from host_cli.app import main

raise SystemExit(main() or 0)
