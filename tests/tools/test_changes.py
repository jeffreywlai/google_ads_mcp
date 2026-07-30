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

import csv
from datetime import date
from datetime import datetime
from datetime import timedelta
import os
from unittest import mock

from ads_mcp.tools import changes
from fastmcp.exceptions import ToolError
import pytest


CUSTOMER_ID = "1234567890"


def test_list_change_statuses_builds_query():
  start_date = (date.today() - timedelta(days=8)).isoformat()
  end_date = (date.today() - timedelta(days=1)).isoformat()
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
        start_date=start_date,
        end_date=end_date,
    )

  query = mock_query.call_args.kwargs["query"]
  assert "FROM change_status" in query
  assert "change_status.resource_type IN (CAMPAIGN, AD_GROUP)" in query
  assert f"'{start_date} 00:00:00'" in query
  end_exclusive = (
      date.fromisoformat(end_date) + timedelta(days=1)
  ).isoformat()
  assert (
      f"change_status.last_change_date_time < '{end_exclusive} 00:00:00'"
      in query
  )
  assert "LIMIT 10000" in query
  assert result["returned_count"] == 0
  assert result["total_count"] == 0
  assert result["truncated"] is False


def test_list_change_events_builds_query():
  start_date = (date.today() - timedelta(days=10)).isoformat()
  end_date = (date.today() - timedelta(days=3)).isoformat()

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
        start_date=start_date,
        end_date=end_date,
    )

  query = mock_query.call_args.kwargs["query"]
  assert "FROM change_event" in query
  assert "change_event.resource_change_operation IN (UPDATE)" in query
  assert "change_event.change_resource_type IN (CAMPAIGN)" in query
  assert f"'{start_date} 00:00:00'" in query
  end_exclusive = (
      date.fromisoformat(end_date) + timedelta(days=1)
  ).isoformat()
  assert f"change_event.change_date_time < '{end_exclusive} 00:00:00'" in query
  assert "change_event.old_resource" not in query
  assert "change_event.new_resource" not in query
  assert "LIMIT 10000" in query
  assert result["returned_count"] == 0
  assert result["total_count"] == 0
  assert result["truncated"] is False


def test_change_tools_ignore_empty_string_enum_filters():
  start_date = (date.today() - timedelta(days=10)).isoformat()
  end_date = (date.today() - timedelta(days=3)).isoformat()
  page = {
      "rows": [],
      "next_page_token": None,
      "total_results_count": 0,
  }

  with mock.patch(
      "ads_mcp.tools.changes.run_gaql_query_page",
      return_value=page,
  ) as mock_query:
    changes.list_change_statuses(
        CUSTOMER_ID,
        resource_types="[]",
        start_date=start_date,
        end_date=end_date,
    )
    changes.list_change_events(
        CUSTOMER_ID,
        resource_change_operations="[]",
        change_resource_types="[]",
        start_date=start_date,
        end_date=end_date,
    )

  status_query = mock_query.call_args_list[0].kwargs["query"]
  event_query = mock_query.call_args_list[1].kwargs["query"]
  assert "change_status.resource_type IN ()" not in status_query
  assert "change_status.resource_type IN" not in status_query
  assert "change_event.resource_change_operation IN ()" not in event_query
  assert "change_event.change_resource_type IN ()" not in event_query
  assert "change_event.resource_change_operation IN" not in event_query
  assert "change_event.change_resource_type IN" not in event_query


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


def test_list_change_statuses_rejects_dates_older_than_90_days():
  too_old_start = (date.today() - timedelta(days=90)).isoformat()

  with pytest.raises(ToolError, match="last 90 days"):
    changes.list_change_statuses(
        CUSTOMER_ID,
        start_date=too_old_start,
        end_date=date.today().isoformat(),
    )


def test_list_change_events_rejects_future_end_date():
  start_date = (date.today() - timedelta(days=1)).isoformat()
  future_end_date = (date.today() + timedelta(days=1)).isoformat()

  with pytest.raises(ToolError, match="dates through today"):
    changes.list_change_events(
        CUSTOMER_ID,
        start_date=start_date,
        end_date=future_end_date,
    )


