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
from typing import Any

import yaml

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tools.api import format_value
from ads_mcp.tools.api import get_ads_client
from ads_mcp.utils import MODULE_DIR
from fastmcp.exceptions import ToolError
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


@mcp.tool()
def get_gaql_doc() -> str:
  """Get compact GAQL syntax reference with grammar, rules, and examples."""
  return _get_gaql_compact_content()


@mcp.resource("resource://Google_Ads_Query_Language")
def get_gaql_doc_resource() -> str:
  """Get Google Ads Query Language (GAQL) guides."""
  return _get_gaql_doc_content()


@mcp.tool()
def get_reporting_view_doc(view: str | None = None) -> str:
  """Get Google Ads API reporting view docs.

  Without a view, returns the list of available view names.
  With a view, returns detailed field metadata in YAML format.
  """
  if view:
    return _get_view_doc_content(view)
  return _get_views_list()


@mcp.resource("resource://Google_Ads_API_Reporting_Views")
def get_reporting_doc() -> str:
  """Get Google Ads API reporting view docs."""
  return _get_reporting_doc_content()


@mcp.resource("resource://views/{view}")
def get_view_doc(view: str) -> str:
  """Get resource view docs for a given Google Ads API view resource."""
  return _get_view_doc_content(view)


_CACHED_FIELDS: dict[str, Any] = {}


@mcp.tool()
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


@mcp.tool()
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
