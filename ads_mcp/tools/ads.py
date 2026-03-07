# Copyright 2025 Google LLC
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

"""Tools for managing ads in Google Ads."""

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tools.api import get_ads_client


def _update_ad_status(
    customer_id: str,
    ad_group_id: str,
    ad_id: str,
    status_value,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates the status of an ad group ad."""
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  ad_group_ad_service = ads_client.get_service("AdGroupAdService")

  operation = ads_client.get_type("AdGroupAdOperation")
  ad_group_ad = operation.update
  ad_group_ad.resource_name = ad_group_ad_service.ad_group_ad_path(
      customer_id, ad_group_id, ad_id
  )
  ad_group_ad.status = status_value
  operation.update_mask.paths.append("status")

  try:
    response = ad_group_ad_service.mutate_ad_group_ads(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@mcp.tool()
def pause_ad(
    customer_id: str,
    ad_group_id: str,
    ad_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Pauses an ad in an ad group.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      ad_group_id: The ad group ID containing the ad.
      ad_id: The ID of the ad to pause.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated ad.
  """
  ads_client = get_ads_client()
  return _update_ad_status(
      customer_id,
      ad_group_id,
      ad_id,
      ads_client.enums.AdGroupAdStatusEnum.PAUSED,
      login_customer_id,
  )


@mcp.tool()
def enable_ad(
    customer_id: str,
    ad_group_id: str,
    ad_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Enables a paused ad in an ad group.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      ad_group_id: The ad group ID containing the ad.
      ad_id: The ID of the ad to enable.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated ad.
  """
  ads_client = get_ads_client()
  return _update_ad_status(
      customer_id,
      ad_group_id,
      ad_id,
      ads_client.enums.AdGroupAdStatusEnum.ENABLED,
      login_customer_id,
  )