def test_change_tools_reject_invalid_dates():
  with pytest.raises(ToolError, match="start_date must be a YYYY-MM-DD date"):
    changes.list_change_statuses(
        CUSTOMER_ID,
        start_date="not-a-date",
    )


def test_change_tools_reject_non_string_dates():
  with pytest.raises(ToolError, match="start_date must be a YYYY-MM-DD date"):
    changes.list_change_statuses(
        CUSTOMER_ID,
        start_date=20260401,
    )


def test_list_change_events_defaults_end_date_to_today():
  start_date = (date.today() - timedelta(days=5)).isoformat()

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
  tomorrow = (date.today() + timedelta(days=1)).isoformat()
  assert f"change_event.change_date_time < '{tomorrow} 00:00:00'" in query


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
  assert (
      f"change_status.last_change_date_time < '{today.isoformat()} 00:00:00'"
      in query
  )


def test_get_change_history_extended_stitches_statuses_and_recent_events():
  today = date.today()
  start_date = (today - timedelta(days=60)).isoformat()
  end_date = today.isoformat()
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value={
          "change_statuses": [{"change_status.resource_name": "x"}],
          "returned_count": 1,
          "total_count": 1,
          "truncated": False,
      },
  ) as mock_statuses:
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
        return_value={
            "change_events": [{"change_event.resource_name": "y"}],
            "returned_count": 1,
            "total_count": 1,
            "total_page_count": 1,
            "truncated": False,
            "next_page_token": None,
            "page_size": 100,
        },
    ) as mock_events:
      result = changes.get_change_history_extended(
          CUSTOMER_ID,
          resource_types=["campaign"],
          start_date=start_date,
          end_date=end_date,
      )

  mock_statuses.assert_called_once()
  event_kwargs = mock_events.call_args.kwargs
  assert event_kwargs["change_resource_types"] == ["CAMPAIGN"]
  assert event_kwargs["start_date"] == (today - timedelta(days=29)).isoformat()
  assert result["change_statuses"] == [{"change_status.resource_name": "x"}]
  assert result["recent_change_events"] == [
      {"change_event.resource_name": "y"}
  ]
  assert result["change_event_window"] == {
      "start_date": (today - timedelta(days=29)).isoformat(),
      "end_date": end_date,
  }
  coverage = result["change_event_coverage"]
  assert coverage["available"] is True
  assert coverage["full_requested_range_covered"] is False
  assert coverage["lookback_days"] == 30
  assert coverage["api_result_cap"] == 10000
  assert coverage["start_date_clamped"] is True
  assert coverage["requested_start_date"] == start_date
  assert (
      coverage["effective_start_date"]
      == (today - timedelta(days=29)).isoformat()
  )
  assert coverage["unavailable_window"] == {
      "start_date": start_date,
      "end_date": (today - timedelta(days=30)).isoformat(),
  }
  assert (
      "Older granular change_event rows are unavailable" in coverage["reason"]
  )
  assert "clamped" in result["coverage_note"]


def test_get_change_history_extended_defaults_to_all_retained_windows():
  today = date.today()
  status_response = {
      "change_statuses": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
  }
  event_response = {
      "change_events": [],
      "returned_count": 0,
      "total_count": 0,
      "total_page_count": 0,
      "truncated": False,
      "next_page_token": None,
      "page_size": 100,
  }
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value=status_response,
  ) as mock_statuses:
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
        return_value=event_response,
    ) as mock_events:
      changes.get_change_history_extended(CUSTOMER_ID)

  assert (
      mock_statuses.call_args.kwargs["start_date"]
      == (today - timedelta(days=89)).isoformat()
  )
  assert mock_statuses.call_args.kwargs["end_date"] == today.isoformat()
  assert (
      mock_events.call_args.kwargs["start_date"]
      == (today - timedelta(days=29)).isoformat()
  )
  assert mock_events.call_args.kwargs["end_date"] == today.isoformat()


