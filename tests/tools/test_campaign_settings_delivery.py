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

"""Campaign settings aliases, compact cores, and exact full-detail recovery."""

import asyncio
import csv
import json
from pathlib import Path
import re
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.tools import api
from ads_mcp.tools import reporting

CUSTOMER = "123"
LOCATION = "campaign_criterion.location.geo_target_constant"
GOAL_MODE = (
    "campaign_goal_config.campaign_new_customer_acquisition_settings."
    "target_option"
)
GOAL_VALUE = (
    "campaign_goal_config.campaign_new_customer_acquisition_settings."
    "value_settings_override.additional_value"
)


@pytest.fixture(autouse=True)
def offline_credentials():
  with mock.patch.object(
      api, "get_ads_credential_cache_scope", return_value="settings-delivery"
  ):
    yield


def _flatten(row, fields):
  return {
      field: api.format_value(api.get_nested_attr(row, field))
      for field in fields
  }


def _settings_data(campaign_count=1, location_count=4):
  """Uses real native scalar defaults plus normal variable-size children."""
  data = {
      "customer": [{"customer.id": 123, "customer.currency_code": "USD"}],
      "campaign": [],
      "conversion_goal_campaign_config": [],
      "campaign_conversion_goal": [],
      "campaign_criterion": [],
      "geo_target_constant": [],
      "campaign_shared_set": [],
      "campaign_goal_config": [],
      "goal": [],
  }
  for campaign_id in range(1, campaign_count + 1):
    resource = f"customers/{CUSTOMER}/campaigns/{campaign_id}"
    row = GoogleAdsRow(
        campaign={
            "id": campaign_id,
            "resource_name": resource,
            "name": f"Campaign {campaign_id}",
            "status": "ENABLED",
            "advertising_channel_type": "PERFORMANCE_MAX",
            "bidding_strategy_type": "MAXIMIZE_CONVERSION_VALUE",
            "maximize_conversion_value": {"target_roas": 4},
            "geo_target_type_setting": {
                "positive_geo_target_type": "PRESENCE",
                "negative_geo_target_type": "PRESENCE",
            },
            "start_date_time": "2026-10-01 00:00:00",
            "asset_automation_settings": [
                {
                    "asset_automation_type": "AUTOMATED_VIDEO_CRAWL",
                    "asset_automation_status": "OPTED_IN",
                }
            ],
        },
        campaign_budget={
            "id": campaign_id,
            "resource_name": f"customers/{CUSTOMER}/campaignBudgets/1",
            "name": "Daily",
            "amount_micros": 1_000_000,
            "period": "DAILY",
        },
    )
    data["campaign"].append(
        _flatten(row, reporting._CAMPAIGN_SETTINGS_FIELDS)  # pylint: disable=protected-access
    )
    data["conversion_goal_campaign_config"].append(
        {
            "campaign.id": campaign_id,
            "conversion_goal_campaign_config.goal_config_level": "CAMPAIGN",
            "conversion_goal_campaign_config.custom_conversion_goal": "",
        }
    )
    data["campaign_conversion_goal"].extend(
        {
            "campaign.id": campaign_id,
            "campaign_conversion_goal.category": f"CATEGORY_{index}",
            "campaign_conversion_goal.origin": "WEBSITE",
            "campaign_conversion_goal.biddable": index % 2 == 0,
        }
        for index in range(16)
    )
    data["campaign_criterion"].extend(
        {
            "campaign.id": campaign_id,
            "campaign_criterion.resource_name": (
                f"customers/{CUSTOMER}/campaignCriteria/{campaign_id}~{i}"
            ),
            "campaign_criterion.criterion_id": i,
            "campaign_criterion.type": "LOCATION",
            "campaign_criterion.status": "ENABLED",
            "campaign_criterion.negative": i % 2 == 0,
            LOCATION: f"geoTargetConstants/{i}",
        }
        for i in range(location_count)
    )
    for goal_id, (goal_type, settings) in enumerate(
        [
            (
                "NEW_CUSTOMER_ACQUISITION",
                {
                    "campaign_new_customer_acquisition_settings": {
                        "target_option": "TARGET_ALL",
                        "value_settings_override": {"additional_value": 0},
                    }
                },
            ),
            (
                "CUSTOMER_RETENTION",
                {
                    "campaign_retention_settings": {
                        "target_option": "TARGET_ALL"
                    }
                },
            ),
            (
                "LOYALTY_RETENTION",
                {
                    "campaign_loyalty_retention_settings": {
                        "enable_bid_adjustments_for_loyalty_members": False
                    }
                },
            ),
        ],
        1,
    ):
      config = GoogleAdsRow(
          campaign_goal_config={
              "resource_name": (
                  f"customers/{CUSTOMER}/campaignGoalConfigs/"
                  f"{campaign_id}~{goal_id}"
              ),
              "campaign": resource,
              "goal": f"customers/{CUSTOMER}/goals/{goal_id}",
              "goal_type": goal_type,
              **settings,
          }
      )
      data["campaign_goal_config"].append(
          _flatten(config, reporting._CAMPAIGN_GOAL_CONFIG_FIELDS)  # pylint: disable=protected-access
      )
  data["geo_target_constant"] = [
      {
          "geo_target_constant.resource_name": f"geoTargetConstants/{i}",
          "geo_target_constant.name": f"Location {i}",
          "geo_target_constant.canonical_name": f"Location {i}, Country",
          "geo_target_constant.country_code": "US",
          "geo_target_constant.target_type": "City",
      }
      for i in range(location_count)
  ]
  return data


