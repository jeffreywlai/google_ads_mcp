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
import pytest


CUSTOMER_ID = "1234567890"


def test_list_campaign_search_term_insights_builds_query():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    result = search_terms.list_campaign_search_term_insights(
        CUSTOMER_ID,
        campaign_id="111",
        min_clicks=5,
        min_impressions=10,
    )

  query = mock_query.call_args.kwargs["query"]
  assert "FROM campaign_search_term_insight" in query
  assert "campaign.id = 111" in query
  assert "metrics.clicks >= 5" in query
  assert "metrics.impressions >= 10" in query
  assert "segments.date DURING LAST_30_DAYS" in query
  assert "metrics.cost_micros" not in query
  assert "segments.search_term" not in query
  assert "segments.search_subcategory" not in query
  assert "LIMIT" not in query
  assert mock_query.call_args.kwargs["page_size"] == 1000
  assert result["returned_count"] == 0
  assert result["total_count"] == 0
  assert result["truncated"] is False


def test_list_campaign_search_term_insights_term_detail_uses_insight_id():
  rows = [
      {
          "campaign_search_term_insight.id": "7",
          "segments.search_term": "brand shoes",
      },
      {
          "campaign_search_term_insight.id": "7",
          "segments.search_term": "brand shoes sale",
      },
  ]

  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query_page",
      return_value={
          "rows": [rows[0]],
          "next_page_token": "1",
          "total_results_count": 2,
      },
  ) as mock_query:
    result = search_terms.list_campaign_search_term_insights(
        CUSTOMER_ID,
        campaign_id="111",
        insight_id="7",
        limit=1,
    )

  query = mock_query.call_args.kwargs["query"]
  assert "campaign_search_term_insight.id = 7" in query
  assert "segments.search_term" in query
  assert "segments.search_subcategory" in query
  assert "ORDER BY" not in query
  assert "LIMIT" not in query
  assert result["campaign_search_term_insights"] == [rows[0]]
  assert result["returned_count"] == 1
  assert result["total_count"] == 2
  assert result["truncated"] is True
  assert result["next_page_token"] == "1"


def test_list_campaign_search_term_insights_rejects_empty_insight_id():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query_page"
  ) as mock_query:
    with pytest.raises(
        search_terms.ToolError,
        match="insight_id must be a non-empty integer",
    ):
      search_terms.list_campaign_search_term_insights(
          CUSTOMER_ID,
          campaign_id="111",
          insight_id="",
      )

  mock_query.assert_not_called()


@pytest.mark.parametrize("field_name", ["min_clicks", "min_impressions"])
def test_list_campaign_search_term_insights_rejects_float_thresholds(
    field_name,
):
  kwargs = {"campaign_id": "111", field_name: 1.5}
  with pytest.raises(search_terms.ToolError, match=f"{field_name} must"):
    search_terms.list_campaign_search_term_insights(CUSTOMER_ID, **kwargs)


def test_list_campaign_search_term_insights_returns_campaign_context():
  rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "segments.search_term": "brand shoes",
      }
  ]

  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query_page",
      return_value={
          "rows": rows,
          "next_page_token": None,
          "total_results_count": 1,
      },
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
      result = search_terms.list_campaign_search_term_insights(
          CUSTOMER_ID,
          campaign_id="111",
      )

  assert result["campaign_search_term_insights"] == rows
  assert result["campaign_context"] == {
      "111": {
          "campaign.name": "Brand",
          "campaign.status": "ENABLED",
          "recent_30_day_cost_micros": 2_000_000,
      }
  }
  assert result["returned_count"] == 1
  assert result["total_count"] == 1


def test_list_campaign_search_term_insights_requires_campaign_id():
  with pytest.raises(TypeError):
    # pylint: disable=no-value-for-parameter
    getattr(search_terms, "list_campaign_search_term_insights")(CUSTOMER_ID)


def test_list_customer_search_term_insights_rejects_campaign_filters():
  with pytest.raises(
      search_terms.ToolError,
      match="does not support campaign_id/campaign_ids filters",
  ):
    search_terms.list_customer_search_term_insights(
        CUSTOMER_ID,
        campaign_ids=["111", "222"],
    )


def test_list_customer_search_term_insights_term_detail_paginates():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query_page",
      return_value={
          "rows": [{"customer_search_term_insight.id": "1"}],
          "next_page_token": "1",
          "total_results_count": 2,
      },
  ) as mock_query:
    result = search_terms.list_customer_search_term_insights(
        CUSTOMER_ID,
        insight_id="1",
        limit=1,
    )

  query = mock_query.call_args.kwargs["query"]
  assert "ORDER BY" not in query
  assert "LIMIT" not in query
  assert result == {
      "customer_search_term_insights": [
          {"customer_search_term_insight.id": "1"}
      ],
      "returned_count": 1,
      "total_count": 2,
      "total_page_count": 2,
      "truncated": True,
      "next_page_token": "1",
      "page_size": 1,
  }


