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

"""Native parsing and dispatch for reported workflows, without live eligibility.

Only the API client is mocked in request tests. These checks prove that the
installed SDK accepts the payloads and the server sends one validate-only
request; Google must still validate account eligibility and business rules.
"""

from collections import Counter
import csv
import json
from pathlib import Path
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.enums.types.ad_group_ad_status import AdGroupAdStatusEnum
from google.ads.googleads.v25.enums.types.asset_field_type import AssetFieldTypeEnum
from google.ads.googleads.v25.resources.types.campaign import Campaign
from google.ads.googleads.v25.services.types.google_ads_service import MutateGoogleAdsRequest
from google.ads.googleads.v25.services.types.google_ads_service import MutateGoogleAdsResponse
from google.api_core.exceptions import ServiceUnavailable
import jsonschema
import pytest

from ads_mcp.tools import api
from ads_mcp.tools import resources

CUSTOMER = "123"
ASSET_GROUP = "customers/123/assetGroups/-1"
FILTER_ROOT = "customers/123/assetGroupListingGroupFilters/-1~-10"
CLONE_DESTINATION = Campaign(
    resource_name="customers/123/campaigns/20",
    advertising_channel_type="PERFORMANCE_MAX",
    brand_guidelines_enabled=False,
    shopping_setting={"merchant_id": 987654321},
)


def _campaign_operations(status="ENABLED"):
  budget = "customers/123/campaignBudgets/-1"
  return [
      {
          "campaign_budget_operation": {
              "create": {
                  "resource_name": budget,
                  "name": "Private PMax budget",
                  "amount_micros": 10_000_000,
                  "period": "DAILY",
                  "delivery_method": "STANDARD",
                  "explicitly_shared": False,
              }
          }
      },
      {
          "campaign_operation": {
              "update": {
                  "resource_name": "customers/123/campaigns/7",
                  "campaign_budget": budget,
                  "geo_target_type_setting": {
                      "positive_geo_target_type": "PRESENCE",
                      "negative_geo_target_type": "PRESENCE",
                  },
                  "maximize_conversion_value": {"target_roas": 4.2},
                  "status": status,
              },
              "update_mask": {
                  "paths": [
                      "campaign_budget",
                      "geo_target_type_setting.positive_geo_target_type",
                      "geo_target_type_setting.negative_geo_target_type",
                      "maximize_conversion_value.target_roas",
                      "status",
                  ]
              },
          }
      },
      {
          "campaign_budget_operation": {
              "update": {
                  "resource_name": "customers/123/campaignBudgets/8",
                  "amount_micros": 12_000_000,
              },
              "update_mask": "amountMicros",
          }
      },
  ]