def _read_settings(data):
  def _read(query, customer_id, login_customer_id):
    assert customer_id == CUSTOMER
    assert login_customer_id in (None, "456")
    assert " LIMIT " not in query.upper()
    return data[re.search(r"\bFROM\s+(\w+)", query)[1]]

  return _read


def _json_bytes(value):
  return len(
      json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
  )


def test_singular_alias_works_through_mcp_and_keeps_positional_manager():
  async def _call_alias():
    async with Client(mcp_server) as client:
      result = await client.call_tool(
          "get_campaign_settings",
          {"customer_id": CUSTOMER, "campaign_id": "1"},
      )
      return result.structured_content

  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=_read_settings(_settings_data())
  ):
    alias = asyncio.run(_call_alias())
    legacy = reporting.get_campaign_settings(CUSTOMER, ["1"], "456")
  assert (
      alias["requested_campaign_ids"]
      == legacy["requested_campaign_ids"]
      == ["1"]
  )
  assert alias["campaigns"] == legacy["campaigns"]


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({}, "Provide campaign_id or campaign_ids"),
        ({"campaign_id": "1", "campaign_ids": ["1"]}, "never both"),
        ({"campaign_id": "1", "campaign_ids": []}, "never both"),
        ({"campaign_id": "1", "campaign_ids": ["2"]}, "never both"),
        ({"campaign_id": ""}, "campaign_id"),
        ({"campaign_id": "1 OR 1=1"}, "campaign_id"),
        ({"campaign_ids": []}, "at least one"),
    ],
)
def test_alias_scope_errors_fail_before_network(kwargs, message):
  with mock.patch.object(reporting, "run_gaql_query") as read:
    with pytest.raises(ToolError, match=message):
      reporting.get_campaign_settings(CUSTOMER, **kwargs)
  read.assert_not_called()


