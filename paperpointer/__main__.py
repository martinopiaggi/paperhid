"""Not a public entry point — use ``python cli.py pointer …``."""


def main() -> None:
    import sys

    sys.stderr.write(
        "paperpointer is not a standalone CLI. Use: python cli.py pointer <command>\n"
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
