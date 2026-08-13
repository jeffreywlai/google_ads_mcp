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

from ads_mcp.tools import api
from ads_mcp.tools import changes
from fastmcp.exceptions import ToolError
import pytest


CUSTOMER_ID = "1234567890"


def _daily_fragment_collection(
    start_day: date,
    day_count: int,
    *,
    prefix: str,
) -> dict:
  fragments = [
      {
          "start": datetime.combine(
              start_day + timedelta(days=index),
              datetime.min.time(),
          ),
          "end_exclusive": datetime.combine(
              start_day + timedelta(days=index + 1),
              datetime.min.time(),
          ),
          "fragment_path": f"/tmp/{prefix}-{index}.csv",
          "row_count": 1,
      }
      for index in range(day_count)
  ]
  return {
      "fragment_paths": [fragment["fragment_path"] for fragment in fragments],
      "fragments": fragments,
      "row_count": day_count,
      "query_count": day_count,
      "complete": True,
      "unresolved_windows": [],
  }


@pytest.fixture(autouse=True)
def mock_account_today():
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      return_value=(date.today(), "Etc/UTC"),
  ):
    yield
  changes._customer_time_zone_for_credential.cache_clear()  # pylint: disable=protected-access


def test_customer_time_zone_is_queried_and_validated():
  changes._customer_time_zone_for_credential.cache_clear()  # pylint: disable=protected-access
  with mock.patch(
      "ads_mcp.tools.changes.get_ads_credential_cache_scope",
      return_value="test-credentials",
  ):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        return_value=[{"customer.time_zone": "Pacific/Kiritimati"}],
    ) as mock_query:
      customer_zone = changes._customer_time_zone(  # pylint: disable=protected-access
          CUSTOMER_ID,
          None,
      )

  assert customer_zone.key == "Pacific/Kiritimati"
  assert "customer.time_zone" in mock_query.call_args.args[0]


def test_customer_time_zone_cache_is_partitioned_by_principal():
  changes._customer_time_zone_for_credential.cache_clear()  # pylint: disable=protected-access
  with mock.patch(
      "ads_mcp.tools.changes.get_ads_credential_cache_scope",
      side_effect=["oauth:principal-a", "oauth:principal-b"],
  ):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        side_effect=[
            [{"customer.time_zone": "Etc/UTC"}],
            [{"customer.time_zone": "Pacific/Kiritimati"}],
        ],
    ) as mock_query:
      principal_a_zone = changes._customer_time_zone(  # pylint: disable=protected-access
          CUSTOMER_ID,
          None,
      )
      principal_b_zone = changes._customer_time_zone(  # pylint: disable=protected-access
          CUSTOMER_ID,
          None,
      )

  assert principal_a_zone.key == "Etc/UTC"
  assert principal_b_zone.key == "Pacific/Kiritimati"
  assert mock_query.call_count == 2


def test_extended_history_uses_account_calendar_for_retention():
  account_today = date(2026, 7, 31)
  empty_statuses = {
      "change_statuses": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  empty_events = {
      "change_events": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      return_value=(account_today, "Pacific/Kiritimati"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_statuses",
        return_value=empty_statuses,
    ) as mock_statuses:
      with mock.patch(
          "ads_mcp.tools.changes.list_change_events",
          return_value=empty_events,
      ) as mock_events:
        result = changes.get_change_history_extended(CUSTOMER_ID)

  assert result["account_today"] == "2026-07-31"
  assert result["account_time_zone"] == "Pacific/Kiritimati"
  assert result["date_range"]["start_date"] == "2026-05-03"
  assert mock_statuses.call_args.kwargs["start_date"] == "2026-05-03"
  assert mock_events.call_args.kwargs["start_date"] == "2026-07-02"


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
          "next_page_token": "capped-status-snapshot-page-2",
          "total_results_count": 10000,
      },
  ):
    result = changes.list_change_statuses(CUSTOMER_ID, limit=10000)

  assert result["truncated"] is True
  assert result["api_result_cap"] == 10000
  assert result["complete_inline"] is False
  assert result["next_page_token"] is None
  assert result["has_more"] is False
  assert result["pagination_suppressed"] is True
  assert "continuation" not in result
  assert result["bulk_export_call"]["tool"] == "export_change_history_csv"
  assert result["bulk_export_call"]["arguments"]["include_recent_events"] is (
      False
  )
  assert "incomplete capped snapshot" in result["bulk_export_note"]


def test_capped_event_export_guidance_preserves_operation_filter():
  with mock.patch(
      "ads_mcp.tools.changes.run_gaql_query_page",
      return_value={
          "rows": [
              {"change_event.resource_name": "customers/123/campaigns/1"}
          ],
          "next_page_token": "capped-event-snapshot-page-2",
          "total_results_count": 10000,
      },
  ):
    result = changes.list_change_events(
        CUSTOMER_ID,
        resource_change_operations=["UPDATE"],
        change_resource_types=["CAMPAIGN"],
        limit=10000,
    )

  arguments = result["bulk_export_call"]["arguments"]
  assert result["bulk_export_call"]["tool"] == "export_change_history_csv"
  assert arguments["resource_change_operations"] == ["UPDATE"]
  assert arguments["resource_types"] == ["CAMPAIGN"]
  assert result["next_page_token"] is None
  assert result["has_more"] is False
  assert result["pagination_suppressed"] is True
  assert "continuation" not in result


def test_change_history_export_applies_event_operation_filter():
  account_today = date(2026, 7, 31)
  captured_queries = []
  empty_collection = {
      "fragment_paths": [],
      "fragments": [],
      "row_count": 0,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }

  def collect_rows(query_builder, customer_id, start, end, *_args, **_kwargs):
    del customer_id
    captured_queries.append(query_builder(start, end))
    return dict(empty_collection)

  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      return_value=(account_today, "Etc/UTC"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=collect_rows,
    ):
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              ("/tmp/status.csv", ["change_status.resource_name"], 0),
              ("/tmp/events.csv", ["change_event.resource_name"], 0),
          ],
      ):
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            resource_types=["CAMPAIGN"],
            resource_change_operations=["UPDATE"],
            start_date=account_today.isoformat(),
            end_date=account_today.isoformat(),
        )

  assert len(captured_queries) == 2
  assert "change_event.resource_change_operation IN (UPDATE)" in (
      captured_queries[1]
  )
  assert result["resource_change_operations"] == ["UPDATE"]


def test_export_status_same_day_retention_failure_is_actionable():
  account_today = date(2026, 7, 31)
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      return_value=(account_today, "Etc/UTC"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=ToolError("START_DATE_TOO_OLD"),
    ):
      with pytest.raises(
          ToolError,
          match=(
              "change_status retention rejected.*"
              "Rerun export_change_history_csv"
          ),
      ) as exc_info:
        changes.export_change_history_csv(
            CUSTOMER_ID,
            include_recent_events=False,
        )

  assert str(exc_info.value) != "START_DATE_TOO_OLD"


