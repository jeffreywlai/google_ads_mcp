"""Offline native-v25 regressions for focused asset, Smart, and goal changes."""

import copy
import json
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.ad_service import MutateAdsResponse
from google.ads.googleads.v25.services.types.asset_group_service import MutateAssetGroupsResponse
from google.ads.googleads.v25.services.types.asset_service import MutateAssetsResponse
from google.ads.googleads.v25.services.types.campaign_goal_config_service import MutateCampaignGoalConfigsResponse
from google.ads.googleads.v25.services.types.campaign_service import MutateCampaignsResponse
from google.ads.googleads.v25.services.types.goal_service import MutateGoalsResponse
from google.ads.googleads.v25.services.types.smart_campaign_setting_service import GeneratePMaxDraftCampaignResponse
from google.api_core import exceptions as google_exceptions
import pytest

from ads_mcp.tools import api
from ads_mcp.tools import assets
from ads_mcp.tools import goals
from ads_mcp.tools import smart_campaigns
from ads_mcp.tools._gaql import prepare_gaql_query


@pytest.fixture(name="clients")
def client_fixture():
  """Mocks every account/client entry point; native messages stay real."""
  with (
      mock.patch.object(assets, "get_ads_client") as asset_client,
      mock.patch.object(assets, "run_gaql_query") as query,
      mock.patch.object(goals, "get_ads_client") as goal_client,
      mock.patch.object(smart_campaigns, "get_ads_client") as smart_client,
      mock.patch.object(
          api, "get_ads_credential_cache_scope", return_value="test-v25"
      ),
  ):
    asset_service = asset_client.return_value.get_service.return_value
    asset_service.mutate_asset_groups.return_value = (
        MutateAssetGroupsResponse()
    )
    asset_service.mutate_campaigns.return_value = MutateCampaignsResponse()
    asset_service.mutate_ads.return_value = MutateAdsResponse()
    asset_service.mutate_assets.return_value = MutateAssetsResponse()
    goal_service = goal_client.return_value.get_service.return_value
    goal_service.mutate_goals.return_value = MutateGoalsResponse()
    goal_service.mutate_campaign_goal_configs.return_value = (
        MutateCampaignGoalConfigsResponse()
    )
    smart_service = smart_client.return_value.get_service.return_value
    smart_service.generate_p_max_draft_campaign.return_value = (
        GeneratePMaxDraftCampaignResponse(
            validated_info="Eligible; warning returned."
        )
    )
    query.return_value = [
        {
            "campaign.resource_name": "customers/123/campaigns/7",
            "campaign.advertising_channel_type": "PERFORMANCE_MAX",
            "campaign.asset_automation_settings": [],
        }
    ]
    yield {
        "assets": asset_client,
        "goals": goal_client,
        "smart": smart_client,
        "query": query,
    }


def _request(clients, module, method):
  service = clients[module].return_value.get_service.return_value
  return getattr(service, method).call_args.kwargs["request"]


def _assert_no_account_access(clients):
  for name in ("assets", "goals", "smart", "query"):
    clients[name].assert_not_called()


def test_smart_validation_only_retains_real_info_and_no_fabricated_resources(
    clients,
):
  result = smart_campaigns.generate_pmax_draft_campaign("1-23", "7")
  request = _request(clients, "smart", "generate_p_max_draft_campaign")
  assert request.resource_name == "customers/123/smartCampaignSettings/7"
  assert request.validate_only is True
  assert request.gbp_enabled is False
  assert request.image_enabled is False
  assert result["executed"] is False
  assert result["generated_resource_names_returned"] is False
  assert result["response"] == {
      "validated_info": "Eligible; warning returned."
  }


