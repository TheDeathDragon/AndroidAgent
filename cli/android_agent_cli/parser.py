from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .errors import UsageError
from .spec import (
    COMMANDS,
    CommandSpec,
    FlagSpec,
    GLOBAL_FLAGS,
    GLOBAL_RENAMED_FLAGS,
    HOME_FLAGS,
    resolve_command,
    valid_flags_summary,
)

_NEGATIVE_NUMBER_RE = re.compile(r"^-(?:\d+(?:\.\d+)?|\.\d+)$")
_VERSION_FLAGS = frozenset(("-v", "-V", "--version"))


@dataclass(frozen=True)
class ParsedInvocation:
    command: str | None
    spec: CommandSpec | None
    flags: dict[str, Any]
    globals: dict[str, Any]
    positionals: list[str]
    help_requested: bool
    version_requested: bool


def _build_flag_lookup(flags: tuple[FlagSpec, ...]) -> dict[str, tuple[FlagSpec, bool]]:
    lookup: dict[str, tuple[FlagSpec, bool]] = {}
    for fl in flags:
        lookup[fl.long] = (fl, False)
        if fl.short:
            lookup[fl.short] = (fl, False)
        if fl.negated:
            lookup[fl.negated] = (fl, True)
    return lookup


def _coerce_value(fl: FlagSpec, raw: str, token: str, ctx_name: str) -> Any:
    if fl.kind == "str":
        val: Any = raw
    elif fl.kind == "int":
        try:
            val = int(raw)
        except ValueError as exc:
            raise UsageError(
                f"invalid integer for {token}: {raw}",
                help_hints=[f"Provide an integer value for {fl.long} in `{ctx_name}`"],
            ) from exc
    elif fl.kind == "float":
        try:
            val = float(raw)
        except ValueError as exc:
            raise UsageError(
                f"invalid number for {token}: {raw}",
                help_hints=[f"Provide a numeric value for {fl.long} in `{ctx_name}`"],
            ) from exc
        if not math.isfinite(val):
            raise UsageError(
                f"non-finite number for {token}: {raw}",
                help_hints=[f"Provide a finite number for {fl.long}"],
            )
    else:
        val = raw

    if fl.choices is not None:
        normalized = str(val).lower() if fl.name in ("format", "target", "scope") else str(val)
        if normalized not in fl.choices:
            raise UsageError(
                f"invalid value for {token}: {raw}",
                help_hints=[f"Valid values for {fl.long}: {', '.join(fl.choices)}"],
            )
        val = normalized

    if fl.min_val is not None and val < fl.min_val:
        bound = int(fl.min_val) if fl.kind == "int" else fl.min_val
        raise UsageError(
            f"{token} must be >= {bound} (got {val})",
            help_hints=[f"Use {fl.long} with a value >= {bound}"],
        )
    if fl.max_val is not None and val > fl.max_val:
        bound = int(fl.max_val) if fl.kind == "int" else fl.max_val
        raise UsageError(
            f"{token} must be <= {bound} (got {val})",
            help_hints=[f"Use {fl.long} with a value <= {bound}"],
        )
    return val


def _split_flag_token(token: str) -> tuple[str, str | None]:
    if token.startswith("--") and "=" in token:
        name, val = token.split("=", 1)
        return name, val
    if len(token) > 2 and token.startswith("-") and not token.startswith("--") and token[2] == "=":
        return token[:2], token[3:]
    return token, None


def _unknown_flag_error(flag_name: str, spec: CommandSpec | None) -> UsageError:
    ctx = spec.name if spec is not None else "android-agent"
    if spec is not None and flag_name in spec.renamed_flags:
        _, hint = spec.renamed_flags[flag_name]
        return UsageError(
            f"unknown flag {flag_name} for `{ctx}`",
            help_hints=[hint, f"Valid flags for `{ctx}`: {valid_flags_summary(spec)}"],
        )
    if flag_name in GLOBAL_RENAMED_FLAGS:
        _, hint = GLOBAL_RENAMED_FLAGS[flag_name]
        return UsageError(
            f"unknown flag {flag_name} for `{ctx}`",
            help_hints=[hint, f"Valid flags for `{ctx}`: {valid_flags_summary(spec)}"],
        )
    return UsageError(
        f"unknown flag {flag_name} for `{ctx}`",
        help_hints=[f"Valid flags for `{ctx}`: {valid_flags_summary(spec)}"],
    )


