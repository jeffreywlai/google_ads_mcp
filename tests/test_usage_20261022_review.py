"""Actual MCP regressions for independent usage-report routing review."""

import asyncio
import sys
from unittest import mock

from fastmcp import Client
import pytest

from ads_mcp import registry  # pylint: disable=unused-import
from ads_mcp.coordinator import mcp_server
from ads_mcp.routing import resolve_intent


@pytest.fixture(autouse=True)
def reject_ads_clients(monkeypatch):
  """Discovery must never create a Google Ads client through any import."""
  for module in tuple(sys.modules.values()):
    if getattr(module, "__name__", "").startswith(
        "ads_mcp.tools."
    ) and hasattr(module, "get_ads_client"):
      monkeypatch.setattr(
          module,
          "get_ads_client",
          mock.Mock(
              side_effect=AssertionError("Discovery created an Ads client")
          ),
      )


_REVIEW_CASES = [
    (
        "clone the asset group assets to another campaign",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "move the asset group assets to another campaign",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "campaign target ROAS change history",
        "get_change_history_extended",
        "read",
    ),
    (
        "export full asset group budget change history including performance",
        "export_change_history_csv",
        "write",
    ),
    ("YouTube video upload history", "execute_gaql", "read"),
    ("export YouTube video upload history", "export_gaql_csv", "write"),
    (
        "copy the asset group with audience signals to another campaign",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "copy an asset group and its audiences to campaign 123",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "list conversion performance for each asset in an asset group",
        "list_asset_group_assets",
        "read",
    ),
    (
        "show asset performance in asset group 123",
        "list_asset_group_assets",
        "read",
    ),
    (
        "show asset group 123 linked assets conversion performance",
        "list_asset_group_assets",
        "read",
    ),
    (
        "show asset group performance grouped by asset",
        "list_asset_group_assets",
        "read",
    ),
    (
        "show asset group performance grouped by asset group",
        "get_asset_group_performance",
        "read",
    ),
    (
        "show asset group performance per asset",
        "list_asset_group_assets",
        "read",
    ),
    (
        "show asset group performance per asset group",
        "get_asset_group_performance",
        "read",
    ),
    ("create a campaign budget $50", "mutate_ads_resources", "mutate"),
    ("create a new campaign Summer Sale", "mutate_ads_resources", "mutate"),
    (
        "set the ROAS target for campaign 123 to 5",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "change the ROAS target for campaign 123 to 5 based on last week"
        " performance",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "please change the ROAS target for campaign 123 to 5 based on last"
        " week performance",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "please change the previous campaign target ROAS to 5",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "please change the last campaign target ROAS to 5",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "please change the bidding strategy to target ROAS for campaign 123"
        " based on last week performance",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "please change to a target ROAS of 5 for campaign 123 based on last"
        " week performance",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "change to a ROAS target of 5 for campaign 123 based on last week"
        " performance",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "based on last week performance, change to a ROAS target of 5 for"
        " campaign 123",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "using last week performance, change to a ROAS target of 5 for"
        " campaign 123",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "given last week performance, change to a ROAS target of 5 for"
        " campaign 123",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "set the ROAS target for campaign 123 to 5 based on last week"
        " performance",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "set campaign target ROAS to 5 based on asset group performance",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "change campaign target ROAS to 5 and export the results",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "set campaign target ROAS to 5 and export the changes",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "update campaign budget to 50 and export the changes",
        "update_campaign_budget",
        "mutate",
    ),
    (
        "pause campaign 123 and export the changes",
        "set_campaign_status",
        "mutate",
    ),
    (
        "copy the asset group to another campaign and export the changes",
        "mutate_ads_resources",
        "mutate",
    ),
    (
        "update campaign geo targeting settings using asset group conversion"
        " performance",
        "mutate_ads_resources",
        "mutate",
    ),
    ("campaign ROAS change last week", "get_campaign_performance", "read"),
    (
        "campaign spend and ROAS change last month",
        "get_campaign_performance",
        "read",
    ),
    (
        "campaign ROAS change from September to October",
        "get_campaign_performance",
        "read",
    ),
    (
        "campaign ROAS change year over year",
        "get_campaign_performance",
        "read",
    ),
    (
        "campaign target ROAS change last week",
        "get_change_history_extended",
        "read",
    ),
    (
        "campaign ROAS target change last week",
        "get_change_history_extended",
        "read",
    ),
    (
        "last week campaign ROAS target change",
        "get_change_history_extended",
        "read",
    ),
    (
        "campaign ROAS target change on Monday",
        "get_change_history_extended",
        "read",
    ),
    (
        "campaign ROAS target change during last week",
        "get_change_history_extended",
        "read",
    ),
    (
        "campaign ROAS target change during the previous week",
        "get_change_history_extended",
        "read",
    ),
    (
        "campaign ROAS target change over the previous quarter",
        "get_change_history_extended",
        "read",
    ),
    (
        "campaign ROAS target change to 5 last week",
        "get_change_history_extended",
        "read",
    ),
    (
        "the change to campaign target ROAS last week",
        "get_change_history_extended",
        "read",
    ),
    (
        "last week change to campaign ROAS target",
        "get_change_history_extended",
        "read",
    ),
    (
        "based on last week data, export the change to campaign ROAS target",
        "export_change_history_csv",
        "write",
    ),
    (
        "export the change to campaign ROAS target",
        "export_change_history_csv",
        "write",
    ),
    (
        "export campaign ROAS target changes",
        "export_change_history_csv",
        "write",
    ),
    (
        "download campaign ROAS target changes",
        "export_change_history_csv",
        "write",
    ),
    (
        "save campaign ROAS target changes to a file",
        "export_change_history_csv",
        "write",
    ),
    (
        "export all keyword bid changes",
        "export_change_history_csv",
        "write",
    ),
    ("export keyword bid", None, "read"),
    ("export keyword status and bid", None, "read"),
    ("export keyword status and bid amount", None, "read"),
    ("export keyword status and bid for keyword 123", None, "read"),
    ("export keyword status and bid for keyword 123 to a file", None, "read"),
    ("export keyword bid and status", None, "read"),
    ("export campaign label", None, "read"),
    ("export campaign status and label", None, "read"),
    ("export campaign status and label name", None, "read"),
    ("export campaign status and label for campaign 123", None, "read"),
    (
        "export campaign status and label for campaign 123 to a file",
        None,
        "read",
    ),
    ("export campaign label and status", None, "read"),
    ("export shared set", None, "read"),
    (
        "export the campaign budget change",
        "export_change_history_csv",
        "write",
    ),
    (
        "I need to export the campaign budget change",
        "export_change_history_csv",
        "write",
    ),
    (
        "I want to export the change to campaign ROAS target",
        "export_change_history_csv",
        "write",
    ),
    (
        "I need to export the change to campaign ROAS target",
        "export_change_history_csv",
        "write",
    ),
    (
        "can you export the change to campaign ROAS target",
        "export_change_history_csv",
        "write",
    ),
    (
        "could you export the campaign budget change",
        "export_change_history_csv",
        "write",
    ),
    (
        "export the campaign budget change from the recommendation",
        "export_change_history_csv",
        "write",
    ),
    (
        "export the change to campaign ROAS target from the recommendation",
        "export_change_history_csv",
        "write",
    ),
    (
        "export campaign budget recommendations",
        "list_recommendations",
        "read",
    ),
    (
        "export budget change recommendations",
        "list_recommendations",
        "read",
    ),
    (
        "the change to campaign ROAS target based on last week data",
        "get_change_history_extended",
        "read",
    ),
    (
        "based on last week data, the change to campaign ROAS target",
        "get_change_history_extended",
        "read",
    ),
    (
        "export campaign target ROAS change last week",
        "export_change_history_csv",
        "write",
    ),
    (
        "campaign ROAS target changes last week",
        "get_change_history_extended",
        "read",
    ),
    (
        "historical asset group performance",
        "get_asset_group_performance",
        "read",
    ),
    (
        "show asset group performance history",
        "get_asset_group_performance",
        "read",
    ),
    (
        "export historical asset group performance",
        "get_asset_group_performance",
        "read",
    ),
    (
        "historical asset group assets conversion performance",
        "list_asset_group_assets",
        "read",
    ),
    (
        "historical performance for each asset in an asset group",
        "list_asset_group_assets",
        "read",
    ),
    (
        "campaign target ROAS change log including full history",
        "export_change_history_csv",
        "write",
    ),
    (
        "target ROAS change events including history",
        "list_change_events",
        "read",
    ),
    (
        "full campaign history of changes to the max allowed CPC",
        "export_change_history_csv",
        "write",
    ),
    (
        "full campaign history of changes to target average CPA",
        "export_change_history_csv",
        "write",
    ),
    ("read campaign assets", None, "read"),
]