def test_get_change_history_extended_clamps_31_date_inclusive_window():
  today = date.today()
  requested_start = (today - timedelta(days=30)).isoformat()
  effective_start = (today - timedelta(days=29)).isoformat()
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value={
          "change_statuses": [],
          "returned_count": 0,
          "total_count": 0,
          "truncated": False,
      },
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
        return_value={
            "change_events": [],
            "returned_count": 0,
            "total_count": 0,
            "total_page_count": 0,
            "truncated": False,
            "next_page_token": None,
            "page_size": 100,
        },
    ) as mock_events:
      result = changes.get_change_history_extended(
          CUSTOMER_ID,
          start_date=requested_start,
          end_date=today.isoformat(),
      )

  assert mock_events.call_args.kwargs["start_date"] == effective_start
  assert result["change_event_coverage"]["start_date_clamped"] is True
  assert result["change_event_coverage"]["requested_start_date"] == (
      requested_start
  )
  assert result["change_event_coverage"]["effective_start_date"] == (
      effective_start
  )
  assert requested_start in result["coverage_note"]
  assert effective_start in result["coverage_note"]


def test_get_change_history_extended_clamps_status_and_exposes_continuations():
  today = date.today()
  requested_start = (today - timedelta(days=120)).isoformat()
  effective_start = (today - timedelta(days=89)).isoformat()
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value={
          "change_statuses": [{"change_status.resource_name": "x"}],
          "returned_count": 1,
          "total_count": 500,
          "truncated": True,
          "next_page_token": "100",
      },
  ) as mock_statuses:
    result = changes.get_change_history_extended(
        CUSTOMER_ID,
        start_date=requested_start,
        end_date=today.isoformat(),
        include_recent_events=False,
    )

  assert mock_statuses.call_args.kwargs["start_date"] == effective_start
  assert result["change_status_window"] == {
      "start_date": effective_start,
      "end_date": today.isoformat(),
  }
  assert result["change_status_coverage"]["start_date_clamped"] is True
  assert result["change_status_next_page_token"] == "100"
  assert result["recent_change_event_next_page_token"] is None
  assert result["continuation_guidance"]["change_status"] == {
      "tool": "list_change_statuses",
      "page_token": "100",
      "instruction": (
          "Call list_change_statuses again with the same filters and this "
          "page_token."
      ),
  }
  assert result["bulk_export_tool"] == "export_change_history_csv"
  assert "90 inclusive days" in result["coverage_note"]


def test_collect_complete_change_rows_splits_capped_windows():
  start_datetime = datetime(2026, 7, 1, 0, 0, 0)
  end_datetime_exclusive = datetime(2026, 7, 1, 0, 0, 4)
  query_builder = mock.Mock(
      side_effect=lambda start, end: (
          f"{start.isoformat()}..{end.isoformat()}"
      )
  )
  with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        side_effect=[
            [{"id": "probe-1"}, {"id": "probe-2"}],
            [{"id": "later"}],
            [{"id": "earlier"}],
        ],
    ):
      with mock.patch(
          "ads_mcp.tools.changes.write_rows_to_temp_csv",
          side_effect=[
              ("/tmp/probe.csv", ["id"], 10),
              ("/tmp/later.csv", ["id"], 10),
              ("/tmp/earlier.csv", ["id"], 10),
          ],
      ) as mock_write:
        result = changes._collect_complete_change_rows(  # pylint: disable=protected-access
            query_builder,
            CUSTOMER_ID,
            start_datetime,
            end_datetime_exclusive,
            None,
            max_queries=10,
            columns=["id"],
        )

  assert result["fragment_paths"] == [
      "/tmp/later.csv",
      "/tmp/earlier.csv",
  ]
  assert result["row_count"] == 2
  assert result["query_count"] == 3
  assert result["complete"] is True
  assert not result["unresolved_windows"]
  assert mock_write.call_count == 3
  assert query_builder.call_args_list == [
      mock.call(start_datetime, end_datetime_exclusive),
      mock.call(datetime(2026, 7, 1, 0, 0, 2), end_datetime_exclusive),
      mock.call(start_datetime, datetime(2026, 7, 1, 0, 0, 2)),
  ]


