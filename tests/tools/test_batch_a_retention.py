"""Explicit retention policy and immutable-history delivery regressions."""

from datetime import date
from datetime import timedelta
from unittest import mock

from fastmcp.exceptions import ToolError
import pytest

from ads_mcp.tools import api
from ads_mcp.tools import changes
from ads_mcp.tools import _gaql
from ads_mcp.tools import _history


TODAY = date(2026, 8, 13)
FIELD = "change_event.change_date_time"


def history_query(predicate, limit="LIMIT 100"):
  return (
      "SELECT change_event.resource_name FROM change_event "
      f"WHERE {predicate} {limit}"
  )


@pytest.fixture(autouse=True)
def offline_calendar():
  with (
      mock.patch.object(
          api,
          "get_ads_credential_cache_scope",
          return_value="batch-a-retention",
      ),
      mock.patch.object(
          api,
          "get_account_calendar",
          return_value=(TODAY, "America/New_York"),
      ),
      mock.patch.object(
          changes, "_account_today", return_value=(TODAY, "America/New_York")
      ),
  ):
    yield


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
def test_out_of_window_error_explains_explicit_clamp_before_reporting(
    tool_name,
):
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError) as error:
      getattr(api, tool_name)(
          history_query(f"{FIELD} DURING LAST_30_DAYS"), "123"
      )
  run.assert_not_called()
  assert "2026-07-15" in str(error.value)
  assert "retention_policy" in str(error.value)
  assert "clamp" in str(error.value)


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
def test_clamp_preserves_last_30_days_exclusion_of_today(tool_name, tmp_path):
  kwargs = (
      {"output_path": str(tmp_path / "history.csv")}
      if tool_name == "export_gaql_csv"
      else {}
  )
  with mock.patch.object(api, "run_gaql_query", return_value=[]) as run:
    result = getattr(api, tool_name)(
        history_query(f"{FIELD} DURING LAST_30_DAYS"),
        "123",
        retention_policy="clamp",
        **kwargs,
    )
  executed = run.call_args.kwargs["query"]
  assert f"{FIELD} >= '2026-07-15 00:00:00'" in executed
  assert f"{FIELD} < '2026-08-13 00:00:00'" in executed
  assert (
      result["retention"]["requested_range"]["start"] == "2026-07-14 00:00:00"
  )
  assert result["retention"]["clamped"] is True
  assert result["warnings"]


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
@pytest.mark.parametrize("separator", ["-", " "])
@pytest.mark.parametrize(
    "literal",
    [
        "LAST_7_DAYS",
        "LAST_30_DAYS",
        "LAST_90_DAYS",
        "LAST_12_MONTHS",
        "LAST_QUARTER",
    ],
)
def test_history_range_aliases_match_canonical_account_local_bounds(
    tool_name, separator, literal, tmp_path
):
  alias = literal.replace("_", separator).lower()
  extra_filter = (
      " AND change_event.user_email = "
      "'change_event.change_date_time DURING LAST-7-DAYS'"
  )
  query = history_query(f"{FIELD} DURING {alias}" + extra_filter)
  canonical_query = history_query(f"{FIELD} DURING {literal}" + extra_filter)
  expected_query, expected_metadata = _history.prepare_change_event_query(
      canonical_query, TODAY, "America/New_York", "clamp"
  )
  kwargs = (
      {"output_path": str(tmp_path / "aliases.csv")}
      if tool_name == "export_gaql_csv"
      else {}
  )
  with mock.patch.object(api, "run_gaql_query", return_value=[]) as run:
    result = getattr(api, tool_name)(
        query, "123", retention_policy="clamp", **kwargs
    )
  assert result["retention"] == expected_metadata["retention"]
  assert result["account_today"] == TODAY.isoformat()
  assert result["original_query"] == query
  if expected_query is None:
    run.assert_not_called()
    assert result["executed_query"] is None
  else:
    prepared = _gaql.preprocess_gaql_query(expected_query)
    assert result["executed_query"] == prepared
    run.assert_called_once_with(
        query=prepared, customer_id="123", login_customer_id=None
    )