def test_export_event_same_day_retention_failure_is_actionable():
  account_today = date(2026, 7, 31)
  empty_status_collection = {
      "fragment_paths": [],
      "fragments": [],
      "row_count": 0,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      return_value=(account_today, "Etc/UTC"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            empty_status_collection,
            ToolError("START_DATE_TOO_OLD"),
        ],
    ):
      with pytest.raises(
          ToolError,
          match=(
              "change_event retention rejected.*"
              "Rerun export_change_history_csv"
          ),
      ) as exc_info:
        changes.export_change_history_csv(CUSTOMER_ID)

  assert str(exc_info.value) != "START_DATE_TOO_OLD"


def test_export_status_second_retention_failure_is_actionable():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, first_day, next_day])
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (next(account_days), "Etc/UTC"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            ToolError("START_DATE_TOO_OLD first"),
            ToolError("START_DATE_TOO_OLD retry"),
        ],
    ):
      with pytest.raises(
          ToolError,
          match=(
              "change_status retention rejected.*"
              "Rerun export_change_history_csv"
          ),
      ) as exc_info:
        changes.export_change_history_csv(
            CUSTOMER_ID,
            include_recent_events=False,
        )

  assert "START_DATE_TOO_OLD retry" not in str(exc_info.value)


def test_export_event_second_retention_failure_is_actionable():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter(
      [first_day, first_day, first_day, next_day, next_day, next_day]
  )
  empty_status_collection = {
      "fragment_paths": [],
      "fragments": [],
      "row_count": 0,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (next(account_days), "Etc/UTC"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            empty_status_collection,
            ToolError("START_DATE_TOO_OLD first"),
            ToolError("START_DATE_TOO_OLD retry"),
        ],
    ):
      with pytest.raises(
          ToolError,
          match=(
              "change_event retention rejected.*"
              "Rerun export_change_history_csv"
          ),
      ) as exc_info:
        changes.export_change_history_csv(
            CUSTOMER_ID,
            start_date=first_day.isoformat(),
            end_date=first_day.isoformat(),
        )

  assert "START_DATE_TOO_OLD retry" not in str(exc_info.value)


def test_export_realign_retention_failure_is_actionable():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, first_day, next_day])
  status_start = datetime.combine(
      first_day - timedelta(days=89),
      datetime.min.time(),
  )
  status_collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "fragments": [
          {
              "start": status_start,
              "end_exclusive": status_start + timedelta(days=1),
              "fragment_path": "/tmp/status-fragment.csv",
              "row_count": 1,
          }
      ],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (next(account_days), "Etc/UTC"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            status_collection,
            ToolError("START_DATE_TOO_OLD realign"),
        ],
    ):
      with mock.patch("ads_mcp.tools.changes._remove_temp_file"):
        with pytest.raises(
            ToolError,
            match=(
                "change_status retention rejected.*"
                "Rerun export_change_history_csv"
            ),
        ) as exc_info:
          changes.export_change_history_csv(CUSTOMER_ID)

  assert "START_DATE_TOO_OLD realign" not in str(exc_info.value)


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


@pytest.mark.parametrize(
    ("tool", "item_key"),
    [
        (changes.list_change_statuses, "change_statuses"),
        (changes.list_change_events, "change_events"),
    ],
)
def test_direct_change_pagination_keeps_omitted_dates_across_midnight(
    tool,
    item_key,
):
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, next_day])
  with api._PAGED_QUERY_CACHE_LOCK:  # pylint: disable=protected-access
    api._PAGED_QUERY_CACHE.clear()  # pylint: disable=protected-access
    api._PAGED_QUERY_BUILDS.clear()  # pylint: disable=protected-access

  rows = [{"row": "first"}, {"row": "second"}, {"row": "third"}]
  try:
    with mock.patch(
        "ads_mcp.tools.changes._account_today",
        side_effect=lambda *_args: (
            next(account_days),
            "Pacific/Kiritimati",
        ),
    ):
      with mock.patch(
          "ads_mcp.tools.api._page_cache_scope",
          return_value="test-scope",
      ):
        with mock.patch(
            "ads_mcp.tools.api._iter_gaql_query_attempt",
            return_value=rows,
        ) as mock_query:
          first_page = tool(CUSTOMER_ID, limit=1)
          second_page = tool(
              CUSTOMER_ID,
              limit=1,
              page_token=first_page["next_page_token"],
          )
  finally:
    with api._PAGED_QUERY_CACHE_LOCK:  # pylint: disable=protected-access
      api._PAGED_QUERY_CACHE.clear()  # pylint: disable=protected-access
      api._PAGED_QUERY_BUILDS.clear()  # pylint: disable=protected-access

  expected_range = {
      "start_date": (first_day - timedelta(days=7)).isoformat(),
      "end_date": first_day.isoformat(),
  }
  assert "|" in first_page["next_page_token"]
  assert first_page["resolved_date_range"] == expected_range
  assert first_page["continuation"]["arguments"]["start_date"] == (
      expected_range["start_date"]
  )
  assert first_page["continuation"]["arguments"]["end_date"] == (
      expected_range["end_date"]
  )
  assert second_page[item_key] == [{"row": "second"}]
  assert second_page["resolved_date_range"] == expected_range
  assert second_page["account_today"] == next_day.isoformat()
  mock_query.assert_called_once()


@pytest.mark.parametrize(
    ("tool", "item_key", "source_name"),
    [
        (changes.list_change_statuses, "change_statuses", "change_status"),
        (changes.list_change_events, "change_events", "change_event"),
    ],
)
def test_direct_change_reads_replan_once_when_retention_advances(
    tool,
    item_key,
    source_name,
):
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, next_day])
  page = {
      "rows": [{"row": source_name}],
      "next_page_token": None,
      "total_results_count": 1,
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query_page",
        side_effect=[ToolError("START_DATE_TOO_OLD"), page],
    ) as mock_page:
      result = tool(CUSTOMER_ID)

  assert mock_page.call_count == 2
  assert result[item_key] == [{"row": source_name}]
  assert result["resolved_date_range"] == {
      "start_date": (next_day - timedelta(days=7)).isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["account_today"] == next_day.isoformat()
  assert source_name in result["retention_refresh_note"]


@pytest.mark.parametrize(
    ("start_omitted", "end_omitted", "day_delta"),
    [
        (start_omitted, end_omitted, day_delta)
        for start_omitted in (False, True)
        for end_omitted in (False, True)
        for day_delta in (1, 2, 7)
    ],
)
def test_resolved_history_request_advance_preserves_date_intent(
    start_omitted,
    end_omitted,
    day_delta,
):
  first_day = date(2026, 7, 31)
  later_day = first_day + timedelta(days=day_delta)
  explicit_start = first_day - timedelta(days=20)
  intent = changes._HistoryDateIntent(  # pylint: disable=protected-access
      start_date=None if start_omitted else explicit_start.isoformat(),
      end_date=None if end_omitted else first_day.isoformat(),
      default_days_back=20,
  )
  request = intent.resolve(
      changes._AccountSnapshot(  # pylint: disable=protected-access
          first_day,
          "Pacific/Kiritimati",
      )
  )
  advanced = request.advance(
      changes._AccountSnapshot(  # pylint: disable=protected-access
          later_day,
          "Pacific/Kiritimati",
      )
  )

  expected_start = (
      later_day - timedelta(days=20) if start_omitted else explicit_start
  )
  expected_end = later_day if end_omitted else first_day
  assert advanced.start_date == expected_start.isoformat()
  assert advanced.end_date == expected_end.isoformat()
  assert advanced.start_date <= advanced.end_date


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
      "arguments": {
          "customer_id": CUSTOMER_ID,
          "resource_types": [],
          "start_date": effective_start,
          "end_date": today.isoformat(),
          "limit": 100,
          "page_token": "100",
          "login_customer_id": None,
      },
      "instruction": (
          "Call list_change_statuses with the arguments exactly as shown."
      ),
  }
  assert result["bulk_export_tool"] == "export_change_history_csv"
  assert "90 inclusive days" in result["coverage_note"]


