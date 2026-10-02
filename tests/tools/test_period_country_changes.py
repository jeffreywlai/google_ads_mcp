# Copyright 2026 Google LLC
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

"""Offline daily country and retained-change workflow regressions."""

import csv
from datetime import date
import json
from pathlib import Path
import re
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
import pytest
import yaml

from ads_mcp.tools import _history
from ads_mcp.tools import api
from ads_mcp.tools import reporting


CUSTOMER = "1234567890"
CAMPAIGN = f"customers/{CUSTOMER}/campaigns/111"
BUDGET = f"customers/{CUSTOMER}/campaignBudgets/222"
TODAY = date(2026, 9, 20)
ZONE = "America/New_York"
COUNTRY_FIELD = "user_location_view.country_criterion_id"
PERIODS = [
    {"label": "all", "start_date": "2026-09-01", "end_date": "2026-09-05"}
]
FIELD_METADATA = yaml.safe_load(
    (
        Path(reporting.__file__).parents[1] / "context" / "fields.yaml"
    ).read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def _mock_credential_scope():
  with mock.patch.object(
      api,
      "get_ads_credential_cache_scope",
      return_value="offline-country-changes",
  ):
    yield


def _validate_query(query):
  """Exercises actual GAQL preflight and installed v25 message fields."""
  api.preprocess_gaql(query)
  fields = re.search(r"SELECT (.*?) FROM", query, re.I | re.S)[1].split(",")
  for field in fields:
    descriptor = GoogleAdsRow.pb().DESCRIPTOR
    for component in field.strip().split("."):
      descriptor_field = descriptor.fields_by_name.get(component)
      if descriptor_field is None:
        descriptor_field = descriptor.fields_by_name[component + "_"]
      descriptor = descriptor_field.message_type
  if "FROM change_event" in query:
    prepared, metadata = _history.prepare_change_event_query(
        query, TODAY, ZONE, "error"
    )
    assert prepared == query
    assert metadata["query_result_limit"] == 10000
  where = re.search(r"WHERE (.*?)(?: ORDER BY|$)", query, re.I | re.S)
  if where:
    for field in re.findall(r"\b[a-z_]+\.[a-z_]+(?:\.[a-z_]+)*\b", where[1]):
      assert FIELD_METADATA[field]["filterable"], field


def _row(day, cost=1_000_000, value=3, country=None, targeting=True):
  row = {
      "campaign.id": 111,
      "segments.date": f"2026-09-{day:02d}",
      "metrics.cost_micros": cost,
      "metrics.impressions": 100,
      "metrics.clicks": 10,
      "metrics.conversions": 1.5,
      "metrics.conversions_value": value,
  }
  if country is not None:
    row[COUNTRY_FIELD] = country
    row["user_location_view.targeting_location"] = targeting
  return row


def _event(
    day, fields, resource_type="CAMPAIGN", associated=True, time="12:34:56"
):
  return {
      "change_event.resource_name": f"customers/{CUSTOMER}/changeEvents/{day}",
      "change_event.change_date_time": f"2026-09-{day:02d} {time}",
      "change_event.change_resource_name": BUDGET
      if resource_type == "CAMPAIGN_BUDGET"
      else CAMPAIGN,
      "change_event.change_resource_type": resource_type,
      "change_event.campaign": CAMPAIGN if associated else "",
      "change_event.resource_change_operation": "UPDATE",
      "change_event.changed_fields": {"paths": fields},
      "change_event.old_resource": {
          "campaign": {"targetRoas": {"targetRoas": 2}}
      },
      "change_event.new_resource": {
          "campaign": {"targetRoas": {"targetRoas": 3}}
      },
  }


def _metadata(query, customer_id, login_customer_id):
  _validate_query(query)
  assert customer_id == CUSTOMER
  assert login_customer_id == "456"
  if "FROM customer" in query:
    return [{"customer.time_zone": ZONE, "customer.currency_code": "USD"}]
  if "FROM campaign " in query:
    return [
        {
            "campaign.id": 111,
            "campaign.name": "Search",
            "campaign.status": "ENABLED",
            "campaign.resource_name": CAMPAIGN,
            "campaign.campaign_budget": BUDGET,
            "campaign.bidding_strategy_type": "TARGET_ROAS",
            "campaign_budget.explicitly_shared": True,
        }
    ]
  assert "FROM geo_target_constant" in query
  assert re.search(r"geo_target_constant\.id IN \(\d+(?:, \d+)*\)", query)
  return [
      {
          "geo_target_constant.id": 2840,
          "geo_target_constant.resource_name": "geoTargetConstants/2840",
          "geo_target_constant.name": "United States",
          "geo_target_constant.canonical_name": "United States",
          "geo_target_constant.country_code": "US",
          "geo_target_constant.target_type": "Country",
      }
  ]


def _invoke(
    rows, campaign_events=(), budget_events=(), country=False, around=False
):
  queries = []

  def snapshot(query, customer_id, login_customer_id):
    _validate_query(query)
    queries.append(query)
    assert customer_id == CUSTOMER
    assert login_customer_id == "456"
    if "FROM change_event" in query:
      source_rows = (
          budget_events if "= CAMPAIGN_BUDGET" in query else campaign_events
      )
    else:
      source_rows = rows
      assert " LIMIT " not in query
      if country:
        assert "FROM user_location_view" in query
        assert "user_location_view.targeting_location" in query
      else:
        assert "FROM campaign " in query
    return {
        "rows": list(source_rows),
        "snapshot_token": "gaql-snapshot-v1:" + "a" * 32,
    }

  with (
      mock.patch.object(reporting, "run_gaql_query", side_effect=_metadata),
      mock.patch.object(
          reporting, "run_gaql_query_snapshot", side_effect=snapshot
      ),
      mock.patch.object(
          reporting, "get_account_calendar", return_value=(TODAY, ZONE)
      ),
  ):
    if around:
      result = reporting.compare_performance_around_changes(
          "123-456-7890",
          "111",
          "2026-09-01",
          "2026-09-05",
          segment_by="COUNTRY" if country else None,
          login_customer_id="456",
      )
    else:
      result = reporting.compare_performance_periods(
          "123-456-7890",
          "111",
          PERIODS,
          segment_by="COUNTRY" if country else None,
          login_customer_id="456",
      )
  return result, queries


def test_country_aggregates_physical_location_buckets_and_resolves_names():
  result, _ = _invoke(
      [
          _row(1, country=2840, targeting=True),
          _row(1, cost=3_000_000, value=5, country=2840, targeting=False),
          _row(2, cost=0, value=0, country=0),
          _row(3, country=2124),
      ],
      country=True,
  )
  period = result["periods"][0]
  segments = {item["segment"]: item for item in period["segments"]}
  assert period["cost_micros"] == 5_000_000
  assert period["roas"] == 11 / 5
  assert segments["2840"]["cost_micros"] == 4_000_000
  assert segments["2840"]["roas"] == 2
  assert (
      segments["2840"]["country"]["geo_target_constant.name"]
      == "United States"
  )
  assert segments["0"]["country"] is None
  assert segments["0"]["roas"] is None
  assert segments["2124"]["country"] is None
  assert "differ from campaign" in result["country_coverage_note"]
  assert result["captured_source_total"] == result["requested_periods_total"]


def test_change_windows_preserve_mixed_days_and_unproven_budget():
  result, queries = _invoke(
      [_row(day) for day in range(1, 6)],
      campaign_events=[
          _event(3, ["target_roas.target_roas"]),
          _event(2, ["name"]),
      ],
      budget_events=[
          _event(4, ["amount_micros"], "CAMPAIGN_BUDGET", associated=False),
          _event(5, ["amount_micros"], "CAMPAIGN_BUDGET", time="00:00:00"),
      ],
      around=True,
  )
  assert [item["start_date"] for item in result["periods"]] == [
      "2026-09-01",
      "2026-09-04",
  ]
  assert [item["end_date"] for item in result["periods"]] == [
      "2026-09-02",
      "2026-09-04",
  ]
  assert result["excluded_boundary_dates"] == ["2026-09-03", "2026-09-05"]
  assert result["requested_periods_total"]["cost_micros"] == 3_000_000
  assert result["excluded_boundary_total"]["cost_micros"] == 2_000_000
  assert result["captured_source_total"]["cost_micros"] == 5_000_000
  assert result["excluded_boundary_row_count"] == 2
  assert result["boundary_event_count"] == 2
  assert result["unproven_budget_event_count"] == 1
  assert (
      result["current_link_only_budget_evidence"][0]["association_evidence"]
      == "current_link_only"
  )
  assert (
      result["change_evidence"][0]["change_event.change_date_time"]
      == "2026-09-03 12:34:56"
  )
  assert result["campaign"]["campaign_budget.explicitly_shared"] is True
  budget_query = next(
      query for query in queries if "= CAMPAIGN_BUDGET" in query
  )
  assert "change_event.change_resource_name =" not in budget_query
  assert "change_event.change_resource_type = CAMPAIGN_BUDGET" in budget_query
  assert "change_event.campaign =" not in budget_query
  assert len(result["change_source_export_calls"]) == 2


def test_all_dates_excluded_still_returns_full_source_and_empty_zero_periods():
  result, _ = _invoke(
      [_row(day) for day in range(1, 6)],
      campaign_events=[
          _event(day, ["target_cpa.target_cpa_micros"]) for day in range(1, 6)
      ],
      around=True,
  )
  assert result["periods"] == []
  assert result["requested_periods_total"]["cost_micros"] == 0
  assert result["requested_periods_total"]["roas"] is None
  assert result["excluded_boundary_total"] == result["captured_source_total"]


def test_no_supported_events_does_not_invent_switches():
  result, _ = _invoke(
      [_row(1), _row(2, value=0)],
      campaign_events=[_event(2, ["name"])],
      around=True,
  )
  assert len(result["periods"]) == 1
  assert result["change_evidence"] == []
  assert result["boundary_event_count"] == 0
  assert "does not prove unchanged" in result["coverage_note"]


def test_country_change_comparison_uses_same_full_source_reconciliation():
  result, _ = _invoke(
      [_row(day, country=2840) for day in range(1, 6)],
      campaign_events=[_event(3, ["maximize_conversion_value.target_roas"])],
      country=True,
      around=True,
  )
  assert result["performance_source"] == "user_location_view"
  assert result["excluded_boundary_total"]["cost_micros"] == 1_000_000
  assert result["requested_periods_total"]["cost_micros"] == 4_000_000


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-08-01", "2026-09-01"),
        ("2026-09-01", "2026-09-20"),
        ("2026-09-05", "2026-09-01"),
        ("2026-09-01 12:00:00", "2026-09-05"),
    ],
)
def test_unretained_or_partial_day_dates_are_rejected(start, end):
  with (
      mock.patch.object(
          reporting, "get_account_calendar", return_value=(TODAY, ZONE)
      ),
      mock.patch.object(reporting, "run_gaql_query_snapshot") as run,
      pytest.raises(ToolError),
  ):
    reporting.compare_performance_around_changes(CUSTOMER, "111", start, end)
  run.assert_not_called()


