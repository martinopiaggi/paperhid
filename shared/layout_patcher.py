"""libepaper.so keymap patch (Move + Paper Pro). Transport-agnostic I/O."""
from __future__ import annotations

import logging
import struct

from shared.constants import (
    BACKUP_PATH,
    LAYOUT_FILE,
    LIBEPAPER_PATH,
    OFFSET_CACHE,
    SCRIPT_DIR,
)
from shared.transport import Transport

log = logging.getLogger("paperhid.layout_patcher")

ENTRY_SIZE = 16
MOVE_KEYMAP_OFFSET = 0x0250b0
MOVE_ENTRY_COUNT = 211
US_KEYMAP_OFFSET = MOVE_KEYMAP_OFFSET
ENTRY_COUNT = MOVE_ENTRY_COUNT

_KNOWN_OFFSETS = (MOVE_KEYMAP_OFFSET, 0x26708)
_MIN_PATCH_LEVELS = 20
_MIN_TABLE_SCORE = 80

DEAD_KEY_QT_MIN = 0x01001250
DEAD_KEY_QT_MAX = 0x01001263

_BRACKET_US = {
    26: (0x5B, 0x7B, 0x5B, 0x7B),
    27: (0x5D, 0x7D, 0x5D, 0x7D),
}

_US_PLAIN = {
    16: 0x71, 17: 0x77, 18: 0x65, 19: 0x72, 20: 0x74,
    21: 0x79, 22: 0x75, 23: 0x69, 24: 0x6F, 25: 0x70,
    30: 0x61, 31: 0x73, 32: 0x64, 33: 0x66, 34: 0x67,
    35: 0x68, 36: 0x6A, 37: 0x6B, 38: 0x6C,
    44: 0x7A, 45: 0x78, 46: 0x63, 47: 0x76, 48: 0x62,
    49: 0x6E, 50: 0x6D,
}
_US_PLAIN_LETTERS = _US_PLAIN


def _layout_mappings(layout_key: str):
    try:
        from tools.generate_qmap import get_layout_mappings
    except ImportError:
        import os
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from tools.generate_qmap import get_layout_mappings
    return get_layout_mappings(layout_key)


def _plausible_entry(data: bytes, off: int) -> bool:
    if off < 0 or off + ENTRY_SIZE > len(data):
        return False
    kc = struct.unpack_from("<H", data, off)[0]
    qt = struct.unpack_from("<I", data, off + 4)[0]
    mod, flags = data[off + 8], data[off + 9]
    special = struct.unpack_from("<H", data, off + 10)[0]
    if not (1 <= kc <= 0x300) or mod > 0x3F or flags > 0x1F or special > 8:
        return False
    if qt > 0x02000000 and not (DEAD_KEY_QT_MIN <= qt <= DEAD_KEY_QT_MAX):
        return False
    return True


def _score_table(data: bytes, offset: int, max_entries: int = 320):
    if offset < 0 or offset + ENTRY_SIZE * 40 > len(data):
        return -1, 0

    entries = []
    for i in range(max_entries):
        off = offset + i * ENTRY_SIZE
        if not _plausible_entry(data, off):
            break
        kc = struct.unpack_from("<H", data, off)[0]
        uni = struct.unpack_from("<H", data, off + 2)[0]
        qt = struct.unpack_from("<I", data, off + 4)[0]
        mod = data[off + 8]
        special = struct.unpack_from("<H", data, off + 10)[0]
        entries.append((kc, uni, qt, mod, special))

    n = len(entries)
    if n < 60:
        return -1, 0

    by_kc: dict[int, list] = {}
    for kc, uni, qt, mod, special in entries:
        by_kc.setdefault(kc, []).append((uni, qt, mod, special))

    for need in list(range(2, 12)) + list(range(16, 26)) + list(range(30, 39)):
        if need not in by_kc:
            return -1, 0
    if sum(1 for kc in by_kc if kc > 80) / len(by_kc) > 0.35:
        return -1, 0

    score = letter_hits = 0
    for kc, expected in _US_PLAIN.items():
        if kc not in by_kc:
            continue
        unis = {u for u, *_ in by_kc[kc]}
        if expected in unis or (expected - 0x20) in unis:
            letter_hits += 1
            score += 3
    if letter_hits < 15:
        return -1, 0

    score += min(sum(1 for v in by_kc.values() if len(v) >= 2), 60)

    if 40 in by_kc:
        levels = sorted(by_kc[40], key=lambda t: t[2])
        plain_u = levels[0][0]
        shift_u = levels[1][0] if len(levels) > 1 else None
        if plain_u == 0x27:
            score += 80
        if shift_u == 0x22:
            score += 40
        if plain_u >= 0xC0:
            score -= 60

    if 26 in by_kc:
        for uni, qt, mod, special in by_kc[26]:
            if DEAD_KEY_QT_MIN <= qt <= DEAD_KEY_QT_MAX or special == 1:
                score += 50
                break
            if uni in (0x5B, 0x7B):
                score += 10
                break

    first_kc = entries[0][0]
    if first_kc in (1, 2):
        score += 40
    elif first_kc > 60:
        score -= 40
    if 90 <= n <= 220:
        score += 25
    elif 220 < n <= 320:
        score += 5
    if max(by_kc) <= 60:
        score += 20

    return (score, n) if score >= 50 else (-1, 0)


