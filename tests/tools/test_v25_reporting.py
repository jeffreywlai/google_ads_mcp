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

"""Native v25 reporting schemas, compatibility, and retained result delivery."""

import csv
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
from google.api_core.exceptions import PermissionDenied
from google.protobuf.field_mask_pb2 import FieldMask
import pytest
import yaml

from ads_mcp.tools import api
from ads_mcp.tools import reporting
from ads_mcp.tools._gaql import preprocess_gaql_query


CUSTOMER = "1234567890"
CAMPAIGN = f"customers/{CUSTOMER}/campaigns/111"
GOAL = "customers/999/goals/10"
FIELDS = yaml.safe_load(
    (
        Path(reporting.__file__).parents[1] / "context" / "fields.yaml"
    ).read_text(encoding="utf-8")
)
EMPTY_PAGE = {"rows": [], "total_results_count": 0, "next_page_token": None}


@pytest.fixture(autouse=True)
def _offline_credential_scope():
  with mock.patch.object(
      api, "get_ads_credential_cache_scope", return_value="v25-report-tests"
  ):
    yield


def _selected_fields(query):
  return [
      field.strip()
      for field in re.search(r"SELECT\s+(.+?)\s+FROM", query, re.I | re.S)[
          1
      ].split(",")
  ]


def _validate_query(query):
  """Checks assembled queries against fresh GAQL metadata and v25 protos."""
  preprocess_gaql_query(query)
  for field in _selected_fields(query):
    assert field in FIELDS
    descriptor = GoogleAdsRow.pb().DESCRIPTOR
    for component in field.split("."):
      member = descriptor.fields_by_name.get(component)
      if member is None:
        member = descriptor.fields_by_name[component + "_"]
      descriptor = member.message_type
  assert " LIMIT " not in query.upper()


@pytest.mark.parametrize(
    "segment_by,original_available",
    [
        (None, True),
        (["DATE"], True),
        (["MONTH"], True),
        (["WEEK"], True),
        (["NETWORK"], True),
        (["DEVICE"], False),
        (["LOYALTY_MEMBERSHIP"], False),
        (["DATE", "LOYALTY_MEMBERSHIP"], False),
    ],
)
def test_campaign_original_value_and_loyalty_use_compatible_metrics(
    segment_by, original_available
):
  with mock.patch.object(
      reporting, "run_gaql_query_page", return_value=EMPTY_PAGE
  ) as run:
    result = reporting.get_campaign_performance(
        CUSTOMER, segment_by=segment_by
    )
  query = run.call_args.kwargs["query"]
  _validate_query(query)
  fields = _selected_fields(query)
  assert ("metrics.original_conversion_value" in fields) is original_available
  assert "metrics.cost_micros" in fields
  assert "metrics.conversions_value" in fields
  assert result["omitted_metrics"] == (
      [] if original_available else ["metrics.original_conversion_value"]
  )
  if segment_by and "LOYALTY_MEMBERSHIP" in segment_by:
    assert "segments.loyalty_membership" in fields


@pytest.mark.parametrize(
    "segment_by,segment_field,booking_available",
    [
        ("VERTICAL", "segments.vertical_ads_vertical", True),
        ("BRAND", "segments.vertical_ads_listing_brand", True),
        ("PRICE_TIER", "segments.vertical_ads_price_tier", True),
        ("RATE_RULE_ID", "segments.vertical_ads_rate_rule_id", True),
        ("RATE_TYPE", "segments.vertical_ads_rate_type", True),
        ("ADVANCE_BOOKING_WINDOW", "segments.advance_booking_window", False),
        ("LENGTH_OF_BOOKING", "segments.length_of_booking", False),
        ("START_DATE", "segments.start_date", False),
        ("START_DAY_OF_WEEK", "segments.start_day_of_week", False),
        ("USER_SET_DATES", "segments.user_set_dates", False),
    ],
)
def test_hotel_segments_respect_booking_metric_compatibility(
    segment_by, segment_field, booking_available
):
  with mock.patch.object(
      reporting, "run_gaql_query_page", return_value=EMPTY_PAGE
  ) as run:
    result = reporting.list_vertical_ads_performance(
        CUSTOMER, segment_by=segment_by
    )
  query = run.call_args.kwargs["query"]
  _validate_query(query)
  fields = _selected_fields(query)
  assert segment_field in fields
  booking_metric = "metrics.vertical_ads_average_booking_value_micros"
  assert (booking_metric in fields) is booking_available
  assert (booking_metric in result["omitted_metrics"]) is not booking_available
  assert "metrics.conversions_value" in fields


