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
from ads_mcp.tools._gaql import date_range_label
from ads_mcp.tools._gaql import merge_single_and_list_arg
from ads_mcp.tools._gaql import quote_int_value
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import segments_date_condition
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools._gaql import validate_non_negative_number
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import run_gaql_query
from ads_mcp.tools.api import run_gaql_query_page


search_term_tool = ads_read_tool(mcp, tags={"search_terms"})

_SEARCH_TERM_COMPARE_METRICS = [
    "metrics.impressions",
    "metrics.clicks",
    "metrics.cost_micros",
    "metrics.conversions",
    "metrics.conversions_value",
]
_SEARCH_TERM_COMPARE_SORT_FIELDS = {
    "CLICKS": "metrics.clicks",
    "CONVERSIONS": "metrics.conversions",
    "CONVERSIONS_VALUE": "metrics.conversions_value",
    "COST": "metrics.cost_micros",
    "IMPRESSIONS": "metrics.impressions",
}


def _validate_non_negative_int(value: int, field_name: str) -> None:
  """Validates non-negative integer thresholds used in integer GAQL fields."""
  if isinstance(value, bool) or not isinstance(value, int):
    raise ToolError(f"{field_name} must be an integer.")
  if value < 0:
    raise ToolError(f"{field_name} must be non-negative.")


def _optional_int_filter(value: str | None, field_name: str) -> str | None:
  """Returns a GAQL-safe integer filter or None for omitted values."""
  if value is None:
    return None
  if isinstance(value, str) and not value.strip():
    raise ToolError(f"{field_name} must be a non-empty integer.")
  return quote_int_value(value, field_name)


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


def _search_term_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
  return (
      str(row.get("campaign.id") or ""),
      str(row.get("ad_group.id") or ""),
      str(row.get("search_term_view.search_term") or ""),
      str(row.get("search_term_view.status") or ""),
      str(row.get("segments.search_term_match_type") or ""),
  )


def _search_term_key_payload(row: dict[str, Any]) -> dict[str, Any]:
  return {
      "campaign_id": row.get("campaign.id"),
      "campaign_name": row.get("campaign.name"),
      "ad_group_id": row.get("ad_group.id"),
      "ad_group_name": row.get("ad_group.name"),
      "search_term": row.get("search_term_view.search_term"),
      "status": row.get("search_term_view.status"),
      "match_type": row.get("segments.search_term_match_type"),
  }


def _metric_delta(
    period_a_row: dict[str, Any],
    period_b_row: dict[str, Any],
) -> dict[str, int | float]:
  return {
      metric: (period_a_row.get(metric) or 0) - (period_b_row.get(metric) or 0)
      for metric in _SEARCH_TERM_COMPARE_METRICS
  }


def _search_term_comparison_entry(
    period_a_row: dict[str, Any] | None,
    period_b_row: dict[str, Any] | None,
) -> dict[str, Any]:
  period_a_row = period_a_row or {}
  period_b_row = period_b_row or {}
  key_source = period_a_row or period_b_row
  return {
      **_search_term_key_payload(key_source),
      "period_a": period_a_row,
      "period_b": period_b_row,
      "delta": _metric_delta(period_a_row, period_b_row),
  }


