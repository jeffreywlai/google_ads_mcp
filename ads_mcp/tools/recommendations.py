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

"""Tools for recommendations and optimization score workflows."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v23.services.types.recommendation_service import (
    ApplyRecommendationOperation,
)

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import quote_string_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import format_value
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import run_gaql_query
from ads_mcp.tools.api import run_gaql_query_page


def _enum_from_name(enum_cls: Any, value: str, field_name: str) -> Any:
  try:
    return getattr(enum_cls, value.upper())
  except AttributeError as exc:
    raise ToolError(f"Invalid {field_name}: {value}") from exc


def _extract_partial_failure(response: Any) -> Any:
  partial_failure = getattr(response, "partial_failure_error", None)
  if not partial_failure:
    return None
  formatted = format_value(partial_failure)
  if formatted:
    return formatted
  return None


def _get_recommendation_type_map(
    customer_id: str,
    recommendation_resource_names: list[str],
    login_customer_id: str | None = None,
) -> dict[str, str]:
  query = f"""
      SELECT
        recommendation.resource_name,
        recommendation.type
      FROM recommendation
      WHERE recommendation.resource_name IN (
        {quote_string_values(recommendation_resource_names)}
      )
      LIMIT {len(recommendation_resource_names)}
  """
  rows = run_gaql_query(query, customer_id, login_customer_id)
  return {
      row["recommendation.resource_name"]: row["recommendation.type"]
      for row in rows
  }


def _validate_apply_parameters(
    operation_field: str,
    parameters: dict[str, Any],
) -> None:
  message_cls = ApplyRecommendationOperation.meta.fields[
      operation_field
  ].message
  allowed_fields = set(message_cls.meta.fields.keys())
  invalid_fields = sorted(set(parameters) - allowed_fields)
  if invalid_fields:
    raise ToolError(
        "Invalid apply_recommendations parameters for "
        f"{operation_field}: {', '.join(invalid_fields)}"
    )


recommendation_read_tool = ads_read_tool(mcp, tags={"optimization"})
recommendation_tool = ads_mutation_tool(mcp, tags={"optimization"})


@recommendation_read_tool
def list_recommendations(
    customer_id: str,
    recommendation_types: list[str] | None = None,
    campaign_ids: list[str] | None = None,
    include_dismissed: bool = False,
    limit: int = 500,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists recommendation resources with impact and account context.

  Args:
      customer_id: Google Ads customer ID.
      recommendation_types: Optional recommendation types such as
          CAMPAIGN_BUDGET or KEYWORD.
      campaign_ids: Optional campaign IDs to filter to.
      include_dismissed: Whether dismissed recommendations should be included.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with recommendation rows.
  """
  validate_limit(limit)

  where_conditions = []
  if recommendation_types:
    where_conditions.append(
        "recommendation.type IN "
        f"({quote_enum_values(recommendation_types)})"
    )
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if not include_dismissed:
    where_conditions.append("recommendation.dismissed = FALSE")

  query = f"""
      SELECT
        recommendation.resource_name,
        recommendation.type,
        recommendation.dismissed,
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        campaign_budget.id,
        campaign_budget.name
      FROM recommendation
      {build_where_clause(where_conditions)}
      ORDER BY recommendation.type
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "recommendations",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@recommendation_read_tool
def get_optimization_score_summary(
    customer_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Summarizes account optimization score and uplift by type.

  Args:
      customer_id: Google Ads customer ID.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with overall optimization score details and a recommendation
      type breakdown for optimization score uplift.
  """
  summary_rows = run_gaql_query(
      """
      SELECT
        customer.id,
        customer.descriptive_name,
        customer.currency_code,
        customer.optimization_score,
        customer.optimization_score_weight,
        metrics.optimization_score_uplift,
        metrics.optimization_score_url
      FROM customer
      LIMIT 1
      """,
      customer_id,
      login_customer_id,
  )
  breakdown_rows = run_gaql_query(
      """
      SELECT
        segments.recommendation_type,
        metrics.optimization_score_uplift,
        metrics.optimization_score_url
      FROM customer
      ORDER BY metrics.optimization_score_uplift DESC
      """,
      customer_id,
      login_customer_id,
  )

  if not summary_rows:
    raise ToolError(f"No customer row was returned for {customer_id}.")

  summary = summary_rows[0]

  recommendation_breakdown = []
  for row in breakdown_rows:
    recommendation_type = row.get("segments.recommendation_type")
    if recommendation_type in {"UNSPECIFIED", "UNKNOWN", None}:
      continue
    recommendation_breakdown.append(
        {
            "recommendation_type": recommendation_type,
            "optimization_score_uplift": row.get(
                "metrics.optimization_score_uplift"
            ),
            "optimization_score_url": row.get(
                "metrics.optimization_score_url"
            ),
        }
    )

  return {
      "customer_id": summary["customer.id"],
      "customer_name": summary.get("customer.descriptive_name"),
      "currency_code": summary.get("customer.currency_code"),
      "optimization_score": summary.get("customer.optimization_score"),
      "optimization_score_weight": summary.get(
          "customer.optimization_score_weight"
      ),
      "total_optimization_score_uplift": summary.get(
          "metrics.optimization_score_uplift"
      ),
      "optimization_score_url": summary.get("metrics.optimization_score_url"),
      "recommendation_type_breakdown": recommendation_breakdown,
  }