def test_smart_explicit_generation_returns_only_actual_resource_names(clients):
  service = clients["smart"].return_value.get_service.return_value
  service.generate_p_max_draft_campaign.return_value = (
      GeneratePMaxDraftCampaignResponse(
          pmax_campaign="customers/123/campaigns/8",
          campaign_budget="customers/123/campaignBudgets/9",
          asset_group="customers/123/assetGroups/10",
          assets=["customers/123/assets/11"],
          validated_info="Converted.",
      )
  )
  result = smart_campaigns.generate_pmax_draft_campaign(
      "123", "7", validate_only=False
  )
  assert (
      _request(clients, "smart", "generate_p_max_draft_campaign").validate_only
      is False
  )
  assert result["response"]["pmax_campaign"] == "customers/123/campaigns/8"
  assert result["draft_status_if_created"] == "PAUSED"
  assert result["draft_creation_status_if_created"] == "INCOMPLETE"
  assert result["generated_resource_names_returned"] is True
  assert service.method_calls == [
      mock.call.generate_p_max_draft_campaign(request=mock.ANY)
  ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gbp_enabled": True},
        {"image_enabled": True},
        {"validate_only": "false"},
        {"gbp_enabled": 1},
        {"image_enabled": "false"},
        {"login_customer_id": "bad"},
    ],
)
def test_smart_unsupported_or_invalid_flags_fail_before_client(
    clients, kwargs
):
  with pytest.raises(ToolError):
    smart_campaigns.generate_pmax_draft_campaign("123", "7", **kwargs)
  _assert_no_account_access(clients)


def test_asset_group_options_use_exact_native_mask_and_clear_values(clients):
  result = assets.update_asset_group_url_options(
      "1-23",
      "5",
      tracking_url_template="",
      final_url_suffix="a=b",
      url_custom_parameters='[{"key":"source", "value":"newsletter"}]',
  )
  request = _request(clients, "assets", "mutate_asset_groups")
  assert request.validate_only is True
  assert request.customer_id == "123"
  assert (
      request.operations[0].update.resource_name
      == "customers/123/assetGroups/5"
  )
  assert request.operations[0].update.tracking_url_template == ""
  assert request.operations[0].update.final_url_suffix == "a=b"
  assert list(request.operations[0].update_mask.paths) == [
      "tracking_url_template",
      "final_url_suffix",
      "url_custom_parameters",
  ]
  assert request.operations[0].update.url_custom_parameters[0].key == "source"
  assert result["returned_result_count"] == 0
  assert result["response"] == {}
  assert result["executed"] is False


def test_asset_group_empty_custom_parameters_are_explicit_clear(clients):
  assets.update_asset_group_url_options("123", "5", url_custom_parameters=[])
  operation = _request(clients, "assets", "mutate_asset_groups").operations[0]
  assert not list(operation.update.url_custom_parameters)
  assert list(operation.update_mask.paths) == ["url_custom_parameters"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {
            "url_custom_parameters": [
                {"key": "x", "value": "v", "extra": "bad"}
            ]
        },
        {
            "url_custom_parameters": [
                {"key": "x", "value": "v"},
                {"key": "x", "value": "w"},
            ]
        },
        {"tracking_url_template": 4},
        {"final_url_suffix": "ok", "validate_only": 0},
    ],
)
def test_invalid_asset_group_options_fail_before_client(clients, kwargs):
  with pytest.raises(ToolError):
    assets.update_asset_group_url_options("123", "5", **kwargs)
  _assert_no_account_access(clients)


