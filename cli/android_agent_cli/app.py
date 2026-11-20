from __future__ import annotations

import os
import sys
import traceback
from typing import TextIO

from ._version import VERSION
from .errors import AxiError
from .handlers import DISPATCH_TABLE
from .parser import parse_argv
from .spec import render_command_help, render_top_level_help
from .toon import encode


def _write_stdout(text: str, stream: TextIO) -> None:
    stream.write(text)
    stream.flush()


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    hook_mode = "--hook" in args
    try:
        inv = parse_argv(args)
        if inv.version_requested:
            _write_stdout(f"{VERSION}\n", out)
            return 0
        if inv.help_requested:
            help_text = render_command_help(inv.spec) if inv.spec is not None else render_top_level_help()
            _write_stdout(help_text, out)
            return 0

        handler = DISPATCH_TABLE[inv.command]
        result = handler(inv)
        if isinstance(result, str):
            _write_stdout(result, out)
        else:
            _write_stdout(encode(result), out)
        return 0
    except BrokenPipeError:
        return 0
    except AxiError as exc:
        try:
            _write_stdout(encode(exc.to_payload()), out)
        except BrokenPipeError:
            return 0
        if os.environ.get("ANDROID_AGENT_DEBUG") == "1":
            traceback.print_exc(file=err)
        return 0 if (hook_mode and exc.exit_code == 1) else exc.exit_code
    except KeyboardInterrupt:
        try:
            _write_stdout(encode({"error": "interrupted"}), out)
        except BrokenPipeError:
            return 0
        return 130
    except Exception as exc:
        try:
            _write_stdout(
                encode(
                    {
                        "error": f"unexpected error: {exc}",
                        "help": "Set ANDROID_AGENT_DEBUG=1 to inspect diagnostics on stderr",
                    }
                ),
                out,
            )
        except BrokenPipeError:
            return 0
        if os.environ.get("ANDROID_AGENT_DEBUG") == "1":
            traceback.print_exc(file=err)
        return 0 if hook_mode else 1