def test_extended_continuation_arguments_use_live_snapshot_across_midnight():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  outside_context_calls = 0

  def _moving_account_today(*_args):
    nonlocal outside_context_calls
    override = changes._ACCOUNT_TODAY_OVERRIDE.get()  # pylint: disable=protected-access
    if override is not None:
      return override
    outside_context_calls += 1
    current_day = first_day if outside_context_calls == 1 else next_day
    return current_day, "Pacific/Kiritimati"

  with api._PAGED_QUERY_CACHE_LOCK:  # pylint: disable=protected-access
    api._PAGED_QUERY_CACHE.clear()  # pylint: disable=protected-access
    api._PAGED_QUERY_BUILDS.clear()  # pylint: disable=protected-access

  rows = [{"row": "first"}, {"row": "second"}, {"row": "third"}]
  try:
    with mock.patch(
        "ads_mcp.tools.changes._account_today",
        side_effect=_moving_account_today,
    ):
      with mock.patch(
          "ads_mcp.tools.api._page_cache_scope",
          return_value="test-scope",
      ):
        with mock.patch(
            "ads_mcp.tools.api._iter_gaql_query_attempt",
            return_value=rows,
        ) as mock_query:
          result = changes.get_change_history_extended(
              CUSTOMER_ID,
              resource_types=["CAMPAIGN"],
              start_date=(first_day - timedelta(days=89)).isoformat(),
              end_date=first_day.isoformat(),
              limit=1,
              login_customer_id="9876543210",
          )

          status_guidance = result["continuation_guidance"]["change_status"]
          event_guidance = result["continuation_guidance"]["change_event"]
          status_page = changes.list_change_statuses(
              **status_guidance["arguments"]
          )
          event_page = changes.list_change_events(
              **event_guidance["arguments"]
          )
  finally:
    with api._PAGED_QUERY_CACHE_LOCK:  # pylint: disable=protected-access
      api._PAGED_QUERY_CACHE.clear()  # pylint: disable=protected-access
      api._PAGED_QUERY_BUILDS.clear()  # pylint: disable=protected-access

  assert status_guidance["arguments"] == {
      "customer_id": CUSTOMER_ID,
      "resource_types": ["CAMPAIGN"],
      "start_date": (next_day - timedelta(days=89)).isoformat(),
      "end_date": first_day.isoformat(),
      "limit": 1,
      "page_token": status_guidance["page_token"],
      "login_customer_id": "9876543210",
  }
  assert event_guidance["arguments"] == {
      "customer_id": CUSTOMER_ID,
      "change_resource_types": ["CAMPAIGN"],
      "start_date": (next_day - timedelta(days=29)).isoformat(),
      "end_date": first_day.isoformat(),
      "limit": 1,
      "page_token": event_guidance["page_token"],
      "login_customer_id": "9876543210",
  }
  assert status_page["change_statuses"] == [{"row": "second"}]
  assert event_page["change_events"] == [{"row": "second"}]
  assert status_page["account_today"] == next_day.isoformat()
  assert event_page["account_today"] == next_day.isoformat()
  assert mock_query.call_count == 2


