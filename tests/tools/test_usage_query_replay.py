"""Anonymous recorded queries lock validation compatibility, not API data."""

# pylint: disable=protected-access

from datetime import datetime
import json
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastmcp.exceptions import ToolError
import pytest

from ads_mcp.tools import api


_CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/usage_report_gaql.json").read_text(
        encoding="utf-8"
    )
)

# Preserve the historical v24 corpus. These resources were removed in v25.
_REMOVED_GOAL_QUERIES = {"q018", "q019", "q022", "q027"}


def test_corpus_matches_report_counts():
  assert len(_CASES) == 122
  assert sum(not case["historical_error"] for case in _CASES) == 106
  assert sum(case["expected_local_error"] for case in _CASES) == 13


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["case"])
def test_recorded_gaql_preflight(case):
  zone = ZoneInfo(case["account_time_zone"])
  timestamp = datetime.fromisoformat(case["timestamp"].replace("Z", "+00:00"))
  today = timestamp.astimezone(zone).date()
  with (
      mock.patch.object(api, "get_ads_client") as client,
      mock.patch.object(
          api, "get_account_calendar", return_value=(today, zone.key)
      ),
  ):
    if case["expected_local_error"] or case["case"] in _REMOVED_GOAL_QUERIES:
      with pytest.raises(ToolError) as caught:
        api._prepare_public_gaql(case["query"], "123", None, "error")
      if case["case"] in _REMOVED_GOAL_QUERIES:
        assert "removed in Google Ads API v25" in str(caught.value)
        assert "unified goal fields" in str(caught.value)
    else:
      prepared, metadata = api._prepare_public_gaql(
          case["query"], "123", None, "error"
      )
      assert prepared is not None
      if not case["historical_error"]:
        assert not metadata.get("retention", {}).get("clamped", False)
      if case["case"] == "q043":
        assert "campaign.id" in prepared.split("FROM", 1)[0]
        assert metadata["query_adjustments"]
  client.assert_not_called()
