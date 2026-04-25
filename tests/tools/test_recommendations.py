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

"""Tests for recommendations.py."""

from unittest import mock

from ads_mcp.tools import recommendations
from fastmcp.exceptions import ToolError
import pytest


CUSTOMER_ID = "1234567890"


def test_list_recommendations_builds_filtered_query():
  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    result = recommendations.list_recommendations(
        CUSTOMER_ID,
        recommendation_types=["campaign_budget", "keyword"],
        campaign_ids=["111", "222"],
    )

  query = mock_query.call_args.kwargs["query"]
  assert "FROM recommendation" in query
  assert "recommendation.type IN (CAMPAIGN_BUDGET, KEYWORD)" in query
  assert "campaign.id IN (111, 222)" in query
  assert "recommendation.dismissed = FALSE" in query
  assert "recommendation.impact" in query
  assert mock_query.call_args.kwargs["page_size"] == 500
  assert result["returned_count"] == 0
  assert result["total_count"] == 0
  assert result["truncated"] is False


def test_list_recommendations_ignores_empty_string_list_filters():
  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    recommendations.list_recommendations(
        CUSTOMER_ID,
        recommendation_types="[]",
        campaign_ids="[]",
    )

  query = mock_query.call_args.kwargs["query"]
  assert "recommendation.type IN ()" not in query
  assert "campaign.id IN ()" not in query
  assert "recommendation.type IN" not in query
  assert "campaign.id IN" not in query


def test_list_recommendation_subscriptions_returns_paging_metadata():
  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query_page",
      return_value={
          "rows": [
              {
                  "recommendation_subscription.resource_name": (
                      "customers/123/recommendationSubscriptions/KEYWORD"
                  )
              }
          ],
          "next_page_token": "100",
          "total_results_count": 150,
      },
  ):
    result = recommendations.list_recommendation_subscriptions(
        CUSTOMER_ID,
        limit=100,
    )

  assert result["recommendation_subscriptions"] == [
      {
          "recommendation_subscription.resource_name": (
              "customers/123/recommendationSubscriptions/KEYWORD"
          )
      }
  ]
  assert result["returned_count"] == 1
  assert result["total_count"] == 150
  assert result["total_page_count"] == 2
  assert result["truncated"] is True
  assert result["next_page_token"] == "100"


def test_get_optimization_score_summary_returns_breakdown():
  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query",
      side_effect=[
          [
              {
                  "customer.id": "1234567890",
                  "customer.descriptive_name": "Test Account",
                  "customer.currency_code": "USD",
                  "customer.optimization_score": 0.71,
                  "customer.optimization_score_weight": 0.95,
                  "metrics.optimization_score_uplift": 0.12,
                  "metrics.optimization_score_url": "https://example.com",
              }
          ],
          [
              {
                  "segments.recommendation_type": "UNSPECIFIED",
                  "metrics.optimization_score_uplift": 0.2,
                  "metrics.optimization_score_url": "https://ignored",
              },
              {
                  "segments.recommendation_type": "CAMPAIGN_BUDGET",
                  "metrics.optimization_score_uplift": 0.07,
                  "metrics.optimization_score_url": "https://budget",
              },
              {
                  "segments.recommendation_type": "KEYWORD",
                  "metrics.optimization_score_uplift": 0.05,
                  "metrics.optimization_score_url": "https://keyword",
              },
          ],
      ],
  ):
    result = recommendations.get_optimization_score_summary(CUSTOMER_ID)

  assert result["customer_name"] == "Test Account"
  assert result["optimization_score"] == 0.71
  assert result["total_optimization_score_uplift"] == 0.12
  assert result["recommendation_type_breakdown"] == [
      {
          "recommendation_type": "CAMPAIGN_BUDGET",
          "optimization_score_uplift": 0.07,
          "optimization_score_url": "https://budget",
      },
      {
          "recommendation_type": "KEYWORD",
          "optimization_score_uplift": 0.05,
          "optimization_score_url": "https://keyword",
      },
  ]