def test_out_of_window_history_alias_still_requires_explicit_clamp():
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="retention_policy"):
      api.execute_gaql(history_query(f"{FIELD} DURING LAST 30 DAYS"), "123")
  run.assert_not_called()


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
def test_wholly_historical_clamp_does_not_query_a_recent_period(
    tool_name, tmp_path
):
  kwargs = (
      {"output_path": str(tmp_path / "empty.csv")}
      if tool_name == "export_gaql_csv"
      else {}
  )
  with mock.patch.object(api, "run_gaql_query") as run:
    result = getattr(api, tool_name)(
        history_query(f"{FIELD} BETWEEN '2026-01-01' AND '2026-01-31'"),
        "123",
        retention_policy="clamp",
        **kwargs,
    )
  run.assert_not_called()
  assert result["retention"]["applied_range"] is None
  assert result["retention"]["unavailable_ranges"]
  assert result.get("data", []) == []


@pytest.mark.parametrize(
    "limit", ["", "LIMIT 0", "LIMIT -1", "LIMIT 10001", "LIMIT 2.5"]
)
def test_change_event_requires_valid_explicit_api_limit(limit):
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="LIMIT.*10,000"):
      api.execute_gaql(
          history_query(f"{FIELD} DURING LAST_7_DAYS", limit), "123"
      )
  run.assert_not_called()


@pytest.mark.parametrize("digits", ["9" * 5000, "0" * 5000])
def test_huge_invalid_limits_raise_actionable_tool_error(digits):
  with pytest.raises(ToolError, match="LIMIT.*10,000"):
    _history.prepare_change_event_query(
        history_query(f"{FIELD} DURING LAST_7_DAYS", f"LIMIT {digits}"),
        TODAY,
        "Etc/UTC",
        "error",
    )


def test_leading_zero_limit_does_not_overflow_integer_parser():
  _, metadata = _history.prepare_change_event_query(
      history_query(
          f"{FIELD} DURING LAST_7_DAYS", "LIMIT " + "0" * 5000 + "1"
      ),
      TODAY,
      "Etc/UTC",
      "error",
  )
  assert metadata["query_result_limit"] == 1


def test_unrepresentable_inclusive_end_has_actionable_error():
  with mock.patch.object(changes, "run_gaql_query_page") as run:
    with pytest.raises(ToolError, match="end_date.*9999-12-31"):
      changes.list_change_events(
          "123", start_date="2026-08-01", end_date="9999-12-31"
      )
  run.assert_not_called()


def test_unsupported_retention_grammar_is_not_rewritten():
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="list_change_events"):
      api.execute_gaql(
          history_query(f"{FIELD} != '2026-07-15'"),
          "123",
          retention_policy="clamp",
      )
  run.assert_not_called()


def test_clamp_preserves_timestamp_operator_precision_and_other_filters():
  query = history_query(
      f"{FIELD} > '2026-07-01 12:34:56.123456' AND "
      f"{FIELD} <= '2026-08-01 01:02:03.456789' AND "
      "change_event.user_email = "
      "'LIMIT 10001 AND change_event.change_date_time'"
  )
  with mock.patch.object(api, "run_gaql_query", return_value=[]) as run:
    result = api.execute_gaql(query, "123", retention_policy="clamp")
  executed = run.call_args.kwargs["query"]
  assert f"{FIELD} <= '2026-08-01 01:02:03.456789'" in executed
  assert "'LIMIT 10001 AND change_event.change_date_time'" in executed
  assert result["retention"]["requested_range"]["start_inclusive"] is False
  assert result["retention"]["applied_range"]["end_inclusive"] is True


def test_performance_queries_do_not_resolve_customer_calendar():
  with mock.patch.object(api, "run_gaql_query", return_value=[]) as run:
    query = (
        "SELECT campaign.id FROM campaign "
        "WHERE segments.date DURING LAST_90_DAYS"
    )
    api.execute_gaql(query, "123", retention_policy="clamp")
  api.get_account_calendar.assert_not_called()
  run.assert_called_once()


def test_list_change_events_clamp_keeps_requested_coverage():
  with mock.patch.object(
      changes,
      "run_gaql_query_page",
      return_value={
          "rows": [],
          "total_results_count": 0,
          "next_page_token": None,
      },
  ) as run:
    result = changes.list_change_events(
        "123",
        start_date="2026-06-01",
        end_date="2026-08-31",
        retention_policy="clamp",
    )
  assert "2026-07-15" in run.call_args.kwargs["query"]
  assert "2026-08-14" in run.call_args.kwargs["query"]
  assert result["retention"]["clamped"] is True
  assert len(result["retention"]["unavailable_ranges"]) == 2
  assert result["resolved_date_range"] == {
      "start_date": "2026-07-15",
      "end_date": "2026-08-13",
  }