def test_capped_evidence_prevents_partial_derived_comparison():
  result, queries = _invoke(
      [],
      campaign_events=[_event(3, ["target_roas.target_roas"])] * 10000,
      around=True,
  )
  assert result["comparison_available"] is False
  assert result["analysis_complete"] is False
  assert result["change_source_complete"] is False
  assert result["captured_change_row_count"] == 10000
  assert not any("FROM campaign WHERE" in query for query in queries)


@pytest.mark.parametrize(
    "field,value",
    [
        ("change_event.change_resource_name", "customers/999/campaigns/111"),
        ("change_event.change_date_time", "2026-08-01 12:00:00"),
        ("change_event.change_date_time", "2026-09-03"),
        ("change_event.change_date_time", "2026-09-03 12:00:00+00:00"),
        ("change_event.change_resource_type", "AD_GROUP"),
        ("change_event.changed_fields", {"paths": [None]}),
    ],
)
def test_invalid_change_source_cannot_derive_evidence(field, value):
  row = _event(3, ["target_roas.target_roas"])
  row[field] = value
  with pytest.raises(ToolError):
    _invoke([], campaign_events=[row], around=True)


def test_geographic_name_map_keeps_source_page_and_cursor():
  rows = [
      {"campaign.id": 111, COUNTRY_FIELD: 2840},
      {"campaign.id": 111, COUNTRY_FIELD: 0},
  ]
  page = {
      "rows": rows,
      "total_results_count": 4,
      "next_page_token": "next",
      "snapshot_token": "gaql-snapshot-v1:" + "a" * 32,
  }
  with (
      mock.patch.object(reporting, "run_gaql_query", side_effect=_metadata),
      mock.patch.object(reporting, "run_gaql_query_page", return_value=page),
  ):
    result = reporting.list_geographic_performance(
        CUSTOMER, login_customer_id="456"
    )
  assert result["geographic_performance"] == rows
  assert result["next_page_token"] == "next"
  assert result["returned_count"] == 2
  assert (
      result["resolved_countries"]["2840"]["geo_target_constant.name"]
      == "United States"
  )
  assert result["resolved_countries"]["0"] is None


