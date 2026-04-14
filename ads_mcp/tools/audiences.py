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

"""Tools for creating modern Google Ads Audience resources."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v23.common.types.audiences import AudienceDimension
from google.ads.googleads.v23.common.types.audiences import AudienceSegment
from google.ads.googleads.v23.common.types.audiences import ExclusionSegment
from google.ads.googleads.v23.enums.types.audience_scope import (
    AudienceScopeEnum,
)

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools.api import get_ads_client


audience_tool = ads_mutation_tool(mcp, tags={"audiences"})

_INCLUDE_SEGMENT_FIELD_BY_TYPE = {
    "USER_LIST": ("user_list", "user_list"),
    "USER_INTEREST": ("user_interest", "user_interest_category"),
    "CUSTOM_AUDIENCE": ("custom_audience", "custom_audience"),
    "LIFE_EVENT": ("life_event", "life_event"),
    "DETAILED_DEMOGRAPHIC": (
        "detailed_demographic",
        "detailed_demographic",
    ),
}


def _normalize_segment(
    segment: dict[str, Any],
    index_label: str,
    *,
    allowed_types: dict[str, tuple[str, str]],
) -> dict[str, str]:
  """Validates a segment payload and returns a normalized copy."""
  if not isinstance(segment, dict):
    raise ToolError(f"{index_label} must be an object.")

  allowed_keys = {"type", "resource_name"}
  invalid_keys = sorted(set(segment) - allowed_keys)
  if invalid_keys:
    invalid_field_names = ", ".join(invalid_keys)
    raise ToolError(f"Invalid {index_label} fields: {invalid_field_names}")

  segment_type = segment.get("type")
  if not isinstance(segment_type, str) or not segment_type:
    raise ToolError(f"{index_label}.type must be a non-empty string.")
  normalized_type = segment_type.upper()
  if normalized_type not in allowed_types:
    raise ToolError(f"Invalid {index_label}.type: {segment_type}")

  resource_name = segment.get("resource_name")
  if not isinstance(resource_name, str) or not resource_name:
    raise ToolError(f"{index_label}.resource_name must be a non-empty string.")

  return {
      "type": normalized_type,
      "resource_name": resource_name,
  }


def _validate_include_dimensions(
    include_dimensions: list[dict[str, Any]],
) -> list[list[dict[str, str]]]:
  """Validates AND/OR include-dimension payloads."""
  if not isinstance(include_dimensions, list):
    raise ToolError("include_dimensions must be a list.")
  if not include_dimensions:
    raise ToolError("include_dimensions must not be empty.")

  normalized_dimensions = []
  for dimension_index, dimension in enumerate(include_dimensions):
    index_label = f"include_dimensions[{dimension_index}]"
    if not isinstance(dimension, dict):
      raise ToolError(f"{index_label} must be an object.")

    invalid_keys = sorted(set(dimension) - {"segments"})
    if invalid_keys:
      invalid_field_names = ", ".join(invalid_keys)
      raise ToolError(f"Invalid {index_label} fields: {invalid_field_names}")

    segments = dimension.get("segments")
    if not isinstance(segments, list):
      raise ToolError(f"{index_label}.segments must be a list.")
    if not segments:
      raise ToolError(f"{index_label}.segments must not be empty.")

    normalized_segments = []
    for segment_index, segment in enumerate(segments):
      normalized_segments.append(
          _normalize_segment(
              segment,
              f"{index_label}.segments[{segment_index}]",
              allowed_types=_INCLUDE_SEGMENT_FIELD_BY_TYPE,
          )
      )
    normalized_dimensions.append(normalized_segments)

  return normalized_dimensions


def _validate_exclude_segments(
    exclude_segments: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
  """Validates exclusion payloads."""
  if exclude_segments is None:
    return []
  if not isinstance(exclude_segments, list):
    raise ToolError("exclude_segments must be a list.")

  normalized_segments = []
  for segment_index, segment in enumerate(exclude_segments):
    normalized_segments.append(
        _normalize_segment(
            segment,
            f"exclude_segments[{segment_index}]",
            allowed_types={"USER_LIST": ("user_list", "user_list")},
        )
    )

  return normalized_segments


def _build_audience_segment(segment: dict[str, str]) -> AudienceSegment:
  """Builds a positive audience segment message."""
  audience_segment = AudienceSegment()
  segment_field, resource_field = _INCLUDE_SEGMENT_FIELD_BY_TYPE[
      segment["type"]
  ]
  setattr(
      getattr(audience_segment, segment_field),
      resource_field,
      segment["resource_name"],
  )
  return audience_segment


def _build_exclusion_segment(segment: dict[str, str]) -> ExclusionSegment:
  """Builds an exclusion segment message."""
  exclusion_segment = ExclusionSegment()
  exclusion_segment.user_list.user_list = segment["resource_name"]
  return exclusion_segment


@audience_tool
def create_audience(
    customer_id: str,
    name: str,
    include_dimensions: list[dict[str, Any]],
    description: str | None = None,
    exclude_segments: list[dict[str, Any]] | None = None,
    login_customer_id: str | None = None,
) -> dict[str, str]:
  """Creates a customer-scope Audience resource.

  Args:
      customer_id: Google Ads customer ID.
      name: Unique audience name.
      include_dimensions: AND across dimensions, OR within each segments list.
      description: Optional audience description.
      exclude_segments: Optional USER_LIST exclusions.
      login_customer_id: Optional manager account ID.

  Returns:
      audience_resource_name and audience_id.
  """
  if not isinstance(name, str) or not name:
    raise ToolError("name must be a non-empty string.")
  if description is not None and not isinstance(description, str):
    raise ToolError("description must be a string or None.")

  normalized_dimensions = _validate_include_dimensions(include_dimensions)
  normalized_exclusions = _validate_exclude_segments(exclude_segments)

  ads_client = get_ads_client(login_customer_id)
  audience_service = ads_client.get_service("AudienceService")

  operation = ads_client.get_type("AudienceOperation")
  audience = operation.create
  audience.name = name
  audience.scope = AudienceScopeEnum.AudienceScope.CUSTOMER
  if description is not None:
    audience.description = description

  for dimension_segments in normalized_dimensions:
    dimension = AudienceDimension()
    for segment in dimension_segments:
      dimension.audience_segments.segments.append(
          _build_audience_segment(segment)
      )
    audience.dimensions.append(dimension)

  for segment in normalized_exclusions:
    audience.exclusion_dimension.exclusions.append(
        _build_exclusion_segment(segment)
    )

  try:
    response = audience_service.mutate_audiences(
        customer_id=customer_id, operations=[operation]
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  audience_resource_name = response.results[0].resource_name
  audience_id = audience_service.parse_audience_path(audience_resource_name)[
      "audience_id"
  ]
  return {
      "audience_resource_name": audience_resource_name,
      "audience_id": audience_id,
  }
