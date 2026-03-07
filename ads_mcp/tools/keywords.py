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


@mcp.tool()
def set_keyword_status(
    customer_id: str,
    ad_group_id: str,
    criterion_id: str,
    status: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Sets a keyword's status.

  status: 'PAUSED' or 'ENABLED'.
  """
  status_upper = status.upper()
  if status_upper not in ("PAUSED", "ENABLED"):
    raise ToolError(
        f"Invalid status '{status}'. Use 'PAUSED' or 'ENABLED'."
    )

  ads_client = get_ads_client(login_customer_id)
  criterion_service = ads_client.get_service("AdGroupCriterionService")

  operation = ads_client.get_type("AdGroupCriterionOperation")
  criterion = operation.update
  criterion.resource_name = criterion_service.ad_group_criterion_path(
      customer_id, ad_group_id, criterion_id
  )
  criterion.status = getattr(
      ads_client.enums.AdGroupCriterionStatusEnum, status_upper
  )
  operation.update_mask.paths.append("status")

  try:
    response = criterion_service.mutate_ad_group_criteria(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@mcp.tool()
def update_keyword_bid(
    customer_id: str,
    ad_group_id: str,
    criterion_id: str,
    cpc_bid_micros: int,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates the CPC bid for a keyword.

  cpc_bid_micros: 1 dollar = 1,000,000 micros.
  """
  ads_client = get_ads_client(login_customer_id)
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
