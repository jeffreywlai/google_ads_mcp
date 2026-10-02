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

"""Focused asset URL, video crawl, and advertiser-attestation mutations."""

from typing import Any
from urllib.parse import urlsplit

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.ad_service import MutateAdsRequest
from google.ads.googleads.v25.services.types.asset_group_service import MutateAssetGroupsRequest
from google.ads.googleads.v25.services.types.asset_service import MutateAssetsRequest
from google.ads.googleads.v25.services.types.campaign_service import MutateCampaignsRequest
from pydantic import StrictBool

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import quote_int_value
from ads_mcp.tools._service import normalized_account_id
from ads_mcp.tools._service import parse_service_request
from ads_mcp.tools._service import service_response_dict
from ads_mcp.tools.api import finalize_bounded_response
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors
from ads_mcp.tools.api import INLINE_PAGE_BYTE_LIMIT
from ads_mcp.tools.api import run_gaql_query


asset_mutation_tool = ads_mutation_tool(mcp, tags={"assets"})


def _resource(customer_id: str, collection: str, value: str) -> str:
  value = quote_int_value(value, f"{collection}_id")
  if int(value) <= 0:
    raise ToolError(f"{collection}_id must be a positive ID.")
  return f"customers/{customer_id}/{collection}/{value}"


def _validate_boolean(value: bool, name: str = "validate_only") -> None:
  if not isinstance(value, bool):
    raise ToolError(f"{name} must be a boolean.")


def _receipt(
    response, customer_id: str, validate_only: bool, **metadata
) -> dict[str, Any]:
  payload = service_response_dict(response)
  result = {
      "customer_id": customer_id,
      "validate_only": validate_only,
      "executed": not validate_only,
      "requested_operation_count": 1,
      "returned_result_count": len(payload.get("results", [])),
      "response": payload,
      **metadata,
  }
  return finalize_bounded_response(
      result, ("response",), max_bytes=INLINE_PAGE_BYTE_LIMIT
  )