def test_get_optimization_score_summary_does_not_filter_query_on_enum():
  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query",
      side_effect=[
          [{"customer.id": "1234567890"}],
          [],
      ],
  ) as mock_query:
    recommendations.get_optimization_score_summary(CUSTOMER_ID)

  breakdown_query = mock_query.call_args_list[1].args[0]
  assert "NOT IN (UNSPECIFIED, UNKNOWN)" not in breakdown_query


@pytest.fixture
def mock_ads_client():
  with mock.patch(
      "ads_mcp.tools.recommendations.get_ads_client"
  ) as mock_get_ads_client:
    client = mock.Mock()
    client.get_type.return_value.update_mask.paths = []
    client.enums.RecommendationTypeEnum.KEYWORD = "KEYWORD_ENUM"
    client.enums.RecommendationTypeEnum.CAMPAIGN_BUDGET = (
        "CAMPAIGN_BUDGET_ENUM"
    )
    client.enums.RecommendationSubscriptionStatusEnum.PAUSED = "PAUSED_ENUM"
    client.enums.RecommendationSubscriptionStatusEnum.ENABLED = "ENABLED_ENUM"
    mock_get_ads_client.return_value = client
    yield client


def test_apply_recommendations_builds_operations(mock_ads_client):
  mock_service = mock_ads_client.get_service.return_value
  mock_service.apply_recommendation.return_value.results = [
      mock.Mock(resource_name="customers/123/recommendations/1"),
      mock.Mock(resource_name="customers/123/recommendations/2"),
  ]
  mock_service.apply_recommendation.return_value.partial_failure_error = None

  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query",
      return_value=[
          {
              "recommendation.resource_name": "customers/123/recommendations/1",
              "recommendation.type": "CAMPAIGN_BUDGET",
          },
          {
              "recommendation.resource_name": "customers/123/recommendations/2",
              "recommendation.type": "KEYWORD",
          },
      ],
  ):
    result = recommendations.apply_recommendations(
        CUSTOMER_ID,
        recommendation_resource_names=[
            "customers/123/recommendations/1",
            "customers/123/recommendations/2",
        ],
        parameters_by_resource_name={
            "customers/123/recommendations/1": {
                "new_budget_amount_micros": 20_000_000,
            }
        },
    )

  assert result == {
      "resource_names": [
          "customers/123/recommendations/1",
          "customers/123/recommendations/2",
      ]
  }
  call_kwargs = mock_service.apply_recommendation.call_args.kwargs
  request = call_kwargs["request"]
  assert request["customer_id"] == CUSTOMER_ID
  assert request["partial_failure"] is False
  assert request["operations"] == [
      {
          "resource_name": "customers/123/recommendations/1",
          "campaign_budget": {"new_budget_amount_micros": 20_000_000},
      },
      {
          "resource_name": "customers/123/recommendations/2",
          "keyword": {},
      },
  ]


def test_apply_recommendations_accepts_single_resource_string(
    mock_ads_client,
):
  mock_service = mock_ads_client.get_service.return_value
  mock_service.apply_recommendation.return_value.results = [
      mock.Mock(resource_name="customers/123/recommendations/1")
  ]
  mock_service.apply_recommendation.return_value.partial_failure_error = None

  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query",
      return_value=[
          {
              "recommendation.resource_name": "customers/123/recommendations/1",
              "recommendation.type": "KEYWORD",
          }
      ],
  ):
    recommendations.apply_recommendations(
        CUSTOMER_ID,
        recommendation_resource_names="customers/123/recommendations/1",
    )

  request = mock_service.apply_recommendation.call_args.kwargs["request"]
  assert request["operations"] == [
      {
          "resource_name": "customers/123/recommendations/1",
          "keyword": {},
      }
  ]


def test_apply_recommendations_rejects_unknown_parameter_key():
  with pytest.raises(ToolError, match="unknown resource names"):
    recommendations.apply_recommendations(
        CUSTOMER_ID,
        recommendation_resource_names=["customers/123/recommendations/1"],
        parameters_by_resource_name={
            "customers/123/recommendations/2": {"new_budget_amount_micros": 1}
        },
    )