@pytest.mark.parametrize(
    "dimension,measurement_type",
    [
        ("CONFIG", "CONFIGURATION"),
        ("FLIGHT", "CONFIGURATION"),
        ("CONFIG", "BRAND"),
        ("CONFIG", "CONVERSION"),
        ("CAMPAIGN", "CONFIGURATION"),
        ("CAMPAIGN", "BRAND"),
        ("AGE_RANGE", "CONFIGURATION"),
        ("AGE_RANGE", "BRAND"),
        ("DEVICE", "CONFIGURATION"),
        ("DEVICE", "BRAND"),
        ("GENDER", "CONFIGURATION"),
        ("GENDER", "BRAND"),
        ("VIDEO", "CONFIGURATION"),
        ("VIDEO", "BRAND"),
    ],
)
def test_lift_resource_and_measurement_matrix(dimension, measurement_type):
  with mock.patch.object(
      reporting, "run_gaql_query_page", return_value=EMPTY_PAGE
  ) as run:
    result = reporting.list_lift_measurements(
        CUSTOMER,
        dimension=dimension.lower(),
        measurement_type=measurement_type.lower(),
        lift_measurement_config_ids='["10", "20", "10"]',
        limit=25,
        login_customer_id="456",
    )
  query = run.call_args.kwargs["query"]
  _validate_query(query)
  assert ".lift_measurement_config_id IN (10, 20)" in query
  assert run.call_args.kwargs["page_size"] == 25
  assert run.call_args.kwargs["login_customer_id"] == "456"
  assert result["dimension"] == dimension
  assert result["measurement_type"] == measurement_type
  fields = _selected_fields(query)
  metrics = [field for field in fields if field.startswith("metrics.")]
  if measurement_type == "CONVERSION":
    assert len(metrics) == 28
    assert "metrics.incremental_conversions_winner_score" in fields
    assert "metrics.relative_conversion_value_lift_p90_upper_bound" in fields
    assert "segments.conversion_lift_start_date" in fields
    assert "segments.conversion_lift_end_date" in fields
    assert "segments.conversion_action" not in fields
    assert "segments.date" not in query
  elif measurement_type == "BRAND":
    assert len(metrics) == 31
    assert "metrics.absolute_brand_lift_p_value" in fields
    assert "metrics.absolute_brand_lift_p90_lower_bound" in fields
    assert "segments.brand_lift_measurement_type" in fields
  else:
    assert not metrics
  if dimension == "FLIGHT":
    assert "lift_measurement_flight.start_date" in fields
    assert "lift_measurement_flight.end_date" in fields


@pytest.mark.parametrize(
    "measurement_type", ["CONFIGURATION", "BRAND", "CONVERSION"]
)
def test_config_lift_omits_upstream_broken_campaigns_with_explicit_hint(
    measurement_type,
):
  with mock.patch.object(
      reporting, "run_gaql_query_page", return_value=EMPTY_PAGE
  ) as run:
    result = reporting.list_lift_measurements(
        CUSTOMER, dimension="CONFIG", measurement_type=measurement_type
    )
  query = run.call_args.kwargs["query"]
  _validate_query(query)
  broken_field = "lift_measurement_config.campaigns"
  view = yaml.safe_load(
      (
          Path(reporting.__file__).parents[1]
          / "context"
          / "views"
          / "lift_measurement_config.yaml"
      ).read_text(encoding="utf-8")
  )
  expected_attributes = {
      field
      for field in view["attributes"]
      if field.startswith("lift_measurement_config.") and field != broken_field
  }
  selected_attributes = {
      field
      for field in _selected_fields(query)
      if field.startswith("lift_measurement_config.")
  }
  assert selected_attributes == expected_attributes
  assert broken_field in view["attributes"]
  assert broken_field not in query
  assert result["omitted_fields"] == [broken_field]
  assert "internal error" in result["configuration_note"]
  assert "2026-10-02" in result["configuration_note"]
  assert "dimension='CAMPAIGN'" in result["configuration_note"]
  run.assert_called_once()


@pytest.mark.parametrize("measurement_type", ["CONFIGURATION", "BRAND"])
def test_campaign_lift_keeps_campaign_associations(measurement_type):
  with mock.patch.object(
      reporting, "run_gaql_query_page", return_value=EMPTY_PAGE
  ) as run:
    result = reporting.list_lift_measurements(
        CUSTOMER, dimension="CAMPAIGN", measurement_type=measurement_type
    )
  query = run.call_args.kwargs["query"]
  _validate_query(query)
  assert "lift_measurement_campaign.campaign" in _selected_fields(query)
  assert result["omitted_fields"] == []
  assert "configuration_note" not in result
  run.assert_called_once()