def find_all_keymap_tables(data: bytes, min_score: int = _MIN_TABLE_SCORE):
    found: dict[int, tuple[int, int]] = {}

    def consider(off: int):
        sc, cnt = _score_table(data, off)
        if sc >= min_score and (off not in found or sc > found[off][1]):
            found[off] = (cnt, sc)

    for off in _KNOWN_OFFSETS:
        consider(off)

    for needle in (
        struct.pack("<HH", 16, 0x71),
        struct.pack("<HH", 30, 0x61),
        struct.pack("<HH", 18, 0x65),
    ):
        start = 0
        while True:
            idx = data.find(needle, start)
            if idx < 0:
                break
            start = idx + 1
            table_start = idx
            for _ in range(300):
                prev = table_start - ENTRY_SIZE
                if prev < 0 or not _plausible_entry(data, prev):
                    break
                table_start = prev
            consider(table_start)

    tables = [(off, cnt, sc) for off, (cnt, sc) in found.items()]
    tables.sort(key=lambda t: (-t[2], t[0]))
    return tables


def find_keymap_offset(data: bytes):
    tables = find_all_keymap_tables(data, min_score=50)
    if not tables:
        log.warning("no keymap table in libepaper.so (%d bytes)", len(data))
        return None
    off, cnt, sc = tables[0]
    log.info("keymap at 0x%X (%d entries, score=%d)", off, cnt, sc)
    return off, cnt


def _layout_targets(layout_key: str) -> dict:
    letters, punct = _layout_mappings(layout_key)
    targets = {}
    for kc, lo, hi, qt in letters:
        targets[kc] = (lo, hi, qt, qt)
    for kc, pu, pq, su, sq in punct:
        targets[kc] = (pu, su, pq, sq)
    for kc, vals in _BRACKET_US.items():
        targets.setdefault(kc, vals)
    return targets


def _patch_level(buf: bytearray, offset: int, new_uni: int, new_qt: int) -> None:
    struct.pack_into("<H", buf, offset + 2, new_uni)
    old_qt = struct.unpack_from("<I", buf, offset + 4)[0]
    want_dead = DEAD_KEY_QT_MIN <= new_qt <= DEAD_KEY_QT_MAX
    was_dead = DEAD_KEY_QT_MIN <= old_qt <= DEAD_KEY_QT_MAX
    if want_dead:
        struct.pack_into("<I", buf, offset + 4, new_qt)
        struct.pack_into("<H", buf, offset + 10, 1)
    elif was_dead:
        struct.pack_into("<I", buf, offset + 4, new_qt)
        struct.pack_into("<H", buf, offset + 10, 0)
    elif new_qt < 0x01000000:
        struct.pack_into("<I", buf, offset + 4, new_qt)


