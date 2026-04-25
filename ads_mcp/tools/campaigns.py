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
from google.ads.googleads.v24.common.types.targeting_setting import (
    TargetRestriction,
    TargetingSetting,
)
from google.ads.googleads.v24.enums.types.targeting_dimension import (
    TargetingDimensionEnum,
)

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import preprocess_gaql_query
from ads_mcp.tools._gaql import quote_int_value
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import require_unique_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import format_value
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import run_gaql_query
from ads_mcp.tools.api import run_gaql_query_page


campaign_read_tool = ads_read_tool(mcp, tags={"campaigns", "audiences"})
campaign_tool = ads_mutation_tool(mcp, tags={"campaigns"})

_AUDIENCE_FIELD_BY_TYPE = {
    "USER_LIST": ("user_list", "user_list"),
    "USER_INTEREST": ("user_interest", "user_interest_category"),
    "CUSTOM_AUDIENCE": ("custom_audience", "custom_audience"),
    "COMBINED_AUDIENCE": ("combined_audience", "combined_audience"),
}
_AUDIENCE_RESOURCE_COLLECTION_BY_TYPE = {
    "USER_LIST": "userLists",
    "USER_INTEREST": "userInterests",
    "CUSTOM_AUDIENCE": "customAudiences",
    "COMBINED_AUDIENCE": "combinedAudiences",
}
_AUDIENCE_ALIASES_BY_TYPE = {
    "USER_LIST": {"user_list", "user_list_id"},
    "USER_INTEREST": {
        "user_interest",
        "user_interest_category",
        "user_interest_id",
    },
    "CUSTOM_AUDIENCE": {"custom_audience", "custom_audience_id"},
    "COMBINED_AUDIENCE": {"combined_audience", "combined_audience_id"},
}
_CAMPAIGN_AUDIENCE_TYPES = set(_AUDIENCE_FIELD_BY_TYPE)
_BASE_AUDIENCE_KEYS = {
    "type",
    "resource_name",
    "bid_modifier",
    "negative",
    "id",
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
  if isinstance(value, str) and not value.strip().isdigit():
    raise ToolError(f"{field_name} must be an integer string.")
  try:
    normalized_value = quote_int_value(value, field_name)
  except ToolError as exc:
    raise ToolError(f"{field_name} must be an integer string.") from exc
  if normalized_value.startswith("-"):
    raise ToolError(f"{field_name} must be an integer string.")
  return normalized_value


def _criterion_id_from_resource_name(resource_name: str) -> str:
  return resource_name.rsplit("~", maxsplit=1)[-1]


def _audience_query_field(audience_type: str) -> str:
  criterion_field, resource_field = _AUDIENCE_FIELD_BY_TYPE[audience_type]
  return f"campaign_criterion.{criterion_field}.{resource_field}"


def _expected_audience_shape(index: int, audience_type: str) -> str:
  collection = _AUDIENCE_RESOURCE_COLLECTION_BY_TYPE[audience_type]
  aliases = sorted(_AUDIENCE_ALIASES_BY_TYPE[audience_type] | {"id"})
  return (
      f"Expected audiences[{index}] for type={audience_type}: "
      f'{{"type": "{audience_type}", "resource_name": '
      f'"customers/<CID>/{collection}/<ID>"}} or one of aliases: '
      + ", ".join(aliases)
      + ". Optional fields: bid_modifier, negative."
  )


def _audience_resource_name_from_id(
    customer_id: str,
    audience_type: str,
    audience_id: str,
) -> str:
  collection = _AUDIENCE_RESOURCE_COLLECTION_BY_TYPE[audience_type]
  return f"customers/{customer_id}/{collection}/{audience_id}"


def _audience_resource_name_from_payload(
    customer_id: str,
    audience_type: str,
    audience: dict[str, Any],
    index: int,
) -> str:
  """Returns a resource name from aliases or ID."""
  resource_name = audience.get("resource_name")
  if isinstance(resource_name, str):
    resource_name = resource_name.strip()
    if resource_name:
      return resource_name

  for alias in sorted(_AUDIENCE_ALIASES_BY_TYPE[audience_type]):
    alias_value = audience.get(alias)
    if alias_value in (None, ""):
      continue
    if isinstance(alias_value, str) and alias_value.startswith("customers/"):
      return alias_value
    audience_id = _validate_numeric_id(
        alias_value, f"audiences[{index}].{alias}"
    )
    return _audience_resource_name_from_id(
        customer_id, audience_type, audience_id
    )

  if audience.get("id") not in (None, ""):
    audience_id = _validate_numeric_id(
        audience["id"], f"audiences[{index}].id"
    )
    return _audience_resource_name_from_id(
        customer_id, audience_type, audience_id
    )

  raise ToolError(
      f"audiences[{index}].resource_name must be a non-empty string. "
      + _expected_audience_shape(index, audience_type)
  )


def _audience_resource_name_from_row(row: dict[str, Any]) -> str:
  audience_type = row.get("campaign_criterion.type")
  if audience_type not in _AUDIENCE_FIELD_BY_TYPE:
    return ""
  return row.get(_audience_query_field(audience_type)) or ""


def _audience_key(row: dict[str, Any]) -> tuple[str, str, bool]:
  return (
      str(row.get("campaign_criterion.type") or ""),
      _audience_resource_name_from_row(row),
      bool(row.get("campaign_criterion.negative")),
  )


def _audience_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
  """Builds add_campaign_audiences-compatible payload from a GAQL row."""
  payload = {
      "type": row.get("campaign_criterion.type"),
      "resource_name": _audience_resource_name_from_row(row),
      "negative": bool(row.get("campaign_criterion.negative")),
  }
  bid_modifier = row.get("campaign_criterion.bid_modifier")
  if bid_modifier not in (None, 0):
    payload["bid_modifier"] = bid_modifier
  return payload


def _audience_rows_by_key(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, bool], dict[str, Any]]:
  rows_by_key = {}
  for row in rows:
    key = _audience_key(row)
    if key[1]:
      rows_by_key[key] = row
  return rows_by_key


