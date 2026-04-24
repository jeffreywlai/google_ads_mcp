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
from google.ads.googleads.v24.enums.types.targeting_dimension import (
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


class TestSetCampaignViewThroughConversionOptimization:

  def test_sets_view_through_optimization(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.campaign_path.return_value = "customers/123/campaigns/111"
    mock_op = mock_ads_client.get_type.return_value
    mock_op.update_mask.paths = []
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaigns/111")
    ]

    result = campaigns.set_campaign_view_through_conversion_optimization(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        True,
    )

    assert result == {
        "resource_name": "customers/123/campaigns/111",
        "view_through_conversion_optimization_enabled": True,
    }
    assert mock_op.update.view_through_conversion_optimization_enabled is True
    assert mock_op.update_mask.paths == [
        "view_through_conversion_optimization_enabled"
    ]

  def test_rejects_non_bool_enabled(self, mock_ads_client):
    with pytest.raises(ToolError, match="enabled must be a boolean"):
      campaigns.set_campaign_view_through_conversion_optimization(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          "true",
      )


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

  @pytest.mark.parametrize("campaign_ids", ["", [], "[]"])
  def test_list_campaign_audiences_rejects_empty_campaign_ids(
      self, campaign_ids
  ):
    with mock.patch(
        "ads_mcp.tools.campaigns.run_gaql_query_page"
    ) as mock_query:
      with pytest.raises(ToolError, match="campaign_ids must not be empty"):
        campaigns.list_campaign_audiences(CUSTOMER_ID, campaign_ids)

    mock_query.assert_not_called()

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
                "type": "USER_INTEREST",
                "resource_name": "customers/123/userInterests/90206",
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
        "successes": [
            {
                "index": 0,
                "resource_name": "customers/123/campaignCriteria/111~7001",
                "criterion_id": "7001",
            }
        ],
        "failures": [{"code": 3, "reason": "Bad audience", "indexes": [1]}],
        "partial_failure_error": {"code": 3, "message": "Bad audience"},
    }
    criterion_service.mutate_campaign_criteria.assert_called_once()
    call_args = criterion_service.mutate_campaign_criteria.call_args.kwargs
    request = call_args["request"]
    assert request["customer_id"] == CUSTOMER_ID
    assert request["partial_failure"] is True
    assert len(request["operations"]) == 2

    assert operations[0].create.campaign == "customers/123/campaigns/111"
    assert operations[0].create.bid_modifier == 1.5
    assert (
        operations[0].create.user_interest.user_interest_category
        == "customers/123/userInterests/90206"
    )
    assert (
        operations[1].create.custom_audience.custom_audience
        == "customers/123/customAudiences/789"
    )

  def test_adds_campaign_audiences_accepts_aliases_and_negative(
      self, mock_ads_client
  ):
    campaign_service = mock.Mock()
    criterion_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "CampaignService": campaign_service,
        "CampaignCriterionService": criterion_service,
    }[name]
    campaign_service.campaign_path.return_value = "customers/123/campaigns/111"

    operation = mock.Mock()
    operation.create = mock.Mock()
    mock_ads_client.get_type.return_value = operation
    response = criterion_service.mutate_campaign_criteria.return_value
    response.results = [
        mock.Mock(resource_name="customers/123/campaignCriteria/111~7002")
    ]
    response.partial_failure_error = None

    result = campaigns.add_campaign_audiences(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        [
            {
                "type": "USER_INTEREST",
                "user_interest_id": 90206,
                "negative": True,
            }
        ],
    )

    assert result["created_criterion_ids"] == ["7002"]
    assert operation.create.negative is True
    assert (
        operation.create.user_interest.user_interest_category
        == f"customers/{CUSTOMER_ID}/userInterests/90206"
    )

  @pytest.mark.parametrize("audience_id", [True, 1.9])
  def test_adds_campaign_audiences_rejects_non_integer_aliases(
      self,
      mock_ads_client,
      audience_id,
  ):
    with pytest.raises(ToolError, match="must be an integer string"):
      campaigns.add_campaign_audiences(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          [
              {
                  "type": "USER_INTEREST",
                  "user_interest_id": audience_id,
              }
          ],
      )

    mock_ads_client.get_service.assert_not_called()

  def test_adds_campaign_audiences_omits_empty_partial_failure_results(
      self, mock_ads_client
  ):
    campaign_service = mock.Mock()
    criterion_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "CampaignService": campaign_service,
        "CampaignCriterionService": criterion_service,
    }[name]
    campaign_service.campaign_path.return_value = "customers/123/campaigns/111"

    operation = mock.Mock()
    operation.create = mock.Mock()
    mock_ads_client.get_type.return_value = operation
    response = criterion_service.mutate_campaign_criteria.return_value
    response.results = [
        mock.Mock(resource_name=""),
        mock.Mock(resource_name="customers/123/campaignCriteria/111~7002"),
    ]
    response.partial_failure_error = None

    result = campaigns.add_campaign_audiences(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        [
            {
                "type": "USER_INTEREST",
                "user_interest_id": 90206,
            },
            {
                "type": "USER_INTEREST",
                "user_interest_id": 90207,
            },
        ],
    )

    assert result["resource_names"] == [
        "customers/123/campaignCriteria/111~7002"
    ]
    assert result["created_criterion_ids"] == ["7002"]
    assert result["successes"] == [
        {
            "index": 1,
            "resource_name": "customers/123/campaignCriteria/111~7002",
            "criterion_id": "7002",
        }
    ]

  def test_adds_campaign_audiences_maps_partial_failure_indexes(
      self, mock_ads_client
  ):
    campaign_service = mock.Mock()
    criterion_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "CampaignService": campaign_service,
        "CampaignCriterionService": criterion_service,
    }[name]
    campaign_service.campaign_path.return_value = "customers/123/campaigns/111"

    def get_type(name):
      assert name == "CampaignCriterionOperation"
      operation = mock.Mock()
      operation.create = mock.Mock()
      return operation

    mock_ads_client.get_type.side_effect = get_type

    response = criterion_service.mutate_campaign_criteria.return_value
    response.results = [
        mock.Mock(resource_name="customers/123/campaignCriteria/111~7002")
    ]
    response.partial_failure_error = {
        "code": 3,
        "message": "Bad first audience",
        "details": [
            {
                "errors": [
                    {
                        "location": {
                            "fieldPathElements": [
                                {"fieldName": "operations", "index": 0}
                            ]
                        }
                    }
                ]
            }
        ],
    }

    result = campaigns.add_campaign_audiences(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        [
            {"type": "USER_INTEREST", "user_interest_id": 90206},
            {"type": "USER_INTEREST", "user_interest_id": 90207},
        ],
    )

    assert result["failures"] == [
        {"code": 3, "reason": "Bad first audience", "indexes": [0]}
    ]
    assert result["successes"] == [
        {
            "index": 1,
            "resource_name": "customers/123/campaignCriteria/111~7002",
            "criterion_id": "7002",
        }
    ]

  def test_adds_campaign_audiences_rejects_blank_resource_name(
      self, mock_ads_client
  ):
    with pytest.raises(ToolError, match="resource_name must be a non-empty"):
      campaigns.add_campaign_audiences(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          [{"type": "USER_INTEREST", "resource_name": "   "}],
      )

    mock_ads_client.get_service.assert_not_called()

  def test_diff_campaign_audiences_returns_copy_ready_payloads(self):
    rows = [
        {
            "campaign.id": CAMPAIGN_ID,
            "campaign_criterion.type": "USER_INTEREST",
            "campaign_criterion.negative": False,
            "campaign_criterion.user_interest.user_interest_category": (
                f"customers/{CUSTOMER_ID}/userInterests/90206"
            ),
        },
        {
            "campaign.id": "222",
            "campaign_criterion.type": "USER_LIST",
            "campaign_criterion.negative": False,
            "campaign_criterion.user_list.user_list": (
                f"customers/{CUSTOMER_ID}/userLists/99"
            ),
        },
    ]
    with mock.patch(
        "ads_mcp.tools.campaigns._read_campaign_audiences",
        return_value=rows,
    ):
      result = campaigns.diff_campaign_audiences(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          "222",
      )

    assert result["missing_in_target"] == [
        {
            "type": "USER_INTEREST",
            "resource_name": f"customers/{CUSTOMER_ID}/userInterests/90206",
            "negative": False,
        }
    ]
    assert result["missing_count"] == 1

  def test_diff_campaign_audiences_normalizes_campaign_ids(self):
    rows = [
        {
            "campaign.id": "111",
            "campaign_criterion.type": "USER_INTEREST",
            "campaign_criterion.negative": False,
            "campaign_criterion.user_interest.user_interest_category": (
                f"customers/{CUSTOMER_ID}/userInterests/90206"
            ),
        },
        {
            "campaign.id": "222",
            "campaign_criterion.type": "USER_LIST",
            "campaign_criterion.negative": False,
            "campaign_criterion.user_list.user_list": (
                f"customers/{CUSTOMER_ID}/userLists/99"
            ),
        },
    ]
    with mock.patch(
        "ads_mcp.tools.campaigns._read_campaign_audiences",
        return_value=rows,
    ):
      result = campaigns.diff_campaign_audiences(
          CUSTOMER_ID,
          " 00111 ",
          "00222",
      )

    assert result["source_campaign_id"] == "111"
    assert result["target_campaign_id"] == "222"
    assert result["missing_count"] == 1

  def test_copy_audiences_uses_normalized_target_campaign_id(self):
    diff = {
        "source_campaign_id": "111",
        "target_campaign_id": "222",
        "missing_in_target": [
            {
                "type": "USER_INTEREST",
                "resource_name": f"customers/{CUSTOMER_ID}/userInterests/90206",
                "negative": False,
            }
        ],
        "common_count": 0,
        "target_only_count": 0,
    }
    with mock.patch(
        "ads_mcp.tools.campaigns.diff_campaign_audiences",
        return_value=diff,
    ):
      with mock.patch(
          "ads_mcp.tools.campaigns.add_campaign_audiences",
          return_value={"resource_names": []},
      ) as mock_add:
        result = campaigns.copy_audiences_between_campaigns(
            CUSTOMER_ID,
            " 00111 ",
            " 00222 ",
            dry_run=False,
        )

    assert result["source_campaign_id"] == "111"
    assert result["target_campaign_id"] == "222"
    assert mock_add.call_args.kwargs["campaign_id"] == "222"

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

  @pytest.mark.parametrize(
      ("criterion_ids", "expected_ids"),
      [
          (["7001", "7002"], ["7001", "7002"]),
          ("7001", ["7001"]),
          ("7001,7002", ["7001", "7002"]),
      ],
  )
  def test_removes_campaign_audiences_by_criterion_id(
      self,
      mock_ads_client,
      criterion_ids,
      expected_ids,
  ):
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
        mock.Mock(resource_name=f"customers/123/campaignCriteria/111~{value}")
        for value in expected_ids
    ]

    result = campaigns.remove_campaign_audiences(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        criterion_ids,
    )

    assert result == {
        "removed_resource_names": [
            f"customers/123/campaignCriteria/111~{value}"
            for value in expected_ids
        ]
    }
    assert [operation.remove for operation in operations] == [
        f"customers/1234567890/campaignCriteria/111~{value}"
        for value in expected_ids
    ]

  @pytest.mark.parametrize(
      "criterion_ids",
      [
          "333 OR metrics.clicks > 0",
          ["333", "333"],
          "333,333",
      ],
  )
  def test_removes_campaign_audiences_rejects_bad_criterion_ids(
      self,
      mock_ads_client,
      criterion_ids,
  ):
    with pytest.raises(ToolError, match="criterion_ids must"):
      campaigns.remove_campaign_audiences(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          criterion_ids,
      )

    mock_ads_client.get_service.assert_not_called()
