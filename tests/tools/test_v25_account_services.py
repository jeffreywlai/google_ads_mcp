"""Fixed v25 account service schemas, required fields and call boundaries."""

from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.incentive_service import FetchIncentiveResponse
from google.ads.googleads.v25.services.types.incentive_service import ApplyIncentiveResponse
from google.ads.googleads.v25.services.types.product_link_invitation_service import CreateProductLinkInvitationResponse
from google.api_core.exceptions import ServiceUnavailable
import pytest

from ads_mcp.tools import account_services


@pytest.mark.parametrize(
    "name,args,response,method",
    [
        (
            "fetch_incentives",
            [
                {
                    "country_code": "US",
                    "language_code": "en",
                    "email": "invalid",
                    "incentive_type": "ACQUISITION",
                }
            ],
            FetchIncentiveResponse(),
            "fetch_incentive",
        ),
        (
            "apply_incentive",
            ["123", {"selected_incentive_id": "7"}],
            ApplyIncentiveResponse(),
            "apply_incentive",
        ),
        (
            "create_product_link_invitation",
            [
                "123",
                {
                    "product_link_invitation": {
                        "advertising_partner": {"customer": "customers/456"},
                        "advertising_partner_properties": {
                            "allowed_domain": "example.com"
                        },
                    }
                },
            ],
            CreateProductLinkInvitationResponse(),
            "create_product_link_invitation",
        ),
    ],
)
def test_native_services_call_once(name, args, response, method):
  with mock.patch.object(account_services, "get_ads_client") as client:
    service = client.return_value.get_service.return_value
    getattr(service, method).return_value = response
    result = getattr(account_services, name)(*args)
  getattr(service, method).assert_called_once()
  assert result["response"] == {}


@pytest.mark.parametrize(
    "name,args",
    [
        ("fetch_incentives", [{"type": "ACQUISITION"}]),
        ("apply_incentive", ["123", {}]),
        ("apply_incentive", ["123", {"selected_incentive_id": 0}]),
        (
            "apply_incentive",
            ["123", {"selected_incentive_id": 7, "customerId": "456"}],
        ),
        ("create_product_link_invitation", ["123", {}]),
        (
            "create_product_link_invitation",
            [
                "123",
                {
                    "product_link_invitation": {
                        "advertising_partner": {"customer": "customers/456"}
                    }
                },
            ],
        ),
    ],
)
def test_invalid_inputs_fail_before_client(name, args):
  with mock.patch.object(account_services, "get_ads_client") as client:
    with pytest.raises(ToolError):
      getattr(account_services, name)(*args)
  client.assert_not_called()


def test_mutation_service_error_is_centralized_and_never_retried():
  with mock.patch.object(account_services, "get_ads_client") as client:
    service = client.return_value.get_service.return_value
    service.apply_incentive.side_effect = ServiceUnavailable(
        "service unavailable"
    )
    with pytest.raises(ToolError, match="service unavailable"):
      account_services.apply_incentive("123", {"selected_incentive_id": 7})
  service.apply_incentive.assert_called_once()


def test_schema_allowlist_cannot_name_arbitrary_service():
  with pytest.raises(ToolError, match="tool_name must be one of"):
    account_services.get_account_service_request_schema("delete_customer")
