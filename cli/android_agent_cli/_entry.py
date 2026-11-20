import sys

from ._version import VERSION


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 1 and args[0] in ("-v", "-V", "--version"):
        sys.stdout.write(f"{VERSION}\n")
        return 0
    from .app import main as app_main

    return app_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
