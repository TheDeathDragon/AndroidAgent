#!/usr/bin/env python3
import sys

# Avoid importing os/pathlib so bare --version skips non-essential module loads.
_file = __file__
_sep = max(_file.rfind("/"), _file.rfind("\\"))
_cli_dir = _file[:_sep] if _sep >= 0 else "."
if _cli_dir not in sys.path:
    sys.path.insert(0, _cli_dir)

from android_agent_cli._version import VERSION

if len(sys.argv) == 2 and sys.argv[1] in ("-v", "-V", "--version"):
    sys.stdout.write(f"{VERSION}\n")
    raise SystemExit(0)

from android_agent_cli._entry import main

raise SystemExit(main())
