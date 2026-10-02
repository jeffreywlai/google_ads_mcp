"""Native release features and resource mutation safety regressions."""

import json
import csv
from pathlib import Path
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.google_ads_service import MutateGoogleAdsResponse
import pytest

from ads_mcp.tools import resources
from ads_mcp.tools import api
from ads_mcp.tools import _gaql


@pytest.mark.parametrize(
    "operation",
    [
        {
            "conversion_action_operation": {
                "update": {
                    "resource_name": "customers/123/conversionActions/5",
                    "category": "IN_APP_AD_REVENUE",
                    "type": "FIREBASE_ANDROID_APP_AD_IMPRESSION",
                },
                "update_mask": {"paths": ["category"]},
            }
        },
        {
            "conversion_action_operation": {
                "update": {
                    "resource_name": "customers/123/conversionActions/5",
                    "type": "FIREBASE_IOS_APP_AD_IMPRESSION",
                },
                "update_mask": "category",
            }
        },
        {
            "conversion_value_rule_set_operation": {
                "create": {"dimensions": ["ITINERARY"]}
            }
        },
        {
            "ad_group_ad_operation": {
                "update": {
                    "resource_name": "customers/123/adGroupAds/4~5",
                    "ad_group_ad_asset_automation_settings": [
                        {
                            "asset_automation_type": (
                                "GENERATE_ANIMATED_IMAGES_FROM_OTHER_ASSETS"
                            ),
                            "asset_automation_status": "OPTED_OUT",
                        }
                    ],
                },
                "update_mask": "adGroupAdAssetAutomationSettings",
            }
        },
        {
            "customer_operation": {
                "update": {
                    "resource_name": "customers/123",
                    "descriptive_name": "Example",
                },
                "update_mask": "descriptiveName",
            }
        },
        {
            "experiment_operation": {
                "create": {
                    "name": "Lift",
                    "lift_measurement_config": (
                        "customers/123/liftMeasurementConfigs/4"
                    ),
                }
            }
        },
        {
            "asset_group_signal_operation": {
                "create": {
                    "asset_group": "customers/123/assetGroups/7",
                    "local_services_id": {"service_id": "category"},
                }
            }
        },
        {
            "asset_set_asset_operation": {
                "create": {
                    "asset_set": "customers/123/assetSets/2",
                    "asset": "customers/123/assets/3",
                }
            }
        },
        {
            "campaign_asset_operation": {
                "remove": "customers/123/campaignAssets/2~3~TEXT_DISCLAIMER"
            }
        },
        {
            "customer_asset_operation": {
                "remove": "customers/123/customerAssets/3~TEXT_DISCLAIMER"
            }
        },
    ],
)
def test_release_resource_requests_are_native_and_validate_by_default(
    operation,
):
  with mock.patch.object(resources, "get_ads_client") as client:
    service = client.return_value.get_service.return_value
    service.mutate.return_value = MutateGoogleAdsResponse()
    result = resources.mutate_ads_resources("1-23", [operation])
  request = service.mutate.call_args.kwargs["request"]
  assert request.customer_id == "123"
  assert request.validate_only is True
  assert request.partial_failure is False
  assert len(request.mutate_operations) == 1
  assert result["executed"] is False
  assert result["returned_result_count"] == 0
  service.mutate.assert_called_once()