def test_extended_recovers_status_preview_when_retention_advances():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, first_day, next_day, next_day])
  status_response = {
      "change_statuses": [{"row": "status"}],
      "returned_count": 1,
      "total_count": 2,
      "truncated": True,
      "next_page_token": "status-next",
  }
  event_response = {
      "change_events": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_statuses",
        side_effect=[ToolError("START_DATE_TOO_OLD"), status_response],
    ) as mock_statuses:
      with mock.patch(
          "ads_mcp.tools.changes.list_change_events",
          return_value=event_response,
      ) as mock_events:
        result = changes.get_change_history_extended(CUSTOMER_ID)

  assert mock_statuses.call_count == 2
  assert (
      mock_statuses.call_args_list[0].kwargs["start_date"]
      == (first_day - timedelta(days=89)).isoformat()
  )
  assert (
      mock_statuses.call_args_list[1].kwargs["start_date"]
      == (next_day - timedelta(days=89)).isoformat()
  )
  assert mock_statuses.call_args_list[1].kwargs["end_date"] == (
      next_day.isoformat()
  )
  assert (
      mock_events.call_args.kwargs["start_date"]
      == (next_day - timedelta(days=29)).isoformat()
  )
  status_arguments = result["continuation_guidance"]["change_status"][
      "arguments"
  ]
  assert (
      status_arguments["start_date"]
      == (next_day - timedelta(days=89)).isoformat()
  )
  assert status_arguments["end_date"] == next_day.isoformat()
  assert result["date_range"] == {
      "start_date": (next_day - timedelta(days=89)).isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert "change_status retention advanced" in result["retention_refresh_note"]


def test_extended_recovers_event_and_refreshes_status_preview():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter(
      [first_day, first_day, first_day, next_day, next_day, next_day]
  )
  old_status_response = {
      "change_statuses": [{"row": "old-status"}],
      "returned_count": 1,
      "total_count": 2,
      "truncated": True,
      "next_page_token": "old-status-next",
  }
  refreshed_status_response = {
      "change_statuses": [{"row": "refreshed-status"}],
      "returned_count": 1,
      "total_count": 2,
      "truncated": True,
      "next_page_token": "refreshed-status-next",
  }
  event_response = {
      "change_events": [{"row": "event"}],
      "returned_count": 1,
      "total_count": 2,
      "truncated": True,
      "next_page_token": "event-next",
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_statuses",
        side_effect=[old_status_response, refreshed_status_response],
    ) as mock_statuses:
      with mock.patch(
          "ads_mcp.tools.changes.list_change_events",
          side_effect=[
              ToolError("START_DATE_TOO_OLD"),
              event_response,
              event_response,
          ],
      ) as mock_events:
        result = changes.get_change_history_extended(CUSTOMER_ID)

  assert mock_statuses.call_count == 2
  assert mock_events.call_count == 3
  assert (
      mock_statuses.call_args.kwargs["start_date"]
      == (next_day - timedelta(days=89)).isoformat()
  )
  assert mock_statuses.call_args.kwargs["end_date"] == next_day.isoformat()
  assert (
      mock_events.call_args.kwargs["start_date"]
      == (next_day - timedelta(days=29)).isoformat()
  )
  assert mock_events.call_args.kwargs["end_date"] == next_day.isoformat()
  assert result["change_statuses"] == [{"row": "refreshed-status"}]
  assert result["recent_change_events"] == [{"row": "event"}]
  assert (
      result["continuation_guidance"]["change_status"]["arguments"][
          "page_token"
      ]
      == "refreshed-status-next"
  )
  assert (
      result["continuation_guidance"]["change_event"]["arguments"][
          "page_token"
      ]
      == "event-next"
  )
  assert result["account_today"] == next_day.isoformat()
  assert "change_event retention advanced" in result["retention_refresh_note"]


def test_extended_converges_when_event_then_status_advance_again():
  first_day = date(2026, 7, 31)
  second_day = first_day + timedelta(days=1)
  third_day = second_day + timedelta(days=1)
  account_days = iter(
      [
          first_day,
          first_day,
          first_day,
          second_day,
          second_day,
          third_day,
          third_day,
      ]
  )
  status_response = {
      "change_statuses": [{"row": "status"}],
      "returned_count": 1,
      "total_count": 1,
      "truncated": False,
      "next_page_token": None,
  }
  event_response = {
      "change_events": [{"row": "event"}],
      "returned_count": 1,
      "total_count": 1,
      "truncated": False,
      "next_page_token": None,
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (next(account_days), "Etc/UTC"),
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_statuses",
        side_effect=[
            status_response,
            ToolError("START_DATE_TOO_OLD"),
            status_response,
        ],
    ) as mock_statuses:
      with mock.patch(
          "ads_mcp.tools.changes.list_change_events",
          side_effect=[
              ToolError("START_DATE_TOO_OLD"),
              event_response,
              event_response,
          ],
      ) as mock_events:
        result = changes.get_change_history_extended(CUSTOMER_ID)

  assert mock_statuses.call_count == 3
  assert mock_events.call_count == 3
  assert result["account_today"] == third_day.isoformat()
  assert result["date_range"]["end_date"] == third_day.isoformat()
  assert result["change_status_window"]["end_date"] == third_day.isoformat()
  assert result["change_event_window"]["end_date"] == third_day.isoformat()
  assert result["change_event_coverage"]["window"]["end_date"] == (
      third_day.isoformat()
  )


@pytest.mark.parametrize("failing_source", ["change_status", "change_event"])
def test_extended_shared_runner_replans_each_source_without_raw_error(
    failing_source,
):
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_call_count = 0
  rollover_call = 3 if failing_source == "change_status" else 4

  def _moving_account_today(*_args):
    nonlocal account_call_count
    account_call_count += 1
    current_day = first_day if account_call_count < rollover_call else next_day
    return current_day, "Pacific/Kiritimati"

  status_response = {
      "change_statuses": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  event_response = {
      "change_events": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  status_side_effect = (
      [ToolError("START_DATE_TOO_OLD"), status_response]
      if failing_source == "change_status"
      else None
  )
  event_side_effect = (
      [
          ToolError("START_DATE_TOO_OLD"),
          event_response,
          event_response,
      ]
      if failing_source == "change_event"
      else None
  )
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=_moving_account_today,
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_statuses",
        return_value=status_response,
        side_effect=status_side_effect,
    ):
      with mock.patch(
          "ads_mcp.tools.changes.list_change_events",
          return_value=event_response,
          side_effect=event_side_effect,
      ):
        result = changes.get_change_history_extended(CUSTOMER_ID)

  assert result["date_range"] == {
      "start_date": (next_day - timedelta(days=89)).isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert failing_source in result["retention_refresh_note"]


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


def test_collect_complete_change_rows_splits_capped_one_second_without_gap():
  timestamp = datetime(2026, 7, 1, 0, 0, 0)

  def query_builder(start, end):
    return changes._build_export_query(  # pylint: disable=protected-access
        ["change_event.change_date_time"],
        "change_event",
        "change_event.change_date_time",
        "change_event.change_resource_type",
        [],
        start,
        end,
    )

  with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        side_effect=[
            [{"id": "probe-1"}, {"id": "probe-2"}],
            [{"id": "later"}],
            [{"id": "earlier"}],
        ],
    ) as mock_query:
      with mock.patch(
          "ads_mcp.tools.changes.write_rows_to_temp_csv",
          side_effect=[
              ("/tmp/probe.csv", ["id"], 10),
              ("/tmp/later.csv", ["id"], 10),
              ("/tmp/earlier.csv", ["id"], 10),
          ],
      ):
        result = changes._collect_complete_change_rows(  # pylint: disable=protected-access
            query_builder,
            CUSTOMER_ID,
            timestamp,
            timestamp + timedelta(seconds=1),
            None,
            max_queries=10,
            columns=["id"],
        )

  assert result["complete"] is True
  assert result["row_count"] == 2
  later_query = mock_query.call_args_list[1].kwargs["query"]
  earlier_query = mock_query.call_args_list[2].kwargs["query"]
  assert (
      "change_event.change_date_time >= '2026-07-01 00:00:00.500000'"
      in later_query
  )
  assert (
      "change_event.change_date_time < '2026-07-01 00:00:00.500000'"
      in earlier_query
  )
  assert "change_event.change_date_time < '2026-07-01 00:00:01'" in later_query
  assert (
      "change_event.change_date_time >= '2026-07-01 00:00:00'" in earlier_query
  )


def test_collect_complete_change_rows_reports_unsplittable_microsecond():
  timestamp = datetime(2026, 7, 1, 0, 0, 0)
  with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        return_value=[{"id": "1"}, {"id": "2"}],
    ):
      with mock.patch(
          "ads_mcp.tools.changes.write_rows_to_temp_csv",
          return_value=("/tmp/one-microsecond.csv", ["id"], 10),
      ):
        result = changes._collect_complete_change_rows(  # pylint: disable=protected-access
            lambda start, end: f"{start}..{end}",
            CUSTOMER_ID,
            timestamp,
            timestamp + timedelta(microseconds=1),
            None,
            max_queries=10,
            columns=["id"],
        )

  assert result["complete"] is False
  assert result["row_count"] == 2
  assert result["unresolved_windows"][0]["reason"] == (
      "api_cap_reached_within_one_microsecond"
  )
  assert result["unresolved_windows"][0]["end_date_time_exclusive"] == (
      "2026-07-01 00:00:00.000001"
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


def test_export_moves_omitted_maximum_bounds_at_status_boundary():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, next_day])
  collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "row_count": 1,
      "query_count": 90,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        return_value=collection,
    ) as mock_collect:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          return_value=(
              "/tmp/status.csv",
              ["change_status.resource_name"],
              10,
          ),
      ):
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            include_recent_events=False,
        )

  expected_start = next_day - timedelta(days=89)
  status_call = mock_collect.call_args
  assert status_call.args[2].date() == expected_start
  assert status_call.args[3].date() == next_day + timedelta(days=1)
  assert result["requested_date_range"] == {
      "start_date": expected_start.isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["change_status_export"]["window"] == {
      "start_date": expected_start.isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["requested_range_fully_available"] is True
  assert result["account_today"] == next_day.isoformat()


def test_export_realigns_status_delta_at_event_boundary():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, first_day, next_day])
  status_collection = _daily_fragment_collection(
      first_day - timedelta(days=89),
      90,
      prefix="status",
  )
  status_delta_collection = _daily_fragment_collection(
      next_day,
      1,
      prefix="status-delta",
  )
  event_collection = {
      "fragment_paths": ["/tmp/event.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            status_collection,
            status_delta_collection,
            event_collection,
        ],
    ) as mock_collect:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              ("/tmp/status.csv", ["change_status.resource_name"], 10),
              ("/tmp/events.csv", ["change_event.resource_name"], 10),
          ],
      ) as mock_merge:
        result = changes.export_change_history_csv(CUSTOMER_ID)

  delta_start = datetime.combine(next_day, datetime.min.time())
  delta_call = mock_collect.call_args_list[1]
  assert delta_call.args[5] == 110
  assert delta_call.kwargs["initial_windows"] == [
      (delta_start, delta_start + timedelta(days=1))
  ]
  status_fragment_paths = mock_merge.call_args_list[0].args[0]
  assert "/tmp/status-0.csv" not in status_fragment_paths
  assert "/tmp/status-delta-0.csv" in status_fragment_paths
  assert len(status_fragment_paths) == 90
  assert result["change_status_export"]["row_count"] == 90
  assert result["change_status_export"]["query_count"] == 91
  assert result["change_status_export"]["window"] == {
      "start_date": (next_day - timedelta(days=89)).isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["change_status_export"]["complete"] is True
  assert (
      result["change_status_coverage"]["full_requested_range_covered"] is True
  )
  assert result["complete"] is True


def test_export_rollover_never_exceeds_exhausted_status_budget():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  old_start = datetime.combine(
      first_day - timedelta(days=89),
      datetime.min.time(),
  )
  old_end = datetime.combine(
      first_day + timedelta(days=1),
      datetime.min.time(),
  )
  midpoint = old_start + timedelta(days=45)
  status_collection = {
      "fragment_paths": [
          "/tmp/status-older.csv",
          "/tmp/status-newer.csv",
      ],
      "fragments": [
          {
              "start": old_start,
              "end_exclusive": midpoint,
              "fragment_path": "/tmp/status-older.csv",
              "row_count": 1,
          },
          {
              "start": midpoint,
              "end_exclusive": old_end,
              "fragment_path": "/tmp/status-newer.csv",
              "row_count": 1,
          },
      ],
      "row_count": 2,
      "query_count": 2,
      "complete": True,
      "unresolved_windows": [],
  }
  event_collection = {
      "fragment_paths": ["/tmp/event.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  account_days = iter([first_day, first_day, next_day])
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[status_collection, event_collection],
    ) as mock_collect:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              ("/tmp/status.csv", ["change_status.resource_name"], 10),
              ("/tmp/events.csv", ["change_event.resource_name"], 10),
          ],
      ) as mock_merge:
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            max_queries_per_resource=2,
        )

  assert mock_collect.call_count == 2
  assert mock_merge.call_args_list[0].args[0] == ["/tmp/status-newer.csv"]
  assert result["change_status_export"]["query_count"] == 2
  assert result["change_status_export"]["complete"] is False
  assert result["change_status_export"]["unresolved_windows"]
  assert all(
      window["reason"] == "retention_advanced_after_query_budget_exhausted"
      for window in result["change_status_export"]["unresolved_windows"]
  )
  assert result["available_data_complete"] is False
  assert result["complete"] is False
  assert "Rerun the same export" in result["next_step"]


