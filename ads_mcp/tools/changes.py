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

from fastmcp.exceptions import ToolError

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import gaql_quote_string
from ads_mcp.tools.api import run_gaql_query


def _default_date_range(days_back: int) -> tuple[str, str]:
  end_date = date.today()
  start_date = end_date - timedelta(days=days_back)
  return start_date.isoformat(), end_date.isoformat()


def _resolve_date_range(
    start_date: str | None,
    end_date: str | None,
    days_back: int,
) -> tuple[str, str]:
  if start_date and not end_date:
    raise ToolError("end_date is required when start_date is provided.")
  if end_date and not start_date:
    raise ToolError("start_date is required when end_date is provided.")
  if not start_date and not end_date:
    return _default_date_range(days_back)
  if start_date > end_date:
    raise ToolError("start_date must be on or before end_date.")
  return start_date, end_date


change_tool = ads_read_tool(mcp, tags={"changes", "audit"})


@change_tool
def list_change_statuses(
    customer_id: str,
    resource_types: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, list[dict[str, object]]]:
  """Lists changed resources from change_status.

  Args:
      customer_id: Google Ads customer ID.
      resource_types: Optional resource types such as CAMPAIGN or AD_GROUP.
      start_date: Inclusive YYYY-MM-DD start date. Defaults to 7 days ago.
      end_date: Inclusive YYYY-MM-DD end date. Defaults to today.
      limit: Maximum number of rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing change status rows.
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
      LIMIT {limit}
  """

  return {
      "change_statuses": run_gaql_query(query, customer_id, login_customer_id)
  }


@change_tool
def list_change_events(
    customer_id: str,
    resource_change_operations: list[str] | None = None,
    change_resource_types: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, list[dict[str, object]]]:
  """Lists granular changes from change_event.

  Args:
      customer_id: Google Ads customer ID.
      resource_change_operations: Optional operations such as CREATE,
          UPDATE, or REMOVE.
      change_resource_types: Optional changed resource types.
      start_date: Inclusive YYYY-MM-DD start date. Defaults to 7 days ago.
      end_date: Inclusive YYYY-MM-DD end date. Defaults to today.
      limit: Maximum number of rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing change event rows.
  """
  validate_limit(limit)
  start_date, end_date = _resolve_date_range(start_date, end_date, 7)

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
      LIMIT {limit}
  """

  return {
      "change_events": run_gaql_query(query, customer_id, login_customer_id)
  }
