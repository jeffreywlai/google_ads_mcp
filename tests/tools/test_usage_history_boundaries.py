"""Preserve accepted next-midnight predicates without permitting future days."""

from datetime import date

from fastmcp.exceptions import ToolError
import pytest

from ads_mcp.tools import _history


def _query(start, end):
  return (
      "SELECT change_event.resource_name FROM change_event WHERE "
      f"change_event.change_date_time >= '{start}' AND "
      f"change_event.change_date_time <= '{end}' LIMIT 100"
  )


def test_next_midnight_inclusive_endpoint_preserves_original_query():
  query = _query("2026-08-11", "2026-08-14")
  prepared, metadata = _history.prepare_change_event_query(
      query, date(2026, 8, 13), "America/New_York", "error"
  )
  assert prepared == query
  assert metadata["retention"]["clamped"] is False
  assert metadata["retention"]["unavailable_ranges"] == []
  assert metadata["retention"]["applied_range"]["end_inclusive"] is True


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-08-11", "2026-08-14 00:00:00.000001"),
        ("2026-08-11", "2026-08-14 23:59:59"),
        ("2026-08-14 00:00:00.000001", "2026-08-15"),
        ("2026-07-14 23:59:59.999999", "2026-08-14"),
    ],
)
def test_real_future_or_old_history_still_rejected(start, end):
  with pytest.raises(ToolError, match="retention_policy"):
    _history.prepare_change_event_query(
        _query(start, end), date(2026, 8, 13), "America/New_York", "error"
    )
