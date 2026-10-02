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

"""Offline regressions for complete settings and explicit period workflows."""

import csv
import json
from pathlib import Path
import re
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
import pytest
import yaml

from ads_mcp.tools import api
from ads_mcp.tools import reporting
from ads_mcp.tools._gaql import preprocess_gaql_query


CUSTOMER_ID = "1234567890"
CAMPAIGN_RESOURCE = f"customers/{CUSTOMER_ID}/campaigns/111"
CUSTOM_GOAL = "customers/999/customConversionGoals/10"
CONVERSION_OWNER_FIELD = (
    "customer.conversion_tracking_setting.google_ads_conversion_customer"
)
LOCATION_FIELD = "campaign_criterion.location.geo_target_constant"
CAMPAIGN_GOAL_MODE = (
    "campaign_goal_config.campaign_new_customer_acquisition_settings."
    "target_option"
)
CAMPAIGN_GOAL_VALUE = (
    "campaign_goal_config.campaign_new_customer_acquisition_settings."
    "value_settings_override.additional_value"
)
ACCOUNT_GOAL_VALUE = (
    "goal.new_customer_acquisition_goal_settings.value_settings."
    "additional_value"
)
CUSTOM_GOAL_FIELD = "conversion_goal_campaign_config.custom_conversion_goal"
ACCOUNT = {
    "customer.id": 1234567890,
    "customer.time_zone": "America/New_York",
    "customer.currency_code": "USD",
    CONVERSION_OWNER_FIELD: "customers/999",
}
WINDOWS = [
    {"label": "before", "start_date": "2026-08-01", "end_date": "2026-08-02"},
    {"label": "after", "start_date": "2026-08-05", "end_date": "2026-08-06"},
]


def _campaign(campaign_id="111"):
  resource = f"customers/{CUSTOMER_ID}/campaigns/{campaign_id}"
  return {
      "campaign.resource_name": resource,
      "campaign.id": int(campaign_id),
      "campaign.name": "Search" if campaign_id == "111" else "PMax",
      "campaign.status": "ENABLED",
      "campaign.advertising_channel_type": "SEARCH"
      if campaign_id == "111"
      else "PERFORMANCE_MAX",
      "campaign.bidding_strategy_type": "TARGET_ROAS",
      "campaign.target_roas.target_roas": 2.5,
      "campaign.start_date_time": "2026-01-01 00:00:00",
      "campaign.end_date_time": "",
      "campaign.ai_max_setting.enable_ai_max": False,
      "campaign.geo_target_type_setting.positive_geo_target_type": "PRESENCE",
      "campaign_budget.amount_micros": 10_000_000,
      "campaign_budget.total_amount_micros": 0,
      "campaign_budget.period": "DAILY",
      "campaign_budget.explicitly_shared": True,
  }


def _settings_data():
  portfolio = f"customers/{CUSTOMER_ID}/accessibleBiddingStrategies/9"
  return {
      "customer": [ACCOUNT],
      "campaign": [
          _campaign(),
          {
              **_campaign("222"),
              "campaign.accessible_bidding_strategy": portfolio,
          },
      ],
      "conversion_goal_campaign_config": [
          {
              "campaign.id": 111,
              "conversion_goal_campaign_config.goal_config_level": "CAMPAIGN",
              CUSTOM_GOAL_FIELD: CUSTOM_GOAL,
          },
          {
              "campaign.id": 222,
              "conversion_goal_campaign_config.goal_config_level": "CUSTOMER",
          },
      ],
      "campaign_conversion_goal": [
          {
              "campaign.id": 111,
              "campaign_conversion_goal.category": "PURCHASE",
              "campaign_conversion_goal.origin": "WEBSITE",
              "campaign_conversion_goal.biddable": True,
          },
      ],
      "custom_conversion_goal": [
          {
              "custom_conversion_goal.resource_name": CUSTOM_GOAL,
              "custom_conversion_goal.name": "Purchase",
              "custom_conversion_goal.conversion_actions": [
                  "customers/999/conversionActions/15"
              ],
          },
      ],
      "campaign_criterion": [
          {
              "campaign.id": 111,
              LOCATION_FIELD: "geoTargetConstants/2840",
              "campaign_criterion.negative": False,
          },
          {
              "campaign.id": 222,
              LOCATION_FIELD: "geoTargetConstants/2124",
              "campaign_criterion.negative": True,
          },
      ],
      "geo_target_constant": [
          {
              "geo_target_constant.resource_name": "geoTargetConstants/2840",
              "geo_target_constant.name": "United States",
          },
      ],
      "campaign_shared_set": [
          {
              "campaign.id": 222,
              "shared_set.name": "Brand negatives",
              "shared_set.type": "NEGATIVE_KEYWORDS",
          },
      ],
      "campaign_goal_config": [
          {
              "campaign_goal_config.campaign": CAMPAIGN_RESOURCE,
              "campaign_goal_config.goal": "customers/999/goals/10",
              "campaign_goal_config.goal_type": "NEW_CUSTOMER_ACQUISITION",
              CAMPAIGN_GOAL_MODE: "TARGET_ALL",
              CAMPAIGN_GOAL_VALUE: 0,
          },
      ],
      "goal": [
          {
              "goal.resource_name": "customers/999/goals/10",
              "goal.owner_customer": "customers/999",
              "goal.goal_type": "NEW_CUSTOMER_ACQUISITION",
              ACCOUNT_GOAL_VALUE: 120,
          },
      ],
      "accessible_bidding_strategy": [
          {
              "accessible_bidding_strategy.resource_name": portfolio,
              "accessible_bidding_strategy.target_roas.target_roas": 4,
          },
      ],
  }


