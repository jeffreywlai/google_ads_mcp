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

"""Tools for managing ad groups in Google Ads."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import quote_int_value
from ads_mcp.tools._gaql import require_unique_values
from ads_mcp.tools.api import build_bounded_mutation_response
from ads_mcp.tools.api import get_ads_client


ad_group_tool = ads_mutation_tool(mcp, tags={"ad_groups"})


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


def _normalize_criterion_ids(criterion_ids: list[str] | str) -> list[str]:
  """Normalizes criterion ID inputs for criterion mutations."""
  criterion_ids = normalize_list_arg(criterion_ids, "criterion_ids")
  if not criterion_ids:
    raise ToolError("criterion_ids must not be empty.")
  return require_unique_values(
      [
          _validate_numeric_id(criterion_id, "criterion_ids")
          for criterion_id in criterion_ids
      ],
      "criterion_ids",
  )


@ad_group_tool
def set_ad_group_status(
    customer_id: str,
    ad_group_id: str,
    status: str,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Sets an ad group's status.

  status: 'PAUSED' or 'ENABLED'.
  """
  status_upper = status.upper()
  if status_upper not in ("PAUSED", "ENABLED"):
    raise ToolError(f"Invalid status '{status}'. Use 'PAUSED' or 'ENABLED'.")

  ads_client = get_ads_client(login_customer_id)
  ad_group_service = ads_client.get_service("AdGroupService")

  operation = ads_client.get_type("AdGroupOperation")
  ad_group = operation.update
  ad_group.resource_name = ad_group_service.ad_group_path(
      customer_id, ad_group_id
  )
  ad_group.status = getattr(ads_client.enums.AdGroupStatusEnum, status_upper)
  operation.update_mask.paths.append("status")

  try:
    response = ad_group_service.mutate_ad_groups(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}


@ad_group_tool
def set_ad_group_criterion_status(
    customer_id: str,
    ad_group_id: str,
    criterion_ids: list[str] | str,
    status: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Sets ad-group criterion statuses.

  Args:
      customer_id: Google Ads customer ID.
      ad_group_id: Ad group ID containing the criteria.
      criterion_ids: Ad group criterion IDs. Accepts an array, JSON string,
          comma-separated string, or single ID.
      status: "PAUSED" or "ENABLED".
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing the updated resource names, criterion IDs, status,
      and updated count.
  """
  status_upper = status.upper()
  if status_upper not in ("PAUSED", "ENABLED"):
    raise ToolError(f"Invalid status '{status}'. Use 'PAUSED' or 'ENABLED'.")

  criterion_ids = _normalize_criterion_ids(criterion_ids)

  ads_client = get_ads_client(login_customer_id)
  criterion_service = ads_client.get_service("AdGroupCriterionService")

  operations = []
  for criterion_id in criterion_ids:
    operation = ads_client.get_type("AdGroupCriterionOperation")
    criterion = operation.update
    criterion.resource_name = criterion_service.ad_group_criterion_path(
        customer_id, ad_group_id, criterion_id
    )
    criterion.status = getattr(
        ads_client.enums.AdGroupCriterionStatusEnum, status_upper
    )
    operation.update_mask.paths.append("status")
    operations.append(operation)

  try:
    response = criterion_service.mutate_ad_group_criteria(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  resource_names = [result.resource_name for result in response.results]
  return build_bounded_mutation_response(
      {
          "resource_names": resource_names,
          "criterion_ids": criterion_ids,
          "status": status_upper,
          "updated_count": len(resource_names),
      },
      ("resource_names", "criterion_ids"),
  )


@ad_group_tool
def remove_ad_group_audiences(
    customer_id: str,
    ad_group_id: str,
    criterion_ids: list[str] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Removes ad-group audience criteria by criterion ID.

  Args:
      customer_id: Google Ads customer ID.
      ad_group_id: Ad group ID.
      criterion_ids: Ad group criterion IDs. Accepts an array, JSON string,
          comma-separated string, or single ID.
      login_customer_id: Optional manager account ID.

  Returns:
      removed_resource_names.
  """
  criterion_ids = _normalize_criterion_ids(criterion_ids)

  ads_client = get_ads_client(login_customer_id)
  criterion_service = ads_client.get_service("AdGroupCriterionService")

  operations = []
  for criterion_id in criterion_ids:
    operation = ads_client.get_type("AdGroupCriterionOperation")
    operation.remove = criterion_service.ad_group_criterion_path(
        customer_id,
        ad_group_id,
        criterion_id,
    )
    operations.append(operation)

  try:
    response = criterion_service.mutate_ad_group_criteria(
        customer_id=customer_id, operations=operations
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return build_bounded_mutation_response(
      {
          "removed_resource_names": [
              result.resource_name for result in response.results
          ],
      },
      ("removed_resource_names",),
  )


@ad_group_tool
def update_ad_group_bid(
    customer_id: str,
    ad_group_id: str,
    cpc_bid_micros: int,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Updates the default CPC bid for an ad group.

  cpc_bid_micros: 1 dollar = 1,000,000 micros.
  """
  ads_client = get_ads_client(login_customer_id)
  ad_group_service = ads_client.get_service("AdGroupService")

  operation = ads_client.get_type("AdGroupOperation")
  ad_group = operation.update
  ad_group.resource_name = ad_group_service.ad_group_path(
      customer_id, ad_group_id
  )
  ad_group.cpc_bid_micros = cpc_bid_micros
  operation.update_mask.paths.append("cpc_bid_micros")

  try:
    response = ad_group_service.mutate_ad_groups(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  return {"resource_name": response.results[0].resource_name}
