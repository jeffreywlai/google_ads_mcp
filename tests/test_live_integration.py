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

"""Live smoke and stress tests for the real stdio MCP path."""

import asyncio
from datetime import date
from datetime import timedelta
import os
from pathlib import Path
import uuid

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest
import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
_RESOLVED_LIVE_CUSTOMER_ID: str | None = None
_CONFIGURED_LOGIN_CUSTOMER_ID: str | None = None


def _credentials_path() -> str:
  credentials_candidates = [
      os.getenv("GOOGLE_ADS_CREDENTIALS"),
      str(Path.home() / "google-ads.yaml"),
      str(ROOT_DIR / "google-ads.yaml"),
  ]
  for candidate in credentials_candidates:
    if candidate and Path(candidate).is_file():
      return candidate

  pytest.skip("Google Ads credentials file is not available.")


def _configured_login_customer_id() -> str | None:
  global _CONFIGURED_LOGIN_CUSTOMER_ID
  if _CONFIGURED_LOGIN_CUSTOMER_ID is None:
    with open(_credentials_path(), "r", encoding="utf-8") as f:
      config = yaml.safe_load(f)
    login_customer_id = config.get("login_customer_id")
    _CONFIGURED_LOGIN_CUSTOMER_ID = (
        str(login_customer_id) if login_customer_id else ""
    )
  return _CONFIGURED_LOGIN_CUSTOMER_ID or None


def _live_mcp_config() -> dict[str, object]:
  if os.getenv("GOOGLE_ADS_RUN_LIVE_TESTS") != "1":
    pytest.skip("GOOGLE_ADS_RUN_LIVE_TESTS is not enabled.")

  return {
      "mcpServers": {
          "GoogleAds": {
              "command": "uv",
              "args": [
                  "run",
                  "--directory",
                  str(ROOT_DIR),
                  "-m",
                  "ads_mcp.stdio",
              ],
              "env": {
                  "GOOGLE_ADS_CREDENTIALS": _credentials_path(),
              },
          }
      }
  }


async def _call_tool(name: str, arguments: dict[str, object]) -> object:
  async with Client(_live_mcp_config()) as client:
    return await client.call_tool(name, arguments)


async def _call_tool_via_proxy(
    client: Client,
    name: str,
    arguments: dict[str, object],
) -> object:
  return await client.call_tool(
      "call_tool",
      {
          "name": name,
          "arguments": arguments,
      },
  )


async def _list_tools() -> set[str]:
  async with Client(_live_mcp_config()) as client:
    return {tool.name for tool in await client.list_tools()}


async def _list_accessible_customer_ids_async() -> list[str]:
  configured_customer_id = os.getenv("GOOGLE_ADS_LIVE_CUSTOMER_ID")
  candidate_customer_ids = []
  if configured_customer_id:
    candidate_customer_ids.append(configured_customer_id)

  accessible_accounts = await _call_tool("list_accessible_accounts", {})
  candidate_customer_ids.extend(accessible_accounts.data)

  deduplicated_customer_ids = []
  seen_customer_ids = set()
  for customer_id in candidate_customer_ids:
    if customer_id in seen_customer_ids:
      continue
    seen_customer_ids.add(customer_id)
    deduplicated_customer_ids.append(customer_id)
  return deduplicated_customer_ids


async def _resolve_live_customer_id_async() -> str:
  for customer_id in await _list_accessible_customer_ids_async():

    try:
      await _call_tool(
          "execute_gaql",
          {
              "customer_id": customer_id,
              "query": """
                  SELECT campaign.id, metrics.impressions
                  FROM campaign
                  WHERE campaign.status != REMOVED
                  LIMIT 1
              """,
              "max_rows": 1,
          },
      )
      return customer_id
    except ToolError as exc:
      if "REQUESTED_METRICS_FOR_MANAGER" in str(exc):
        continue
      if "CUSTOMER_NOT_ENABLED" in str(exc):
        continue
      raise

  pytest.skip("No metric-capable client account was found for live tests.")


