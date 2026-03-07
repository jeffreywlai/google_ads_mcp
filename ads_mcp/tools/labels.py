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
from ads_mcp.tools.api import get_ads_client


@mcp.tool()
def create_label(
    customer_id: str,
    name: str,
    description: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Creates a new label for organizing Google Ads resources.

  Labels can be applied to campaigns, ad groups, ads, and keywords
  using the apply_label_to_campaigns and apply_label_to_ad_groups
  tools.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      name: The name of the label.
      description: Optional description for the label.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the created label.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
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


@mcp.tool()
def delete_label(
    customer_id: str,
    label_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Deletes a label.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      label_id: The ID of the label to delete.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_name of the deleted label.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
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


@mcp.tool()
def apply_label_to_campaigns(
    customer_id: str,
    label_id: str,
    campaign_ids: list[str],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Applies a label to one or more campaigns.

  Use execute_gaql to find label IDs:
    SELECT label.id, label.name FROM label

  Args:
      customer_id: The Google Ads customer ID (digits only).
      label_id: The ID of the label to apply.
      campaign_ids: List of campaign IDs to label.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_names of the created campaign labels.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  campaign_label_service = ads_client.get_service("CampaignLabelService")
  campaign_service = ads_client.get_service("CampaignService")
  label_service = ads_client.get_service("LabelService")

  label_resource = label_service.label_path(customer_id, label_id)
  operations = []
  for campaign_id in campaign_ids:
    operation = ads_client.get_type("CampaignLabelOperation")
    campaign_label = operation.create
    campaign_label.campaign = campaign_service.campaign_path(
        customer_id, campaign_id
    )
    campaign_label.label = label_resource
    operations.append(operation)

  try:
    response = campaign_label_service.mutate_campaign_labels(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_names": [r.resource_name for r in response.results]}


@mcp.tool()
def remove_label_from_campaigns(
    customer_id: str,
    label_id: str,
    campaign_ids: list[str],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes a label from one or more campaigns.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      label_id: The ID of the label to remove.
      campaign_ids: List of campaign IDs to remove the label from.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_names of the removed campaign labels.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  campaign_label_service = ads_client.get_service("CampaignLabelService")

  operations = []
  for campaign_id in campaign_ids:
    operation = ads_client.get_type("CampaignLabelOperation")
    operation.remove = campaign_label_service.campaign_label_path(
        customer_id, campaign_id, label_id
    )
    operations.append(operation)

  try:
    response = campaign_label_service.mutate_campaign_labels(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_names": [r.resource_name for r in response.results]}


@mcp.tool()
def apply_label_to_ad_groups(
    customer_id: str,
    label_id: str,
    ad_group_ids: list[str],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Applies a label to one or more ad groups.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      label_id: The ID of the label to apply.
      ad_group_ids: List of ad group IDs to label.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_names of the created ad group labels.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  ad_group_label_service = ads_client.get_service("AdGroupLabelService")
  ad_group_service = ads_client.get_service("AdGroupService")
  label_service = ads_client.get_service("LabelService")

  label_resource = label_service.label_path(customer_id, label_id)
  operations = []
  for ad_group_id in ad_group_ids:
    operation = ads_client.get_type("AdGroupLabelOperation")
    ad_group_label = operation.create
    ad_group_label.ad_group = ad_group_service.ad_group_path(
        customer_id, ad_group_id
    )
    ad_group_label.label = label_resource
    operations.append(operation)

  try:
    response = ad_group_label_service.mutate_ad_group_labels(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_names": [r.resource_name for r in response.results]}


@mcp.tool()
def remove_label_from_ad_groups(
    customer_id: str,
    label_id: str,
    ad_group_ids: list[str],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes a label from one or more ad groups.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      label_id: The ID of the label to remove.
      ad_group_ids: List of ad group IDs to remove the label from.
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with the resource_names of the removed ad group labels.
  """
  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id
  ad_group_label_service = ads_client.get_service("AdGroupLabelService")

  operations = []
  for ad_group_id in ad_group_ids:
    operation = ads_client.get_type("AdGroupLabelOperation")
    operation.remove = ad_group_label_service.ad_group_label_path(
        customer_id, ad_group_id, label_id
    )
    operations.append(operation)

  try:
    response = ad_group_label_service.mutate_ad_group_labels(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_names": [r.resource_name for r in response.results]}