def _patch_one_table(buf: bytearray, layout_key: str, table_off: int, entry_count: int) -> int:
    targets = _layout_targets(layout_key)
    if not targets:
        return 0

    by_kc: dict[int, list[tuple[int, int]]] = {}
    for i in range(entry_count):
        off = table_off + i * ENTRY_SIZE
        if off + ENTRY_SIZE > len(buf):
            break
        kc = struct.unpack_from("<H", buf, off)[0]
        if kc in targets:
            by_kc.setdefault(kc, []).append((off, buf[off + 8]))

    n = 0
    for kc, (plain_u, shift_u, plain_qt, shift_qt) in targets.items():
        rows = by_kc.get(kc) or []
        if not rows:
            continue
        rows.sort(key=lambda r: r[1])
        levels, seen = [], set()
        for off, mod in rows:
            if mod not in seen:
                seen.add(mod)
                levels.append(off)
        _patch_level(buf, levels[0], plain_u, plain_qt)
        n += 1
        if len(levels) >= 2:
            _patch_level(buf, levels[1], shift_u, shift_qt)
            n += 1
    return n


def _patch_binary(data: bytes, layout_key: str, keymap_offset=None, entry_count=None):
    if keymap_offset is not None and entry_count is not None:
        tables = [(keymap_offset, entry_count, 0)]
    else:
        tables = find_all_keymap_tables(data)
        if not tables:
            found = find_keymap_offset(data)
            tables = [(found[0], found[1], 0)] if found else []
    if not tables:
        return data, 0

    best = tables[0][2]
    to_patch = [t for t in tables if t[2] >= max(best - 30, _MIN_TABLE_SCORE)] or [tables[0]]
    buf = bytearray(data)
    total = 0
    for off, cnt, sc in to_patch:
        levels = _patch_one_table(buf, layout_key, off, cnt)
        total += levels
        log.info(
            "layout=%s table=0x%X entries=%d score=%s levels=%d",
            layout_key, off, cnt, sc, levels,
        )
    return bytes(buf), total


def _recover(t: Transport, soft: bool = False) -> None:
    try:
        if soft and t.is_connected:
            try:
                t.run("true", timeout=5)
                return
            except Exception:
                pass
        t.ensure_connected(timeout=15, retries=5, pause=2.0)
    except Exception as e:
        log.warning("transport recover: %s", e)


def _xovi_available(t: Transport) -> bool:
    """True when XOVI can re-tether xochitl (keeps Settings/cursor QMD patches)."""
    try:
        _, _, code = t.run(
            "test -x /home/root/xovi/start && test -f /home/root/xovi/xovi.so",
            timeout=5,
        )
        return code == 0
    except Exception:
        return False


def restart_display(t: Transport, status=None) -> None:
    """Start xochitl, preferring XOVI so Help/cursor QMD patches stay active.

    Plain ``systemctl start xochitl`` drops XOVI and makes Settings → Help look
    empty even though paperpointer-settings.qmd is still on disk.
    """
    def _status(msg: str) -> None:
        if status:
            status(msg)
        log.info("%s", msg)

    _status("Starting display app...")
    try:
        t.run("systemctl reset-failed xochitl.service 2>/dev/null || true", timeout=10)
    except Exception:
        pass
    if _xovi_available(t):
        out, err, code = t.run("/home/root/xovi/start", timeout=90)
        if code != 0:
            log.warning("xovi start failed (%s): %s", code, (err or out)[:300])
            # Fall back to stock so the tablet is never left without UI.
            t.run("systemctl start xochitl", timeout=30)
    else:
        t.run("systemctl start xochitl", timeout=30)
    # Brief settle so callers can re-check stability.
    try:
        t.run("sleep 2", timeout=5)
    except Exception:
        pass


def _deploy_binary(t: Transport, binary_data: bytes, status, restart_ui: bool) -> None:
    if restart_ui:
        status("Stopping display app for library update...")
        try:
            t.run("systemctl stop xochitl", timeout=15)
        except Exception as e:
            log.warning("stop xochitl: %s", e)
            _recover(t)

    try:
        try:
            t.run("mount -o remount,rw /", timeout=10)
        except Exception:
            _recover(t)
            t.run("mount -o remount,rw /", timeout=10)
        try:
            t.write_bytes(LIBEPAPER_PATH, binary_data)
            t.run("sync", timeout=5)
        finally:
            try:
                t.run("mount -o remount,ro /", timeout=10)
            except Exception:
                _recover(t)
                try:
                    t.run("mount -o remount,ro /", timeout=10)
                except Exception:
                    pass
    finally:
        if restart_ui:
            try:
                restart_display(t, status=status)
            except Exception as e:
                log.warning("restart display: %s", e)
                _recover(t)
                try:
                    restart_display(t, status=status)
                except Exception as e2:
                    log.warning("restart display retry: %s", e2)
            _recover(t, soft=True)