def test_list_customer_search_term_insights_rejects_empty_insight_id():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query_page"
  ) as mock_query:
    with pytest.raises(
        search_terms.ToolError,
        match="insight_id must be a non-empty integer",
    ):
      search_terms.list_customer_search_term_insights(
          CUSTOMER_ID,
          insight_id="",
      )

  mock_query.assert_not_called()


@pytest.mark.parametrize("field_name", ["min_clicks", "min_impressions"])
def test_list_customer_search_term_insights_rejects_float_thresholds(
    field_name,
):
  with pytest.raises(search_terms.ToolError, match=f"{field_name} must"):
    search_terms.list_customer_search_term_insights(
        CUSTOMER_ID,
        **{field_name: 1.5},
    )


def test_list_customer_search_term_insights_uses_bulk_default_page_size():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    search_terms.list_customer_search_term_insights(CUSTOMER_ID)

  assert mock_query.call_args.kwargs["page_size"] == 1000


def test_list_customer_search_terms_rejects_campaign_filter_with_insight_id():
  with pytest.raises(
      search_terms.ToolError,
      match="does not support campaign_id/campaign_ids filters",
  ):
    search_terms.list_customer_search_term_insights(
        CUSTOMER_ID,
        campaign_id="111",
        insight_id="7",
    )


def test_compare_search_terms_returns_period_diff():
  period_a_rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "ad_group.id": "222",
          "ad_group.name": "Core",
          "search_term_view.search_term": "new winner",
          "metrics.clicks": 20,
          "metrics.cost_micros": 2_000_000,
          "metrics.conversions": 2,
          "metrics.conversions_value": 100.0,
      },
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "ad_group.id": "222",
          "ad_group.name": "Core",
          "search_term_view.search_term": "shared term",
          "metrics.clicks": 30,
          "metrics.cost_micros": 3_000_000,
          "metrics.conversions": 3,
          "metrics.conversions_value": 150.0,
      },
  ]
  period_b_rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "ad_group.id": "222",
          "ad_group.name": "Core",
          "search_term_view.search_term": "shared term",
          "metrics.clicks": 10,
          "metrics.cost_micros": 1_000_000,
          "metrics.conversions": 1,
          "metrics.conversions_value": 50.0,
      },
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "ad_group.id": "222",
          "ad_group.name": "Core",
          "search_term_view.search_term": "lost term",
          "metrics.clicks": 12,
          "metrics.cost_micros": 1_200_000,
          "metrics.conversions": 0,
          "metrics.conversions_value": 0.0,
      },
  ]

  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      side_effect=[period_a_rows, period_b_rows],
  ) as mock_query:
    with mock.patch(
        "ads_mcp.tools.search_terms.get_campaign_context",
        return_value={"111": {"campaign.name": "Brand"}},
    ):
      result = search_terms.compare_search_terms(
          CUSTOMER_ID,
          period_a={"start_date": "2026-04-13", "end_date": "2026-04-14"},
          period_b={"start_date": "2026-04-06", "end_date": "2026-04-07"},
          campaign_ids='["111"]',
          ad_group_id="222",
          period_limit=50,
          top_n=5,
      )

  first_query = mock_query.call_args_list[0].args[0]
  assert "FROM search_term_view" in first_query
  assert "campaign.id IN (111)" in first_query
  assert "ad_group.id = 222" in first_query
  assert "segments.date BETWEEN '2026-04-13' AND '2026-04-14'" in first_query
  assert "ORDER BY metrics.clicks DESC" in first_query
  assert "LIMIT 50" in first_query
  assert result["new_count"] == 1
  assert result["lost_count"] == 1
  assert result["common_count"] == 1
  assert result["new_terms"][0]["search_term"] == "new winner"
  assert result["lost_terms"][0]["search_term"] == "lost term"
  assert result["improved_terms"][0]["search_term"] == "shared term"
  assert result["improved_terms"][0]["delta"]["metrics.clicks"] == 20
  assert result["campaign_context"] == {"111": {"campaign.name": "Brand"}}


def test_compare_search_terms_ignores_empty_string_campaign_ids():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      side_effect=[[], []],
  ) as mock_query:
    with mock.patch(
        "ads_mcp.tools.search_terms.get_campaign_context",
        return_value={},
    ):
      search_terms.compare_search_terms(
          CUSTOMER_ID,
          period_a={"start_date": "2026-04-13", "end_date": "2026-04-14"},
          period_b={"start_date": "2026-04-06", "end_date": "2026-04-07"},
          campaign_ids="[]",
      )

  first_query = mock_query.call_args_list[0].args[0]
  assert "campaign.id IN ()" not in first_query
  assert "campaign.id IN" not in first_query


