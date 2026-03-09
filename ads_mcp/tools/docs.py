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

"""This module provides tools for accessing Google Ads API documentation."""

import os
import re
from typing import Any

import yaml

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tooling import local_read_tool
from ads_mcp.tooling import session_control_tool
from ads_mcp.tools.api import format_value
from ads_mcp.tools.api import get_ads_client
from ads_mcp.utils import MODULE_DIR
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.transforms.visibility import disable_components
from fastmcp.server.transforms.visibility import enable_components
from fastmcp.server.transforms.visibility import get_visibility_rules
from google.ads.googleads.errors import GoogleAdsException


def _get_gaql_compact_content() -> str:
  """Reads the compact GAQL documentation."""
  with open(
      os.path.join(MODULE_DIR, "context/GAQL_compact.md"),
      "r",
      encoding="utf-8",
  ) as f:
    data = f.read()
  return data


def _get_gaql_doc_content() -> str:
  """Reads the full GAQL documentation."""
  with open(
      os.path.join(MODULE_DIR, "context/GAQL.md"), "r", encoding="utf-8"
  ) as f:
    data = f.read()
  return data


def _get_reporting_doc_content() -> str:
  """Reads the general reporting documentation."""
  with open(
      os.path.join(MODULE_DIR, "context/Google_Ads_API_Reporting_Views.md"),
      "r",
      encoding="utf-8",
  ) as f:
    data = f.read()
  return data


def _get_views_list() -> str:
  """Reads the list of available view names."""
  with open(
      os.path.join(MODULE_DIR, "context/views.yaml"), "r", encoding="utf-8"
  ) as f:
    data = f.read()
  return data


def _get_tool_guide_content() -> str:
  """Reads the compact tool guide."""
  with open(
      os.path.join(MODULE_DIR, "context/tool_guide.yaml"),
      "r",
      encoding="utf-8",
  ) as f:
    data = f.read()
  return data


def _topic_matches(topic: str, *texts: str) -> bool:
  """Returns True when all topic tokens appear across the provided texts."""
  topic_tokens = re.findall(r"[a-z0-9]+", topic.lower())
  if not topic_tokens:
    return False

  haystack = " ".join(texts).lower()
  return all(token in haystack for token in topic_tokens)


def _get_view_doc_content(view: str) -> str:
  """Reads documentation for a specific view."""
  expected_dir = os.path.realpath(os.path.join(MODULE_DIR, "context", "views"))
  target_file = os.path.join(expected_dir, f"{view}.yaml")
  resolved_path = os.path.realpath(target_file)

  if not resolved_path.startswith(expected_dir):
    return "Invalid view name."
  try:
    with open(
        target_file,
        "r",
        encoding="utf-8",
    ) as f:
      data = f.read()
  except FileNotFoundError as exc:
    raise ToolError(
        f"No view resource with the name {view} was found."
    ) from exc
  return data


doc_tool = local_read_tool(mcp, tags={"docs"})
tool_guide_tool = local_read_tool(mcp, tags={"docs", "guide"})
ads_field_tool = ads_read_tool(mcp, tags={"docs", "fields", "gaql"})
visibility_tool = session_control_tool(
    mcp,
    tags={"profiles", "visibility"},
)


@doc_tool
def get_gaql_doc() -> str:
  """Get compact GAQL syntax reference with grammar, rules, and examples."""
  return _get_gaql_compact_content()


@mcp.resource("resource://Google_Ads_Query_Language")
def get_gaql_doc_resource() -> str:
  """Get Google Ads Query Language (GAQL) guides."""
  return _get_gaql_doc_content()


@doc_tool
def get_reporting_view_doc(view: str | None = None) -> str:
  """Get Google Ads API reporting view docs.

  Without a view, returns the list of available view names.
  With a view, returns detailed field metadata in YAML format.
  """
  if view:
    return _get_view_doc_content(view)
  return _get_views_list()


