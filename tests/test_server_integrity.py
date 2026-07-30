# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Server integrity tests: tool registration, docstrings, update masks,
and cross-tool state management.
"""

import asyncio
from datetime import date
from datetime import timedelta
import inspect
import os
import re
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.visibility import Visibility
import pytest
import yaml

from ads_mcp.coordinator import mcp_server
from ads_mcp.tooling import MUTATE_TAG
from ads_mcp.tooling import compact_search_result_serializer
from ads_mcp.tools import ad_groups
from ads_mcp.tools import ads
from ads_mcp.tools import api
from ads_mcp.tools import audiences
from ads_mcp.tools import campaigns
from ads_mcp.tools import changes
from ads_mcp.tools import conversions
from ads_mcp.tools import docs
from ads_mcp.tools import keyword_planner
from ads_mcp.tools import keywords
from ads_mcp.tools import labels
from ads_mcp.tools import negatives
from ads_mcp.tools import performance_max
from ads_mcp.tools import reporting
from ads_mcp.tools import recommendations
from ads_mcp.tools import search_terms
from ads_mcp.tools import simulations
from ads_mcp.tools import smart_campaigns


# All tool modules and their expected public tool functions.
TOOL_MODULES = {
    api: [
        "execute_gaql",
        "export_gaql_csv",
        "list_accessible_accounts",
    ],
    audiences: [
        "search_user_interests",
        "create_audience",
    ],
    campaigns: [
        "set_campaign_status",
        "update_campaign_budget",
        "set_campaign_view_through_conversion_optimization",
        "update_campaign_targeting_setting",
        "list_campaign_audiences",
        "diff_campaign_audiences",
        "add_campaign_audiences",
        "copy_audiences_between_campaigns",
        "remove_campaign_audiences",
    ],
    ad_groups: [
        "set_ad_group_status",
        "set_ad_group_criterion_status",
        "remove_ad_group_audiences",
        "update_ad_group_bid",
    ],
    ads: [
        "set_ad_status",
    ],
    keywords: [
        "set_keyword_status",
        "update_keyword_bid",
    ],
    labels: [
        "create_label",
        "delete_label",
        "manage_campaign_labels",
        "manage_ad_group_labels",
    ],
    negatives: [
        "list_shared_sets",
        "create_shared_set",
        "delete_shared_set",
        "list_shared_set_keywords",
        "add_shared_set_keywords",
        "remove_shared_set_keywords",
        "list_campaign_shared_sets",
        "attach_shared_set_to_campaign",
        "detach_shared_set_from_campaign",
        "list_campaign_negative_keywords",
        "add_campaign_negative_keywords",
        "remove_campaign_negative_keywords",
    ],
    keyword_planner: [
        "generate_keyword_ideas",
    ],
    smart_campaigns: [
        "suggest_keyword_themes",
        "suggest_smart_campaign_ad",
        "suggest_smart_campaign_budget",
    ],
    docs: [
        "get_tool_guide",
        "get_gaql_doc",
        "get_resource_metadata",
        "get_reporting_view_doc",
        "get_reporting_fields_doc",
        "search_google_ads_fields",
        "get_tool_visibility_profile",
        "unlock_mutation_tools",
        "lock_mutation_tools",
    ],
    recommendations: [
        "list_recommendations",
        "get_optimization_score_summary",
        "apply_recommendations",
        "dismiss_recommendations",
        "list_recommendation_subscriptions",
        "create_recommendation_subscription",
        "set_recommendation_subscription_status",
    ],
    search_terms: [
        "list_campaign_search_term_insights",
        "list_customer_search_term_insights",
        "compare_search_terms",
        "analyze_search_terms",
    ],
    simulations: [
        "list_campaign_simulations",
        "list_ad_group_simulations",
        "list_ad_group_criterion_simulations",
    ],
    changes: [
        "list_change_statuses",
        "list_change_events",
        "export_change_history_csv",
        "get_change_history_extended",
    ],
    conversions: [
        "list_offline_conversion_upload_client_summaries",
        "list_offline_conversion_upload_conversion_action_summaries",
        "upload_click_conversions",
        "upload_call_conversions",
    ],
    performance_max: [
        "list_asset_group_assets",
        "list_asset_group_top_combinations",
        "list_performance_max_placements",
    ],
    reporting: [
        "list_device_performance",
        "list_geographic_performance",
        "list_impression_share",
        "get_campaign_performance",
        "get_competitive_pressure_report",
        "get_campaign_conversion_goals",
        "list_keyword_quality_scores",
        "summarize_keyword_quality_scores",
        "list_rsa_ad_strength",
        "list_conversion_actions",
        "list_audience_performance",
        "get_demographic_performance",
        "get_landing_page_performance",
        "get_ad_inventory",
        "list_video_enhancements",
        "summarize_cart_data_sales",
        "compare_biddable_vs_all_cart_value",
        "list_cart_profit_outliers",
        "list_shopping_attribution_breakdown",
        "list_campaign_view_through_optimization",
        "list_video_audibility_performance",
        "list_vertical_ads_performance",
        "list_campaign_search_terms",
        "list_ai_max_search_term_ad_combinations",
        "list_final_url_expansion_assets",
        "list_targeting_expansion_performance",
        "list_content_suitability_placements",
        "list_location_interest_performance",
        "summarize_shopping_product_status",
        "list_shopping_product_status",
        "list_travel_feed_asset_sets",
        "list_retail_filter_shared_criteria",
    ],
}


# ===================================================================
# 1. Tool registration: all expected tools exist as callable functions
# ===================================================================


class TestToolRegistration:

  def test_total_tool_count_is_107(self):
    total = sum(len(fns) for fns in TOOL_MODULES.values())
    assert total == 107, f"Expected 107 tools, found {total}"

  @pytest.mark.parametrize(
      "module,func_name",
      [(mod, fn) for mod, fns in TOOL_MODULES.items() for fn in fns],
  )
  def test_tool_exists_and_callable(self, module, func_name):
    func = getattr(module, func_name, None)
    assert func is not None, f"{module.__name__}.{func_name} does not exist"
    assert callable(func), f"{module.__name__}.{func_name} is not callable"


# ===================================================================
# 2. All tools have non-empty docstrings (FastMCP uses them)
# ===================================================================


class TestToolDocstrings:

  @pytest.mark.parametrize(
      "module,func_name",
      [(mod, fn) for mod, fns in TOOL_MODULES.items() for fn in fns],
  )
  def test_tool_has_docstring(self, module, func_name):
    func = getattr(module, func_name)
    docstring = func.__doc__
    assert docstring, (
        f"{module.__name__}.{func_name} has no docstring. "
        "FastMCP uses docstrings as tool descriptions."
    )
    assert len(docstring.strip()) > 10, (
        f"{module.__name__}.{func_name} docstring is too short: "
        f"'{docstring.strip()}'"
    )


# ===================================================================
# 3. All tools accept customer_id (except docs tools)
# ===================================================================


class TestToolSignatures:

  NON_CUSTOMER_TOOLS = {
      "get_gaql_doc",
      "get_tool_guide",
      "get_resource_metadata",
      "get_reporting_view_doc",
      "get_reporting_fields_doc",
      "search_google_ads_fields",
      "list_accessible_accounts",
      "get_tool_visibility_profile",
      "unlock_mutation_tools",
      "lock_mutation_tools",
  }

  @pytest.mark.parametrize(
      "module,func_name",
      [(mod, fn) for mod, fns in TOOL_MODULES.items() for fn in fns],
  )
  def test_customer_id_parameter(self, module, func_name):
    if func_name in self.NON_CUSTOMER_TOOLS:
      return
    func = getattr(module, func_name)
    sig = inspect.signature(func)
    assert (
        "customer_id" in sig.parameters
    ), f"{module.__name__}.{func_name} missing 'customer_id' parameter"

  TOOLS_WITH_LOGIN_CUSTOMER_ID = {
      fn for fns in TOOL_MODULES.values() for fn in fns
  } - {
      "get_gaql_doc",
      "get_tool_guide",
      "get_resource_metadata",
      "get_reporting_view_doc",
      "get_reporting_fields_doc",
      "search_google_ads_fields",
      "list_accessible_accounts",
      "get_tool_visibility_profile",
      "unlock_mutation_tools",
      "lock_mutation_tools",
  }

  @pytest.mark.parametrize(
      "module,func_name",
      [
          (mod, fn)
          for mod, fns in TOOL_MODULES.items()
          for fn in fns
          if fn
          not in {
              "get_gaql_doc",
              "get_tool_guide",
              "get_resource_metadata",
              "get_reporting_view_doc",
              "get_reporting_fields_doc",
              "search_google_ads_fields",
              "list_accessible_accounts",
              "get_tool_visibility_profile",
              "unlock_mutation_tools",
              "lock_mutation_tools",
          }
      ],
  )
  def test_login_customer_id_optional(self, module, func_name):
    func = getattr(module, func_name)
    sig = inspect.signature(func)
    assert (
        "login_customer_id" in sig.parameters
    ), f"{module.__name__}.{func_name} missing 'login_customer_id'"
    param = sig.parameters["login_customer_id"]
    assert param.default is None, (
        f"{module.__name__}.{func_name} login_customer_id "
        f"default should be None, got {param.default}"
    )


# ===================================================================
# 4. update_mask paths match the fields being set
# ===================================================================


# ===================================================================
# 5. Sequential login_customer_id state management
# ===================================================================


class TestSequentialLoginCustomerId:
  """Calling multiple tools in sequence with different
  login_customer_ids must not leak state."""

  @pytest.fixture(autouse=True)
  def setup(self):
    # We need to test the actual get_ads_client logic, so we mock
    # at a lower level: GoogleAdsClient.load_from_storage.
    self.campaign_patch = mock.patch("ads_mcp.tools.campaigns.get_ads_client")
    self.ad_group_patch = mock.patch("ads_mcp.tools.ad_groups.get_ads_client")
    self.campaign_mock = self.campaign_patch.start()
    self.ad_group_mock = self.ad_group_patch.start()

    for m in [self.campaign_mock, self.ad_group_mock]:
      client = mock.Mock()
      m.return_value = client
      service = client.get_service.return_value
      response = mock.Mock()
      response.results = [mock.Mock(resource_name="ok")]
      service.mutate_campaigns.return_value = response
      service.mutate_ad_groups.return_value = response

    yield
    self.campaign_patch.stop()
    self.ad_group_patch.stop()

  def test_campaign_then_ad_group_different_login_ids(self):
    campaigns.set_campaign_status("123", "456", "PAUSED", "mcc1")
    self.campaign_mock.assert_called_with("mcc1")

    ad_groups.set_ad_group_status("123", "789", "ENABLED", "mcc2")
    self.ad_group_mock.assert_called_with("mcc2")

  def test_tool_without_login_id_passes_none(self):
    campaigns.set_campaign_status("123", "456", "PAUSED")
    self.campaign_mock.assert_called_with(None)

  def test_sequential_same_tool_different_login_ids(self):
    campaigns.set_campaign_status("123", "456", "PAUSED", "mcc1")
    campaigns.set_campaign_status("123", "456", "ENABLED", "mcc2")

    calls = self.campaign_mock.call_args_list
    assert calls[-2] == mock.call("mcc1")
    assert calls[-1] == mock.call("mcc2")


# ===================================================================
# 6. Embedded GAQL queries are syntactically valid
# ===================================================================


class TestEmbeddedGaqlSyntax:
  """GAQL queries hardcoded in negatives.py must follow basic syntax."""

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.negatives.get_ads_client") as m:
      client = mock.Mock()
      m.return_value = client
      service = client.get_service.return_value
      service.search_stream.return_value = []
      self.service = service
      yield

  def _get_query(self):
    return self.service.search_stream.call_args.kwargs.get(
        "query",
        self.service.search_stream.call_args[1].get("query", ""),
    )

  def _assert_valid_gaql(self, query):
    """Basic GAQL syntax validation."""
    assert "SELECT" in query, "Missing SELECT"
    assert "FROM" in query, "Missing FROM"
    # Check SELECT comes before FROM
    assert query.index("SELECT") < query.index(
        "FROM"
    ), "SELECT must come before FROM"
    # Check WHERE comes after FROM if present
    if "WHERE" in query:
      assert query.index("FROM") < query.index(
          "WHERE"
      ), "WHERE must come after FROM"

  def test_list_shared_sets_valid_gaql(self):
    negatives.list_shared_sets("123")
    self._assert_valid_gaql(self._get_query())

  def test_list_shared_set_keywords_valid_gaql(self):
    negatives.list_shared_set_keywords("123", "456")
    self._assert_valid_gaql(self._get_query())

  def test_list_campaign_shared_sets_valid_gaql(self):
    negatives.list_campaign_shared_sets("123")
    self._assert_valid_gaql(self._get_query())

  def test_list_campaign_shared_sets_with_campaign_filter_valid_gaql(self):
    negatives.list_campaign_shared_sets("123", campaign_id="999")
    self._assert_valid_gaql(self._get_query())

  def test_list_campaign_shared_sets_with_shared_set_filter_valid_gaql(self):
    negatives.list_campaign_shared_sets("123", shared_set_id="888")
    self._assert_valid_gaql(self._get_query())

  def test_list_campaign_negative_keywords_valid_gaql(self):
    negatives.list_campaign_negative_keywords("123", "789")
    self._assert_valid_gaql(self._get_query())

  def test_queries_use_single_from_resource(self):
    """GAQL requires exactly one resource in FROM."""
    for call_fn in [
        lambda: negatives.list_shared_sets("123"),
        lambda: negatives.list_shared_set_keywords("123", "456"),
        lambda: negatives.list_campaign_shared_sets("123"),
        lambda: negatives.list_campaign_negative_keywords("123", "789"),
    ]:
      call_fn()
      query = self._get_query()
      # Extract FROM clause resource
      from_match = re.search(r"FROM\s+(\w+)", query)
      assert from_match, f"No FROM found in: {query}"
      # Only one word after FROM
      resource = from_match.group(1)
      assert re.match(
          r"^[a-z][a-z_]*$", resource
      ), f"Invalid FROM resource: {resource}"


# ===================================================================
# 7. Mutation tools only touch the fields they claim to update
# ===================================================================


class TestMutationFieldIntegrity:
  """Each mutation tool should only set the update_mask for the field
  it modifies — not extra fields."""

  def _make_mock(self, mod_path):
    patcher = mock.patch(f"{mod_path}.get_ads_client")
    mock_get = patcher.start()
    client = mock.Mock()
    mock_get.return_value = client
    op = client.get_type.return_value
    op.update_mask.paths = []
    response = mock.Mock()
    response.results = [mock.Mock(resource_name="ok")]
    # Set return value for all mutate methods
    service = client.get_service.return_value
    for attr in [
        "mutate_campaigns",
        "mutate_campaign_budgets",
        "mutate_ad_groups",
        "mutate_ad_group_ads",
        "mutate_ad_group_criteria",
    ]:
      setattr(service, attr, mock.Mock(return_value=response))
    return patcher, op

  def test_campaign_status_only_sets_status(self):
    p, op = self._make_mock("ads_mcp.tools.campaigns")
    campaigns.set_campaign_status("123", "456", "PAUSED")
    assert op.update_mask.paths == ["status"]
    p.stop()

  def test_campaign_budget_only_sets_amount(self):
    p, op = self._make_mock("ads_mcp.tools.campaigns")
    campaigns.update_campaign_budget("123", "456", 50_000_000)
    assert op.update_mask.paths == ["amount_micros"]
    p.stop()

  def test_campaign_view_through_optimization_only_sets_field(self):
    p, op = self._make_mock("ads_mcp.tools.campaigns")
    campaigns.set_campaign_view_through_conversion_optimization(
        "123",
        "456",
        True,
    )
    assert op.update_mask.paths == [
        "view_through_conversion_optimization_enabled"
    ]
    p.stop()

  def test_campaign_targeting_setting_only_sets_target_restrictions(self):
    p, op = self._make_mock("ads_mcp.tools.campaigns")
    client = campaigns.get_ads_client(None)
    google_ads_service = mock.Mock()
    google_ads_service.search_stream.return_value = []
    client.get_service.side_effect = lambda name: (
        google_ads_service
        if name == "GoogleAdsService"
        else client.get_service.return_value
    )

    campaigns.update_campaign_targeting_setting(
        "123",
        "456",
        [{"targeting_dimension": "AUDIENCE", "bid_only": True}],
    )
    assert op.update_mask.paths == ["targeting_setting.target_restrictions"]
    p.stop()

  def test_ad_group_status_only_sets_status(self):
    p, op = self._make_mock("ads_mcp.tools.ad_groups")
    ad_groups.set_ad_group_status("123", "456", "ENABLED")
    assert op.update_mask.paths == ["status"]
    p.stop()

  def test_ad_group_criterion_status_only_sets_status(self):
    p, op = self._make_mock("ads_mcp.tools.ad_groups")
    ad_groups.set_ad_group_criterion_status("123", "456", "789", "PAUSED")
    assert op.update_mask.paths == ["status"]
    p.stop()

  def test_ad_group_bid_only_sets_cpc(self):
    p, op = self._make_mock("ads_mcp.tools.ad_groups")
    ad_groups.update_ad_group_bid("123", "456", 2_000_000)
    assert op.update_mask.paths == ["cpc_bid_micros"]
    p.stop()

  def test_ad_status_only_sets_status(self):
    p, op = self._make_mock("ads_mcp.tools.ads")
    ads.set_ad_status("123", "456", "789", "PAUSED")
    assert op.update_mask.paths == ["status"]
    p.stop()

  def test_keyword_status_only_sets_status(self):
    p, op = self._make_mock("ads_mcp.tools.keywords")
    keywords.set_keyword_status("123", "456", "789", "ENABLED")
    assert op.update_mask.paths == ["status"]
    p.stop()

  def test_keyword_bid_only_sets_cpc(self):
    p, op = self._make_mock("ads_mcp.tools.keywords")
    keywords.update_keyword_bid("123", "456", "789", 1_500_000)
    assert op.update_mask.paths == ["cpc_bid_micros"]
    p.stop()


# ===================================================================
# 8. FastMCP metadata and discovery configuration
# ===================================================================


class TestFastMcpConfiguration:

  def test_registered_tools_have_tags_and_annotations(self):
    registered_tools = {
        tool.name: tool
        for tool in asyncio.run(mcp_server._local_provider.list_tools())
    }

    assert len(registered_tools) == 107
    for tool_name in sorted(registered_tools):
      tool = registered_tools[tool_name]
      assert tool.tags, f"{tool_name} should have at least one tag"
      assert (
          tool.annotations is not None
      ), f"{tool_name} should have FastMCP annotations"

    assert registered_tools["execute_gaql"].annotations.readOnlyHint is True
    assert (
        registered_tools["export_gaql_csv"].annotations.readOnlyHint is False
    )
    assert (
        registered_tools["export_gaql_csv"].annotations.destructiveHint is True
    )
    assert (
        registered_tools["export_change_history_csv"].annotations.readOnlyHint
        is False
    )
    assert (
        registered_tools[
            "export_change_history_csv"
        ].annotations.destructiveHint
        is True
    )
    assert MUTATE_TAG not in registered_tools["export_gaql_csv"].tags
    export_search_items = compact_search_result_serializer(
        [registered_tools["export_gaql_csv"]]
    )
    assert export_search_items[0]["mode"] == "write"
    assert MUTATE_TAG in registered_tools["apply_recommendations"].tags
    assert registered_tools["delete_label"].annotations.destructiveHint is True
    assert (
        registered_tools["unlock_mutation_tools"].annotations.openWorldHint
        is False
    )

  def test_tool_guide_references_registered_tools(self):
    registered_tool_names = {
        tool.name
        for tool in asyncio.run(mcp_server._local_provider.list_tools())
    }
    tool_guide_path = os.path.join(
        docs.MODULE_DIR,
        "context/tool_guide.yaml",
    )
    with open(tool_guide_path, "r", encoding="utf-8") as tool_guide_file:
      tool_guide = yaml.safe_load(tool_guide_file)

    guide_tool_names = {
        tool_name
        for category in tool_guide["categories"].values()
        for tool_name in category.get("tools", {})
    }
    missing_tools = sorted(guide_tool_names - registered_tool_names)
    undocumented_tools = sorted(registered_tool_names - guide_tool_names)

    assert not missing_tools
    assert not undocumented_tools

  def test_get_tool_guide_returns_structured_content_through_client(self):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_tool_guide",
            {"topic": "gaql"},
        )

      assert set(result.structured_content) == {
          "guide",
          "matched_category_count",
          "topic",
      }
      assert result.structured_content["topic"] == "gaql"
      assert result.structured_content["matched_category_count"] >= 1

    asyncio.run(_run())

  def test_search_google_ads_fields_rejects_bool_limit_through_client(self):
    async def _run():
      async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="Input should be a valid integer"):
          await client.call_tool(
              "search_google_ads_fields",
              {
                  "query": "SELECT name WHERE name LIKE 'campaign.%%'",
                  "limit": True,
              },
          )

    asyncio.run(_run())

  def test_bm25_search_and_default_visibility_transforms_configured(self):
    transforms = mcp_server._transforms

    search_transform = next(
        transform
        for transform in transforms
        if isinstance(transform, BM25SearchTransform)
    )
    assert search_transform._max_results == 8
    assert (
        search_transform._search_result_serializer
        is compact_search_result_serializer
    )

    visibility_transform = next(
        transform
        for transform in transforms
        if isinstance(transform, Visibility)
    )
    assert visibility_transform._enabled is False
    assert visibility_transform.tags == {MUTATE_TAG}
    assert visibility_transform.components == {"tool"}
    assert (
        "Read/reporting and docs tools are directly visible"
        in mcp_server.instructions
    )
    assert "do not treat change_event retention" in mcp_server.instructions

  def test_public_tool_list_exposes_all_non_mutation_tools(self):
    public_tools = asyncio.run(mcp_server.list_tools())
    raw_tools = asyncio.run(mcp_server._local_provider.list_tools())

    expected_visible = {
        tool.name
        for tool in raw_tools
        if MUTATE_TAG not in set(tool.tags or [])
    }
    public_tool_names = {tool.name for tool in public_tools}

    assert public_tool_names == expected_visible | {
        "search_tools",
        "call_tool",
    }

    synthetic_tools = {"search_tools", "call_tool"}
    search_transform = next(
        transform
        for transform in mcp_server._transforms
        if isinstance(transform, BM25SearchTransform)
    )
    assert search_transform._always_visible == expected_visible

    for tool in public_tools:
      if tool.name not in synthetic_tools:
        assert MUTATE_TAG not in set(tool.tags or [])

    assert "analyze_search_terms" in public_tool_names
    assert "get_optimization_score_summary" in public_tool_names
    assert "get_campaign_conversion_goals" in public_tool_names
    assert "list_offline_conversion_upload_client_summaries" in (
        public_tool_names
    )
    assert "list_offline_conversion_upload_conversion_action_summaries" in (
        public_tool_names
    )
    assert "list_device_performance" in public_tool_names
    assert "list_video_enhancements" in public_tool_names
    assert "summarize_keyword_quality_scores" in public_tool_names
    assert "export_gaql_csv" in public_tool_names
    assert "get_resource_metadata" in public_tool_names
    assert "search_google_ads_fields" in public_tool_names
    assert "apply_recommendations" not in public_tool_names

  def test_tool_visibility_profile_output_schema_is_specific(self):
    tools = {
        tool.name: tool
        for tool in asyncio.run(mcp_server._local_provider.list_tools())
    }
    schema = tools["get_tool_visibility_profile"].output_schema

    assert schema["type"] == "object"
    assert schema["properties"]["mutation_tools_unlocked"] == {
        "type": "boolean"
    }
    assert schema["properties"]["session_rules"] == {
        "type": "array",
        "items": {"type": "object"},
    }
    assert set(schema["required"]) == {
        "mutation_tools_unlocked",
        "session_rules",
    }

  def test_resource_list_includes_live_release_notes(self):
    resources = {
        resource.name: resource
        for resource in asyncio.run(
            mcp_server._local_provider.list_resources()
        )
    }

    assert "get_release_notes" in resources
    assert (
        str(resources["get_release_notes"].uri)
        == "resource://Google_Ads_API_Release_Notes"
    )

  def test_client_can_call_visible_tool_directly_and_via_proxy(self):
    async def _run():
      async with Client(mcp_server) as client:
        with mock.patch(
            "ads_mcp.tools.recommendations.run_gaql_query"
        ) as mock_run:
          mock_run.side_effect = [
              [
                  {
                      "customer.id": "123",
                      "customer.descriptive_name": "Test Customer",
                      "customer.currency_code": "USD",
                      "customer.optimization_score": 0.85,
                      "customer.optimization_score_weight": 0.42,
                      "metrics.optimization_score_uplift": 0.12,
                      "metrics.optimization_score_url": "https://example.com",
                  }
              ],
              [
                  {
                      "segments.recommendation_type": "KEYWORD",
                      "metrics.optimization_score_uplift": 0.05,
                      "metrics.optimization_score_url": (
                          "https://example.com/keyword"
                      ),
                  }
              ],
              [
                  {
                      "customer.id": "123",
                      "customer.descriptive_name": "Test Customer",
                      "customer.currency_code": "USD",
                      "customer.optimization_score": 0.85,
                      "customer.optimization_score_weight": 0.42,
                      "metrics.optimization_score_uplift": 0.12,
                      "metrics.optimization_score_url": "https://example.com",
                  }
              ],
              [
                  {
                      "segments.recommendation_type": "KEYWORD",
                      "metrics.optimization_score_uplift": 0.05,
                      "metrics.optimization_score_url": (
                          "https://example.com/keyword"
                      ),
                  }
              ],
          ]

          direct_result = await client.call_tool(
              "get_optimization_score_summary",
              {"customer_id": "123"},
          )
          proxy_result = await client.call_tool(
              "call_tool",
              {
                  "name": "get_optimization_score_summary",
                  "arguments": {"customer_id": "123"},
              },
          )

        assert direct_result.data["customer_id"] == "123"
        assert direct_result.data["optimization_score"] == 0.85
        assert proxy_result.data["customer_id"] == "123"
        assert proxy_result.data["recommendation_type_breakdown"] == [
            {
                "recommendation_type": "KEYWORD",
                "optimization_score_uplift": 0.05,
                "optimization_score_url": "https://example.com/keyword",
            }
        ]

    asyncio.run(_run())

  def test_client_change_events_return_structured_output_directly_and_via_proxy(
      self,
  ):
    async def _run():
      async with Client(mcp_server) as client:
        rows = [
            {
                "change_event.change_date_time": "2026-03-09 00:00:00",
                "change_event.change_resource_type": "CAMPAIGN",
                "change_event.resource_change_operation": "UPDATE",
                "change_event.resource_name": "customers/123/campaigns/1",
                "change_event.client_type": "GOOGLE_ADS_API",
                "change_event.user_email": "a@example.com",
                "change_event.changed_fields": {"paths": ["campaign.status"]},
            }
        ]

        account_today = date.today()
        with (
            mock.patch(
                "ads_mcp.tools.changes._account_today",
                return_value=(account_today, "Etc/UTC"),
            ),
            mock.patch(
                "ads_mcp.tools.changes.run_gaql_query_page",
                return_value={
                    "rows": rows,
                    "next_page_token": None,
                    "total_results_count": 1,
                },
            ),
        ):
          direct_result = await client.call_tool(
              "list_change_events",
              {"customer_id": "123"},
          )
          proxy_result = await client.call_tool(
              "call_tool",
              {
                  "name": "list_change_events",
                  "arguments": {"customer_id": "123"},
              },
          )

        expected = {
            "change_events": rows,
            "returned_count": 1,
            "total_count": 1,
            "total_page_count": 1,
            "truncated": False,
            "next_page_token": None,
            "page_size": 100,
            "account_time_zone": "Etc/UTC",
            "account_today": account_today.isoformat(),
            "resolved_date_range": {
                "start_date": (account_today - timedelta(days=7)).isoformat(),
                "end_date": account_today.isoformat(),
            },
        }
        assert direct_result.structured_content == expected
        assert proxy_result.structured_content == expected
        assert "change_events" in direct_result.data
        assert len(direct_result.data["change_events"]) == len(rows)
        assert proxy_result.data == expected

    asyncio.run(_run())

  def test_client_change_events_empty_results_remain_structured(self):
    async def _run():
      async with Client(mcp_server) as client:
        account_today = date.today()
        with (
            mock.patch(
                "ads_mcp.tools.changes._account_today",
                return_value=(account_today, "Etc/UTC"),
            ),
            mock.patch(
                "ads_mcp.tools.changes.run_gaql_query_page",
                return_value={
                    "rows": [],
                    "next_page_token": None,
                    "total_results_count": 0,
                },
            ),
        ):
          direct_result = await client.call_tool(
              "list_change_events",
              {"customer_id": "123"},
          )
          proxy_result = await client.call_tool(
              "call_tool",
              {
                  "name": "list_change_events",
                  "arguments": {"customer_id": "123"},
              },
          )

        expected = {
            "change_events": [],
            "returned_count": 0,
            "total_count": 0,
            "total_page_count": 0,
            "truncated": False,
            "next_page_token": None,
            "page_size": 100,
            "account_time_zone": "Etc/UTC",
            "account_today": account_today.isoformat(),
            "resolved_date_range": {
                "start_date": (account_today - timedelta(days=7)).isoformat(),
                "end_date": account_today.isoformat(),
            },
        }
        assert direct_result.structured_content == expected
        assert proxy_result.structured_content == expected
        assert direct_result.data == expected
        assert proxy_result.data == expected

    asyncio.run(_run())

  def test_client_can_proxy_customer_search_term_insights(self):
    async def _run():
      async with Client(mcp_server) as client:
        rows = [
            {
                "customer_search_term_insight.id": "1",
                "customer_search_term_insight.category_label": "Brand",
                "segments.campaign": "customers/123/campaigns/7",
                "segments.search_term": "brand shoes",
                "segments.search_subcategory": "Footwear",
                "metrics.impressions": 100,
                "metrics.clicks": 10,
                "metrics.ctr": 0.1,
                "metrics.conversions": 2,
                "metrics.conversions_value": 50.0,
            }
        ]

        with mock.patch(
            "ads_mcp.tools.search_terms.run_gaql_query_page",
            return_value={
                "rows": rows,
                "next_page_token": None,
                "total_results_count": 1,
            },
        ):
          direct_result = await client.call_tool(
              "list_customer_search_term_insights",
              {"customer_id": "123"},
          )
          proxy_result = await client.call_tool(
              "call_tool",
              {
                  "name": "list_customer_search_term_insights",
                  "arguments": {"customer_id": "123"},
              },
          )

        expected = {
            "customer_search_term_insights": rows,
            "returned_count": 1,
            "total_count": 1,
            "total_page_count": 1,
            "truncated": False,
            "next_page_token": None,
            "page_size": 1000,
        }
        assert direct_result.structured_content == expected
        assert proxy_result.structured_content == expected
        assert direct_result.data == expected
        assert proxy_result.data == expected

    asyncio.run(_run())

  def test_client_search_tools_returns_structured_results_and_empty_lists(
      self,
  ):
    async def _run():
      async with Client(mcp_server) as client:
        populated = await client.call_tool(
            "search_tools",
            {"query": "recent change history"},
        )
        empty = await client.call_tool(
            "search_tools",
            {"query": "quokka narwhal xylophone"},
        )

        assert populated.structured_content["result"][0]["name"] == (
            "get_change_history_extended"
        )
        assert populated.data[0].name == "get_change_history_extended"
        assert populated.data[0].workflow == "changes"
        assert empty.structured_content == {"result": []}
        assert empty.data == []

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "full change history",
          "every account change",
          "all account edits",
          "full audit log",
          "exhaustive account change log",
          "download all changes",
          "download complete changelog",
          "export change history",
          "export full edit history",
          "maximum available change history",
          "maximum revision history",
          "all revisions",
          "every account revision",
          "as much change history as possible",
          "longest available change history",
          "change history as far back as possible",
          "show change history as far back as you can",
          "give me whatever change history is available",
          "show the oldest possible change history",
      ],
  )
  def test_client_search_tools_routes_full_history_to_csv_export(self, query):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "export_change_history_csv"
        )
        assert result.data[0].name == "export_change_history_csv"

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "show change history for campaign 123",
          "changes in the last week",
          "show changes from 2026-06-01 to 2026-07-03",
          "recent audit trail",
          "campaign status history",
          "show campaign settings history",
          "history of campaign configuration",
          "campaign targeting history",
          "campaign budget history",
          "budget history for campaign 123",
          "targeting history for campaign 123",
          "bid strategy history for campaign 123",
          "history of ad group status",
          "status history for ad 456",
          "status history for ad group 456",
          "status history for keyword 789",
          "keyword status history",
          "historical campaign status",
          "historical keyword status",
      ],
  )
  def test_client_search_tools_routes_contextual_history_to_preview(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "get_change_history_extended"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "change events history",
          "granular changes yesterday",
          "show field-level changes",
      ],
  )
  def test_client_search_tools_leaves_granular_history_with_events(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "list_change_events"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "change all campaign budgets",
          "can you change all campaign budgets",
          "could we change all campaign budgets",
          "please change all campaign budgets",
          "can I change all keyword bids",
          "would it be possible to change all budgets",
          "change the maximum CPC for all keywords",
          "could you update all campaign bids",
          "may I update all campaigns",
          "edit campaign ads",
          "I need to edit all keyword bids",
          "I want you to change all keyword bids",
          "let us change all campaign budgets",
          "let's change all campaign budgets",
          "apply all recommendation changes",
          "please apply all recommendation changes",
          "suggest changes to all campaigns",
          "recommend all campaign modifications",
          "advise me on changes to all campaigns",
          "what campaign changes should I make",
          "what changes can I apply",
          "go ahead change all campaign budgets",
          "accept all recommendation changes",
          "we should change all budgets",
          "which campaign changes would improve performance",
          "what modifications might improve performance",
          "recommended campaign changes",
          "please review all proposed campaign changes",
          "export all changes recommended",
          "all budget changes recommended by Google",
          "download all recommendation changes",
          "I'm hoping to change all campaign budgets",
          "how can I change all campaign budgets",
          "what if we change all campaign budgets",
          "help us change all campaign budgets",
          "tell me how to change all campaign budgets",
          "would you be able to change all campaign budgets",
          "I was hoping to change all campaign budgets",
          "how do I change all campaign budgets",
          "what is the best way to change all campaign budgets",
          "full list of changes to make",
          "show all changes we need to make",
          "all changes I want to apply",
          "I am considering all campaign changes",
          "all campaign changes under consideration",
          "give me every change we ought to make",
          "show all changes that need implementing",
          "list the complete set of changes worth making",
          "all pending campaign changes",
          "all future campaign changes",
          "all upcoming campaign changes",
          "all changes needing implementation",
      ],
  )
  def test_client_search_tools_does_not_route_actions_to_history(self, query):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] not in (
            "export_change_history_csv",
            "get_change_history_extended",
            "list_change_events",
            "list_change_statuses",
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "change all campaign budgets",
          "go ahead and change all campaign budgets",
          "go ahead change all campaign budgets",
          "accept all recommendation changes",
          "we should change all budgets",
          "which campaign changes would improve performance",
          "recommended campaign changes",
          "export all changes recommended",
          "full list of changes to make",
          "show all changes we need to make",
          "all changes I want to apply",
          "I am considering all campaign changes",
          "give me every change we ought to make",
          "show all changes that need implementing",
          "list the complete set of changes worth making",
          "all pending campaign changes",
          "all future campaign changes",
          "all upcoming campaign changes",
          "all changes needing implementation",
      ],
  )
  def test_client_search_tools_removes_change_reports_for_actions(self, query):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        result_names = {
            item["name"] for item in result.structured_content["result"]
        }
        assert result_names.isdisjoint(
            {
                "export_change_history_csv",
                "get_change_history_extended",
                "get_competitive_pressure_report",
                "list_change_events",
                "list_change_statuses",
            }
        )

    asyncio.run(_run())

  def test_client_search_tools_keeps_explicit_competitive_action_context(self):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {
                "query": (
                    "recommend campaign changes based on competitive pressure"
                )
            },
        )

        result_names = {
            item["name"] for item in result.structured_content["result"]
        }
        assert "get_competitive_pressure_report" in result_names

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "show all changes in campaign performance over time",
          "show every day over day change in impression share",
          "show every change in CTR",
          "show all average CPC changes over time",
          "show all CPA changes over time",
          "show all ROAS changes over time",
      ],
  )
  def test_client_search_tools_does_not_route_metric_changes_to_history(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        result_names = {
            item["name"] for item in result.structured_content["result"]
        }
        assert result_names.isdisjoint(
            {
                "export_change_history_csv",
                "get_change_history_extended",
                "list_change_events",
                "list_change_statuses",
            }
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "export all month over month changes in conversion rate",
          "export all conversion rate changes",
          "export all changes in audience performance",
          "full CTR change history",
          "show all changes in spend",
          "show every impression change",
          "export all conversion changes",
      ],
  )
  def test_client_search_tools_routes_metric_change_export_to_gaql_csv(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "export_gaql_csv"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "conversion rate change history",
          "history of CTR changes",
          "show a change in spend",
          "show an impression change",
          "show a conversion change",
      ],
  )
  def test_client_search_tools_routes_bounded_metric_changes_to_performance(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "get_campaign_performance"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "show the full history of budget changes",
          "show all edits to campaign 123",
          "all changes already made",
          "all campaign changes made last week",
          "all planned changes made last week",
          "show all recommended changes applied yesterday",
          "all proposed changes already implemented",
          "full campaign status history",
          "full campaign settings history",
          "full campaign budget history",
          "full ad status history",
          "show all max CPC changes",
          "show all target CPA changes",
      ],
  )
  def test_client_search_tools_keeps_account_changes_in_history(self, query):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "export_change_history_csv"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      ("query", "expected_tool"),
      [
          ("full recommendation history", "list_recommendation_subscriptions"),
          (
              "maximum campaign performance history",
              "get_competitive_pressure_report",
          ),
          (
              "show me full campaign performance history",
              "get_competitive_pressure_report",
          ),
          ("history of all campaigns", "get_competitive_pressure_report"),
          ("campaign spend history", "get_competitive_pressure_report"),
          ("complete account audit", "get_optimization_score_summary"),
      ],
  )
  def test_client_search_tools_keeps_unrelated_histories_in_domain(
      self, query, expected_tool
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == expected_tool

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "conversion history",
          "billing history",
          "browser history",
          "keyword performance history",
      ],
  )
  def test_client_search_tools_demotes_change_reports_for_other_histories(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        result_names = {
            item["name"] for item in result.structured_content["result"]
        }
        assert result_names.isdisjoint(
            {
                "export_change_history_csv",
                "get_change_history_extended",
                "get_competitive_pressure_report",
                "list_change_events",
                "list_change_statuses",
            }
        )

    asyncio.run(_run())

  def test_client_search_tools_does_not_treat_performance_as_budget_history(
      self,
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": "campaign budget performance history"},
        )

        assert result.structured_content["result"][0]["name"] not in (
            "export_change_history_csv",
            "get_change_history_extended",
            "list_change_events",
            "list_change_statuses",
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "show all API changes in v24",
          "full Google Ads API changelog",
          "show all billing changes",
          "all browser changes",
          "show release changes in version 24",
          "Google Ads API revision history",
          "browser modification history",
          "billing edit history",
          "v24 changelog",
      ],
  )
  def test_client_search_tools_removes_history_for_unrelated_changes(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        result_names = {
            item["name"] for item in result.structured_content["result"]
        }
        assert result_names.isdisjoint(
            {
                "export_change_history_csv",
                "get_change_history_extended",
                "get_competitive_pressure_report",
                "list_change_events",
                "list_change_statuses",
            }
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "export all asset group assets to csv",
          "export all audience performance",
          "full demographic performance export",
          "export every campaign audience",
          "download all recommendations",
          "dump complete audience performance to disk",
          "dump all recommendations",
          "save all recommendations to disk",
          "write recommendations to disk",
          "persist all recommendations to disk",
          "store recommendations on disk",
          "save all recommendations as a spreadsheet",
          "save demographic performance as XLSX",
          "send asset group assets to disk",
          "archive audience performance on disk",
          "save audience performance locally",
          "store recommendations locally",
      ],
  )
  def test_client_search_tools_routes_large_exports_to_gaql_csv(self, query):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "export_gaql_csv"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "save a recommendation",
          "write a recommendation summary",
          "store a recommendation",
          "show first 10 recommendations",
          "send a recommendation",
          "archive a campaign",
      ],
  )
  def test_client_search_tools_does_not_export_without_spill_intent(
      self, query
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] != (
            "export_gaql_csv"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      "query",
      [
          "list campaign audiences",
          "show all campaign audiences",
          "list the audiences for campaign 123",
          "show me audiences attached to campaign 123",
          "what audiences are on campaign 123",
          "get audiences for campaign 123",
          "list audiences in campaign 123",
          "which audiences are on campaign 123",
          "which audiences are attached to campaign 123",
          "campaign 123 audiences",
          "audiences for campaign 123",
          "audience for campaign 123",
          "campaign audiences for 123",
          "campaign 123 audience targeting",
          "campaign 123 audience criteria",
          "audience targeting on campaign 123",
          "campaign 123 audiences and bid modifiers",
          "compact campaign audiences",
          "audiences targeted by campaign 123",
          "campaign 123's audiences",
          "audience bid modifiers for campaign 123",
          "first page of campaign audiences",
          "campaign audiences page 2",
          "25 campaign audiences",
      ],
  )
  def test_client_search_tools_routes_campaign_audience_lists(self, query):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == (
            "list_campaign_audiences"
        )

    asyncio.run(_run())

  @pytest.mark.parametrize(
      ("query", "expected_tool"),
      [
          ("compare campaign audiences", "diff_campaign_audiences"),
          ("show campaign audience comparisons", "diff_campaign_audiences"),
          ("show campaign audience differences", "diff_campaign_audiences"),
          ("show copied campaign audiences", "diff_campaign_audiences"),
          ("show campaign audiences to copy", "diff_campaign_audiences"),
          (
              "show differences in campaign audiences",
              "diff_campaign_audiences",
          ),
          ("show campaign audience performance", "list_audience_performance"),
          (
              "compare campaign audience performance",
              "list_audience_performance",
          ),
          (
              "show differences in campaign audience performance",
              "list_audience_performance",
          ),
          (
              "show campaign audiences with metrics",
              "list_audience_performance",
          ),
          ("campaign audience stats", "list_audience_performance"),
          (
              "show campaign audience targeting expansion performance",
              "list_targeting_expansion_performance",
          ),
      ],
  )
  def test_client_search_tools_preserves_other_campaign_audience_intents(
      self, query, expected_tool
  ):
    async def _run():
      async with Client(mcp_server) as client:
        result = await client.call_tool(
            "search_tools",
            {"query": query},
        )

        assert result.structured_content["result"][0]["name"] == expected_tool

    asyncio.run(_run())

  @pytest.mark.parametrize(
      ("query", "expected_tool"),
      [
          ("pause campaign 123", "set_campaign_status"),
          ("apply all recommendation changes", "apply_recommendations"),
          (
              "copy campaign audiences from campaign 1 to campaign 2",
              "copy_audiences_between_campaigns",
          ),
          ("remove audiences from campaign 123", "remove_campaign_audiences"),
          ("change all campaign budgets", "update_campaign_budget"),
          ("turn off campaign 123", "set_campaign_status"),
          ("stop campaign 123", "set_campaign_status"),
          ("unpause campaign 123", "set_campaign_status"),
          ("reactivate campaign 123", "set_campaign_status"),
          ("disable campaign 123", "set_campaign_status"),
          ("deactivate campaign 123", "set_campaign_status"),
          ("switch campaign 123 off", "set_campaign_status"),
          ("switch campaign 123 on", "set_campaign_status"),
          ("delete audiences from campaign 123", "remove_campaign_audiences"),
          ("purge audiences from campaign 123", "remove_campaign_audiences"),
          ("wipe campaign audiences", "remove_campaign_audiences"),
          ("accept all recs", "apply_recommendations"),
          (
              "clear campaign audiences from campaign 123",
              "remove_campaign_audiences",
          ),
          ("take audiences off campaign 123", "remove_campaign_audiences"),
      ],
  )
  def test_client_search_tools_routes_visible_mutation_intents(
      self, query, expected_tool
  ):
    async def _run():
      async with Client(mcp_server) as client:
        locked_result = await client.call_tool(
            "search_tools",
            {"query": query},
        )
        locked_names = {
            item["name"] for item in locked_result.structured_content["result"]
        }

        assert expected_tool not in locked_names
        assert locked_names.isdisjoint(
            {
                "export_change_history_csv",
                "get_change_history_extended",
                "get_competitive_pressure_report",
                "list_change_events",
                "list_change_statuses",
            }
        )

        await client.call_tool("unlock_mutation_tools", {})
        unlocked_result = await client.call_tool(
            "search_tools",
            {"query": query},
        )
        assert unlocked_result.structured_content["result"][0]["name"] == (
            expected_tool
        )
        unlocked_names = {
            item["name"]
            for item in unlocked_result.structured_content["result"]
        }
        assert unlocked_names.isdisjoint(
            {
                "export_change_history_csv",
                "get_change_history_extended",
                "get_competitive_pressure_report",
                "list_change_events",
                "list_change_statuses",
            }
        )

        await client.call_tool("lock_mutation_tools", {})

    asyncio.run(_run())

  @pytest.mark.parametrize(
      ("canonical_query", "paired_query", "expected_tool"),
      [
          (
              "show the oldest possible change history",
              "show the oldest available change history",
              "export_change_history_csv",
          ),
          (
              "change history as far back as possible",
              "change history going back as far as available",
              "export_change_history_csv",
          ),
          (
              "budget history for campaign 123",
              "campaign 123 budget history",
              "get_change_history_extended",
          ),
          (
              "targeting history for campaign 123",
              "campaign 123's targeting history",
              "get_change_history_extended",
          ),
          (
              "budget history for campaign 123",
              "history for campaign 123 budget",
              "get_change_history_extended",
          ),
          (
              "status history for ad group 456",
              "ad group 456 status history",
              "get_change_history_extended",
          ),
          (
              "save audience performance locally",
              "locally save audience performance",
              "export_gaql_csv",
          ),
          (
              "save recommendations to a local file",
              "recommendations saved to a local file",
              "export_gaql_csv",
          ),
          (
              "next page of campaign audiences",
              "next campaign audience page",
              "list_campaign_audiences",
          ),
      ],
  )
  def test_client_search_tools_routes_paired_order_equivalents(
      self, canonical_query, paired_query, expected_tool
  ):
    async def _run():
      async with Client(mcp_server) as client:
        for query in (canonical_query, paired_query):
          locked_result = await client.call_tool(
              "search_tools",
              {"query": query},
          )
          assert locked_result.structured_content["result"][0]["name"] == (
              expected_tool
          )

        await client.call_tool("unlock_mutation_tools", {})
        for query in (canonical_query, paired_query):
          unlocked_result = await client.call_tool(
              "search_tools",
              {"query": query},
          )
          assert unlocked_result.structured_content["result"][0]["name"] == (
              expected_tool
          )

        await client.call_tool("lock_mutation_tools", {})

    asyncio.run(_run())

  @pytest.mark.parametrize(
      ("canonical_query", "paired_query"),
      [
          (
              "scheduled campaign changes for next week",
              "campaign changes scheduled for next week",
          ),
          (
              "Google Ads API revision history",
              "revision history of Google Ads API",
          ),
          (
              "browser modification history",
              "modification history for browser",
          ),
          (
              "billing edit history",
              "edit history for billing",
          ),
          (
              "v24 changelog",
              "version 24 revision history",
          ),
          (
              "changelog for v24",
              "revision history for version 24",
          ),
      ],
  )
  def test_client_search_tools_excludes_history_for_paired_non_history_orders(
      self, canonical_query, paired_query
  ):
    async def _run():
      excluded_tools = {
          "export_change_history_csv",
          "get_change_history_extended",
          "get_competitive_pressure_report",
          "list_change_events",
          "list_change_statuses",
      }

      async with Client(mcp_server) as client:
        for query in (canonical_query, paired_query):
          locked_result = await client.call_tool(
              "search_tools",
              {"query": query},
          )
          locked_names = {
              item["name"]
              for item in locked_result.structured_content["result"]
          }
          assert locked_names.isdisjoint(excluded_tools)

        await client.call_tool("unlock_mutation_tools", {})
        for query in (canonical_query, paired_query):
          unlocked_result = await client.call_tool(
              "search_tools",
              {"query": query},
          )
          unlocked_names = {
              item["name"]
              for item in unlocked_result.structured_content["result"]
          }
          assert unlocked_names.isdisjoint(excluded_tools)

        await client.call_tool("lock_mutation_tools", {})

    asyncio.run(_run())

  @pytest.mark.parametrize(
      ("canonical_query", "paired_query", "expected_tool"),
      [
          (
              "disable campaign 123",
              "campaign 123 disable",
              "set_campaign_status",
          ),
          (
              "switch campaign 123 off",
              "campaign 123 switch off",
              "set_campaign_status",
          ),
          (
              "wipe campaign audiences",
              "campaign 123 audiences wipe",
              "remove_campaign_audiences",
          ),
          (
              "accept all recs",
              "all recs accept",
              "apply_recommendations",
          ),
      ],
  )
  def test_client_search_tools_routes_paired_subject_first_mutations(
      self, canonical_query, paired_query, expected_tool
  ):
    async def _run():
      excluded_tools = {
          "export_change_history_csv",
          "get_change_history_extended",
          "get_competitive_pressure_report",
          "list_change_events",
          "list_change_statuses",
      }

      async with Client(mcp_server) as client:
        for query in (canonical_query, paired_query):
          locked_result = await client.call_tool(
              "search_tools",
              {"query": query},
          )
          locked_names = {
              item["name"]
              for item in locked_result.structured_content["result"]
          }
          assert expected_tool not in locked_names
          assert locked_names.isdisjoint(excluded_tools)

        await client.call_tool("unlock_mutation_tools", {})
        for query in (canonical_query, paired_query):
          unlocked_result = await client.call_tool(
              "search_tools",
              {"query": query},
          )
          assert unlocked_result.structured_content["result"][0]["name"] == (
              expected_tool
          )
          unlocked_names = {
              item["name"]
              for item in unlocked_result.structured_content["result"]
          }
          assert unlocked_names.isdisjoint(excluded_tools)

        await client.call_tool("lock_mutation_tools", {})

    asyncio.run(_run())

  def test_call_tool_surfaces_underlying_tool_errors(self):
    async def _run():
      async with Client(mcp_server) as client:
        too_old_start = (date.today() - timedelta(days=31)).isoformat()
        end_date = date.today().isoformat()

        with (
            mock.patch(
                "ads_mcp.tools.changes._account_today",
                return_value=(date.today(), "Etc/UTC"),
            ),
            pytest.raises(ToolError, match="last 30 days"),
        ):
          await client.call_tool(
              "call_tool",
              {
                  "name": "list_change_events",
                  "arguments": {
                      "customer_id": "123",
                      "start_date": too_old_start,
                      "end_date": end_date,
                  },
              },
          )

    asyncio.run(_run())

  def test_client_session_unlock_updates_public_tool_list(self):
    raw_tools = asyncio.run(mcp_server._local_provider.list_tools())
    all_registered = {tool.name for tool in raw_tools}
    locked_visible = {
        tool.name
        for tool in raw_tools
        if MUTATE_TAG not in set(tool.tags or [])
    }

    async def _run():
      async with Client(mcp_server) as client:
        before_names = {tool.name for tool in await client.list_tools()}
        assert before_names == locked_visible | {"search_tools", "call_tool"}
        assert "apply_recommendations" not in before_names

        unlock_result = await client.call_tool("unlock_mutation_tools", {})
        assert unlock_result.data == {"mutation_tools_unlocked": True}

        after_names = {tool.name for tool in await client.list_tools()}
        assert after_names == all_registered | {"search_tools", "call_tool"}
        assert "apply_recommendations" in after_names
        assert "create_label" in after_names

        lock_result = await client.call_tool("lock_mutation_tools", {})
        assert lock_result.data == {"mutation_tools_unlocked": False}

        relocked_names = {tool.name for tool in await client.list_tools()}
        assert relocked_names == before_names

    asyncio.run(_run())

  def test_compact_search_serializer_returns_minimal_shape(self):
    tools = asyncio.run(mcp_server._local_provider.list_tools())
    selected_tools = [
        tool
        for tool in tools
        if tool.name
        in {
            "execute_gaql",
            "list_campaign_search_term_insights",
            "unlock_mutation_tools",
        }
    ]
    selected_tools = sorted(selected_tools, key=lambda tool: tool.name)

    result = compact_search_result_serializer(selected_tools)

    assert result == [
        {
            "name": "execute_gaql",
            "mode": "read",
            "workflow": "reporting",
            "summary": "Executes a GAQL query to get reporting data.",
            "required_args": ["query"],
            "optional_args": [
                "max_rows",
                "max_results",
                "warning_row_threshold",
            ],
        },
        {
            "name": "list_campaign_search_term_insights",
            "mode": "read",
            "workflow": "search_terms",
            "summary": (
                "Lists campaign_search_term_insight rows with key metrics."
            ),
            "required_args": ["campaign_id"],
            "optional_args": [
                "insight_id",
                "date_range",
                "min_clicks",
                "page_token",
            ],
        },
        {
            "name": "unlock_mutation_tools",
            "mode": "control",
            "workflow": "profiles",
            "summary": "Unlocks mutating tools for the current session only.",
        },
    ]
