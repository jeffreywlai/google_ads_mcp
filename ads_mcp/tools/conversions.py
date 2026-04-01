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

"""Conversion upload tools and offline upload diagnostics."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v23.services.types.conversion_upload_service import (
    CallConversion,
    ClickConversion,
    UploadCallConversionsRequest,
    UploadClickConversionsRequest,
)

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import format_value
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import run_gaql_query_page


def _extract_partial_failure(response: Any) -> Any:
  partial_failure = getattr(response, "partial_failure_error", None)
  if not partial_failure:
    return None
  formatted = format_value(partial_failure)
  if formatted:
    return formatted
  return None


def _build_conversion_messages(
    conversions: list[dict[str, Any]],
    conversion_cls: Any,
    conversion_label: str,
) -> list[Any]:
  """Builds protobuf conversion messages from plain dict payloads."""
  if not conversions:
    raise ToolError("conversions must not be empty.")

  message_list = []
  for index, conversion in enumerate(conversions):
    if not isinstance(conversion, dict):
      raise ToolError(f"conversions[{index}] must be an object.")

    try:
      message_list.append(conversion_cls(conversion))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
      raise ToolError(
          f"Invalid {conversion_label} conversion at index {index}: {exc}"
      ) from exc

  return message_list


def _format_results(results: Any) -> list[dict[str, Any]]:
  """Formats upload results into JSON-serializable dicts."""
  return [format_value(result) for result in results]


def _summary_filter_conditions(
    summary_field_prefix: str,
    clients: list[str] | None = None,
    statuses: list[str] | None = None,
) -> list[str]:
  """Builds common WHERE conditions for offline upload summary views."""
  where_conditions = []
  if clients:
    where_conditions.append(
        f"{summary_field_prefix}.client IN ({quote_enum_values(clients)})"
    )
  if statuses:
    where_conditions.append(
        f"{summary_field_prefix}.status IN ({quote_enum_values(statuses)})"
    )
  return where_conditions


conversion_diagnostics_tool = ads_read_tool(
    mcp,
    tags={"conversions", "reporting"},
)
conversion_upload_tool = ads_mutation_tool(mcp, tags={"conversions"})


@conversion_diagnostics_tool
def list_offline_conversion_upload_client_summaries(
    customer_id: str,
    clients: list[str] | None = None,
    statuses: list[str] | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists account-level offline conversion upload diagnostics.

  This uses the `offline_conversion_upload_client_summary` reporting view to
  retrieve import health by client type, including `alerts`,
  `daily_summaries`, and `job_summaries`.

  Google Ads only returns diagnostics when `customer_id` matches the account
  used recently to import conversions. For cross-account conversion tracking,
  query the manager account that performed the imports.

  Args:
      customer_id: Google Ads customer ID used for the imports.
      clients: Optional client filters such as `GOOGLE_ADS_API` or
          `GOOGLE_ADS_WEB_CLIENT`.
      statuses: Optional status filters such as `EXCELLENT`,
          `NEEDS_ATTENTION`, or `NO_RECENT_UPLOADS`.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing client-level offline upload diagnostic rows plus
      completeness metadata.
  """
  validate_limit(limit)
  summary = "offline_conversion_upload_client_summary"
  where_conditions = _summary_filter_conditions(
      summary,
      clients=clients,
      statuses=statuses,
  )

  query = f"""
      SELECT
        customer.id,
        customer.descriptive_name,
        {summary}.client,
        {summary}.alerts,
        {summary}.daily_summaries,
        {summary}.job_summaries,
        {summary}.last_upload_date_time,
        {summary}.pending_event_count,
        {summary}.pending_rate,
        {summary}.status,
        {summary}.success_rate,
        {summary}.successful_event_count,
        {summary}.total_event_count
      FROM {summary}
      {build_where_clause(where_conditions)}
      ORDER BY
        {summary}.last_upload_date_time DESC,
        {summary}.client ASC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "offline_conversion_upload_client_summaries",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@conversion_diagnostics_tool
def list_offline_conversion_upload_conversion_action_summaries(
    customer_id: str,
    conversion_action_ids: list[str] | None = None,
    clients: list[str] | None = None,
    statuses: list[str] | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists conversion-action-level offline conversion upload diagnostics.

  This uses the `offline_conversion_upload_conversion_action_summary`
  reporting view to retrieve import health per conversion action and client,
  including `alerts`, `daily_summaries`, and `job_summaries`.

  Google Ads only returns diagnostics when `customer_id` matches the account
  used recently to import conversions. For cross-account conversion tracking,
  query the manager account that performed the imports.

  Args:
      customer_id: Google Ads customer ID used for the imports.
      conversion_action_ids: Optional conversion action numeric IDs to filter
          to.
      clients: Optional client filters such as `GOOGLE_ADS_API`.
      statuses: Optional status filters such as `EXCELLENT`,
          `NEEDS_ATTENTION`, or `NO_RECENT_UPLOADS`.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing conversion-action-level offline upload diagnostic rows
      plus completeness metadata.
  """
  validate_limit(limit)
  summary = "offline_conversion_upload_conversion_action_summary"
  where_conditions = _summary_filter_conditions(
      summary,
      clients=clients,
      statuses=statuses,
  )
  if conversion_action_ids:
    where_conditions.append(
        f"{summary}.conversion_action_id IN "
        f"({quote_int_values(conversion_action_ids)})"
    )

  query = f"""
      SELECT
        {summary}.conversion_action_id,
        {summary}.conversion_action_name,
        {summary}.client,
        {summary}.alerts,
        {summary}.daily_summaries,
        {summary}.job_summaries,
        {summary}.last_upload_date_time,
        {summary}.pending_event_count,
        {summary}.status,
        {summary}.successful_event_count,
        {summary}.total_event_count
      FROM {summary}
      {build_where_clause(where_conditions)}
      ORDER BY
        {summary}.last_upload_date_time DESC,
        {summary}.conversion_action_name ASC,
        {summary}.client ASC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "offline_conversion_upload_conversion_action_summaries",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@conversion_upload_tool
def upload_click_conversions(
    customer_id: str,
    conversions: list[dict[str, Any]],
    partial_failure: bool = True,
    validate_only: bool = False,
    job_id: int | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Uploads raw ClickConversion payloads through ConversionUploadService.

  Args:
      customer_id: Google Ads conversion customer ID receiving the upload.
      conversions: List of dicts that map directly to the v23
          ClickConversion proto. Common fields include gclid, gbraid,
          wbraid, conversion_action, conversion_date_time,
          conversion_value, currency_code, order_id, consent,
          user_identifiers, custom_variables, and cart_data.
      partial_failure: Whether valid rows should succeed when some fail.
          Defaults to True so row-level errors are returned in the response.
      validate_only: Whether to validate the upload without executing it.
      job_id: Optional upload job ID for tracing or deduplication.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing formatted upload results, result_count, job_id, and
      any partial_failure_error returned by the API.
  """
  ads_client = get_ads_client(login_customer_id)
  conversion_upload_service = ads_client.get_service("ConversionUploadService")

  request = UploadClickConversionsRequest(
      customer_id=customer_id,
      conversions=_build_conversion_messages(
          conversions,
          ClickConversion,
          "click",
      ),
      partial_failure=partial_failure,
      validate_only=validate_only,
  )
  if job_id is not None:
    request.job_id = job_id

  try:
    response = conversion_upload_service.upload_click_conversions(
        request=request
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  result = {
      "results": _format_results(response.results),
      "result_count": len(response.results),
      "job_id": response.job_id,
  }
  partial_failure_error = _extract_partial_failure(response)
  if partial_failure_error:
    result["partial_failure_error"] = partial_failure_error
  return result


@conversion_upload_tool
def upload_call_conversions(
    customer_id: str,
    conversions: list[dict[str, Any]],
    partial_failure: bool = True,
    validate_only: bool = False,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Uploads raw CallConversion payloads through ConversionUploadService.

  Args:
      customer_id: Google Ads conversion customer ID receiving the upload.
      conversions: List of dicts that map directly to the v23
          CallConversion proto. Common fields include caller_id,
          call_start_date_time, conversion_action, conversion_date_time,
          conversion_value, currency_code, custom_variables, and consent.
      partial_failure: Whether valid rows should succeed when some fail.
          Defaults to True so row-level errors are returned in the response.
      validate_only: Whether to validate the upload without executing it.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing formatted upload results, result_count, and any
      partial_failure_error returned by the API.
  """
  ads_client = get_ads_client(login_customer_id)
  conversion_upload_service = ads_client.get_service("ConversionUploadService")

  request = UploadCallConversionsRequest(
      customer_id=customer_id,
      conversions=_build_conversion_messages(
          conversions,
          CallConversion,
          "call",
      ),
      partial_failure=partial_failure,
      validate_only=validate_only,
  )

  try:
    response = conversion_upload_service.upload_call_conversions(
        request=request
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e

  result = {
      "results": _format_results(response.results),
      "result_count": len(response.results),
  }
  partial_failure_error = _extract_partial_failure(response)
  if partial_failure_error:
    result["partial_failure_error"] = partial_failure_error
  return result
