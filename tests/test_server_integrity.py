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
import re
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.visibility import Visibility
import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.tooling import MUTATE_TAG
from ads_mcp.tooling import compact_search_result_serializer
from ads_mcp.tools import ad_groups
from ads_mcp.tools import ads
from ads_mcp.tools import api
from ads_mcp.tools import campaigns
from ads_mcp.tools import changes
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
        "list_accessible_accounts",
    ],
    campaigns: [
        "set_campaign_status",
        "update_campaign_budget",
    ],
    ad_groups: [
        "set_ad_group_status",
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
        "get_campaign_conversion_goals",
        "list_keyword_quality_scores",
        "summarize_keyword_quality_scores",
        "list_rsa_ad_strength",
        "list_conversion_actions",
        "list_audience_performance",
    ],
}


# ===================================================================
# 1. Tool registration: all 64 tools exist as callable functions
# ===================================================================


class TestToolRegistration:

  def test_total_tool_count_is_64(self):
    total = sum(len(fns) for fns in TOOL_MODULES.values())
    assert total == 64, f"Expected 64 tools, found {total}"

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

  def test_ad_group_status_only_sets_status(self):
    p, op = self._make_mock("ads_mcp.tools.ad_groups")
    ad_groups.set_ad_group_status("123", "456", "ENABLED")
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

    assert len(registered_tools) == 64
    for tool_name in sorted(registered_tools):
      tool = registered_tools[tool_name]
      assert tool.tags, f"{tool_name} should have at least one tag"
      assert (
          tool.annotations is not None
      ), f"{tool_name} should have FastMCP annotations"

    assert registered_tools["execute_gaql"].annotations.readOnlyHint is True
    assert MUTATE_TAG in registered_tools["apply_recommendations"].tags
    assert registered_tools["delete_label"].annotations.destructiveHint is True
    assert (
        registered_tools["unlock_mutation_tools"].annotations.openWorldHint
        is False
    )

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
    assert "list_device_performance" in public_tool_names
    assert "summarize_keyword_quality_scores" in public_tool_names
    assert "search_google_ads_fields" in public_tool_names
    assert "apply_recommendations" not in public_tool_names

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

        with mock.patch(
            "ads_mcp.tools.changes.run_gaql_query",
            return_value=rows,
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

        assert direct_result.structured_content == {"change_events": rows}
        assert proxy_result.structured_content == {"change_events": rows}
        assert direct_result.data.change_events == rows
        assert proxy_result.data == {"change_events": rows}

    asyncio.run(_run())

  def test_client_change_events_empty_results_remain_structured(self):
    async def _run():
      async with Client(mcp_server) as client:
        with mock.patch(
            "ads_mcp.tools.changes.run_gaql_query",
            return_value=[],
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

        assert direct_result.structured_content == {"change_events": []}
        assert proxy_result.structured_content == {"change_events": []}
        assert direct_result.data.change_events == []
        assert proxy_result.data == {"change_events": []}

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
            "ads_mcp.tools.search_terms.run_gaql_query",
            return_value=rows,
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

        expected = {"customer_search_term_insights": rows}
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
            {"query": "change events history"},
        )
        empty = await client.call_tool(
            "search_tools",
            {"query": "quokka narwhal xylophone"},
        )

        assert populated.structured_content["result"][0]["name"] == (
            "list_change_events"
        )
        assert populated.data[0].name == "list_change_events"
        assert populated.data[0].workflow == "changes"
        assert empty.structured_content == {"result": []}
        assert empty.data == []

    asyncio.run(_run())

  def test_call_tool_surfaces_underlying_tool_errors(self):
    async def _run():
      async with Client(mcp_server) as client:
        too_old_start = (date.today() - timedelta(days=31)).isoformat()
        end_date = date.today().isoformat()

        with pytest.raises(ToolError, match="last 30 days"):
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
            "optional_args": ["max_rows"],
        },
        {
            "name": "list_campaign_search_term_insights",
            "mode": "read",
            "workflow": "search_terms",
            "summary": (
                "Lists campaign_search_term_insight rows with key metrics."
            ),
            "optional_args": [
                "campaign_id",
                "campaign_ids",
                "date_range",
                "min_clicks",
            ],
        },
        {
            "name": "unlock_mutation_tools",
            "mode": "control",
            "workflow": "profiles",
            "summary": "Unlocks mutating tools for the current session only.",
        },
    ]