def _clone_operations():
  """Models a caller-remapped clone into a legacy retail PMax destination.

  The destination fixture has brand guidelines explicitly disabled. Its
  existing asset IDs stand for compatible text, image and video assets; their
  actual content, dimensions and eligibility still require API validation.
  Source reads and temporary-ID remapping belong to the caller.
  """
  operations = [
      {
          "asset_group_operation": {
              "create": {
                  "resource_name": ASSET_GROUP,
                  "campaign": CLONE_DESTINATION.resource_name,
                  "name": "Cloned asset group",
                  "status": "PAUSED",
                  "final_urls": ["https://example.com/landing"],
              }
          }
      },
      {
          "asset_group_listing_group_filter_operation": {
              "create": {
                  "resource_name": FILTER_ROOT,
                  "asset_group": ASSET_GROUP,
                  "type": "SUBDIVISION",
                  "listing_source": "SHOPPING",
              }
          }
      },
      {
          "asset_group_listing_group_filter_operation": {
              "create": {
                  "resource_name": (
                      "customers/123/assetGroupListingGroupFilters/-1~-11"
                  ),
                  "asset_group": ASSET_GROUP,
                  "type": "UNIT_INCLUDED",
                  "listing_source": "SHOPPING",
                  "parent_listing_group_filter": FILTER_ROOT,
                  "case_value": {"product_brand": {"value": "Example"}},
              }
          }
      },
      {
          "asset_group_listing_group_filter_operation": {
              "create": {
                  "resource_name": (
                      "customers/123/assetGroupListingGroupFilters/-1~-12"
                  ),
                  "asset_group": ASSET_GROUP,
                  "type": "UNIT_EXCLUDED",
                  "listing_source": "SHOPPING",
                  "parent_listing_group_filter": FILTER_ROOT,
                  "case_value": {"product_brand": {}},
              }
          }
      },
  ]
  field_types = (
      ["HEADLINE"] * 15
      + ["LONG_HEADLINE"] * 5
      + ["DESCRIPTION"] * 5
      + ["MARKETING_IMAGE"] * 20
      + ["SQUARE_MARKETING_IMAGE"] * 5
      + ["LOGO"] * 5
      + ["YOUTUBE_VIDEO"] * 3
      + ["BUSINESS_NAME"]
  )
  operations.extend(
      {
          "asset_group_asset_operation": {
              "create": {
                  "asset_group": ASSET_GROUP,
                  "asset": f"customers/123/assets/{index}",
                  "field_type": field_type,
              }
          }
      }
      for index, field_type in enumerate(field_types, 1)
  )
  operations.extend(
      {
          "asset_group_signal_operation": {
              "create": {
                  "asset_group": ASSET_GROUP,
                  "search_theme": {"text": f"Search theme {index}"},
              }
          }
      }
      for index in range(34)
  )
  operations.append(
      {
          "asset_group_signal_operation": {
              "create": {
                  "asset_group": ASSET_GROUP,
                  "audience": {"audience": "customers/123/audiences/4"},
              }
          }
      }
  )
  return operations


def _create_pmax_operations():
  """Creates a private budget and paused campaign with native required fields.

  Uses the core fields in the official create-campaign/create-budget guides:
  https://developers.google.com/google-ads/api/performance-max/create-campaign
  https://developers.google.com/google-ads/api/performance-max/create-budget
  Asset groups and their minimum links are independently covered by the
  clone fixture. This is request construction, not live account eligibility.
  """
  return [
      _campaign_operations()[0],
      {
          "campaign_operation": {
              "create": {
                  "resource_name": "customers/123/campaigns/-2",
                  "name": "New Performance Max campaign",
                  "status": "PAUSED",
                  "advertising_channel_type": "PERFORMANCE_MAX",
                  "campaign_budget": "customers/123/campaignBudgets/-1",
                  "maximize_conversion_value": {"target_roas": 4.2},
                  "brand_guidelines_enabled": False,
                  "contains_eu_political_advertising": (
                      "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
                  ),
              }
          }
      },
  ]


def _demand_gen_operations():
  videos = ["abcdefghijk", "lmnopqrstuv", "wxyz0123456"]
  asset_names = [f"customers/123/assets/{-200 - i}" for i in range(3)]
  operations = [
      {
          "asset_operation": {
              "create": {
                  "resource_name": asset_name,
                  "name": f"Uploaded video {index}",
                  "youtube_video_asset": {"youtube_video_id": video_id},
              }
          }
      }
      for index, (asset_name, video_id) in enumerate(zip(asset_names, videos))
  ]
  for index, references in enumerate((asset_names[:2], asset_names[2:])):
    operations.append(
        {
            "ad_group_ad_operation": {
                "create": {
                    "ad_group": "customers/123/adGroups/30",
                    "status": "PAUSED",
                    "ad": {
                        "name": f"Demand Gen video {index}",
                        "final_urls": ["https://example.com/landing"],
                        "demand_gen_video_responsive_ad": {
                            "headlines": [{"text": "Example headline"}],
                            "long_headlines": [
                                {"text": "Example long headline"}
                            ],
                            "descriptions": [{"text": "Example description"}],
                            "business_name": {"text": "Example"},
                            "videos": [{"asset": name} for name in references],
                            "logo_images": [
                                {"asset": "customers/123/assets/901"}
                            ],
                            "call_to_actions": [
                                {"asset": "customers/123/assets/902"}
                            ],
                        },
                    },
                }
            }
        }
    )
  return operations


