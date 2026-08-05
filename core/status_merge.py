"""Keyboard + pointer status classification for PaperHid.

States: not_installed | staged | active | inactive | failed | unknown.
Exit 0 unless a registered unit is inactive/failed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ComponentStatus:
    state: str  # not_installed | staged | active | inactive | failed | unknown
    detail: str = ""
    exit_code: int = 0
    label: str = "component"

    @property
    def is_absent(self) -> bool:
        """True when the component is not expected to be running (optional / residual)."""
        return self.state in ("not_installed", "staged")


# Back-compat alias used by older tests / imports
PointerStatus = ComponentStatus


def classify_pointer_status(
    *,
    home_present: bool,
    unit_file_present: bool,
    is_active: Optional[bool],
    is_failed: bool = False,
) -> ComponentStatus:
    """Classify pointer daemon install/health from probe facts.

    Uninstall keeps ``~/.paperpointer`` on purpose. Home files without a unit
    file are **staged** (residual), not an unhealthy install.
    """
    if not unit_file_present:
        if home_present:
            return ComponentStatus(
                state="staged",
                detail=(
                    "pointer home files present, no unit "
                    "(residual after uninstall or staged payload); OK"
                ),
                exit_code=0,
                label="pointer",
            )
        return ComponentStatus(
            state="not_installed",
            detail="pointer not installed (optional; keyboard-only is fine)",
            exit_code=0,
            label="pointer",
        )
    # Unit registered — health matters
    if is_failed:
        return ComponentStatus(
            state="failed",
            detail="pointer unit present but failed",
            exit_code=1,
            label="pointer",
        )
    if is_active is True:
        return ComponentStatus(
            state="active",
            detail="paperpointer.service active",
            exit_code=0,
            label="pointer",
        )
    if is_active is False:
        return ComponentStatus(
            state="inactive",
            detail="pointer unit present but not active",
            exit_code=1,
            label="pointer",
        )
    return ComponentStatus(
        state="unknown",
        detail="pointer unit present; active state unknown",
        exit_code=0,
        label="pointer",
    )


def classify_keyboard_status(
    *,
    service_present: bool,
    service_active: Optional[bool],
    service_failed: bool = False,
) -> ComponentStatus:
    """Classify BT keyboard service health (same state model as pointer)."""
    if not service_present:
        return ComponentStatus(
            state="not_installed",
            detail="keyboard BT service not installed (optional for pointer-only)",
            exit_code=0,
            label="keyboard",
        )
    if service_failed:
        return ComponentStatus(
            state="failed",
            detail="keyboard service present but failed",
            exit_code=1,
            label="keyboard",
        )
    if service_active is True:
        return ComponentStatus(
            state="active",
            detail="remarkable-bt-keyboard.service active",
            exit_code=0,
            label="keyboard",
        )
    if service_active is False:
        return ComponentStatus(
            state="inactive",
            detail="keyboard service installed but not active",
            exit_code=1,
            label="keyboard",
        )
    return ComponentStatus(
        state="unknown",
        detail="keyboard service present; active state unknown",
        exit_code=0,
        label="keyboard",
    )


def merge_exit_codes(
    keyboard: ComponentStatus | int,
    pointer: ComponentStatus,
) -> int:
    """Combine section exit codes.

    Accepts a legacy int keyboard code for older call sites; prefer ComponentStatus.
    Absent/staged components contribute 0 via their exit_code.
    """
    if isinstance(keyboard, int):
        kb_code = keyboard
    else:
        kb_code = keyboard.exit_code
    if kb_code != 0:
        return kb_code
    return pointer.exit_code


def format_status_report(
    keyboard_lines: list[str],
    pointer: ComponentStatus,
    keyboard: ComponentStatus | None = None,
) -> str:
    lines = ["=== keyboard ==="]
    if keyboard is not None:
        lines.append(f"state: {keyboard.state}")
        if keyboard.detail:
            lines.append(f"detail: {keyboard.detail}")
    lines.extend(keyboard_lines if keyboard_lines else ["(no keyboard status)"])
    lines.append("=== pointer ===")
    lines.append(f"state: {pointer.state}")
    if pointer.detail:
        lines.append(f"detail: {pointer.detail}")
    return "\n".join(lines) + "\n"
