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

"""Tests for the campaign and budget management tools."""

from unittest import mock

from ads_mcp.tools import campaigns
import pytest


CUSTOMER_ID = "1234567890"
CAMPAIGN_ID = "111"
BUDGET_ID = "222"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.campaigns.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


class TestSetCampaignStatus:

  def test_pauses_campaign(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.campaign_path.return_value = "customers/123/campaigns/111"
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaigns/111")
    ]

    result = campaigns.set_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "PAUSED")
    assert result == {"resource_name": "customers/123/campaigns/111"}

  def test_enables_campaign(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.campaign_path.return_value = "customers/123/campaigns/111"
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaigns/111")
    ]

    result = campaigns.set_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "ENABLED")
    assert result == {"resource_name": "customers/123/campaigns/111"}

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    campaigns.set_campaign_status(
        CUSTOMER_ID, CAMPAIGN_ID, "PAUSED", login_customer_id="999"
    )
    mock_ads_client._mock_get.assert_called_with("999")


class TestUpdateCampaignBudget:

  def test_updates_budget(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.campaign_budget_path.return_value = (
        "customers/123/campaignBudgets/222"
    )
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_campaign_budgets.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaignBudgets/222")
    ]

    result = campaigns.update_campaign_budget(
        CUSTOMER_ID, BUDGET_ID, 50_000_000
    )
    assert result == {"resource_name": "customers/123/campaignBudgets/222"}
    assert mock_op.update.amount_micros == 50_000_000
