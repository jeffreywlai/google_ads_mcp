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

"""Thin wrappers for change_status and change_event reporting views."""

from datetime import date
from datetime import timedelta
from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import gaql_quote_string
from ads_mcp.tools.api import run_gaql_query_page

_CHANGE_EVENT_MAX_LOOKBACK_DAYS = 30
_CHANGE_HISTORY_RESULT_CAP = 10_000


def _default_date_range(days_back: int) -> tuple[str, str]:
  end_date = date.today()
  start_date = end_date - timedelta(days=days_back)
  return start_date.isoformat(), end_date.isoformat()


def _resolve_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
) -> tuple[str, str]:
  default_start_date, default_end_date = _default_date_range(days_back)
  if not start_date:
    start_date = default_start_date
  if not end_date:
    end_date = default_end_date
  if start_date > end_date:
    raise ToolError("start_date must be on or before end_date.")
  return start_date, end_date


def _resolve_change_event_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
) -> tuple[str, str]:
  """Resolves change_event dates and enforces the API's 30-day lookback."""
  start_date, end_date = _resolve_date_range(start_date, end_date, days_back)
  oldest_supported_start = (
      date.today() - timedelta(days=_CHANGE_EVENT_MAX_LOOKBACK_DAYS)
  ).isoformat()
  if start_date < oldest_supported_start:
    raise ToolError(
        "change_event only supports the last"
        f" {_CHANGE_EVENT_MAX_LOOKBACK_DAYS} days. Use start_date >= "
        f"{oldest_supported_start}."
    )
  return start_date, end_date


change_tool = ads_read_tool(mcp, tags={"changes", "audit"})


@change_tool
def list_change_statuses(
    customer_id: str,
    resource_types: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists changed resources from change_status.

  Args:
      customer_id: Google Ads customer ID.
      resource_types: Optional resource types such as CAMPAIGN or AD_GROUP.
      start_date: Inclusive YYYY-MM-DD start date. Defaults to 7 days ago.
      end_date: Inclusive YYYY-MM-DD end date. Defaults to today.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing change status rows plus completeness metadata.
  """
  validate_limit(limit)
  start_date, end_date = _resolve_date_range(start_date, end_date, 7)

  where_conditions = [
      "change_status.last_change_date_time >= "
      f"{gaql_quote_string(start_date + ' 00:00:00')}",
      "change_status.last_change_date_time <= "
      f"{gaql_quote_string(end_date + ' 23:59:59')}",
  ]
  if resource_types:
    where_conditions.append(
        "change_status.resource_type IN "
        f"({quote_enum_values(resource_types)})"
    )

  query = f"""
      SELECT
        change_status.resource_name,
        change_status.resource_type,
        change_status.resource_status,
        change_status.last_change_date_time
      FROM change_status
      {build_where_clause(where_conditions)}
      ORDER BY change_status.last_change_date_time DESC
      LIMIT {_CHANGE_HISTORY_RESULT_CAP}
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "change_statuses",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  if page["total_results_count"] >= _CHANGE_HISTORY_RESULT_CAP:
    result["truncated"] = True
    result["api_result_cap"] = _CHANGE_HISTORY_RESULT_CAP
  return result


@change_tool
def list_change_events(
    customer_id: str,
    resource_change_operations: list[str] | None = None,
    change_resource_types: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists granular changes from change_event.

  Args:
      customer_id: Google Ads customer ID.
      resource_change_operations: Optional operations such as CREATE,
          UPDATE, or REMOVE.
      change_resource_types: Optional changed resource types.
      start_date: Inclusive YYYY-MM-DD start date. Defaults to 7 days ago.
          Google Ads only exposes change_event for the last 30 days.
      end_date: Inclusive YYYY-MM-DD end date. Defaults to today.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing change event rows plus completeness metadata.
  """
  validate_limit(limit)
  start_date, end_date = _resolve_change_event_date_range(
      start_date, end_date, 7
  )

  where_conditions = [
      "change_event.change_date_time >= "
      f"{gaql_quote_string(start_date + ' 00:00:00')}",
      "change_event.change_date_time <= "
      f"{gaql_quote_string(end_date + ' 23:59:59')}",
  ]
  if resource_change_operations:
    where_conditions.append(
        "change_event.resource_change_operation IN "
        f"({quote_enum_values(resource_change_operations)})"
    )
  if change_resource_types:
    where_conditions.append(
        "change_event.change_resource_type IN "
        f"({quote_enum_values(change_resource_types)})"
    )

  query = f"""
      SELECT
        change_event.change_date_time,
        change_event.change_resource_type,
        change_event.resource_change_operation,
        change_event.resource_name,
        change_event.client_type,
        change_event.user_email,
        change_event.changed_fields
      FROM change_event
      {build_where_clause(where_conditions)}
      ORDER BY change_event.change_date_time DESC
      LIMIT {_CHANGE_HISTORY_RESULT_CAP}
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "change_events",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  if page["total_results_count"] >= _CHANGE_HISTORY_RESULT_CAP:
    result["truncated"] = True
    result["api_result_cap"] = _CHANGE_HISTORY_RESULT_CAP
  return result
