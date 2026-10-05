"""Malformed history requests must fail before credentials or network access."""

from datetime import date
from unittest import mock

from fastmcp.exceptions import ToolError
import pytest

from ads_mcp.tools import api

_FIELD = "change_event.change_date_time"
_VALID_PREDICATE = f"{_FIELD} DURING LAST_7_DAYS"


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
@pytest.mark.parametrize(
    "fields,predicate,limit,diagnostic",
    [
        ("change_event.resource_name", _VALID_PREDICATE, "", "LIMIT"),
        ("change_event.resource_name", _VALID_PREDICATE, "LIMIT 0", "LIMIT"),
        (
            "change_event.resource_name",
            _VALID_PREDICATE,
            "LIMIT 10001",
            "LIMIT",
        ),
        (
            "change_event.resource_name",
            f"{_FIELD} >= '2026-08-01'",
            "LIMIT 100",
            "interval",
        ),
        (
            "change_event.resource_name",
            f"{_FIELD} BETWEEN '2026-08-13' AND '2026-08-01'",
            "LIMIT 100",
            "on or before",
        ),
        (
            "change_event.resource_name",
            f"{_FIELD} BETWEEN '2026-02-30' AND '2026-08-01'",
            "LIMIT 100",
            "Invalid change_event date",
        ),
        (
            "change_event.resource_name",
            f"{_FIELD} DURING ALL_TIME",
            "LIMIT 100",
            "interval",
        ),
        (
            "change_event.resource_name",
            f"{_FIELD} DURING UNKNOWN_RANGE",
            "LIMIT 100",
            "date_range",
        ),
        (
            "change_event.resource_name",
            f"{_FIELD} DURING LAST_0_DAYS",
            "LIMIT 100",
            "greater than 0",
        ),
        (
            "change_event.resource_name",
            f"{_FIELD} DURING LAST_3651_DAYS",
            "LIMIT 100",
            "3650 days or fewer",
        ),
        (
            "change_event.resource_name",
            _VALID_PREDICATE
            + " AND change_event.change_resource_type = WRONG",
            "LIMIT 100",
            "enum",
        ),
        ("metrics.clicks", _VALID_PREDICATE, "LIMIT 100", "compatible"),
        (
            "SUM(metrics.clicks)",
            _VALID_PREDICATE,
            "LIMIT 100",
            "aggregate",
        ),
    ],
)
def test_history_validation_precedes_account_lookup(
    tool_name, fields, predicate, limit, diagnostic, tmp_path
):
  query = f"SELECT {fields} FROM change_event WHERE {predicate} {limit}"
  kwargs = (
      {"output_path": str(tmp_path / "invalid.csv")}
      if tool_name == "export_gaql_csv"
      else {}
  )
  with (
      mock.patch.object(
          api, "get_account_calendar", side_effect=AssertionError("calendar")
      ) as calendar,
      mock.patch.object(
          api,
          "get_ads_credential_cache_scope",
          side_effect=AssertionError("credentials"),
      ) as credentials,
      mock.patch.object(api, "get_ads_client") as client,
      mock.patch.object(api, "run_gaql_query") as run,
  ):
    with pytest.raises(ToolError, match=diagnostic):
      getattr(api, tool_name)(query, "123", **kwargs)
  calendar.assert_not_called()
  credentials.assert_not_called()
  client.assert_not_called()
  run.assert_not_called()
  assert not (tmp_path / "invalid.csv").exists()


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
@pytest.mark.parametrize("literal", ["LAST_7_DAYS", "LAST 35 DAYS"])
def test_valid_history_resolves_only_the_account_calendar(
    tool_name, literal, tmp_path
):
  query = (
      "SELECT change_event.resource_name FROM change_event "
      f"WHERE {_FIELD} DURING {literal} LIMIT 100"
  )
  kwargs = (
      {"output_path": str(tmp_path / "valid.csv")}
      if tool_name == "export_gaql_csv"
      else {}
  )
  with (
      mock.patch.object(
          api,
          "get_account_calendar",
          return_value=(date(2026, 8, 13), "America/New_York"),
      ) as calendar,
      mock.patch.object(api, "run_gaql_query", return_value=[]) as run,
  ):
    result = getattr(api, tool_name)(
        query, "123", retention_policy="clamp", **kwargs
    )
  calendar.assert_called_once_with("123", None)
  run.assert_called_once()
  assert result["account_today"] == "2026-08-13"
  assert result["account_time_zone"] == "America/New_York"
  assert result["original_query"] == query
  assert "DURING" not in result["executed_query"]
  assert "2026-08-13 00:00:00" in result["executed_query"]
