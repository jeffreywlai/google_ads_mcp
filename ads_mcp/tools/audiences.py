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

"""Tools for audience discovery, Customer Match reporting, and creation."""

from collections import Counter
import heapq
import re
from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v25.common.types.audiences import AudienceDimension
from google.ads.googleads.v25.common.types.audiences import AudienceSegment
from google.ads.googleads.v25.common.types.audiences import ExclusionSegment
from google.ads.googleads.v25.enums.types.audience_scope import (
    AudienceScopeEnum,
)

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import gaql_like_substring_pattern
from ads_mcp.tools._gaql import gaql_quote_string
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import quote_int_value
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import INLINE_PAGE_BYTE_LIMIT
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import finalize_bounded_response
from ads_mcp.tools.api import gaql_snapshot_group
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors
from ads_mcp.tools.api import run_gaql_query_page
from ads_mcp.tools.api import run_gaql_query_snapshot

audience_read_tool = ads_read_tool(mcp, tags={"audiences", "discovery"})
audience_tool = ads_mutation_tool(mcp, tags={"audiences"})

_CUSTOMER_MATCH_LIST_FIELD = (
    "offline_user_data_job.customer_match_user_list_metadata.user_list"
)
_CUSTOMER_MATCH_RATE_FIELD = (
    "offline_user_data_job.operation_metadata.match_rate_range"
)
_CUSTOMER_MATCH_JOB_FIELDS = [
    "offline_user_data_job.resource_name",
    "offline_user_data_job.id",
    "offline_user_data_job.external_id",
    "offline_user_data_job.type",
    "offline_user_data_job.status",
    "offline_user_data_job.failure_reason",
    _CUSTOMER_MATCH_LIST_FIELD,
    _CUSTOMER_MATCH_RATE_FIELD,
    "user_list.resource_name",
    "user_list.name",
]

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