def test_export_midnight_preserves_fully_explicit_bounds():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  start_date = (first_day - timedelta(days=1)).isoformat()
  end_date = first_day.isoformat()
  account_days = iter([first_day, first_day, next_day])
  status_collection = {
      "fragment_paths": ["/tmp/status.csv"],
      "row_count": 1,
      "query_count": 2,
      "complete": True,
      "unresolved_windows": [],
  }
  event_collection = {
      "fragment_paths": ["/tmp/event.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[status_collection, event_collection],
    ) as mock_collect:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              ("/tmp/status.csv", ["change_status.resource_name"], 10),
              ("/tmp/events.csv", ["change_event.resource_name"], 10),
          ],
      ):
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            start_date=start_date,
            end_date=end_date,
        )

  assert mock_collect.call_count == 2
  assert result["requested_date_range"] == {
      "start_date": start_date,
      "end_date": end_date,
  }
  assert result["change_status_export"]["window"] == {
      "start_date": start_date,
      "end_date": end_date,
  }


def test_export_explicit_future_end_adds_newly_available_status_day():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  explicit_start = first_day - timedelta(days=1)
  account_days = iter([first_day, first_day, next_day])
  status_collection = _daily_fragment_collection(
      explicit_start,
      2,
      prefix="status-explicit",
  )
  status_delta_collection = _daily_fragment_collection(
      next_day,
      1,
      prefix="status-explicit-delta",
  )
  event_collection = {
      "fragment_paths": ["/tmp/event.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            status_collection,
            status_delta_collection,
            event_collection,
        ],
    ) as mock_collect:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              ("/tmp/status.csv", ["change_status.resource_name"], 10),
              ("/tmp/events.csv", ["change_event.resource_name"], 10),
          ],
      ):
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            start_date=explicit_start.isoformat(),
            end_date=next_day.isoformat(),
        )

  delta_start = datetime.combine(next_day, datetime.min.time())
  assert mock_collect.call_args_list[1].kwargs["initial_windows"] == [
      (delta_start, delta_start + timedelta(days=1))
  ]
  assert result["requested_date_range"] == {
      "start_date": explicit_start.isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["change_status_export"]["window"] == {
      "start_date": explicit_start.isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["change_status_export"]["row_count"] == 3
  assert result["change_status_export"]["query_count"] == 3
  assert (
      result["change_status_coverage"]["full_requested_range_covered"] is True
  )
  assert result["complete"] is True


def test_export_explicit_start_preserves_fetched_older_status_fragment():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  explicit_start = first_day - timedelta(days=89)
  account_days = iter([first_day, first_day, next_day])
  status_collection = _daily_fragment_collection(
      explicit_start,
      90,
      prefix="status-explicit-oldest",
  )
  status_delta_collection = _daily_fragment_collection(
      next_day,
      1,
      prefix="status-explicit-new",
  )
  event_collection = {
      "fragment_paths": ["/tmp/event.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            status_collection,
            status_delta_collection,
            event_collection,
        ],
    ):
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              ("/tmp/status.csv", ["change_status.resource_name"], 10),
              ("/tmp/events.csv", ["change_event.resource_name"], 10),
          ],
      ) as mock_merge:
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            start_date=explicit_start.isoformat(),
            end_date=next_day.isoformat(),
        )

  status_paths = mock_merge.call_args_list[0].args[0]
  assert "/tmp/status-explicit-oldest-0.csv" in status_paths
  assert "/tmp/status-explicit-new-0.csv" in status_paths
  assert len(status_paths) == 91
  assert result["change_status_export"]["window"] == {
      "start_date": explicit_start.isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["change_status_export"]["row_count"] == 91
  assert result["change_status_export"]["query_count"] == 91
  assert (
      result["change_status_coverage"]["full_requested_range_covered"] is True
  )


def test_partial_default_midnight_never_inverts_requested_range():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  explicit_end = first_day - timedelta(days=89)
  account_days = iter([first_day, next_day])
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
    ) as mock_collect:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          return_value=(
              "/tmp/status.csv",
              ["change_status.resource_name"],
              0,
          ),
      ):
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            end_date=explicit_end.isoformat(),
            include_recent_events=False,
        )

  mock_collect.assert_not_called()
  assert result["requested_date_range"] == {
      "start_date": explicit_end.isoformat(),
      "end_date": explicit_end.isoformat(),
  }
  assert result["change_status_export"]["window"] is None
  assert result["change_status_coverage"]["available"] is False
  assert result["complete"] is True


