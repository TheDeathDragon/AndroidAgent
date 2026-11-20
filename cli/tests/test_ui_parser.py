from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli.ui_parser import filter_ui_nodes, parse_hierarchy_xml, resolve_tap_targets

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="Settings" resource-id="com.android.settings:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[48,100][400,180]" />
    <node index="1" text="" resource-id="com.android.settings:id/row_wifi" class="android.widget.LinearLayout" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,200][1080,360]">
      <node index="0" text="Wi-Fi" resource-id="android:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[48,220][300,280]" />
      <node index="1" text="HomeNet" resource-id="android:id/summary" class="android.widget.TextView" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[48,290][400,340]" />
      <node index="2" text="" resource-id="com.android.settings:id/wifi_switch" class="android.widget.Switch" package="com.android.settings" content-desc="Toggle Wi-Fi" checkable="true" checked="true" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[900,240][1040,320]" />
    </node>
    <node index="2" text="Save" resource-id="com.android.settings:id/btn_save_1" class="android.widget.Button" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[100,500][400,600]" />
    <node index="3" text="Save Draft" resource-id="com.android.settings:id/btn_save_2" class="android.widget.Button" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[500,500][900,600]" />
  </node>
</hierarchy>
"""


class TestUiParser(unittest.TestCase):
    def test_bounds_center_short_id_and_label_hoisting(self) -> None:
        snap = parse_hierarchy_xml(SAMPLE_XML)
        self.assertEqual(snap.rotation, 0)
        self.assertEqual(snap.screen_size(), (1080, 2400))

        wifi_row = next(n for n in snap.nodes if n.res_id == "row_wifi")
        self.assertEqual((wifi_row.cx, wifi_row.cy), (540, 280))
        # Hoists non-clickable child labels while stopping at the nested clickable switch.
        self.assertEqual(wifi_row.label, "Wi-Fi - HomeNet")
        self.assertNotIn("Toggle Wi-Fi", wifi_row.label)

        switch = next(n for n in snap.nodes if n.res_id == "wifi_switch")
        self.assertEqual(switch.label, "Toggle Wi-Fi")
        self.assertEqual(switch.act, "click+check")

    def test_selector_walks_up_to_clickable_ancestor_and_deduplicates(self) -> None:
        snap = parse_hierarchy_xml(SAMPLE_XML)
        targets = resolve_tap_targets(snap, text="HomeNet")
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].res_id, "row_wifi")
        self.assertEqual((targets[0].cx, targets[0].cy), (540, 280))

    def test_selector_ambiguous_vs_exact_and_zero_matches(self) -> None:
        snap = parse_hierarchy_xml(SAMPLE_XML)
        ambig = resolve_tap_targets(snap, text="Save")
        self.assertEqual(len(ambig), 2)

        exact = resolve_tap_targets(snap, text="Save", exact=True)
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].res_id, "btn_save_1")

        none = resolve_tap_targets(snap, text="NonExistentButton")
        self.assertEqual(none, [])

    def test_filter_ui_nodes_default_interactive_vs_all(self) -> None:
        snap = parse_hierarchy_xml(SAMPLE_XML)
        interactive = filter_ui_nodes(snap)
        self.assertEqual([n.res_id for n in interactive], ["row_wifi", "wifi_switch", "btn_save_1", "btn_save_2"])

        all_nodes = filter_ui_nodes(snap, include_all=True)
        self.assertEqual(len(all_nodes), 8)


if __name__ == "__main__":
    unittest.main()