@audience_read_tool
def search_user_interests(
    customer_id: str,
    query: str | None = None,
    taxonomy_types: list[str] | str | None = None,
    include_not_launched: bool = False,
    limit: int = 50,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Searches user-interest audience taxonomy nodes.

  Args:
      customer_id: Google Ads customer ID.
      query: Optional case-insensitive name substring to search.
      taxonomy_types: Optional taxonomy types such as AFFINITY or IN_MARKET.
      include_not_launched: Whether to include interests not launched to all
          customers.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict of user-interest rows with add_campaign_audiences-ready
      resource names.
  """
  validate_limit(limit)
  where_conditions = []
  if query is not None:
    if not isinstance(query, str):
      raise ToolError("query must be a string.")
    stripped_query = query.strip()
    if stripped_query:
      name_pattern = gaql_like_substring_pattern(stripped_query)
      where_conditions.append(
          "user_interest.name LIKE " f"{gaql_quote_string(name_pattern)}"
      )
  taxonomy_types = normalize_list_arg(taxonomy_types, "taxonomy_types")
  if taxonomy_types:
    where_conditions.append(
        "user_interest.taxonomy_type IN "
        f"({quote_enum_values(taxonomy_types)})"
    )
  if not include_not_launched:
    where_conditions.append("user_interest.launched_to_all = TRUE")

  query_text = f"""
      SELECT
        user_interest.resource_name,
        user_interest.user_interest_id,
        user_interest.name,
        user_interest.taxonomy_type,
        user_interest.user_interest_parent,
        user_interest.launched_to_all
      FROM user_interest
      {build_where_clause(where_conditions)}
      ORDER BY user_interest.name
  """
  page = run_gaql_query_page(
      query=query_text,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "user_interests",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
      snapshot_token=page.get("snapshot_token"),
  )


def _customer_match_positive_id(value: Any, field_name: str) -> str:
  """Validates positive IDs before constructing scoped reporting queries."""
  normalized = quote_int_value(value, field_name)
  if int(normalized) <= 0:
    raise ToolError(f"{field_name} must be greater than 0.")
  return normalized


def _customer_match_category(value: Any) -> str:
  """Keeps missing API diagnostics explicit instead of inventing a bucket."""
  return "UNAVAILABLE" if value is None or value == "" else str(value)


def _customer_match_account_id(value: Any, field_name: str) -> str:
  """Accepts familiar account-ID formatting without permitting query syntax."""
  if isinstance(value, str):
    value = value.strip()
    if not re.fullmatch(r"[0-9]+(?:[ -]+[0-9]+)*", value):
      raise ToolError(
          f"{field_name} must be numeric; dashes/spaces are allowed."
      )
    value = re.sub(r"[ -]", "", value)
  return _customer_match_positive_id(value, field_name)


@audience_read_tool
def summarize_customer_match_jobs(
    customer_id: str,
    user_list_ids: list[str] | str | None = None,
    jobs_per_list: int = 5,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Summarizes all available Customer Match jobs by user list and diagnostics.

  Fetches every matching job without a GAQL LIMIT, including its attributed
  user-list name. All-job counts are independent of the selected previews.
  Previews contain the N highest numeric job IDs per list. v25 exposes no
  Customer Match job creation/upload timestamp, and ID order does not prove
  time recency. RUNNING match-rate ranges are estimates; terminal-job ranges
  are final only when available. Ranges are not exact or volume-weighted rates.
  Offline conversion upload health is a different reporting capability.

  Args:
      customer_id: Google Ads customer ID whose available jobs are summarized.
      user_list_ids: Optional nonempty list of user-list IDs to filter to.
          A JSON array string is also accepted. Omit for every list with jobs.
      jobs_per_list: Number of highest-ID preview jobs per list, from 1 to 100.
          This never caps the jobs contributing to the summary or source CSV.
      login_customer_id: Optional manager account ID.

  Returns:
      Complete all-job counts, per-list diagnostic groups and highest-ID
      previews, explicit unavailable time recency, and exact source/derived
      exports when the 32 KiB inline response omits summaries. API availability
      still constrains the source; the result is not a historical archive.
  """
  customer_id = _customer_match_account_id(customer_id, "customer_id")
  if login_customer_id is not None:
    login_customer_id = _customer_match_account_id(
        login_customer_id, "login_customer_id"
    )
  if isinstance(jobs_per_list, bool) or not isinstance(jobs_per_list, int):
    raise ToolError("jobs_per_list must be an integer.")
  if not 1 <= jobs_per_list <= 100:
    raise ToolError("jobs_per_list must be between 1 and 100.")
  requested_list_ids = normalize_list_arg(user_list_ids, "user_list_ids")
  if user_list_ids is not None and not requested_list_ids:
    raise ToolError("user_list_ids must not be empty when provided.")
  normalized_list_ids = list(
      dict.fromkeys(
          _customer_match_positive_id(value, "user_list_ids")
          for value in requested_list_ids
      )
  )
  conditions = ["offline_user_data_job.type = CUSTOMER_MATCH_USER_LIST"]
  if normalized_list_ids:
    conditions.append(
        "user_list.id IN (" + ", ".join(normalized_list_ids) + ")"
    )
  query = (
      "SELECT "
      + ", ".join(_CUSTOMER_MATCH_JOB_FIELDS)
      + " FROM offline_user_data_job "
      + build_where_clause(conditions)
      + " ORDER BY offline_user_data_job.id DESC"
  )

  with gaql_snapshot_group():
    with handle_google_ads_errors():
      source = run_gaql_query_snapshot(query, customer_id, login_customer_id)
    user_lists = {}
    all_status_counts = Counter()
    all_match_rate_counts = Counter()
    for row_index, row in enumerate(source["rows"]):
      list_resource = row.get(_CUSTOMER_MATCH_LIST_FIELD)
      attributed_resource = row.get("user_list.resource_name")
      if (
          list_resource
          and attributed_resource
          and (list_resource != attributed_resource)
      ):
        raise ToolError(
            "Customer Match job returned a different user-list name owner."
        )
      list_resource = list_resource or attributed_resource or "UNAVAILABLE"
      if list_resource not in user_lists:
        user_lists[list_resource] = {
            "user_list_resource_name": list_resource,
            "user_list_name": row.get("user_list.name"),
            "job_count": 0,
            "status_counts": Counter(),
            "match_rate_range_counts": Counter(),
            "failure_reason_counts": Counter(),
            "_diagnostic_groups": Counter(),
            "_preview_heap": [],
        }
      summary = user_lists[list_resource]
      status = _customer_match_category(
          row.get("offline_user_data_job.status")
      )
      match_rate = _customer_match_category(
          row.get(_CUSTOMER_MATCH_RATE_FIELD)
      )
      failure = _customer_match_category(
          row.get("offline_user_data_job.failure_reason")
      )
      summary["job_count"] += 1
      summary["status_counts"][status] += 1
      summary["match_rate_range_counts"][match_rate] += 1
      summary["_diagnostic_groups"][(status, match_rate, failure)] += 1
      if status == "FAILED":
        summary["failure_reason_counts"][failure] += 1
      all_status_counts[status] += 1
      all_match_rate_counts[match_rate] += 1
      job_id = _customer_match_positive_id(
          row.get("offline_user_data_job.id"), "offline_user_data_job.id"
      )
      preview = {
          "job_id": job_id,
          "job_resource_name": row.get("offline_user_data_job.resource_name"),
          "external_id": row.get("offline_user_data_job.external_id"),
          "status": status,
          "match_rate_range": match_rate,
          "failure_reason": failure,
      }
      heapq.heappush(
          summary["_preview_heap"], (int(job_id), row_index, preview)
      )
      if len(summary["_preview_heap"]) > jobs_per_list:
        heapq.heappop(summary["_preview_heap"])

    summaries = []
    for list_resource in sorted(user_lists):
      summary = user_lists[list_resource]
      groups = summary.pop("_diagnostic_groups")
      summary["diagnostic_groups"] = [
          {
              "status": key[0],
              "match_rate_range": key[1],
              "failure_reason": key[2],
              "job_count": groups[key],
          }
          for key in sorted(groups)
      ]
      summary["highest_id_jobs"] = [
          item[2]
          for item in sorted(summary.pop("_preview_heap"), reverse=True)
      ]
      summary["preview_covers_all_jobs"] = (
          len(summary["highest_id_jobs"]) == summary["job_count"]
      )
      for key in (
          "status_counts",
          "match_rate_range_counts",
          "failure_reason_counts",
      ):
        summary[key] = dict(summary[key])
      summaries.append(summary)

    result = {
        "customer_id": customer_id,
        "source_job_count": len(source["rows"]),
        "user_list_count": len(summaries),
        "jobs_per_list": jobs_per_list,
        "analysis_complete": True,
        "status_counts": dict(all_status_counts),
        "match_rate_range_counts": dict(all_match_rate_counts),
        "user_lists": summaries,
        "recency": {
            "time_recency_available": False,
            "preview_selection": "HIGHEST_NUMERIC_JOB_IDS_DESCENDING_HEURISTIC",
            "explanation": (
                "v25 exposes no Customer Match job creation/upload timestamp. "
                "Numeric job IDs do not establish chronology; these previews "
                "must not be called the latest uploads by time."
            ),
        },
        "methodology": (
            "Every returned Customer Match job contributes to all-job counts. "
            "Failure-reason counts include only FAILED jobs. RUNNING "
            "match-rate "
            "ranges are estimates; SUCCESS/FAILED ranges are final when "
            "available. Buckets are not exact or volume-weighted match rates. "
            "Lists without returned jobs are absent, and API availability "
            "constrains coverage; no historical completeness is promised."
        ),
        "bulk_export_call": {
            "tool": "export_gaql_csv",
            "arguments": {"snapshot_token": source["snapshot_token"]},
        },
        "bulk_export_scope": (
            "ALL_RETRIEVED_CUSTOMER_MATCH_JOB_ROWS_WITH_LIST_NAMES"
        ),
        "complete_inline": False,
        "truncated": False,
    }
    result = finalize_bounded_response(
        result, ("user_lists",), max_bytes=INLINE_PAGE_BYTE_LIMIT
    )
    result["complete_inline"] = not result["truncated"]
    return result


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