def test_omitted_end_moves_without_changing_explicit_start():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  collection = {
      "fragment_paths": ["/tmp/status.csv"],
      "row_count": 1,
      "query_count": 2,
      "complete": True,
      "unresolved_windows": [],
  }
  account_days = iter([first_day, next_day])
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        return_value=collection,
    ):
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          return_value=(
              "/tmp/status.csv",
              ["change_status.resource_name"],
              10,
          ),
      ):
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            start_date=first_day.isoformat(),
            include_recent_events=False,
        )

  assert result["requested_date_range"] == {
      "start_date": first_day.isoformat(),
      "end_date": next_day.isoformat(),
  }
  assert result["change_status_export"]["window"] == {
      "start_date": first_day.isoformat(),
      "end_date": next_day.isoformat(),
  }


def test_preview_partial_default_midnight_never_inverts_requested_range():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  explicit_end = first_day - timedelta(days=89)
  account_days = iter([first_day, next_day])
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_statuses",
    ) as mock_statuses:
      result = changes.get_change_history_extended(
          CUSTOMER_ID,
          end_date=explicit_end.isoformat(),
          include_recent_events=False,
      )

  mock_statuses.assert_not_called()
  assert result["date_range"] == {
      "start_date": explicit_end.isoformat(),
      "end_date": explicit_end.isoformat(),
  }
  assert result["change_status_window"] is None
  assert result["change_status_coverage"]["available"] is False


def test_export_recovers_once_when_event_retention_advances_at_midnight():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, first_day, first_day, next_day])
  status_start = first_day - timedelta(days=89)
  status_fragments = [
      {
          "start": datetime.combine(
              status_start + timedelta(days=index),
              datetime.min.time(),
          ),
          "end_exclusive": datetime.combine(
              status_start + timedelta(days=index + 1),
              datetime.min.time(),
          ),
          "fragment_path": f"/tmp/status-{index}.csv",
          "row_count": 1,
      }
      for index in range(90)
  ]
  status_collection = {
      "fragment_paths": [
          fragment["fragment_path"] for fragment in status_fragments
      ],
      "fragments": status_fragments,
      "row_count": 90,
      "query_count": 90,
      "complete": True,
      "unresolved_windows": [],
  }
  delta_start = datetime.combine(next_day, datetime.min.time())
  status_delta_collection = {
      "fragment_paths": ["/tmp/status-delta.csv"],
      "fragments": [
          {
              "start": delta_start,
              "end_exclusive": delta_start + timedelta(days=1),
              "fragment_path": "/tmp/status-delta.csv",
              "row_count": 1,
          }
      ],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  event_collection = {
      "fragment_paths": ["/tmp/event.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes._collect_complete_change_rows",
        side_effect=[
            status_collection,
            ToolError("START_DATE_TOO_OLD"),
            status_delta_collection,
            event_collection,
        ],
    ) as mock_collect:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              ("/tmp/status.csv", ["change_status.resource_name"], 10),
              ("/tmp/events.csv", ["change_event.resource_name"], 20),
          ],
      ):
        result = changes.export_change_history_csv(CUSTOMER_ID)

  first_event_call = mock_collect.call_args_list[1]
  status_delta_call = mock_collect.call_args_list[2]
  retry_event_call = mock_collect.call_args_list[3]
  assert first_event_call.args[2].date() == first_day - timedelta(days=29)
  assert retry_event_call.args[2].date() == next_day - timedelta(days=29)
  assert first_event_call.args[5] == 200
  assert status_delta_call.args[5] == 110
  assert status_delta_call.kwargs["initial_windows"] == [
      (delta_start, delta_start + timedelta(days=1))
  ]
  assert retry_event_call.args[5] == 199
  assert result["change_status_export"]["query_count"] == 91
  assert result["change_status_export"]["row_count"] == 90
  assert result["change_event_export"]["query_count"] == 2
  assert (
      result["change_event_export"]["window"]["start_date"]
      == (next_day - timedelta(days=29)).isoformat()
  )
  assert result["account_today"] == next_day.isoformat()
  assert "change_event retention advanced" in result["retention_refresh_note"]


def test_event_recovery_reports_attempt_when_slice_expires():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  oldest_event_day = first_day - timedelta(days=29)
  account_days = iter([first_day, first_day, first_day, next_day])
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch(
        "ads_mcp.tools.changes.run_gaql_query",
        side_effect=ToolError("START_DATE_TOO_OLD"),
    ) as mock_query:
      with mock.patch(
          "ads_mcp.tools.changes.merge_temp_csv_files",
          side_effect=[
              (
                  "/tmp/status.csv",
                  ["change_status.resource_name"],
                  0,
              ),
              (
                  "/tmp/events.csv",
                  ["change_event.resource_name"],
                  0,
              ),
          ],
      ):
        result = changes.export_change_history_csv(
            CUSTOMER_ID,
            resource_types=["AD"],
            start_date=oldest_event_day.isoformat(),
            end_date=oldest_event_day.isoformat(),
        )

  mock_query.assert_called_once()
  assert result["change_event_export"]["file_path"] == "/tmp/events.csv"
  assert result["change_event_export"]["row_count"] == 0
  assert result["change_event_export"]["query_count"] == 1
  assert result["change_event_export"]["window"] is None
  assert result["change_event_export"]["complete"] is True
  assert result["change_event_coverage"]["available"] is False
  assert result["available_data_complete"] is True
  assert result["complete"] is True