def test_collect_complete_change_rows_reports_unsplittable_second():
  timestamp = datetime(2026, 7, 1, 0, 0, 0)
  with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        return_value=[{"id": "1"}, {"id": "2"}],
    ):
      with mock.patch(
          "ads_mcp.tools.changes.write_rows_to_temp_csv",
          return_value=("/tmp/one-second.csv", ["id"], 10),
      ):
        result = changes._collect_complete_change_rows(  # pylint: disable=protected-access
            lambda start, end: f"{start}..{end}",
            CUSTOMER_ID,
            timestamp,
            timestamp + timedelta(seconds=1),
            None,
            max_queries=10,
            columns=["id"],
        )

  assert result["complete"] is False
  assert result["row_count"] == 2
  assert result["unresolved_windows"][0]["reason"] == (
      "api_cap_reached_within_one_second"
  )
  assert result["unresolved_windows"][0]["end_date_time_exclusive"] == (
      "2026-07-01 00:00:01"
  )


def test_collect_complete_change_rows_preserves_rows_at_query_budget():
  start_datetime = datetime(2026, 7, 1, 0, 0, 0)
  end_datetime_exclusive = datetime(2026, 7, 1, 0, 0, 3)
  capped_rows = [{"id": "1"}, {"id": "2"}]
  with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        return_value=capped_rows,
    ) as mock_query:
      with mock.patch(
          "ads_mcp.tools.changes.write_rows_to_temp_csv",
          return_value=("/tmp/capped.csv", ["id"], 10),
      ):
        result = changes._collect_complete_change_rows(  # pylint: disable=protected-access
            lambda start, end: f"{start}..{end}",
            CUSTOMER_ID,
            start_datetime,
            end_datetime_exclusive,
            None,
            max_queries=1,
            columns=["id"],
        )

  mock_query.assert_called_once()
  assert result["fragment_paths"] == ["/tmp/capped.csv"]
  assert result["row_count"] == len(capped_rows)
  assert result["query_count"] == 1
  assert result["complete"] is False
  assert result["unresolved_windows"][0]["reason"] == (
      "query_budget_exhausted_before_split"
  )


def test_export_change_history_csv_returns_files_and_complete_coverage():
  status_collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  event_collection = {
      "fragment_paths": ["/tmp/event-fragment.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
      side_effect=[status_collection, event_collection],
  ) as mock_collect:
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        side_effect=[
            ("/tmp/status.csv", ["change_status.resource_name"], 10),
            ("/tmp/events.csv", ["change_event.resource_name"], 20),
        ],
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          resource_types=["campaign"],
      )

  status_query_builder = mock_collect.call_args_list[0].args[0]
  event_query_builder = mock_collect.call_args_list[1].args[0]
  query_start = datetime(2026, 7, 1, 0, 0, 0)
  query_end_exclusive = datetime(2026, 7, 2, 0, 0, 0)
  status_query = status_query_builder(query_start, query_end_exclusive)
  event_query = event_query_builder(query_start, query_end_exclusive)

  assert "change_status.campaign_shared_set" in status_query
  assert "change_status.resource_type IN (CAMPAIGN)" in status_query
  assert (
      "change_status.last_change_date_time < '2026-07-02 00:00:00'"
      in status_query
  )
  assert "change_event.old_resource" in event_query
  assert "change_event.new_resource" in event_query
  today = date.today()
  status_call = mock_collect.call_args_list[0]
  event_call = mock_collect.call_args_list[1]
  assert status_call.args[2:6] == (
      datetime.combine(today - timedelta(days=89), datetime.min.time()),
      datetime.combine(today + timedelta(days=1), datetime.min.time()),
      None,
      200,
  )
  assert len(status_call.kwargs["initial_windows"]) == 90
  assert event_call.args[2:6] == (
      datetime.combine(today - timedelta(days=29), datetime.min.time()),
      datetime.combine(today + timedelta(days=1), datetime.min.time()),
      None,
      200,
  )
  assert result["change_status_export"]["file_path"] == "/tmp/status.csv"
  assert result["change_event_export"]["file_path"] == "/tmp/events.csv"
  assert result["complete"] is True
  assert result["available_data_complete"] is True
  assert result["requested_range_fully_available"] is False
  assert "all planned queries" in result["complete_meaning"]
  assert (
      result["change_status_export"]["partitioning"][
          "daily_partitioning_complete"
      ]
      is True
  )
  assert (
      result["requested_date_range"]["start_date"]
      == (date.today() - timedelta(days=89)).isoformat()
  )