@tool_guide_tool
def get_tool_guide(topic: str | None = None) -> str:
  """Get a compact map of tools and when to use them.

  Without a topic, returns the full guide.
  With a topic, returns only matching categories and tools.
  """
  content = _get_tool_guide_content()
  if not topic:
    return content

  guide = yaml.safe_load(content)
  filtered_categories = {}

  for category_name, category_data in guide["categories"].items():
    summary = category_data.get("summary", "")
    category_match = _topic_matches(topic, category_name, summary)
    matched_tools = {
        tool_name: tool_summary
        for tool_name, tool_summary in category_data.get("tools", {}).items()
        if _topic_matches(topic, tool_name, tool_summary)
    }

    if category_match or matched_tools:
      filtered_category = dict(category_data)
      if matched_tools and not category_match:
        filtered_category["tools"] = matched_tools
      filtered_categories[category_name] = filtered_category

  if not filtered_categories:
    raise ToolError(f"No tool guide entries matched topic '{topic}'.")

  filtered_guide = {
      "principles": guide["principles"],
      "categories": filtered_categories,
  }
  return yaml.safe_dump(filtered_guide, sort_keys=False)


@mcp.resource("resource://Google_Ads_API_Reporting_Views")
def get_reporting_doc() -> str:
  """Get Google Ads API reporting view docs."""
  return _get_reporting_doc_content()


@mcp.resource("resource://Google_Ads_MCP_Tool_Guide")
def get_tool_guide_resource() -> str:
  """Get a compact map of Google Ads MCP tools and selection hints."""
  return _get_tool_guide_content()


@mcp.resource("resource://views/{view}")
def get_view_doc(view: str) -> str:
  """Get resource view docs for a given Google Ads API view resource."""
  return _get_view_doc_content(view)


_CACHED_FIELDS: dict[str, Any] = {}


@doc_tool
def get_reporting_fields_doc(fields: list[str]) -> str:
  """Get detailed docs for specific Google Ads API reporting query fields."""
  global _CACHED_FIELDS
  if not _CACHED_FIELDS:
    with open(
        os.path.join(MODULE_DIR, "context/fields.yaml"),
        "r",
        encoding="utf-8",
    ) as f:
      _CACHED_FIELDS = yaml.safe_load(f)

  fields_info = {field: _CACHED_FIELDS.get(field) for field in fields}
  missing_fields = [field for field in fields if not fields_info[field]]
  if missing_fields:
    raise ToolError("Unknown fields: " + ", ".join(missing_fields))

  return yaml.dump(fields_info)


@visibility_tool
async def get_tool_visibility_profile(
    ctx: Context,
) -> dict[str, Any]:
  """Gets the current session tool visibility profile."""
  rules = await get_visibility_rules(ctx)
  mutation_tools_unlocked = False
  for rule in rules:
    if set(rule.get("tags", [])) == {"mutate"} and set(
        rule.get("components", [])
    ) == {"tool"}:
      mutation_tools_unlocked = bool(rule.get("enabled"))
  return {
      "mutation_tools_unlocked": mutation_tools_unlocked,
      "session_rules": rules,
  }


@visibility_tool
async def unlock_mutation_tools(
    ctx: Context,
) -> dict[str, bool]:
  """Unlocks mutating tools for the current session only."""
  await enable_components(ctx, tags={"mutate"}, components={"tool"})
  return {"mutation_tools_unlocked": True}


@visibility_tool
async def lock_mutation_tools(
    ctx: Context,
) -> dict[str, bool]:
  """Locks mutating tools for the current session only."""
  await disable_components(ctx, tags={"mutate"}, components={"tool"})
  return {"mutation_tools_unlocked": False}


@ads_field_tool
def search_google_ads_fields(
    query: str,
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
  """Searches live GoogleAdsField metadata to help build GAQL queries.

  Args:
      query: A GoogleAdsFieldService query, for example:
          SELECT name, category, selectable WHERE name LIKE 'campaign.%'
      limit: Maximum number of fields to return.

  Returns:
      A dict containing live field metadata rows.
  """
  if not query.strip():
    raise ToolError("query must not be empty.")
  if limit <= 0:
    raise ToolError("limit must be greater than 0.")

  ads_client = get_ads_client()
  field_service = ads_client.get_service("GoogleAdsFieldService")

  try:
    pager = field_service.search_google_ads_fields(
        request={
            "query": query,
            "page_size": min(limit, 1000),
        }
    )
    fields = []
    for index, field in enumerate(pager):
      if index >= limit:
        break
      fields.append(format_value(field))
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"fields": fields}