def test_country_full_totals_survive_bounded_preview_with_exact_derived_export(
    tmp_path,
):
  rows = [_row(1, country=country) for country in range(1, 501)]
  result, _ = _invoke(rows, country=True)
  assert result["source_row_count"] == 500
  assert result["requested_periods_total"]["cost_micros"] == 500_000_000
  assert len(json.dumps(result, ensure_ascii=False).encode()) <= 32768
  export = result["full_materialized_response_export"]["export_call"]
  artifact = api.export_materialized_response_csv(
      **export["arguments"], output_path=str(tmp_path / "country.csv")
  )
  with Path(artifact["file_path"]).open(encoding="utf-8") as source:
    exported = list(csv.DictReader(source))
  period = json.loads(
      next(
          row["result"] for row in exported if row["result_type"] == "periods"
      )
  )
  assert len(period["segments"]) == 500
  assert period["cost_micros"] == 500_000_000


def test_budget_scope_retains_direct_history_and_ignores_unrelated_resources():
  historic_budget = _event(2, ["amount_micros"], "CAMPAIGN_BUDGET")
  historic_budget["change_event.change_resource_name"] = (
      f"customers/{CUSTOMER}/campaignBudgets/333"
  )
  unrelated_budget = _event(
      4, ["amount_micros"], "CAMPAIGN_BUDGET", associated=False
  )
  unrelated_budget["change_event.change_resource_name"] = (
      f"customers/{CUSTOMER}/campaignBudgets/444"
  )
  result, _ = _invoke(
      [_row(day) for day in range(1, 6)],
      budget_events=[historic_budget, unrelated_budget],
      around=True,
  )
  assert result["excluded_boundary_dates"] == ["2026-09-02"]
  assert result["boundary_event_count"] == 1
  assert result["unproven_budget_event_count"] == 0
  assert result["captured_change_row_count"] == 2
  assert result["current_budget_resource"] == BUDGET