def test_status_partition_windows_coarsen_without_date_gaps():
  start = datetime(2026, 7, 1)
  end_exclusive = datetime(2026, 7, 6)

  windows, metadata = changes._status_partition_windows(  # pylint: disable=protected-access
      start,
      end_exclusive,
      max_queries=2,
  )

  assert windows == [
      (datetime(2026, 7, 3), datetime(2026, 7, 6)),
      (datetime(2026, 7, 1), datetime(2026, 7, 3)),
  ]
  assert metadata["strategy"] == "budget_coarsened_contiguous_windows"
  assert metadata["daily_partitioning_complete"] is False
  assert metadata["window_days"] == [3, 2]
  assert windows[1][1] == windows[0][0]
  assert "latest change_status row per resource" in metadata["semantic_limit"]


def test_export_marks_coarsened_status_resolution_incomplete():
  today = date.today()
  collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "row_count": 1,
      "query_count": 2,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
      return_value=collection,
  ) as mock_collect:
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        return_value=("/tmp/status.csv", ["change_status.resource_name"], 10),
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          start_date=(today - timedelta(days=4)).isoformat(),
          end_date=today.isoformat(),
          include_recent_events=False,
          max_queries_per_resource=2,
      )

  windows = mock_collect.call_args.kwargs["initial_windows"]
  assert len(windows) == 2
  assert windows[-1][0] == datetime.combine(
      today - timedelta(days=4),
      datetime.min.time(),
  )
  assert windows[0][1] == datetime.combine(
      today + timedelta(days=1),
      datetime.min.time(),
  )
  assert result["change_status_export"]["complete"] is True
  assert result["change_status_export"]["daily_resolution_complete"] is False
  assert result["available_data_complete"] is False
  assert result["complete"] is False
  assert "Increase max_queries_per_resource" in result["next_step"]


def test_change_resource_types_are_partitioned_by_v24_enum():
  status_types, event_types, coverage = changes._partition_resource_types(  # pylint: disable=protected-access
      ["shared_set", "ad", "campaign"]
  )

  assert status_types == ["SHARED_SET", "CAMPAIGN"]
  assert event_types == ["AD", "CAMPAIGN"]
  assert coverage["change_status"]["unsupported_resource_types"] == ["AD"]
  assert coverage["change_event"]["unsupported_resource_types"] == [
      "SHARED_SET"
  ]


def test_change_resource_types_reject_values_unsupported_by_both_resources():
  with pytest.raises(ToolError, match="Unsupported change-history"):
    changes.get_change_history_extended(
        CUSTOMER_ID,
        resource_types=["NOT_A_CHANGE_RESOURCE"],
    )


def test_preview_skips_incompatible_event_resource_type():
  status_response = {
      "change_statuses": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value=status_response,
  ) as mock_statuses:
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
    ) as mock_events:
      result = changes.get_change_history_extended(
          CUSTOMER_ID,
          resource_types=["SHARED_SET"],
      )

  assert mock_statuses.call_args.kwargs["resource_types"] == ["SHARED_SET"]
  mock_events.assert_not_called()
  assert (
      result["resource_type_coverage"]["change_event"]["query_skipped"] is True
  )
  assert (
      result["change_event_coverage"]["query_skipped_for_resource_types"]
      is True
  )
  assert "invalid enum" in result["change_event_coverage"]["reason"]


def test_empty_change_csv_has_fixed_headers():
  columns = ["change_status.resource_name", "change_status.resource_type"]
  file_path, output_columns, _ = changes.write_rows_to_temp_csv(
      [],
      columns=columns,
  )
  try:
    with open(file_path, newline="", encoding="utf-8") as csv_file:
      assert list(csv.reader(csv_file)) == [columns]
    assert output_columns == columns
  finally:
    os.remove(file_path)


