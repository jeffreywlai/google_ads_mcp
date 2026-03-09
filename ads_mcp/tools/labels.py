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

"""Tools for managing labels in Google Ads."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools.api import get_ads_client


label_tool = ads_mutation_tool(mcp, tags={"labels"})
destructive_label_tool = ads_mutation_tool(
    mcp,
    tags={"labels"},
    destructive=True,
)


@label_tool
def create_label(
    customer_id: str,
    name: str,
    description: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Creates a new label for organizing Google Ads resources."""
  ads_client = get_ads_client(login_customer_id)
  label_service = ads_client.get_service("LabelService")

  operation = ads_client.get_type("LabelOperation")
  label = operation.create
  label.name = name
  if description:
    label.text_label.description = description

  try:
    response = label_service.mutate_labels(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@destructive_label_tool
def delete_label(
    customer_id: str,
    label_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Deletes a label."""
  ads_client = get_ads_client(login_customer_id)
  label_service = ads_client.get_service("LabelService")

  operation = ads_client.get_type("LabelOperation")
  operation.remove = label_service.label_path(customer_id, label_id)

  try:
    response = label_service.mutate_labels(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@label_tool
def manage_campaign_labels(
    customer_id: str,
    label_id: str,
    campaign_ids: list[str],
    action: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Applies or removes a label to/from campaigns.

  action: 'APPLY' or 'REMOVE'.
  Use execute_gaql to find label IDs:
    SELECT label.id, label.name FROM label
  """
  ads_client = get_ads_client(login_customer_id)
  campaign_label_service = ads_client.get_service("CampaignLabelService")

  operations = []
  if action.upper() == "APPLY":
    campaign_service = ads_client.get_service("CampaignService")
    label_service = ads_client.get_service("LabelService")
    label_resource = label_service.label_path(customer_id, label_id)
    for campaign_id in campaign_ids:
      operation = ads_client.get_type("CampaignLabelOperation")
      campaign_label = operation.create
      campaign_label.campaign = campaign_service.campaign_path(
          customer_id, campaign_id
      )
      campaign_label.label = label_resource
      operations.append(operation)
  elif action.upper() == "REMOVE":
    for campaign_id in campaign_ids:
      operation = ads_client.get_type("CampaignLabelOperation")
      operation.remove = campaign_label_service.campaign_label_path(
          customer_id, campaign_id, label_id
      )
      operations.append(operation)
  else:
    raise ToolError(f"Invalid action '{action}'. Use 'APPLY' or 'REMOVE'.")

  try:
    response = campaign_label_service.mutate_campaign_labels(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_names": [r.resource_name for r in response.results]}


@label_tool
def manage_ad_group_labels(
    customer_id: str,
    label_id: str,
    ad_group_ids: list[str],
    action: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Applies or removes a label to/from ad groups.

  action: 'APPLY' or 'REMOVE'.
  """
  ads_client = get_ads_client(login_customer_id)
  ad_group_label_service = ads_client.get_service("AdGroupLabelService")

  operations = []
  if action.upper() == "APPLY":
    ad_group_service = ads_client.get_service("AdGroupService")
    label_service = ads_client.get_service("LabelService")
    label_resource = label_service.label_path(customer_id, label_id)
    for ad_group_id in ad_group_ids:
      operation = ads_client.get_type("AdGroupLabelOperation")
      ad_group_label = operation.create
      ad_group_label.ad_group = ad_group_service.ad_group_path(
          customer_id, ad_group_id
      )
      ad_group_label.label = label_resource
      operations.append(operation)
  elif action.upper() == "REMOVE":
    for ad_group_id in ad_group_ids:
      operation = ads_client.get_type("AdGroupLabelOperation")
      operation.remove = ad_group_label_service.ad_group_label_path(
          customer_id, ad_group_id, label_id
      )
      operations.append(operation)
  else:
    raise ToolError(f"Invalid action '{action}'. Use 'APPLY' or 'REMOVE'.")

  try:
    response = ad_group_label_service.mutate_ad_group_labels(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_names": [r.resource_name for r in response.results]}