def _campaign_audience_query(
    campaign_ids: list[str] | str,
    include_negative: bool | None = None,
) -> str:
  normalized_campaign_ids = normalize_list_arg(campaign_ids, "campaign_ids")
  if not normalized_campaign_ids:
    raise ToolError("campaign_ids must not be empty.")
  campaign_id_values = quote_int_values(
      normalized_campaign_ids,
      "campaign_ids",
  )
  where_conditions = [
      f"campaign.id IN ({campaign_id_values})",
      "campaign_criterion.status != REMOVED",
      "campaign_criterion.type IN "
      f"({quote_enum_values(sorted(_CAMPAIGN_AUDIENCE_TYPES))})",
  ]
  if include_negative is not None:
    where_conditions.append(
        "campaign_criterion.negative = "
        + ("TRUE" if include_negative else "FALSE")
    )

  audience_fields = [
      _audience_query_field(audience_type)
      for audience_type in sorted(_CAMPAIGN_AUDIENCE_TYPES)
  ]
  select_fields = [
      "campaign.id",
      "campaign.name",
      "campaign_criterion.criterion_id",
      "campaign_criterion.type",
      "campaign_criterion.status",
      "campaign_criterion.negative",
      "campaign_criterion.bid_modifier",
      *audience_fields,
  ]
  return f"""
      SELECT
        {", ".join(select_fields)}
      FROM campaign_criterion
      {build_where_clause(where_conditions)}
      ORDER BY campaign.id, campaign_criterion.type,
        campaign_criterion.criterion_id
  """


def _read_campaign_audiences(
    customer_id: str,
    campaign_ids: list[str] | str,
    include_negative: bool | None = None,
    login_customer_id: str | None = None,
) -> list[dict[str, Any]]:
  return run_gaql_query(
      _campaign_audience_query(campaign_ids, include_negative),
      customer_id,
      login_customer_id,
  )


