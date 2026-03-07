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

"""Tools for managing negative keyword lists in Google Ads."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tools.api import get_ads_client


# ---------------------------------------------------------------------------
# Shared Negative Keyword Lists (SharedSet)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_shared_sets(
    customer_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists all shared negative keyword lists for a customer."""
  ads_client = get_ads_client(login_customer_id)
  ads_service = ads_client.get_service("GoogleAdsService")

  query = """
      SELECT
        shared_set.id,
        shared_set.name,
        shared_set.member_count
      FROM shared_set
      WHERE shared_set.type = 'NEGATIVE_KEYWORDS'
        AND shared_set.status = 'ENABLED'
  """

  try:
    response = ads_service.search_stream(query=query, customer_id=customer_id)
    results = []
    for batch in response:
      for row in batch.results:
        results.append(
            {
                "id": str(row.shared_set.id),
                "name": row.shared_set.name,
                "member_count": row.shared_set.member_count,
            }
        )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"shared_sets": results}


@mcp.tool()
def create_shared_set(
    customer_id: str,
    name: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Creates a new shared negative keyword list."""
  ads_client = get_ads_client(login_customer_id)
  shared_set_service = ads_client.get_service("SharedSetService")

  operation = ads_client.get_type("SharedSetOperation")
  shared_set = operation.create
  shared_set.name = name
  shared_set.type_ = ads_client.enums.SharedSetTypeEnum.NEGATIVE_KEYWORDS

  try:
    response = shared_set_service.mutate_shared_sets(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@mcp.tool()
def delete_shared_set(
    customer_id: str,
    shared_set_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Deletes a shared negative keyword list."""
  ads_client = get_ads_client(login_customer_id)
  shared_set_service = ads_client.get_service("SharedSetService")

  resource_name = shared_set_service.shared_set_path(
      customer_id, shared_set_id
  )
  operation = ads_client.get_type("SharedSetOperation")
  operation.remove = resource_name

  try:
    response = shared_set_service.mutate_shared_sets(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


# ---------------------------------------------------------------------------
# Keywords in Shared Sets (SharedCriterion)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_shared_set_keywords(
    customer_id: str,
    shared_set_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists all keywords in a shared negative keyword list."""
  ads_client = get_ads_client(login_customer_id)
  ads_service = ads_client.get_service("GoogleAdsService")

  query = f"""
      SELECT
        shared_criterion.criterion_id,
        shared_criterion.keyword.text,
        shared_criterion.keyword.match_type
      FROM shared_criterion
      WHERE shared_set.id = {shared_set_id}
  """

  try:
    response = ads_service.search_stream(query=query, customer_id=customer_id)
    results = []
    for batch in response:
      for row in batch.results:
        results.append(
            {
                "criterion_id": str(row.shared_criterion.criterion_id),
                "text": row.shared_criterion.keyword.text,
                "match_type": row.shared_criterion.keyword.match_type.name,
            }
        )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"keywords": results}


@mcp.tool()
def add_shared_set_keywords(
    customer_id: str,
    shared_set_id: str,
    keywords: list[dict[str, str]],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Adds negative keywords to a shared negative keyword list.

  keywords: list of dicts with 'text' and 'match_type' (BROAD/PHRASE/EXACT).
  """
  ads_client = get_ads_client(login_customer_id)
  shared_criterion_service = ads_client.get_service("SharedCriterionService")
  shared_set_service = ads_client.get_service("SharedSetService")

  shared_set_resource = shared_set_service.shared_set_path(
      customer_id, shared_set_id
  )

  operations = []
  for kw in keywords:
    operation = ads_client.get_type("SharedCriterionOperation")
    criterion = operation.create
    criterion.shared_set = shared_set_resource
    criterion.keyword.text = kw["text"]
    criterion.keyword.match_type = ads_client.enums.KeywordMatchTypeEnum[
        kw["match_type"].upper()
    ].value

    operations.append(operation)

  try:
    response = shared_criterion_service.mutate_shared_criteria(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {
      "resource_names": [r.resource_name for r in response.results],
  }


@mcp.tool()
def remove_shared_set_keywords(
    customer_id: str,
    shared_set_id: str,
    criterion_ids: list[str],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes keywords from a shared negative keyword list by criterion ID."""
  ads_client = get_ads_client(login_customer_id)
  shared_criterion_service = ads_client.get_service("SharedCriterionService")

  operations = []
  for criterion_id in criterion_ids:
    resource_name = (
        f"customers/{customer_id}/sharedCriteria/"
        f"{shared_set_id}~{criterion_id}"
    )
    operation = ads_client.get_type("SharedCriterionOperation")
    operation.remove = resource_name
    operations.append(operation)

  try:
    response = shared_criterion_service.mutate_shared_criteria(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {
      "resource_names": [r.resource_name for r in response.results],
  }


# ---------------------------------------------------------------------------
# Campaign-to-SharedSet Links (CampaignSharedSet)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_campaign_shared_sets(
    customer_id: str,
    campaign_id: str | None = None,
    shared_set_id: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign-to-shared-set links for negative keyword lists."""
  ads_client = get_ads_client(login_customer_id)
  ads_service = ads_client.get_service("GoogleAdsService")

  query = """
      SELECT
        campaign.id,
        campaign.name,
        shared_set.id,
        shared_set.name
      FROM campaign_shared_set
      WHERE shared_set.type = 'NEGATIVE_KEYWORDS'
        AND campaign_shared_set.status = 'ENABLED'
  """
  if campaign_id:
    query += f"  AND campaign.id = {campaign_id}\n"
  if shared_set_id:
    query += f"  AND shared_set.id = {shared_set_id}\n"

  try:
    response = ads_service.search_stream(query=query, customer_id=customer_id)
    results = []
    for batch in response:
      for row in batch.results:
        results.append(
            {
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "shared_set_id": str(row.shared_set.id),
                "shared_set_name": row.shared_set.name,
            }
        )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"campaign_shared_sets": results}


@mcp.tool()
def attach_shared_set_to_campaign(
    customer_id: str,
    campaign_id: str,
    shared_set_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Attaches a shared negative keyword list to a campaign."""
  ads_client = get_ads_client(login_customer_id)
  campaign_shared_set_service = ads_client.get_service(
      "CampaignSharedSetService"
  )
  campaign_service = ads_client.get_service("CampaignService")
  shared_set_service = ads_client.get_service("SharedSetService")

  operation = ads_client.get_type("CampaignSharedSetOperation")
  css = operation.create
  css.campaign = campaign_service.campaign_path(customer_id, campaign_id)
  css.shared_set = shared_set_service.shared_set_path(
      customer_id, shared_set_id
  )

  try:
    response = campaign_shared_set_service.mutate_campaign_shared_sets(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@mcp.tool()
def detach_shared_set_from_campaign(
    customer_id: str,
    campaign_id: str,
    shared_set_id: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Detaches a shared negative keyword list from a campaign."""
  ads_client = get_ads_client(login_customer_id)
  campaign_shared_set_service = ads_client.get_service(
      "CampaignSharedSetService"
  )

  resource_name = (
      f"customers/{customer_id}/campaignSharedSets/"
      f"{campaign_id}~{shared_set_id}"
  )
  operation = ads_client.get_type("CampaignSharedSetOperation")
  operation.remove = resource_name

  try:
    response = campaign_shared_set_service.mutate_campaign_shared_sets(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


# ---------------------------------------------------------------------------
# Campaign-Level Negative Keywords (CampaignCriterion)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_campaign_negative_keywords(
    customer_id: str,
    campaign_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists negative keywords applied directly to a campaign.

  Does not include negatives inherited from shared keyword lists.
  """
  ads_client = get_ads_client(login_customer_id)
  ads_service = ads_client.get_service("GoogleAdsService")

  query = f"""
      SELECT
        campaign_criterion.criterion_id,
        campaign_criterion.keyword.text,
        campaign_criterion.keyword.match_type
      FROM campaign_criterion
      WHERE campaign_criterion.type = 'KEYWORD'
        AND campaign_criterion.negative = TRUE
        AND campaign.id = {campaign_id}
  """

  try:
    response = ads_service.search_stream(query=query, customer_id=customer_id)
    results = []
    for batch in response:
      for row in batch.results:
        results.append(
            {
                "criterion_id": str(row.campaign_criterion.criterion_id),
                "text": row.campaign_criterion.keyword.text,
                "match_type": row.campaign_criterion.keyword.match_type.name,
            }
        )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"keywords": results}


@mcp.tool()
def add_campaign_negative_keywords(
    customer_id: str,
    campaign_id: str,
    keywords: list[dict[str, str]],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Adds negative keywords directly to a campaign.

  keywords: list of dicts with 'text' and 'match_type' (BROAD/PHRASE/EXACT).
  """
  ads_client = get_ads_client(login_customer_id)
  campaign_criterion_service = ads_client.get_service(
      "CampaignCriterionService"
  )
  campaign_service = ads_client.get_service("CampaignService")

  campaign_resource = campaign_service.campaign_path(customer_id, campaign_id)

  operations = []
  for kw in keywords:
    operation = ads_client.get_type("CampaignCriterionOperation")
    criterion = operation.create
    criterion.campaign = campaign_resource
    criterion.negative = True
    criterion.keyword.text = kw["text"]
    criterion.keyword.match_type = ads_client.enums.KeywordMatchTypeEnum[
        kw["match_type"].upper()
    ].value

    operations.append(operation)

  try:
    response = campaign_criterion_service.mutate_campaign_criteria(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {
      "resource_names": [r.resource_name for r in response.results],
  }


@mcp.tool()
def remove_campaign_negative_keywords(
    customer_id: str,
    campaign_id: str,
    criterion_ids: list[str],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes negative keywords from a campaign by criterion ID."""
  ads_client = get_ads_client(login_customer_id)
  campaign_criterion_service = ads_client.get_service(
      "CampaignCriterionService"
  )

  operations = []
  for criterion_id in criterion_ids:
    resource_name = (
        f"customers/{customer_id}/campaignCriteria/"
        f"{campaign_id}~{criterion_id}"
    )
    operation = ads_client.get_type("CampaignCriterionOperation")
    operation.remove = resource_name
    operations.append(operation)

  try:
    response = campaign_criterion_service.mutate_campaign_criteria(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {
      "resource_names": [r.resource_name for r in response.results],
  }
