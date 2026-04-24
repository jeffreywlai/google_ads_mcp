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

"""Tests for reporting.py."""

from unittest import mock

from ads_mcp.tools import reporting
from fastmcp.exceptions import ToolError
import pytest


CUSTOMER_ID = "123"


def test_list_device_performance_builds_campaign_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_device_performance(
        CUSTOMER_ID,
        campaign_ids=["111", "222"],
        date_range="LAST_7_DAYS",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM campaign" in query
  assert "segments.device" in query
  assert "campaign.id IN (111, 222)" in query
  assert "segments.date DURING LAST_7_DAYS" in query
  assert result["returned_count"] == 0
  assert result["total_count"] == 0


def test_list_device_performance_accepts_extended_range_and_string_ids(
    mocker,
):
  mocker.patch(
      "ads_mcp.tools._gaql._literal_date_bounds",
      return_value=(
          __import__("datetime").date(2026, 1, 23),
          __import__("datetime").date(2026, 4, 22),
      ),
  )
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_device_performance(
        CUSTOMER_ID,
        campaign_ids='["111", "222"]',
        date_range="LAST_90_DAYS",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "campaign.id IN (111, 222)" in query
  assert "segments.date BETWEEN '2026-01-23' AND '2026-04-22'" in query


def test_list_geographic_performance_uses_geographic_view():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_geographic_performance(
        CUSTOMER_ID,
        location_view="geographic",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM geographic_view" in query
  assert "geographic_view.country_criterion_id" in query
  assert "geographic_view.location_type" in query
  assert result["location_view"] == "GEOGRAPHIC"
  assert result["returned_count"] == 0


def test_list_geographic_performance_rejects_invalid_view():
  with pytest.raises(ToolError, match="Invalid location_view"):
    reporting.list_geographic_performance(CUSTOMER_ID, location_view="city")


def test_list_geographic_performance_rejects_non_string_view():
  with pytest.raises(
      ToolError, match="location_view must be a non-empty string"
  ):
    reporting.list_geographic_performance(CUSTOMER_ID, location_view=1)


def test_list_geographic_performance_rejects_empty_string_view():
  with pytest.raises(
      ToolError, match="location_view must be a non-empty string"
  ):
    reporting.list_geographic_performance(CUSTOMER_ID, location_view="")


def test_get_competitive_pressure_report_bundles_queries_and_changes():
  pressure_rows = [{"campaign.id": "111", "metrics.cost_micros": 100}]
  auction_rows = [{"segments.auction_insight_domain": "example.com"}]
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      side_effect=[pressure_rows, auction_rows],
  ) as mock_run:
    with mock.patch(
        "ads_mcp.tools.changes.get_change_history_extended",
        return_value={"change_statuses": []},
    ) as mock_changes:
      result = reporting.get_competitive_pressure_report(
          CUSTOMER_ID,
          campaign_ids='["111"]',
          date_range={"start_date": "2026-02-01", "end_date": "2026-02-28"},
          trend_limit=10,
          auction_insight_limit=5,
      )

  pressure_query = mock_run.call_args_list[0].args[0]
  auction_query = mock_run.call_args_list[1].args[0]
  assert "metrics.search_budget_lost_impression_share" in pressure_query
  assert "segments.date" in pressure_query
  assert "campaign.id IN (111)" in pressure_query
  assert "LIMIT 10" in pressure_query
  assert "segments.auction_insight_domain" in auction_query
  assert "LIMIT 5" in auction_query
  mock_changes.assert_called_once()
  assert mock_changes.call_args.kwargs["start_date"] == "2026-02-01"
  assert mock_changes.call_args.kwargs["end_date"] == "2026-02-28"
  assert result["pressure_trend"] == pressure_rows
  assert result["auction_insights"] == auction_rows
  assert result["change_history"] == {"change_statuses": []}


def test_get_competitive_pressure_report_can_skip_optional_sections():
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=[],
  ) as mock_run:
    result = reporting.get_competitive_pressure_report(
        CUSTOMER_ID,
        include_auction_insights=False,
        include_change_history=False,
        granularity="none",
    )

  assert mock_run.call_count == 1
  assert result["auction_insights"] == []
  assert result["change_history"] is None
  assert result["granularity"] == "NONE"


def test_get_competitive_pressure_report_all_time_skips_change_call():
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=[],
  ):
    with mock.patch(
        "ads_mcp.tools.changes.get_change_history_extended",
    ) as mock_changes:
      result = reporting.get_competitive_pressure_report(
          CUSTOMER_ID,
          date_range="ALL_TIME",
          include_auction_insights=False,
      )

  mock_changes.assert_not_called()
  assert "finite date bounds" in result["change_history"]["coverage_note"]