@asset_mutation_tool
def update_asset_group_url_options(
    customer_id: str,
    asset_group_id: str,
    tracking_url_template: str | None = None,
    final_url_suffix: str | None = None,
    url_custom_parameters: list[dict[str, str]] | str | None = None,
    validate_only: StrictBool = True,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Updates v25.2 asset-group URL options, validating by default.

  Args:
      customer_id: Customer owning the asset group.
      asset_group_id: Positive asset-group ID.
      tracking_url_template: Tracking template; empty string clears it.
      final_url_suffix: Final URL suffix; empty string clears it.
      url_custom_parameters: Replacement list of key/value mappings or JSON
          array. Empty list clears parameters; omitted options are preserved.
      validate_only: True validates without applying any account change.
      login_customer_id: Optional manager account ID.

  Returns:
      Validation/execution receipt and complete bounded native API response.
  """
  _validate_boolean(validate_only)
  customer_id = normalized_account_id(customer_id, "customer_id")
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(
        login_customer_id, "login_customer_id"
    )
  resource_name = _resource(customer_id, "assetGroups", asset_group_id)
  update = {"resource_name": resource_name}
  mask = []
  for field, value, json_path in (
      ("tracking_url_template", tracking_url_template, "trackingUrlTemplate"),
      ("final_url_suffix", final_url_suffix, "finalUrlSuffix"),
  ):
    if value is not None:
      if not isinstance(value, str):
        raise ToolError(f"{field} must be a string.")
      update[field] = value
      mask.append(json_path)
  if url_custom_parameters is not None:
    parameters = normalize_list_arg(
        url_custom_parameters, "url_custom_parameters"
    )
    keys = set()
    for parameter in parameters:
      if (
          not isinstance(parameter, dict)
          or set(parameter) != {"key", "value"}
          or not isinstance(parameter["key"], str)
          or not parameter["key"]
          or not isinstance(parameter["value"], str)
      ):
        raise ToolError("Custom parameters require string key/value pairs.")
      if parameter["key"] in keys:
        raise ToolError("Custom parameter keys must be unique.")
      keys.add(parameter["key"])
    update["url_custom_parameters"] = parameters
    mask.append("urlCustomParameters")
  if not mask:
    raise ToolError("Supply at least one URL option to update.")
  request = parse_service_request(
      {
          "customer_id": customer_id,
          "validate_only": validate_only,
          "operations": [{"update": update, "update_mask": ",".join(mask)}],
      },
      MutateAssetGroupsRequest,
  )
  client = get_ads_client(login_customer_id)
  service = client.get_service("AssetGroupService")
  with handle_google_ads_errors():
    response = service.mutate_asset_groups(request=request)
  return _receipt(response, customer_id, validate_only)


@asset_mutation_tool
def update_campaign_video_crawl_settings(
    customer_id: str,
    campaign_id: str,
    automated_video_crawl_infos: list[dict[str, Any]] | str,
    asset_automation_status: str = "OPTED_IN",
    validate_only: StrictBool = True,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Updates v25.2 automated video crawling while preserving other settings.

  Validates the new entry before accessing an account, then reads the complete
  current automation list and replaces only AUTOMATED_VIDEO_CRAWL. The API
  replaces this repeated field as a whole; concurrent edits require care.

  Args:
      customer_id: Customer owning the campaign.
      campaign_id: Positive Performance Max campaign ID.
      automated_video_crawl_infos: Replacement list or JSON array of objects
          with url, source_platform (LANDING_PAGE, SOCIAL, YOUTUBE), and boolean
          enabled. Empty list clears crawl sources.
      asset_automation_status: OPTED_IN or OPTED_OUT for automated video crawl.
      validate_only: True validates without applying any account change.
      login_customer_id: Optional manager account ID.

  Returns:
      Validation/execution receipt and number of preserved automation entries.
  """
  _validate_boolean(validate_only)
  customer_id = normalized_account_id(customer_id, "customer_id")
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(
        login_customer_id, "login_customer_id"
    )
  resource_name = _resource(customer_id, "campaigns", campaign_id)
  infos = normalize_list_arg(
      automated_video_crawl_infos, "automated_video_crawl_infos"
  )
  if not isinstance(
      asset_automation_status, str
  ) or asset_automation_status not in {"OPTED_IN", "OPTED_OUT"}:
    raise ToolError("asset_automation_status must be OPTED_IN or OPTED_OUT.")
  for info in infos:
    if not isinstance(info, dict) or set(info) != {
        "url",
        "source_platform",
        "enabled",
    }:
      raise ToolError(
          "Each crawl source requires url, source_platform, enabled."
      )
    _validate_boolean(info["enabled"], "enabled")
    if not isinstance(info["source_platform"], str) or info[
        "source_platform"
    ] not in {"LANDING_PAGE", "SOCIAL", "YOUTUBE"}:
      raise ToolError("Unsupported video crawl source_platform.")
    try:
      parsed = urlsplit(info["url"]) if isinstance(info["url"], str) else None
    except ValueError as exc:
      raise ToolError("Invalid video crawl URL.") from exc
    if (
        not parsed
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
      raise ToolError(
          "Video crawl URLs require HTTP(S), a host, and no credentials."
      )
  video_setting = {
      "asset_automation_type": "AUTOMATED_VIDEO_CRAWL",
      "asset_automation_status": asset_automation_status,
      "automated_video_crawl_setting": {"automated_video_crawl_infos": infos},
  }
  request = parse_service_request(
      {
          "customer_id": customer_id,
          "validate_only": validate_only,
          "operations": [
              {
                  "update": {
                      "resource_name": resource_name,
                      "asset_automation_settings": [video_setting],
                  },
                  "update_mask": "assetAutomationSettings",
              }
          ],
      },
      MutateCampaignsRequest,
  )
  with handle_google_ads_errors():
    rows = run_gaql_query(
        "SELECT campaign.resource_name, campaign.advertising_channel_type, "
        "campaign.asset_automation_settings FROM campaign WHERE campaign.id = "
        + quote_int_value(campaign_id, "campaign_id"),
        customer_id,
        login_customer_id,
    )
  if len(rows) != 1 or rows[0].get("campaign.resource_name") != resource_name:
    raise ToolError("No unique campaign was returned for the requested ID.")
  if rows[0].get("campaign.advertising_channel_type") != "PERFORMANCE_MAX":
    raise ToolError(
        "Automated video crawl requires a Performance Max campaign."
    )
  current = rows[0].get("campaign.asset_automation_settings")
  if not isinstance(current, list) or not all(
      isinstance(setting, dict) for setting in current
  ):
    raise ToolError("Campaign automation settings were not returned.")
  preserved = [
      setting
      for setting in current
      if setting.get(
          "asset_automation_type", setting.get("assetAutomationType")
      )
      != "AUTOMATED_VIDEO_CRAWL"
  ]
  merged = parse_service_request(
      {
          "customer_id": customer_id,
          "validate_only": validate_only,
          "operations": [
              {
                  "update": {
                      "resource_name": resource_name,
                      "asset_automation_settings": preserved + [video_setting],
                  },
                  "update_mask": "assetAutomationSettings",
              }
          ],
      },
      MutateCampaignsRequest,
  )
  request = merged
  client = get_ads_client(login_customer_id)
  service = client.get_service("CampaignService")
  with handle_google_ads_errors():
    response = service.mutate_campaigns(request=request)
  return _receipt(
      response,
      customer_id,
      validate_only,
      preserved_automation_setting_count=len(preserved),
  )


def _update_attestation(
    customer_id,
    entity_id,
    status,
    validate_only,
    login_customer_id,
    *,
    collection,
    request_type,
    service_name,
    method_name,
):
  _validate_boolean(validate_only)
  customer_id = normalized_account_id(customer_id, "customer_id")
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(
        login_customer_id, "login_customer_id"
    )
  if not isinstance(status, str) or status not in {
      "IS_SYNTHETIC",
      "NOT_SYNTHETIC",
  }:
    raise ToolError(
        "attestation_status must be IS_SYNTHETIC or NOT_SYNTHETIC."
    )
  resource_name = _resource(customer_id, collection, entity_id)
  request = parse_service_request(
      {
          "customer_id": customer_id,
          "validate_only": validate_only,
          "operations": [
              {
                  "update": {
                      "resource_name": resource_name,
                      "synthetic_content_info": {
                          "advertiser_attestation": {
                              "status": status,
                              "source": "ADVERTISER_ATTESTED",
                          }
                      },
                  },
                  "update_mask": "syntheticContentInfo.advertiserAttestation",
              }
          ],
      },
      request_type,
  )
  client = get_ads_client(login_customer_id)
  service = client.get_service(service_name)
  with handle_google_ads_errors():
    response = getattr(service, method_name)(request=request)
  return _receipt(response, customer_id, validate_only)


@asset_mutation_tool
def update_ad_synthetic_content_info(
    customer_id: str,
    ad_id: str,
    attestation_status: str,
    validate_only: StrictBool = True,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Updates only an ad's advertiser synthetic-content attestation.

  Args:
      customer_id: Customer owning the ad.
      ad_id: Positive ad ID.
      attestation_status: IS_SYNTHETIC or NOT_SYNTHETIC.
      validate_only: True validates without applying any account change.
      login_customer_id: Optional manager account ID.

  Returns:
      Validation/execution receipt. Google system attestation is preserved.
  """
  return _update_attestation(
      customer_id,
      ad_id,
      attestation_status,
      validate_only,
      login_customer_id,
      collection="ads",
      request_type=MutateAdsRequest,
      service_name="AdService",
      method_name="mutate_ads",
  )


@asset_mutation_tool
def update_asset_synthetic_content_info(
    customer_id: str,
    asset_id: str,
    attestation_status: str,
    validate_only: StrictBool = True,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Updates only an asset's advertiser synthetic-content attestation.

  Args:
      customer_id: Customer owning the asset.
      asset_id: Positive asset ID; IMAGE, MEDIA_BUNDLE, or YOUTUBE_VIDEO assets
          are eligible. Google validates type eligibility.
      attestation_status: IS_SYNTHETIC or NOT_SYNTHETIC.
      validate_only: True validates without applying any account change.
      login_customer_id: Optional manager account ID.

  Returns:
      Validation/execution receipt. Google system attestation is preserved.
  """
  return _update_attestation(
      customer_id,
      asset_id,
      attestation_status,
      validate_only,
      login_customer_id,
      collection="assets",
      request_type=MutateAssetsRequest,
      service_name="AssetService",
      method_name="mutate_assets",
  )
