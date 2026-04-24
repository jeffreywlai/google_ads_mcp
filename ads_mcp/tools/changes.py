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
from ads_mcp.tools._gaql import gaql_quote_string
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import run_gaql_query_page

_CHANGE_EVENT_MAX_LOOKBACK_DAYS = 30
_CHANGE_HISTORY_RESULT_CAP = 10_000


def _default_date_range(days_back: int) -> tuple[str, str]:
  end_date = date.today()
  start_date = end_date - timedelta(days=days_back)
  return start_date.isoformat(), end_date.isoformat()


def _parse_date(value: str, field_name: str) -> date:
  if not isinstance(value, str):
    raise ToolError(f"{field_name} must be a YYYY-MM-DD date.")
  try:
    return date.fromisoformat(value)
  except ValueError as exc:
    raise ToolError(f"{field_name} must be a YYYY-MM-DD date.") from exc


def _oldest_change_event_start() -> str:
  return (
      date.today() - timedelta(days=_CHANGE_EVENT_MAX_LOOKBACK_DAYS)
  ).isoformat()


def _resolve_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
) -> tuple[str, str]:
  default_start_date, default_end_date = _default_date_range(days_back)
  start_date = start_date or default_start_date
  end_date = end_date or default_end_date
  start_day = _parse_date(start_date, "start_date")
  end_day = _parse_date(end_date, "end_date")
  if start_day > end_day:
    raise ToolError("start_date must be on or before end_date.")
  return start_day.isoformat(), end_day.isoformat()


def _resolve_change_event_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
) -> tuple[str, str]:
  """Resolves change_event dates and enforces the API's 30-day lookback."""
  start_date, end_date = _resolve_date_range(start_date, end_date, days_back)
  oldest_supported_start = _oldest_change_event_start()
  if start_date < oldest_supported_start:
    raise ToolError(
        "change_event only supports the last"
        f" {_CHANGE_EVENT_MAX_LOOKBACK_DAYS} days. Use start_date >= "
        f"{oldest_supported_start}."
    )
  today = date.today().isoformat()
  if end_date > today:
    raise ToolError(
        "change_event only supports dates through today. Use end_date <= "
        f"{today}."
    )
  return start_date, end_date


def _datetime_range_conditions(
    field_name: str,
    start_date: str,
    end_date: str,
) -> list[str]:
  return [
      f"{field_name} >= " + gaql_quote_string(start_date + " 00:00:00"),
      f"{field_name} <= " + gaql_quote_string(end_date + " 23:59:59"),
  ]