def _cleanup_legacy_qmap_bits(t: Transport) -> None:
    t.run(
        "rm -f /etc/systemd/system/xochitl.service.d/paperwriter-keymap.conf "
        f"{SCRIPT_DIR}/paperwriter-keymap.conf 2>/dev/null; "
        "rmdir /etc/systemd/system/xochitl.service.d 2>/dev/null; "
        f"rm -rf {SCRIPT_DIR}/keymaps 2>/dev/null; true",
        timeout=10,
    )


def apply_layout(
    t: Transport,
    layout_key: str,
    status_cb=None,
    restart_ui: bool = True,
) -> None:
    def status(msg: str) -> None:
        if status_cb:
            status_cb(msg)
        log.info("%s", msg)

    layout_key = (layout_key or "us").strip().lower()
    t.run(f"mkdir -p {SCRIPT_DIR}", timeout=10)
    _cleanup_legacy_qmap_bits(t)

    if not t.exists(BACKUP_PATH):
        status("Backing up original libepaper.so...")
        out, err, c = t.run(f"cp {LIBEPAPER_PATH} {BACKUP_PATH}", timeout=15)
        if c != 0:
            raise RuntimeError(f"Cannot backup libepaper.so: {err or out}")

    status("Downloading library...")
    original = t.read_bytes(BACKUP_PATH)

    status("Locating keymap...")
    found = find_keymap_offset(original)
    if not found:
        raise RuntimeError(
            "Could not find keyboard keymap in libepaper.so on this firmware."
        )
    km_off, km_count = found
    status(f"Keymap at 0x{km_off:X} ({km_count} entries)")
    try:
        t.write_text(OFFSET_CACHE, f"0x{km_off:x} {km_count}\n")
    except Exception:
        pass

    # Prefer live binary if size diverged (firmware drift)
    try:
        live = t.read_bytes(LIBEPAPER_PATH)
        if len(live) != len(original):
            original = live
            found = find_keymap_offset(original)
            if found:
                km_off, km_count = found
    except Exception:
        pass

    status(f"Patching for {layout_key}...")
    patched, levels = _patch_binary(original, layout_key)
    if levels < _MIN_PATCH_LEVELS:
        raise RuntimeError(
            f"Layout patch only matched {levels} key levels "
            f"(need at least {_MIN_PATCH_LEVELS}); refusing partial write."
        )

    t.write_text(LAYOUT_FILE, layout_key + "\n")
    _deploy_binary(t, patched, status, restart_ui=restart_ui)
    status(f"Layout {layout_key} applied ({levels} keys)")


def restore_original(t: Transport, restart_ui: bool = True) -> None:
    if not t.exists(BACKUP_PATH):
        _cleanup_legacy_qmap_bits(t)
        return

    original = t.read_bytes(BACKUP_PATH)
    try:
        t.run(f"rm -f {LAYOUT_FILE} {OFFSET_CACHE}", timeout=5)
    except Exception:
        pass

    _deploy_binary(t, original, lambda _m: None, restart_ui=restart_ui)

    try:
        t.run(f"rm -f {BACKUP_PATH} {LAYOUT_FILE} {OFFSET_CACHE}", timeout=5)
    except Exception:
        _recover(t)
        t.run(f"rm -f {BACKUP_PATH} {LAYOUT_FILE} {OFFSET_CACHE}", timeout=5)
    _cleanup_legacy_qmap_bits(t)


def read_device_layout(t: Transport) -> str | None:
    try:
        out, _, code = t.run(f"cat {LAYOUT_FILE} 2>/dev/null", timeout=5)
        if code != 0:
            return None
        key = (out or "").strip().lower()
        return key or None
    except Exception:
        return None


def read_layout_display_name(t: Transport, layouts_list) -> str:
    key = read_device_layout(t) or ""
    if not key:
        return ""
    for display, k in layouts_list:
        if k == key:
            return display
    return ""
