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

"""Tests for the ad group management tools."""

from unittest import mock

from ads_mcp.tools import ad_groups
import pytest


CUSTOMER_ID = "1234567890"
AD_GROUP_ID = "111"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.ad_groups.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    yield client


class TestPauseAdGroup:

  def test_pauses_ad_group(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_path.return_value = "customers/123/adGroups/111"
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroups/111")
    ]

    result = ad_groups.pause_ad_group(CUSTOMER_ID, AD_GROUP_ID)
    assert result == {"resource_name": "customers/123/adGroups/111"}

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.pause_ad_group(CUSTOMER_ID, AD_GROUP_ID, login_customer_id="999")
    assert mock_ads_client.login_customer_id == "999"


class TestEnableAdGroup:

  def test_enables_ad_group(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_path.return_value = "customers/123/adGroups/111"
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroups/111")
    ]

    result = ad_groups.enable_ad_group(CUSTOMER_ID, AD_GROUP_ID)
    assert result == {"resource_name": "customers/123/adGroups/111"}


class TestUpdateAdGroupBid:

  def test_updates_bid(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_path.return_value = "customers/123/adGroups/111"
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroups/111")
    ]

    result = ad_groups.update_ad_group_bid(CUSTOMER_ID, AD_GROUP_ID, 2_500_000)
    assert result == {"resource_name": "customers/123/adGroups/111"}
    assert mock_op.update.cpc_bid_micros == 2_500_000

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.update_ad_group_bid(
        CUSTOMER_ID,
        AD_GROUP_ID,
        1_000_000,
        login_customer_id="999",
    )
    assert mock_ads_client.login_customer_id == "999"
