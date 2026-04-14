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

"""Tools for managing campaigns, budgets, and targeting in Google Ads."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v23.common.types.targeting_setting import (
    TargetRestriction,
    TargetingSetting,
)
from google.ads.googleads.v23.enums.types.targeting_dimension import (
    TargetingDimensionEnum,
)

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools.api import format_value
from ads_mcp.tools.api import get_ads_client


campaign_tool = ads_mutation_tool(mcp, tags={"campaigns"})

_AUDIENCE_FIELD_BY_TYPE = {
    "USER_LIST": ("user_list", "user_list"),
    "USER_INTEREST": ("user_interest", "user_interest_category"),
    "CUSTOM_AUDIENCE": ("custom_audience", "custom_audience"),
    "COMBINED_AUDIENCE": ("combined_audience", "combined_audience"),
}


def _extract_partial_failure(response: Any) -> Any:
  """Returns a JSON-serializable partial failure payload when present."""
  partial_failure = getattr(response, "partial_failure_error", None)
  if not partial_failure:
    return None
  formatted = format_value(partial_failure)
  if isinstance(formatted, dict):
    compact = {}
    if "code" in formatted:
      compact["code"] = formatted["code"]
    if formatted.get("message"):
      compact["message"] = formatted["message"]
    if compact:
      return compact
  if formatted:
    return formatted
  return None


def _validate_target_restrictions(
    target_restrictions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
  """Validates and normalizes targeting restriction inputs."""
  if not isinstance(target_restrictions, list):
    raise ToolError("target_restrictions must be a list.")

  validated = []
  seen_dimensions = set()
  allowed_keys = {"targeting_dimension", "bid_only"}

  for index, restriction in enumerate(target_restrictions):
    if not isinstance(restriction, dict):
      raise ToolError(f"target_restrictions[{index}] must be an object.")

    invalid_keys = sorted(set(restriction) - allowed_keys)
    if invalid_keys:
      invalid_field_names = ", ".join(invalid_keys)
      raise ToolError(
          f"Invalid target_restrictions[{index}] fields: "
          f"{invalid_field_names}"
      )

    targeting_dimension = restriction.get("targeting_dimension")
    if not isinstance(targeting_dimension, str) or not targeting_dimension:
      raise ToolError(
          f"target_restrictions[{index}].targeting_dimension must be a "
          "non-empty string."
      )

    bid_only = restriction.get("bid_only")
    if not isinstance(bid_only, bool):
      raise ToolError(
          f"target_restrictions[{index}].bid_only must be a boolean."
      )

    normalized_dimension = targeting_dimension.upper()
    try:
      getattr(TargetingDimensionEnum.TargetingDimension, normalized_dimension)
    except AttributeError as exc:
      raise ToolError(
          "Invalid target_restrictions"
          f"[{index}].targeting_dimension: {targeting_dimension}"
      ) from exc

    if normalized_dimension in seen_dimensions:
      raise ToolError(
          "Duplicate targeting_dimension in target_restrictions: "
          f"{normalized_dimension}"
      )
    seen_dimensions.add(normalized_dimension)

    validated.append(
        {
            "targeting_dimension": normalized_dimension,
            "bid_only": bid_only,
        }
    )

  return validated


def _validate_numeric_id(value: str, field_name: str) -> str:
  """Validates that an ID-like input can be safely treated as an integer."""
  try:
    return str(int(value))
  except (TypeError, ValueError) as exc:
    raise ToolError(f"{field_name} must be an integer string.") from exc


def _get_campaign_target_restrictions(
    ads_client: Any,
    customer_id: str,
    campaign_id: str,
) -> dict[str, bool]:
  """Returns current target restrictions keyed by targeting dimension."""
  ads_service = ads_client.get_service("GoogleAdsService")
  normalized_campaign_id = _validate_numeric_id(campaign_id, "campaign_id")
  query = f"""
      SELECT
        campaign.targeting_setting.target_restrictions
      FROM campaign
      WHERE campaign.id = {normalized_campaign_id}
      LIMIT 1
  """

  current_restrictions = {}
  try:
    response = ads_service.search_stream(query=query, customer_id=customer_id)
    for batch in response:
      for row in batch.results:
        for restriction in row.campaign.targeting_setting.target_restrictions:
          current_restrictions[restriction.targeting_dimension.name] = (
              restriction.bid_only
          )
        return current_restrictions
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return current_restrictions


def _validate_audience_payload(
    audiences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
  """Validates and normalizes audience criterion inputs."""
  if not isinstance(audiences, list):
    raise ToolError("audiences must be a list.")
  if not audiences:
    raise ToolError("audiences must not be empty.")

  validated = []
  allowed_keys = {"type", "resource_name", "bid_modifier"}

  for index, audience in enumerate(audiences):
    if not isinstance(audience, dict):
      raise ToolError(f"audiences[{index}] must be an object.")

    invalid_keys = sorted(set(audience) - allowed_keys)
    if invalid_keys:
      invalid_field_names = ", ".join(invalid_keys)
      raise ToolError(
          f"Invalid audiences[{index}] fields: {invalid_field_names}"
      )

    audience_type = audience.get("type")
    if not isinstance(audience_type, str) or not audience_type:
      raise ToolError(f"audiences[{index}].type must be a non-empty string.")
    audience_type = audience_type.upper()
    if audience_type == "AUDIENCE":
      raise ToolError(
          "audiences"
          f"[{index}].type AUDIENCE is not supported by CampaignCriterion "
          "in Google Ads API v23."
      )
    if audience_type not in _AUDIENCE_FIELD_BY_TYPE:
      invalid_audience_type = audience["type"]
      raise ToolError(
          f"Invalid audiences[{index}].type: {invalid_audience_type}"
      )

    resource_name = audience.get("resource_name")
    if not isinstance(resource_name, str) or not resource_name:
      raise ToolError(
          f"audiences[{index}].resource_name must be a non-empty string."
      )

    normalized = {
        "type": audience_type,
        "resource_name": resource_name,
    }
    if "bid_modifier" in audience:
      bid_modifier = audience["bid_modifier"]
      if isinstance(bid_modifier, bool) or not isinstance(
          bid_modifier, (int, float)
      ):
        raise ToolError(f"audiences[{index}].bid_modifier must be a number.")
      normalized["bid_modifier"] = float(bid_modifier)

    validated.append(normalized)

  return validated


def _extract_campaign_criterion_ids(resource_names: list[str]) -> list[str]:
  """Parses criterion IDs from campaign criterion resource names."""
  return [
      resource_name.rsplit("~", maxsplit=1)[-1]
      for resource_name in resource_names
  ]


@campaign_tool
def set_campaign_status(
    customer_id: str,
    campaign_id: str,
    status: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Sets a campaign's status.

  status: 'PAUSED' or 'ENABLED'.
  """
  status_upper = status.upper()
  if status_upper not in ("PAUSED", "ENABLED"):
    raise ToolError(f"Invalid status '{status}'. Use 'PAUSED' or 'ENABLED'.")

  ads_client = get_ads_client(login_customer_id)
  campaign_service = ads_client.get_service("CampaignService")

  operation = ads_client.get_type("CampaignOperation")
  campaign = operation.update
  campaign.resource_name = campaign_service.campaign_path(
      customer_id, campaign_id
  )
  campaign.status = getattr(ads_client.enums.CampaignStatusEnum, status_upper)
  operation.update_mask.paths.append("status")

  try:
    response = campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@campaign_tool