@pytest.mark.parametrize(
    "operations",
    [
        [],
        [{}],
        [{"book_campaigns_operation": {}}],
        [{"campaign_operation": {}}],
        [
            {
                "campaign_operation": {
                    "update": {
                        "resource_name": "customers/456/campaigns/7",
                        "name": "X",
                    },
                    "update_mask": "name",
                }
            }
        ],
        [
            {
                "campaign_operation": {
                    "update": {
                        "resource_name": "customers/123/campaigns/7",
                        "name": "X",
                    }
                }
            }
        ],
        [
            {
                "campaign_operation": {
                    "update": {"resource_name": "customers/123/campaigns/7"},
                    "update_mask": "notAField",
                }
            }
        ],
        [
            {
                "campaign_operation": {
                    "update": {"resource_name": "customers/123/campaigns/7"},
                    "update_mask": "resourceName",
                }
            }
        ],
        [
            {
                "campaign_operation": {
                    "create": {"name": "X"},
                    "update_mask": "name",
                }
            }
        ],
        [
            {
                "campaign_operation": {
                    "update": {
                        "resource_name": "customers/123/campaigns/7",
                        "name": "X",
                    },
                    "remove": "customers/123/campaigns/7",
                }
            }
        ],
        [{"campaign_operation": {"remove": "customers/456/campaigns/7"}}],
        [
            {
                "ad_group_criterion_operation": {
                    "create": {"vertical_ads_item_bid": {}}
                }
            }
        ],
        '[{"campaign_operation":{"create":{"name":"a","name":"b"}}}]',
        '[{"campaign_operation":{"create":{"unknown":true}}}]',
    ],
)
def test_invalid_requests_fail_before_client(operations):
  with mock.patch.object(resources, "get_ads_client") as client:
    with pytest.raises(ToolError):
      resources.mutate_ads_resources("123", operations)
  client.assert_not_called()


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_mutation_flags_are_not_coerced(flag):
  with mock.patch.object(resources, "get_ads_client") as client:
    with pytest.raises(ToolError, match="booleans"):
      resources.mutate_ads_resources("123", [], validate_only=flag)
  client.assert_not_called()


def test_partial_failure_and_empty_result_positions_survive():
  response = MutateGoogleAdsResponse(
      mutate_operation_responses=[
          {"campaign_result": {"resource_name": "customers/123/campaigns/7"}},
          {},
      ],
      partial_failure_error={"code": 3, "message": "operation 1 failed"},
  )
  with mock.patch.object(resources, "get_ads_client") as client:
    service = client.return_value.get_service.return_value
    service.mutate.return_value = response
    result = resources.mutate_ads_resources(
        "123",
        [
            {"campaign_operation": {"remove": "customers/123/campaigns/7"}},
            {"campaign_operation": {"remove": "customers/123/campaigns/8"}},
        ],
        validate_only=False,
        partial_failure=True,
    )
  assert result["executed"] is True
  assert result["response"]["mutate_operation_responses"][1] == {}
  assert result["response"]["partial_failure_error"]["code"] == 3
  assert result["returned_result_count"] == 2
  service.mutate.assert_called_once()


def test_large_resource_schema_is_bounded_and_has_exact_export():
  with mock.patch.object(
      api,
      "get_ads_credential_cache_scope",
      side_effect=AssertionError("Local schemas must not access credentials"),
  ):
    result = resources.get_resource_mutation_schema("campaign_operation")
  assert len(json.dumps(result).encode()) <= 32768
  if result.get("truncated"):
    assert result["full_materialized_response_export"]["export_call"]


def test_local_services_email_removal_has_contact_migration_hint():
  with pytest.raises(ToolError, match="unavailable in v25") as caught:
    _gaql.preprocess_gaql_query(
        "SELECT local_services_lead.contact_details.email "
        "FROM local_services_lead"
    )
  assert "local_services_lead.contact_details" in str(caught.value)


def test_public_schema_token_cannot_read_ads_snapshot():
  with mock.patch.object(
      api, "get_ads_credential_cache_scope", return_value="private-account"
  ):
    token = api._store_materialized_snapshot([{"private": "account-data"}])
  forged = token.replace(
      "materialized-snapshot-v1:", "public-schema-snapshot-v1:"
  )
  with mock.patch.object(
      api,
      "get_ads_credential_cache_scope",
      side_effect=AssertionError(
          "Public namespace must not inspect credentials"
      ),
  ):
    with pytest.raises(ToolError, match="expired"):
      api._get_materialized_snapshot_rows(forged)


def test_public_schema_export_is_exact_without_credentials():
  with mock.patch.object(
      api,
      "get_ads_credential_cache_scope",
      side_effect=AssertionError("No credential lookup for public schema"),
  ):
    result = resources.get_resource_mutation_schema("campaign_operation")
    call = result["full_materialized_response_export"]["export_call"]
    exported = api.export_materialized_response_csv(**call["arguments"])
  try:
    with Path(exported["file_path"]).open(encoding="utf-8") as stream:
      rows = list(csv.DictReader(stream))
    serialized = json.dumps(rows)
    assert "AUTOMATED_VIDEO_CRAWL" in serialized
    assert "conversion_attribution_integration_partners" in serialized
    assert len(rows) == exported["row_count"]
  finally:
    Path(exported["file_path"]).unlink(missing_ok=True)
