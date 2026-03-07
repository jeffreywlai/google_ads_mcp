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

"""Tests for the keyword management tools."""

from unittest import mock

from ads_mcp.tools import keywords
import pytest


CUSTOMER_ID = "1234567890"
AD_GROUP_ID = "111"
CRITERION_ID = "222"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.keywords.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


class TestSetKeywordStatus:

  def test_pauses_keyword(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_criterion_path.return_value = (
        "customers/123/adGroupCriteria/111~222"
    )
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupCriteria/111~222")
    ]

    result = keywords.set_keyword_status(
        CUSTOMER_ID, AD_GROUP_ID, CRITERION_ID, "PAUSED"
    )
    assert result == {"resource_name": "customers/123/adGroupCriteria/111~222"}

  def test_enables_keyword(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_criterion_path.return_value = (
        "customers/123/adGroupCriteria/111~222"
    )
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupCriteria/111~222")
    ]

    result = keywords.set_keyword_status(
        CUSTOMER_ID, AD_GROUP_ID, CRITERION_ID, "ENABLED"
    )
    assert result == {"resource_name": "customers/123/adGroupCriteria/111~222"}


class TestUpdateKeywordBid:

  def test_updates_bid(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_criterion_path.return_value = (
        "customers/123/adGroupCriteria/111~222"
    )
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupCriteria/111~222")
    ]

    result = keywords.update_keyword_bid(
        CUSTOMER_ID, AD_GROUP_ID, CRITERION_ID, 2_500_000
    )
    assert result == {"resource_name": "customers/123/adGroupCriteria/111~222"}
    assert mock_op.update.cpc_bid_micros == 2_500_000

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    keywords.update_keyword_bid(
        CUSTOMER_ID,
        AD_GROUP_ID,
        CRITERION_ID,
        1_000_000,
        login_customer_id="999",
    )
    mock_ads_client._mock_get.assert_any_call("999")
