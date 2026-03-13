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

"""Live smoke tests for the real stdio MCP path and risky read tools."""

import asyncio
import os
from pathlib import Path

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
_RESOLVED_LIVE_CUSTOMER_ID: str | None = None


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


async def _list_tools() -> set[str]:
  async with Client(_live_mcp_config()) as client:
    return {tool.name for tool in await client.list_tools()}


async def _resolve_live_customer_id_async() -> str:
  configured_customer_id = os.getenv("GOOGLE_ADS_LIVE_CUSTOMER_ID")
  candidate_customer_ids = []
  if configured_customer_id:
    candidate_customer_ids.append(configured_customer_id)

  accessible_accounts = await _call_tool("list_accessible_accounts", {})
  candidate_customer_ids.extend(accessible_accounts.data)

  seen_customer_ids = set()
  for customer_id in candidate_customer_ids:
    if customer_id in seen_customer_ids:
      continue
    seen_customer_ids.add(customer_id)

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


def _live_customer_id() -> str:
  global _RESOLVED_LIVE_CUSTOMER_ID
  if _RESOLVED_LIVE_CUSTOMER_ID is None:
    _RESOLVED_LIVE_CUSTOMER_ID = asyncio.run(_resolve_live_customer_id_async())
  return _RESOLVED_LIVE_CUSTOMER_ID


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