def test_compare_search_terms_preserves_status_and_match_type_dimensions():
  period_a_rows = [
      {
          "campaign.id": "111",
          "ad_group.id": "222",
          "search_term_view.search_term": "same text",
          "search_term_view.status": "NONE",
          "segments.search_term_match_type": "BROAD",
          "metrics.clicks": 20,
          "metrics.cost_micros": 2_000_000,
          "metrics.conversions": 0,
          "metrics.conversions_value": 0.0,
      },
      {
          "campaign.id": "111",
          "ad_group.id": "222",
          "search_term_view.search_term": "same text",
          "search_term_view.status": "NONE",
          "segments.search_term_match_type": "EXACT",
          "metrics.clicks": 10,
          "metrics.cost_micros": 1_000_000,
          "metrics.conversions": 0,
          "metrics.conversions_value": 0.0,
      },
  ]

  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      side_effect=[period_a_rows, []],
  ):
    with mock.patch(
        "ads_mcp.tools.search_terms.get_campaign_context",
        return_value={},
    ):
      result = search_terms.compare_search_terms(
          CUSTOMER_ID,
          period_a="LAST_7_DAYS",
          period_b="LAST_14_DAYS",
          top_n=5,
      )

  assert result["period_a_row_count"] == 2
  assert result["new_count"] == 2
  assert {entry["match_type"] for entry in result["new_terms"]} == {
      "BROAD",
      "EXACT",
  }


def test_compare_search_terms_orders_period_queries_by_sort_metric():
  with mock.patch(
      "ads_mcp.tools.search_terms.run_gaql_query",
      side_effect=[[], []],
  ) as mock_query:
    search_terms.compare_search_terms(
        CUSTOMER_ID,
        period_a="LAST_7_DAYS",
        period_b="LAST_14_DAYS",
        sort_by="COST",
    )

  assert (
      "ORDER BY metrics.cost_micros DESC"
      in mock_query.call_args_list[0].args[0]
  )
  assert (
      "ORDER BY metrics.cost_micros DESC"
      in mock_query.call_args_list[1].args[0]
  )


def test_compare_search_terms_rejects_bad_sort_by():
  with pytest.raises(search_terms.ToolError, match="Invalid sort_by"):
    search_terms.compare_search_terms(
        CUSTOMER_ID,
        period_a="LAST_7_DAYS",
        period_b="LAST_14_DAYS",
        sort_by="ROAS",
    )


def test_compare_search_terms_rejects_non_string_sort_by():
  with pytest.raises(search_terms.ToolError, match="sort_by must"):
    search_terms.compare_search_terms(
        CUSTOMER_ID,
        period_a="LAST_7_DAYS",
        period_b="LAST_14_DAYS",
        sort_by=1,
    )


@pytest.mark.parametrize("min_clicks", [True, "1", None, 0.5])
def test_compare_search_terms_rejects_non_numeric_thresholds(min_clicks):
  with pytest.raises(search_terms.ToolError, match="min_clicks must"):
    search_terms.compare_search_terms(
        CUSTOMER_ID,
        period_a="LAST_7_DAYS",
        period_b="LAST_14_DAYS",
        min_clicks=min_clicks,
    )


def test_compare_search_terms_rejects_float_cost_threshold():
  with pytest.raises(search_terms.ToolError, match="min_cost_micros must"):
    search_terms.compare_search_terms(
        CUSTOMER_ID,
        period_a="LAST_7_DAYS",
        period_b="LAST_14_DAYS",
        min_cost_micros=0.5,
    )


def test_compare_search_terms_rejects_malformed_ad_group_id():
  with pytest.raises(search_terms.ToolError, match="ad_group_id must"):
    search_terms.compare_search_terms(
        CUSTOMER_ID,
        period_a="LAST_7_DAYS",
        period_b="LAST_14_DAYS",
        ad_group_id="abc",
    )


def test_analyze_search_terms_rejects_bool_threshold():
  with pytest.raises(
      search_terms.ToolError,
      match="min_negative_clicks must be an integer",
  ):
    search_terms.analyze_search_terms(
        CUSTOMER_ID,
        min_negative_clicks=True,
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("min_negative_clicks", 1.5),
        ("min_negative_cost_micros", 1.5),
    ],
)
def test_analyze_search_terms_rejects_float_integer_thresholds(
    field_name,
    value,
):
  with pytest.raises(search_terms.ToolError, match=f"{field_name} must"):
    search_terms.analyze_search_terms(
        CUSTOMER_ID,
        **{field_name: value},
    )


def test_analyze_search_terms_rejects_malformed_campaign_id():
  with pytest.raises(search_terms.ToolError, match="campaign_id must"):
    search_terms.analyze_search_terms(
        CUSTOMER_ID,
        campaign_id="abc",
    )


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