def test_video_crawl_merges_other_entries_and_replaces_only_requested_entry(
    clients,
):
  current = [
      {
          "asset_automation_type": "TEXT_ASSET_AUTOMATION",
          "asset_automation_status": "OPTED_OUT",
      },
      {
          "asset_automation_type": "AUTOMATED_VIDEO_CRAWL",
          "asset_automation_status": "OPTED_OUT",
          "automated_video_crawl_setting": {"automated_video_crawl_infos": []},
      },
      {
          "asset_automation_type": "GENERATE_IMAGE_ENHANCEMENT",
          "asset_automation_status": "OPTED_IN",
      },
  ]
  original = copy.deepcopy(current)
  clients["query"].return_value[0][
      "campaign.asset_automation_settings"
  ] = current
  result = assets.update_campaign_video_crawl_settings(
      "123",
      "7",
      '[{"url":"https://example.com/video", '
      '"source_platform":"YOUTUBE", "enabled":false}]',
  )
  emitted_query = clients["query"].call_args.args[0]
  prepared_query, _ = prepare_gaql_query(emitted_query)
  assert "campaign.asset_automation_settings" in prepared_query
  request = _request(clients, "assets", "mutate_campaigns")
  entries = request.operations[0].update.asset_automation_settings
  assert len(entries) == 3
  assert entries[0].asset_automation_status.name == "OPTED_OUT"
  assert entries[1].asset_automation_type.name == "GENERATE_IMAGE_ENHANCEMENT"
  assert entries[2].asset_automation_type.name == "AUTOMATED_VIDEO_CRAWL"
  assert (
      entries[2]
      .automated_video_crawl_setting.automated_video_crawl_infos[0]
      .enabled
      is False
  )
  assert list(request.operations[0].update_mask.paths) == [
      "asset_automation_settings"
  ]
  assert current == original
  assert result["preserved_automation_setting_count"] == 2
  assert request.validate_only is True


@pytest.mark.parametrize(
    "info",
    [
        {
            "url": "https://example.com",
            "source_platform": "YOUTUBE",
            "enabled": "false",
        },
        {"url": "https://example.com", "source_platform": [], "enabled": True},
        {
            "url": "https://example.com",
            "source_platform": "UNKNOWN",
            "enabled": True,
        },
        {
            "url": "https://user:pass@example.com",
            "source_platform": "SOCIAL",
            "enabled": True,
        },
        {
            "url": "file:///path",
            "source_platform": "LANDING_PAGE",
            "enabled": True,
        },
        {"url": "https://example.com", "enabled": True},
    ],
)
def test_video_input_validation_precedes_reads_and_clients(clients, info):
  with pytest.raises(ToolError):
    assets.update_campaign_video_crawl_settings("123", "7", [info])
  _assert_no_account_access(clients)


@pytest.mark.parametrize(
    "row",
    [
        {},
        {
            "campaign.resource_name": "customers/123/campaigns/7",
            "campaign.advertising_channel_type": "SEARCH",
        },
        {
            "campaign.resource_name": "customers/123/campaigns/7",
            "campaign.advertising_channel_type": "PERFORMANCE_MAX",
            "campaign.asset_automation_settings": [None],
        },
    ],
)
def test_video_missing_or_ineligible_context_never_mutates(clients, row):
  clients["query"].return_value = [row]
  with pytest.raises(ToolError):
    assets.update_campaign_video_crawl_settings("123", "7", [])
  clients["assets"].assert_not_called()


@pytest.mark.parametrize(
    "function,method,collection",
    [
        (assets.update_ad_synthetic_content_info, "mutate_ads", "ads"),
        (
            assets.update_asset_synthetic_content_info,
            "mutate_assets",
            "assets",
        ),
    ],
)
def test_attestation_targets_only_advertiser_child(
    clients, function, method, collection
):
  result = function("123", "9", "IS_SYNTHETIC")
  request = _request(clients, "assets", method)
  operation = request.operations[0]
  assert operation.update.resource_name == f"customers/123/{collection}/9"
  assert list(operation.update_mask.paths) == [
      "synthetic_content_info.advertiser_attestation"
  ]
  assert (
      operation.update.synthetic_content_info.advertiser_attestation.source.name
      == "ADVERTISER_ATTESTED"
  )
  assert (
      operation.update.synthetic_content_info.advertiser_attestation.status.name
      == "IS_SYNTHETIC"
  )
  native = type(operation.update).pb(operation.update)
  assert not native.synthetic_content_info.HasField("system_attestation")
  assert result["validate_only"] is True


@pytest.mark.parametrize(
    "status", ["UNKNOWN", "GOOGLE_GENERATED_FULLY_AUTOMATED", [], None]
)
def test_invalid_attestation_rejected_locally(clients, status):
  with pytest.raises(ToolError):
    assets.update_asset_synthetic_content_info("123", "9", status)
  _assert_no_account_access(clients)


