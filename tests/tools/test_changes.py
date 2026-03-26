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

"""Tests for changes.py."""

from datetime import date
from datetime import timedelta
from unittest import mock

from ads_mcp.tools import changes
from fastmcp.exceptions import ToolError
import pytest


CUSTOMER_ID = "1234567890"


def test_list_change_statuses_builds_query():
  with mock.patch(
      "ads_mcp.tools.changes.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    result = changes.list_change_statuses(
        CUSTOMER_ID,
        resource_types=["campaign", "ad_group"],
        start_date="2026-03-01",
        end_date="2026-03-08",
    )

  query = mock_query.call_args.kwargs["query"]
  assert "FROM change_status" in query
  assert "change_status.resource_type IN (CAMPAIGN, AD_GROUP)" in query
  assert "'2026-03-01 00:00:00'" in query
  assert "'2026-03-08 23:59:59'" in query
  assert "LIMIT 10000" in query
  assert result["returned_count"] == 0
  assert result["total_count"] == 0
  assert result["truncated"] is False


def test_list_change_events_builds_query():
  with mock.patch(
      "ads_mcp.tools.changes.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    result = changes.list_change_events(
        CUSTOMER_ID,
        resource_change_operations=["update"],
        change_resource_types=["campaign"],
        start_date="2026-03-01",
        end_date="2026-03-08",
    )

  query = mock_query.call_args.kwargs["query"]
  assert "FROM change_event" in query
  assert "change_event.resource_change_operation IN (UPDATE)" in query
  assert "change_event.change_resource_type IN (CAMPAIGN)" in query
  assert "'2026-03-01 00:00:00'" in query
  assert "'2026-03-08 23:59:59'" in query
  assert "change_event.old_resource" not in query
  assert "change_event.new_resource" not in query
  assert "LIMIT 10000" in query
  assert result["returned_count"] == 0
  assert result["total_count"] == 0
  assert result["truncated"] is False


def test_change_tools_flag_when_google_cap_is_reached():
  with mock.patch(
      "ads_mcp.tools.changes.run_gaql_query_page",
      return_value={
          "rows": [
              {"change_status.resource_name": "customers/123/campaigns/1"}
          ],
          "next_page_token": None,
          "total_results_count": 10000,
      },
  ):
    result = changes.list_change_statuses(CUSTOMER_ID, limit=10000)

  assert result["truncated"] is True
  assert result["api_result_cap"] == 10000


def test_list_change_events_rejects_dates_older_than_30_days():
  too_old_start = (date.today() - timedelta(days=31)).isoformat()
  end_date = date.today().isoformat()

  with pytest.raises(ToolError, match="last 30 days"):
    changes.list_change_events(
        CUSTOMER_ID,
        start_date=too_old_start,
        end_date=end_date,
    )


def test_list_change_events_defaults_end_date_to_today():
  start_date = "2026-03-01"

  with mock.patch(
      "ads_mcp.tools.changes.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    changes.list_change_events(
        CUSTOMER_ID,
        start_date=start_date,
    )

  query = mock_query.call_args.kwargs["query"]
  assert f"'{start_date} 00:00:00'" in query
  assert f"'{date.today().isoformat()} 23:59:59'" in query


def test_list_change_statuses_defaults_start_date_when_only_end_date_provided():
  today = date.today()
  end_date = (today - timedelta(days=1)).isoformat()
  expected_start_date = (today - timedelta(days=7)).isoformat()

  with mock.patch(
      "ads_mcp.tools.changes.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    changes.list_change_statuses(
        CUSTOMER_ID,
        end_date=end_date,
    )

  query = mock_query.call_args.kwargs["query"]
  assert f"'{expected_start_date} 00:00:00'" in query
  assert f"'{end_date} 23:59:59'" in query