@pytest.mark.parametrize("query,expected,mode", _REVIEW_CASES)
def test_reviewed_request_keeps_resource_and_effect_in_both_sessions(
    query, expected, mode
):
  decision = resolve_intent(query)
  assert decision.target == expected
  assert decision.requires_mutation_visibility is (mode == "mutate")
  assert decision.exclude_remote_mutations is (mode != "mutate")

  async def check():
    async with Client(mcp_server) as client:
      for unlocked in (False, True):
        if unlocked:
          await client.call_tool("unlock_mutation_tools", {})
        response = await client.call_tool("search_tools", {"query": query})
        assert not response.is_error
        items = response.structured_content["result"]
        assert items
        if expected:
          assert items[0]["name"] == expected
          assert items[0]["mode"] == mode
        if mode == "mutate":
          assert items[0]["locked"] is not unlocked
        else:
          assert all(item["mode"] != "mutate" for item in items)

  asyncio.run(check())


@pytest.mark.parametrize(
    "query",
    [
        "export planned campaign target ROAS changes",
        "download proposed campaign budget changes",
        "export upcoming campaign target ROAS changes",
    ],
)
def test_prospective_exports_do_not_offer_past_records_or_mutations(query):
  history_tools = {
      "get_change_history_extended",
      "list_change_events",
      "list_change_statuses",
      "export_change_history_csv",
  }
  decision = resolve_intent(query)
  assert decision.target not in history_tools
  assert history_tools <= decision.excluded_tools
  assert not decision.requires_mutation_visibility
  assert decision.exclude_remote_mutations

  async def check():
    async with Client(mcp_server) as client:
      for unlocked in (False, True):
        if unlocked:
          await client.call_tool("unlock_mutation_tools", {})
        response = await client.call_tool("search_tools", {"query": query})
        assert not response.is_error
        items = response.structured_content["result"]
        assert all(item["name"] not in history_tools for item in items)
        assert all(item["mode"] != "mutate" for item in items)

  asyncio.run(check())


