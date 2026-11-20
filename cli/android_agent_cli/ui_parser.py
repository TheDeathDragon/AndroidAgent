from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from .errors import RuntimeAxiError

_BOUNDS_RE = re.compile(r"^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$")


@dataclass
class UiNode:
    node_id: int
    parent_id: int | None
    text: str
    desc: str
    raw_res_id: str
    res_id: str
    cls: str
    pkg: str
    bounds: tuple[int, int, int, int]
    cx: int
    cy: int
    clickable: bool
    long_clickable: bool
    scrollable: bool
    checkable: bool
    checked: bool
    enabled: bool
    focusable: bool
    focused: bool
    selected: bool
    password: bool
    hoisted_label: str = ""

    @property
    def label(self) -> str:
        return self.text or self.desc or self.hoisted_label

    @property
    def is_interactive(self) -> bool:
        return self.clickable or self.long_clickable or self.scrollable or self.checkable

    @property
    def has_area(self) -> bool:
        left, top, right, bottom = self.bounds
        return right > left and bottom > top

    @property
    def act(self) -> str:
        flags: list[str] = []
        if self.clickable:
            flags.append("click")
        if self.long_clickable:
            flags.append("long")
        if self.scrollable:
            flags.append("scroll")
        if self.checkable:
            flags.append("check")
        return "+".join(flags) if flags else "-"

    def bounds_str(self) -> str:
        left, top, right, bottom = self.bounds
        return f"[{left},{top}][{right},{bottom}]"

    def to_row(self, index: int, fields: tuple[str, ...]) -> dict[str, Any]:
        values: dict[str, Any] = {
            "i": index,
            "label": self.label or "-",
            "text": self.text,
            "desc": self.desc,
            "res_id": self.res_id or "-",
            "raw_res_id": self.raw_res_id,
            "class": self.cls.rsplit(".", 1)[-1] if self.cls else "",
            "pkg": self.pkg,
            "bounds": self.bounds_str(),
            "cx": self.cx,
            "cy": self.cy,
            "act": self.act,
            "clickable": self.clickable,
            "long_clickable": self.long_clickable,
            "scrollable": self.scrollable,
            "checkable": self.checkable,
            "checked": self.checked,
            "enabled": self.enabled,
            "focusable": self.focusable,
            "focused": self.focused,
            "selected": self.selected,
        }
        return {f: values[f] for f in fields}


@dataclass(frozen=True)
class UiSnapshot:
    rotation: int
    nodes: list[UiNode]
    children_map: dict[int, list[int]]
    width: int = 0
    height: int = 0

    def screen_size(self) -> tuple[int, int] | None:
        if self.width > 0 and self.height > 0:
            return (self.width, self.height)
        max_x = 0
        max_y = 0
        for node in self.nodes:
            _, _, right, bottom = node.bounds
            if right > max_x:
                max_x = right
            if bottom > max_y:
                max_y = bottom
        if max_x > 0 and max_y > 0:
            return (max_x, max_y)
        return None

    def interactive_nodes(self) -> list[UiNode]:
        return [n for n in self.nodes if n.is_interactive and n.has_area]

    def clickable_ancestor(self, node: UiNode) -> UiNode:
        cur: UiNode | None = node
        while cur is not None:
            if cur.clickable and cur.has_area:
                return cur
            cur = self.nodes[cur.parent_id] if cur.parent_id is not None else None
        return node

    def has_clickable_ancestor(self, node: UiNode) -> bool:
        parent_id = node.parent_id
        while parent_id is not None:
            parent = self.nodes[parent_id]
            if parent.clickable:
                return True
            parent_id = parent.parent_id
        return False


def _short_res_id(raw: str) -> str:
    if ":id/" in raw:
        return raw.split(":id/", 1)[1]
    return raw


def _parse_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() == "true"


def _parse_bounds(raw: str | None) -> tuple[int, int, int, int]:
    if not raw:
        return (0, 0, 0, 0)
    match = _BOUNDS_RE.match(raw.strip())
    if not match:
        return (0, 0, 0, 0)
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(match.group(4)),
    )


def _hoist_labels(nodes: list[UiNode], children_map: dict[int, list[int]]) -> None:
    def collect_descendant_labels(node_id: int, out: list[str], seen: set[str]) -> None:
        for child_id in children_map.get(node_id, ()):
            child = nodes[child_id]
            # Stop at nested clickable sub-controls so a parent container does not steal their labels.
            if child.clickable:
                continue
            piece = child.text or child.desc
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
            collect_descendant_labels(child_id, out, seen)

    for node in nodes:
        if (node.clickable or node.checkable) and not node.text and not node.desc:
            collected: list[str] = []
            collect_descendant_labels(node.node_id, collected, set())
            if collected:
                node.hoisted_label = " - ".join(collected)


