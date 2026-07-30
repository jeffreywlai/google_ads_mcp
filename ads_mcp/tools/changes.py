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

from collections.abc import Callable
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tooling import local_write_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import gaql_quote_string
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import run_gaql_query
from ads_mcp.tools.api import run_gaql_query_page
from ads_mcp.tools.api import write_rows_to_temp_csv


_CHANGE_STATUS_MAX_LOOKBACK_DAYS = 90
_CHANGE_EVENT_MAX_LOOKBACK_DAYS = 30
_CHANGE_HISTORY_RESULT_CAP = 10_000
_DEFAULT_EXPORT_QUERY_BUDGET = 200
_CHANGE_STATUS_EXPORT_FIELDS = [
    "change_status.resource_name",
    "change_status.resource_type",
    "change_status.resource_status",
    "change_status.last_change_date_time",
    "change_status.ad_group",
    "change_status.ad_group_ad",
    "change_status.ad_group_asset",
    "change_status.ad_group_bid_modifier",
    "change_status.ad_group_criterion",
    "change_status.asset",
    "change_status.asset_group",
    "change_status.asset_set",
    "change_status.campaign",
    "change_status.campaign_asset",
    "change_status.campaign_asset_set",
    "change_status.campaign_budget",
    "change_status.campaign_criterion",
    "change_status.campaign_shared_set",
    "change_status.combined_audience",
    "change_status.customer_asset",
    "change_status.shared_set",
]
_CHANGE_EVENT_EXPORT_FIELDS = [
    "change_event.change_date_time",
    "change_event.change_resource_type",
    "change_event.resource_change_operation",
    "change_event.resource_name",
    "change_event.change_resource_name",
    "change_event.campaign",
    "change_event.ad_group",
    "change_event.asset",
    "change_event.client_type",
    "change_event.user_email",
    "change_event.changed_fields",
    "change_event.old_resource",
    "change_event.new_resource",
]


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


def _oldest_supported_start(lookback_days: int) -> str:
  """Returns the first date in an inclusive lookback window."""
  return (date.today() - timedelta(days=lookback_days - 1)).isoformat()


def _oldest_change_status_start() -> str:
  return _oldest_supported_start(_CHANGE_STATUS_MAX_LOOKBACK_DAYS)


def _oldest_change_event_start() -> str:
  return _oldest_supported_start(_CHANGE_EVENT_MAX_LOOKBACK_DAYS)


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


def _resolve_supported_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
    resource_name: str,
    max_lookback_days: int,
) -> tuple[str, str]:
  """Resolves dates and enforces a Google Ads resource lookback."""
  start_date, end_date = _resolve_date_range(start_date, end_date, days_back)
  oldest_supported_start = _oldest_supported_start(max_lookback_days)
  if start_date < oldest_supported_start:
    raise ToolError(
        f"{resource_name} only supports the last {max_lookback_days} days. "
        "Use start_date >= "
        f"{oldest_supported_start}."
    )
  today = date.today().isoformat()
  if end_date > today:
    raise ToolError(
        f"{resource_name} only supports dates through today. Use end_date <= "
        f"{today}."
    )
  return start_date, end_date


def _resolve_change_status_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
) -> tuple[str, str]:
  return _resolve_supported_date_range(
      start_date,
      end_date,
      days_back,
      "change_status",
      _CHANGE_STATUS_MAX_LOOKBACK_DAYS,
  )


def _resolve_change_event_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
) -> tuple[str, str]:
  return _resolve_supported_date_range(
      start_date,
      end_date,
      days_back,
      "change_event",
      _CHANGE_EVENT_MAX_LOOKBACK_DAYS,
  )


def _datetime_range_conditions(
    field_name: str,
    start_date: str,
    end_date: str,
) -> list[str]:
  return [
      f"{field_name} >= " + gaql_quote_string(start_date + " 00:00:00"),
      f"{field_name} <= " + gaql_quote_string(end_date + " 23:59:59"),
  ]


def _format_datetime(value: datetime) -> str:
  return value.strftime("%Y-%m-%d %H:%M:%S")