def test_summarize_cart_data_sales_builds_v24_cart_query():
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=[
          {
              "campaign.id": "111",
              "metrics.all_gross_profit_micros": 12_000_000,
          }
      ],
  ) as mock_run:
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={"111": {"campaign.name": "Shopping"}},
    ):
      result = reporting.summarize_cart_data_sales(
          CUSTOMER_ID,
          group_by="campaign",
          campaign_ids=["111"],
          date_range="LAST_7_DAYS",
          top_limit=10,
      )

  query = mock_run.call_args.args[0]
  assert "FROM cart_data_sales_view" in query
  assert "campaign.id" in query
  assert "metrics.all_revenue_micros" in query
  assert "metrics.all_cross_sell_gross_profit_micros" in query
  assert "campaign.id IN (111)" in query
  assert "segments.date DURING LAST_7_DAYS" in query
  assert "LIMIT 10" in query
  assert result["group_by"] == "CAMPAIGN"
  assert result["returned_count"] == 1


def test_summarize_cart_data_sales_uses_filter_context_for_non_campaign_group():
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=[
          {
              "segments.product_sold_brand": "Brand",
              "metrics.all_gross_profit_micros": 12_000_000,
          }
      ],
  ):
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={"111": {"campaign.name": "Shopping"}},
    ) as mock_context:
      result = reporting.summarize_cart_data_sales(
          CUSTOMER_ID,
          group_by="sold_brand",
          campaign_ids='["111", "222"]',
      )

  mock_context.assert_called_once_with(CUSTOMER_ID, ["111", "222"], None)
  assert result["group_by"] == "SOLD_BRAND"
  assert result["campaign_context"] == {"111": {"campaign.name": "Shopping"}}


def test_summarize_cart_data_sales_normalizes_comma_string_campaign_ids():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query", return_value=[]):
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={},
    ) as mock_context:
      reporting.summarize_cart_data_sales(
          CUSTOMER_ID,
          group_by="sold_brand",
          campaign_ids="111,222",
      )

  mock_context.assert_called_once_with(CUSTOMER_ID, ["111", "222"], None)


def test_summarize_cart_data_sales_rejects_invalid_date_range():
  with pytest.raises(ToolError, match="Invalid date_range"):
    reporting.summarize_cart_data_sales(
        CUSTOMER_ID,
        date_range="LAST_30_DAYS OR metrics.clicks > 0",
    )


def test_summarize_cart_data_sales_rejects_invalid_group():
  with pytest.raises(ToolError, match="Invalid group_by"):
    reporting.summarize_cart_data_sales(CUSTOMER_ID, group_by="query")


def test_summarize_cart_data_sales_rejects_non_string_group():
  with pytest.raises(ToolError, match="group_by must be a non-empty string"):
    reporting.summarize_cart_data_sales(CUSTOMER_ID, group_by=1)


def test_summarize_cart_data_sales_rejects_empty_group():
  with pytest.raises(ToolError, match="group_by must be a non-empty string"):
    reporting.summarize_cart_data_sales(CUSTOMER_ID, group_by="")


def test_compare_biddable_vs_all_cart_value_adds_delta_metrics():
  rows = [
      {
          "campaign.id": "111",
          "campaign.name": "PMax",
          "metrics.all_revenue_micros": 20_000_000,
          "metrics.revenue_micros": 15_000_000,
          "metrics.all_gross_profit_micros": 8_000_000,
          "metrics.gross_profit_micros": 5_000_000,
          "metrics.all_units_sold": 6.0,
          "metrics.units_sold": 4.0,
      }
  ]
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={"111": {"campaign.name": "PMax"}},
    ):
      result = reporting.compare_biddable_vs_all_cart_value(
          CUSTOMER_ID,
          campaign_ids=["111"],
          date_range="last_30_days",
      )

  query = mock_run.call_args.args[0]
  assert "FROM cart_data_sales_view" in query
  assert "metrics.gross_profit_micros" in query
  assert "metrics.all_gross_profit_micros" in query
  assert result["date_range"] == "LAST_30_DAYS"
  comparison = result["cart_value_comparisons"][0]
  assert comparison["non_biddable_revenue_micros"] == 5_000_000
  assert comparison["non_biddable_gross_profit_micros"] == 3_000_000
  assert comparison["non_biddable_units_sold"] == 2.0


