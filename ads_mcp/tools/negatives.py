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
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import gaql_quote_string
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import preprocess_gaql_query
from ads_mcp.tools._gaql import quote_int_value
from ads_mcp.tools._gaql import require_unique_values
from ads_mcp.tools.api import handle_google_ads_errors
from ads_mcp.tools.api import get_ads_client


# ---------------------------------------------------------------------------
# Shared Negative Keyword Lists (SharedSet)
# ---------------------------------------------------------------------------


negative_read_tool = ads_read_tool(mcp, tags={"negatives"})
negative_tool = ads_mutation_tool(mcp, tags={"negatives"})
destructive_negative_tool = ads_mutation_tool(
    mcp,
    tags={"negatives"},
    destructive=True,
)


def _search_stream(ads_service: Any, query: str, customer_id: str) -> Any:
  """Runs a GAQL search stream after shared local preprocessing."""
  return ads_service.search_stream(
      query=preprocess_gaql_query(query),
      customer_id=customer_id,
  )


def _normalize_criterion_ids(criterion_ids: list[str] | str) -> list[str]:
  """Normalizes destructive criterion ID inputs to integer strings."""
  normalized_values = normalize_list_arg(criterion_ids, "criterion_ids")
  normalized_ids = [
      quote_int_value(criterion_id, "criterion_ids")
      for criterion_id in normalized_values
  ]
  return require_unique_values(normalized_ids, "criterion_ids")


def _list_attached_campaigns(
    ads_client: Any,
    query_customer_id: str,
    shared_set_customer_id: str,
    shared_set_id: str,
) -> list[dict[str, str]]:
  """Returns enabled campaigns in one customer attached to a shared set."""
  ads_service = ads_client.get_service("GoogleAdsService")
  shared_set_resource_name = (
      f"customers/{shared_set_customer_id}/sharedSets/{shared_set_id}"
  )
  query = f"""
      SELECT
        campaign.id,
        campaign.name
      FROM campaign_shared_set
      WHERE campaign_shared_set.shared_set =
        {gaql_quote_string(shared_set_resource_name)}
        AND campaign_shared_set.status = 'ENABLED'
      ORDER BY campaign.name
  """

  with handle_google_ads_errors():
    response = _search_stream(ads_service, query, query_customer_id)
    attached_campaigns = []
    for batch in response:
      for row in batch.results:
        attached_campaigns.append(
            {
                "customer_id": query_customer_id,
                "campaign_id": str(row.campaign.id),
                "name": row.campaign.name,
            }
        )

  return attached_campaigns


def _get_shared_set_reference_count(
    ads_client: Any,
    customer_id: str,
    shared_set_id: str,
) -> int | None:
  """Returns Google's campaign-reference count for a shared set."""
  ads_service = ads_client.get_service("GoogleAdsService")
  query = f"""
      SELECT
        shared_set.reference_count
      FROM shared_set
      WHERE shared_set.id = {shared_set_id}
  """

  with handle_google_ads_errors():
    response = _search_stream(ads_service, query, customer_id)
    for batch in response:
      for row in batch.results:
        return int(row.shared_set.reference_count)

  return None


def _inspect_shared_set_usage(
    ads_client: Any,
    customer_id: str,
    shared_set_id: str,
) -> tuple[int | None, list[dict[str, str]], list[dict[str, str]]]:
  """Best-effort inspection of shared-set attachment state."""
  diagnostic_errors = []
  try:
    reference_count = _get_shared_set_reference_count(
        ads_client,
        customer_id,
        shared_set_id,
    )
  except ToolError as exc:
    reference_count = None
    diagnostic_errors.append(
        {
            "check": "shared_set.reference_count",
            "error": str(exc),
        }
    )

  try:
    attached_campaigns = _list_attached_campaigns(
        ads_client,
        customer_id,
        customer_id,
        shared_set_id,
    )
  except ToolError as exc:
    attached_campaigns = []
    diagnostic_errors.append(
        {
            "check": "campaign_shared_set attachments",
            "error": str(exc),
        }
    )

  return reference_count, attached_campaigns, diagnostic_errors