def parse_hierarchy_xml(xml_text: str) -> UiSnapshot:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RuntimeAxiError(f"failed to parse hierarchy XML: {exc}") from exc

    rotation_raw = root.attrib.get("rotation", "0")
    try:
        rotation = int(rotation_raw)
    except ValueError:
        rotation = 0

    try:
        width = max(0, int(root.attrib.get("width", "0") or 0))
        height = max(0, int(root.attrib.get("height", "0") or 0))
    except ValueError:
        width, height = 0, 0

    nodes: list[UiNode] = []
    children_map: dict[int, list[int]] = {}

    def walk(elem: ET.Element, parent_id: int | None) -> None:
        if elem.tag == "node":
            node_id = len(nodes)
            bounds = _parse_bounds(elem.attrib.get("bounds"))
            left, top, right, bottom = bounds
            raw_id = elem.attrib.get("resource-id", "").strip()
            node = UiNode(
                node_id=node_id,
                parent_id=parent_id,
                text=elem.attrib.get("text", "").strip(),
                desc=elem.attrib.get("content-desc", "").strip(),
                raw_res_id=raw_id,
                res_id=_short_res_id(raw_id),
                cls=elem.attrib.get("class", "").strip(),
                pkg=elem.attrib.get("package", "").strip(),
                bounds=bounds,
                cx=(left + right) // 2,
                cy=(top + bottom) // 2,
                clickable=_parse_bool(elem.attrib.get("clickable")),
                long_clickable=_parse_bool(elem.attrib.get("long-clickable")),
                scrollable=_parse_bool(elem.attrib.get("scrollable")),
                checkable=_parse_bool(elem.attrib.get("checkable")),
                checked=_parse_bool(elem.attrib.get("checked")),
                enabled=_parse_bool(elem.attrib.get("enabled")),
                focusable=_parse_bool(elem.attrib.get("focusable")),
                focused=_parse_bool(elem.attrib.get("focused")),
                selected=_parse_bool(elem.attrib.get("selected")),
                password=_parse_bool(elem.attrib.get("password")),
            )
            nodes.append(node)
            if parent_id is not None:
                children_map.setdefault(parent_id, []).append(node_id)
            next_parent = node_id
        else:
            next_parent = parent_id

        for child in elem:
            walk(child, next_parent)

    walk(root, None)
    _hoist_labels(nodes, children_map)
    return UiSnapshot(rotation=rotation, nodes=nodes, children_map=children_map, width=width, height=height)


def _match_str(haystack: str, needle: str, exact: bool) -> bool:
    if not haystack:
        return False
    h = haystack.lower()
    n = needle.lower()
    return h == n if exact else n in h


def node_matches(
    node: UiNode,
    query: str | None = None,
    text: str | None = None,
    res_id: str | None = None,
    exact: bool = False,
) -> bool:
    if text is not None:
        if not (
            _match_str(node.text, text, exact)
            or _match_str(node.desc, text, exact)
            or _match_str(node.hoisted_label, text, exact)
        ):
            return False
    if res_id is not None:
        if not (_match_str(node.res_id, res_id, exact) or _match_str(node.raw_res_id, res_id, exact)):
            return False
    if query is not None:
        if not (
            _match_str(node.text, query, exact)
            or _match_str(node.desc, query, exact)
            or _match_str(node.hoisted_label, query, exact)
            or _match_str(node.res_id, query, exact)
            or _match_str(node.raw_res_id, query, exact)
        ):
            return False
    return True


def filter_ui_nodes(
    snapshot: UiSnapshot,
    *,
    query: str | None = None,
    text: str | None = None,
    res_id: str | None = None,
    clickable: bool = False,
    scrollable: bool = False,
    include_all: bool = False,
) -> list[UiNode]:
    has_search = any(v is not None for v in (query, text, res_id))
    matched: list[UiNode] = []
    matched_ids: set[int] = set()

    for node in snapshot.nodes:
        if not include_all and not node.has_area:
            continue
        if clickable and not node.clickable:
            continue
        if scrollable and not node.scrollable:
            continue
        if not include_all and not clickable and not scrollable:
            if not has_search:
                if not node.is_interactive:
                    continue
            else:
                if not node.is_interactive and snapshot.has_clickable_ancestor(node):
                    ancestor = snapshot.clickable_ancestor(node)
                    if ancestor.node_id in matched_ids:
                        continue
        if has_search and not node_matches(node, query=query, text=text, res_id=res_id):
            continue
        matched.append(node)
        matched_ids.add(node.node_id)
    return matched


def resolve_tap_targets(
    snapshot: UiSnapshot,
    *,
    query: str | None = None,
    text: str | None = None,
    res_id: str | None = None,
    exact: bool = False,
) -> list[UiNode]:
    targets: list[UiNode] = []
    seen_ids: set[int] = set()

    # Prefer direct own text/desc/res_id matches over hoisted container text when exact=True.
    for node in snapshot.nodes:
        if not node.has_area:
            continue
        if not node_matches(node, query=query, text=text, res_id=res_id, exact=exact):
            continue
        target = snapshot.clickable_ancestor(node)
        if target.node_id not in seen_ids:
            seen_ids.add(target.node_id)
            targets.append(target)
    return targets
