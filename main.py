import argparse
import sys
import tkinter as tk

from core.logutil import get_logger, setup_logging
from ui.app import App

log = get_logger("main")


def main(argv=None):
    parser = argparse.ArgumentParser(description="PaperHid desktop app")
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Less terminal output (warnings/errors only)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug logging (includes SSH command traces)",
    )
    args = parser.parse_args(argv)

    setup_logging(verbose=not args.quiet, debug=args.debug)
    log.info("PaperHid starting (python %s)", sys.version.split()[0])

    root = tk.Tk()
    App(root)
    log.info("UI ready — connect to your Paper Pro over USB")
    try:
        root.mainloop()
    finally:
        log.info("PaperHid exited")


if __name__ == "__main__":
    main()
