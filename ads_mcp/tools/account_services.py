"""Fixed native incentive and product-link invitation service tools."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.incentive_service import ApplyIncentiveRequest
from google.ads.googleads.v25.services.types.incentive_service import FetchIncentiveRequest
from google.ads.googleads.v25.services.types.product_link_invitation_service import CreateProductLinkInvitationRequest

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tooling import local_read_tool
from ads_mcp.tools._service import normalized_account_id
from ads_mcp.tools._service import parse_service_request
from ads_mcp.tools._service import request_schema
from ads_mcp.tools._service import scoped_service_request
from ads_mcp.tools._service import service_response_dict
from ads_mcp.tools.api import finalize_bounded_response
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors
from ads_mcp.tools.api import INLINE_PAGE_BYTE_LIMIT

_REQUESTS = {
    "fetch_incentives": FetchIncentiveRequest,
    "apply_incentive": ApplyIncentiveRequest,
    "create_product_link_invitation": CreateProductLinkInvitationRequest,
}


def _call(
    service_name, method_name, request, login_customer_id, customer_id=None
):
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(login_customer_id)
  client = get_ads_client(login_customer_id)
  service = client.get_service(service_name)
  with handle_google_ads_errors():
    response = getattr(service, method_name)(request=request)
  result = {
      "service": service_name,
      "method": method_name,
      "response": service_response_dict(response),
  }
  if customer_id is not None:
    result["customer_id"] = customer_id
  return finalize_bounded_response(
      result, ("response",), max_bytes=INLINE_PAGE_BYTE_LIMIT
  )


@local_read_tool(mcp, tags={"schema"})
def get_account_service_request_schema(tool_name: str) -> dict[str, Any]:
  """Returns the installed v25.2 native request schema for a fixed tool.

  Args:
      tool_name: fetch_incentives, apply_incentive, or
          create_product_link_invitation.

  Returns:
      The complete native JSON schema, including nested enums and oneofs.
  """
  if tool_name not in _REQUESTS:
    raise ToolError("tool_name must be one of: " + ", ".join(_REQUESTS))
  return finalize_bounded_response(
      request_schema(_REQUESTS[tool_name]),
      ("$defs",),
      max_bytes=INLINE_PAGE_BYTE_LIMIT,
      public_schema=True,
  )


@ads_read_tool(mcp, tags={"incentives"})
def fetch_incentives(
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Fetches available offers using IncentiveService's v25 request schema.

  Args:
      request: Native FetchIncentiveRequest object or JSON string. Use
          incentive_type, replacing the removed type field. The request has
          language_code, country_code, email and incentive_type; it has no
          customer_id field. Google's eligibility rules still apply.
      login_customer_id: Optional manager account ID for authentication.

  Returns:
      The bounded native offer response with incentive_type and offer_type.
  """
  native = parse_service_request(request, FetchIncentiveRequest)
  return _call(
      "IncentiveService", "fetch_incentive", native, login_customer_id
  )


@ads_mutation_tool(mcp, tags={"incentives"})
def apply_incentive(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Redeems a selected incentive once; this service has no validate-only.

  Args:
      customer_id: Account receiving the incentive; required by v25.
      request: Native ApplyIncentiveRequest object or JSON string. Requires
          a positive selected_incentive_id. Optional native country_code.
          Any supplied customer_id must match the outer account ID.
      login_customer_id: Optional manager account ID.

  Returns:
      The native redemption response. Eligibility errors retain Google's
      named v25 reasons. This call applies the incentive and is never retried.
  """
  customer_id, native = scoped_service_request(
      customer_id, request, ApplyIncentiveRequest
  )
  if native.selected_incentive_id <= 0:
    raise ToolError("selected_incentive_id must be a positive integer.")
  return _call(
      "IncentiveService",
      "apply_incentive",
      native,
      login_customer_id,
      customer_id,
  )


@ads_mutation_tool(mcp, tags={"account-links"})
def create_product_link_invitation(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Creates a typed product-link invitation; no validate-only is available.

  Args:
      customer_id: Account creating the invitation.
      request: Native CreateProductLinkInvitationRequest object or JSON
          string with product_link_invitation. Advertising-partner links
          require advertising_partner_properties.allowed_domain in v25.
          Supplied customer_id must match the outer account ID.
      login_customer_id: Optional manager account ID.

  Returns:
      The native invitation response. Sending an invitation applies this
      account operation and is never retried.
  """
  customer_id, native = scoped_service_request(
      customer_id, request, CreateProductLinkInvitationRequest
  )
  invitation = CreateProductLinkInvitationRequest.pb(
      native
  ).product_link_invitation
  if invitation.WhichOneof("invited_account") is None:
    raise ToolError("product_link_invitation requires one native product.")
  if (
      invitation.WhichOneof("invited_account") == "advertising_partner"
      and not invitation.advertising_partner_properties.allowed_domain.strip()
  ):
    raise ToolError("Advertising-partner invitations require allowed_domain.")
  return _call(
      "ProductLinkInvitationService",
      "create_product_link_invitation",
      native,
      login_customer_id,
      customer_id,
  )
