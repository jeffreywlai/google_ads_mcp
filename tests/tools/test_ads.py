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

"""Tests for the ad management tools."""

from unittest import mock

from ads_mcp.tools import ads
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v25.errors.types.errors import GoogleAdsFailure
from google.api_core import exceptions as google_exceptions
import pytest

CUSTOMER_ID = "1234567890"
AD_GROUP_ID = "111"
AD_ID = "222"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.ads.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


class TestSetAdStatus:

  def test_pauses_ad(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_ad_path.return_value = (
        "customers/123/adGroupAds/111~222"
    )
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupAds/111~222")
    ]

    result = ads.set_ad_status(CUSTOMER_ID, AD_GROUP_ID, AD_ID, "PAUSED")
    assert result == {"resource_name": "customers/123/adGroupAds/111~222"}

  def test_enables_ad(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_ad_path.return_value = (
        "customers/123/adGroupAds/111~222"
    )
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupAds/111~222")
    ]

    result = ads.set_ad_status(CUSTOMER_ID, AD_GROUP_ID, AD_ID, "ENABLED")
    assert result == {"resource_name": "customers/123/adGroupAds/111~222"}

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ads.set_ad_status(
        CUSTOMER_ID,
        AD_GROUP_ID,
        AD_ID,
        "PAUSED",
        login_customer_id="999",
    )
    mock_ads_client._mock_get.assert_any_call("999")


# Pytest injects fixtures using their declared names.
# pylint: disable=redefined-outer-name


@pytest.fixture(params=["transport", "google_ads"])
def api_failure(request):
  """Provides transport errors and native Google Ads hint-bearing failures."""
  if request.param == "transport":
    return google_exceptions.ServiceUnavailable("transport unavailable")
  return GoogleAdsException(
      error=mock.Mock(),
      failure=GoogleAdsFailure(errors=[{"message": "USER_PERMISSION_DENIED"}]),
      call=mock.Mock(),
      request_id="test",
  )


def _assert_api_failure(raised, original):
  assert raised.__cause__ is original
  if isinstance(original, GoogleAdsException):
    assert "USER_PERMISSION_DENIED" in str(raised)
    assert "Hints:\n- Call list_accessible_accounts" in str(raised)
  else:
    assert str(raised) == str(original)


@pytest.mark.parametrize("status", ["PAUSED", "ENABLED"])
def test_set_ad_status_handles_api_failure_once(
    mock_ads_client, api_failure, status
):
  mutation = mock_ads_client.get_service.return_value.mutate_ad_group_ads
  mutation.side_effect = api_failure

  with pytest.raises(ToolError) as caught:
    ads.set_ad_status(CUSTOMER_ID, AD_GROUP_ID, AD_ID, status)

  _assert_api_failure(caught.value, api_failure)
  mutation.assert_called_once()
  assert len(mutation.call_args.kwargs["operations"]) == 1