@pytest.mark.parametrize(
    "setting",
    [
        "retention_goal_settings",
        "new_customer_acquisition_goal_settings",
        "loyalty_retention_goal_settings",
    ],
)
@pytest.mark.parametrize(
    "values",
    [
        {"additional_value": 0.0, "additional_high_lifetime_value": 20.0},
        {"value_multiplier": 1.5, "high_lifetime_value_multiplier": 2.0},
    ],
)
def test_all_goal_types_support_native_value_or_multiplier_oneofs(
    clients, setting, values
):
  result = goals.mutate_goals(
      "1-23", [{"create": {setting: {"value_settings": values}}}]
  )
  request = _request(clients, "goals", "mutate_goals")
  assert request.validate_only is True
  assert request.partial_failure is False
  assert request.customer_id == "123"
  assert result["requested_operation_count"] == 1
  assert result["returned_result_count"] == 0
  assert result["response"] == {}
  native = type(request.operations[0].create).pb(request.operations[0].create)
  assert native.WhichOneof("goal_settings") == setting
  value_settings = getattr(native, setting).value_settings
  assert value_settings.WhichOneof("value_adjustment") in values
  assert value_settings.WhichOneof("high_lifetime_value_adjustment") in values


def test_goal_update_accepts_json_operations_and_native_fieldmask(clients):
  operations = [
      {
          "update": {
              "resourceName": "customers/123/goals/4",
              "newCustomerAcquisitionGoalSettings": {
                  "valueSettings": {"valueMultiplier": 2.0}
              },
          },
          "updateMask": {
              "paths": [
                  "new_customer_acquisition_goal_settings."
                  "value_settings.value_multiplier"
              ]
          },
      }
  ]
  goals.mutate_goals("123", json.dumps(operations))
  request = _request(clients, "goals", "mutate_goals")
  assert list(request.operations[0].update_mask.paths) == [
      "new_customer_acquisition_goal_settings.value_settings.value_multiplier"
  ]


def test_masked_goal_leaf_can_be_explicitly_cleared(clients):
  goals.mutate_goals(
      "123",
      [
          {
              "update": {"resource_name": "customers/123/goals/4"},
              "update_mask": (
                  "retentionGoalSettings.valueSettings.additionalValue"
              ),
          }
      ],
  )
  operation = _request(clients, "goals", "mutate_goals").operations[0]
  assert (
      not type(operation.update)
      .pb(operation.update)
      .HasField("retention_goal_settings")
  )
  assert list(operation.update_mask.paths) == [
      "retention_goal_settings.value_settings.additional_value"
  ]


@pytest.mark.parametrize(
    "operation",
    [
        {},
        {"create": {}},
        {"remove": "customers/123/goals/4"},
        {"create": {"retention_goal_settings": {}, "goal_id": 0}},
        {
            "create": {
                "retention_goal_settings": {},
                "owner_customer": "customers/999",
            }
        },
        {"create": {"retention_goal_settings": {}, "goalType": "UNSPECIFIED"}},
        {"create": {"retention_goal_settings": {}, "unknown": 1}},
        {
            "create": {
                "retention_goal_settings": {},
                "new_customer_acquisition_goal_settings": {},
            }
        },
        {
            "create": {
                "retention_goal_settings": {
                    "value_settings": {
                        "additional_value": 1,
                        "value_multiplier": 2,
                    }
                }
            }
        },
        {
            "create": {
                "retention_goal_settings": {
                    "value_settings": {"value_multiplier": "NaN"}
                }
            }
        },
        {
            "update": {"resource_name": "customers/999/goals/4"},
            "update_mask": "retentionGoalSettings",
        },
        {"update": {"resource_name": "customers/123/goals/4"}},
        {
            "update": {"resource_name": "customers/123/goals/4"},
            "update_mask": "resourceName",
        },
        {
            "update": {"resource_name": "customers/123/goals/4"},
            "update_mask": "retentionGoalSettings.unknown",
        },
        {
            "update": {
                "resource_name": "customers/123/goals/4",
                "retention_goal_settings": {},
            },
            "update_mask": "loyaltyRetentionGoalSettings",
        },
        {
            "create": {"retention_goal_settings": {}},
            "update_mask": "retentionGoalSettings",
        },
    ],
)
def test_invalid_goal_operations_fail_before_client(clients, operation):
  with pytest.raises(ToolError):
    goals.mutate_goals("123", [operation])
  _assert_no_account_access(clients)