def _validate_read_query(query):
  """Checks syntax, local compatibility, and installed v25 field paths."""
  preprocess_gaql_query(query)
  selected = re.search(r"SELECT\s+(.+?)\s+FROM", query, re.DOTALL).group(1)
  for field in selected.split(","):
    descriptor = GoogleAdsRow.pb().DESCRIPTOR
    for part in field.strip().split("."):
      # The Python SDK names reserved attributes such as type as type_.
      python_part = part if part in descriptor.fields_by_name else part + "_"
      field_descriptor = descriptor.fields_by_name[python_part]
      descriptor = field_descriptor.message_type


def _query_callback(data):
  def _run(query, customer_id, login_customer_id):
    del customer_id, login_customer_id
    _validate_read_query(query)
    resource = re.search(r"\bFROM\s+(\w+)", query).group(1)
    return data[resource]

  return _run


def test_settings_joins_v25_sources_and_conversion_owner():
  with mock.patch.object(
      reporting,
      "run_gaql_query",
      side_effect=_query_callback(_settings_data()),
  ) as run:
    result = reporting.get_campaign_settings(
        "123-456-7890", '["111", "222", "333", "111"]', "456"
    )
  assert result["requested_campaign_ids"] == ["111", "222", "333"]
  assert result["missing_campaign_ids"] == ["333"]
  assert result["snapshot_complete"] is True
  search, pmax = result["campaigns"]
  assert search["campaign.start_date_time"] == "2026-01-01 00:00:00"
  assert search["campaign.ai_max_setting.enable_ai_max"] is False
  assert search["campaign_budget.explicitly_shared"] is True
  assert (
      search["conversion_goals"]["custom"]["custom_conversion_goal.name"]
      == "Purchase"
  )
  assert (
      search["location_targets"][0]["resolved_location"][
          "geo_target_constant.name"
      ]
      == "United States"
  )
  assert pmax["location_targets"][0]["resolved_location"] is None
  assert pmax["location_targets"][0]["campaign_criterion.negative"] is True
  assert search["campaign_goal_configs"][0][CAMPAIGN_GOAL_VALUE] == 0
  assert pmax["campaign_goal_configs"] == []
  assert result["account_goals"][0][ACCOUNT_GOAL_VALUE] == 120
  assert result["goal_owner_customer_id"] == "999"
  assert "campaign_lifecycle_goals" not in search
  assert "customer_lifecycle_goals" not in search
  assert (
      pmax["portfolio_bidding_strategy"][
          "accessible_bidding_strategy.target_roas.target_roas"
      ]
      == 4
  )
  assert pmax["shared_sets"][0]["shared_set.name"] == "Brand negatives"
  fields_path = Path(reporting.__file__).parents[1] / "context" / "fields.yaml"
  metadata = yaml.safe_load(fields_path.read_text(encoding="utf-8"))
  for call in run.call_args_list:
    query, customer, manager = call.args
    assert " LIMIT " not in query.upper()
    assert manager == "456"
    if "FROM custom_conversion_goal" in query or "FROM goal" in query:
      assert customer == "999"
    else:
      assert customer == CUSTOMER_ID
    fields = query.split("SELECT ", 1)[1].split(" FROM ", 1)[0].split(", ")
    assert all(field in metadata for field in fields)
    if "FROM campaign_goal_config" in query:
      assert "campaign_goal_config.campaign IN" in query
      assert "campaign.id" not in query