def _validate_native_request(operations, *, string_input=False):
  with mock.patch.object(resources, "get_ads_client") as client:
    service = client.return_value.get_service.return_value
    service.mutate.return_value = MutateGoogleAdsResponse()
    result = resources.mutate_ads_resources(
        "1-23",
        json.dumps(operations) if string_input else operations,
        login_customer_id="9-99",
    )
  client.assert_called_once_with("999")
  client.return_value.get_service.assert_called_once_with("GoogleAdsService")
  service.mutate.assert_called_once()
  request = service.mutate.call_args.kwargs["request"]
  assert isinstance(request, MutateGoogleAdsRequest)
  assert request.customer_id == CUSTOMER
  assert request.validate_only is True
  assert request.partial_failure is False
  assert len(request.mutate_operations) == len(operations)
  assert result["requested_operation_count"] == len(operations)
  assert result["validate_only"] is True
  assert result["executed"] is False
  assert result["partial_failure"] is False
  assert result["returned_result_count"] == 0
  return request


@pytest.mark.parametrize("status", ["ENABLED", "PAUSED"])
def test_campaign_geo_troas_budget_and_status_are_native_validate_only(status):
  request = _validate_native_request(_campaign_operations(status))
  budget = request.mutate_operations[0].campaign_budget_operation.create
  assert budget.resource_name == "customers/123/campaignBudgets/-1"
  assert budget.explicitly_shared is False
  assert budget.period.name == "DAILY"
  campaign_op = request.mutate_operations[1].campaign_operation
  assert campaign_op.update.campaign_budget == budget.resource_name
  assert (
      campaign_op.update.geo_target_type_setting.positive_geo_target_type.name
      == "PRESENCE"
  )
  assert (
      campaign_op.update.geo_target_type_setting.negative_geo_target_type.name
      == "PRESENCE"
  )
  assert campaign_op.update.maximize_conversion_value.target_roas == 4.2
  assert campaign_op.update.status.name == status
  assert (
      list(campaign_op.update_mask.paths)
      == _campaign_operations()[1]["campaign_operation"]["update_mask"][
          "paths"
      ]
  )
  update = request.mutate_operations[2].campaign_budget_operation
  assert update.update.amount_micros == 12_000_000
  assert list(update.update_mask.paths) == ["amount_micros"]