def test_apply_recommendations_rejects_duplicate_resource_names(
    mock_ads_client,
):
  with mock.patch(
      "ads_mcp.tools.recommendations.run_gaql_query"
  ) as mock_query:
    with pytest.raises(ToolError, match="must not contain duplicates"):
      recommendations.apply_recommendations(
          CUSTOMER_ID,
          recommendation_resource_names=[
              "customers/123/recommendations/1",
              "customers/123/recommendations/1",
          ],
      )

  mock_query.assert_not_called()
  mock_ads_client.get_service.assert_not_called()


def test_dismiss_recommendations_calls_service(mock_ads_client):
  mock_service = mock_ads_client.get_service.return_value
  mock_service.dismiss_recommendation.return_value.results = [
      mock.Mock(resource_name="customers/123/recommendations/1")
  ]
  mock_service.dismiss_recommendation.return_value.partial_failure_error = None

  result = recommendations.dismiss_recommendations(
      CUSTOMER_ID, ["customers/123/recommendations/1"]
  )

  assert result == {"resource_names": ["customers/123/recommendations/1"]}
  request = mock_service.dismiss_recommendation.call_args.kwargs["request"]
  assert request["customer_id"] == CUSTOMER_ID
  assert request["partial_failure"] is False
  assert request["operations"] == [
      {"resource_name": "customers/123/recommendations/1"}
  ]


def test_dismiss_recommendations_accepts_json_stringified_list(
    mock_ads_client,
):
  mock_service = mock_ads_client.get_service.return_value
  mock_service.dismiss_recommendation.return_value.results = [
      mock.Mock(resource_name="customers/123/recommendations/1"),
      mock.Mock(resource_name="customers/123/recommendations/2"),
  ]
  mock_service.dismiss_recommendation.return_value.partial_failure_error = None

  recommendations.dismiss_recommendations(
      CUSTOMER_ID,
      '["customers/123/recommendations/1", '
      '"customers/123/recommendations/2"]',
  )

  request = mock_service.dismiss_recommendation.call_args.kwargs["request"]
  assert request["operations"] == [
      {"resource_name": "customers/123/recommendations/1"},
      {"resource_name": "customers/123/recommendations/2"},
  ]


def test_dismiss_recommendations_rejects_duplicate_resource_names(
    mock_ads_client,
):
  with pytest.raises(ToolError, match="must not contain duplicates"):
    recommendations.dismiss_recommendations(
        CUSTOMER_ID,
        '["customers/123/recommendations/1", '
        '"customers/123/recommendations/1"]',
    )

  mock_ads_client.get_service.assert_not_called()


def test_create_recommendation_subscription_defaults_to_paused(
    mock_ads_client,
):
  mock_service = mock_ads_client.get_service.return_value
  mock_operation = mock_ads_client.get_type.return_value
  mock_service.mutate_recommendation_subscription.return_value.results = [
      mock.Mock(
          resource_name="customers/123/recommendationSubscriptions/KEYWORD"
      )
  ]

  result = recommendations.create_recommendation_subscription(
      CUSTOMER_ID, "keyword"
  )

  assert result == {
      "resource_name": "customers/123/recommendationSubscriptions/KEYWORD"
  }
  assert mock_operation.create.type_ == "KEYWORD_ENUM"
  assert mock_operation.create.status == "PAUSED_ENUM"


def test_set_recommendation_subscription_status_updates_mask(mock_ads_client):
  mock_service = mock_ads_client.get_service.return_value
  mock_operation = mock_ads_client.get_type.return_value
  mock_service.mutate_recommendation_subscription.return_value.results = [
      mock.Mock(
          resource_name="customers/123/recommendationSubscriptions/KEYWORD"
      )
  ]

  recommendations.set_recommendation_subscription_status(
      CUSTOMER_ID,
      "customers/123/recommendationSubscriptions/KEYWORD",
      "enabled",
  )

  assert (
      mock_operation.update.resource_name
      == "customers/123/recommendationSubscriptions/KEYWORD"
  )
  assert mock_operation.update.status == "ENABLED_ENUM"
  assert mock_operation.update_mask.paths == ["status"]
