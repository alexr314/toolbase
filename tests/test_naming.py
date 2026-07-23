"""Tests for toolbase.naming — the one canonical tool-naming rule and the
cross-toolkit collision detector."""

import unittest

from toolbase.naming import (
    strip_tool_suffix,
    mcp_tool_name,
    namespaced_tool_name,
    find_name_collisions,
)


class _Tool:
    """Stand-in for a BaseTool instance (only the naming-relevant bits)."""
    def __init__(self, cls_name, display=None):
        self.__class__ = type(cls_name, (_Tool,), {})
        if display is not None:
            self._mcp_display_name = display


class TestNameRule(unittest.TestCase):
    def test_strip_tool_suffix_preserves_pascalcase(self):
        self.assertEqual(strip_tool_suffix("CalculateInvariantMassTool"),
                         "CalculateInvariantMass")
        self.assertEqual(strip_tool_suffix("Add"), "Add")  # no suffix -> unchanged

    def test_mcp_tool_name_from_class_string(self):
        self.assertEqual(mcp_tool_name("InspireSearchTool"), "InspireSearch")

    def test_mcp_tool_name_display_wins_over_strip(self):
        # The SortByPt regression: a naive strip gives "SortByPt"; the served
        # name is the display name "SortByPT".
        self.assertEqual(mcp_tool_name("SortByPtTool", "SortByPT"), "SortByPT")

    def test_mcp_tool_name_reads_instance_display(self):
        t = _Tool("SortByPtTool", display="SortByPT")
        self.assertEqual(mcp_tool_name(t), "SortByPT")

    def test_mcp_tool_name_instance_without_display_strips_class(self):
        t = _Tool("AddTool")
        self.assertEqual(mcp_tool_name(t), "Add")

    def test_namespaced(self):
        self.assertEqual(
            namespaced_tool_name("calculator", "AddTool"), "calculator__Add")
        self.assertEqual(
            namespaced_tool_name("heptapod", "SortByPtTool", "SortByPT"),
            "heptapod__SortByPT")


class TestCollisions(unittest.TestCase):
    def test_no_collision(self):
        self.assertEqual(
            find_name_collisions({"calc": ["Add", "Sub"], "units": ["Convert"]}),
            {},
        )

    def test_one_collision_reports_sorted_owners(self):
        got = find_name_collisions({
            "calc": ["Add", "Multiply"],
            "matrix": ["Multiply", "Invert"],
        })
        self.assertEqual(got, {"Multiply": ["calc", "matrix"]})

    def test_three_way_collision(self):
        got = find_name_collisions({
            "b": ["run"], "a": ["run"], "c": ["run", "other"],
        })
        self.assertEqual(got, {"run": ["a", "b", "c"]})  # owners sorted

    def test_empty_and_single_toolkit(self):
        self.assertEqual(find_name_collisions({}), {})
        self.assertEqual(find_name_collisions({"solo": ["a", "b"]}), {})


if __name__ == "__main__":
    unittest.main()
