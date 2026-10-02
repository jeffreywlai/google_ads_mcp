"""Native, bounded resource mutations for the v25.2 feature families."""

import json
import re
from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.google_ads_service import MutateGoogleAdsRequest
from google.ads.googleads.v25.services.types.google_ads_service import MutateOperation
from pydantic import StrictBool

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tooling import local_read_tool
from ads_mcp.tools._service import normalized_account_id
from ads_mcp.tools._service import parse_service_request
from ads_mcp.tools._service import request_schema
from ads_mcp.tools._service import service_response_dict
from ads_mcp.tools.api import finalize_bounded_response
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors
from ads_mcp.tools.api import INLINE_PAGE_BYTE_LIMIT


# Fixed resource families cover the release additions without arbitrary RPCs.
_COLLECTIONS = {
    "ad_operation": "ads",
    "ad_group_ad_operation": "adGroupAds",
    "ad_group_operation": "adGroups",
    "ad_group_criterion_operation": "adGroupCriteria",
    "asset_operation": "assets",
    "asset_group_operation": "assetGroups",
    "asset_group_asset_operation": "assetGroupAssets",
    "asset_group_signal_operation": "assetGroupSignals",
    "asset_set_operation": "assetSets",
    "asset_set_asset_operation": "assetSetAssets",
    "campaign_operation": "campaigns",
    "customer_operation": "customers",
    "conversion_action_operation": "conversionActions",
    "conversion_value_rule_operation": "conversionValueRules",
    "conversion_value_rule_set_operation": "conversionValueRuleSets",
    "experiment_operation": "experiments",
    "experiment_arm_operation": "experimentArms",
    "ad_group_asset_operation": "adGroupAssets",
    "campaign_asset_operation": "campaignAssets",
    "customer_asset_operation": "customerAssets",
}


def _target_name(name, customer_id, collection, *, temporary=False):
  if collection == "customers":
    valid = name == f"customers/{customer_id}"
  else:
    identifier = r"-?[1-9][0-9]*(?:~(?:-?[1-9][0-9]*|[A-Z][A-Z_]*))*"
    if not temporary:
      identifier = r"[1-9][0-9]*(?:~(?:[1-9][0-9]*|[A-Z][A-Z_]*))*"
    valid = re.fullmatch(
        rf"customers/{customer_id}/{collection}/{identifier}", name
    )
  if not valid:
    raise ToolError(
        f"Mutation target must be a {collection} resource of customer_id."
    )


@local_read_tool(mcp, tags={"schema"})
def get_resource_mutation_schema(operation_type: str) -> dict[str, Any]:
  """Returns the installed v25.2 schema for an allowed native operation.

  Args:
      operation_type: Native operation name, such as campaign_operation,
          conversion_action_operation, or ad_group_criterion_operation.

  Returns:
      Allowed operation names and the selected native JSON schema. Required
      business fields, immutable fields, eligibility and field combinations
      are additionally checked by Google; use validate_only first.
  """
  if operation_type not in _COLLECTIONS:
    raise ToolError(
        "operation_type must be one of: " + ", ".join(_COLLECTIONS)
    )
  return finalize_bounded_response(
      {
          "api_release": "25.2",
          "allowed_operations": list(_COLLECTIONS),
          "operation_type": operation_type,
          "schema": request_schema(
              MutateOperation.meta.fields[operation_type].message
          ),
      },
      ("schema",),
      max_bytes=INLINE_PAGE_BYTE_LIMIT,
      public_schema=True,
  )


@ads_mutation_tool(mcp, tags={"resources"})
def mutate_ads_resources(
    customer_id: str,
    operations: list[dict[str, Any]] | str,
    validate_only: StrictBool = True,
    partial_failure: StrictBool = False,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Validates or applies native v25.2 resource changes in one API call.

  Args:
      customer_id: Account owning every mutated target.
      operations: Native MutateOperation objects or a JSON array. Each has
          one allowed *_operation containing create, update, or remove.
          Supports ads, ad groups/criteria, assets and their associations,
          asset groups/signals/sets, campaigns, customer settings, conversion
          actions/value rules, and experiments/arms. Get exact nested fields
          from get_resource_mutation_schema. Updates require resource_name
          and a nonempty native update_mask. Lifecycle goals use mutate_goals
          and mutate_campaign_goal_configs instead.
      validate_only: True validates without applying any account changes.
          Google checks mutable fields, eligibility and business rules.
      partial_failure: Native partial-failure flag. Errors and empty indexed
          result slots are preserved; executed does not imply full success.
      login_customer_id: Optional manager account ID.

  Returns:
      Flags, operation counts and the unchanged native response, including
      partial failures. Large responses have an exact materialized export.
  """
  if not isinstance(validate_only, bool) or not isinstance(
      partial_failure, bool
  ):
    raise ToolError("validate_only and partial_failure must be booleans.")
  customer_id = normalized_account_id(customer_id)
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(login_customer_id)
  values = {
      "customer_id": customer_id,
      "validate_only": validate_only,
      "partial_failure": partial_failure,
  }
  if isinstance(operations, str):
    # Strict parsing retains duplicate-key and malformed-array detection.
    values = (
        json.dumps(values)[:-1] + ', "mutate_operations": ' + operations + "}"
    )
  else:
    values["mutate_operations"] = operations
  request = parse_service_request(values, MutateGoogleAdsRequest)
  if not request.mutate_operations:
    raise ToolError("operations must contain at least one operation.")
  for outer in MutateGoogleAdsRequest.pb(request).mutate_operations:
    operation_type = outer.WhichOneof("operation")
    if operation_type not in _COLLECTIONS:
      raise ToolError(
          "Unsupported resource operation; get its allowed schema."
      )
    operation = getattr(outer, operation_type)
    kind = (
        operation.WhichOneof("operation")
        if "operation" in operation.DESCRIPTOR.oneofs_by_name
        else "update"
        if operation.HasField("update")
        else None
    )
    if kind is None:
      raise ToolError("Each operation requires one create, update, or remove.")
    mask = getattr(operation, "update_mask", None)
    if kind != "update" and mask and mask.paths:
      raise ToolError("Only update operations accept update_mask.")
    if kind == "remove":
      _target_name(operation.remove, customer_id, _COLLECTIONS[operation_type])
      continue
    entity = getattr(operation, kind)
    if kind == "update" or entity.resource_name:
      _target_name(
          entity.resource_name,
          customer_id,
          _COLLECTIONS[operation_type],
          temporary=kind == "create",
      )
    if kind == "update":
      mask = operation.update_mask
      if not mask.paths or not mask.IsValidForDescriptor(entity.DESCRIPTOR):
        raise ToolError("Updates require a valid nonempty native update_mask.")
      if any(
          path.split(".", 1)[0] in {"id", "resource_name"}
          for path in mask.paths
      ):
        raise ToolError("update_mask cannot modify resource identity.")
  client = get_ads_client(login_customer_id)
  service = client.get_service("GoogleAdsService")
  with handle_google_ads_errors():
    response = service.mutate(request=request)
  payload = service_response_dict(response)
  return finalize_bounded_response(
      {
          "customer_id": customer_id,
          "validate_only": validate_only,
          "executed": not validate_only,
          "partial_failure": partial_failure,
          "requested_operation_count": len(request.mutate_operations),
          "returned_result_count": len(
              payload.get("mutate_operation_responses", [])
          ),
          "response": payload,
      },
      ("response",),
      max_bytes=INLINE_PAGE_BYTE_LIMIT,
  )
