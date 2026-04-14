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

from types import SimpleNamespace
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v23.enums.types.targeting_dimension import (
    TargetingDimensionEnum,
)
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


class TestUpdateCampaignTargetingSetting:

  def test_updates_restrictions_and_warns_on_audience_reach_collapse(
      self, mock_ads_client
  ):
    campaign_service = mock.Mock()
    google_ads_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "CampaignService": campaign_service,
        "GoogleAdsService": google_ads_service,
    }[name]

    current_audience_restriction = SimpleNamespace(
        targeting_dimension=SimpleNamespace(name="AUDIENCE"),
        bid_only=True,
    )
    row = mock.Mock()
    row.campaign.targeting_setting.target_restrictions = [
        current_audience_restriction
    ]
    google_ads_service.search_stream.return_value = [mock.Mock(results=[row])]

    campaign_service.campaign_path.return_value = "customers/123/campaigns/111"
    response = campaign_service.mutate_campaigns.return_value
    response.results = [mock.Mock(resource_name="customers/123/campaigns/111")]

    operation = mock.Mock()
    operation.update = mock.Mock()
    operation.update_mask.paths = []
    mock_ads_client.get_type.return_value = operation

    result = campaigns.update_campaign_targeting_setting(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        [
            {"targeting_dimension": "AUDIENCE", "bid_only": False},
            {"targeting_dimension": "KEYWORD", "bid_only": True},
        ],
    )

    assert result["campaign_resource_name"] == "customers/123/campaigns/111"
    assert result["updated_restrictions"] == [
        {"targeting_dimension": "AUDIENCE", "bid_only": False},
        {"targeting_dimension": "KEYWORD", "bid_only": True},
    ]
    assert (
        result["warning"]
        == "AUDIENCE bid_only true->false can sharply reduce reach."
    )
    assert operation.update_mask.paths == [
        "targeting_setting.target_restrictions"
    ]

    updated = operation.update.targeting_setting.target_restrictions
    assert len(updated) == 2
    assert (
        updated[0].targeting_dimension
        == TargetingDimensionEnum.TargetingDimension.AUDIENCE
    )
    assert updated[0].bid_only is False
    assert (
        updated[1].targeting_dimension
        == TargetingDimensionEnum.TargetingDimension.KEYWORD
    )
    assert updated[1].bid_only is True

  def test_rejects_duplicate_targeting_dimensions(self, mock_ads_client):
    with pytest.raises(ToolError, match="Duplicate targeting_dimension"):
      campaigns.update_campaign_targeting_setting(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          [
              {"targeting_dimension": "AUDIENCE", "bid_only": True},
              {"targeting_dimension": "audience", "bid_only": False},
          ],
      )

  def test_rejects_non_numeric_campaign_id_for_targeting_read(
      self, mock_ads_client
  ):
    with pytest.raises(
        ToolError, match="campaign_id must be an integer string"
    ):
      campaigns.update_campaign_targeting_setting(
          CUSTOMER_ID,
          "111 OR 1=1",
          [{"targeting_dimension": "AUDIENCE", "bid_only": False}],
      )

  def test_skips_current_restrictions_read_when_warning_is_not_possible(
      self, mock_ads_client
  ):
    campaign_service = mock.Mock()
    google_ads_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "CampaignService": campaign_service,
        "GoogleAdsService": google_ads_service,
    }[name]
    campaign_service.campaign_path.return_value = "customers/123/campaigns/111"
    response = campaign_service.mutate_campaigns.return_value
    response.results = [mock.Mock(resource_name="customers/123/campaigns/111")]

    operation = mock.Mock()
    operation.update = mock.Mock()
    operation.update_mask.paths = []
    mock_ads_client.get_type.return_value = operation

    result = campaigns.update_campaign_targeting_setting(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        [{"targeting_dimension": "KEYWORD", "bid_only": True}],
    )

    assert result == {
        "campaign_resource_name": "customers/123/campaigns/111",
        "updated_restrictions": [
            {"targeting_dimension": "KEYWORD", "bid_only": True}
        ],
    }
    google_ads_service.search_stream.assert_not_called()