@pytest.mark.parametrize(
    "campaign_count,location_count", [(5, 4), (1, 150), (3, 150)]
)
def test_oversized_settings_keep_every_core_and_exact_full_details(
    campaign_count, location_count, tmp_path, monkeypatch
):
  data = _settings_data(campaign_count, location_count)
  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=_read_settings(data)
  ) as read:
    with mock.patch.object(api, "_write_csv_rows") as write:
      result = reporting.get_campaign_settings(
          CUSTOMER, [str(i) for i in range(1, campaign_count + 1)]
      )
    write.assert_not_called()
    assert result["settings_view"] == "compact_core"
    assert result["core_settings_complete_inline"] is True
    assert result["full_settings_complete_inline"] is False
    assert result["snapshot_complete"] is True
    assert result["found_campaign_count"] == campaign_count
    assert result["missing_campaign_count"] == 0
    assert result["missing_campaign_ids"] == []
    assert result["returned_counts"]["campaigns"] == campaign_count
    assert result["shared_inline_delivery"]["omitted_counts"]["campaigns"] == 0
    assert _json_bytes(result) <= api.INLINE_PAGE_BYTE_LIMIT
    for core in result["campaigns"]:
      assert core["campaign.status"] == "ENABLED"
      assert (
          core["campaign.bidding_strategy_type"] == "MAXIMIZE_CONVERSION_VALUE"
      )
      assert core["campaign.maximize_conversion_value.target_roas"] == 4
      assert core["campaign_budget.period"] == "DAILY"
      assert core["campaign_budget.amount_micros"] == 1_000_000
      assert (
          core["campaign.geo_target_type_setting.positive_geo_target_type"]
          == "PRESENCE"
      )
      assert core["campaign.start_date_time"] == "2026-10-01 00:00:00"
      config = core["campaign_goal_configs"][0]
      assert config["goal_type"] == "NEW_CUSTOMER_ACQUISITION"
      assert (
          config["campaign_new_customer_acquisition_settings"]["target_option"]
          == "TARGET_ALL"
      )
      assert (
          config["campaign_new_customer_acquisition_settings"][
              "value_settings_override"
          ]["additional_value"]
          == 0
      )
      assert core["detail_counts"]["location_targets"] == location_count
      assert core["conversion_goals"]["standard_count"] == 16
    call_count = read.call_count
    monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
    call = result["full_materialized_response_export"]["export_call"]
    exported = api.export_materialized_response_csv(
        **call["arguments"], output_path=str(tmp_path / "settings.csv")
    )
    assert read.call_count == call_count
  old_limit = csv.field_size_limit(2_000_000)
  try:
    with Path(exported["file_path"]).open(
        encoding="utf-8", newline=""
    ) as stream:
      details = [
          json.loads(row["result"])
          for row in csv.DictReader(stream)
          if row["result_type"] == "campaign_details"
      ]
  finally:
    csv.field_size_limit(old_limit)
  assert len(details) == campaign_count
  for detail in details:
    assert len(detail["location_targets"]) == location_count
    assert len(detail["conversion_goals"]["standard"]) == 16
    assert detail["campaign_goal_configs"][0][GOAL_VALUE] == 0
    assert detail["campaign_goal_configs"][0][GOAL_MODE] == "TARGET_ALL"
    assert (
        detail["campaign.asset_automation_settings"][0][
            "assetAutomationStatus"
        ]
        == "OPTED_IN"
    )
  assert exported["truncated"] is False


def test_detail_preview_uses_remaining_budget_without_hiding_other_cores():
  data = _settings_data(3, 150)
  data["campaign_criterion"] = [
      row for row in data["campaign_criterion"] if row["campaign.id"] == 1
  ]
  data["campaign_conversion_goal"] = []
  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=_read_settings(data)
  ):
    result = reporting.get_campaign_settings(CUSTOMER, ["1", "2", "3", "99"])
  assert result["missing_campaign_ids"] == ["99"]
  assert result["missing_campaign_count"] == 1
  assert result["requested_campaign_count"] == 4
  assert result["found_campaign_count"] == 3
  assert result["core_settings_complete_inline"] is True
  assert result["campaign_details"]
  assert all(row["campaign.id"] != 1 for row in result["campaign_details"])
  detail_ids = {row["campaign.id"] for row in result["campaign_details"]}
  for core in result["campaigns"]:
    assert core["full_detail_inline"] == (core["campaign.id"] in detail_ids)
  assert _json_bytes(result) <= api.INLINE_PAGE_BYTE_LIMIT


def test_core_overflow_is_explicit_and_never_becomes_missing_campaigns():
  data = _settings_data(40, 0)
  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=_read_settings(data)
  ):
    result = reporting.get_campaign_settings(
        CUSTOMER, [str(i) for i in range(1, 41)]
    )
  assert (
      result["found_campaign_count"]
      == result["requested_campaign_count"]
      == 40
  )
  assert result["missing_campaign_count"] == 0
  assert result["missing_campaign_ids"] == []
  assert result["core_settings_complete_inline"] is False
  assert result["complete_counts"]["campaigns"] == 40
  assert result["returned_counts"]["campaigns"] < 40
  assert result["shared_inline_delivery"]["omitted_counts"]["campaigns"] > 0
  assert result["full_materialized_response_export"]["available"] is True
  assert _json_bytes(result) <= api.INLINE_PAGE_BYTE_LIMIT