def test_change_large_evidence_is_bounded_and_exact_export_preserves_values(
    tmp_path,
):
  event = _event(3, ["target_roas.target_roas"])
  event["change_event.old_resource"]["large_exact_value"] = "x" * 40000
  result, _ = _invoke(
      [_row(day) for day in range(1, 6)], campaign_events=[event], around=True
  )
  assert len(json.dumps(result, ensure_ascii=False).encode()) <= 32768
  assert result["boundary_event_count"] == 1
  assert result["requested_periods_total"]["cost_micros"] == 4_000_000
  export = result["full_materialized_response_export"]["export_call"]
  artifact = api.export_materialized_response_csv(
      **export["arguments"], output_path=str(tmp_path / "evidence.csv")
  )
  with Path(artifact["file_path"]).open(encoding="utf-8") as source:
    exported = list(csv.DictReader(source))
  evidence = json.loads(
      next(
          row["result"]
          for row in exported
          if row["result_type"] == "change_evidence"
      )
  )
  assert (
      evidence["change_event.old_resource"]["large_exact_value"] == "x" * 40000
  )


def test_geographic_large_enrichment_exposes_page_omissions_and_exact_access(
    tmp_path,
):
  rows = [
      {
          "campaign.id": 111,
          COUNTRY_FIELD: country,
          "metrics.impressions": 1,
          "padding": "x" * 400,
      }
      for country in range(1, 61)
  ]
  page = {
      "rows": rows,
      "total_results_count": 90,
      "next_page_token": "next",
      "snapshot_token": "gaql-snapshot-v1:" + "a" * 32,
  }

  def names(query, customer_id, login_customer_id):
    _validate_query(query)
    assert customer_id == CUSTOMER
    assert login_customer_id == "456"
    return [
        {
            "geo_target_constant.id": country,
            "geo_target_constant.name": "x" * 1000,
        }
        for country in range(1, 61)
    ]

  with (
      mock.patch.object(reporting, "run_gaql_query", side_effect=names),
      mock.patch.object(reporting, "run_gaql_query_page", return_value=page),
  ):
    result = reporting.list_geographic_performance(
        CUSTOMER, login_customer_id="456"
    )
  assert len(json.dumps(result, ensure_ascii=False).encode()) <= 49152
  assert result["represented_row_count"] == 60
  assert result["returned_count"] == len(result["geographic_performance"])
  assert result["inline_omitted_row_count"] == 60 - result["returned_count"]
  assert result["complete_inline"] is False
  assert result["next_page_token"] == "next"
  assert (
      result["bulk_export_call"]["arguments"]["snapshot_token"]
      == page["snapshot_token"]
  )
  export = result["full_materialized_response_export"]["export_call"]
  artifact = api.export_materialized_response_csv(
      **export["arguments"], output_path=str(tmp_path / "geography.csv")
  )
  with Path(artifact["file_path"]).open(encoding="utf-8") as source:
    exported = list(csv.DictReader(source))
  assert (
      sum(row["result_type"] == "geographic_performance" for row in exported)
      == 60
  )
  assert (
      sum(row["result_type"] == "resolved_countries" for row in exported) == 60
  )