def test_brand_lift_date_filter_is_not_fabricated_for_study_windows():
  with mock.patch.object(
      reporting, "run_gaql_query_page", return_value=EMPTY_PAGE
  ) as run:
    result = reporting.list_lift_measurements(
        CUSTOMER,
        measurement_type="BRAND",
        date_range={"start_date": "2026-09-01", "end_date": "2026-09-30"},
    )
  query = run.call_args.kwargs["query"]
  _validate_query(query)
  assert "segments.date BETWEEN '2026-09-01' AND '2026-09-30'" in query
  assert result["date_range"] == {
      "start_date": "2026-09-01",
      "end_date": "2026-09-30",
  }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dimension": "COUNTRY"},
        {"measurement_type": "UNKNOWN"},
        {"dimension": "FLIGHT", "measurement_type": "BRAND"},
        {"dimension": "CAMPAIGN", "measurement_type": "CONVERSION"},
        {"measurement_type": "CONVERSION", "date_range": "LAST_30_DAYS"},
        {"date_range": "LAST_30_DAYS"},
        {"lift_measurement_config_ids": []},
        {"lift_measurement_config_ids": [True]},
        {"lift_measurement_config_ids": ["0"]},
        {"lift_measurement_config_ids": ["-1"]},
        {"lift_measurement_config_ids": [str(2**63)]},
        {"lift_measurement_config_ids": ["10 OR 1=1"]},
        {"limit": 0},
    ],
)
def test_lift_invalid_inputs_fail_before_client(kwargs):
  with mock.patch.object(api, "get_ads_client") as client:
    with pytest.raises(ToolError):
      reporting.list_lift_measurements(CUSTOMER, **kwargs)
  client.assert_not_called()


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        (reporting.list_lift_measurements, {}),
        (reporting.get_campaign_performance, {}),
        (reporting.list_vertical_ads_performance, {}),
    ],
)
def test_reporting_service_errors_use_central_tool_errors(tool, kwargs):
  with mock.patch.object(
      reporting,
      "run_gaql_query_page",
      side_effect=PermissionDenied("offline denied"),
  ):
    with pytest.raises(ToolError, match="offline denied"):
      tool(CUSTOMER, **kwargs)


@pytest.mark.parametrize(
    "segment_by,social_available",
    [(None, True), ("AD_FORMAT", True), ("AD_SUB_FORMAT", False)],
)
def test_video_format_and_shorts_metrics_use_valid_v25_combinations(
    segment_by, social_available
):
  with mock.patch.object(
      reporting, "run_gaql_query_page", return_value=EMPTY_PAGE
  ) as run:
    result = reporting.list_video_audibility_performance(
        CUSTOMER, segment_by=segment_by
    )
  query = run.call_args.kwargs["query"]
  _validate_query(query)
  fields = _selected_fields(query)
  for metric in (
      "metrics.youtube_comments",
      "metrics.youtube_likes",
      "metrics.youtube_shares",
  ):
    assert (metric in fields) is social_available
    assert (metric in result["omitted_metrics"]) is not social_available
  if segment_by == "AD_SUB_FORMAT":
    assert "segments.ad_format_type" in fields
    assert "segments.ad_sub_format_type" in fields


def test_video_invalid_format_fails_before_client():
  with mock.patch.object(api, "get_ads_client") as client:
    with pytest.raises(ToolError, match="Invalid segment_by"):
      reporting.list_video_audibility_performance(
          CUSTOMER, segment_by="BAD_FORMAT"
      )
  client.assert_not_called()