def _search_term_period_rows(
    customer_id: str,
    date_range: str | dict[str, str],
    campaign_ids: list[str] | str | None,
    ad_group_id: str | None,
    min_clicks: int,
    min_cost_micros: int,
    limit: int,
    sort_metric: str,
    login_customer_id: str | None,
) -> list[dict[str, Any]]:
  where_conditions = [segments_date_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_id:
    ad_group_id_filter = quote_int_value(ad_group_id, "ad_group_id")
    where_conditions.append(f"ad_group.id = {ad_group_id_filter}")
  if min_clicks:
    where_conditions.append(f"metrics.clicks >= {min_clicks}")
  if min_cost_micros:
    where_conditions.append(f"metrics.cost_micros >= {min_cost_micros}")

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
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM search_term_view
      {build_where_clause(where_conditions)}
      ORDER BY {sort_metric} DESC
      LIMIT {limit}
  """
  return run_gaql_query(query, customer_id, login_customer_id)


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
    date_range: str | dict[str, str] = "LAST_30_DAYS",
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
  _validate_non_negative_int(min_clicks, "min_clicks")
  _validate_non_negative_int(min_impressions, "min_impressions")
  insight_id_filter = _optional_int_filter(insight_id, "insight_id")
  include_search_terms = insight_id_filter is not None

  campaign_id_filter = quote_int_value(campaign_id, "campaign_id")
  where_conditions = [
      segments_date_condition(date_range),
      f"campaign.id = {campaign_id_filter}",
  ]
  if insight_id_filter is not None:
    where_conditions.append(
        f"campaign_search_term_insight.id = {insight_id_filter}"
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
    campaign_ids: list[str] | str | None = None,
    insight_id: str | None = None,
    date_range: str | dict[str, str] = "LAST_30_DAYS",
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
  _validate_non_negative_int(min_clicks, "min_clicks")
  _validate_non_negative_int(min_impressions, "min_impressions")
  campaign_ids = merge_single_and_list_arg(
      campaign_id,
      campaign_ids,
      "campaign_ids",
  )
  insight_id_filter = _optional_int_filter(insight_id, "insight_id")
  include_search_terms = insight_id_filter is not None

  if campaign_ids:
    raise ToolError(
        "customer_search_term_insight does not support campaign_id/"
        "campaign_ids filters. Use list_campaign_search_term_insights for "
        "campaign-scoped analysis or query by insight_id only."
    )

  where_conditions = [segments_date_condition(date_range)]
  if insight_id_filter is not None:
    where_conditions.append(
        f"customer_search_term_insight.id = {insight_id_filter}"
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
def compare_search_terms(
    customer_id: str,
    period_a: str | dict[str, str],
    period_b: str | dict[str, str],
    campaign_id: str | None = None,
    campaign_ids: list[str] | str | None = None,
    ad_group_id: str | None = None,
    min_clicks: int = 0,
    min_cost_micros: int = 0,
    period_limit: int = 1000,
    top_n: int = 25,
    sort_by: str = "CLICKS",
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Compares search terms between two date windows.

  Args:
      customer_id: Google Ads customer ID.
      period_a: Current/comparison date range.
      period_b: Baseline date range.
      campaign_id: Optional single campaign ID filter.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_id: Optional ad group ID filter.
      min_clicks: Optional minimum clicks in each period query.
      min_cost_micros: Optional minimum cost in each period query.
      period_limit: Maximum raw rows to fetch per period.
      top_n: Maximum rows per comparison bucket.
      sort_by: IMPRESSIONS, CLICKS, COST, CONVERSIONS, or CONVERSIONS_VALUE.
      login_customer_id: Optional manager account ID.

  Returns:
      A compact period-over-period diff with new, lost, improved, and
      declined search terms.
  """
  validate_limit(period_limit)
  validate_limit(top_n)
  _validate_non_negative_int(min_clicks, "min_clicks")
  _validate_non_negative_int(min_cost_micros, "min_cost_micros")
  if not isinstance(sort_by, str) or not sort_by:
    raise ToolError("sort_by must be a non-empty string.")
  normalized_sort_by = sort_by.upper()
  if normalized_sort_by not in _SEARCH_TERM_COMPARE_SORT_FIELDS:
    raise ToolError(
        "Invalid sort_by. Use one of: "
        + ", ".join(sorted(_SEARCH_TERM_COMPARE_SORT_FIELDS))
    )
  sort_metric = _SEARCH_TERM_COMPARE_SORT_FIELDS[normalized_sort_by]

  merged_campaign_ids = merge_single_and_list_arg(
      campaign_id,
      campaign_ids,
      "campaign_ids",
  )
  period_a_rows = _search_term_period_rows(
      customer_id,
      period_a,
      merged_campaign_ids,
      ad_group_id,
      min_clicks,
      min_cost_micros,
      period_limit,
      sort_metric,
      login_customer_id,
  )
  period_b_rows = _search_term_period_rows(
      customer_id,
      period_b,
      merged_campaign_ids,
      ad_group_id,
      min_clicks,
      min_cost_micros,
      period_limit,
      sort_metric,
      login_customer_id,
  )
  period_a_by_key = {_search_term_key(row): row for row in period_a_rows}
  period_b_by_key = {_search_term_key(row): row for row in period_b_rows}
  a_keys = set(period_a_by_key)
  b_keys = set(period_b_by_key)

  def _entry_sort_value(entry: dict[str, Any]) -> int | float:
    return abs(entry["delta"].get(sort_metric) or 0)

  new_terms = [
      _search_term_comparison_entry(period_a_by_key[key], None)
      for key in a_keys - b_keys
  ]
  lost_terms = [
      _search_term_comparison_entry(None, period_b_by_key[key])
      for key in b_keys - a_keys
  ]
  common_entries = [
      _search_term_comparison_entry(period_a_by_key[key], period_b_by_key[key])
      for key in a_keys & b_keys
  ]
  improved_terms = [
      entry
      for entry in common_entries
      if (entry["delta"].get(sort_metric) or 0) > 0
  ]
  declined_terms = [
      entry
      for entry in common_entries
      if (entry["delta"].get(sort_metric) or 0) < 0
  ]

  new_terms.sort(
      key=lambda entry: entry["period_a"].get(sort_metric) or 0,
      reverse=True,
  )
  lost_terms.sort(
      key=lambda entry: entry["period_b"].get(sort_metric) or 0,
      reverse=True,
  )
  improved_terms.sort(key=_entry_sort_value, reverse=True)
  declined_terms.sort(key=_entry_sort_value, reverse=True)
  campaign_context_rows = [*period_a_rows, *period_b_rows]

  return {
      "period_a": date_range_label(period_a),
      "period_b": date_range_label(period_b),
      "sort_by": normalized_sort_by,
      "sort_metric": sort_metric,
      "period_a_row_count": len(period_a_rows),
      "period_b_row_count": len(period_b_rows),
      "common_count": len(a_keys & b_keys),
      "new_count": len(new_terms),
      "lost_count": len(lost_terms),
      "period_limit": period_limit,
      "top_n": top_n,
      "new_terms": new_terms[:top_n],
      "lost_terms": lost_terms[:top_n],
      "improved_terms": improved_terms[:top_n],
      "declined_terms": declined_terms[:top_n],
      "campaign_context": _campaign_context_from_rows(
          customer_id,
          campaign_context_rows,
          login_customer_id,
      ),
  }


@search_term_tool
def analyze_search_terms(
    customer_id: str,
    campaign_id: str | None = None,
    ad_group_id: str | None = None,
    date_range: str | dict[str, str] = "LAST_30_DAYS",
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
  _validate_non_negative_int(
      min_negative_clicks,
      "min_negative_clicks",
  )
  _validate_non_negative_int(
      min_negative_cost_micros,
      "min_negative_cost_micros",
  )
  validate_non_negative_number(
      min_exact_match_conversions,
      "min_exact_match_conversions",
  )

  where_conditions = [segments_date_condition(date_range)]
  if campaign_id:
    campaign_id_filter = quote_int_value(campaign_id, "campaign_id")
    where_conditions.append(f"campaign.id = {campaign_id_filter}")
  if ad_group_id:
    ad_group_id_filter = quote_int_value(ad_group_id, "ad_group_id")
    where_conditions.append(f"ad_group.id = {ad_group_id_filter}")

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