def test_list_cart_profit_outliers_builds_paginated_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_cart_profit_outliers(
        CUSTOMER_ID,
        group_by="sold_brand",
        sort_by="gross_profit_margin",
        direction="desc",
        page_token="25",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM cart_data_sales_view" in query
  assert "segments.product_sold_brand" in query
  assert "ORDER BY metrics.all_gross_profit_margin" in query
  assert "DESC" in query
  assert mock_run.call_args.kwargs["page_token"] == "25"
  assert result["group_by"] == "SOLD_BRAND"
  assert result["sort_by"] == "GROSS_PROFIT_MARGIN"


def test_list_cart_profit_outliers_rejects_invalid_direction():
  with pytest.raises(ToolError, match="Invalid direction"):
    reporting.list_cart_profit_outliers(CUSTOMER_ID, direction="sideways")


def test_list_shopping_attribution_breakdown_includes_event_type():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_shopping_attribution_breakdown(
        CUSTOMER_ID,
        campaign_ids=["111"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM shopping_performance_view" in query
  assert "segments.conversion_attribution_event_type" in query
  assert "segments.product_item_id" in query
  assert "metrics.impressions" not in query
  assert "metrics.clicks" not in query
  assert "metrics.all_revenue_micros" in query
  assert "metrics.all_gross_profit_micros" in query
  assert "campaign.id IN (111)" in query


def test_list_campaign_view_through_optimization_includes_v24_field():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_campaign_view_through_optimization(
        CUSTOMER_ID,
        advertising_channel_types=["DEMAND_GEN"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM campaign" in query
  assert "campaign.view_through_conversion_optimization_enabled" in query
  assert "campaign.advertising_channel_type IN (DEMAND_GEN)" in query


def test_list_campaign_view_through_optimization_rejects_bad_enum_filter():
  with pytest.raises(ToolError, match="Invalid advertising_channel_types"):
    reporting.list_campaign_view_through_optimization(
        CUSTOMER_ID,
        advertising_channel_types=["DEMAND_GEN) OR campaign.id > 0"],
    )


def test_list_video_audibility_performance_includes_audible_metrics():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_video_audibility_performance(CUSTOMER_ID)

  query = mock_run.call_args.kwargs["query"]
  assert "metrics.active_view_audible_impressions_rate" in query
  assert "metrics.active_view_audible_two_seconds_impressions" in query
  assert "metrics.video_trueview_views" in query
  assert "metrics.video_watch_time_duration_millis" in query
  assert "metrics.video_views" not in query


def test_list_vertical_ads_performance_builds_segment_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_vertical_ads_performance(
        CUSTOMER_ID,
        segment_by="brand",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM campaign" in query
  assert "segments.vertical_ads_listing_brand" in query
  assert "metrics.conversions_value" in query
  assert result["segment_by"] == "BRAND"


def test_list_vertical_ads_performance_rejects_invalid_segment():
  with pytest.raises(ToolError, match="Invalid segment_by"):
    reporting.list_vertical_ads_performance(CUSTOMER_ID, segment_by="device")


def test_list_campaign_search_terms_builds_compact_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_campaign_search_terms(
        CUSTOMER_ID,
        campaign_ids=["111"],
        min_clicks=5,
        min_cost_micros=1_000_000,
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM campaign_search_term_view" in query
  assert "campaign_search_term_view.search_term" in query
  assert "segments.search_term_targeting_status" in query
  assert "metrics.cost_per_conversion" in query
  assert "campaign.id IN (111)" in query
  assert "metrics.clicks >= 5" in query
  assert "metrics.cost_micros >= 1000000" in query


def test_list_ai_max_search_term_ad_combinations_builds_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_ai_max_search_term_ad_combinations(
        CUSTOMER_ID,
        campaign_ids=["111"],
        ad_group_ids=["222"],
        min_impressions=10,
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM ai_max_search_term_ad_combination_view" in query
  assert "ai_max_search_term_ad_combination_view.search_term" in query
  assert "ai_max_search_term_ad_combination_view.headline" in query
  assert "ai_max_search_term_ad_combination_view.landing_page" in query
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query
  assert "metrics.impressions >= 10" in query


def test_list_final_url_expansion_assets_builds_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_final_url_expansion_assets(
        CUSTOMER_ID,
        campaign_ids=["111"],
        asset_group_ids=["222"],
        statuses=["ENABLED"],
        field_types=["FINAL_URL"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM final_url_expansion_asset_view" in query
  assert "final_url_expansion_asset_view.final_url" in query
  assert "final_url_expansion_asset_view.status IN (ENABLED)" in query
  assert "final_url_expansion_asset_view.field_type IN (FINAL_URL)" in query
  assert "campaign.id = 111" in query
  assert "campaign.advertising_channel_type = PERFORMANCE_MAX" in query
  assert "asset_group.id IN (222)" in query


@pytest.mark.parametrize("campaign_ids", [None, [], ["111", "222"]])
def test_list_final_url_expansion_assets_requires_single_campaign(
    campaign_ids,
):
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    with pytest.raises(ToolError, match="requires exactly one campaign_id"):
      reporting.list_final_url_expansion_assets(
          CUSTOMER_ID,
          campaign_ids=campaign_ids,
      )

  mock_run.assert_not_called()


def test_list_targeting_expansion_performance_builds_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_targeting_expansion_performance(
        CUSTOMER_ID,
        campaign_ids=["111"],
        ad_group_ids=["222"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM targeting_expansion_view" in query
  assert "targeting_expansion_view.resource_name" in query
  assert "metrics.value_per_conversion" in query
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query


def test_list_content_suitability_placements_builds_group_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_content_suitability_placements(
        CUSTOMER_ID,
        placement_view="group",
        placement_types=["YOUTUBE_VIDEO"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM group_content_suitability_placement_view" in query
  assert "group_content_suitability_placement_view.placement" in query
  assert (
      "group_content_suitability_placement_view.placement_type IN "
      "(YOUTUBE_VIDEO)"
  ) in query
  assert result["placement_view"] == "GROUP"


def test_list_content_suitability_placements_builds_detail_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_content_suitability_placements(
        CUSTOMER_ID,
        placement_view="detail",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM detail_content_suitability_placement_view" in query
  assert "detail_content_suitability_placement_view.target_url" in query
  assert result["placement_view"] == "DETAIL"


def test_list_location_interest_performance_builds_location_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_location_interest_performance(
        CUSTOMER_ID,
        interest_view="location_interest",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM location_interest_view" in query
  assert "ad_group_criterion.location.geo_target_constant" in query
  assert result["interest_view"] == "LOCATION_INTEREST"


def test_list_location_interest_performance_builds_matched_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_location_interest_performance(
        CUSTOMER_ID,
        interest_view="matched_location_interest",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM matched_location_interest_view" in query
  assert "segments.geo_target_most_specific_location" in query
  assert result["interest_view"] == "MATCHED_LOCATION_INTEREST"


def test_list_campaign_search_terms_rejects_negative_threshold():
  with pytest.raises(ToolError, match="min_clicks must be non-negative"):
    reporting.list_campaign_search_terms(CUSTOMER_ID, min_clicks=-1)


def test_list_campaign_search_terms_rejects_non_numeric_threshold():
  with pytest.raises(ToolError, match="min_clicks must be a number"):
    reporting.list_campaign_search_terms(CUSTOMER_ID, min_clicks="1")


def test_summarize_shopping_product_status_builds_compact_summary():
  rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Shopping",
          "shopping_product.item_id": "sku-1",
          "shopping_product.status": "LIMITED",
          "shopping_product.issues": [
              {"type": "PRICE_MISMATCH", "severity": "WARNING"}
          ],
          "metrics.cost_micros": 5_000_000,
      },
      {
          "campaign.id": "111",
          "campaign.name": "Shopping",
          "shopping_product.item_id": "sku-2",
          "shopping_product.status": "ELIGIBLE",
          "shopping_product.issues": [],
          "metrics.cost_micros": 2_000_000,
      },
  ]
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={"111": {"campaign.name": "Shopping"}},
    ):
      result = reporting.summarize_shopping_product_status(
          CUSTOMER_ID,
          campaign_ids=["111"],
          statuses=["LIMITED", "ELIGIBLE"],
          date_range="last_30_days",
          row_limit=500,
          top_issue_products_limit=5,
      )

  query = mock_run.call_args.args[0]
  assert "FROM shopping_product" in query
  assert "shopping_product.campaign = 'customers/123/campaigns/111'" in query
  assert "shopping_product.status IN (LIMITED, ELIGIBLE)" in query
  assert "shopping_product.issues" in query
  assert "LIMIT 500" in query
  assert result["date_range"] == "LAST_30_DAYS"
  assert result["analyzed_row_count"] == 2
  assert result["status_distribution"] == [
      {"status": "ELIGIBLE", "product_count": 1},
      {"status": "LIMITED", "product_count": 1},
  ]
  assert result["issue_type_distribution"] == [
      {"issue_type": "PRICE_MISMATCH", "issue_count": 1}
  ]
  assert result["top_issue_products"] == [rows[0]]
  assert result["campaign_ids"] == ["111"]


@pytest.mark.parametrize("campaign_ids", [None, [], ["111", "222"]])
def test_summarize_shopping_product_status_requires_single_campaign(
    campaign_ids,
):
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    with pytest.raises(ToolError, match="requires exactly one campaign_id"):
      reporting.summarize_shopping_product_status(
          CUSTOMER_ID,
          campaign_ids=campaign_ids,
      )

  mock_run.assert_not_called()


def test_list_shopping_product_status_builds_paginated_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_shopping_product_status(
        CUSTOMER_ID,
        campaign_ids=["111"],
        ad_group_ids=["222"],
        statuses=["NOT_ELIGIBLE"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM shopping_product" in query
  assert "shopping_product.campaign = 'customers/123/campaigns/111'" in query
  assert "shopping_product.ad_group = 'customers/123/adGroups/222'" in query
  assert "shopping_product.status IN (NOT_ELIGIBLE)" in query
  assert "shopping_product.issues" in query


@pytest.mark.parametrize("campaign_ids", [None, [], ["111", "222"]])
def test_list_shopping_product_status_requires_single_campaign(campaign_ids):
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    with pytest.raises(ToolError, match="requires exactly one campaign_id"):
      reporting.list_shopping_product_status(
          CUSTOMER_ID,
          campaign_ids=campaign_ids,
      )

  mock_run.assert_not_called()


@pytest.mark.parametrize("ad_group_ids", [["222", "333"]])
def test_list_shopping_product_status_requires_single_ad_group(ad_group_ids):
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    with pytest.raises(ToolError, match="requires exactly one ad_group_id"):
      reporting.list_shopping_product_status(
          CUSTOMER_ID,
          campaign_ids=["111"],
          ad_group_ids=ad_group_ids,
      )

  mock_run.assert_not_called()


def test_list_shopping_product_status_rejects_bad_status_filter():
  with pytest.raises(ToolError, match="Invalid statuses"):
    reporting.list_shopping_product_status(
        CUSTOMER_ID,
        campaign_ids=["111"],
        statuses=["NOT_ELIGIBLE) OR metrics.clicks > 0"],
    )


def test_list_travel_feed_asset_sets_builds_config_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_travel_feed_asset_sets(
        CUSTOMER_ID,
        statuses=["ENABLED"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM asset_set" in query
  assert "asset_set.type = TRAVEL_FEED" in query
  assert "asset_set.status IN (ENABLED)" in query
  assert "asset_set.travel_feed_data.merchant_center_id" in query
  assert "asset_set.travel_feed_data.partner_center_id" in query


def test_list_travel_feed_asset_sets_rejects_bad_status_filter():
  with pytest.raises(ToolError, match="Invalid statuses"):
    reporting.list_travel_feed_asset_sets(
        CUSTOMER_ID,
        statuses=["ENABLED) OR asset_set.id > 0"],
    )


def test_list_retail_filter_shared_criteria_builds_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_retail_filter_shared_criteria(
        CUSTOMER_ID,
        shared_set_ids=["333"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM shared_criterion" in query
  assert "shared_set.type = RETAIL_FILTER" in query
  assert "shared_set.id IN (333)" in query
  assert "shared_criterion.retail_filter.tag.value" in query


def test_list_impression_share_includes_share_metrics():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_impression_share(CUSTOMER_ID)

  query = mock_run.call_args.kwargs["query"]
  assert "campaign.status = ENABLED" in query
  assert "metrics.search_impression_share" in query
  assert "metrics.search_top_impression_share" in query
  assert "metrics.search_absolute_top_impression_share" in query
  assert "metrics.search_budget_lost_impression_share" in query
  assert "metrics.search_rank_lost_impression_share" in query
  assert result["returned_count"] == 0


def test_list_impression_share_can_include_non_enabled_campaigns():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_impression_share(CUSTOMER_ID, enabled_only=False)

  query = mock_run.call_args.kwargs["query"]
  assert "campaign.status = ENABLED" not in query


def test_get_campaign_performance_builds_segmented_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [{"campaign.id": "111"}],
        "next_page_token": "25",
        "total_results_count": 26,
    }
    result = reporting.get_campaign_performance(
        CUSTOMER_ID,
        campaign_ids='["111"]',
        date_range="LAST_90_DAYS",
        segment_by=["date", "device"],
        limit=25,
        page_token="0",
        login_customer_id="999",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM campaign" in query
  assert "segments.date BETWEEN" in query
  assert "segments.date DURING LAST_90_DAYS" not in query
  assert "campaign.id IN (111)" in query
  assert "segments.date" in query
  assert "segments.device" in query
  assert "metrics.cost_per_conversion" in query
  assert (
      "ORDER BY segments.date, segments.device, metrics.cost_micros DESC"
      in (query)
  )
  assert "LIMIT 25" not in query
  assert mock_run.call_args.kwargs["page_size"] == 25
  assert mock_run.call_args.kwargs["page_token"] == "0"
  assert mock_run.call_args.kwargs["login_customer_id"] == "999"
  assert result["campaign_performance"] == [{"campaign.id": "111"}]
  assert result["segment_by"] == ["DATE", "DEVICE"]
  assert result["truncated"] is True


def test_get_campaign_performance_rejects_bad_segment():
  with pytest.raises(ToolError, match="Invalid segment_by"):
    reporting.get_campaign_performance(CUSTOMER_ID, segment_by="BAD")


def test_list_keyword_quality_scores_builds_filtered_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    reporting.list_keyword_quality_scores(
        CUSTOMER_ID,
        campaign_ids=["111"],
        ad_group_ids=["222"],
        min_quality_score=4,
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM keyword_view" in query
  assert "ad_group_criterion.type = KEYWORD" in query
  assert "ad_group_criterion.negative = FALSE" in query
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query
  assert "ad_group_criterion.quality_info.quality_score >= 4" in query
  assert "ad_group_criterion.quality_info.creative_quality_score" in query
  assert "ad_group_criterion.criterion_id ASC" in query
  assert "LIMIT" not in query
  assert mock_run.call_args.kwargs["page_size"] == 1000


def test_list_keyword_quality_scores_rejects_invalid_score():
  with pytest.raises(ToolError, match="between 1 and 10"):
    reporting.list_keyword_quality_scores(CUSTOMER_ID, min_quality_score=11)


def test_list_keyword_quality_scores_can_omit_limit_clause():
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=[],
  ) as mock_run:
    reporting.list_keyword_quality_scores(
        CUSTOMER_ID,
        limit=None,
    )

  query = mock_run.call_args.args[0]
  assert "FROM keyword_view" in query
  assert "LIMIT" not in query


def test_list_keyword_quality_scores_returns_pagination_metadata():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [
            {
                "campaign.id": "77",
                "campaign.name": "Brand",
                "ad_group_criterion.criterion_id": "1",
            }
        ],
        "next_page_token": "next-page",
        "total_results_count": 18050,
    }
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={
            "77": {
                "campaign.name": "Brand",
                "campaign.status": "ENABLED",
                "recent_30_day_cost_micros": 3_000_000,
            }
        },
    ):
      result = reporting.list_keyword_quality_scores(
          CUSTOMER_ID,
          limit=1000,
          page_token="current-page",
      )

  assert mock_run.call_args.kwargs["page_size"] == 1000
  assert mock_run.call_args.kwargs["page_token"] == "current-page"
  assert result == {
      "keyword_quality_scores": [
          {
              "campaign.id": "77",
              "campaign.name": "Brand",
              "ad_group_criterion.criterion_id": "1",
          }
      ],
      "returned_count": 1,
      "total_count": 18050,
      "returned_row_count": 1,
      "total_row_count": 18050,
      "total_page_count": 19,
      "truncated": True,
      "next_page_token": "next-page",
      "page_size": 1000,
      "campaign_context": {
          "77": {
              "campaign.name": "Brand",
              "campaign.status": "ENABLED",
              "recent_30_day_cost_micros": 3_000_000,
          }
      },
  }


def test_get_campaign_conversion_goals_merges_standard_and_custom_goals():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    mock_run.side_effect = [
        [
            {
                "campaign.id": "111",
                "campaign.name": "Search",
                "campaign.status": "ENABLED",
                "conversion_goal_campaign_config.goal_config_level": (
                    "CAMPAIGN"
                ),
                "conversion_goal_campaign_config.custom_conversion_goal": (
                    "customers/123/customConversionGoals/77"
                ),
            }
        ],
        [
            {
                "campaign_conversion_goal.category": "PURCHASE",
                "campaign_conversion_goal.origin": "WEBSITE",
                "campaign_conversion_goal.biddable": True,
            }
        ],
        [
            {
                "custom_conversion_goal.id": "77",
                "custom_conversion_goal.name": "Primary Goal",
                "custom_conversion_goal.status": "ENABLED",
                "custom_conversion_goal.conversion_actions": [
                    "customers/123/conversionActions/1"
                ],
            }
        ],
    ]
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={
            "111": {
                "campaign.name": "Search",
                "campaign.status": "ENABLED",
                "recent_30_day_cost_micros": 4_000_000,
            }
        },
    ):
      result = reporting.get_campaign_conversion_goals(CUSTOMER_ID, "111")

  assert (
      "FROM conversion_goal_campaign_config"
      in mock_run.call_args_list[0].args[0]
  )
  assert "FROM campaign_conversion_goal" in mock_run.call_args_list[1].args[0]
  assert "FROM custom_conversion_goal" in mock_run.call_args_list[2].args[0]
  assert result == {
      "campaign": {
          "id": "111",
          "name": "Search",
          "status": "ENABLED",
          "recent_30_day_cost_micros": 4_000_000,
      },
      "goal_config_level": "CAMPAIGN",
      "uses_custom_conversion_goal": True,
      "custom_conversion_goal": {
          "id": "77",
          "name": "Primary Goal",
          "status": "ENABLED",
          "conversion_actions": ["customers/123/conversionActions/1"],
          "resource_name": "customers/123/customConversionGoals/77",
      },
      "standard_conversion_goals": [
          {
              "category": "PURCHASE",
              "origin": "WEBSITE",
              "biddable": True,
          }
      ],
  }


def test_summarize_keyword_quality_scores_returns_compact_distributions():
  rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "ad_group_criterion.keyword.match_type": "EXACT",
          "ad_group_criterion.status": "ENABLED",
          "ad_group_criterion.quality_info.quality_score": 10,
      },
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "ad_group_criterion.keyword.match_type": "PHRASE",
          "ad_group_criterion.status": "ENABLED",
          "ad_group_criterion.quality_info.quality_score": 8,
      },
      {
          "campaign.id": "222",
          "campaign.name": "Generic",
          "ad_group_criterion.keyword.match_type": "BROAD",
          "ad_group_criterion.status": "PAUSED",
          "ad_group_criterion.quality_info.quality_score": None,
      },
  ]

  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=rows,
  ):
    with mock.patch(
        "ads_mcp.tools.reporting.get_campaign_context",
        return_value={
            "111": {
                "campaign.name": "Brand",
                "campaign.status": "ENABLED",
                "recent_30_day_cost_micros": 5_000_000,
            },
            "222": {
                "campaign.name": "Generic",
                "campaign.status": "PAUSED",
                "recent_30_day_cost_micros": 0,
            },
        },
    ):
      result = reporting.summarize_keyword_quality_scores(CUSTOMER_ID)

  assert result["total_keyword_count"] == 3
  assert result["scored_keyword_count"] == 2
  assert result["unscored_keyword_count"] == 1
  assert result["average_quality_score"] == 9.0
  assert result["quality_score_distribution"] == [
      {"quality_score": 8, "keyword_count": 1},
      {"quality_score": 10, "keyword_count": 1},
      {"quality_score": None, "keyword_count": 1},
  ]
  assert result["match_type_distribution"] == [
      {"match_type": "BROAD", "keyword_count": 1},
      {"match_type": "EXACT", "keyword_count": 1},
      {"match_type": "PHRASE", "keyword_count": 1},
  ]
  assert result["keyword_status_distribution"] == [
      {"status": "ENABLED", "keyword_count": 2},
      {"status": "PAUSED", "keyword_count": 1},
  ]
  assert result["campaign_distribution"] == [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "keyword_count": 2,
      },
      {
          "campaign.id": "222",
          "campaign.name": "Generic",
          "keyword_count": 1,
      },
  ]


def test_list_rsa_ad_strength_filters_to_responsive_search_ads():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_rsa_ad_strength(
        CUSTOMER_ID,
        ad_group_ids=["333"],
        date_range="LAST_14_DAYS",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM ad_group_ad" in query
  assert "ad_group_ad.ad.type = RESPONSIVE_SEARCH_AD" in query
  assert "ad_group.id IN (333)" in query
  assert "segments.date DURING LAST_14_DAYS" in query
  assert "ad_group_ad.ad_strength" in query
  assert result["returned_count"] == 0


def test_list_conversion_actions_builds_filtered_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_conversion_actions(
        CUSTOMER_ID,
        statuses=["enabled"],
        types=["purchase", "sign_up"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM conversion_action" in query
  assert "conversion_action.status IN (ENABLED)" in query
  assert "conversion_action.type IN (PURCHASE, SIGN_UP)" in query
  assert (
      "conversion_action.attribution_model_settings.attribution_model" in query
  )
  assert result["returned_count"] == 0


def test_list_audience_performance_campaign_scope_uses_campaign_view():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_audience_performance(
        CUSTOMER_ID,
        scope="campaign",
        campaign_ids=["111"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert result["scope"] == "CAMPAIGN"
  assert "FROM campaign_audience_view" in query
  assert "campaign_criterion.user_list.user_list" in query
  assert "campaign_criterion.custom_audience.custom_audience" in query
  assert "campaign.id IN (111)" in query
  assert result["returned_count"] == 0


def test_list_audience_performance_ad_group_scope_uses_ad_group_view():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_audience_performance(
        CUSTOMER_ID,
        scope="ad_group",
        campaign_ids=["111"],
        ad_group_ids=["222"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert result["scope"] == "AD_GROUP"
  assert "FROM ad_group_audience_view" in query
  assert "ad_group_criterion.audience.audience" in query
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query
  assert result["returned_count"] == 0


def test_list_audience_performance_rejects_ad_group_ids_for_campaign_scope():
  with pytest.raises(ToolError, match="scope is AD_GROUP"):
    reporting.list_audience_performance(
        CUSTOMER_ID,
        scope="campaign",
        ad_group_ids=["222"],
    )


def test_get_demographic_performance_fans_out_selected_views():
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      side_effect=[
          [{"ad_group_criterion.age_range.type": "AGE_RANGE_25_34"}],
          [{"ad_group_criterion.gender.type": "MALE"}],
      ],
  ) as mock_run:
    result = reporting.get_demographic_performance(
        CUSTOMER_ID,
        campaign_ids=["111"],
        ad_group_ids='["222"]',
        demographic_types=["age", "gender"],
        limit_per_type=12,
    )

  age_query = mock_run.call_args_list[0].args[0]
  gender_query = mock_run.call_args_list[1].args[0]
  assert "FROM age_range_view" in age_query
  assert "ad_group_criterion.age_range.type" in age_query
  assert "campaign.id IN (111)" in age_query
  assert "ad_group.id IN (222)" in age_query
  assert "LIMIT 12" in age_query
  assert "FROM gender_view" in gender_query
  assert result["demographic_types"] == ["AGE", "GENDER"]
  assert result["returned_counts"] == {"AGE": 1, "GENDER": 1}


def test_get_landing_page_performance_uses_expanded_view_and_device():
  rows = [{"expanded_landing_page_view.expanded_final_url": "https://x.test"}]
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    result = reporting.get_landing_page_performance(
        CUSTOMER_ID,
        campaign_ids='["111", "222"]',
        landing_page_view="expanded",
        segment_by_device=True,
        limit=15,
    )

  query = mock_run.call_args.args[0]
  assert "FROM expanded_landing_page_view" in query
  assert "expanded_landing_page_view.expanded_final_url" in query
  assert "segments.device" in query
  assert "campaign.id IN (111, 222)" in query
  assert "LIMIT 15" in query
  assert result["landing_page_performance"] == rows
  assert result["landing_page_view"] == "EXPANDED"
  assert result["segment_by_device"] is True


def test_get_ad_inventory_can_return_structure_without_metrics_or_text():
  rows = [{"ad_group_ad.ad.id": "7"}]
  with mock.patch(
      "ads_mcp.tools.reporting.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    result = reporting.get_ad_inventory(
        CUSTOMER_ID,
        campaign_ids=["111"],
        ad_group_ids=["222"],
        ad_statuses=["enabled"],
        ad_types=["responsive_search_ad"],
        include_metrics=False,
        include_text_assets=False,
        limit=20,
    )

  query = mock_run.call_args.args[0]
  assert "FROM ad_group_ad" in query
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query
  assert "ad_group_ad.status IN (ENABLED)" in query
  assert "ad_group_ad.ad.type IN (RESPONSIVE_SEARCH_AD)" in query
  assert "metrics.cost_micros" not in query
  assert "responsive_search_ad.headlines" not in query
  assert "LIMIT 20" in query
  assert result["ad_inventory"] == rows
  assert result["include_metrics"] is False


def test_get_ad_inventory_validates_date_range_without_metrics():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    with pytest.raises(ToolError, match="Invalid date_range"):
      reporting.get_ad_inventory(
          CUSTOMER_ID,
          date_range="BAD_RANGE",
          include_metrics=False,
      )

  mock_run.assert_not_called()


def test_list_video_enhancements_builds_filtered_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_video_enhancements(
        CUSTOMER_ID,
        sources=["advertiser", "enhanced_by_google_ads"],
        campaign_ids=["111"],
        ad_group_ids=["222"],
        date_range="LAST_14_DAYS",
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM video_enhancement" in query
  assert (
      "video_enhancement.source IN "
      "(ADVERTISER, ENHANCED_BY_GOOGLE_ADS)" in query
  )
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query
  assert "segments.date DURING LAST_14_DAYS" in query
  assert "metrics.video_trueview_views" in query
  assert "metrics.video_watch_time_duration_millis" in query
  assert "video_enhancement.title ASC" in query
  assert "video_enhancement.source ASC" in query
  assert "video_enhancement.duration_millis ASC" in query
  assert result["returned_count"] == 0
  assert result["sources"] == [
      "ADVERTISER",
      "ENHANCED_BY_GOOGLE_ADS",
  ]


def test_list_video_enhancements_deduplicates_sources_preserving_order():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }
    result = reporting.list_video_enhancements(
        CUSTOMER_ID,
        sources=["advertiser", "ADVERTISER", "unknown"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "video_enhancement.source IN (ADVERTISER, UNKNOWN)" in query
  assert result["sources"] == ["ADVERTISER", "UNKNOWN"]


def test_list_video_enhancements_rejects_invalid_source():
  with pytest.raises(ToolError, match="Invalid sources"):
    reporting.list_video_enhancements(CUSTOMER_ID, sources=["google_magic"])


def test_list_video_enhancements_returns_pagination_metadata():
  rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Demand Gen",
          "video_enhancement.resource_name": (
              "customers/123/videoEnhancements/999"
          ),
      }
  ]
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": rows,
        "next_page_token": "next-page",
        "total_results_count": 5,
    }
    result = reporting.list_video_enhancements(
        CUSTOMER_ID,
        limit=1,
        page_token="current-page",
    )

  assert mock_run.call_args.kwargs["page_size"] == 1
  assert mock_run.call_args.kwargs["page_token"] == "current-page"
  assert result == {
      "video_enhancements": rows,
      "returned_count": 1,
      "total_count": 5,
      "total_page_count": 5,
      "truncated": True,
      "next_page_token": "next-page",
      "page_size": 1,
  }