@pytest.mark.parametrize(
    "campaign_ids", [[], "[]", [True], ["0"], ["-1"], ["1 OR 1=1"]]
)
def test_settings_rejects_bad_or_unscoped_ids_before_network(campaign_ids):
  with mock.patch.object(reporting, "run_gaql_query") as run:
    with pytest.raises(ToolError):
      reporting.get_campaign_settings(CUSTOMER_ID, campaign_ids)
  run.assert_not_called()


def test_settings_missing_campaigns_do_not_invent_goals():
  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=[[ACCOUNT], []]
  ) as run:
    result = reporting.get_campaign_settings(CUSTOMER_ID, ["111"])
  assert result["campaigns"] == []
  assert result["missing_campaign_ids"] == ["111"]
  assert run.call_count == 2


def test_settings_api_failure_is_not_reported_as_empty_settings():
  def _run(query, customer, manager):
    if "FROM campaign_goal_config" in query:
      raise ToolError("denied")
    return _query_callback(_settings_data())(query, customer, manager)

  with mock.patch.object(reporting, "run_gaql_query", side_effect=_run):
    with pytest.raises(ToolError, match="denied"):
      reporting.get_campaign_settings(CUSTOMER_ID, ["111", "222"])


@pytest.mark.parametrize("location_count", [32, 150])
def test_settings_large_children_are_bounded_with_exact_logical_export(
    tmp_path, location_count
):
  data = _settings_data()
  data["campaign"] = [_campaign()]
  data["campaign_criterion"] = [
      {
          "campaign.id": 111,
          "campaign_criterion.criterion_id": index,
          LOCATION_FIELD: "geoTargetConstants/2840",
          "description": "x" * 1000,
      }
      for index in range(location_count)
  ]
  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=_query_callback(data)
  ):
    with mock.patch.object(
        api, "get_ads_credential_cache_scope", return_value="workflow-tests"
    ):
      with mock.patch.object(
          api,
          "_write_csv_rows",
          wraps=api._write_csv_rows,  # pylint: disable=protected-access
      ) as write_csv:
        result = reporting.get_campaign_settings(CUSTOMER_ID, ["111"])
        write_csv.assert_not_called()
        assert (
            len(
                json.dumps(
                    result, ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            <= 32768
        )
        export_call = result["full_materialized_response_export"][
            "export_call"
        ]
        with mock.patch.dict(
            "os.environ", {"GOOGLE_ADS_MCP_EXPORT_DIR": str(tmp_path)}
        ):
          artifact = api.export_materialized_response_csv(
              **export_call["arguments"],
              output_path=str(tmp_path / "settings.csv"),
          )
  previous_limit = csv.field_size_limit(1_000_000)
  try:
    with Path(artifact["file_path"]).open(
        encoding="utf-8", newline=""
    ) as exported:
      campaign_row = next(
          row
          for row in csv.DictReader(exported)
          if row["result_type"] == "campaigns"
      )
  finally:
    csv.field_size_limit(previous_limit)
  restored = json.loads(campaign_row["result"])
  assert len(restored["location_targets"]) == location_count
  if location_count == 32:
    assert 32768 < len(campaign_row["result"].encode()) < 49152
  assert artifact["truncated"] is False


def _performance_row(day, cost, conversions, value, device=None):
  row = {
      "campaign.id": 111,
      "segments.date": f"2026-08-{day:02d}",
      "metrics.cost_micros": cost,
      "metrics.impressions": 20,
      "metrics.clicks": 10,
      "metrics.conversions": conversions,
      "metrics.conversions_value": value,
  }
  if device is not None:
    row["segments.device"] = device
  return row


def _comparison(rows, periods=None, segment_by=None):
  periods = WINDOWS if periods is None else periods
  with mock.patch.object(
      reporting,
      "run_gaql_query",
      side_effect=_query_callback(
          {"customer": [ACCOUNT], "campaign": [_campaign()]}
      ),
  ):
    with mock.patch.object(reporting, "run_gaql_query_snapshot") as snapshot:

      def _source(query, customer, manager):
        _validate_read_query(query)
        assert customer == CUSTOMER_ID
        assert manager == "456"
        assert " OR " not in query
        assert "campaign.id = 111" in query
        assert "LIMIT" not in query
        return {"rows": rows, "snapshot_token": "source-exact"}

      snapshot.side_effect = _source
      result = reporting.compare_performance_periods(
          "123-456-7890", "111", periods, segment_by, "456"
      )
  return result


def test_comparison_uses_summed_ratios_and_excludes_gaps():
  result = _comparison(
      [
          _performance_row(1, 1_000_000, 1, 3),
          _performance_row(2, 9_000_000, 3, 7),
          _performance_row(3, 900_000_000, 900, 900),
          _performance_row(5, 2_000_000, 0, 0),
      ],
      periods=list(reversed(WINDOWS)),
  )
  before, after = result["periods"]
  assert before["label"] == "before"
  assert before["cost_micros"] == 10_000_000
  assert before["roas"] == 1
  assert before["cost_per_conversion"] == 2.5
  assert after["cost_per_conversion"] is None
  assert result["requested_periods_total"]["cost_micros"] == 12_000_000
  assert result["requested_periods_total"]["roas"] == pytest.approx(10 / 12)
  assert result["excluded_gap_row_count"] == 1
  assert result["source_row_count"] == 4
  assert result["analysis_complete"] is True
  assert result["account_time_zone"] == "America/New_York"
  assert all(
      period["boundary_evidence"] == "caller_supplied_dates"
      for period in result["periods"]
  )
  assert (
      result["bulk_export_call"]["arguments"]["snapshot_token"]
      == "source-exact"
  )


def test_comparison_device_breakdowns_add_to_totals_and_empty_periods():
  result = _comparison(
      [
          _performance_row(1, 1_000_000, 1, 3, "MOBILE"),
          _performance_row(1, 9_000_000, 3, 7, "DESKTOP"),
      ],
      segment_by="device",
  )
  before, after = result["periods"]
  assert before["cost_micros"] == sum(
      row["cost_micros"] for row in before["segments"]
  )
  assert before["conversions"] == sum(
      row["conversions"] for row in before["segments"]
  )
  assert after["cost_micros"] == 0
  assert after["source_row_count"] == 0
  assert after["roas"] is None
  assert after["segments"] == []


@pytest.mark.parametrize(
    "periods",
    [
        [],
        [{"start_date": "2026-08-02", "end_date": "2026-08-01"}],
        [{"start_date": "2026-08-01 12:00:00", "end_date": "2026-08-02"}],
        [WINDOWS[0], {"start_date": "2026-08-02", "end_date": "2026-08-03"}],
        [WINDOWS[0], {**WINDOWS[1], "label": "before"}],
        [{**WINDOWS[0], "label": ""}],
        [{**WINDOWS[0], "unexpected": True}],
    ],
)
def test_comparison_rejects_invalid_periods_before_network(periods):
  with mock.patch.object(reporting, "run_gaql_query") as run:
    with pytest.raises(ToolError):
      reporting.compare_performance_periods(CUSTOMER_ID, "111", periods)
  run.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("metrics.cost_micros", -1),
        ("metrics.cost_micros", 1.5),
        ("metrics.cost_micros", None),
        ("metrics.cost_micros", True),
        ("metrics.conversions", float("nan")),
        ("metrics.conversions_value", float("inf")),
        ("segments.date", "2026-08-99"),
        ("campaign.id", 222),
    ],
)
def test_comparison_rejects_invalid_source_data(field, value):
  row = _performance_row(1, 1_000_000, 1, 3)
  row[field] = value
  with pytest.raises(ToolError):
    _comparison([row])