class TestAddCampaignAudiences:

  def test_adds_campaign_audiences_with_partial_failure_enabled(
      self, mock_ads_client
  ):
    campaign_service = mock.Mock()
    criterion_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "CampaignService": campaign_service,
        "CampaignCriterionService": criterion_service,
    }[name]
    campaign_service.campaign_path.return_value = "customers/123/campaigns/111"

    operations = []

    def get_type(name):
      assert name == "CampaignCriterionOperation"
      operation = mock.Mock()
      operation.create = mock.Mock()
      operations.append(operation)
      return operation

    mock_ads_client.get_type.side_effect = get_type

    response = criterion_service.mutate_campaign_criteria.return_value
    response.results = [
        mock.Mock(resource_name="customers/123/campaignCriteria/111~7001")
    ]
    response.partial_failure_error = {
        "code": 3,
        "message": "Bad audience",
        "details": [{"ignored": True}],
    }

    result = campaigns.add_campaign_audiences(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        [
            {
                "type": "USER_LIST",
                "resource_name": "customers/123/userLists/456",
                "bid_modifier": 1.5,
            },
            {
                "type": "CUSTOM_AUDIENCE",
                "resource_name": "customers/123/customAudiences/789",
            },
        ],
    )

    assert result == {
        "created_criterion_ids": ["7001"],
        "resource_names": [
            "customers/123/campaignCriteria/111~7001",
        ],
        "partial_failure_error": {"code": 3, "message": "Bad audience"},
    }
    criterion_service.mutate_campaign_criteria.assert_called_once()
    call_args = criterion_service.mutate_campaign_criteria.call_args.kwargs
    assert call_args["customer_id"] == CUSTOMER_ID
    assert call_args["partial_failure"] is True
    assert len(call_args["operations"]) == 2

    assert operations[0].create.campaign == "customers/123/campaigns/111"
    assert operations[0].create.bid_modifier == 1.5
    assert (
        operations[0].create.user_list.user_list
        == "customers/123/userLists/456"
    )
    assert (
        operations[1].create.custom_audience.custom_audience
        == "customers/123/customAudiences/789"
    )

  def test_rejects_invalid_audience_type(self, mock_ads_client):
    with pytest.raises(ToolError, match="Invalid audiences\\[0\\].type"):
      campaigns.add_campaign_audiences(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          [{"type": "NOT_REAL", "resource_name": "customers/123/userLists/1"}],
      )

  def test_rejects_modern_audience_resources(self, mock_ads_client):
    with pytest.raises(
        ToolError, match="AUDIENCE is not supported by CampaignCriterion"
    ):
      campaigns.add_campaign_audiences(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          [
              {
                  "type": "AUDIENCE",
                  "resource_name": "customers/123/audiences/99",
              }
          ],
      )


class TestRemoveCampaignAudiences:

  def test_removes_campaign_audiences_by_criterion_id(self, mock_ads_client):
    criterion_service = mock.Mock()
    mock_ads_client.get_service.return_value = criterion_service
    criterion_service.campaign_criterion_path.side_effect = (
        lambda customer_id, campaign_id, criterion_id: (
            "customers/"
            f"{customer_id}/campaignCriteria/{campaign_id}~{criterion_id}"
        )
    )

    operations = []

    def get_type(name):
      assert name == "CampaignCriterionOperation"
      operation = mock.Mock()
      operations.append(operation)
      return operation

    mock_ads_client.get_type.side_effect = get_type

    response = criterion_service.mutate_campaign_criteria.return_value
    response.results = [
        mock.Mock(resource_name="customers/123/campaignCriteria/111~7001"),
        mock.Mock(resource_name="customers/123/campaignCriteria/111~7002"),
    ]

    result = campaigns.remove_campaign_audiences(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        ["7001", "7002"],
    )

    assert result == {
        "removed_resource_names": [
            "customers/123/campaignCriteria/111~7001",
            "customers/123/campaignCriteria/111~7002",
        ]
    }
    assert (
        operations[0].remove
        == "customers/1234567890/campaignCriteria/111~7001"
    )
    assert (
        operations[1].remove
        == "customers/1234567890/campaignCriteria/111~7002"
    )