def test_change_csv_fragment_merge_cleans_fragments():
  columns = ["id", "value"]
  first_path, _, _ = changes.write_rows_to_temp_csv(
      [{"id": 1, "value": "first"}],
      columns=columns,
  )
  second_path, _, _ = changes.write_rows_to_temp_csv(
      [{"id": 2, "value": "second"}],
      columns=columns,
  )

  output_path, _, _ = changes.merge_temp_csv_files(
      [first_path, second_path],
      columns,
  )
  try:
    assert not os.path.exists(first_path)
    assert not os.path.exists(second_path)
    with open(output_path, newline="", encoding="utf-8") as csv_file:
      assert list(csv.reader(csv_file)) == [
          columns,
          ["1", "first"],
          ["2", "second"],
      ]
  finally:
    os.remove(output_path)


def test_export_removes_status_file_when_event_export_fails():
  collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
      side_effect=[collection, ToolError("event query failed")],
  ):
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        return_value=("/tmp/status.csv", ["change_status.resource_name"], 10),
    ):
      with mock.patch(
          "ads_mcp.tools.changes._remove_temp_file"
      ) as mock_remove:
        with pytest.raises(ToolError, match="event query failed"):
          changes.export_change_history_csv(CUSTOMER_ID)

  mock_remove.assert_called_once_with("/tmp/status.csv")


def test_export_expired_range_distinguishes_availability_from_completion():
  today = date.today()
  start_date = (today - timedelta(days=120)).isoformat()
  end_date = (today - timedelta(days=100)).isoformat()
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
  ) as mock_collect:
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        return_value=(
            "/tmp/status.csv",
            ["change_status.resource_name"],
            1,
        ),
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          start_date=start_date,
          end_date=end_date,
      )

  mock_collect.assert_not_called()
  assert result["available_data_complete"] is True
  assert result["complete"] is True
  assert result["requested_range_fully_available"] is False
  assert result["change_status_coverage"]["available"] is False
  assert (
      "does not mean the entire requested range" in result["complete_meaning"]
  )


@pytest.mark.parametrize("max_queries", [0, True, "10"])
def test_export_change_history_csv_rejects_invalid_query_budget(max_queries):
  with pytest.raises(ToolError, match="max_queries_per_resource"):
    changes.export_change_history_csv(
        CUSTOMER_ID,
        max_queries_per_resource=max_queries,
    )


def test_get_change_history_extended_skips_events_when_range_is_too_old():
  today = date.today()
  start_date = (today - timedelta(days=90)).isoformat()
  end_date = (today - timedelta(days=45)).isoformat()
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value={
          "change_statuses": [],
          "returned_count": 0,
          "total_count": 0,
          "truncated": False,
      },
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
    ) as mock_events:
      result = changes.get_change_history_extended(
          CUSTOMER_ID,
          start_date=start_date,
          end_date=end_date,
      )

  mock_events.assert_not_called()
  assert result["change_event_window"] is None
  assert result["recent_change_events"] == []
  assert result["change_event_coverage"] == {
      "available": False,
      "window": None,
      "full_requested_range_covered": False,
      "lookback_days": 30,
      "api_result_cap": 10000,
      "unavailable_window": {
          "start_date": start_date,
          "end_date": end_date,
      },
      "reason": (
          "The requested range does not overlap the Google Ads "
          "change_event lookback window."
      ),
  }


def test_get_change_history_extended_marks_events_disabled():
  today = date.today()
  start_date = (today - timedelta(days=5)).isoformat()
  end_date = today.isoformat()
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value={
          "change_statuses": [],
          "returned_count": 0,
          "total_count": 0,
          "truncated": False,
      },
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
    ) as mock_events:
      result = changes.get_change_history_extended(
          CUSTOMER_ID,
          start_date=start_date,
          end_date=end_date,
          include_recent_events=False,
      )

  mock_events.assert_not_called()
  assert result["change_event_coverage"] == {
      "available": False,
      "window": None,
      "full_requested_range_covered": False,
      "lookback_days": 30,
      "api_result_cap": 10000,
      "reason": "include_recent_events is false.",
  }
  assert "not requested" in result["coverage_note"]