async def _resolve_accessible_customer_ids_async(
    min_count: int,
) -> list[str]:
  queryable_customer_ids = []
  for customer_id in await _list_accessible_customer_ids_async():
    try:
      result = await _call_tool(
          "execute_gaql",
          {
              "customer_id": customer_id,
              "query": "SELECT customer.id FROM customer LIMIT 1",
              "max_rows": 1,
          },
      )
    except ToolError:
      continue

    rows = result.structured_content["data"]
    if rows and str(rows[0]["customer.id"]) == customer_id:
      queryable_customer_ids.append(customer_id)
    if len(queryable_customer_ids) >= min_count:
      return queryable_customer_ids

  pytest.skip(
      f"Expected at least {min_count} queryable accounts for this test."
  )


def _live_customer_id() -> str:
  global _RESOLVED_LIVE_CUSTOMER_ID
  if _RESOLVED_LIVE_CUSTOMER_ID is None:
    _RESOLVED_LIVE_CUSTOMER_ID = asyncio.run(_resolve_live_customer_id_async())
  return _RESOLVED_LIVE_CUSTOMER_ID


def _mutation_customer_id() -> str:
  return os.getenv("GOOGLE_ADS_LIVE_MUTATION_CUSTOMER_ID") or _live_customer_id()


async def _first_search_campaign_id(customer_id: str) -> str | None:
  result = await _call_tool(
      "execute_gaql",
      {
          "customer_id": customer_id,
          "query": """
              SELECT campaign.id
              FROM campaign
              WHERE campaign.status != REMOVED
                AND campaign.advertising_channel_type = SEARCH
              LIMIT 1
          """,
      },
  )
  rows = result.structured_content["data"]
  if not rows:
    return None
  return str(rows[0]["campaign.id"])


async def _first_performance_max_campaign_id(customer_id: str) -> str | None:
  result = await _call_tool(
      "execute_gaql",
      {
          "customer_id": customer_id,
          "query": """
              SELECT campaign.id
              FROM campaign
              WHERE campaign.status != REMOVED
                AND campaign.advertising_channel_type = PERFORMANCE_MAX
              LIMIT 1
          """,
      },
  )
  rows = result.structured_content["data"]
  if not rows:
    return None
  return str(rows[0]["campaign.id"])


def _keyword_quality_score_key(row: dict[str, object]) -> tuple[str, str, str]:
  return (
      str(row["campaign.id"]),
      str(row["ad_group.id"]),
      str(row["ad_group_criterion.criterion_id"]),
  )


def _resource_id(resource_name: str) -> str:
  return resource_name.rsplit("/", maxsplit=1)[-1]


def test_live_stdio_tools_are_exposed():
  tool_names = asyncio.run(_list_tools())
  assert "execute_gaql" in tool_names
  assert "list_change_events" in tool_names
  assert "list_customer_search_term_insights" in tool_names
  assert "list_keyword_quality_scores" in tool_names


def test_live_execute_gaql_returns_structured_rows():
  customer_id = _live_customer_id()
  result = asyncio.run(
      _call_tool(
          "execute_gaql",
          {
              "customer_id": customer_id,
              "query": "SELECT customer.id FROM customer LIMIT 1",
              "max_rows": 1,
          },
      )
  )

  assert "data" in result.structured_content
  assert result.structured_content["returned_row_count"] == 1
  assert result.structured_content["max_rows_applied"] == 1


def test_live_change_events_defaults_dates_and_preserves_shape():
  customer_id = _live_customer_id()
  result = asyncio.run(
      _call_tool(
          "list_change_events",
          {
              "customer_id": customer_id,
              "limit": 1,
          },
      )
  )

  assert "change_events" in result.structured_content
  assert isinstance(result.structured_content["change_events"], list)


def test_live_change_statuses_smoke():
  customer_id = _live_customer_id()
  result = asyncio.run(
      _call_tool(
          "list_change_statuses",
          {
              "customer_id": customer_id,
              "limit": 1,
          },
      )
  )

  assert "change_statuses" in result.structured_content
  assert isinstance(result.structured_content["change_statuses"], list)


