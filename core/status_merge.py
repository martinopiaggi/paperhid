"""Combined keyboard + pointer status semantics for PaperHid.

Pointer states:

* **not_installed** — no unit / no home install; informational; contributes exit 0
* **active** — unit active; healthy; exit 0
* **inactive** / **failed** — installed but not healthy; nonzero exit

Combined status always prints labeled keyboard and pointer sections, attempts both,
and returns nonzero only for a real failure — not merely because the optional
pointer component is absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PointerStatus:
    state: str  # not_installed | active | inactive | failed | unknown
    detail: str = ""
    exit_code: int = 0

    @property
    def is_absent(self) -> bool:
        return self.state == "not_installed"


def classify_pointer_status(
    *,
    home_present: bool,
    unit_file_present: bool,
    is_active: Optional[bool],
    is_failed: bool = False,
) -> PointerStatus:
    """Classify pointer daemon install/health from probe facts."""
    installed = home_present or unit_file_present
    if not installed:
        return PointerStatus(
            state="not_installed",
            detail="pointer not installed (optional; keyboard-only is fine)",
            exit_code=0,
        )
    if is_failed:
        return PointerStatus(
            state="failed",
            detail="pointer unit present but failed",
            exit_code=1,
        )
    if is_active is True:
        return PointerStatus(
            state="active",
            detail="paperpointer.service active",
            exit_code=0,
        )
    if is_active is False:
        return PointerStatus(
            state="inactive",
            detail="pointer installed but not active",
            exit_code=1,
        )
    return PointerStatus(
        state="unknown",
        detail="pointer install present; active state unknown",
        exit_code=0,
    )


def merge_exit_codes(keyboard_code: int, pointer: PointerStatus) -> int:
    """Combine section exit codes; absent pointer never forces failure."""
    ptr = 0 if pointer.is_absent else pointer.exit_code
    if keyboard_code != 0:
        return keyboard_code
    if ptr != 0:
        return ptr
    return 0


def format_status_report(keyboard_lines: list[str], pointer: PointerStatus) -> str:
    lines = ["=== keyboard ==="]
    lines.extend(keyboard_lines if keyboard_lines else ["(no keyboard status)"])
    lines.append("=== pointer ===")
    lines.append(f"state: {pointer.state}")
    if pointer.detail:
        lines.append(f"detail: {pointer.detail}")
    return "\n".join(lines) + "\n"