@pytest.mark.parametrize(
    "function", [goals.mutate_goals, goals.mutate_campaign_goal_configs]
)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"validate_only": "false"},
        {"partial_failure": 1},
        {"login_customer_id": "bad"},
    ],
)
def test_goal_flags_and_manager_validation_precede_client(
    clients, function, kwargs
):
  with pytest.raises(ToolError):
    function("123", [], **kwargs)
  _assert_no_account_access(clients)


@pytest.mark.parametrize(
    "operations",
    [
        [],
        "[]",
        '[{"create":{"retention_goal_settings":{},'
        '"retention_goal_settings":{}}}]',
        [
            {
                "create": {
                    "retention_goal_settings": {},
                    "retentionGoalSettings": {},
                }
            }
        ],
    ],
)
def test_empty_or_duplicate_goal_requests_rejected(clients, operations):
  with pytest.raises(ToolError):
    goals.mutate_goals("123", operations)
  _assert_no_account_access(clients)


@pytest.mark.parametrize(
    "setting",
    [
        "campaign_retention_settings",
        "campaign_new_customer_acquisition_settings",
        "campaign_loyalty_retention_settings",
    ],
)
def test_campaign_configs_allow_conversion_owner_goal_reference(
    clients, setting
):
  goals.mutate_campaign_goal_configs(
      "123",
      [
          {
              "create": {
                  "campaign": "customers/123/campaigns/7",
                  "goal": "customers/999/goals/4",
                  setting: {
                      "value_settings_override": {"value_multiplier": 2.0}
                  },
              }
          }
      ],
  )
  request = _request(clients, "goals", "mutate_campaign_goal_configs")
  assert request.operations[0].create.goal == "customers/999/goals/4"
  assert request.validate_only is True


def test_loyalty_flags_update_false_and_native_partial_failure_receipt(
    clients,
):
  service = clients["goals"].return_value.get_service.return_value
  service.mutate_campaign_goal_configs.return_value = (
      MutateCampaignGoalConfigsResponse(
          results=[{"resource_name": "customers/123/campaignGoalConfigs/7~4"}],
          partial_failure_error={
              "code": 3,
              "message": "Second operation failed.",
          },
      )
  )
  result = goals.mutate_campaign_goal_configs(
      "123",
      [
          {
              "update": {
                  "resource_name": "customers/123/campaignGoalConfigs/7~4",
                  "campaign_loyalty_retention_settings": {
                      "enable_bid_adjustments_for_loyalty_members": False,
                      "show_targeted_loyalty_member_benefits_in_pla": True,
                  },
              },
              "update_mask": "campaignLoyaltyRetentionSettings",
          }
      ],
      validate_only=False,
      partial_failure=True,
  )
  request = _request(clients, "goals", "mutate_campaign_goal_configs")
  assert request.validate_only is False
  assert request.partial_failure is True
  loyalty = request.operations[0].update.campaign_loyalty_retention_settings
  assert loyalty.show_targeted_loyalty_member_benefits_in_pla is True
  assert result["response"]["partial_failure_error"]["code"] == 3
  assert result["returned_result_count"] == 1


def test_campaign_config_remove_supported_without_fabricated_result(clients):
  result = goals.mutate_campaign_goal_configs(
      "123", [{"remove": "customers/123/campaignGoalConfigs/7~4"}]
  )
  assert (
      _request(clients, "goals", "mutate_campaign_goal_configs")
      .operations[0]
      .remove
      == "customers/123/campaignGoalConfigs/7~4"
  )
  assert result["returned_result_count"] == 0


