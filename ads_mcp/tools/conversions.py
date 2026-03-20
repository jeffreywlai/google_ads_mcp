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

"""Low-level ConversionUploadService tools for testing raw uploads."""

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
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tools.api import format_value
from ads_mcp.tools.api import get_ads_client


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


conversion_upload_tool = ads_mutation_tool(mcp, tags={"conversions"})


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
