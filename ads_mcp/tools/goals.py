# Copyright 2026 Google LLC
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

"""Typed v25 lifecycle goal and campaign-goal configuration mutations."""

import json
import math
import re
from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.campaign_goal_config_service import MutateCampaignGoalConfigsRequest
from google.ads.googleads.v25.services.types.goal_service import MutateGoalsRequest
from google.protobuf import descriptor as descriptors
from pydantic import StrictBool

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools._service import normalized_account_id
from ads_mcp.tools._service import parse_service_request
from ads_mcp.tools._service import service_response_dict
from ads_mcp.tools.api import finalize_bounded_response
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors
from ads_mcp.tools.api import INLINE_PAGE_BYTE_LIMIT


goal_mutation_tool = ads_mutation_tool(mcp, tags={"goals"})
_GOAL_SETTINGS = {
    "retention_goal_settings",
    "new_customer_acquisition_goal_settings",
    "loyalty_retention_goal_settings",
}
_CAMPAIGN_SETTINGS = {
    "campaign_retention_settings",
    "campaign_new_customer_acquisition_settings",
    "campaign_loyalty_retention_settings",
}


def _boolean(value: bool, field_name: str) -> None:
  if not isinstance(value, bool):
    raise ToolError(f"{field_name} must be a boolean.")


def _resource_parts(
    value: str,
    collection: str,
    customer_id: str | None = None,
    *,
    temporary_id: bool = False,
) -> tuple[str, str]:
  identifier = r"[1-9][0-9]*"
  if temporary_id:
    identifier = r"-?[1-9][0-9]*"
  if collection == "campaignGoalConfigs":
    identifier = r"[1-9][0-9]*~[1-9][0-9]*"
  match = re.fullmatch(
      rf"customers/([1-9][0-9]*)/{collection}/({identifier})", value
  )
  if not match:
    raise ToolError(f"Invalid {collection} resource name: {value!r}.")
  owner, resource_id = match.groups()
  if customer_id is not None and owner != customer_id:
    raise ToolError(f"{collection} resources must belong to customer_id.")
  return owner, resource_id


def _finite_values(message) -> None:
  """Checks finite native numeric inputs before account access."""
  for field, value in message.ListFields():
    if field.type in {
        descriptors.FieldDescriptor.TYPE_DOUBLE,
        descriptors.FieldDescriptor.TYPE_FLOAT,
    } and not math.isfinite(value):
      raise ToolError(f"{field.name} must be a finite number.")
    if field.message_type:
      _finite_values(value)


def _check_provided_fields(
    raw_entity: dict, entity, allowed_roots: set
) -> None:
  aliases = {field.json_name: field.name for field in entity.DESCRIPTOR.fields}
  for name in raw_entity:
    name = aliases.get(name, name)
    if name not in allowed_roots:
      raise ToolError(
          f"{name} is output-only or immutable for this operation."
      )


def _check_update_mask(
    operation, entity, mutable_roots: set, raw_entity: dict
) -> None:
  paths = list(operation.update_mask.paths)
  if not paths:
    raise ToolError("Update operations require a nonempty update_mask.")
  if not operation.update_mask.IsValidForDescriptor(entity.DESCRIPTOR):
    raise ToolError("update_mask contains an unsupported native field path.")
  if any(path.split(".", 1)[0] not in mutable_roots for path in paths):
    raise ToolError("update_mask may contain only mutable goal settings.")

  def check_supplied_fields(value, descriptor, prefix=""):
    # Protobuf presence drops explicit proto3 defaults. Inspect the original
    # parsed JSON keys so false, zero, empty, and null cannot escape the mask.
    aliases = {
        alias: field
        for field in descriptor.fields
        for alias in (field.name, field.json_name)
    }
    for name, supplied in value.items():
      field = aliases[name]
      path = prefix + field.name
      if path == "resource_name":
        continue
      if not any(
          path == mask
          or path.startswith(mask + ".")
          or mask.startswith(path + ".")
          for mask in paths
      ):
        raise ToolError(
            f"Provided update field {path} is outside update_mask."
        )
      if field.message_type and isinstance(supplied, dict):
        check_supplied_fields(supplied, field.message_type, path + ".")

  check_supplied_fields(raw_entity, entity.DESCRIPTOR)


def _validate_operation(
    operation, raw_operation: dict, customer_id: str, campaign_configs: bool
) -> None:
  kind = operation.WhichOneof("operation")
  if kind is None:
    raise ToolError(
        "Each operation requires exactly one create, update, or remove."
    )
  if kind != "update" and operation.update_mask.paths:
    raise ToolError("Only update operations accept update_mask.")
  collection = "campaignGoalConfigs" if campaign_configs else "goals"
  if kind == "remove":
    _resource_parts(operation.remove, collection, customer_id)
    return
  entity = getattr(operation, kind)
  settings = _CAMPAIGN_SETTINGS if campaign_configs else _GOAL_SETTINGS
  allowed = settings | {"resource_name"}
  if campaign_configs and kind == "create":
    allowed |= {"campaign", "goal"}
  _check_provided_fields(raw_operation[kind], entity, allowed)
  if kind == "update":
    _resource_parts(entity.resource_name, collection, customer_id)
    _check_update_mask(operation, entity, settings, raw_operation[kind])
  else:
    resource_parts = None
    if entity.resource_name:
      resource_parts = _resource_parts(
          entity.resource_name,
          collection,
          customer_id,
          temporary_id=not campaign_configs,
      )
    oneof = (
        "campaign_goal_config_settings"
        if campaign_configs
        else "goal_settings"
    )
    if entity.WhichOneof(oneof) is None:
      raise ToolError(
          "Create operations require one native goal-settings object."
      )
    if campaign_configs:
      _, campaign_id = _resource_parts(
          entity.campaign, "campaigns", customer_id
      )
      # A goal can legitimately be owned by the conversion-tracking owner.
      # Google authorizes this reference; campaign ownership stays fixed.
      _, goal_id = _resource_parts(entity.goal, "goals")
      if resource_parts and resource_parts[1] != f"{campaign_id}~{goal_id}":
        raise ToolError(
            "Config resource IDs must match its campaign and goal."
        )
  _finite_values(entity)