def test_list_change_events_wholly_unavailable_has_no_reporting_io():
  with mock.patch.object(changes, "run_gaql_query_page") as run:
    result = changes.list_change_events(
        "123",
        start_date="2026-01-01",
        end_date="2026-01-31",
        retention_policy="clamp",
    )
  run.assert_not_called()
  assert result["change_events"] == []
  assert result["retention"]["applied_range"] is None


def test_last_available_day_aging_out_preserves_unavailable_coverage():
  with (
      mock.patch.object(
          changes,
          "_account_today",
          side_effect=[
              (TODAY, "America/New_York"),
              (TODAY + timedelta(days=1), "America/New_York"),
          ],
      ),
      mock.patch.object(
          changes,
          "run_gaql_query_page",
          side_effect=ToolError("START_DATE_TOO_OLD"),
      ) as run,
  ):
    result = changes.list_change_events(
        "123",
        start_date="2026-07-15",
        end_date="2026-07-15",
        retention_policy="clamp",
    )
  run.assert_called_once()
  assert result["change_events"] == []
  assert result["retention_refreshed"] is True
  assert result["account_today"] == "2026-08-14"
  assert result["retention"]["applied_range"] is None
  assert result["retention"]["unavailable_ranges"]
  assert result["resolved_date_range"] is None
  assert result["warnings"]


@pytest.mark.parametrize("days", [29, 30, 31, 90])
def test_inclusive_calendar_lookbacks_and_named_ranges_are_distinct(days):
  start = TODAY - timedelta(days=days - 1)
  requested = _history.date_interval(start.isoformat(), TODAY.isoformat())
  applied, metadata = _history.plan_retention(
      requested, TODAY, "America/New_York", "clamp"
  )
  assert applied.start.date() == max(start, date(2026, 7, 15))
  assert applied.end.date() == TODAY + timedelta(days=1)
  assert metadata["retention"]["clamped"] == (days > 30)
  if days <= 30:
    assert (
        _history.plan_retention(requested, TODAY, "America/New_York", "error")[
            0
        ]
        == requested
    )


@pytest.mark.parametrize("day", [date(2026, 3, 8), date(2026, 11, 1)])
def test_retention_uses_calendar_days_across_dst(day):
  requested = _history.date_interval(
      (day - timedelta(days=29)).isoformat(), day.isoformat()
  )
  applied, metadata = _history.plan_retention(
      requested, day, "America/New_York", "error"
  )
  assert applied == requested
  assert metadata["retention"]["unavailable_ranges"] == []


@pytest.mark.parametrize(
    "predicate,empty",
    [
        (f"{FIELD} >= '2026-07-14' AND {FIELD} < '2026-07-15'", True),
        (f"{FIELD} >= '2026-07-14' AND {FIELD} <= '2026-07-15'", False),
        (f"{FIELD} > '2026-08-14' AND {FIELD} < '2026-08-15'", True),
        (f"{FIELD} BETWEEN '20260714' AND '20260715'", False),
    ],
)
def test_date_only_comparison_operators_keep_midnight_semantics(
    predicate, empty
):
  prepared, metadata = _history.prepare_change_event_query(
      history_query(predicate), TODAY, "America/New_York", "clamp"
  )
  assert (prepared is None) == empty
  assert (metadata["retention"]["applied_range"] is None) == empty


@pytest.mark.parametrize(
    "predicate",
    [
        f"{FIELD} >= '2026-08-01'",
        f"{FIELD} DURING ALL_TIME",
        f"{FIELD} >= '2026-08-01' AND {FIELD} >= '2026-08-02' "
        f"AND {FIELD} < '2026-08-13'",
        f"({FIELD} >= '2026-08-01') AND {FIELD} < '2026-08-13'",
    ],
)
def test_unsupported_forms_never_get_silently_repaired(predicate):
  with pytest.raises(ToolError, match="list_change_events"):
    _history.prepare_change_event_query(
        history_query(predicate), TODAY, "Etc/UTC", "clamp"
    )


