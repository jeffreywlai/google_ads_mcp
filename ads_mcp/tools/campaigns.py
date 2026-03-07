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

"""Tools for managing campaigns and budgets in Google Ads."""

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tools.api import get_ads_client


@mcp.tool()
def pause_campaign(
    customer_id: str,
    campaign_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Pauses a Google Ads campaign.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      campaign_id: The ID of the campaign to pause.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated campaign.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  campaign_service = ads_client.get_service("CampaignService")

  operation = ads_client.get_type("CampaignOperation")
  campaign = operation.update
  campaign.resource_name = campaign_service.campaign_path(
      customer_id, campaign_id
  )
  campaign.status = ads_client.enums.CampaignStatusEnum.PAUSED
  operation.update_mask.paths.append("status")

  try:
    response = campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@mcp.tool()
def resume_campaign(
    customer_id: str,
    campaign_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Resumes (enables) a paused Google Ads campaign.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      campaign_id: The ID of the campaign to resume.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated campaign.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  campaign_service = ads_client.get_service("CampaignService")

  operation = ads_client.get_type("CampaignOperation")
  campaign = operation.update
  campaign.resource_name = campaign_service.campaign_path(
      customer_id, campaign_id
  )
  campaign.status = ads_client.enums.CampaignStatusEnum.ENABLED
  operation.update_mask.paths.append("status")

  try:
    response = campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@mcp.tool()
def update_campaign_budget(
    customer_id: str,
    budget_id: str,
    amount_micros: int,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates the daily amount of a campaign budget.

  Use execute_gaql to find the budget ID:
    SELECT campaign_budget.id, campaign_budget.amount_micros
    FROM campaign_budget

  Args:
      customer_id: The Google Ads customer ID (digits only).
      budget_id: The ID of the campaign budget to update.
      amount_micros: The new daily budget amount in micros
          (1 dollar = 1,000,000 micros).
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the updated budget.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  budget_service = ads_client.get_service("CampaignBudgetService")

  operation = ads_client.get_type("CampaignBudgetOperation")
  budget = operation.update
  budget.resource_name = budget_service.campaign_budget_path(
      customer_id, budget_id
  )
  budget.amount_micros = amount_micros
  operation.update_mask.paths.append("amount_micros")

  try:
    response = budget_service.mutate_campaign_budgets(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}