def test_98_operation_clone_retains_temp_references_tree_and_operation_order():
  assert CLONE_DESTINATION.brand_guidelines_enabled is False
  assert Campaign.pb(CLONE_DESTINATION).HasField("brand_guidelines_enabled")
  operations = _clone_operations()
  assert len(operations) == 98
  request = _validate_native_request(operations, string_input=True)
  native = MutateGoogleAdsRequest.pb(request)
  families = [op.WhichOneof("operation") for op in native.mutate_operations]
  assert families == (
      ["asset_group_operation"]
      + ["asset_group_listing_group_filter_operation"] * 3
      + ["asset_group_asset_operation"] * 59
      + ["asset_group_signal_operation"] * 35
  )
  assert Counter(families) == {
      "asset_group_operation": 1,
      "asset_group_listing_group_filter_operation": 3,
      "asset_group_asset_operation": 59,
      "asset_group_signal_operation": 35,
  }
  group = request.mutate_operations[0].asset_group_operation.create
  assert group.resource_name == ASSET_GROUP
  assert group.campaign == CLONE_DESTINATION.resource_name
  assert group.status.name == "PAUSED"
  filters = [
      op.asset_group_listing_group_filter_operation.create
      for op in request.mutate_operations[1:4]
  ]
  assert filters[0].resource_name == FILTER_ROOT
  assert filters[0].parent_listing_group_filter == ""
  assert filters[1].parent_listing_group_filter == FILTER_ROOT
  assert filters[2].parent_listing_group_filter == FILTER_ROOT
  assert filters[1].case_value.product_brand.value == "Example"
  catch_all = native.mutate_operations[
      3
  ].asset_group_listing_group_filter_operation.create
  assert catch_all.case_value.WhichOneof("dimension") == "product_brand"
  assert catch_all.case_value.product_brand.HasField("value") is False
  links = [
      op.asset_group_asset_operation.create
      for op in request.mutate_operations[4:63]
  ]
  assert all(link.asset_group == ASSET_GROUP for link in links)
  assert [link.asset for link in links] == [
      f"customers/123/assets/{i}" for i in range(1, 60)
  ]
  # Official minimums apply to this brand-guidelines-disabled destination:
  # https://developers.google.com/google-ads/api/performance-max/asset-requirements
  field_counts = Counter(
      AssetFieldTypeEnum.AssetFieldType(link.field_type).name for link in links
  )
  for field, (minimum, maximum) in {
      "HEADLINE": (3, 15),
      "LONG_HEADLINE": (1, 5),
      "DESCRIPTION": (2, 5),
      "MARKETING_IMAGE": (1, 20),
      "SQUARE_MARKETING_IMAGE": (1, 20),
      "BUSINESS_NAME": (1, 1),
      "LOGO": (1, 5),
  }.items():
    assert minimum <= field_counts[field] <= maximum
  assert field_counts["YOUTUBE_VIDEO"] == 3
  signals = [
      op.asset_group_signal_operation.create
      for op in request.mutate_operations[63:]
  ]
  assert all(signal.asset_group == ASSET_GROUP for signal in signals)
  assert [signal.search_theme.text for signal in signals[:34]] == [
      f"Search theme {i}" for i in range(34)
  ]
  assert signals[-1].audience.audience == "customers/123/audiences/4"


def test_pmax_campaign_create_keeps_private_budget_reference_and_one_rpc():
  request = _validate_native_request(_create_pmax_operations())
  native = MutateGoogleAdsRequest.pb(request)
  assert [op.WhichOneof("operation") for op in native.mutate_operations] == [
      "campaign_budget_operation",
      "campaign_operation",
  ]
  budget = request.mutate_operations[0].campaign_budget_operation.create
  campaign_op = request.mutate_operations[1].campaign_operation
  campaign = campaign_op.create
  assert budget.resource_name == "customers/123/campaignBudgets/-1"
  assert budget.explicitly_shared is False
  assert budget.amount_micros == 10_000_000
  assert budget.period.name == "DAILY"
  assert budget.delivery_method.name == "STANDARD"
  assert campaign.resource_name == "customers/123/campaigns/-2"
  assert campaign.name == "New Performance Max campaign"
  assert campaign.status.name == "PAUSED"
  assert campaign.advertising_channel_type.name == "PERFORMANCE_MAX"
  assert campaign.advertising_channel_sub_type.name == "UNSPECIFIED"
  assert campaign.campaign_budget == budget.resource_name
  assert campaign.maximize_conversion_value.target_roas == 4.2
  assert campaign.brand_guidelines_enabled is False
  assert campaign.contains_eu_political_advertising.name == (
      "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
  )
  assert (
      native.mutate_operations[1].campaign_operation.WhichOneof("operation")
      == "create"
  )
  assert (
      native.mutate_operations[1].campaign_operation.create.WhichOneof(
          "campaign_bidding_strategy"
      )
      == "maximize_conversion_value"
  )
  assert not campaign_op.update_mask.paths