@recommendation_tool
def apply_recommendations(
    customer_id: str,
    recommendation_resource_names: list[str],
    parameters_by_resource_name: dict[str, dict[str, Any]] | None = None,
    partial_failure: bool = False,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Applies existing recommendations through RecommendationService.

  Args:
      customer_id: Google Ads customer ID.
      recommendation_resource_names: Recommendation resource names to apply.
      parameters_by_resource_name: Optional per-resource parameters.
          Example for CAMPAIGN_BUDGET:
          {"customers/.../recommendations/...":
           {"new_budget_amount_micros": 20000000}}
      partial_failure: Whether valid operations should continue when some fail.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with applied recommendation resource names.
  """
  if not recommendation_resource_names:
    raise ToolError("recommendation_resource_names must not be empty.")

  parameters_by_resource_name = parameters_by_resource_name or {}
  unknown_keys = sorted(
      set(parameters_by_resource_name) - set(recommendation_resource_names)
  )
  if unknown_keys:
    raise ToolError(
        "parameters_by_resource_name contains unknown resource names: "
        + ", ".join(unknown_keys)
    )

  type_map = _get_recommendation_type_map(
      customer_id=customer_id,
      recommendation_resource_names=recommendation_resource_names,
      login_customer_id=login_customer_id,
  )
  missing_resource_names = sorted(
      set(recommendation_resource_names) - set(type_map)
  )
  if missing_resource_names:
    raise ToolError(
        "Unable to resolve recommendation types for: "
        + ", ".join(missing_resource_names)
    )

  operations = []
  for resource_name in recommendation_resource_names:
    recommendation_type = type_map[resource_name]
    operation_field = recommendation_type.lower()
    if operation_field not in ApplyRecommendationOperation.meta.fields:
      raise ToolError(
          "Unsupported recommendation type for apply_recommendations: "
          f"{recommendation_type}"
      )

    parameters = parameters_by_resource_name.get(resource_name, {})
    _validate_apply_parameters(operation_field, parameters)
    operations.append(
        {
            "resource_name": resource_name,
            operation_field: parameters,
        }
    )

  ads_client = get_ads_client(login_customer_id)
  recommendation_service = ads_client.get_service("RecommendationService")

  try:
    response = recommendation_service.apply_recommendation(
        request={
            "customer_id": customer_id,
            "operations": operations,
            "partial_failure": partial_failure,
        }
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  result = {
      "resource_names": [row.resource_name for row in response.results],
  }
  partial_failure_error = _extract_partial_failure(response)
  if partial_failure_error:
    result["partial_failure_error"] = partial_failure_error
  return result


@recommendation_tool
def dismiss_recommendations(
    customer_id: str,
    recommendation_resource_names: list[str],
    partial_failure: bool = False,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Dismisses recommendation resources.

  Args:
      customer_id: Google Ads customer ID.
      recommendation_resource_names: Recommendation resource names to dismiss.
      partial_failure: Whether valid operations should continue when some fail.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with dismissed recommendation resource names.
  """
  if not recommendation_resource_names:
    raise ToolError("recommendation_resource_names must not be empty.")

  operations = [
      {"resource_name": resource_name}
      for resource_name in recommendation_resource_names
  ]

  ads_client = get_ads_client(login_customer_id)
  recommendation_service = ads_client.get_service("RecommendationService")

  try:
    response = recommendation_service.dismiss_recommendation(
        request={
            "customer_id": customer_id,
            "operations": operations,
            "partial_failure": partial_failure,
        }
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  result = {
      "resource_names": [row.resource_name for row in response.results],
  }
  partial_failure_error = _extract_partial_failure(response)
  if partial_failure_error:
    result["partial_failure_error"] = partial_failure_error
  return result


@recommendation_read_tool
def list_recommendation_subscriptions(
    customer_id: str,
    recommendation_types: list[str] | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists recommendation subscriptions used for auto-apply.

  Args:
      customer_id: Google Ads customer ID.
      recommendation_types: Optional recommendation types to filter to.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with recommendation subscription rows.
  """
  validate_limit(limit)

  where_conditions = []
  if recommendation_types:
    where_conditions.append(
        "recommendation_subscription.type IN "
        f"({quote_enum_values(recommendation_types)})"
    )

  query = f"""
      SELECT
        recommendation_subscription.resource_name,
        recommendation_subscription.type,
        recommendation_subscription.status,
        recommendation_subscription.create_date_time,
        recommendation_subscription.modify_date_time
      FROM recommendation_subscription
      {build_where_clause(where_conditions)}
      ORDER BY recommendation_subscription.type
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "recommendation_subscriptions",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@recommendation_tool
def create_recommendation_subscription(
    customer_id: str,
    recommendation_type: str,
    status: str = "PAUSED",
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Creates a recommendation subscription, defaulting to PAUSED.

  Args:
      customer_id: Google Ads customer ID.
      recommendation_type: Recommendation type to subscribe to.
      status: Initial status, usually PAUSED or ENABLED.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with the created subscription resource name.
  """
  ads_client = get_ads_client(login_customer_id)
  subscription_service = ads_client.get_service(
      "RecommendationSubscriptionService"
  )

  operation = ads_client.get_type("RecommendationSubscriptionOperation")
  subscription = operation.create
  subscription.type_ = _enum_from_name(
      ads_client.enums.RecommendationTypeEnum,
      recommendation_type,
      "recommendation_type",
  )
  subscription.status = _enum_from_name(
      ads_client.enums.RecommendationSubscriptionStatusEnum,
      status,
      "status",
  )

  try:
    response = subscription_service.mutate_recommendation_subscription(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@recommendation_tool
def set_recommendation_subscription_status(
    customer_id: str,
    resource_name: str,
    status: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates a recommendation subscription status.

  Args:
      customer_id: Google Ads customer ID.
      resource_name: Recommendation subscription resource name.
      status: ENABLED or PAUSED.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with the updated subscription resource name.
  """
  ads_client = get_ads_client(login_customer_id)
  subscription_service = ads_client.get_service(
      "RecommendationSubscriptionService"
  )

  operation = ads_client.get_type("RecommendationSubscriptionOperation")
  subscription = operation.update
  subscription.resource_name = resource_name
  subscription.status = _enum_from_name(
      ads_client.enums.RecommendationSubscriptionStatusEnum,
      status,
      "status",
  )
  operation.update_mask.paths.append("status")

  try:
    response = subscription_service.mutate_recommendation_subscription(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}
