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

"""Tests for search_terms.py."""

from unittest import mock

from ads_mcp.tools import search_terms


CUSTOMER_ID = "1234567890"


def test_list_campaign_search_term_insights_builds_query():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      return_value=[],
  ) as mock_query:
    search_terms.list_campaign_search_term_insights(
        CUSTOMER_ID,
        campaign_ids=["111"],
        min_clicks=5,
        min_impressions=10,
    )

  query = mock_query.call_args.args[0]
  assert "FROM campaign_search_term_insight" in query
  assert "campaign.id IN (111)" in query
  assert "metrics.clicks >= 5" in query
  assert "metrics.impressions >= 10" in query
  assert "segments.date DURING LAST_30_DAYS" in query
  assert "metrics.cost_micros" not in query


def test_list_campaign_search_term_insights_accepts_campaign_id_alias():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      return_value=[],
  ) as mock_query:
    search_terms.list_campaign_search_term_insights(
        CUSTOMER_ID,
        campaign_id="111",
    )

  query = mock_query.call_args.args[0]
  assert "campaign.id IN (111)" in query


def test_list_campaign_search_term_insights_returns_campaign_context():
  rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "segments.search_term": "brand shoes",
      }
  ]

  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      return_value=rows,
  ):
    with mock.patch(
        "ads_mcp.tools.search_terms.get_campaign_context",
        return_value={
            "111": {
                "campaign.name": "Brand",
                "campaign.status": "ENABLED",
                "recent_30_day_cost_micros": 2_000_000,
            }
        },
    ):
      result = search_terms.list_campaign_search_term_insights(CUSTOMER_ID)

  assert result["campaign_search_term_insights"] == rows
  assert result["campaign_context"] == {
      "111": {
          "campaign.name": "Brand",
          "campaign.status": "ENABLED",
          "recent_30_day_cost_micros": 2_000_000,
      }
  }


def test_list_customer_search_term_insights_uses_campaign_resources():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      return_value=[],
  ) as mock_query:
    search_terms.list_customer_search_term_insights(
        CUSTOMER_ID, campaign_ids=["111", "222"]
    )

  query = mock_query.call_args.args[0]
  assert "FROM customer_search_term_insight" in query
  assert "segments.campaign IN (" in query
  assert "segments.campaign," not in query
  assert "'customers/1234567890/campaigns/111'" in query
  assert "'customers/1234567890/campaigns/222'" in query
  assert "metrics.cost_micros" not in query


def test_list_customer_search_term_insights_accepts_campaign_id_alias():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      return_value=[],
  ) as mock_query:
    search_terms.list_customer_search_term_insights(
        CUSTOMER_ID,
        campaign_id="111",
    )

  query = mock_query.call_args.args[0]
  assert "'customers/1234567890/campaigns/111'" in query


def test_analyze_search_terms_returns_candidates():
  rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "search_term_view.search_term": "free trial",
          "search_term_view.status": "NONE",
          "metrics.clicks": 15,
          "metrics.cost_micros": 7_000_000,
          "metrics.conversions": 0,
      },
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "search_term_view.search_term": "buy product",
          "search_term_view.status": "NONE",
          "metrics.clicks": 8,
          "metrics.cost_micros": 3_000_000,
          "metrics.conversions": 2,
      },
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "search_term_view.search_term": "already exact",
          "search_term_view.status": "ADDED_EXACT",
          "metrics.clicks": 20,
          "metrics.cost_micros": 10_000_000,
          "metrics.conversions": 3,
      },
  ]

  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      return_value=rows,
  ):
    with mock.patch(
        "ads_mcp.tools.search_terms.get_campaign_context",
        return_value={
            "111": {
                "campaign.name": "Brand",
                "campaign.status": "ENABLED",
                "recent_30_day_cost_micros": 6_000_000,
            }
        },
    ):
      result = search_terms.analyze_search_terms(CUSTOMER_ID)

  assert result["negative_keyword_candidates"] == [rows[0]]
  assert result["exact_match_candidates"] == [rows[1]]
  assert result["analyzed_row_count"] == 3
  assert result["campaign_context"] == {
      "111": {
          "campaign.name": "Brand",
          "campaign.status": "ENABLED",
          "recent_30_day_cost_micros": 6_000_000,
      }
  }
