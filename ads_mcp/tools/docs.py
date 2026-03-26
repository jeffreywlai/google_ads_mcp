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
import urllib.error
import urllib.request
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


_TEXT_FILE_CACHE: dict[str, tuple[float, str]] = {}
_YAML_FILE_CACHE: dict[str, tuple[float, Any]] = {}
_CACHED_FIELDS: dict[str, Any] = {}
_CACHED_FIELDS_MTIME: float | None = None
_LIVE_RELEASE_NOTES_URL = (
    "https://developers.google.com/google-ads/api/docs/release-notes"
)
_REMOTE_DOC_USER_AGENT = "Mozilla/5.0"


def _read_cached_text(path: str) -> str:
  """Reads a text file with mtime-based process-local caching."""
  cache_mtime = os.path.getmtime(path)
  cache_entry = _TEXT_FILE_CACHE.get(path)
  if cache_entry and cache_entry[0] == cache_mtime:
    return cache_entry[1]

  with open(path, "r", encoding="utf-8") as f:
    data = f.read()

  _TEXT_FILE_CACHE[path] = (cache_mtime, data)
  return data


def _load_cached_yaml(path: str) -> Any:
  """Loads a YAML file with mtime-based process-local caching."""
  cache_mtime = os.path.getmtime(path)
  cache_entry = _YAML_FILE_CACHE.get(path)
  if cache_entry and cache_entry[0] == cache_mtime:
    return cache_entry[1]

  with open(path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)

  _YAML_FILE_CACHE[path] = (cache_mtime, data)
  return data


def _read_live_doc(url: str) -> str:
  """Fetches a live Google Ads documentation page."""
  request = urllib.request.Request(
      url,
      headers={"User-Agent": _REMOTE_DOC_USER_AGENT},
  )
  try:
    with urllib.request.urlopen(request, timeout=20) as response:
      return response.read().decode("utf-8")
  except urllib.error.URLError as exc:
    raise ToolError(
        f"Failed to fetch live Google Ads documentation from {url}."
    ) from exc


def _get_gaql_compact_content() -> str:
  """Reads the compact GAQL documentation."""
  return _read_cached_text(os.path.join(MODULE_DIR, "context/GAQL_compact.md"))


def _get_gaql_doc_content() -> str:
  """Reads the full GAQL documentation."""
  return _read_cached_text(os.path.join(MODULE_DIR, "context/GAQL.md"))


def _get_reporting_doc_content() -> str:
  """Reads the general reporting documentation."""
  return _read_cached_text(
      os.path.join(MODULE_DIR, "context/Google_Ads_API_Reporting_Views.md")
  )


def _get_views_list() -> str:
  """Reads the list of available view names."""
  return _read_cached_text(os.path.join(MODULE_DIR, "context/views.yaml"))


def _get_tool_guide_content() -> str:
  """Reads the compact tool guide."""
  return _read_cached_text(os.path.join(MODULE_DIR, "context/tool_guide.yaml"))


def _build_resource_metadata_query(resource_name: str) -> str:
  """Builds a GoogleAdsFieldService query for one GAQL resource."""
  if not re.fullmatch(r"[a-z][a-z0-9_]*", resource_name):
    raise ToolError(
        "resource_name must be a snake_case Google Ads resource name."
    )

  return (
      "SELECT name, selectable, filterable, sortable "
      f"WHERE name LIKE '{resource_name}.%'"
  )


def _search_google_ads_field_rows(
    query: str,
    limit: int,
) -> list[Any]:
  """Runs a GoogleAdsFieldService query and returns raw field rows."""
  ads_client = get_ads_client()
  field_service = ads_client.get_service("GoogleAdsFieldService")
  pager = field_service.search_google_ads_fields(
      request={
          "query": query,
          "page_size": min(limit, 1000),
      }
  )
  rows = []
  for index, field in enumerate(pager):
    if index >= limit:
      break
    rows.append(field)
  return rows


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
    data = _read_cached_text(target_file)
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


@ads_field_tool
def get_resource_metadata(resource_name: str) -> dict[str, Any]:
  """Get selectable, filterable, and sortable fields for one resource.

  Args:
      resource_name: Google Ads resource name, for example `campaign`
          or `ad_group`.

  Returns:
      A dict containing sorted field names grouped by metadata type.
  """
  query = _build_resource_metadata_query(resource_name)

  try:
    fields = _search_google_ads_field_rows(query, limit=5000)
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  resource_prefix = f"{resource_name}."
  selectable = []
  filterable = []
  sortable = []

  for field in fields:
    field_name = getattr(field, "name", "")
    if not field_name.startswith(resource_prefix):
      continue
    if field.selectable:
      selectable.append(field_name)
    if field.filterable:
      filterable.append(field_name)
    if field.sortable:
      sortable.append(field_name)

  return {
      "resource": resource_name,
      "selectable": sorted(selectable),
      "filterable": sorted(filterable),
      "sortable": sorted(sortable),
  }


@tool_guide_tool
def get_tool_guide(topic: str | None = None) -> str:
  """Get a compact map of tools and when to use them.

  Without a topic, returns the full guide.
  With a topic, returns only matching categories and tools.
  """
  tool_guide_path = os.path.join(MODULE_DIR, "context/tool_guide.yaml")
  content = _read_cached_text(tool_guide_path)
  if not topic:
    return content

  guide = _load_cached_yaml(tool_guide_path)
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


@mcp.resource(
    "resource://Google_Ads_API_Release_Notes",
    mime_type="text/html",
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
def get_release_notes() -> str:
  """Get live Google Ads API release notes."""
  return _read_live_doc(_LIVE_RELEASE_NOTES_URL)


@mcp.resource("resource://views/{view}")
def get_view_doc(view: str) -> str:
  """Get resource view docs for a given Google Ads API view resource."""
  return _get_view_doc_content(view)


@doc_tool
def get_reporting_fields_doc(fields: list[str]) -> str:
  """Get detailed docs for specific Google Ads API reporting query fields."""
  global _CACHED_FIELDS, _CACHED_FIELDS_MTIME
  fields_path = os.path.join(MODULE_DIR, "context/fields.yaml")
  current_mtime = os.path.getmtime(fields_path)
  if not _CACHED_FIELDS or _CACHED_FIELDS_MTIME != current_mtime:
    with open(fields_path, "r", encoding="utf-8") as f:
      _CACHED_FIELDS = yaml.safe_load(f)
    _CACHED_FIELDS_MTIME = current_mtime

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

  try:
    fields = [
        format_value(field)
        for field in _search_google_ads_field_rows(query, limit=limit)
    ]
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"fields": fields}
