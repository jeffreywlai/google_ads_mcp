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
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._campaign_context import get_campaign_context
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import validate_date_range
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import run_gaql_query
from ads_mcp.tools.api import run_gaql_query_page


def _date_range_condition(date_range: str) -> str:
  return f"segments.date DURING {validate_date_range(date_range)}"


def _non_negative(value: int | float, field_name: str) -> None:
  if value < 0:
    raise ToolError(f"{field_name} must be non-negative.")


search_term_tool = ads_read_tool(mcp, tags={"search_terms"})


def _merge_campaign_ids(
    campaign_id: str | None,
    campaign_ids: list[str] | None,
) -> list[str] | None:
  if campaign_id:
    if campaign_ids:
      return [campaign_id, *campaign_ids]
    return [campaign_id]
  return campaign_ids


def _campaign_context_from_rows(
    customer_id: str,
    rows: list[dict[str, Any]],
    login_customer_id: str | None = None,
) -> dict[str, dict[str, Any]]:
  """Builds a compact campaign context map from result rows."""
  campaign_ids = sorted(
      {
          row["campaign.id"]
          for row in rows
          if row.get("campaign.id") not in (None, "")
      },
      key=int,
  )
  return get_campaign_context(customer_id, campaign_ids, login_customer_id)


def _campaign_insight_select_fields(
    include_search_terms: bool,
) -> list[str]:
  """Returns campaign insight select fields for category or term detail."""
  fields = [
      "campaign.id",
      "campaign.name",
      "campaign_search_term_insight.id",
      "campaign_search_term_insight.category_label",
  ]
  if include_search_terms:
    fields.extend(
        [
            "segments.search_term",
            "segments.search_subcategory",
        ]
    )
  fields.extend(
      [
          "metrics.impressions",
          "metrics.clicks",
          "metrics.ctr",
          "metrics.conversions",
          "metrics.conversions_value",
      ]
  )
  return fields


def _customer_insight_select_fields(
    include_search_terms: bool,
) -> list[str]:
  """Returns customer insight select fields for category or term detail."""
  fields = [
      "customer_search_term_insight.id",
      "customer_search_term_insight.category_label",
  ]
  if include_search_terms:
    fields.extend(
        [
            "segments.search_term",
            "segments.search_subcategory",
        ]
    )
  fields.extend(
      [
          "metrics.impressions",
          "metrics.clicks",
          "metrics.ctr",
          "metrics.conversions",
          "metrics.conversions_value",
      ]
  )
  return fields


@search_term_tool
def list_campaign_search_term_insights(
    customer_id: str,
    campaign_id: str,
    insight_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    min_clicks: int = 0,
    min_impressions: int = 0,
    limit: int = 1000,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign_search_term_insight rows with key metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Required campaign ID filter. Google Ads requires a single
          campaign resource for campaign_search_term_insight.
      insight_id: Optional single insight ID. When provided, includes
          `segments.search_term` and `segments.search_subcategory` for that
          specific insight. Without it, returns category-level rows only.
      date_range: GAQL date range such as LAST_30_DAYS.
      min_clicks: Optional minimum clicks filter.
      min_impressions: Optional minimum impressions filter.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing campaign search term insight rows.
  """
  validate_limit(limit)
  _non_negative(min_clicks, "min_clicks")
  _non_negative(min_impressions, "min_impressions")
  include_search_terms = insight_id is not None

  where_conditions = [
      _date_range_condition(date_range),
      f"campaign.id = {int(campaign_id)}",
  ]
  if insight_id:
    where_conditions.append(
        f"campaign_search_term_insight.id = {int(insight_id)}"
    )
  if min_clicks:
    where_conditions.append(f"metrics.clicks >= {min_clicks}")
  if min_impressions:
    where_conditions.append(f"metrics.impressions >= {min_impressions}")

  query = f"""
      SELECT
        {", ".join(_campaign_insight_select_fields(include_search_terms))}
      FROM campaign_search_term_insight
      {build_where_clause(where_conditions)}
      {"ORDER BY metrics.clicks DESC" if not include_search_terms else ""}
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "campaign_search_term_insights",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["campaign_context"] = _campaign_context_from_rows(
      customer_id,
      page["rows"],
      login_customer_id,
  )
  return result


@search_term_tool
def list_customer_search_term_insights(
    customer_id: str,
    campaign_id: str | None = None,
    campaign_ids: list[str] | None = None,
    insight_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    min_clicks: int = 0,
    min_impressions: int = 0,
    limit: int = 1000,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists customer_search_term_insight rows with key metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Unsupported for this resource. Use
          list_campaign_search_term_insights for campaign-scoped analysis.
      campaign_ids: Unsupported for this resource. Use
          list_campaign_search_term_insights for campaign-scoped analysis.
      insight_id: Optional single insight ID. When provided, includes
          `segments.search_term` and `segments.search_subcategory` for that
          specific insight. Without it, returns category-level rows only.
      date_range: GAQL date range such as LAST_30_DAYS.
      min_clicks: Optional minimum clicks filter.
      min_impressions: Optional minimum impressions filter.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing customer search term insight rows.
  """
  validate_limit(limit)
  _non_negative(min_clicks, "min_clicks")
  _non_negative(min_impressions, "min_impressions")
  campaign_ids = _merge_campaign_ids(campaign_id, campaign_ids)
  include_search_terms = insight_id is not None

  if campaign_ids:
    raise ToolError(
        "customer_search_term_insight does not support campaign_id/"
        "campaign_ids filters. Use list_campaign_search_term_insights for "
        "campaign-scoped analysis or query by insight_id only."
    )

  where_conditions = [_date_range_condition(date_range)]
  if insight_id:
    where_conditions.append(
        f"customer_search_term_insight.id = {int(insight_id)}"
    )
  if min_clicks:
    where_conditions.append(f"metrics.clicks >= {min_clicks}")
  if min_impressions:
    where_conditions.append(f"metrics.impressions >= {min_impressions}")

  query = f"""
      SELECT
        {", ".join(_customer_insight_select_fields(include_search_terms))}
      FROM customer_search_term_insight
      {build_where_clause(where_conditions)}
      {"ORDER BY metrics.clicks DESC" if not include_search_terms else ""}
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "customer_search_term_insights",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@search_term_tool
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
      "campaign_context": _campaign_context_from_rows(
          customer_id,
          search_terms,
          login_customer_id,
      ),
      "heuristics": {
          "date_range": date_range,
          "min_negative_clicks": min_negative_clicks,
          "min_negative_cost_micros": min_negative_cost_micros,
          "min_exact_match_conversions": min_exact_match_conversions,
      },
  }