def test_live_customer_search_term_insights_smoke():
  customer_id = _live_customer_id()
  result = asyncio.run(
      _call_tool(
          "list_customer_search_term_insights",
          {
              "customer_id": customer_id,
              "limit": 1,
          },
      )
  )

  assert "customer_search_term_insights" in result.structured_content
  assert isinstance(
      result.structured_content["customer_search_term_insights"], list
  )


def test_live_keyword_quality_scores_include_pagination_metadata():
  customer_id = _live_customer_id()
  result = asyncio.run(
      _call_tool(
          "list_keyword_quality_scores",
          {
              "customer_id": customer_id,
              "limit": 1,
          },
      )
  )

  assert "keyword_quality_scores" in result.structured_content
  assert "total_row_count" in result.structured_content
  assert "total_page_count" in result.structured_content
  assert "next_page_token" in result.structured_content
  assert result.structured_content["page_size"] == 1


def test_live_keyword_quality_scores_pages_are_consistent():
  customer_id = _live_customer_id()

  async def _run():
    async with Client(_live_mcp_config()) as client:
      pages = []
      next_page_token = None

      for _ in range(3):
        arguments = {
            "customer_id": customer_id,
            "limit": 5,
        }
        if next_page_token:
          arguments["page_token"] = next_page_token
        page = await client.call_tool("list_keyword_quality_scores", arguments)
        pages.append(page.structured_content)
        next_page_token = page.structured_content["next_page_token"]
        if not next_page_token:
          break

      first_page = pages[0]
      assert first_page["returned_row_count"] <= 5
      assert first_page["total_row_count"] >= first_page["returned_row_count"]
      if first_page["total_row_count"] <= first_page["returned_row_count"]:
        pytest.skip("Not enough quality-score rows for multi-page testing.")

      assert first_page["next_page_token"] is not None
      expected_total_row_count = first_page["total_row_count"]
      expected_total_page_count = first_page["total_page_count"]
      seen_keys = set()

      for page in pages:
        assert page["page_size"] == 5
        assert page["total_row_count"] == expected_total_row_count
        assert page["total_page_count"] == expected_total_page_count
        page_keys = {
            _keyword_quality_score_key(row)
            for row in page["keyword_quality_scores"]
        }
        assert seen_keys.isdisjoint(page_keys)
        seen_keys.update(page_keys)

  asyncio.run(_run())


def test_live_campaign_search_term_insights_smoke():
  customer_id = _live_customer_id()
  campaign_id = asyncio.run(_first_search_campaign_id(customer_id))
  if not campaign_id:
    pytest.skip("No SEARCH campaign is available for the live smoke test.")

  result = asyncio.run(
      _call_tool(
          "list_campaign_search_term_insights",
          {
              "customer_id": customer_id,
              "campaign_id": campaign_id,
              "limit": 1,
          },
      )
  )

  assert "campaign_search_term_insights" in result.structured_content
  assert "campaign_context" in result.structured_content


def test_live_call_tool_proxy_matches_direct_calls():
  customer_id = _live_customer_id()

  async def _run():
    async with Client(_live_mcp_config()) as client:
      tool_calls = [
          (
              "execute_gaql",
              {
                  "customer_id": customer_id,
                  "query": "SELECT customer.id FROM customer LIMIT 1",
                  "max_rows": 1,
              },
          ),
          (
              "list_customer_search_term_insights",
              {
                  "customer_id": customer_id,
                  "limit": 1,
              },
          ),
          (
              "list_keyword_quality_scores",
              {
                  "customer_id": customer_id,
                  "limit": 2,
              },
          ),
      ]

      for name, arguments in tool_calls:
        direct = await client.call_tool(name, arguments)
        proxied = await _call_tool_via_proxy(client, name, arguments)
        assert direct.structured_content == proxied.structured_content

  asyncio.run(_run())