@pytest.mark.parametrize(
    "query,expected",
    [
        (
            "add campaign negative keywords and export results",
            "add_campaign_negative_keywords",
        ),
        (
            "update keyword bid and export results",
            "update_keyword_bid",
        ),
        (
            "bid 2 USD on keyword 123 and export results",
            "update_keyword_bid",
        ),
        (
            "bid 2 dollars for keyword 123 and export results",
            "update_keyword_bid",
        ),
        (
            "bid 2 USD on keyword 123 and export the changes",
            "update_keyword_bid",
        ),
        (
            "using last week performance, bid 2 dollars for keyword 123"
            " and export results",
            "update_keyword_bid",
        ),
        (
            "based on last week performance, bid 2 USD on keyword 123"
            " and export results",
            "update_keyword_bid",
        ),
        (
            "label campaign 123 and export results",
            "manage_campaign_labels",
        ),
        (
            "export keyword status and bid 2 dollars for keyword 123",
            "update_keyword_bid",
        ),
        (
            "export campaign status and label campaign 123",
            "manage_campaign_labels",
        ),
        (
            "bid on keyword 123 at 2 USD and export results",
            "update_keyword_bid",
        ),
        (
            "export results and bid on keyword 123 at 2 USD",
            "update_keyword_bid",
        ),
        (
            "update name of campaign 123 to Spring Sale and export results",
            None,
        ),
        (
            "set the name for campaign 123 to Spring Sale and export results",
            None,
        ),
    ],
)
def test_current_write_with_export_preserves_unlocked_fallback(
    query, expected
):
  decision = resolve_intent(query)
  assert not decision.exclude_remote_mutations

  async def check():
    async with Client(mcp_server) as client:
      for unlocked in (False, True):
        if unlocked:
          await client.call_tool("unlock_mutation_tools", {})
        response = await client.call_tool("search_tools", {"query": query})
        assert not response.is_error
        items = response.structured_content["result"]
        if unlocked:
          assert any(
              item["mode"] == "mutate"
              and (expected is None or item["name"] == expected)
              for item in items
          )
        else:
          assert all(
              item["mode"] != "mutate" or item.get("locked") for item in items
          )

  asyncio.run(check())


@pytest.mark.parametrize(
    "query",
    [
        "export full campaign target ROAS change history including actual ROAS"
        " and spend",
        "full asset group target ROAS change history including performance",
        "export full campaign ROAS target changes last week including actual"
        " ROAS",
    ],
)
def test_target_roas_history_preserves_separately_requested_metrics(query):
  expected = {
      "export_change_history_csv",
      "get_competitive_pressure_report",
      "export_gaql_csv",
  }
  decision = resolve_intent(query)
  assert decision.target == "export_change_history_csv"
  assert set(decision.preferred_targets) == expected
  assert not decision.requires_mutation_visibility
  assert decision.exclude_remote_mutations

  async def check():
    async with Client(mcp_server) as client:
      for unlocked in (False, True):
        if unlocked:
          await client.call_tool("unlock_mutation_tools", {})
        response = await client.call_tool("search_tools", {"query": query})
        assert not response.is_error
        items = response.structured_content["result"]
        assert items[0]["name"] == "export_change_history_csv"
        assert expected <= {item["name"] for item in items}
        assert all(item["mode"] != "mutate" for item in items)

  asyncio.run(check())