def _diff_campaign_audience_rows(
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
  target_by_key = _audience_rows_by_key(target_rows)
  source_by_key = _audience_rows_by_key(source_rows)
  return {
      "missing_in_target": [
          _audience_payload_from_row(row)
          for key, row in source_by_key.items()
          if key not in target_by_key
      ],
      "common": [
          _audience_payload_from_row(row)
          for key, row in source_by_key.items()
          if key in target_by_key
      ],
      "target_only": [
          _audience_payload_from_row(row)
          for key, row in target_by_key.items()
          if key not in source_by_key
      ],
  }


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
    response = ads_service.search_stream(
        query=preprocess_gaql_query(query),
        customer_id=customer_id,
    )
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
    customer_id: str,
    audiences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
  """Validates and normalizes audience criterion inputs."""
  if not isinstance(audiences, list):
    raise ToolError("audiences must be a list.")
  if not audiences:
    raise ToolError("audiences must not be empty.")

  validated = []

  for index, audience in enumerate(audiences):
    if not isinstance(audience, dict):
      raise ToolError(f"audiences[{index}] must be an object.")

    audience_type = audience.get("type")
    if not isinstance(audience_type, str) or not audience_type:
      raise ToolError(f"audiences[{index}].type must be a non-empty string.")
    audience_type = audience_type.upper()
    if audience_type == "AUDIENCE":
      raise ToolError(
          "audiences"
          f"[{index}].type AUDIENCE is not supported by CampaignCriterion "
          "in Google Ads API v24."
      )
    if audience_type not in _AUDIENCE_FIELD_BY_TYPE:
      invalid_audience_type = audience["type"]
      raise ToolError(
          f"Invalid audiences[{index}].type: {invalid_audience_type}"
      )

    allowed_keys = (
        _BASE_AUDIENCE_KEYS | _AUDIENCE_ALIASES_BY_TYPE[audience_type]
    )
    invalid_keys = sorted(set(audience) - allowed_keys)
    if invalid_keys:
      invalid_field_names = ", ".join(invalid_keys)
      raise ToolError(
          f"Invalid audiences[{index}] fields: {invalid_field_names}. "
          + _expected_audience_shape(index, audience_type)
      )

    resource_name = _audience_resource_name_from_payload(
        customer_id,
        audience_type,
        audience,
        index,
    )
    normalized = {
        "type": audience_type,
        "resource_name": resource_name,
        "negative": bool(audience.get("negative", False)),
    }
    if "negative" in audience and not isinstance(audience["negative"], bool):
      raise ToolError(f"audiences[{index}].negative must be a boolean.")
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
  return [_criterion_id_from_resource_name(name) for name in resource_names]


def _partial_failure_indexes_from_value(value: Any) -> list[int]:
  """Extracts failed operation indexes from a formatted partial failure."""
  indexes = []
  if isinstance(value, dict):
    if isinstance(value.get("index"), int):
      indexes.append(value["index"])
    for key in ("field_path_elements", "fieldPathElements"):
      elements = value.get(key)
      if isinstance(elements, list):
        for element in elements:
          if not isinstance(element, dict):
            continue
          if (
              element.get("field_name") == "operations"
              or element.get("fieldName") == "operations"
          ) and isinstance(element.get("index"), int):
            indexes.append(element["index"])
    for nested_value in value.values():
      indexes.extend(_partial_failure_indexes_from_value(nested_value))
  elif isinstance(value, list):
    for item in value:
      indexes.extend(_partial_failure_indexes_from_value(item))
  return indexes


def _partial_failure_indexes(partial_failure_error: Any) -> list[int]:
  """Returns unique failed operation indexes from partial failure details."""
  formatted = format_value(partial_failure_error)
  indexes = _partial_failure_indexes_from_value(formatted)
  return sorted(set(indexes))


def _success_operation_indexes(
    resource_names: list[str],
    failed_indexes: list[int],
    operation_count: int | None = None,
) -> list[int]:
  """Maps returned success rows back to submitted operation indexes."""
  if operation_count is None:
    return list(range(len(resource_names)))
  if len(resource_names) == operation_count:
    return [
        index
        for index, resource_name in enumerate(resource_names)
        if resource_name
    ]
  remaining_indexes = [
      index for index in range(operation_count) if index not in failed_indexes
  ]
  return remaining_indexes[: len(resource_names)]


def _operation_successes(
    resource_names: list[str],
    failed_indexes: list[int] | None = None,
    operation_count: int | None = None,
) -> list[dict[str, Any]]:
  """Builds per-operation success entries from mutation resource names."""
  failed_indexes = failed_indexes or []
  success_indexes = _success_operation_indexes(
      resource_names,
      failed_indexes,
      operation_count,
  )
  successes = []
  success_position = 0
  for result_index, resource_name in enumerate(resource_names):
    if not resource_name:
      continue
    if len(resource_names) == operation_count:
      operation_index = result_index
    else:
      operation_index = success_indexes[success_position]
      success_position += 1
    successes.append(
        {
            "index": operation_index,
            "resource_name": resource_name,
            "criterion_id": _criterion_id_from_resource_name(resource_name),
        }
    )
  return successes


def _failed_operation_indexes(
    resource_names: list[str],
    operation_count: int | None = None,
    partial_failure_error: Any = None,
) -> list[int]:
  """Returns indexes for operations without a success resource name."""
  partial_failure_indexes = _partial_failure_indexes(partial_failure_error)
  if partial_failure_indexes:
    return partial_failure_indexes
  failed_indexes = [
      index
      for index, resource_name in enumerate(resource_names)
      if not resource_name
  ]
  if operation_count is not None and len(resource_names) < operation_count:
    failed_indexes.extend(range(len(resource_names), operation_count))
  return failed_indexes


def _partial_failure_entries(
    partial_failure_error: Any,
    failed_indexes: list[int] | None = None,
) -> list[dict[str, Any]]:
  """Builds a compact failures list while preserving the legacy error key."""
  if not partial_failure_error:
    return []
  failed_indexes = failed_indexes or []
  if isinstance(partial_failure_error, dict):
    failure = {}
    if partial_failure_error.get("message"):
      failure["reason"] = partial_failure_error["message"]
    if "code" in partial_failure_error:
      failure["code"] = partial_failure_error["code"]
    if failed_indexes:
      failure["indexes"] = failed_indexes
    return [failure] if failure else []
  failure = {"reason": str(partial_failure_error)}
  if failed_indexes:
    failure["indexes"] = failed_indexes
  return [failure]


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
def set_campaign_view_through_conversion_optimization(
    customer_id: str,
    campaign_id: str,
    enabled: bool,
    login_customer_id: str | None = None,
) -> dict[str, str | bool]:
  """Enables or disables view-through conversion optimization.

  This setting is only supported by eligible campaign types in Google Ads.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Campaign ID to update.
      enabled: Whether view-through conversion optimization should be enabled.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with the updated campaign resource name and requested state.
  """
  if not isinstance(enabled, bool):
    raise ToolError("enabled must be a boolean.")

  ads_client = get_ads_client(login_customer_id)
  campaign_service = ads_client.get_service("CampaignService")

  operation = ads_client.get_type("CampaignOperation")
  campaign = operation.update
  campaign.resource_name = campaign_service.campaign_path(
      customer_id,
      campaign_id,
  )
  campaign.view_through_conversion_optimization_enabled = enabled
  operation.update_mask.paths.append(
      "view_through_conversion_optimization_enabled"
  )

  try:
    response = campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {
      "resource_name": response.results[0].resource_name,
      "view_through_conversion_optimization_enabled": enabled,
  }


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


@campaign_read_tool
def list_campaign_audiences(
    customer_id: str,
    campaign_ids: list[str] | str,
    include_negative: bool | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign-level audience criteria with copy-ready resource names.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Campaign IDs to inspect. Accepts an array, JSON string,
          comma-separated string, or single ID.
      include_negative: Optional filter for exclusions (`true`) or positive
          audiences (`false`). Leave unset to include both.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing campaign audience rows and pagination metadata.
  """
  validate_limit(limit)
  query = _campaign_audience_query(campaign_ids, include_negative)
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "campaign_audiences",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["copy_ready_audiences"] = [
      _audience_payload_from_row(row)
      for row in page["rows"]
      if _audience_resource_name_from_row(row)
  ]
  return result


@campaign_read_tool
def diff_campaign_audiences(
    customer_id: str,
    source_campaign_id: str,
    target_campaign_id: str,
    include_negative: bool | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Compares campaign audiences and returns copy-ready differences.

  Args:
      customer_id: Google Ads customer ID.
      source_campaign_id: Campaign ID to copy from.
      target_campaign_id: Campaign ID to compare against.
      include_negative: Optional filter for exclusions (`true`) or positive
          audiences (`false`). Leave unset to include both.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with missing_in_target, common, and target_only audience payloads.
  """
  rows = _read_campaign_audiences(
      customer_id,
      [source_campaign_id, target_campaign_id],
      include_negative=include_negative,
      login_customer_id=login_customer_id,
  )
  source_campaign_id = quote_int_value(
      source_campaign_id, "source_campaign_id"
  )
  target_campaign_id = quote_int_value(
      target_campaign_id, "target_campaign_id"
  )
  source_rows = [
      row for row in rows if str(row.get("campaign.id")) == source_campaign_id
  ]
  target_rows = [
      row for row in rows if str(row.get("campaign.id")) == target_campaign_id
  ]
  diff = _diff_campaign_audience_rows(source_rows, target_rows)
  return {
      "source_campaign_id": source_campaign_id,
      "target_campaign_id": target_campaign_id,
      "missing_count": len(diff["missing_in_target"]),
      "common_count": len(diff["common"]),
      "target_only_count": len(diff["target_only"]),
      **diff,
  }


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
          AudienceService audience resources are not supported here. Each
          audience may use resource_name or friendly aliases such as
          user_interest_id, user_list_id, combined_audience_id, or
          combined_audience. Set negative=true to create exclusions.
      login_customer_id: Optional manager account ID.

  Returns:
      created_criterion_ids, resource_names, successes, failures, and any
      partial_failure_error.
  """
  validated_audiences = _validate_audience_payload(customer_id, audiences)
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
    if audience.get("negative"):
      campaign_criterion.negative = True

    criterion_field, resource_field = _AUDIENCE_FIELD_BY_TYPE[audience["type"]]
    criterion_info = getattr(campaign_criterion, criterion_field)
    setattr(criterion_info, resource_field, audience["resource_name"])
    operations.append(operation)

  try:
    response = campaign_criterion_service.mutate_campaign_criteria(
        request={
            "customer_id": customer_id,
            "operations": operations,
            "partial_failure": True,
        }
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  raw_resource_names = [result.resource_name for result in response.results]
  resource_names = [
      resource_name for resource_name in raw_resource_names if resource_name
  ]
  partial_failure_error = _extract_partial_failure(response)
  failed_indexes = _failed_operation_indexes(
      raw_resource_names,
      len(operations),
      response.partial_failure_error,
  )
  result = {
      "created_criterion_ids": _extract_campaign_criterion_ids(resource_names),
      "resource_names": resource_names,
      "successes": _operation_successes(
          raw_resource_names,
          failed_indexes,
          len(operations),
      ),
      "failures": _partial_failure_entries(
          partial_failure_error,
          failed_indexes,
      ),
  }
  if partial_failure_error:
    result["partial_failure_error"] = partial_failure_error

  return result


@campaign_tool
def copy_audiences_between_campaigns(
    customer_id: str,
    source_campaign_id: str,
    target_campaign_id: str,
    include_negative: bool | None = None,
    dry_run: bool = True,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Copies missing campaign audiences from one campaign to another.

  Args:
      customer_id: Google Ads customer ID.
      source_campaign_id: Campaign ID to copy from.
      target_campaign_id: Campaign ID to copy into.
      include_negative: Optional filter for exclusions (`true`) or positive
          audiences (`false`). Leave unset to include both.
      dry_run: When true, only returns the audiences that would be created.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with the audience diff and mutation result when dry_run=false.
  """
  diff = diff_campaign_audiences(
      customer_id=customer_id,
      source_campaign_id=source_campaign_id,
      target_campaign_id=target_campaign_id,
      include_negative=include_negative,
      login_customer_id=login_customer_id,
  )
  audiences_to_create = diff["missing_in_target"]
  normalized_source_campaign_id = diff["source_campaign_id"]
  normalized_target_campaign_id = diff["target_campaign_id"]
  result = {
      "source_campaign_id": normalized_source_campaign_id,
      "target_campaign_id": normalized_target_campaign_id,
      "dry_run": dry_run,
      "audiences_to_create": audiences_to_create,
      "create_count": len(audiences_to_create),
      "common_count": diff["common_count"],
      "target_only_count": diff["target_only_count"],
  }
  if dry_run or not audiences_to_create:
    return result

  result["mutation_result"] = add_campaign_audiences(
      customer_id=customer_id,
      campaign_id=normalized_target_campaign_id,
      audiences=audiences_to_create,
      login_customer_id=login_customer_id,
  )
  return result


@campaign_tool
def remove_campaign_audiences(
    customer_id: str,
    campaign_id: str,
    criterion_ids: list[str] | str,
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
  criterion_ids = normalize_list_arg(criterion_ids, "criterion_ids")
  if not criterion_ids:
    raise ToolError("criterion_ids must not be empty.")
  criterion_ids = require_unique_values(
      [
          _validate_numeric_id(criterion_id, "criterion_ids")
          for criterion_id in criterion_ids
      ],
      "criterion_ids",
  )

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