def _datetime_window_conditions(
    field_name: str,
    start_datetime: datetime,
    end_datetime: datetime,
) -> list[str]:
  return [
      f"{field_name} >= "
      + gaql_quote_string(_format_datetime(start_datetime)),
      f"{field_name} <= " + gaql_quote_string(_format_datetime(end_datetime)),
  ]


def _date_range_datetimes(
    start_date: str,
    end_date: str,
) -> tuple[datetime, datetime]:
  return (
      datetime.combine(date.fromisoformat(start_date), datetime.min.time()),
      datetime.combine(
          date.fromisoformat(end_date), datetime.max.time()
      ).replace(microsecond=0),
  )


def _available_date_window(
    start_date: str,
    end_date: str,
    oldest_supported_start: str,
) -> dict[str, str] | None:
  effective_start = max(start_date, oldest_supported_start)
  effective_end = min(end_date, date.today().isoformat())
  if effective_start > effective_end:
    return None
  return {
      "start_date": effective_start,
      "end_date": effective_end,
  }


def _validate_query_budget(max_queries_per_resource: int) -> None:
  if isinstance(max_queries_per_resource, bool) or not isinstance(
      max_queries_per_resource, int
  ):
    raise ToolError("max_queries_per_resource must be an integer.")
  if max_queries_per_resource <= 0:
    raise ToolError("max_queries_per_resource must be greater than 0.")


def _build_export_query(
    select_fields: list[str],
    from_resource: str,
    datetime_field: str,
    resource_type_field: str,
    resource_types: list[str],
    start_datetime: datetime,
    end_datetime: datetime,
) -> str:
  where_conditions = _datetime_window_conditions(
      datetime_field,
      start_datetime,
      end_datetime,
  )
  if resource_types:
    where_conditions.append(
        f"{resource_type_field} IN ({quote_enum_values(resource_types)})"
    )
  return f"""
      SELECT
        {", ".join(select_fields)}
      FROM {from_resource}
      {build_where_clause(where_conditions)}
      ORDER BY {datetime_field} DESC
      LIMIT {_CHANGE_HISTORY_RESULT_CAP}
  """