@pytest.mark.parametrize(
    "operation",
    [
        {
            "create": {
                "campaign": "customers/999/campaigns/7",
                "goal": "customers/123/goals/4",
                "campaign_retention_settings": {},
            }
        },
        {
            "create": {
                "campaign": "customers/123/campaigns/7",
                "goal": "bad",
                "campaign_retention_settings": {},
            }
        },
        {
            "create": {
                "campaign": "customers/123/campaigns/7",
                "goal": "customers/123/goals/4",
            }
        },
        {
            "create": {
                "resource_name": "customers/123/campaignGoalConfigs/8~4",
                "campaign": "customers/123/campaigns/7",
                "goal": "customers/123/goals/4",
                "campaign_retention_settings": {},
            }
        },
        {
            "update": {
                "resource_name": "customers/123/campaignGoalConfigs/7~4",
                "campaign": "customers/123/campaigns/7",
            },
            "update_mask": "campaignRetentionSettings",
        },
        {"remove": "customers/999/campaignGoalConfigs/7~4"},
        {
            "remove": "customers/123/campaignGoalConfigs/7~4",
            "update_mask": "campaignRetentionSettings",
        },
    ],
)
def test_invalid_campaign_config_resources_and_operations_fail_before_client(
    clients, operation
):
  with pytest.raises(ToolError):
    goals.mutate_campaign_goal_configs("123", [operation])
  _assert_no_account_access(clients)


def test_native_google_api_error_converted_without_mutation_retry(clients):
  service = clients["goals"].return_value.get_service.return_value
  service.mutate_goals.side_effect = google_exceptions.InvalidArgument(
      "Rejected goal."
  )
  with pytest.raises(ToolError, match="Rejected goal"):
    goals.mutate_goals("123", [{"create": {"retention_goal_settings": {}}}])
  assert service.mutate_goals.call_count == 1


def test_large_smart_validation_receipt_bounded_with_exact_deferred_export(
    clients,
):
  service = clients["smart"].return_value.get_service.return_value
  value = "Warnings " * 6000
  service.generate_p_max_draft_campaign.return_value = (
      GeneratePMaxDraftCampaignResponse(validated_info=value)
  )
  result = smart_campaigns.generate_pmax_draft_campaign("123", "7")
  assert (
      len(
          json.dumps(
              result, ensure_ascii=False, separators=(",", ":")
          ).encode()
      )
      <= 32768
  )
  token = result["full_materialized_response_export"]["export_call"][
      "arguments"
  ]["snapshot_token"]
  rows = api._get_materialized_snapshot_rows(token)  # pylint: disable=protected-access
  assert any(value in json.dumps(row) for row in rows)
  assert result["generated_resource_names_returned"] is False


@pytest.mark.parametrize("camel_case", [False, True])
@pytest.mark.parametrize("default_value", [False, None])
def test_unmasked_loyalty_default_is_rejected_before_client(
    clients, camel_case, default_value
):
  if camel_case:
    update = {
        "resourceName": "customers/123/campaignGoalConfigs/7~4",
        "campaignLoyaltyRetentionSettings": {
            "enableBidAdjustmentsForLoyaltyMembers": default_value,
            "valueSettingsOverride": {"valueMultiplier": 2},
        },
    }
  else:
    update = {
        "resource_name": "customers/123/campaignGoalConfigs/7~4",
        "campaign_loyalty_retention_settings": {
            "enable_bid_adjustments_for_loyalty_members": default_value,
            "value_settings_override": {"value_multiplier": 2},
        },
    }
  with pytest.raises(
      ToolError,
      match="enable_bid_adjustments_for_loyalty_members is outside update_mask",
  ):
    goals.mutate_campaign_goal_configs(
        "123",
        [
            {
                "update": update,
                "update_mask": (
                    "campaignLoyaltyRetentionSettings."
                    "valueSettingsOverride.valueMultiplier"
                ),
            }
        ],
    )
  _assert_no_account_access(clients)


