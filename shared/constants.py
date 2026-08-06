"""Paths and unit names shared by desktop and on-device code.

Product name is PaperHid. On-device paths and unit basenames that still say
``paperwriter`` / ``paperpointer`` are intentional runtime locations for
existing installs — change only with a migration path.
"""

# Live install layout (do not rename without migrating tablet state).
SCRIPT_DIR = "/home/root/.paperwriter"
KEYBOARD_MAC_PATH = "/home/root/.paperwriter-keyboard"
LAYOUT_FILE = "/home/root/.paperwriter-layout"

# PaperHid-owned on-device helpers (Settings UI); pointer conf stays under .paperpointer.
PAPERHID_HOME = "/home/root/.paperhid"
PAPERHID_UI = f"{PAPERHID_HOME}/paperhid-ui"

# Residual paths cleaned on uninstall (MoveWriter / older brands).
LEGACY_HOME_DIRS = (
    "/home/root/.movewriter",
)
LEGACY_KEYBOARD_MAC_PATHS = (
    "/home/root/.movewriter-keyboard",
)
LEGACY_NATIVE_APP_IDS = (
    "movewriter",
)

LIBEPAPER_PATH = "/usr/lib/plugins/platforms/libepaper.so"
BACKUP_PATH = f"{SCRIPT_DIR}/libepaper.so.orig"
OFFSET_CACHE = f"{SCRIPT_DIR}/keymap_offset"

SERVICE_NAME = "remarkable-bt-keyboard.service"
BOOTSTRAP_NAME = "paperwriter-bt-bootstrap.service"
SCRIPT_NAME = "bt-keyboard.sh"
LIB_SCRIPT_NAME = "bt-lib.sh"
RESUME_SCRIPT_NAME = "bt-resume.sh"
BOOTSTRAP_SCRIPT_NAME = "paperwriter-bt-bootstrap.sh"
SLEEP_HOOK_NAME = "zz-paperwriter-bt.sh"

SCRIPT_REMOTE_PATH = f"{SCRIPT_DIR}/{SCRIPT_NAME}"
LIB_REMOTE_PATH = f"{SCRIPT_DIR}/{LIB_SCRIPT_NAME}"
RESUME_REMOTE_PATH = f"{SCRIPT_DIR}/{RESUME_SCRIPT_NAME}"
BOOTSTRAP_SCRIPT_REMOTE = f"{SCRIPT_DIR}/{BOOTSTRAP_SCRIPT_NAME}"
SLEEP_HOOK_HOME_PATH = f"{SCRIPT_DIR}/{SLEEP_HOOK_NAME}"
UNIT_HOME_PATH = f"{SCRIPT_DIR}/{SERVICE_NAME}"
BOOTSTRAP_HOME_PATH = f"{SCRIPT_DIR}/{BOOTSTRAP_NAME}"

SERVICE_PERSISTENT_PATH = f"/usr/lib/systemd/system/{SERVICE_NAME}"
BOOTSTRAP_PERSISTENT_PATH = f"/usr/lib/systemd/system/{BOOTSTRAP_NAME}"
ENABLE_SYMLINK_DIR = "/usr/lib/systemd/system/multi-user.target.wants"
ENABLE_SYMLINK_PATH = f"{ENABLE_SYMLINK_DIR}/{SERVICE_NAME}"
BOOTSTRAP_ENABLE_PATH = f"{ENABLE_SYMLINK_DIR}/{BOOTSTRAP_NAME}"
SLEEP_HOOK_DIR = "/usr/lib/systemd/system-sleep"
SLEEP_HOOK_PATH = f"{SLEEP_HOOK_DIR}/{SLEEP_HOOK_NAME}"

SERVICE_VOLATILE_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
BOOTSTRAP_VOLATILE_PATH = f"/etc/systemd/system/{BOOTSTRAP_NAME}"
