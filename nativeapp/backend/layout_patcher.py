"""On-device layout API — local transport over shared implementation."""
from shared.layout_patcher import (  # noqa: F401
    BACKUP_PATH,
    LAYOUT_FILE,
    LIBEPAPER_PATH,
    OFFSET_CACHE,
    SCRIPT_DIR,
)
from shared import layout_patcher as _impl
from shared.transport import LocalTransport

_t = LocalTransport()


def apply_layout(layout_key, status_cb=None):
    return _impl.apply_layout(
        _t, layout_key, status_cb=status_cb, restart_ui=False
    )


def restore_original():
    return _impl.restore_original(_t, restart_ui=False)


def read_current_layout_key():
    return _impl.read_device_layout(_t) or ""


def read_current_layout_display_name(layouts_list):
    return _impl.read_layout_display_name(_t, layouts_list)