def test_clamped_pagination_and_export_preserve_original_calendar_and_coverage(
    tmp_path,
):
  rows = [{"change_event.resource_name": str(index)} for index in range(3)]
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=rows
  ) as run:
    first = changes.list_change_events(
        "123",
        start_date="2026-06-01",
        end_date="2026-08-13",
        limit=1,
        retention_policy="clamp",
    )
    with mock.patch.object(
        changes,
        "_account_today",
        side_effect=AssertionError("calendar must stay frozen"),
    ):
      second = changes.list_change_events(**first["continuation"]["arguments"])
      repeated = changes.list_change_events(
          "123",
          start_date="2026-06-01",
          end_date="2026-08-13",
          limit=1,
          retention_policy="clamp",
          page_token=first["next_page_token"],
      )
      exported = api.export_gaql_csv(
          **first["bulk_export_call"]["arguments"],
          output_path=str(tmp_path / "exact.csv"),
      )
  assert second["change_events"] == [rows[1]]
  assert (
      repeated["retention"]
      == first["retention"]
      == second["retention"]
      == exported["retention"]
  )
  assert (
      exported["account_today"]
      == first["account_today"]
      == second["account_today"]
  )
  assert "2026-07-15" in exported["executed_query"]
  assert exported["row_count"] == 3
  run.assert_called_once()


@pytest.mark.parametrize(
    "overrides",
    [
        {"retention_policy": "error"},
        {"start_date": "2026-07-16"},
        {"end_date": "2026-08-12"},
        {"change_resource_types": "CAMPAIGN"},
        {"customer_id": "456"},
        {"login_customer_id": "789"},
        {"limit": 2},
    ],
)
def test_continuation_rejects_changed_request_without_reporting_io(overrides):
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=[{"id": 1}, {"id": 2}]
  ) as run:
    first = changes.list_change_events(
        "123", start_date="2026-06-01", limit=1, retention_policy="clamp"
    )
    args = {**first["continuation"]["arguments"], **overrides}
    with pytest.raises(ToolError):
      changes.list_change_events(**args)
  run.assert_called_once()


def test_snapshot_retention_is_credential_scoped_and_cannot_be_reclamped(
    tmp_path,
):
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=[{"id": 1}, {"id": 2}]
  ):
    first = changes.list_change_events("123", limit=1)
  args = first["bulk_export_call"]["arguments"]
  with pytest.raises(ToolError, match="retention overrides"):
    api.export_gaql_csv(**args, retention_policy="clamp")
  with mock.patch.object(
      api, "get_ads_credential_cache_scope", return_value="different-principal"
  ):
    with pytest.raises(ToolError, match="credentials"):
      api.export_gaql_csv(**args, output_path=str(tmp_path / "denied.csv"))


def test_expired_history_snapshot_does_not_recompute_dates_or_query():
  clock = [100.0]
  with (
      mock.patch.object(api.time, "monotonic", side_effect=lambda: clock[0]),
      mock.patch.object(
          api, "_iter_gaql_query_attempt", return_value=[{"id": 1}, {"id": 2}]
      ) as run,
  ):
    first = changes.list_change_events("123", limit=1)
    clock[0] += 91.0
    with mock.patch.object(
        changes,
        "_account_today",
        side_effect=AssertionError("expired snapshots must not refresh dates"),
    ):
      with pytest.raises(ToolError, match="expired"):
        changes.list_change_events(**first["continuation"]["arguments"])
      with pytest.raises(ToolError, match="expired"):
        api.export_gaql_csv(**first["bulk_export_call"]["arguments"])
  run.assert_called_once()


@pytest.mark.parametrize("value", [None, True, "silent", "CLAMP"])
def test_invalid_policy_rejected_without_api_io(value):
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="retention_policy"):
      api.execute_gaql(
          "SELECT campaign.id FROM campaign", "123", retention_policy=value
      )
  run.assert_not_called()


def test_raw_source_limit_is_not_reported_as_complete_history():
  with mock.patch.object(api, "run_gaql_query", return_value=[{"id": 1}]):
    result = api.execute_gaql(
        history_query(f"{FIELD} DURING LAST_7_DAYS", "LIMIT 1"), "123"
    )
  assert result["source_complete"] is False
  assert result["requested_range_complete"] is False
  assert result["result_limit_reached"] is True
  assert result["warnings"]