def update_campaign_budget(
    customer_id: str,
    budget_id: str,
    amount_micros: int,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates the daily amount of a campaign budget.

  amount_micros: 1 dollar = 1,000,000 micros.
  Use execute_gaql to find the budget_id:
    SELECT campaign_budget.id, campaign_budget.amount_micros
    FROM campaign_budget
  """
  ads_client = get_ads_client(login_customer_id)
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


@campaign_tool
def update_campaign_targeting_setting(
    customer_id: str,
    campaign_id: str,
    target_restrictions: list[dict[str, Any]],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Replaces campaign targeting restrictions.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Campaign ID.
      target_restrictions: Full replacement list of
          {targeting_dimension, bid_only}.
      login_customer_id: Optional manager account ID.

  Returns:
      campaign_resource_name, updated_restrictions, and warning when
      AUDIENCE changes from observation to targeting.
  """
  validated_restrictions = _validate_target_restrictions(target_restrictions)
  ads_client = get_ads_client(login_customer_id)
  campaign_service = ads_client.get_service("CampaignService")
  next_restrictions = {
      restriction["targeting_dimension"]: restriction["bid_only"]
      for restriction in validated_restrictions
  }
  current_restrictions = {}
  if next_restrictions.get("AUDIENCE") is False:
    current_restrictions = _get_campaign_target_restrictions(
        ads_client, customer_id, campaign_id
    )

  operation = ads_client.get_type("CampaignOperation")
  campaign = operation.update
  campaign.resource_name = campaign_service.campaign_path(
      customer_id, campaign_id
  )
  campaign.targeting_setting = TargetingSetting()
  for restriction in validated_restrictions:
    target_restriction = TargetRestriction()
    target_restriction.targeting_dimension = getattr(
        TargetingDimensionEnum.TargetingDimension,
        restriction["targeting_dimension"],
    )
    target_restriction.bid_only = restriction["bid_only"]
    campaign.targeting_setting.target_restrictions.append(target_restriction)
  operation.update_mask.paths.append("targeting_setting.target_restrictions")

  try:
    response = campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  result = {
      "campaign_resource_name": response.results[0].resource_name,
      "updated_restrictions": validated_restrictions,
  }

  if (
      current_restrictions.get("AUDIENCE") is True
      and next_restrictions.get("AUDIENCE") is False
  ):
    result["warning"] = (
        "AUDIENCE bid_only true->false can sharply reduce reach."
    )

  return result


@campaign_tool
def add_campaign_audiences(
    customer_id: str,
    campaign_id: str,
    audiences: list[dict[str, Any]],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Adds campaign audience criteria to a campaign.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Campaign ID.
      audiences: Criteria to create. Supported types are USER_LIST,
          USER_INTEREST, CUSTOM_AUDIENCE, and COMBINED_AUDIENCE. Modern
          AudienceService audience resources are not supported here.
      login_customer_id: Optional manager account ID.

  Returns:
      created_criterion_ids, resource_names, and any partial_failure_error.
  """
  validated_audiences = _validate_audience_payload(audiences)
  ads_client = get_ads_client(login_customer_id)
  campaign_service = ads_client.get_service("CampaignService")
  campaign_criterion_service = ads_client.get_service(
      "CampaignCriterionService"
  )

  campaign_resource_name = campaign_service.campaign_path(
      customer_id, campaign_id
  )
  operations = []
  for audience in validated_audiences:
    operation = ads_client.get_type("CampaignCriterionOperation")
    campaign_criterion = operation.create
    campaign_criterion.campaign = campaign_resource_name

    if "bid_modifier" in audience:
      campaign_criterion.bid_modifier = audience["bid_modifier"]

    criterion_field, resource_field = _AUDIENCE_FIELD_BY_TYPE[audience["type"]]
    criterion_info = getattr(campaign_criterion, criterion_field)
    setattr(criterion_info, resource_field, audience["resource_name"])
    operations.append(operation)

  try:
    response = campaign_criterion_service.mutate_campaign_criteria(
        customer_id=customer_id,
        operations=operations,
        partial_failure=True,
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  resource_names = [result.resource_name for result in response.results]
  result = {
      "created_criterion_ids": _extract_campaign_criterion_ids(resource_names),
      "resource_names": resource_names,
  }
  partial_failure_error = _extract_partial_failure(response)
  if partial_failure_error:
    result["partial_failure_error"] = partial_failure_error

  return result


@campaign_tool
def remove_campaign_audiences(
    customer_id: str,
    campaign_id: str,
    criterion_ids: list[str],
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes campaign audience criteria by criterion ID.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Campaign ID.
      criterion_ids: Campaign criterion IDs.
      login_customer_id: Optional manager account ID.

  Returns:
      removed_resource_names.
  """
  if not isinstance(criterion_ids, list):
    raise ToolError("criterion_ids must be a list.")
  if not criterion_ids:
    raise ToolError("criterion_ids must not be empty.")

  ads_client = get_ads_client(login_customer_id)
  campaign_criterion_service = ads_client.get_service(
      "CampaignCriterionService"
  )

  operations = []
  for criterion_id in criterion_ids:
    operation = ads_client.get_type("CampaignCriterionOperation")
    operation.remove = campaign_criterion_service.campaign_criterion_path(
        customer_id,
        campaign_id,
        criterion_id,
    )
    operations.append(operation)

  try:
    response = campaign_criterion_service.mutate_campaign_criteria(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {
      "removed_resource_names": [r.resource_name for r in response.results],
  }