def test_late_status_retention_recovery_uses_only_remaining_query_budget():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, first_day, next_day])
  capped_rows = [{"id": "1"}, {"id": "2"}]
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
      with mock.patch(
          "ads_mcp.tools.changes.run_gaql_query",
          side_effect=[
              capped_rows,
              [{"id": "middle"}],
              [{"id": "newest"}],
              [{"id": "later"}],
              ToolError("START_DATE_TOO_OLD"),
              [{"id": "coarsened-retry"}],
          ],
      ) as mock_query:
        with mock.patch(
            "ads_mcp.tools.changes.write_rows_to_temp_csv",
            side_effect=[
                ("/tmp/status-oldest.csv", ["id"], 10),
                ("/tmp/status-middle.csv", ["id"], 10),
                ("/tmp/status-newest.csv", ["id"], 10),
                ("/tmp/status-later.csv", ["id"], 10),
                ("/tmp/status-coarsened-retry.csv", ["id"], 10),
            ],
        ):
          with mock.patch(
              "ads_mcp.tools.changes.merge_temp_csv_files",
              return_value=(
                  "/tmp/status.csv",
                  ["change_status.resource_name"],
                  10,
              ),
          ):
            result = changes.export_change_history_csv(
                CUSTOMER_ID,
                start_date=(first_day - timedelta(days=2)).isoformat(),
                end_date=first_day.isoformat(),
                include_recent_events=False,
                max_queries_per_resource=6,
            )

  assert mock_query.call_count == 6
  assert result["change_status_export"]["query_count"] == 6
  assert result["change_status_export"]["complete"] is True
  assert result["requested_date_range"] == {
      "start_date": (first_day - timedelta(days=2)).isoformat(),
      "end_date": first_day.isoformat(),
  }
  assert result["change_status_export"]["partitioning"]["window_count"] == 1
  assert (
      result["change_status_export"]["partitioning"]["strategy"]
      == "budget_coarsened_contiguous_windows"
  )
  assert "change_status retention advanced" in result["retention_refresh_note"]


def test_late_event_retention_recovery_uses_only_remaining_query_budget():
  first_day = date(2026, 7, 31)
  next_day = first_day + timedelta(days=1)
  account_days = iter([first_day, first_day, first_day, next_day])
  capped_rows = [{"id": "1"}, {"id": "2"}]
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      side_effect=lambda *_args: (
          next(account_days),
          "Pacific/Kiritimati",
      ),
  ):
    with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
      with mock.patch(
          "ads_mcp.tools.changes.run_gaql_query",
          side_effect=[
              capped_rows,
              [{"id": "later"}],
              ToolError("START_DATE_TOO_OLD"),
              [{"id": "retry"}],
          ],
      ) as mock_query:
        with mock.patch(
            "ads_mcp.tools.changes.write_rows_to_temp_csv",
            side_effect=[
                ("/tmp/event-initial.csv", ["id"], 10),
                ("/tmp/event-later.csv", ["id"], 10),
                ("/tmp/event-retry.csv", ["id"], 10),
            ],
        ):
          with mock.patch(
              "ads_mcp.tools.changes.merge_temp_csv_files",
              side_effect=[
                  (
                      "/tmp/status.csv",
                      ["change_status.resource_name"],
                      0,
                  ),
                  (
                      "/tmp/events.csv",
                      ["change_event.resource_name"],
                      10,
                  ),
              ],
          ):
            result = changes.export_change_history_csv(
                CUSTOMER_ID,
                resource_types=["AD"],
                start_date=(first_day - timedelta(days=1)).isoformat(),
                end_date=first_day.isoformat(),
                max_queries_per_resource=4,
            )

  assert mock_query.call_count == 4
  assert result["change_event_export"]["query_count"] == 4
  assert result["change_event_export"]["complete"] is True
  assert "change_event retention advanced" in result["retention_refresh_note"]


def test_late_retention_failure_with_no_budget_stops_actionably():
  first_day = date(2026, 7, 31)
  capped_rows = [{"id": "1"}, {"id": "2"}]
  with mock.patch(
      "ads_mcp.tools.changes._account_today",
      return_value=(first_day, "Pacific/Kiritimati"),
  ):
    with mock.patch.object(changes, "_CHANGE_HISTORY_RESULT_CAP", 2):
      with mock.patch(
          "ads_mcp.tools.changes.run_gaql_query",
          side_effect=[
              capped_rows,
              [{"id": "later"}],
              ToolError("START_DATE_TOO_OLD"),
          ],
      ) as mock_query:
        with mock.patch(
            "ads_mcp.tools.changes.write_rows_to_temp_csv",
            side_effect=[
                ("/tmp/status-initial.csv", ["id"], 10),
                ("/tmp/status-later.csv", ["id"], 10),
            ],
        ):
          with pytest.raises(
              ToolError,
              match=(
                  "after 3 query attempts, exhausting the configured "
                  "max_queries_per_resource=3.*No recovery query was sent"
              ),
          ):
            changes.export_change_history_csv(
                CUSTOMER_ID,
                start_date=first_day.isoformat(),
                end_date=first_day.isoformat(),
                include_recent_events=False,
                max_queries_per_resource=3,
            )

  assert mock_query.call_count == 3


def test_cap_export_guidance_arguments_are_directly_executable():
  today = date.today()
  truncated_statuses = {
      "change_statuses": [],
      "returned_count": 10_000,
      "total_count": 10_000,
      "truncated": True,
      "next_page_token": None,
  }
  empty_events = {
      "change_events": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value=truncated_statuses,
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
        return_value=empty_events,
    ):
      preview = changes.get_change_history_extended(
          CUSTOMER_ID,
          resource_types=["CAMPAIGN"],
          start_date=(today - timedelta(days=6)).isoformat(),
          end_date=today.isoformat(),
          limit=10_000,
      )

  guidance = preview["continuation_guidance"]["change_status"]
  complete_collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "row_count": 1,
      "query_count": 7,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
      return_value=complete_collection,
  ):
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        return_value=("/tmp/status.csv", ["change_status.resource_name"], 10),
    ):
      export = changes.export_change_history_csv(**guidance["arguments"])

  assert guidance["arguments"]["resource_types"] == ["CAMPAIGN"]
  assert guidance["arguments"]["include_recent_events"] is False
  assert export["change_status_export"]["row_count"] == 1