def _is_flag_like(token: str, accepts_numeric_pos: bool) -> bool:
    if not token.startswith("-") or token == "-":
        return False
    if accepts_numeric_pos and _NEGATIVE_NUMBER_RE.match(token):
        return False
    return True


def parse_argv(argv: list[str]) -> ParsedInvocation:
    global_lookup = _build_flag_lookup(GLOBAL_FLAGS)
    home_lookup = _build_flag_lookup(HOME_FLAGS)

    globals_dict: dict[str, Any] = {f.name: f.default for f in GLOBAL_FLAGS}
    spec: CommandSpec | None = None
    cmd_lookup: dict[str, tuple[FlagSpec, bool]] = {}
    flags_dict: dict[str, Any] = {}
    positionals: list[str] = []
    help_requested = False
    version_requested = False
    end_of_flags = False

    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        i += 1

        if end_of_flags:
            positionals.append(token)
            continue

        if token == "--":
            end_of_flags = True
            continue

        if spec is None and token in _VERSION_FLAGS:
            version_requested = True
            continue

        accepts_num = spec.accepts_numeric_positionals if spec is not None else False
        if _is_flag_like(token, accepts_num):
            flag_name, inline_val = _split_flag_token(token)
            entry = global_lookup.get(flag_name)
            is_global = entry is not None
            if entry is None:
                if spec is None:
                    entry = home_lookup.get(flag_name)
                else:
                    entry = cmd_lookup.get(flag_name)

            if entry is None:
                raise _unknown_flag_error(flag_name, spec)

            fl, is_negated = entry
            ctx_name = spec.name if spec is not None else "android-agent"
            if fl.kind == "bool":
                if inline_val is not None:
                    raise UsageError(
                        f"flag {flag_name} does not accept a value",
                        help_hints=[f"Pass {flag_name} without '=...'"],
                    )
                value: Any = False if is_negated else True
            else:
                if inline_val is not None:
                    raw = inline_val
                else:
                    if i >= n:
                        raise UsageError(
                            f"missing value for {flag_name}",
                            help_hints=[f"Provide {fl.value_name or 'VALUE'} after {flag_name}"],
                        )
                    raw = argv[i]
                    i += 1
                value = _coerce_value(fl, raw, flag_name, ctx_name)

            if is_global:
                if fl.name == "help":
                    help_requested = True
                globals_dict[fl.name] = value
            else:
                flags_dict[fl.name] = value
            continue

        if spec is None:
            resolved = resolve_command(token)
            if resolved is None:
                valid_cmds = ", ".join(c.name for c in COMMANDS)
                raise UsageError(
                    f"unknown command: {token}",
                    help_hints=[f"Valid commands: {valid_cmds}"],
                )
            spec = resolved
            cmd_lookup = _build_flag_lookup(spec.flags)
            # Reject home-only flags (like --hook) if placed before a subcommand.
            for stray in list(flags_dict.keys()):
                raise _unknown_flag_error(f"--{stray}", spec)
            flags_dict = {f.name: f.default for f in spec.flags}
            continue

        positionals.append(token)

    if spec is None:
        merged_home = {f.name: f.default for f in HOME_FLAGS}
        merged_home.update(flags_dict)
        flags_dict = merged_home

    if version_requested and spec is None and not help_requested:
        return ParsedInvocation(
            command=None,
            spec=None,
            flags=flags_dict,
            globals=globals_dict,
            positionals=positionals,
            help_requested=False,
            version_requested=True,
        )

    if help_requested:
        return ParsedInvocation(
            command=spec.name if spec else None,
            spec=spec,
            flags=flags_dict,
            globals=globals_dict,
            positionals=positionals,
            help_requested=True,
            version_requested=False,
        )

    if spec is not None:
        if len(positionals) < spec.min_args:
            raise UsageError(
                f"missing required argument for `{spec.name}`",
                help_hints=[f"Usage: {spec.usage}"],
            )
        if spec.max_args is not None and len(positionals) > spec.max_args:
            extra = " ".join(positionals[spec.max_args :])
            raise UsageError(
                f"unexpected argument for `{spec.name}`: {extra}",
                help_hints=[f"Usage: {spec.usage}"],
            )

    return ParsedInvocation(
        command=spec.name if spec else None,
        spec=spec,
        flags=flags_dict,
        globals=globals_dict,
        positionals=positionals,
        help_requested=False,
        version_requested=False,
    )