def test_three_youtube_assets_and_two_paused_demand_gen_ads_are_native():
  request = _validate_native_request(_demand_gen_operations())
  native = MutateGoogleAdsRequest.pb(request)
  assert [op.WhichOneof("operation") for op in native.mutate_operations] == (
      ["asset_operation"] * 3 + ["ad_group_ad_operation"] * 2
  )
  assets = [op.asset_operation.create for op in request.mutate_operations[:3]]
  assert [asset.youtube_video_asset.youtube_video_id for asset in assets] == [
      "abcdefghijk",
      "lmnopqrstuv",
      "wxyz0123456",
  ]
  ads = [
      op.ad_group_ad_operation.create for op in request.mutate_operations[3:]
  ]
  assert all(
      ad.status == AdGroupAdStatusEnum.AdGroupAdStatus.PAUSED for ad in ads
  )
  for ad, names in zip(
      ads,
      (
          [asset.resource_name for asset in assets[:2]],
          [assets[2].resource_name],
      ),
  ):
    assert ad.ad_group == "customers/123/adGroups/30"
    info = ad.ad.demand_gen_video_responsive_ad
    assert [video.asset for video in info.videos] == names
    assert info.business_name.text == "Example"
    assert info.logo_images[0].asset == "customers/123/assets/901"
    assert info.call_to_actions[0].asset == "customers/123/assets/902"
    assert info.headlines[0].text == "Example headline"
    assert list(ad.ad.final_urls) == ["https://example.com/landing"]


@pytest.mark.parametrize(
    "factory",
    [
        _campaign_operations,
        _clone_operations,
        _demand_gen_operations,
        _create_pmax_operations,
    ],
)
def test_report_workflows_never_retry_a_failed_mutation(factory):
  with mock.patch.object(resources, "get_ads_client") as client:
    service = client.return_value.get_service.return_value
    service.mutate.side_effect = ServiceUnavailable("offline unavailable")
    with pytest.raises(ToolError, match="offline unavailable"):
      resources.mutate_ads_resources(CUSTOMER, factory())
  service.mutate.assert_called_once()


@pytest.mark.parametrize(
    "operation_type,collection",
    [
        ("campaign_budget_operation", "campaignBudgets"),
        (
            "asset_group_listing_group_filter_operation",
            "assetGroupListingGroupFilters",
        ),
    ],
)
@pytest.mark.parametrize("kind", ["create", "update", "remove"])
def test_new_operation_families_reject_wrong_customer_before_client(
    operation_type, collection, kind
):
  identifier = "1~2" if collection == "assetGroupListingGroupFilters" else "1"
  target = f"customers/456/{collection}/{identifier}"
  operation = {kind: target if kind == "remove" else {"resource_name": target}}
  if kind == "update":
    operation["update_mask"] = "resourceName"
  with mock.patch.object(resources, "get_ads_client") as client:
    with pytest.raises(ToolError, match="resource of customer_id"):
      resources.mutate_ads_resources(CUSTOMER, [{operation_type: operation}])
  client.assert_not_called()


def _full_schema(result, tmp_path):
  if not result.get("truncated"):
    return result["schema"]
  call = result["full_materialized_response_export"]["export_call"]
  exported = api.export_materialized_response_csv(
      **call["arguments"], output_path=str(tmp_path / "schema.csv")
  )
  schema = {}
  old_limit = csv.field_size_limit(10_000_000)
  try:
    with Path(exported["file_path"]).open(
        encoding="utf-8", newline=""
    ) as file:
      for row in csv.DictReader(file):
        if row["result_type"] == "schema":
          entry = json.loads(row["result"])
          schema[entry["entry_key"]] = entry["value"]
  finally:
    csv.field_size_limit(old_limit)
  return schema


@pytest.mark.parametrize(
    "operation_type,example",
    [
        (name, operation[name])
        for operation in (
            _campaign_operations()[:2]
            + _clone_operations()[:5]
            + [_clone_operations()[-1]]
            + _demand_gen_operations()[::3]
            + _create_pmax_operations()[1:]
        )
        for name in operation
    ],
)
def test_report_operation_families_have_usable_local_schema(
    operation_type, example, tmp_path, monkeypatch
):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  with mock.patch.object(resources, "get_ads_client") as client:
    result = resources.get_resource_mutation_schema(operation_type)
    schema = _full_schema(result, tmp_path)
  client.assert_not_called()
  assert operation_type in result["allowed_operations"]
  assert result["operation_type"] == operation_type
  jsonschema.validate(example, schema)