def _mutate(
    customer_id,
    operations,
    validate_only,
    partial_failure,
    login_customer_id,
    *,
    campaign_configs,
):
  _boolean(validate_only, "validate_only")
  _boolean(partial_failure, "partial_failure")
  customer_id = normalized_account_id(customer_id)
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(
        login_customer_id, "login_customer_id"
    )
  request_type = (
      MutateCampaignGoalConfigsRequest
      if campaign_configs
      else MutateGoalsRequest
  )
  request_values = {
      "customer_id": customer_id,
      "validate_only": validate_only,
      "partial_failure": partial_failure,
  }
  if isinstance(operations, str):
    # Preserve duplicate JSON-key detection in the shared strict parser.
    raw_request = (
        json.dumps(request_values)[:-1] + ', "operations": ' + operations + "}"
    )
  else:
    raw_request = dict(request_values, operations=operations)
  request = parse_service_request(raw_request, request_type)
  if not request.operations:
    raise ToolError("operations must contain at least one operation.")
  raw_operations = (
      json.loads(operations) if isinstance(operations, str) else operations
  )
  native_request = request_type.pb(request)
  for operation, raw_operation in zip(
      native_request.operations, raw_operations
  ):
    _validate_operation(
        operation, raw_operation, customer_id, campaign_configs
    )
  client = get_ads_client(login_customer_id)
  service_name = (
      "CampaignGoalConfigService" if campaign_configs else "GoalService"
  )
  service = client.get_service(service_name)
  with handle_google_ads_errors():
    if campaign_configs:
      response = service.mutate_campaign_goal_configs(request=request)
    else:
      response = service.mutate_goals(request=request)
  payload = service_response_dict(response)
  result = {
      "customer_id": customer_id,
      "validate_only": validate_only,
      "executed": not validate_only,
      "partial_failure": partial_failure,
      "requested_operation_count": len(request.operations),
      "returned_result_count": len(payload.get("results", [])),
      "response": payload,
  }
  return finalize_bounded_response(
      result, ("response",), max_bytes=INLINE_PAGE_BYTE_LIMIT
  )


@goal_mutation_tool
def mutate_goals(
    customer_id: str,
    operations: list[dict[str, Any]] | str,
    validate_only: StrictBool = True,
    partial_failure: StrictBool = False,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Creates or updates typed v25 unified lifecycle goals; validates by default.

  Args:
      customer_id: Customer owning each goal mutation.
      operations: Native GoalOperation dictionaries or JSON array. Exactly one
          create/update per operation. Supports retention, new-customer
          acquisition, and loyalty-retention settings and native value or
          multiplier oneofs. Updates require resource_name and update_mask,
          either a protobuf JSON string or {paths: [snake_case.path]}.
          Output-only fields and unmasked supplied fields are rejected.
      validate_only: True validates without applying any account changes.
      partial_failure: Native partial-failure flag; errors remain in response.
      login_customer_id: Optional manager account ID.

  Returns:
      Complete bounded native receipt; absent results remain absent for
      validation-only requests. Google validates eligibility and value rules.
  """
  return _mutate(
      customer_id,
      operations,
      validate_only,
      partial_failure,
      login_customer_id,
      campaign_configs=False,
  )


@goal_mutation_tool
def mutate_campaign_goal_configs(
    customer_id: str,
    operations: list[dict[str, Any]] | str,
    validate_only: StrictBool = True,
    partial_failure: StrictBool = False,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Creates, updates, or removes typed campaign-goal lifecycle configurations.

  Args:
      customer_id: Customer owning each config and campaign.
      operations: Native CampaignGoalConfigOperation dictionaries or JSON.
          Create requires campaign, goal, and one native settings object.
          Goal references may use an authorized conversion-tracking owner.
          Update requires resource_name and a mutable-settings update_mask,
          either protobuf JSON string or {paths: [snake_case.path]}. Remove
          accepts a config resource name. Supports NCA, retention, loyalty
          flags, and full native value/multiplier override oneofs.
      validate_only: True validates without applying any account changes.
      partial_failure: Native partial-failure flag; errors remain in response.
      login_customer_id: Optional manager account ID.

  Returns:
      Complete bounded native receipt, including partial-failure details and
      only returned resource names. Google validates linked-goal eligibility.
  """
  return _mutate(
      customer_id,
      operations,
      validate_only,
      partial_failure,
      login_customer_id,
      campaign_configs=True,
  )