def test_comparison_missing_campaign_is_not_zero_performance():
  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=[[ACCOUNT], []]
  ):
    with mock.patch.object(reporting, "run_gaql_query_snapshot") as snapshot:
      with pytest.raises(ToolError, match="No campaign"):
        reporting.compare_performance_periods(CUSTOMER_ID, "111", WINDOWS)
  snapshot.assert_not_called()


def test_comparison_materializes_all_periods_before_bounding():
  periods = [
      {
          "label": f"period-{index}" + "x" * 2000,
          "start_date": f"2026-08-{index:02d}",
          "end_date": f"2026-08-{index:02d}",
      }
      for index in range(1, 32)
  ]
  rows = [_performance_row(index, 1_000_000, 1, 3) for index in range(1, 32)]
  with mock.patch.object(
      api, "get_ads_credential_cache_scope", return_value="workflow-tests"
  ):
    result = _comparison(rows, periods)
    assert result["requested_periods_total"]["cost_micros"] == 31_000_000
    assert result["analysis_complete"] is True
    assert (
        len(
            json.dumps(
                result, ensure_ascii=False, separators=(",", ":")
            ).encode()
        )
        <= 32768
    )
    assert result["truncated"] is True
    token = result["full_materialized_response_export"]["export_call"][
        "arguments"
    ]["snapshot_token"]
    exported = api._get_materialized_snapshot_rows(token)  # pylint: disable=protected-access
    assert (
        len([row for row in exported if row["result_type"] == "periods"]) == 31
    )
