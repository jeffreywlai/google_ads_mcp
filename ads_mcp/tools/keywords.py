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

"""Tools for managing ad group keywords in Google Ads."""

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tools.api import get_ads_client


def _update_keyword_status(
    customer_id: str,
    ad_group_id: str,
    criterion_id: str,
    status_value,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates the status of an ad group keyword."""
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  criterion_service = ads_client.get_service("AdGroupCriterionService")

  operation = ads_client.get_type("AdGroupCriterionOperation")
  criterion = operation.update
  criterion.resource_name = criterion_service.ad_group_criterion_path(
      customer_id, ad_group_id, criterion_id
  )
  criterion.status = status_value
  operation.update_mask.paths.append("status")

  try:
    response = criterion_service.mutate_ad_group_criteria(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@mcp.tool()
def pause_keyword(
    customer_id: str,
    ad_group_id: str,
    criterion_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Pauses a keyword in an ad group.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      ad_group_id: The ad group ID containing the keyword.
      criterion_id: The criterion ID of the keyword to pause.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated keyword.
  """
  ads_client = get_ads_client()
  return _update_keyword_status(
      customer_id,
      ad_group_id,
      criterion_id,
      ads_client.enums.AdGroupCriterionStatusEnum.PAUSED,
      login_customer_id,
  )


@mcp.tool()
def enable_keyword(
    customer_id: str,
    ad_group_id: str,
    criterion_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Enables a paused keyword in an ad group.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      ad_group_id: The ad group ID containing the keyword.
      criterion_id: The criterion ID of the keyword to enable.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated keyword.
  """
  ads_client = get_ads_client()
  return _update_keyword_status(
      customer_id,
      ad_group_id,
      criterion_id,
      ads_client.enums.AdGroupCriterionStatusEnum.ENABLED,
      login_customer_id,
  )


@mcp.tool()
def update_keyword_bid(
    customer_id: str,
    ad_group_id: str,
    criterion_id: str,
    cpc_bid_micros: int,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates the CPC bid for a keyword in an ad group.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      ad_group_id: The ad group ID containing the keyword.
      criterion_id: The criterion ID of the keyword to update.
      cpc_bid_micros: The new CPC bid in micros
          (1 dollar = 1,000,000 micros).
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated keyword.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  criterion_service = ads_client.get_service("AdGroupCriterionService")

  operation = ads_client.get_type("AdGroupCriterionOperation")
  criterion = operation.update
  criterion.resource_name = criterion_service.ad_group_criterion_path(
      customer_id, ad_group_id, criterion_id
  )
  criterion.cpc_bid_micros = cpc_bid_micros
  operation.update_mask.paths.append("cpc_bid_micros")

  try:
    response = criterion_service.mutate_ad_group_criteria(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}
