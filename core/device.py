"""Detect reMarkable device model over SSH.

PaperHid targets the reMarkable Paper Pro (codename Ferrari). Detection also
recognizes Move. Keyboard language patches the keymap in libepaper.so.
On-device native app (XOVI/AppLoad) is supported on Paper Pro and Move.
"""
from __future__ import annotations

import re

# Known product families. Detection is best-effort and case-insensitive.
MODEL_MOVE = "move"
MODEL_PAPER_PRO = "paper_pro"
MODEL_PAPER_PRO_MOVE = "paper_pro_move"  # reserved if ever distinguished
MODEL_UNKNOWN = "unknown"

# Substrings found in device-tree model / soc machine / hostname
_PAPER_PRO_MARKERS = (
    "ferrari",  # Paper Pro SoC/board codename
    "paper pro",
    "paperpro",
)
_MOVE_MARKERS = (
    "chill",  # Move codename (when present)
    "remarkable move",
    " rm move",
)


def detect(ssh, timeout=8):
    """Return a device info dict from a connected SSH session.

    Keys:
      model: one of MODEL_* constants
      label: human-readable product name
      hostname: device hostname
      kernel: uname -a (short)
      img_version: IMG_VERSION from os-release if present
      raw_model: concatenated model strings from the device
      supports_bt_keyboard_service: True when btnxpuart stack is expected
      supports_layout_patch: True for Move and Paper Pro (libepaper.so keymap)
      supports_native_app: True only for Move-oriented XOVI/AppLoad path
    """
    info = {
        "model": MODEL_UNKNOWN,
        "label": "reMarkable",
        "hostname": "",
        "kernel": "",
        "img_version": "",
        "raw_model": "",
        # Default: try the service; hardware differs. RM1/RM2 lack BT and fail soft.
        "supports_bt_keyboard_service": True,
        "supports_layout_patch": False,
        "supports_native_app": False,
    }

    try:
        out, _, _ = ssh.exec(
            "cat /proc/device-tree/model 2>/dev/null; "
            "echo; cat /sys/devices/soc0/machine 2>/dev/null; "
            "echo; hostname; echo; uname -a; echo; "
            "grep -E '^IMG_VERSION=' /etc/os-release 2>/dev/null || true",
            timeout=timeout,
        )
    except Exception:
        return info

    # device-tree model may contain a trailing NUL
    text = (out or "").replace("\x00", " ").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blob = " ".join(lines).lower()
    info["raw_model"] = " ".join(lines)

    if lines:
        info["hostname"] = _pick_hostname(lines)
        for ln in lines:
            if ln.startswith("Linux "):
                info["kernel"] = ln
            if ln.startswith("IMG_VERSION="):
                info["img_version"] = ln.split("=", 1)[1].strip().strip('"')

    model = _classify(blob, info["hostname"].lower())
    info["model"] = model
    info["label"] = _label_for(model, info["raw_model"])
    info["supports_layout_patch"] = model in (MODEL_MOVE, MODEL_PAPER_PRO)
    # XOVI/AppLoad are supported on Paper Pro (primary AppLoad target) and Move.
    info["supports_native_app"] = model in (MODEL_MOVE, MODEL_PAPER_PRO)
    return info


def _is_hostname_token(ln: str) -> bool:
    """True for short single-token strings that look like hostnames."""
    if not ln or " " in ln or ln.startswith("Linux") or ln.startswith("IMG_"):
        return False
    if len(ln) >= 64:
        return False
    return bool(re.match(r"^[A-Za-z0-9._-]+$", ln))


def _pick_hostname(lines):
    # Prefer known reMarkable hostname patterns
    for ln in lines:
        if not _is_hostname_token(ln):
            continue
        low = ln.lower()
        if (
            "remarkable" in low
            or "imx" in low
            or "ferrari" in low
            or "chill" in low
            or ln.endswith("-ferrari")
            or ln.endswith("-chill")
        ):
            return ln
    for ln in lines:
        if ln in ("imx8mm-ferrari", "reMarkable", "remarkable"):
            return ln
    # Last resort: first plausible short hostname token
    for ln in lines:
        if _is_hostname_token(ln):
            return ln
    return ""


def _classify(blob, hostname):
    combined = f"{blob} {hostname}"
    for marker in _PAPER_PRO_MARKERS:
        if marker in combined:
            return MODEL_PAPER_PRO
    for marker in _MOVE_MARKERS:
        if marker in combined:
            return MODEL_MOVE
    # Hostname / residual fallbacks used on stock firmware
    if "ferrari" in combined:
        return MODEL_PAPER_PRO
    if "chill" in combined or ("move" in combined and "remarkable" in combined):
        return MODEL_MOVE
    return MODEL_UNKNOWN


def _label_for(model, raw):
    if model == MODEL_PAPER_PRO:
        return "reMarkable Paper Pro"
    if model == MODEL_MOVE:
        return "reMarkable Move"
    if raw:
        first = raw.split()[0:3]
        return " ".join(first) if first else "reMarkable"
    return "reMarkable"


def is_paper_pro(info):
    return (info or {}).get("model") == MODEL_PAPER_PRO


def is_move(info):
    return (info or {}).get("model") == MODEL_MOVE
