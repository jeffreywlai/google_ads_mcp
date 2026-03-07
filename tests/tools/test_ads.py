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
    yield client


class TestPauseAd:

  def test_pauses_ad(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_ad_path.return_value = (
        "customers/123/adGroupAds/111~222"
    )
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupAds/111~222")
    ]

    result = ads.pause_ad(CUSTOMER_ID, AD_GROUP_ID, AD_ID)
    assert result == {"resource_name": "customers/123/adGroupAds/111~222"}

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ads.pause_ad(CUSTOMER_ID, AD_GROUP_ID, AD_ID, login_customer_id="999")
    assert mock_ads_client.login_customer_id == "999"


class TestEnableAd:

  def test_enables_ad(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_ad_path.return_value = (
        "customers/123/adGroupAds/111~222"
    )
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupAds/111~222")
    ]

    result = ads.enable_ad(CUSTOMER_ID, AD_GROUP_ID, AD_ID)
    assert result == {"resource_name": "customers/123/adGroupAds/111~222"}