def test_cap_export_guidance_beats_incomplete_snapshot_continuation():
  today = date.today()
  event_window = {
      "start_date": (today - timedelta(days=6)).isoformat(),
      "end_date": today.isoformat(),
  }
  exact_export_call = {
      "tool": "export_change_history_csv",
      "arguments": {
          "customer_id": CUSTOMER_ID,
          "resource_types": ["CAMPAIGN"],
          "resource_change_operations": ["UPDATE"],
          **event_window,
          "include_recent_events": True,
          "login_customer_id": "987",
      },
  }
  statuses = {
      "change_statuses": [],
      "returned_count": 0,
      "total_count": 0,
      "truncated": False,
      "next_page_token": None,
  }
  capped_events = {
      "change_events": [{"change_event.resource_name": "x"}],
      "returned_count": 100,
      "total_count": 10_000,
      "truncated": True,
      "api_result_cap": 10_000,
      "next_page_token": "known-incomplete-page-2",
      "bulk_export_call": exact_export_call,
  }

  guidance = changes._preview_continuation_guidance(  # pylint: disable=protected-access
      statuses,
      capped_events,
      customer_id=CUSTOMER_ID,
      status_window=event_window,
      event_window=event_window,
      status_resource_types=["CAMPAIGN"],
      event_resource_types=["CAMPAIGN"],
      limit=100,
      login_customer_id="987",
  )

  assert guidance["change_event"]["tool"] == "export_change_history_csv"
  assert guidance["change_event"]["arguments"] == (
      exact_export_call["arguments"]
  )
  assert "page through" in guidance["change_event"]["instruction"]


def test_extended_history_shares_byte_budget_without_skip_token():
  today = date.today()
  window = {
      "start_date": (today - timedelta(days=6)).isoformat(),
      "end_date": today.isoformat(),
  }
  status_export = {
      "tool": "export_gaql_csv",
      "arguments": {"snapshot_token": "gaql-snapshot-v1:" + "a" * 32},
  }
  event_export = {
      "tool": "export_gaql_csv",
      "arguments": {"snapshot_token": "gaql-snapshot-v1:" + "b" * 32},
  }
  statuses = {
      "change_statuses": [{"kind": "status", "payload": "s" * 20_000}],
      "returned_count": 1,
      "total_count": 1,
      "truncated": False,
      "has_more": False,
      "complete_inline": True,
      "next_page_token": None,
      "bulk_export_call": status_export,
  }
  events = {
      "change_events": [
          {"kind": "event-1", "payload": "e" * 20_000},
          {"kind": "event-2", "payload": "e" * 20_000},
      ],
      "returned_count": 2,
      "total_count": 3,
      "truncated": True,
      "has_more": True,
      "complete_inline": False,
      "next_page_token": "after-event-2",
      "bulk_export_call": event_export,
  }
  with mock.patch(
      "ads_mcp.tools.changes.list_change_statuses",
      return_value=statuses,
  ):
    with mock.patch(
        "ads_mcp.tools.changes.list_change_events",
        return_value=events,
    ):
      result = changes.get_change_history_extended(
          CUSTOMER_ID,
          resource_types=["CAMPAIGN"],
          **window,
      )

  shared = result["shared_inline_delivery"]
  assert shared["inline_bytes"] <= shared["inline_byte_limit"]
  assert result["change_status_returned_count"] == 1
  assert result["recent_change_event_returned_count"] == 1
  assert result["recent_change_event_next_page_token"] is None
  assert shared["omitted_counts"]["change_events"] == 1
  guidance = result["continuation_guidance"]["change_event"]
  assert guidance["tool"] == "export_gaql_csv"
  assert guidance["arguments"] == event_export["arguments"]
  assert "would skip data" in guidance["instruction"]


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
  assert windows[0][0] == datetime.combine(
      today - timedelta(days=4),
      datetime.min.time(),
  )
  assert windows[-1][1] == datetime.combine(
      today + timedelta(days=1),
      datetime.min.time(),
  )
  assert result["change_status_export"]["complete"] is True
  assert result["change_status_export"]["daily_resolution_complete"] is False
  assert result["available_data_complete"] is False
  assert result["complete"] is False
  assert "Increase max_queries_per_resource" in result["next_step"]


def test_export_does_not_recommend_impossible_microsecond_cap_retry():
  status_collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  event_collection = {
      "fragment_paths": ["/tmp/event-fragment.csv"],
      "row_count": 10_000,
      "query_count": 41,
      "complete": False,
      "unresolved_windows": [
          {
              "start_date_time": "2026-07-30 12:00:00.000001",
              "end_date_time_exclusive": "2026-07-30 12:00:00.000002",
              "reason": "api_cap_reached_within_one_microsecond",
              "returned_count": 10_000,
          }
      ],
  }
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
      side_effect=[status_collection, event_collection],
  ):
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        side_effect=[
            ("/tmp/status.csv", ["change_status.resource_name"], 10),
            ("/tmp/events.csv", ["change_event.resource_name"], 20),
        ],
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          start_date=date.today().isoformat(),
          end_date=date.today().isoformat(),
      )

  assert "Time subdivision is exhausted" in result["next_step"]
  assert (
      "higher max_queries_per_resource cannot resolve" in result["next_step"]
  )
  assert "resource_types divided into smaller subsets" in result["next_step"]


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


def test_ad_only_export_uses_applicable_event_range_for_availability():
  today = date.today()
  collection = {
      "fragment_paths": ["/tmp/event-fragment.csv"],
      "row_count": 1,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
      return_value=collection,
  ) as mock_collect:
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        side_effect=[
            ("/tmp/status.csv", ["change_status.resource_name"], 1),
            ("/tmp/events.csv", ["change_event.resource_name"], 1),
        ],
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          resource_types=["AD"],
          start_date=(today - timedelta(days=6)).isoformat(),
          end_date=today.isoformat(),
      )

  assert mock_collect.call_count == 1
  assert "FROM change_event" in mock_collect.call_args.args[0](
      datetime(2026, 7, 1),
      datetime(2026, 7, 2),
  )
  assert result["change_status_coverage"]["available"] is False
  assert (
      result["change_event_coverage"]["full_requested_range_covered"] is True
  )
  assert result["requested_range_fully_available"] is True


def test_shared_set_only_export_uses_applicable_status_range_for_availability():
  today = date.today()
  collection = {
      "fragment_paths": ["/tmp/status-fragment.csv"],
      "row_count": 1,
      "query_count": 7,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
      return_value=collection,
  ) as mock_collect:
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        return_value=("/tmp/status.csv", ["change_status.resource_name"], 1),
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          resource_types=["SHARED_SET"],
          start_date=(today - timedelta(days=6)).isoformat(),
          end_date=today.isoformat(),
      )

  assert mock_collect.call_count == 1
  assert "FROM change_status" in mock_collect.call_args.args[0](
      datetime(2026, 7, 1),
      datetime(2026, 7, 2),
  )
  assert (
      result["change_status_coverage"]["full_requested_range_covered"] is True
  )
  assert result["change_event_coverage"]["available"] is False
  assert result["requested_range_fully_available"] is True


def test_requested_range_requires_at_least_one_applicable_change_source():
  with mock.patch(
      "ads_mcp.tools.changes._collect_complete_change_rows",
  ) as mock_collect:
    with mock.patch(
        "ads_mcp.tools.changes.merge_temp_csv_files",
        return_value=("/tmp/status.csv", ["change_status.resource_name"], 1),
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          resource_types=["AD"],
          include_recent_events=False,
      )

  mock_collect.assert_not_called()
  assert result["requested_range_fully_available"] is False


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


def test_export_removes_status_fragments_when_event_query_fails():
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

  mock_remove.assert_called_once_with("/tmp/status-fragment.csv")


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