def test_live_manager_login_customer_id_header_works():
  customer_id = _live_customer_id()
  login_customer_id = _configured_login_customer_id()
  if not login_customer_id:
    pytest.skip("No login_customer_id is configured for the live test.")

  async def _run():
    async with Client(_live_mcp_config()) as client:
      default_result = await client.call_tool(
          "execute_gaql",
          {
              "customer_id": customer_id,
              "query": "SELECT customer.id FROM customer LIMIT 1",
              "max_rows": 1,
          },
      )
      explicit_result = await client.call_tool(
          "execute_gaql",
          {
              "customer_id": customer_id,
              "login_customer_id": login_customer_id,
              "query": "SELECT customer.id FROM customer LIMIT 1",
              "max_rows": 1,
          },
      )
      reset_result = await client.call_tool(
          "execute_gaql",
          {
              "customer_id": customer_id,
              "query": "SELECT customer.id FROM customer LIMIT 1",
              "max_rows": 1,
          },
      )

      for result in (default_result, explicit_result, reset_result):
        assert result.structured_content["data"][0]["customer.id"] == int(
            customer_id
        )

  asyncio.run(_run())


def test_live_mutation_canary_create_and_delete_label():
  customer_id = _mutation_customer_id()
  label_name = f"mcp-live-canary-{uuid.uuid4().hex[:10]}"

  async def _run():
    async with Client(_live_mcp_config()) as client:
      before_tools = {tool.name for tool in await client.list_tools()}
      assert "create_label" not in before_tools

      unlock_result = await client.call_tool("unlock_mutation_tools", {})
      assert unlock_result.structured_content == {
          "mutation_tools_unlocked": True
      }

      unlocked_tools = {tool.name for tool in await client.list_tools()}
      assert "create_label" in unlocked_tools
      assert "delete_label" in unlocked_tools

      created_resource_name = None
      try:
        created = await client.call_tool(
            "create_label",
            {
                "customer_id": customer_id,
                "name": label_name,
                "description": "Live MCP canary label",
            },
        )
        created_resource_name = created.structured_content["resource_name"]
        label_id = _resource_id(created_resource_name)

        label_lookup = await client.call_tool(
            "execute_gaql",
            {
                "customer_id": customer_id,
                "query": (
                    "SELECT label.id, label.name "
                    f"FROM label WHERE label.id = {label_id} LIMIT 1"
                ),
                "max_rows": 1,
            },
        )
        assert label_lookup.structured_content["data"] == [
            {
                "label.id": int(label_id),
                "label.name": label_name,
            }
        ]
      finally:
        if created_resource_name:
          label_id = _resource_id(created_resource_name)
          await client.call_tool(
              "delete_label",
              {
                  "customer_id": customer_id,
                  "label_id": label_id,
              },
          )

        lock_result = await client.call_tool("lock_mutation_tools", {})
        assert lock_result.structured_content == {
            "mutation_tools_unlocked": False
        }
        after_tools = {tool.name for tool in await client.list_tools()}
        assert "create_label" not in after_tools

  asyncio.run(_run())


def test_live_recommendation_and_optimization_smoke():
  customer_id = _live_customer_id()

  async def _run():
    async with Client(_live_mcp_config()) as client:
      optimization = await client.call_tool(
          "get_optimization_score_summary",
          {
              "customer_id": customer_id,
          },
      )
      recommendations = await client.call_tool(
          "list_recommendations",
          {
              "customer_id": customer_id,
              "limit": 5,
          },
      )
      subscriptions = await client.call_tool(
          "list_recommendation_subscriptions",
          {
              "customer_id": customer_id,
              "limit": 5,
          },
      )

      assert optimization.structured_content["customer_id"] == int(customer_id)
      assert "optimization_score" in optimization.structured_content
      assert "optimization_score_weight" in optimization.structured_content
      assert isinstance(
          optimization.structured_content["recommendation_type_breakdown"],
          list,
      )
      assert "recommendations" in recommendations.structured_content
      assert isinstance(
          recommendations.structured_content["recommendations"], list
      )
      assert "recommendation_subscriptions" in subscriptions.structured_content
      assert isinstance(
          subscriptions.structured_content["recommendation_subscriptions"],
          list,
      )

  asyncio.run(_run())