def _build_change_page_response(
    item_key: str,
    page: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
  result = build_paginated_list_response(
      item_key,
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  if page["total_results_count"] >= _CHANGE_HISTORY_RESULT_CAP:
    result["truncated"] = True
    result["api_result_cap"] = _CHANGE_HISTORY_RESULT_CAP
  return result


def _empty_change_events_response(limit: int) -> dict[str, Any]:
  return build_paginated_list_response(
      "change_events",
      [],
      total_count=0,
      page_size=limit,
      next_page_token=None,
  )


def _unavailable_change_event_window(
    start_date: str,
    end_date: str,
    event_start_date: str,
) -> dict[str, str] | None:
  """Returns the older requested range not exposed by change_event."""
  if start_date >= event_start_date:
    return None

  last_unavailable_day = (
      date.fromisoformat(event_start_date) - timedelta(days=1)
  ).isoformat()
  unavailable_end = min(end_date, last_unavailable_day)
  if start_date > unavailable_end:
    return None
  return {"start_date": start_date, "end_date": unavailable_end}


def _change_event_coverage(
    start_date: str,
    end_date: str,
    event_window_used: dict[str, str] | None,
    include_recent_events: bool,
) -> dict[str, Any]:
  """Builds compact coverage metadata for extended change history."""
  oldest_event_start = _oldest_change_event_start()
  coverage = {
      "available": event_window_used is not None,
      "window": event_window_used,
      "full_requested_range_covered": event_window_used
      == {"start_date": start_date, "end_date": end_date},
      "lookback_days": _CHANGE_EVENT_MAX_LOOKBACK_DAYS,
      "api_result_cap": _CHANGE_HISTORY_RESULT_CAP,
  }

  unavailable_window = _unavailable_change_event_window(
      start_date,
      end_date,
      max(start_date, oldest_event_start),
  )
  if unavailable_window:
    coverage["unavailable_window"] = unavailable_window

  if not include_recent_events:
    coverage["reason"] = "include_recent_events is false."
  elif event_window_used is None:
    coverage["reason"] = (
        "The requested range does not overlap the Google Ads "
        "change_event lookback window."
    )
  elif unavailable_window:
    coverage["reason"] = (
        "Older granular change_event rows are unavailable from Google Ads; "
        "use change_status rows for that slice."
    )
  return coverage


change_tool = ads_read_tool(mcp, tags={"changes", "audit"})


@change_tool
def list_change_statuses(
    customer_id: str,
    resource_types: list[str] | str | None = None,
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

  where_conditions = _datetime_range_conditions(
      "change_status.last_change_date_time",
      start_date,
      end_date,
  )
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
  return _build_change_page_response("change_statuses", page, limit)


@change_tool
def list_change_events(
    customer_id: str,
    resource_change_operations: list[str] | str | None = None,
    change_resource_types: list[str] | str | None = None,
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

  where_conditions = _datetime_range_conditions(
      "change_event.change_date_time",
      start_date,
      end_date,
  )
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
  return _build_change_page_response("change_events", page, limit)


@change_tool
def get_change_history_extended(
    customer_id: str,
    resource_types: list[str] | str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_recent_events: bool = True,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Gets change history across windows longer than change_event allows.

  Google Ads only exposes granular change_event rows for the last 30 days.
  This helper always uses change_status for the requested window, and also
  includes recent change_event rows when the requested dates overlap the
  supported 30-day window.

  Args:
      customer_id: Google Ads customer ID.
      resource_types: Optional resource types such as CAMPAIGN or AD_GROUP.
      start_date: Inclusive YYYY-MM-DD start date. Defaults to 90 days ago.
      end_date: Inclusive YYYY-MM-DD end date. Defaults to today.
      include_recent_events: Whether to include granular change_event rows
          for the portion of the window available in Google Ads.
      limit: Maximum rows to return for each underlying section.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with change_statuses for the full range, recent change_events
      when available, and coverage metadata.
  """
  validate_limit(limit)
  start_date, end_date = _resolve_date_range(start_date, end_date, 90)

  statuses = list_change_statuses(
      customer_id=customer_id,
      resource_types=resource_types,
      start_date=start_date,
      end_date=end_date,
      limit=limit,
      login_customer_id=login_customer_id,
  )

  oldest_event_start = _oldest_change_event_start()
  recent_events = _empty_change_events_response(limit)
  event_start_date = max(start_date, oldest_event_start)
  event_window_used = None
  if include_recent_events and event_start_date <= end_date:
    event_window_used = {
        "start_date": event_start_date,
        "end_date": end_date,
    }
    recent_events = list_change_events(
        customer_id=customer_id,
        change_resource_types=resource_types,
        start_date=event_start_date,
        end_date=end_date,
        limit=limit,
        login_customer_id=login_customer_id,
    )

  coverage_note = (
      "change_status covers the requested date range. Granular "
      "change_event rows are only available for the last "
      f"{_CHANGE_EVENT_MAX_LOOKBACK_DAYS} days and are capped at "
      f"{_CHANGE_HISTORY_RESULT_CAP} rows per query."
  )
  if not include_recent_events:
    coverage_note += " Granular change_event rows were not requested."
  elif not event_window_used:
    coverage_note += " The requested range does not overlap that window."

  return {
      "date_range": {"start_date": start_date, "end_date": end_date},
      "change_event_window": event_window_used,
      "change_event_coverage": _change_event_coverage(
          start_date,
          end_date,
          event_window_used,
          include_recent_events,
      ),
      "coverage_note": coverage_note,
      "change_statuses": statuses["change_statuses"],
      "change_status_returned_count": statuses["returned_count"],
      "change_status_total_count": statuses["total_count"],
      "change_status_truncated": statuses["truncated"],
      "recent_change_events": recent_events["change_events"],
      "recent_change_event_returned_count": recent_events["returned_count"],
      "recent_change_event_total_count": recent_events["total_count"],
      "recent_change_event_truncated": recent_events["truncated"],
  }