def _native_setting_rows(
    query, customer_id, login_customer_id, goal_type="LOYALTY_RETENTION"
):
  """Returns v25 messages using the same flattening as a real search stream."""
  _validate_query(query)
  assert login_customer_id == "456"
  resource = re.search(r"\bFROM\s+(\w+)", query)[1]
  assert customer_id == ("999" if resource == "goal" else CUSTOMER)
  integration = {"conversion_attribution_integration_partner": "TRANSUNION"}
  goal_settings = {
      "LOYALTY_RETENTION": "loyalty_retention_goal_settings",
      "NEW_CUSTOMER_ACQUISITION": "new_customer_acquisition_goal_settings",
      "CUSTOMER_RETENTION": "retention_goal_settings",
  }[goal_type]
  campaign_settings = {
      "LOYALTY_RETENTION": "campaign_loyalty_retention_settings",
      "NEW_CUSTOMER_ACQUISITION": "campaign_new_customer_acquisition_settings",
      "CUSTOMER_RETENTION": "campaign_retention_settings",
  }[goal_type]
  values = (
      {"value_multiplier": 1.5}
      if goal_type == "LOYALTY_RETENTION"
      else {"additional_value": 0, "high_lifetime_value_multiplier": 3}
  )
  if resource == "customer":
    row = GoogleAdsRow(
        customer={
            "id": 1234567890,
            "time_zone": "America/New_York",
            "currency_code": "USD",
            "conversion_tracking_setting": {
                "google_ads_conversion_customer": "customers/999"
            },
            "video_customer": {
                "third_party_integration_partners": {
                    "conversion_attribution_integration_partners": [
                        integration
                    ]
                }
            },
        }
    )
  elif resource == "campaign":
    row = GoogleAdsRow(
        campaign={
            "id": 111,
            "resource_name": CAMPAIGN,
            "name": "Local Services",
            "aca_migration_date_time": "2026-09-30 10:00:00",
            "broad_match_migration_date_time": "2026-09-30 11:00:00",
            "shopping_setting": {
                "ignore_brand_exclusion_in_shopping_ads": True
            },
            "pmax_campaign_settings": {
                "local_services_enabled": True,
                "local_services_pmax_campaign_settings": {
                    "country_code": "US",
                    "founding_year": 2000,
                    "navigational_query_leads_enabled": False,
                    "phone_numbers": [
                        {"phone_number": "5551234567", "country_code": "US"}
                    ],
                },
            },
            "third_party_integration_partners": {
                "conversion_attribution_integration_partners": [integration]
            },
            "asset_automation_settings": [
                {
                    "asset_automation_type": "AUTOMATED_VIDEO_CRAWL",
                    "asset_automation_status": "OPTED_IN",
                    "automated_video_crawl_setting": {
                        "automated_video_crawl_infos": [
                            {
                                "url": "https://example.com/video",
                                "source_platform": "YOUTUBE",
                                "enabled": True,
                            }
                        ]
                    },
                }
            ],
        }
    )
  elif resource == "goal":
    row = GoogleAdsRow(
        goal={
            "resource_name": GOAL,
            "owner_customer": "customers/999",
            "goal_type": goal_type,
            goal_settings: {"value_settings": values},
        }
    )
  elif resource == "campaign_goal_config":
    settings = {"value_settings_override": {"value_multiplier": 2}}
    if goal_type == "LOYALTY_RETENTION":
      settings.update(
          {
              "enable_bid_adjustments_for_loyalty_members": False,
              "show_targeted_loyalty_member_benefits_in_pla": True,
          }
      )
    else:
      settings["target_option"] = "TARGET_ALL"
    row = GoogleAdsRow(
        campaign_goal_config={
            "campaign": CAMPAIGN,
            "goal": GOAL,
            "goal_type": goal_type,
            campaign_settings: settings,
        }
    )
  else:
    return []
  return [
      {
          field: api.format_value(api.get_nested_attr(row, field))
          for field in _selected_fields(query)
      }
  ]


def test_native_settings_preserve_unified_goals_and_new_configuration():
  with mock.patch.object(
      reporting, "run_gaql_query", side_effect=_native_setting_rows
  ) as run:
    result = reporting.get_campaign_settings(CUSTOMER, ["111"], "456")
  assert result["goal_owner_customer_id"] == "999"
  assert result["account_goals"][0]["goal.goal_type"] == "LOYALTY_RETENTION"
  assert (
      result["account_goals"][0][
          "goal.loyalty_retention_goal_settings.value_settings.value_multiplier"
      ]
      == 1.5
  )
  campaign = result["campaigns"][0]
  assert campaign["campaign.aca_migration_date_time"] == "2026-09-30 10:00:00"
  assert campaign["campaign.broad_match_migration_date_time"] == (
      "2026-09-30 11:00:00"
  )
  assert (
      campaign[
          "campaign.shopping_setting.ignore_brand_exclusion_in_shopping_ads"
      ]
      is True
  )
  assert campaign["campaign.pmax_campaign_settings.local_services_enabled"]
  goal_config = campaign["campaign_goal_configs"][0]
  assert goal_config["campaign_goal_config.goal"] == GOAL
  assert (
      goal_config[
          "campaign_goal_config.campaign_loyalty_retention_settings."
          "enable_bid_adjustments_for_loyalty_members"
      ]
      is False
  )
  assert "active value-adjustment oneof" in result["snapshot_note"]
  automation = campaign["campaign.asset_automation_settings"][0]
  crawl = automation["automatedVideoCrawlSetting"]["automatedVideoCrawlInfos"]
  assert crawl[0]["sourcePlatform"] == "YOUTUBE"
  assert crawl[0]["enabled"] is True
  account_integrations = result["account"][
      "customer.video_customer.third_party_integration_partners."
      "conversion_attribution_integration_partners"
  ]
  assert account_integrations[0][
      "conversionAttributionIntegrationPartner"
  ] == ("TRANSUNION")
  assert not any(
      "lifecycle_goal" in call.args[0] for call in run.call_args_list
  )