def _shared_set_in_use_response(
    customer_id: str,
    shared_set_id: str,
    attached_campaigns: list[dict[str, str]],
    reference_count: int | None,
    api_reported_in_use: bool = False,
    diagnostic_errors: list[dict[str, str]] | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Builds an actionable response for an attached shared set."""
  diagnostic_errors = diagnostic_errors or []
  attachments_complete = (
      reference_count is not None
      and reference_count == len(attached_campaigns)
      and not (api_reported_in_use and not attached_campaigns)
      and not diagnostic_errors
  )
  response = {
      "deleted": False,
      "shared_set_id": shared_set_id,
      "reference_count": reference_count,
      "attached_campaigns": attached_campaigns,
      "attachments_complete": attachments_complete,
  }
  known_detach_calls = [
      {
          "tool": "detach_shared_set_from_campaign",
          "arguments": {
              "customer_id": campaign["customer_id"],
              "campaign_id": campaign["campaign_id"],
              "shared_set_id": shared_set_id,
              "login_customer_id": login_customer_id,
          },
      }
      for campaign in attached_campaigns
  ]
  if known_detach_calls:
    response["known_detach_calls"] = known_detach_calls
  if diagnostic_errors:
    response["diagnostic_errors"] = diagnostic_errors
  if attachments_complete:
    response["next_step"] = (
        "Call each entry in known_detach_calls first, then retry "
        "delete_shared_set."
    )
    return response

  if reference_count is not None and reference_count > len(attached_campaigns):
    response["warning"] = (
        f"Google reports {reference_count} campaign references, but only "
        f"{len(attached_campaigns)} were found in customer {customer_id}. "
        "Missing attachments may be in managed client accounts."
    )
  elif api_reported_in_use:
    response["warning"] = (
        "Google returned SHARED_SET_IN_USE, but the attached campaigns could "
        "not be completely enumerated. They may be in managed client "
        "accounts."
    )
  else:
    response["warning"] = (
        "The attached campaigns could not be completely enumerated. Missing "
        "attachments may be in managed client accounts."
    )
  if diagnostic_errors:
    response["warning"] += (
        " One or more diagnostic queries failed; use the next step rather "
        "than retrying the identical delete."
    )
  response["managed_customer_discovery"] = {
      "tool": "execute_gaql",
      "arguments": {
          "customer_id": customer_id,
          "login_customer_id": login_customer_id,
          "query": (
              "SELECT customer_client.id, "
              "customer_client.descriptive_name, customer_client.level "
              "FROM customer_client WHERE customer_client.level > 0"
          ),
      },
  }
  known_detach_step = (
      "Call each entry in known_detach_calls first. "
      if known_detach_calls
      else ""
  )
  login_step = (
      f"use login_customer_id={login_customer_id}"
      if login_customer_id is not None
      else "omit login_customer_id so the configured default is reused"
  )
  response["next_step"] = (
      f"{known_detach_step}Run managed_customer_discovery next. For each "
      "returned client ID, "
      "call list_campaign_shared_sets with that client as customer_id, "
      f"shared_set_id={shared_set_id}, and "
      f"{login_step}. Then call "
      "detach_shared_set_from_campaign with each client customer_id and "
      "campaign_id before retrying delete_shared_set."
  )
  return response


@negative_read_tool
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
    response = _search_stream(ads_service, query, customer_id)
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


@negative_tool
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


@destructive_negative_tool
def delete_shared_set(
    customer_id: str,
    shared_set_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Deletes a shared negative keyword list when no campaigns use it.

  Args:
      customer_id: Google Ads customer ID.
      shared_set_id: Shared negative keyword list ID.
      login_customer_id: Optional manager account ID.

  Returns:
      The deleted resource name, or attached campaigns and the detach-first
      next step when the shared set is still in use.
  """
  shared_set_id = quote_int_value(shared_set_id, "shared_set_id")
  ads_client = get_ads_client(login_customer_id)
  reference_count, attached_campaigns, diagnostic_errors = (
      _inspect_shared_set_usage(
          ads_client,
          customer_id,
          shared_set_id,
      )
  )
  if attached_campaigns or (reference_count or 0) > 0:
    return _shared_set_in_use_response(
        customer_id,
        shared_set_id,
        attached_campaigns,
        reference_count,
        diagnostic_errors=diagnostic_errors,
        login_customer_id=login_customer_id,
    )

  shared_set_service = ads_client.get_service("SharedSetService")

  resource_name = shared_set_service.shared_set_path(
      customer_id, shared_set_id
  )
  operation = ads_client.get_type("SharedSetOperation")
  operation.remove = resource_name

  try:
    with handle_google_ads_errors():
      response = shared_set_service.mutate_shared_sets(
          customer_id=customer_id, operations=[operation]
      )
  except ToolError as exc:
    if "SHARED_SET_IN_USE" not in str(exc):
      raise
    reference_count, attached_campaigns, postflight_errors = (
        _inspect_shared_set_usage(
            ads_client,
            customer_id,
            shared_set_id,
        )
    )
    return _shared_set_in_use_response(
        customer_id,
        shared_set_id,
        attached_campaigns,
        reference_count,
        api_reported_in_use=True,
        diagnostic_errors=postflight_errors,
        login_customer_id=login_customer_id,
    )

  return {"resource_name": response.results[0].resource_name}


# ---------------------------------------------------------------------------
# Keywords in Shared Sets (SharedCriterion)
# ---------------------------------------------------------------------------


@negative_read_tool
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
    response = _search_stream(ads_service, query, customer_id)
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


@negative_tool
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


@destructive_negative_tool
def remove_shared_set_keywords(
    customer_id: str,
    shared_set_id: str,
    criterion_ids: list[str] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes keywords from a shared negative keyword list by criterion ID."""
  criterion_ids = _normalize_criterion_ids(criterion_ids)
  if not criterion_ids:
    raise ToolError("criterion_ids must not be empty.")

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


@negative_read_tool
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
    response = _search_stream(ads_service, query, customer_id)
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


@negative_tool
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


@destructive_negative_tool
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


@negative_read_tool
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
    response = _search_stream(ads_service, query, customer_id)
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


@negative_tool
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


@destructive_negative_tool
def remove_campaign_negative_keywords(
    customer_id: str,
    campaign_id: str,
    criterion_ids: list[str] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes negative keywords from a campaign by criterion ID."""
  criterion_ids = _normalize_criterion_ids(criterion_ids)
  if not criterion_ids:
    raise ToolError("criterion_ids must not be empty.")

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
