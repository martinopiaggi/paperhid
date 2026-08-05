"""Desktop layout API — SSH transport over shared implementation."""
from shared.layout_patcher import (  # noqa: F401
    BACKUP_PATH,
    DEAD_KEY_QT_MAX,
    DEAD_KEY_QT_MIN,
    ENTRY_COUNT,
    ENTRY_SIZE,
    LAYOUT_FILE,
    LIBEPAPER_PATH,
    MOVE_ENTRY_COUNT,
    MOVE_KEYMAP_OFFSET,
    OFFSET_CACHE,
    SCRIPT_DIR,
    US_KEYMAP_OFFSET,
    _US_PLAIN,
    _US_PLAIN_LETTERS,
    _patch_binary,
    find_all_keymap_tables,
    find_keymap_offset,
)
from shared import layout_patcher as _impl
from shared.transport import as_transport


def apply_layout(ssh, layout_key, status_cb=None, force=False):
    if not force:
        try:
            from core import device as device_mod
            info = device_mod.detect(ssh)
            if not info.get("supports_layout_patch"):
                label = info.get("label") or info.get("model") or "this device"
                raise RuntimeError(
                    f"Refusing layout patch on {label}: "
                    "supported on reMarkable Move and Paper Pro only."
                )
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("Refusing layout patch: could not verify device model")

    return _impl.apply_layout(
        as_transport(ssh), layout_key, status_cb=status_cb, restart_ui=True
    )


def restore_original(ssh):
    return _impl.restore_original(as_transport(ssh), restart_ui=True)


def read_device_layout(ssh):
    return _impl.read_device_layout(as_transport(ssh))