def test_live_parallel_reads_across_customer_contexts_remain_isolated():
  customer_ids = asyncio.run(_resolve_accessible_customer_ids_async(2))

  async def _run():
    async with Client(_live_mcp_config()) as client:
      results = await asyncio.gather(
          *[
              client.call_tool(
                  "execute_gaql",
                  {
                      "customer_id": customer_id,
                      "query": "SELECT customer.id FROM customer LIMIT 1",
                      "max_rows": 1,
                  },
              )
              for customer_id in customer_ids
          ]
      )

    returned_customer_ids = [
        str(result.structured_content["data"][0]["customer.id"])
        for result in results
    ]
    assert returned_customer_ids == customer_ids

  asyncio.run(_run())


def test_live_performance_max_reads_smoke():
  customer_id = _live_customer_id()
  campaign_id = asyncio.run(_first_performance_max_campaign_id(customer_id))
  if not campaign_id:
    pytest.skip("No Performance Max campaign is available for the live test.")

  async def _run():
    async with Client(_live_mcp_config()) as client:
      placements = await client.call_tool(
          "list_performance_max_placements",
          {
              "customer_id": customer_id,
              "campaign_id": campaign_id,
              "limit": 1,
          },
      )
      assets = await client.call_tool(
          "list_asset_group_assets",
          {
              "customer_id": customer_id,
              "campaign_id": campaign_id,
              "limit": 1,
          },
      )
      combinations = await client.call_tool(
          "list_asset_group_top_combinations",
          {
              "customer_id": customer_id,
              "campaign_id": campaign_id,
              "limit": 1,
          },
      )

      assert "performance_max_placements" in placements.structured_content
      assert isinstance(
          placements.structured_content["performance_max_placements"], list
      )
      assert "asset_group_assets" in assets.structured_content
      assert isinstance(assets.structured_content["asset_group_assets"], list)
      assert "asset_group_top_combinations" in combinations.structured_content
      assert isinstance(
          combinations.structured_content["asset_group_top_combinations"], list
      )

  asyncio.run(_run())


def test_live_empty_results_preserve_shape():
  customer_id = _live_customer_id()
  tomorrow = (date.today() + timedelta(days=1)).isoformat()

  async def _run():
    async with Client(_live_mcp_config()) as client:
      change_events = await client.call_tool(
          "list_change_events",
          {
              "customer_id": customer_id,
              "start_date": tomorrow,
              "end_date": tomorrow,
              "limit": 1,
          },
      )
      customer_search_terms = await client.call_tool(
          "list_customer_search_term_insights",
          {
              "customer_id": customer_id,
              "insight_id": "99999999999",
              "limit": 1,
          },
      )
      campaign_search_terms = await client.call_tool(
          "list_campaign_search_term_insights",
          {
              "customer_id": customer_id,
              "campaign_id": "99999999999",
              "limit": 1,
          },
      )
      quality_scores = await client.call_tool(
          "list_keyword_quality_scores",
          {
              "customer_id": customer_id,
              "campaign_ids": ["99999999999"],
              "limit": 5,
          },
      )

      assert change_events.structured_content == {"change_events": []}
      assert customer_search_terms.structured_content == {
          "customer_search_term_insights": []
      }
      assert campaign_search_terms.structured_content == {
          "campaign_search_term_insights": [],
          "campaign_context": {},
      }
      assert quality_scores.structured_content["keyword_quality_scores"] == []
      assert quality_scores.structured_content["returned_row_count"] == 0
      assert quality_scores.structured_content["total_row_count"] == 0
      assert quality_scores.structured_content["total_page_count"] == 0
      assert quality_scores.structured_content["next_page_token"] is None

  asyncio.run(_run())
