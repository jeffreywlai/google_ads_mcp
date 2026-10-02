"""Discovery regressions for the fixed campaign reporting workflows."""

import asyncio

from fastmcp import Client
import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.routing.intents import resolve_intent
from ads_mcp.tools import reporting  # pylint: disable=unused-import


@pytest.mark.parametrize(
    "query,target",
    [
        ("show current campaign settings snapshot", "get_campaign_settings"),
        ("get campaign configuration", "get_campaign_settings"),
        (
            "compare campaign performance across explicit periods",
            "compare_performance_periods",
        ),
        (
            "compare campaign clicks and ROAS across date windows",
            "compare_performance_periods",
        ),
        (
            "compare campaign performance periods by device",
            "compare_performance_periods",
        ),
    ],
)
def test_dedicated_workflow_routing_is_a_read(query, target):
  decision = resolve_intent(query)
  assert decision.target == target
  assert decision.requires_mutation_visibility is False
  assert decision.exclude_remote_mutations is True


@pytest.mark.parametrize(
    "query",
    [
        "campaign settings history",
        "change campaign settings",
        "compare campaign audiences across periods",
        "compare campaign city performance across periods",
        "export current campaign settings",
        "update campaign settings",
        "delete campaign configuration",
        "compare campaign performance periods by ad group",
        "compare campaign performance periods by conversion action",
        "show campaign ad group settings",
        "show campaign keyword configuration",
        "compare campaign performance periods by hour",
        "compare campaign performance periods by network",
    ],
)
def test_workflow_routes_do_not_override_other_operations(query):
  assert resolve_intent(query).target not in {
      "get_campaign_settings",
      "compare_performance_periods",
  }


def test_new_workflows_are_visible_and_searchable_with_read_annotations():
  async def check():
    async with Client(mcp_server) as client:
      tools = {tool.name: tool for tool in await client.list_tools()}
      for name in ("get_campaign_settings", "compare_performance_periods"):
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False
      for query, target in (
          ("show current campaign settings snapshot", "get_campaign_settings"),
          (
              "compare campaign performance across explicit periods",
              "compare_performance_periods",
          ),
      ):
        result = await client.call_tool("search_tools", {"query": query})
        assert result.structured_content["result"][0]["name"] == target

  asyncio.run(check())