def test_unmasked_default_enum_rejected_before_client(clients):
  with pytest.raises(ToolError, match="target_option is outside update_mask"):
    goals.mutate_campaign_goal_configs(
        "123",
        [
            {
                "update": {
                    "resource_name": "customers/123/campaignGoalConfigs/7~4",
                    "campaign_new_customer_acquisition_settings": {
                        "target_option": "UNSPECIFIED",
                        "value_settings_override": {"value_multiplier": 2},
                    },
                },
                "update_mask": (
                    "campaignNewCustomerAcquisitionSettings."
                    "valueSettingsOverride.valueMultiplier"
                ),
            }
        ],
    )
  _assert_no_account_access(clients)


@pytest.mark.parametrize("camel_case", [False, True])
def test_masked_false_loyalty_leaf_remains_supported(clients, camel_case):
  settings_name = (
      "campaignLoyaltyRetentionSettings"
      if camel_case
      else "campaign_loyalty_retention_settings"
  )
  flag_name = (
      "enableBidAdjustmentsForLoyaltyMembers"
      if camel_case
      else "enable_bid_adjustments_for_loyalty_members"
  )
  goals.mutate_campaign_goal_configs(
      "123",
      [
          {
              "update": {
                  "resource_name": "customers/123/campaignGoalConfigs/7~4",
                  settings_name: {flag_name: False},
              },
              "update_mask": (
                  "campaignLoyaltyRetentionSettings."
                  "enableBidAdjustmentsForLoyaltyMembers"
              ),
          }
      ],
  )
  operation = _request(
      clients, "goals", "mutate_campaign_goal_configs"
  ).operations[0]
  loyalty = operation.update.campaign_loyalty_retention_settings
  assert loyalty.enable_bid_adjustments_for_loyalty_members is False
  assert list(operation.update_mask.paths) == [
      "campaign_loyalty_retention_settings."
      "enable_bid_adjustments_for_loyalty_members"
  ]


def test_mask_only_campaign_config_reset_remains_supported(clients):
  goals.mutate_campaign_goal_configs(
      "123",
      [
          {
              "update": {
                  "resource_name": "customers/123/campaignGoalConfigs/7~4",
              },
              "update_mask": (
                  "campaignLoyaltyRetentionSettings."
                  "enableBidAdjustmentsForLoyaltyMembers"
              ),
          }
      ],
  )
  clients["goals"].assert_called_once()


def test_masked_zero_oneof_leaf_with_camel_aliases_is_preserved(clients):
  goals.mutate_goals(
      "123",
      [
          {
              "update": {
                  "resourceName": "customers/123/goals/4",
                  "newCustomerAcquisitionGoalSettings": {
                      "valueSettings": {"additionalValue": 0},
                  },
              },
              "updateMask": (
                  "newCustomerAcquisitionGoalSettings."
                  "valueSettings.additionalValue"
              ),
          }
      ],
  )
  operation = _request(clients, "goals", "mutate_goals").operations[0]
  native = type(operation.update).pb(operation.update)
  values = native.new_customer_acquisition_goal_settings.value_settings
  assert values.WhichOneof("value_adjustment") == "additional_value"
  assert values.additional_value == 0


def test_mask_cannot_use_oneof_group_name_in_place_of_native_field(clients):
  with pytest.raises(ToolError, match="unsupported native field path"):
    goals.mutate_goals(
        "123",
        [
            {
                "update": {"resource_name": "customers/123/goals/4"},
                "update_mask": {"paths": ["goal_settings"]},
            }
        ],
    )
  _assert_no_account_access(clients)


@pytest.mark.parametrize(
    "error_class",
    [google_exceptions.GoogleAPICallError, google_exceptions.PermissionDenied],
)
def test_video_preflight_api_error_is_tool_error_without_mutation(
    clients, error_class
):
  clients["query"].side_effect = error_class("Cannot read campaign settings.")
  with pytest.raises(ToolError, match="Cannot read campaign settings"):
    assets.update_campaign_video_crawl_settings("123", "7", [])
  clients["query"].assert_called_once()
  clients["assets"].assert_not_called()
  service = clients["assets"].return_value.get_service.return_value
  service.mutate_campaigns.assert_not_called()
