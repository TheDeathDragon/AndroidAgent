from __future__ import annotations

import math
import re
from typing import Any

_UNQUOTED_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_NUMERIC_LITERAL_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_LEADING_ZERO_RE = re.compile(r"^0\d+$")
_SPECIAL_CHARS = frozenset(',:"\\[]{}')


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _escape_string(text: str) -> str:
    out: list[str] = ['"']
    for ch in text:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _needs_string_quote(text: str) -> bool:
    if not text:
        return True
    if text[0].isspace() or text[-1].isspace():
        return True
    if text in ("true", "false", "null"):
        return True
    if text.startswith("-"):
        return True
    if _NUMERIC_LITERAL_RE.match(text) or _LEADING_ZERO_RE.match(text):
        return True
    for ch in text:
        if ch in _SPECIAL_CHARS or ord(ch) < 0x20:
            return True
    return False


def _format_key(key: str) -> str:
    if _UNQUOTED_KEY_RE.match(key):
        return key
    return _escape_string(key)


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        return "null"
    if value == 0.0:
        return "0"
    if value.is_integer() and abs(value) < 1e16:
        return str(int(value))
    text = repr(value)
    if ("e" in text or "E" in text) and 1e-12 < abs(value) < 1e15:
        text = f"{value:.15f}".rstrip("0").rstrip(".")
    return text


def _format_primitive(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_float(value)
    if isinstance(value, str):
        return _escape_string(value) if _needs_string_quote(value) else value
    return _format_primitive(str(value))


def _is_tabular(items: list[Any]) -> list[str] | None:
    if not items:
        return None
    first = items[0]
    if not isinstance(first, dict) or not first:
        return None
    keys = list(first.keys())
    if not all(isinstance(k, str) for k in keys):
        return None
    for item in items:
        if not isinstance(item, dict) or list(item.keys()) != keys:
            return None
        if not all(_is_primitive(v) for v in item.values()):
            return None
    return keys


def _encode_mapping(mapping: dict[Any, Any], indent: int, lines: list[str]) -> None:
    prefix = " " * indent
    for raw_key, value in mapping.items():
        key = _format_key(str(raw_key))
        if _is_primitive(value):
            lines.append(f"{prefix}{key}: {_format_primitive(value)}")
        elif isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            if value:
                _encode_mapping(value, indent + 2, lines)
        elif isinstance(value, list):
            _encode_list(value, indent, lines, key=key)
        else:
            lines.append(f"{prefix}{key}: {_format_primitive(str(value))}")


def _encode_list(
    items: list[Any],
    indent: int,
    lines: list[str],
    key: str | None = None,
) -> None:
    prefix = " " * indent
    label = f"{key}[{len(items)}]" if key is not None else f"[{len(items)}]"
    if not items:
        lines.append(f"{prefix}{label}:")
        return
    if all(_is_primitive(v) for v in items):
        joined = ",".join(_format_primitive(v) for v in items)
        lines.append(f"{prefix}{label}: {joined}")
        return
    tabular_keys = _is_tabular(items)
    if tabular_keys is not None:
        header = ",".join(_format_key(k) for k in tabular_keys)
        lines.append(f"{prefix}{label}{{{header}}}:")
        row_prefix = " " * (indent + 2)
        for item in items:
            row = ",".join(_format_primitive(item[k]) for k in tabular_keys)
            lines.append(f"{row_prefix}{row}")
        return
    lines.append(f"{prefix}{label}:")
    _encode_expanded_items(items, indent + 2, lines)


def _encode_expanded_items(items: list[Any], indent: int, lines: list[str]) -> None:
    prefix = " " * indent
    for item in items:
        if _is_primitive(item):
            lines.append(f"{prefix}- {_format_primitive(item)}")
        elif isinstance(item, dict):
            if not item:
                lines.append(f"{prefix}-")
                continue
            entries = list(item.items())
            first_key, first_val = entries[0]
            fk = _format_key(str(first_key))
            if _is_primitive(first_val):
                lines.append(f"{prefix}- {fk}: {_format_primitive(first_val)}")
            elif isinstance(first_val, dict):
                lines.append(f"{prefix}- {fk}:")
                if first_val:
                    _encode_mapping(first_val, indent + 4, lines)
            elif isinstance(first_val, list):
                _encode_inline_list_item(first_val, indent, lines, key=fk)
            else:
                lines.append(f"{prefix}- {fk}: {_format_primitive(str(first_val))}")
            if len(entries) > 1:
                rest = dict(entries[1:])
                _encode_mapping(rest, indent + 2, lines)
        elif isinstance(item, list):
            _encode_inline_list_item(item, indent, lines, key=None)
        else:
            lines.append(f"{prefix}- {_format_primitive(str(item))}")


def _encode_inline_list_item(
    items: list[Any],
    indent: int,
    lines: list[str],
    key: str | None,
) -> None:
    prefix = " " * indent
    label = f"{key}[{len(items)}]" if key is not None else f"[{len(items)}]"
    if not items:
        lines.append(f"{prefix}- {label}:")
        return
    if all(_is_primitive(v) for v in items):
        joined = ",".join(_format_primitive(v) for v in items)
        lines.append(f"{prefix}- {label}: {joined}")
        return
    tabular_keys = _is_tabular(items)
    if tabular_keys is not None:
        header = ",".join(_format_key(k) for k in tabular_keys)
        lines.append(f"{prefix}- {label}{{{header}}}:")
        row_prefix = " " * (indent + 4)
        for row_item in items:
            row = ",".join(_format_primitive(row_item[k]) for k in tabular_keys)
            lines.append(f"{row_prefix}{row}")
        return
    lines.append(f"{prefix}- {label}:")
    _encode_expanded_items(items, indent + 4, lines)


def encode(data: Any) -> str:
    lines: list[str] = []
    if _is_primitive(data):
        lines.append(_format_primitive(data))
    elif isinstance(data, dict):
        if data:
            _encode_mapping(data, 0, lines)
    elif isinstance(data, list):
        _encode_list(data, 0, lines)
    else:
        lines.append(_format_primitive(str(data)))
    cleaned = [line.rstrip(" ") for line in lines]
    if not cleaned:
        return "\n"
    return "\n".join(cleaned) + "\n"


def truncate_text(text: str, limit: int) -> tuple[str, bool, int]:
    total = len(text)
    if total <= limit:
        return text, False, total
    preview = f"{text[:limit]}... (truncated, {total} chars total)"
    return preview, True, total