def _collect_complete_change_rows(
    query_builder: Callable[[datetime, datetime], str],
    customer_id: str,
    start_datetime: datetime,
    end_datetime: datetime,
    login_customer_id: str | None,
    max_queries: int,
) -> dict[str, Any]:
  """Collects capped change rows by repeatedly splitting time windows."""

  def _query_window(
      window_start: datetime,
      window_end: datetime,
  ) -> dict[str, Any]:
    return {
        "start": window_start,
        "end": window_end,
        "rows": run_gaql_query(
            query=query_builder(window_start, window_end),
            customer_id=customer_id,
            login_customer_id=login_customer_id,
        ),
    }

  leaves = [_query_window(start_datetime, end_datetime)]
  query_count = 1
  while query_count + 2 <= max_queries:
    splittable = [
        (index, leaf)
        for index, leaf in enumerate(leaves)
        if len(leaf["rows"]) >= _CHANGE_HISTORY_RESULT_CAP
        and leaf["start"] < leaf["end"]
    ]
    if not splittable:
      break

    leaf_index, leaf = max(
        splittable,
        key=lambda item: (item[1]["end"] - item[1]["start"]).total_seconds(),
    )
    span_seconds = int((leaf["end"] - leaf["start"]).total_seconds())
    midpoint = leaf["start"] + timedelta(seconds=span_seconds // 2)
    later = _query_window(midpoint + timedelta(seconds=1), leaf["end"])
    earlier = _query_window(leaf["start"], midpoint)
    leaves[leaf_index : leaf_index + 1] = [later, earlier]
    query_count += 2

  rows = [row for leaf in leaves for row in leaf["rows"]]
  unresolved_windows = []
  for leaf in leaves:
    if len(leaf["rows"]) < _CHANGE_HISTORY_RESULT_CAP:
      continue
    reason = "query_budget_exhausted_before_split"
    if leaf["start"] == leaf["end"]:
      reason = "api_cap_reached_within_one_second"
    unresolved_windows.append(
        {
            "start_date_time": _format_datetime(leaf["start"]),
            "end_date_time": _format_datetime(leaf["end"]),
            "reason": reason,
            "returned_count": len(leaf["rows"]),
        }
    )

  return {
      "rows": rows,
      "row_count": len(rows),
      "query_count": query_count,
      "complete": not unresolved_windows,
      "unresolved_windows": unresolved_windows,
  }


def _write_change_export(
    collection: dict[str, Any],
    window: dict[str, str] | None,
) -> dict[str, Any]:
  file_path, columns, bytes_written = write_rows_to_temp_csv(
      collection["rows"]
  )
  return {
      "file_path": file_path,
      "row_count": collection["row_count"],
      "bytes_written": bytes_written,
      "columns": columns,
      "window": window,
      "complete": collection["complete"],
      "query_count": collection["query_count"],
      "unresolved_windows": collection["unresolved_windows"],
  }


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


def _empty_change_statuses_response(limit: int) -> dict[str, Any]:
  return build_paginated_list_response(
      "change_statuses",
      [],
      total_count=0,
      page_size=limit,
      next_page_token=None,
  )


def _change_status_coverage(
    start_date: str,
    end_date: str,
    status_window_used: dict[str, str] | None,
) -> dict[str, Any]:
  """Builds compact coverage metadata for change_status."""
  coverage = {
      "available": status_window_used is not None,
      "window": status_window_used,
      "full_requested_range_covered": status_window_used
      == {"start_date": start_date, "end_date": end_date},
      "lookback_days": _CHANGE_STATUS_MAX_LOOKBACK_DAYS,
      "api_result_cap": _CHANGE_HISTORY_RESULT_CAP,
  }
  if status_window_used is None:
    coverage["reason"] = (
        "The requested range does not overlap the Google Ads "
        "change_status lookback window."
    )
    return coverage

  if status_window_used["start_date"] > start_date:
    coverage.update(
        {
            "start_date_clamped": True,
            "requested_start_date": start_date,
            "effective_start_date": status_window_used["start_date"],
        }
    )
  if status_window_used["end_date"] < end_date:
    coverage.update(
        {
            "end_date_clamped": True,
            "requested_end_date": end_date,
            "effective_end_date": status_window_used["end_date"],
        }
    )
  if not coverage["full_requested_range_covered"]:
    coverage["reason"] = (
        "Change status is only available for the last "
        f"{_CHANGE_STATUS_MAX_LOOKBACK_DAYS} days."
    )
  return coverage


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
  if event_window_used and event_window_used["start_date"] > start_date:
    coverage.update(
        {
            "start_date_clamped": True,
            "requested_start_date": start_date,
            "effective_start_date": event_window_used["start_date"],
        }
    )
  if event_window_used and event_window_used["end_date"] < end_date:
    coverage.update(
        {
            "end_date_clamped": True,
            "requested_end_date": end_date,
            "effective_end_date": event_window_used["end_date"],
        }
    )

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
  elif event_window_used and event_window_used["end_date"] < end_date:
    coverage["reason"] = (
        "Future granular change_event rows are unavailable from Google Ads."
    )
  return coverage


change_tool = ads_read_tool(mcp, tags={"changes", "audit"})
change_export_tool = local_write_tool(
    mcp,
    tags={"changes", "audit", "export"},
)


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
  start_date, end_date = _resolve_change_status_date_range(
      start_date, end_date, 7
  )

  where_conditions = _datetime_range_conditions(
      "change_status.last_change_date_time",
      start_date,
      end_date,
  )
  resource_types = normalize_list_arg(resource_types, "resource_types")
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
  resource_change_operations = normalize_list_arg(
      resource_change_operations,
      "resource_change_operations",
  )
  if resource_change_operations:
    where_conditions.append(
        "change_event.resource_change_operation IN "
        f"({quote_enum_values(resource_change_operations)})"
    )
  change_resource_types = normalize_list_arg(
      change_resource_types,
      "change_resource_types",
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


@change_export_tool
def export_change_history_csv(
    customer_id: str,
    resource_types: list[str] | str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_recent_events: bool = True,
    max_queries_per_resource: int = _DEFAULT_EXPORT_QUERY_BUDGET,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Exports the maximum available change history to temporary CSV files.

  The export covers up to 90 inclusive days of change_status and 30 inclusive
  days of granular change_event data. Queries that reach Google's 10,000-row
  cap are repeatedly split into smaller time windows. The response remains
  compact by returning file paths and coverage metadata instead of rows.

  Args:
      customer_id: Google Ads customer ID.
      resource_types: Optional resource types such as CAMPAIGN or AD_GROUP.
      start_date: Inclusive YYYY-MM-DD start date. Defaults to the oldest
          available change_status date.
      end_date: Inclusive YYYY-MM-DD end date. Defaults to today.
      include_recent_events: Whether to export granular change_event rows,
          including old and new resource snapshots where Google exposes them.
      max_queries_per_resource: Safety budget applied separately to
          change_status and change_event. Increase or use a narrower range if
          unresolved windows are reported.
      login_customer_id: Optional manager account ID.

  Returns:
      A compact dict with CSV paths, row counts, query counts, and explicit
      completeness metadata for both Google Ads change resources.
  """
  _validate_query_budget(max_queries_per_resource)
  start_date, end_date = _resolve_date_range(start_date, end_date, 89)
  resource_types = normalize_list_arg(resource_types, "resource_types")

  status_window = _available_date_window(
      start_date,
      end_date,
      _oldest_change_status_start(),
  )
  if status_window:
    status_start, status_end = _date_range_datetimes(
        status_window["start_date"],
        status_window["end_date"],
    )

    def _status_query(window_start: datetime, window_end: datetime) -> str:
      return _build_export_query(
          _CHANGE_STATUS_EXPORT_FIELDS,
          "change_status",
          "change_status.last_change_date_time",
          "change_status.resource_type",
          resource_types,
          window_start,
          window_end,
      )

    status_collection = _collect_complete_change_rows(
        _status_query,
        customer_id,
        status_start,
        status_end,
        login_customer_id,
        max_queries_per_resource,
    )
  else:
    status_collection = {
        "rows": [],
        "row_count": 0,
        "query_count": 0,
        "complete": True,
        "unresolved_windows": [],
    }
  status_export = _write_change_export(status_collection, status_window)

  event_window = None
  event_export = None
  if include_recent_events:
    event_window = _available_date_window(
        start_date,
        end_date,
        _oldest_change_event_start(),
    )
    if event_window:
      event_start, event_end = _date_range_datetimes(
          event_window["start_date"],
          event_window["end_date"],
      )

      def _event_query(window_start: datetime, window_end: datetime) -> str:
        return _build_export_query(
            _CHANGE_EVENT_EXPORT_FIELDS,
            "change_event",
            "change_event.change_date_time",
            "change_event.change_resource_type",
            resource_types,
            window_start,
            window_end,
        )

      event_collection = _collect_complete_change_rows(
          _event_query,
          customer_id,
          event_start,
          event_end,
          login_customer_id,
          max_queries_per_resource,
      )
      event_export = _write_change_export(event_collection, event_window)

  complete = status_export["complete"] and (
      event_export is None or event_export["complete"]
  )
  result = {
      "requested_date_range": {
          "start_date": start_date,
          "end_date": end_date,
      },
      "change_status_coverage": _change_status_coverage(
          start_date,
          end_date,
          status_window,
      ),
      "change_event_coverage": _change_event_coverage(
          start_date,
          end_date,
          event_window,
          include_recent_events,
      ),
      "change_status_export": status_export,
      "change_event_export": event_export,
      "complete": complete,
      "max_queries_per_resource": max_queries_per_resource,
      "coverage_note": (
          "CSV files keep full available rows outside the model context. "
          "change_status covers up to 90 inclusive days; change_event covers "
          "up to 30 inclusive days and includes old/new snapshots."
      ),
  }
  if not complete:
    result["next_step"] = (
        "Rerun export_change_history_csv for each unresolved window with a "
        "narrower date range or a higher max_queries_per_resource."
    )
  return result


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
  """Gets a bounded preview of the maximum available change history.

  Google Ads exposes change_status for 90 inclusive days and granular
  change_event rows for 30 inclusive days. This helper returns a token-safe
  preview with continuation metadata. Use export_change_history_csv for full
  available rows, automatic cap subdivision, and old/new event snapshots.

  Args:
      customer_id: Google Ads customer ID.
      resource_types: Optional resource types such as CAMPAIGN or AD_GROUP.
      start_date: Inclusive YYYY-MM-DD start date. Defaults to 89 days ago,
          which is the oldest date in the 90-day inclusive status window.
      end_date: Inclusive YYYY-MM-DD end date. Defaults to today.
      include_recent_events: Whether to include granular change_event rows
          for the portion of the window available in Google Ads.
      limit: Maximum preview rows to return for each underlying section.
      login_customer_id: Optional manager account ID.

  Returns:
      A bounded dict with status/event previews, pagination tokens, and
      explicit coverage/export guidance.
  """
  validate_limit(limit)
  start_date, end_date = _resolve_date_range(start_date, end_date, 89)

  status_window_used = _available_date_window(
      start_date,
      end_date,
      _oldest_change_status_start(),
  )
  statuses = _empty_change_statuses_response(limit)
  if status_window_used:
    statuses = list_change_statuses(
        customer_id=customer_id,
        resource_types=resource_types,
        start_date=status_window_used["start_date"],
        end_date=status_window_used["end_date"],
        limit=limit,
        login_customer_id=login_customer_id,
    )

  recent_events = _empty_change_events_response(limit)
  event_window_used = None
  if include_recent_events:
    event_window_used = _available_date_window(
        start_date,
        end_date,
        _oldest_change_event_start(),
    )
  if event_window_used:
    recent_events = list_change_events(
        customer_id=customer_id,
        change_resource_types=resource_types,
        start_date=event_window_used["start_date"],
        end_date=event_window_used["end_date"],
        limit=limit,
        login_customer_id=login_customer_id,
    )

  coverage_note = (
      "change_status is available for the last "
      f"{_CHANGE_STATUS_MAX_LOOKBACK_DAYS} inclusive days; granular "
      "change_event rows are available for the last "
      f"{_CHANGE_EVENT_MAX_LOOKBACK_DAYS} inclusive days. Each query is "
      f"capped at {_CHANGE_HISTORY_RESULT_CAP} rows."
  )
  if status_window_used and status_window_used["start_date"] > start_date:
    effective_status_start_date = status_window_used["start_date"]
    coverage_note += (
        f" Requested change_status start_date {start_date} was clamped to "
        f"{effective_status_start_date}."
    )
  if event_window_used and event_window_used["start_date"] > start_date:
    effective_start_date = event_window_used["start_date"]
    coverage_note += (
        f" Requested change_event start_date {start_date} was clamped to "
        f"{effective_start_date} to keep the date range within "
        "the 30-day inclusive API window."
    )
  if not include_recent_events:
    coverage_note += " Granular change_event rows were not requested."
  elif not event_window_used:
    coverage_note += " The requested range does not overlap that window."

  return {
      "date_range": {"start_date": start_date, "end_date": end_date},
      "change_status_window": status_window_used,
      "change_status_coverage": _change_status_coverage(
          start_date,
          end_date,
          status_window_used,
      ),
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
      "change_status_next_page_token": statuses.get("next_page_token"),
      "recent_change_events": recent_events["change_events"],
      "recent_change_event_returned_count": recent_events["returned_count"],
      "recent_change_event_total_count": recent_events["total_count"],
      "recent_change_event_truncated": recent_events["truncated"],
      "recent_change_event_next_page_token": recent_events.get(
          "next_page_token"
      ),
      "bulk_export_tool": "export_change_history_csv",
  }