@pytest.mark.parametrize(
    "goal_type,goal_settings,campaign_settings",
    [
        (
            "NEW_CUSTOMER_ACQUISITION",
            "new_customer_acquisition_goal_settings",
            "campaign_new_customer_acquisition_settings",
        ),
        (
            "CUSTOMER_RETENTION",
            "retention_goal_settings",
            "campaign_retention_settings",
        ),
    ],
)
def test_unified_goals_preserve_explicit_zero_and_multiplier(
    goal_type, goal_settings, campaign_settings
):
  def _query(query, customer, manager):
    return _native_setting_rows(query, customer, manager, goal_type=goal_type)

  with mock.patch.object(reporting, "run_gaql_query", side_effect=_query):
    result = reporting.get_campaign_settings(CUSTOMER, ["111"], "456")
  goal = result["account_goals"][0]
  assert goal["goal.goal_type"] == goal_type
  assert goal[f"goal.{goal_settings}.value_settings.additional_value"] == 0
  assert (
      goal[
          f"goal.{goal_settings}.value_settings.high_lifetime_value_multiplier"
      ]
      == 3
  )
  config = result["campaigns"][0]["campaign_goal_configs"][0]
  assert config["campaign_goal_config.goal_type"] == goal_type
  assert (
      config[
          f"campaign_goal_config.{campaign_settings}."
          "value_settings_override.value_multiplier"
      ]
      == 2
  )
  assert result["account_goals_read"] is True


def test_absent_campaigns_mark_account_goals_as_unread():
  with mock.patch.object(
      reporting,
      "run_gaql_query",
      side_effect=[
          [
              {
                  "customer.conversion_tracking_setting."
                  "google_ads_conversion_customer": "customers/999"
              }
          ],
          [],
      ],
  ):
    result = reporting.get_campaign_settings(CUSTOMER, ["111"])
  assert result["account_goals"] == []
  assert result["account_goals_read"] is False
  assert result["goal_owner_customer_id"] == "999"


def test_lift_full_snapshot_export_never_refetches_or_drops_large_rows(
    tmp_path,
):
  source_rows = [
      GoogleAdsRow(
          lift_measurement_config={
              "resource_name": (
                  f"customers/{CUSTOMER}/liftMeasurementConfigs/{i}"
              ),
              "lift_measurement_config_id": i,
              "name": "x" * 40000 if i == 1 else f"study {i}",
          }
      )
      for i in range(1, 105)
  ]

  def _stream(*, query, customer_id):
    _validate_query(query)
    assert customer_id == CUSTOMER
    return [
        SimpleNamespace(
            results=source_rows,
            field_mask=FieldMask(paths=_selected_fields(query)),
        )
    ]

  client = mock.Mock()
  client.get_service.return_value.search_stream.side_effect = _stream
  with mock.patch.object(api, "get_ads_client", return_value=client):
    with mock.patch.object(
        api,
        "_write_csv_rows",
        wraps=api._write_csv_rows,  # pylint: disable=protected-access
    ) as write_csv:
      result = reporting.list_lift_measurements(CUSTOMER, limit=25)
      write_csv.assert_not_called()
      assert result["total_count"] == 104
      assert result["inline_omitted_row_count"] == 1
      assert result["has_more"] is True
      assert len(json.dumps(result).encode()) < api.INLINE_RESPONSE_BYTE_LIMIT
      with mock.patch.dict(
          "os.environ", {"GOOGLE_ADS_MCP_EXPORT_DIR": str(tmp_path)}
      ):
        artifact = api.export_gaql_csv(
            **result["bulk_export_call"]["arguments"],
            output_path=str(tmp_path / "lift.csv"),
        )
  with Path(artifact["file_path"]).open(
      encoding="utf-8", newline=""
  ) as stream:
    exported = list(csv.DictReader(stream))
  assert artifact["row_count"] == len(exported) == 104
  assert len(exported[0]["lift_measurement_config.name"]) == 40000
  client.get_service.return_value.search_stream.assert_called_once()
