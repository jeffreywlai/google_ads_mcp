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
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_device_performance(
        CUSTOMER_ID,
        campaign_ids=["111", "222"],
        date_range="LAST_7_DAYS",
    )

  query = mock_run.call_args.args[0]
  assert "FROM campaign" in query
  assert "segments.device" in query
  assert "campaign.id IN (111, 222)" in query
  assert "segments.date DURING LAST_7_DAYS" in query


def test_list_geographic_performance_uses_geographic_view():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_geographic_performance(
        CUSTOMER_ID,
        location_view="geographic",
    )

  query = mock_run.call_args.args[0]
  assert "FROM geographic_view" in query
  assert "geographic_view.country_criterion_id" in query
  assert "geographic_view.location_type" in query


def test_list_geographic_performance_rejects_invalid_view():
  with pytest.raises(ToolError, match="Invalid location_view"):
    reporting.list_geographic_performance(CUSTOMER_ID, location_view="city")


def test_list_impression_share_includes_share_metrics():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_impression_share(CUSTOMER_ID)

  query = mock_run.call_args.args[0]
  assert "campaign.status = ENABLED" in query
  assert "metrics.search_impression_share" in query
  assert "metrics.search_top_impression_share" in query
  assert "metrics.search_absolute_top_impression_share" in query
  assert "metrics.search_budget_lost_impression_share" in query
  assert "metrics.search_rank_lost_impression_share" in query


def test_list_impression_share_can_include_non_enabled_campaigns():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_impression_share(CUSTOMER_ID, enabled_only=False)

  query = mock_run.call_args.args[0]
  assert "campaign.status = ENABLED" not in query


def test_list_keyword_quality_scores_builds_filtered_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_keyword_quality_scores(
        CUSTOMER_ID,
        campaign_ids=["111"],
        ad_group_ids=["222"],
        min_quality_score=4,
    )

  query = mock_run.call_args.args[0]
  assert "FROM keyword_view" in query
  assert "ad_group_criterion.type = KEYWORD" in query
  assert "ad_group_criterion.negative = FALSE" in query
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query
  assert "ad_group_criterion.quality_info.quality_score >= 4" in query
  assert "ad_group_criterion.quality_info.creative_quality_score" in query


def test_list_keyword_quality_scores_rejects_invalid_score():
  with pytest.raises(ToolError, match="between 1 and 10"):
    reporting.list_keyword_quality_scores(CUSTOMER_ID, min_quality_score=11)


def test_list_keyword_quality_scores_can_omit_limit_clause():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_keyword_quality_scores(
        CUSTOMER_ID,
        limit=None,
    )

  query = mock_run.call_args.args[0]
  assert "FROM keyword_view" in query
  assert "LIMIT" not in query


def test_list_rsa_ad_strength_filters_to_responsive_search_ads():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_rsa_ad_strength(
        CUSTOMER_ID,
        ad_group_ids=["333"],
        date_range="LAST_14_DAYS",
    )

  query = mock_run.call_args.args[0]
  assert "FROM ad_group_ad" in query
  assert "ad_group_ad.ad.type = RESPONSIVE_SEARCH_AD" in query
  assert "ad_group.id IN (333)" in query
  assert "segments.date DURING LAST_14_DAYS" in query
  assert "ad_group_ad.ad_strength" in query


def test_list_conversion_actions_builds_filtered_query():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    reporting.list_conversion_actions(
        CUSTOMER_ID,
        statuses=["enabled"],
        types=["purchase", "sign_up"],
    )

  query = mock_run.call_args.args[0]
  assert "FROM conversion_action" in query
  assert "conversion_action.status IN (ENABLED)" in query
  assert "conversion_action.type IN (PURCHASE, SIGN_UP)" in query
  assert (
      "conversion_action.attribution_model_settings.attribution_model" in query
  )


def test_list_audience_performance_campaign_scope_uses_campaign_view():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    result = reporting.list_audience_performance(
        CUSTOMER_ID,
        scope="campaign",
        campaign_ids=["111"],
    )

  query = mock_run.call_args.args[0]
  assert result["scope"] == "CAMPAIGN"
  assert "FROM campaign_audience_view" in query
  assert "campaign_criterion.user_list.user_list" in query
  assert "campaign_criterion.custom_audience.custom_audience" in query
  assert "campaign.id IN (111)" in query


def test_list_audience_performance_ad_group_scope_uses_ad_group_view():
  with mock.patch("ads_mcp.tools.reporting.run_gaql_query") as mock_run:
    result = reporting.list_audience_performance(
        CUSTOMER_ID,
        scope="ad_group",
        campaign_ids=["111"],
        ad_group_ids=["222"],
    )

  query = mock_run.call_args.args[0]
  assert result["scope"] == "AD_GROUP"
  assert "FROM ad_group_audience_view" in query
  assert "ad_group_criterion.audience.audience" in query
  assert "campaign.id IN (111)" in query
  assert "ad_group.id IN (222)" in query


def test_list_audience_performance_rejects_ad_group_ids_for_campaign_scope():
  with pytest.raises(ToolError, match="scope is AD_GROUP"):
    reporting.list_audience_performance(
        CUSTOMER_ID,
        scope="campaign",
        ad_group_ids=["222"],
    )
