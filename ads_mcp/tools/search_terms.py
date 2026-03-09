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

"""Curated search-term insight and search-term analysis tools."""

from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import quote_string_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import run_gaql_query


def _date_range_condition(date_range: str) -> str:
  return f"segments.date DURING {date_range}"


def _campaign_resource_names(
    customer_id: str, campaign_ids: list[str]
) -> list[str]:
  return [
      f"customers/{customer_id}/campaigns/{campaign_id}"
      for campaign_id in campaign_ids
  ]


def _non_negative(value: int | float, field_name: str) -> None:
  if value < 0:
    raise ToolError(f"{field_name} must be non-negative.")


@mcp.tool()
def list_campaign_search_term_insights(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    min_clicks: int = 0,
    min_impressions: int = 0,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign_search_term_insight rows with key metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      min_clicks: Optional minimum clicks filter.
      min_impressions: Optional minimum impressions filter.
      limit: Maximum number of rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing campaign search term insight rows.
  """
  validate_limit(limit)
  _non_negative(min_clicks, "min_clicks")
  _non_negative(min_impressions, "min_impressions")

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if min_clicks:
    where_conditions.append(f"metrics.clicks >= {min_clicks}")
  if min_impressions:
    where_conditions.append(f"metrics.impressions >= {min_impressions}")

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign_search_term_insight.id,
        campaign_search_term_insight.category_label,
        segments.search_term,
        segments.search_subcategory,
        metrics.impressions,
        metrics.clicks,
        metrics.ctr,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM campaign_search_term_insight
      {build_where_clause(where_conditions)}
      ORDER BY metrics.clicks DESC
      LIMIT {limit}
  """

  return {
      "campaign_search_term_insights": run_gaql_query(
          query, customer_id, login_customer_id
      )
  }


@mcp.tool()
def list_customer_search_term_insights(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    min_clicks: int = 0,
    min_impressions: int = 0,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists customer_search_term_insight rows with key metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      min_clicks: Optional minimum clicks filter.
      min_impressions: Optional minimum impressions filter.
      limit: Maximum number of rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing customer search term insight rows.
  """
  validate_limit(limit)
  _non_negative(min_clicks, "min_clicks")
  _non_negative(min_impressions, "min_impressions")

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        "segments.campaign IN "
        f"({quote_string_values(_campaign_resource_names(customer_id, campaign_ids))})"
    )
  if min_clicks:
    where_conditions.append(f"metrics.clicks >= {min_clicks}")
  if min_impressions:
    where_conditions.append(f"metrics.impressions >= {min_impressions}")

  query = f"""
      SELECT
        customer_search_term_insight.id,
        customer_search_term_insight.category_label,
        segments.campaign,
        segments.search_term,
        segments.search_subcategory,
        metrics.impressions,
        metrics.clicks,
        metrics.ctr,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM customer_search_term_insight
      {build_where_clause(where_conditions)}
      ORDER BY metrics.clicks DESC
      LIMIT {limit}
  """

  return {
      "customer_search_term_insights": run_gaql_query(
          query, customer_id, login_customer_id
      )
  }


@mcp.tool()
def analyze_search_terms(
    customer_id: str,
    campaign_id: str | None = None,
    ad_group_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    min_negative_clicks: int = 10,
    min_negative_cost_micros: int = 5_000_000,
    min_exact_match_conversions: float = 1.0,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Analyzes search_term_view and flags heuristic candidates.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Optional campaign ID filter.
      ad_group_id: Optional ad group ID filter.
      date_range: GAQL date range such as LAST_30_DAYS.
      min_negative_clicks: Minimum clicks before flagging a negative
          candidate when conversions are zero.
      min_negative_cost_micros: Minimum spend before flagging a negative
          candidate when conversions are zero.
      min_exact_match_conversions: Minimum conversions before flagging an
          exact-match candidate when the term is not already exact.
      limit: Maximum number of rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with heuristic candidate subsets and the number of rows analyzed.
  """
  validate_limit(limit)
  _non_negative(min_negative_clicks, "min_negative_clicks")
  _non_negative(min_negative_cost_micros, "min_negative_cost_micros")
  _non_negative(min_exact_match_conversions, "min_exact_match_conversions")

  where_conditions = [_date_range_condition(date_range)]
  if campaign_id:
    where_conditions.append(f"campaign.id = {int(campaign_id)}")
  if ad_group_id:
    where_conditions.append(f"ad_group.id = {int(ad_group_id)}")

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        search_term_view.search_term,
        search_term_view.status,
        segments.search_term_match_type,
        metrics.impressions,
        metrics.clicks,
        metrics.ctr,
        metrics.cost_micros,
        metrics.conversions,
        metrics.cost_per_conversion,
        metrics.conversions_value
      FROM search_term_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.clicks DESC
      LIMIT {limit}
  """

  search_terms = run_gaql_query(query, customer_id, login_customer_id)
  negative_keyword_candidates = []
  exact_match_candidates = []

  for row in search_terms:
    status = row.get("search_term_view.status")
    clicks = row.get("metrics.clicks") or 0
    cost_micros = row.get("metrics.cost_micros") or 0
    conversions = row.get("metrics.conversions") or 0

    if (
        status != "EXCLUDED"
        and clicks >= min_negative_clicks
        and conversions == 0
        and cost_micros >= min_negative_cost_micros
    ):
      negative_keyword_candidates.append(row)

    if (
        status not in ("EXCLUDED", "ADDED_EXACT")
        and conversions >= min_exact_match_conversions
    ):
      exact_match_candidates.append(row)

  return {
      "analyzed_row_count": len(search_terms),
      "negative_keyword_candidates": negative_keyword_candidates,
      "exact_match_candidates": exact_match_candidates,
      "heuristics": {
          "date_range": date_range,
          "min_negative_clicks": min_negative_clicks,
          "min_negative_cost_micros": min_negative_cost_micros,
          "min_exact_match_conversions": min_exact_match_conversions,
      },
  }
