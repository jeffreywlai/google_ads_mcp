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

"""Curated reporting tools for high-frequency Google Ads read workflows."""

from collections import Counter
import math
import re
from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._campaign_context import get_campaign_context
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import validate_date_range
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import gaql_quote_string
from ads_mcp.tools.api import run_gaql_query
from ads_mcp.tools.api import run_gaql_query_page


def _date_range_condition(date_range: str) -> str:
  return f"segments.date DURING {validate_date_range(date_range)}"


def _normalize_choice(
    value: object,
    field_name: str,
    allowed_values: set[str],
) -> str:
  if not isinstance(value, str) or not value:
    raise ToolError(f"{field_name} must be a non-empty string.")
  normalized_value = value.upper()
  if normalized_value not in allowed_values:
    allowed_values_text = ", ".join(sorted(allowed_values))
    raise ToolError(
        f"Invalid {field_name}: {value}. "
        f"Use one of: {allowed_values_text}."
    )
  return normalized_value


def _normalize_choices(
    values: list[str],
    field_name: str,
    allowed_values: set[str],
) -> list[str]:
  """Normalizes and deduplicates a list of enum-like input values."""
  normalized_choices: list[str] = []
  seen: set[str] = set()
  for value in values:
    normalized_value = _normalize_choice(value, field_name, allowed_values)
    if normalized_value in seen:
      continue
    seen.add(normalized_value)
    normalized_choices.append(normalized_value)
  return normalized_choices


def _normalize_enum_filters(values: list[str], field_name: str) -> list[str]:
  """Normalizes GAQL enum filters while rejecting malformed values."""
  normalized_values: list[str] = []
  seen: set[str] = set()
  for value in values:
    if not isinstance(value, str):
      raise ToolError(f"{field_name} values must be strings.")
    normalized_value = value.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized_value):
      raise ToolError(
          f"Invalid {field_name}: {value}. Use Google Ads enum names."
      )
    if normalized_value in seen:
      continue
    seen.add(normalized_value)
    normalized_values.append(normalized_value)
  return normalized_values


def _validate_quality_score(min_quality_score: int | None) -> None:
  if min_quality_score is None:
    return
  if min_quality_score < 1 or min_quality_score > 10:
    raise ToolError("min_quality_score must be between 1 and 10.")


def _validate_non_negative(value: int | float, field_name: str) -> None:
  """Validates non-negative numeric threshold inputs."""
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ToolError(f"{field_name} must be a number.")
  if value < 0:
    raise ToolError(f"{field_name} must be non-negative.")


def _campaign_ids_from_rows(rows: list[dict[str, Any]]) -> list[str]:
  """Returns unique campaign IDs present in GAQL result rows."""
  campaign_ids = {
      row.get("campaign.id")
      for row in rows
      if row.get("campaign.id") not in (None, "")
  }
  return sorted((str(campaign_id) for campaign_id in campaign_ids), key=int)


_CART_GROUP_FIELDS = {
    "CAMPAIGN": ["campaign.id", "campaign.name"],
    "ADVERTISED_ITEM": ["segments.product_item_id", "segments.product_title"],
    "ADVERTISED_BRAND": ["segments.product_brand"],
    "ADVERTISED_CATEGORY": ["segments.product_category_level1"],
    "SOLD_ITEM": [
        "segments.product_sold_item_id",
        "segments.product_sold_title",
    ],
    "SOLD_BRAND": ["segments.product_sold_brand"],
    "SOLD_CATEGORY": ["segments.product_sold_category_level1"],
}
_CART_ALL_METRICS = [
    "metrics.all_revenue_micros",
    "metrics.all_cost_of_goods_sold_micros",
    "metrics.all_gross_profit_micros",
    "metrics.all_gross_profit_margin",
    "metrics.all_units_sold",
    "metrics.all_lead_revenue_micros",
    "metrics.all_lead_cost_of_goods_sold_micros",
    "metrics.all_lead_gross_profit_micros",
    "metrics.all_lead_units_sold",
    "metrics.all_cross_sell_revenue_micros",
    "metrics.all_cross_sell_cost_of_goods_sold_micros",
    "metrics.all_cross_sell_gross_profit_micros",
    "metrics.all_cross_sell_units_sold",
]
_CART_BIDDABLE_METRICS = [
    "metrics.revenue_micros",
    "metrics.cost_of_goods_sold_micros",
    "metrics.gross_profit_micros",
    "metrics.gross_profit_margin",
    "metrics.units_sold",
    "metrics.lead_revenue_micros",
    "metrics.lead_cost_of_goods_sold_micros",
    "metrics.lead_gross_profit_micros",
    "metrics.lead_units_sold",
    "metrics.cross_sell_revenue_micros",
    "metrics.cross_sell_cost_of_goods_sold_micros",
    "metrics.cross_sell_gross_profit_micros",
    "metrics.cross_sell_units_sold",
]
_CART_OUTLIER_SORT_FIELDS = {
    "GROSS_PROFIT": "metrics.all_gross_profit_micros",
    "GROSS_PROFIT_MARGIN": "metrics.all_gross_profit_margin",
    "REVENUE": "metrics.all_revenue_micros",
    "UNITS_SOLD": "metrics.all_units_sold",
    "CROSS_SELL_GROSS_PROFIT": ("metrics.all_cross_sell_gross_profit_micros"),
}
_VERTICAL_ADS_FIELDS = {
    "VERTICAL": "segments.vertical_ads_vertical",
    "LISTING": "segments.vertical_ads_listing",
    "BRAND": "segments.vertical_ads_listing_brand",
    "CITY": "segments.vertical_ads_listing_city",
    "COUNTRY": "segments.vertical_ads_listing_country",
    "REGION": "segments.vertical_ads_listing_region",
    "PARTNER_ACCOUNT": "segments.vertical_ads_partner_account",
}
_CONTENT_SUITABILITY_VIEW_FIELDS = {
    "DETAIL": (
        "detail_content_suitability_placement_view",
        [
            "detail_content_suitability_placement_view.display_name",
            "detail_content_suitability_placement_view.placement",
            "detail_content_suitability_placement_view.placement_type",
            "detail_content_suitability_placement_view.target_url",
        ],
    ),
    "GROUP": (
        "group_content_suitability_placement_view",
        [
            "group_content_suitability_placement_view.display_name",
            "group_content_suitability_placement_view.placement",
            "group_content_suitability_placement_view.placement_type",
            "group_content_suitability_placement_view.target_url",
        ],
    ),
}
_LOCATION_INTEREST_VIEW_FIELDS = {
    "LOCATION_INTEREST": (
        "location_interest_view",
        ["ad_group_criterion.location.geo_target_constant"],
    ),
    "MATCHED_LOCATION_INTEREST": (
        "matched_location_interest_view",
        [
            "segments.geo_target_most_specific_location",
            "segments.geo_target_region",
            "segments.geo_target_city",
        ],
    ),
}


def _issue_field(issue: Any, field_name: str) -> Any:
  """Returns a field from a shopping product issue dict/proto-like value."""
  if isinstance(issue, dict):
    return issue.get(field_name)
  return getattr(issue, field_name, None)


def _issue_label(issue: Any, field_name: str) -> str:
  """Returns a stable issue label for compact counters."""
  value = _issue_field(issue, field_name)
  if value in (None, ""):
    return "UNKNOWN"
  return str(value)


def _cart_group_fields(group_by: object) -> tuple[str, list[str]]:
  """Returns a normalized cart-data grouping and its select fields."""
  if not isinstance(group_by, str) or not group_by:
    raise ToolError("group_by must be a non-empty string.")
  normalized_group_by = group_by.upper()
  if normalized_group_by not in _CART_GROUP_FIELDS:
    raise ToolError(
        "Invalid group_by. Use one of: "
        + ", ".join(sorted(_CART_GROUP_FIELDS))
    )
  return normalized_group_by, _CART_GROUP_FIELDS[normalized_group_by]


def _numeric_delta(row: dict[str, Any], lhs: str, rhs: str) -> int | float:
  """Returns lhs - rhs while treating missing numeric metrics as zero."""
  return (row.get(lhs) or 0) - (row.get(rhs) or 0)


def _keyword_quality_score_query(
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    min_quality_score: int | None = None,
) -> str:
  """Builds the shared keyword quality score GAQL query."""
  where_conditions = [
      "ad_group_criterion.type = KEYWORD",
      "ad_group_criterion.negative = FALSE",
  ]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )
  if min_quality_score is not None:
    where_conditions.append(
        "ad_group_criterion.quality_info.quality_score >= "
        f"{min_quality_score}"
    )

  return f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        ad_group_criterion.criterion_id,
        ad_group_criterion.keyword.text,
        ad_group_criterion.keyword.match_type,
        ad_group_criterion.status,
        ad_group_criterion.quality_info.quality_score,
        ad_group_criterion.quality_info.creative_quality_score,
        ad_group_criterion.quality_info.post_click_quality_score,
        ad_group_criterion.quality_info.search_predicted_ctr
      FROM keyword_view
      {build_where_clause(where_conditions)}
      ORDER BY
        ad_group_criterion.quality_info.quality_score ASC,
        campaign.id ASC,
        ad_group.id ASC,
        ad_group_criterion.criterion_id ASC
  """


def _distribution(
    counter: Counter[Any],
    key_name: str,
    count_name: str,
) -> list[dict[str, Any]]:
  """Serializes a counter into a stable list of dicts."""

  def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
      return (2, "")
    if isinstance(value, (int, float)):
      return (0, value)
    return (1, str(value))

  ordered_items = sorted(
      counter.items(),
      key=lambda item: _sort_key(item[0]),
  )
  return [
      {key_name: value, count_name: count} for value, count in ordered_items
  ]


reporting_tool = ads_read_tool(mcp, tags={"reporting"})


@reporting_tool
def list_device_performance(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign performance segmented by device.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing device-segmented campaign performance rows.
  """
  validate_limit(limit)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign.status,
        campaign.advertising_channel_type,
        segments.device,
        metrics.impressions,
        metrics.clicks,
        metrics.ctr,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM campaign
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "device_performance",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_geographic_performance(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    location_view: str = "USER_LOCATION",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign performance segmented by geography.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      location_view: USER_LOCATION or GEOGRAPHIC.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing geographic performance rows.
  """
  validate_limit(limit)
  normalized_view = _normalize_choice(
      location_view,
      "location_view",
      {"GEOGRAPHIC", "USER_LOCATION"},
  )

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  if normalized_view == "GEOGRAPHIC":
    select_fields = [
        "campaign.id",
        "campaign.name",
        "geographic_view.country_criterion_id",
        "geographic_view.location_type",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.ctr",
        "metrics.cost_micros",
        "metrics.conversions",
        "metrics.conversions_value",
    ]
    from_resource = "geographic_view"
  else:
    select_fields = [
        "campaign.id",
        "campaign.name",
        "user_location_view.country_criterion_id",
        "user_location_view.targeting_location",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.ctr",
        "metrics.cost_micros",
        "metrics.conversions",
        "metrics.conversions_value",
    ]
    from_resource = "user_location_view"

  query = f"""
      SELECT
        {", ".join(select_fields)}
      FROM {from_resource}
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "geographic_performance",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["location_view"] = normalized_view
  return result


@reporting_tool
def list_impression_share(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    enabled_only: bool = True,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign impression share metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      enabled_only: Whether to include only ENABLED campaigns.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing campaign impression share rows.
  """
  validate_limit(limit)

  where_conditions = [_date_range_condition(date_range)]
  if enabled_only:
    where_conditions.append("campaign.status = ENABLED")
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign.status,
        metrics.impressions,
        metrics.clicks,
        metrics.search_impression_share,
        metrics.search_top_impression_share,
        metrics.search_absolute_top_impression_share,
        metrics.search_budget_lost_impression_share,
        metrics.search_rank_lost_impression_share
      FROM campaign
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "impression_share",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def get_campaign_conversion_goals(
    customer_id: str,
    campaign_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Returns campaign conversion goals, including custom goal config.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Campaign ID to inspect.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with campaign goal config, standard goals, and custom goal
      details when configured.
  """
  campaign_id = str(int(campaign_id))
  config_rows = run_gaql_query(
      f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign.status,
        conversion_goal_campaign_config.goal_config_level,
        conversion_goal_campaign_config.custom_conversion_goal
      FROM conversion_goal_campaign_config
      WHERE campaign.id = {campaign_id}
      LIMIT 1
      """,
      customer_id,
      login_customer_id,
  )
  if not config_rows:
    raise ToolError(
        f"No conversion goal config was returned for {campaign_id}."
    )

  standard_goal_rows = run_gaql_query(
      f"""
      SELECT
        campaign_conversion_goal.category,
        campaign_conversion_goal.origin,
        campaign_conversion_goal.biddable
      FROM campaign_conversion_goal
      WHERE campaign.id = {campaign_id}
      ORDER BY campaign_conversion_goal.category,
        campaign_conversion_goal.origin
      """,
      customer_id,
      login_customer_id,
  )

  config = config_rows[0]
  custom_goal_resource = config.get(
      "conversion_goal_campaign_config.custom_conversion_goal"
  )
  custom_goal = None
  if custom_goal_resource:
    quoted_custom_goal_resource = gaql_quote_string(custom_goal_resource)
    custom_goal_resource_condition = (
        "custom_conversion_goal.resource_name = "
        f"{quoted_custom_goal_resource}"
    )
    custom_goal_rows = run_gaql_query(
        f"""
        SELECT
          custom_conversion_goal.id,
          custom_conversion_goal.name,
          custom_conversion_goal.status,
          custom_conversion_goal.conversion_actions
        FROM custom_conversion_goal
        WHERE {custom_goal_resource_condition}
        LIMIT 1
        """,
        customer_id,
        login_customer_id,
    )
    if custom_goal_rows:
      custom_goal = {
          "id": custom_goal_rows[0].get("custom_conversion_goal.id"),
          "name": custom_goal_rows[0].get("custom_conversion_goal.name"),
          "status": custom_goal_rows[0].get("custom_conversion_goal.status"),
          "conversion_actions": custom_goal_rows[0].get(
              "custom_conversion_goal.conversion_actions"
          ),
          "resource_name": custom_goal_resource,
      }

  campaign_context = get_campaign_context(
      customer_id,
      [campaign_id],
      login_customer_id,
  ).get(campaign_id, {})

  return {
      "campaign": {
          "id": config["campaign.id"],
          "name": config.get("campaign.name"),
          "status": config.get("campaign.status"),
          "recent_30_day_cost_micros": campaign_context.get(
              "recent_30_day_cost_micros", 0
          ),
      },
      "goal_config_level": config.get(
          "conversion_goal_campaign_config.goal_config_level"
      ),
      "uses_custom_conversion_goal": bool(custom_goal_resource),
      "custom_conversion_goal": custom_goal,
      "standard_conversion_goals": [
          {
              "category": row.get("campaign_conversion_goal.category"),
              "origin": row.get("campaign_conversion_goal.origin"),
              "biddable": row.get("campaign_conversion_goal.biddable"),
          }
          for row in standard_goal_rows
      ],
  }


@reporting_tool
def list_keyword_quality_scores(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    min_quality_score: int | None = None,
    limit: int | None = 1000,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists keyword quality score diagnostics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      min_quality_score: Optional minimum quality score from 1 to 10.
      limit: Page size when paginating quality-score results. Set to None
          to return all rows without pagination.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing keyword quality score rows.
  """
  _validate_quality_score(min_quality_score)
  if limit is not None:
    validate_limit(limit)

  query = _keyword_quality_score_query(
      campaign_ids=campaign_ids,
      ad_group_ids=ad_group_ids,
      min_quality_score=min_quality_score,
  )

  if limit is None:
    rows = run_gaql_query(query, customer_id, login_customer_id)
    campaign_context = get_campaign_context(
        customer_id,
        _campaign_ids_from_rows(rows),
        login_customer_id,
    )
    return {
        "keyword_quality_scores": rows,
        "returned_count": len(rows),
        "total_count": len(rows),
        "returned_row_count": len(rows),
        "total_row_count": len(rows),
        "total_page_count": 1,
        "truncated": False,
        "next_page_token": None,
        "page_size": None,
        "campaign_context": campaign_context,
    }

  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  total_row_count = page["total_results_count"]
  total_page_count = (
      math.ceil(total_row_count / limit) if total_row_count else 0
  )
  campaign_context = get_campaign_context(
      customer_id,
      _campaign_ids_from_rows(page["rows"]),
      login_customer_id,
  )

  return {
      "keyword_quality_scores": page["rows"],
      "returned_count": len(page["rows"]),
      "total_count": total_row_count,
      "returned_row_count": len(page["rows"]),
      "total_row_count": total_row_count,
      "total_page_count": total_page_count,
      "truncated": page["next_page_token"] is not None,
      "next_page_token": page["next_page_token"],
      "page_size": limit,
      "campaign_context": campaign_context,
  }


@reporting_tool
def summarize_keyword_quality_scores(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    min_quality_score: int | None = None,
    top_campaigns_limit: int = 20,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Summarizes keyword quality-score results without returning raw rows.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      min_quality_score: Optional minimum quality score from 1 to 10.
      top_campaigns_limit: Maximum number of campaign summary rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with compact quality-score distributions and top campaigns.
  """
  _validate_quality_score(min_quality_score)
  validate_limit(top_campaigns_limit)

  rows = run_gaql_query(
      _keyword_quality_score_query(
          campaign_ids=campaign_ids,
          ad_group_ids=ad_group_ids,
          min_quality_score=min_quality_score,
      ),
      customer_id,
      login_customer_id,
  )
  quality_score_counts: Counter[Any] = Counter()
  match_type_counts: Counter[Any] = Counter()
  status_counts: Counter[Any] = Counter()
  campaign_counts: Counter[str] = Counter()
  campaign_names: dict[str, Any] = {}
  scored_keywords = 0
  total_quality_score = 0

  for row in rows:
    quality_score = row.get("ad_group_criterion.quality_info.quality_score")
    quality_score_counts[quality_score] += 1
    match_type_counts[row.get("ad_group_criterion.keyword.match_type")] += 1
    status_counts[row.get("ad_group_criterion.status")] += 1

    campaign_id = row.get("campaign.id")
    if campaign_id:
      campaign_counts[campaign_id] += 1
      campaign_names[campaign_id] = row.get("campaign.name")

    if quality_score is not None:
      scored_keywords += 1
      total_quality_score += quality_score

  campaign_context = get_campaign_context(
      customer_id,
      list(campaign_counts),
      login_customer_id,
  )
  top_campaigns = []
  for campaign_id, keyword_count in campaign_counts.most_common(
      top_campaigns_limit
  ):
    top_campaigns.append(
        {
            "campaign.id": campaign_id,
            "campaign.name": campaign_names.get(campaign_id),
            "keyword_count": keyword_count,
        }
    )

  return {
      "total_keyword_count": len(rows),
      "scored_keyword_count": scored_keywords,
      "unscored_keyword_count": len(rows) - scored_keywords,
      "average_quality_score": (
          round(total_quality_score / scored_keywords, 2)
          if scored_keywords
          else None
      ),
      "quality_score_distribution": _distribution(
          quality_score_counts,
          "quality_score",
          "keyword_count",
      ),
      "match_type_distribution": _distribution(
          match_type_counts,
          "match_type",
          "keyword_count",
      ),
      "keyword_status_distribution": _distribution(
          status_counts,
          "status",
          "keyword_count",
      ),
      "campaign_distribution": top_campaigns,
      "campaign_context": campaign_context,
  }


@reporting_tool
def list_rsa_ad_strength(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists RSA ad strength diagnostics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing responsive search ad strength rows.
  """
  validate_limit(limit)

  where_conditions = [
      _date_range_condition(date_range),
      "ad_group_ad.ad.type = RESPONSIVE_SEARCH_AD",
  ]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        ad_group_ad.ad.id,
        ad_group_ad.ad.name,
        ad_group_ad.status,
        ad_group_ad.ad_strength,
        ad_group_ad.primary_status,
        ad_group_ad.policy_summary.approval_status,
        ad_group_ad.action_items,
        metrics.impressions,
        metrics.clicks
      FROM ad_group_ad
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "rsa_ad_strength",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_conversion_actions(
    customer_id: str,
    statuses: list[str] | None = None,
    types: list[str] | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists conversion action configuration.

  Args:
      customer_id: Google Ads customer ID.
      statuses: Optional conversion action statuses to filter to.
      types: Optional conversion action types to filter to.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing conversion action rows.
  """
  validate_limit(limit)

  where_conditions = []
  if statuses:
    where_conditions.append(
        "conversion_action.status IN " f"({quote_enum_values(statuses)})"
    )
  if types:
    where_conditions.append(
        f"conversion_action.type IN ({quote_enum_values(types)})"
    )

  query = f"""
      SELECT
        conversion_action.id,
        conversion_action.name,
        conversion_action.category,
        conversion_action.type,
        conversion_action.status,
        conversion_action.origin,
        conversion_action.primary_for_goal,
        conversion_action.include_in_conversions_metric,
        conversion_action.counting_type,
        conversion_action.attribution_model_settings.attribution_model
      FROM conversion_action
      {build_where_clause(where_conditions)}
      ORDER BY conversion_action.name
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "conversion_actions",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_audience_performance(
    customer_id: str,
    scope: str = "CAMPAIGN",
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists audience performance at campaign or ad group scope.

  Args:
      customer_id: Google Ads customer ID.
      scope: CAMPAIGN or AD_GROUP.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to when scope is AD_GROUP.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing audience performance rows.
  """
  validate_limit(limit)
  normalized_scope = _normalize_choice(
      scope, "scope", {"AD_GROUP", "CAMPAIGN"}
  )
  if normalized_scope == "CAMPAIGN" and ad_group_ids:
    raise ToolError("ad_group_ids can only be used when scope is AD_GROUP.")

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  if normalized_scope == "AD_GROUP":
    select_fields = [
        "campaign.id",
        "campaign.name",
        "ad_group.id",
        "ad_group.name",
        "ad_group_audience_view.resource_name",
        "ad_group_criterion.criterion_id",
        "ad_group_criterion.type",
        "ad_group_criterion.user_list.user_list",
        "ad_group_criterion.user_interest.user_interest_category",
        "ad_group_criterion.combined_audience.combined_audience",
        "ad_group_criterion.custom_audience.custom_audience",
        "ad_group_criterion.audience.audience",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.ctr",
        "metrics.cost_micros",
        "metrics.conversions",
        "metrics.conversions_value",
    ]
    from_resource = "ad_group_audience_view"
    if ad_group_ids:
      where_conditions.append(
          f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
      )
  else:
    select_fields = [
        "campaign.id",
        "campaign.name",
        "campaign_audience_view.resource_name",
        "campaign_criterion.criterion_id",
        "campaign_criterion.type",
        "campaign_criterion.user_list.user_list",
        "campaign_criterion.user_interest.user_interest_category",
        "campaign_criterion.combined_audience.combined_audience",
        "campaign_criterion.custom_audience.custom_audience",
        "metrics.impressions",
        "metrics.clicks",
        "metrics.ctr",
        "metrics.cost_micros",
        "metrics.conversions",
        "metrics.conversions_value",
    ]
    from_resource = "campaign_audience_view"

  query = f"""
      SELECT
        {", ".join(select_fields)}
      FROM {from_resource}
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "audience_performance",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["scope"] = normalized_scope
  return result


@reporting_tool
def list_video_enhancements(
    customer_id: str,
    sources: list[str] | None = None,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists Video Enhancement rows with performance metrics.

  Args:
      customer_id: Google Ads customer ID.
      sources: Optional source filters. Accepted values are ADVERTISER,
          ENHANCED_BY_GOOGLE_ADS, UNKNOWN, and UNSPECIFIED.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing Video Enhancement rows and pagination metadata.
  """
  validate_limit(limit)

  normalized_sources = None
  if sources:
    normalized_sources = _normalize_choices(
        sources,
        "sources",
        {
            "ADVERTISER",
            "ENHANCED_BY_GOOGLE_ADS",
            "UNKNOWN",
            "UNSPECIFIED",
        },
    )

  where_conditions = [_date_range_condition(date_range)]
  if normalized_sources:
    where_conditions.append(
        "video_enhancement.source IN "
        f"({quote_enum_values(normalized_sources)})"
    )
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign.advertising_channel_type,
        ad_group.id,
        ad_group.name,
        video_enhancement.resource_name,
        video_enhancement.title,
        video_enhancement.source,
        video_enhancement.duration_millis,
        metrics.impressions,
        metrics.clicks,
        metrics.video_trueview_views,
        metrics.video_trueview_view_rate,
        metrics.video_watch_time_duration_millis
      FROM video_enhancement
      {build_where_clause(where_conditions)}
      ORDER BY
        metrics.impressions DESC,
        campaign.id ASC,
        ad_group.id ASC,
        video_enhancement.title ASC,
        video_enhancement.source ASC,
        video_enhancement.duration_millis ASC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "video_enhancements",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  if normalized_sources:
    result["sources"] = normalized_sources
  return result


@reporting_tool
def summarize_cart_data_sales(
    customer_id: str,
    group_by: str = "CAMPAIGN",
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    top_limit: int = 25,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Summarizes v24 cart-data sales and profit metrics.

  This uses `cart_data_sales_view`, which only returns useful rows when
  conversions with cart data are implemented. Prefer this summary before
  requesting detailed product rows.

  Args:
      customer_id: Google Ads customer ID.
      group_by: CAMPAIGN, ADVERTISED_ITEM, ADVERTISED_BRAND,
          ADVERTISED_CATEGORY, SOLD_ITEM, SOLD_BRAND, or SOLD_CATEGORY.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      top_limit: Maximum grouped rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A compact dict containing grouped all-conversion cart profitability.
  """
  validate_limit(top_limit)
  normalized_group_by, group_fields = _cart_group_fields(group_by)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        {", ".join(group_fields + _CART_ALL_METRICS)}
      FROM cart_data_sales_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.all_gross_profit_micros DESC
      LIMIT {top_limit}
  """
  rows = run_gaql_query(query, customer_id, login_customer_id)
  context_campaign_ids = (
      campaign_ids
      if campaign_ids
      else _campaign_ids_from_rows(rows)
      if normalized_group_by == "CAMPAIGN"
      else []
  )
  return {
      "group_by": normalized_group_by,
      "date_range": validate_date_range(date_range),
      "cart_data_sales": rows,
      "returned_count": len(rows),
      "top_limit": top_limit,
      "campaign_context": get_campaign_context(
          customer_id,
          context_campaign_ids,
          login_customer_id,
      ),
  }


@reporting_tool
def compare_biddable_vs_all_cart_value(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 25,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Compares biddable cart metrics with all cart metrics by campaign.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum campaign rows to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with campaign rows and deltas showing value outside biddable
      conversion metrics.
  """
  validate_limit(limit)

  normalized_date_range = validate_date_range(date_range)
  where_conditions = [_date_range_condition(normalized_date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        {", ".join(_CART_BIDDABLE_METRICS + _CART_ALL_METRICS)}
      FROM cart_data_sales_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.all_gross_profit_micros DESC
      LIMIT {limit}
  """
  rows = run_gaql_query(query, customer_id, login_customer_id)
  comparisons = []
  for row in rows:
    enriched_row = dict(row)
    enriched_row["non_biddable_revenue_micros"] = _numeric_delta(
        row,
        "metrics.all_revenue_micros",
        "metrics.revenue_micros",
    )
    enriched_row["non_biddable_gross_profit_micros"] = _numeric_delta(
        row,
        "metrics.all_gross_profit_micros",
        "metrics.gross_profit_micros",
    )
    enriched_row["non_biddable_units_sold"] = _numeric_delta(
        row,
        "metrics.all_units_sold",
        "metrics.units_sold",
    )
    comparisons.append(enriched_row)

  return {
      "date_range": normalized_date_range,
      "cart_value_comparisons": comparisons,
      "returned_count": len(comparisons),
      "limit": limit,
      "campaign_context": get_campaign_context(
          customer_id,
          _campaign_ids_from_rows(comparisons),
          login_customer_id,
      ),
  }


@reporting_tool
def list_cart_profit_outliers(
    customer_id: str,
    group_by: str = "SOLD_ITEM",
    sort_by: str = "GROSS_PROFIT",
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    direction: str = "ASC",
    limit: int = 25,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists highest or lowest cart-data profit outliers.

  Args:
      customer_id: Google Ads customer ID.
      group_by: Cart grouping such as SOLD_ITEM, SOLD_BRAND, or CAMPAIGN.
      sort_by: GROSS_PROFIT, GROSS_PROFIT_MARGIN, REVENUE, UNITS_SOLD, or
          CROSS_SELL_GROSS_PROFIT.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      direction: ASC for worst/lowest or DESC for best/highest.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing cart-data profit outlier rows.
  """
  validate_limit(limit)
  normalized_group_by, group_fields = _cart_group_fields(group_by)
  normalized_sort_by = _normalize_choice(
      sort_by,
      "sort_by",
      set(_CART_OUTLIER_SORT_FIELDS),
  )
  normalized_direction = _normalize_choice(
      direction,
      "direction",
      {"ASC", "DESC"},
  )

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        {", ".join(group_fields + _CART_ALL_METRICS)}
      FROM cart_data_sales_view
      {build_where_clause(where_conditions)}
      ORDER BY {_CART_OUTLIER_SORT_FIELDS[normalized_sort_by]}
        {normalized_direction}
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "cart_profit_outliers",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["group_by"] = normalized_group_by
  result["sort_by"] = normalized_sort_by
  result["direction"] = normalized_direction
  return result


@reporting_tool
def list_shopping_attribution_breakdown(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists Shopping performance by conversion attribution event type.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing Shopping attribution breakdown rows.
  """
  validate_limit(limit)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        segments.conversion_attribution_event_type,
        segments.product_item_id,
        segments.product_title,
        segments.product_brand,
        metrics.conversions,
        metrics.conversions_value,
        metrics.all_conversions,
        metrics.all_conversions_value,
        metrics.revenue_micros,
        metrics.all_revenue_micros,
        metrics.gross_profit_micros,
        metrics.all_gross_profit_micros,
        metrics.units_sold,
        metrics.all_units_sold
      FROM shopping_performance_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.all_conversions_value DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "shopping_attribution_breakdown",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_campaign_view_through_optimization(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    advertising_channel_types: list[str] | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign view-through conversion optimization settings.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      advertising_channel_types: Optional channel filters such as DEMAND_GEN
          or MULTI_CHANNEL.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing view-through optimization settings.
  """
  validate_limit(limit)

  where_conditions = []
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if advertising_channel_types:
    advertising_channel_types = _normalize_enum_filters(
        advertising_channel_types,
        "advertising_channel_types",
    )
    where_conditions.append(
        "campaign.advertising_channel_type IN "
        f"({quote_enum_values(advertising_channel_types)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign.status,
        campaign.advertising_channel_type,
        campaign.view_through_conversion_optimization_enabled
      FROM campaign
      {build_where_clause(where_conditions)}
      ORDER BY campaign.id ASC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "campaign_view_through_optimization",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_video_audibility_performance(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists video audibility and watch-time metrics by campaign.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing video audibility rows.
  """
  validate_limit(limit)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign.status,
        campaign.advertising_channel_type,
        metrics.impressions,
        metrics.video_trueview_views,
        metrics.video_trueview_view_rate,
        metrics.video_watch_time_duration_millis,
        metrics.average_video_watch_time_duration_millis,
        metrics.active_view_audibility_measurable_impressions,
        metrics.active_view_audibility_measurable_impressions_rate,
        metrics.active_view_audible_impressions,
        metrics.active_view_audible_impressions_rate,
        metrics.active_view_audible_two_seconds_impressions,
        metrics.active_view_audible_two_seconds_impressions_rate,
        metrics.active_view_audible_thirty_seconds_impressions,
        metrics.active_view_audible_thirty_seconds_impressions_rate
      FROM campaign
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "video_audibility_performance",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_vertical_ads_performance(
    customer_id: str,
    segment_by: str = "VERTICAL",
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists vertical-ads performance for travel and local inventory.

  Args:
      customer_id: Google Ads customer ID.
      segment_by: VERTICAL, LISTING, BRAND, CITY, COUNTRY, REGION, or
          PARTNER_ACCOUNT.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing vertical-ads performance rows.
  """
  validate_limit(limit)
  normalized_segment_by = _normalize_choice(
      segment_by,
      "segment_by",
      set(_VERTICAL_ADS_FIELDS),
  )

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        {_VERTICAL_ADS_FIELDS[normalized_segment_by]},
        metrics.impressions,
        metrics.clicks,
        metrics.ctr,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM campaign
      {build_where_clause(where_conditions)}
      ORDER BY metrics.conversions_value DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "vertical_ads_performance",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["segment_by"] = normalized_segment_by
  return result


@reporting_tool
def list_campaign_search_terms(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    min_clicks: int = 0,
    min_cost_micros: int = 0,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign-level search terms with compact cost metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      min_clicks: Optional minimum clicks filter.
      min_cost_micros: Optional minimum cost filter.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing campaign search term rows.
  """
  validate_limit(limit)
  _validate_non_negative(min_clicks, "min_clicks")
  _validate_non_negative(min_cost_micros, "min_cost_micros")

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if min_clicks:
    where_conditions.append(f"metrics.clicks >= {min_clicks}")
  if min_cost_micros:
    where_conditions.append(f"metrics.cost_micros >= {min_cost_micros}")

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign_search_term_view.search_term,
        segments.search_term_match_source,
        segments.search_term_match_type,
        segments.search_term_targeting_status,
        metrics.impressions,
        metrics.clicks,
        metrics.ctr,
        metrics.cost_micros,
        metrics.conversions,
        metrics.cost_per_conversion,
        metrics.conversions_value
      FROM campaign_search_term_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.cost_micros DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "campaign_search_terms",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_ai_max_search_term_ad_combinations(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    min_impressions: int = 0,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists AI Max search term, headline, and landing-page combinations.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      min_impressions: Optional minimum impressions filter.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing AI Max combination rows.
  """
  validate_limit(limit)
  _validate_non_negative(min_impressions, "min_impressions")

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )
  if min_impressions:
    where_conditions.append(f"metrics.impressions >= {min_impressions}")

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        ai_max_search_term_ad_combination_view.search_term,
        ai_max_search_term_ad_combination_view.headline,
        ai_max_search_term_ad_combination_view.landing_page,
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM ai_max_search_term_ad_combination_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.conversions_value DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "ai_max_search_term_ad_combinations",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_final_url_expansion_assets(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    asset_group_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    field_types: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists final URL expansion assets with recent performance.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      asset_group_ids: Optional asset group IDs to filter to.
      statuses: Optional final URL expansion asset statuses.
      field_types: Optional final URL expansion field types.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing final URL expansion asset rows.
  """
  validate_limit(limit)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if asset_group_ids:
    where_conditions.append(
        f"asset_group.id IN ({quote_int_values(asset_group_ids)})"
    )
  if statuses:
    statuses = _normalize_enum_filters(statuses, "statuses")
    where_conditions.append(
        "final_url_expansion_asset_view.status IN "
        f"({quote_enum_values(statuses)})"
    )
  if field_types:
    field_types = _normalize_enum_filters(field_types, "field_types")
    where_conditions.append(
        "final_url_expansion_asset_view.field_type IN "
        f"({quote_enum_values(field_types)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        asset_group.id,
        asset_group.name,
        final_url_expansion_asset_view.final_url,
        final_url_expansion_asset_view.field_type,
        final_url_expansion_asset_view.status,
        metrics.impressions,
        metrics.conversions,
        metrics.conversions_value
      FROM final_url_expansion_asset_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "final_url_expansion_assets",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_targeting_expansion_performance(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists automated targeting expansion performance.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing targeting expansion performance rows.
  """
  validate_limit(limit)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        targeting_expansion_view.resource_name,
        metrics.impressions,
        metrics.clicks,
        metrics.ctr,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value,
        metrics.value_per_conversion
      FROM targeting_expansion_view
      {build_where_clause(where_conditions)}
      ORDER BY metrics.conversions_value DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "targeting_expansion_performance",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_content_suitability_placements(
    customer_id: str,
    placement_view: str = "GROUP",
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    placement_types: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists content-suitability placements by grouped or detailed view.

  Args:
      customer_id: Google Ads customer ID.
      placement_view: GROUP or DETAIL.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      placement_types: Optional placement type filters.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing content-suitability placement rows.
  """
  validate_limit(limit)
  normalized_view = _normalize_choice(
      placement_view,
      "placement_view",
      set(_CONTENT_SUITABILITY_VIEW_FIELDS),
  )
  from_resource, placement_fields = _CONTENT_SUITABILITY_VIEW_FIELDS[
      normalized_view
  ]

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )
  if placement_types:
    placement_types = _normalize_enum_filters(
        placement_types,
        "placement_types",
    )
    where_conditions.append(
        f"{from_resource}.placement_type IN "
        f"({quote_enum_values(placement_types)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        {", ".join(placement_fields)},
        metrics.impressions
      FROM {from_resource}
      {build_where_clause(where_conditions)}
      ORDER BY metrics.impressions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "content_suitability_placements",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["placement_view"] = normalized_view
  return result


@reporting_tool
def list_location_interest_performance(
    customer_id: str,
    interest_view: str = "LOCATION_INTEREST",
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists location-interest performance for geo optimization.

  Args:
      customer_id: Google Ads customer ID.
      interest_view: LOCATION_INTEREST or MATCHED_LOCATION_INTEREST.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing location-interest performance rows.
  """
  validate_limit(limit)
  normalized_view = _normalize_choice(
      interest_view,
      "interest_view",
      set(_LOCATION_INTEREST_VIEW_FIELDS),
  )
  from_resource, location_fields = _LOCATION_INTEREST_VIEW_FIELDS[
      normalized_view
  ]

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        {", ".join(location_fields)},
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM {from_resource}
      {build_where_clause(where_conditions)}
      ORDER BY metrics.conversions_value DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  result = build_paginated_list_response(
      "location_interest_performance",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
  result["interest_view"] = normalized_view
  return result


@reporting_tool
def summarize_shopping_product_status(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    row_limit: int = 5000,
    top_issue_products_limit: int = 25,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Summarizes Shopping product status and issues compactly.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      statuses: Optional Shopping product statuses such as ELIGIBLE,
          LIMITED, NOT_ELIGIBLE, or PENDING.
      date_range: GAQL date range used for product performance context.
      row_limit: Maximum Shopping product rows to analyze.
      top_issue_products_limit: Maximum issue-bearing products to return.
      login_customer_id: Optional manager account ID.

  Returns:
      A compact dict with status and issue distributions plus top products
      with issues ordered by recent cost.
  """
  validate_limit(row_limit)
  validate_limit(top_issue_products_limit)

  normalized_date_range = validate_date_range(date_range)
  where_conditions = [_date_range_condition(normalized_date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )
  if statuses:
    statuses = _normalize_enum_filters(statuses, "statuses")
    where_conditions.append(
        f"shopping_product.status IN ({quote_enum_values(statuses)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        shopping_product.item_id,
        shopping_product.title,
        shopping_product.brand,
        shopping_product.merchant_center_id,
        shopping_product.status,
        shopping_product.issues,
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM shopping_product
      {build_where_clause(where_conditions)}
      ORDER BY metrics.cost_micros DESC
      LIMIT {row_limit}
  """
  rows = run_gaql_query(query, customer_id, login_customer_id)
  status_counts: Counter[Any] = Counter()
  issue_type_counts: Counter[Any] = Counter()
  issue_severity_counts: Counter[Any] = Counter()
  issue_products = []

  for row in rows:
    status_counts[row.get("shopping_product.status")] += 1
    issues = row.get("shopping_product.issues") or []
    if not issues:
      continue
    for issue in issues:
      issue_type_counts[_issue_label(issue, "type")] += 1
      issue_severity_counts[_issue_label(issue, "severity")] += 1
    if len(issue_products) < top_issue_products_limit:
      issue_products.append(row)

  return {
      "date_range": normalized_date_range,
      "analyzed_row_count": len(rows),
      "row_limit": row_limit,
      "truncated": len(rows) >= row_limit,
      "status_distribution": _distribution(
          status_counts,
          "status",
          "product_count",
      ),
      "issue_type_distribution": _distribution(
          issue_type_counts,
          "issue_type",
          "issue_count",
      ),
      "issue_severity_distribution": _distribution(
          issue_severity_counts,
          "issue_severity",
          "issue_count",
      ),
      "top_issue_products": issue_products,
      "top_issue_products_limit": top_issue_products_limit,
      "campaign_context": get_campaign_context(
          customer_id,
          _campaign_ids_from_rows(rows),
          login_customer_id,
      ),
  }


@reporting_tool
def list_shopping_product_status(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists Shopping products with status, issues, and performance context.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      statuses: Optional Shopping product statuses such as ELIGIBLE,
          LIMITED, NOT_ELIGIBLE, or PENDING.
      date_range: GAQL date range used for product performance context.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing Shopping product health rows.
  """
  validate_limit(limit)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if ad_group_ids:
    where_conditions.append(
        f"ad_group.id IN ({quote_int_values(ad_group_ids)})"
    )
  if statuses:
    statuses = _normalize_enum_filters(statuses, "statuses")
    where_conditions.append(
        f"shopping_product.status IN ({quote_enum_values(statuses)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        shopping_product.item_id,
        shopping_product.title,
        shopping_product.brand,
        shopping_product.merchant_center_id,
        shopping_product.status,
        shopping_product.issues,
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
      FROM shopping_product
      {build_where_clause(where_conditions)}
      ORDER BY metrics.cost_micros DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "shopping_product_status",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_travel_feed_asset_sets(
    customer_id: str,
    statuses: list[str] | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists v24 travel feed asset sets and their linked feed IDs.

  Args:
      customer_id: Google Ads customer ID.
      statuses: Optional asset set statuses to filter to.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing travel feed asset set configuration rows.
  """
  validate_limit(limit)

  where_conditions = ["asset_set.type = TRAVEL_FEED"]
  if statuses:
    statuses = _normalize_enum_filters(statuses, "statuses")
    where_conditions.append(
        f"asset_set.status IN ({quote_enum_values(statuses)})"
    )

  query = f"""
      SELECT
        asset_set.resource_name,
        asset_set.id,
        asset_set.name,
        asset_set.status,
        asset_set.type,
        asset_set.travel_feed_data.travel_feed_vertical_type,
        asset_set.travel_feed_data.hotel_center_account_id,
        asset_set.travel_feed_data.merchant_center_id,
        asset_set.travel_feed_data.partner_center_id,
        asset_set.travel_feed_data.subset_id
      FROM asset_set
      {build_where_clause(where_conditions)}
      ORDER BY asset_set.name ASC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "travel_feed_asset_sets",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@reporting_tool
def list_retail_filter_shared_criteria(
    customer_id: str,
    shared_set_ids: list[str] | None = None,
    limit: int = 100,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists v24 tag-based retail filter shared criteria.

  Args:
      customer_id: Google Ads customer ID.
      shared_set_ids: Optional shared set IDs to filter to.
      limit: Maximum rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A paginated dict containing retail filter shared criteria rows.
  """
  validate_limit(limit)

  where_conditions = ["shared_set.type = RETAIL_FILTER"]
  if shared_set_ids:
    where_conditions.append(
        f"shared_set.id IN ({quote_int_values(shared_set_ids)})"
    )

  query = f"""
      SELECT
        shared_set.resource_name,
        shared_set.id,
        shared_set.name,
        shared_set.status,
        shared_set.type,
        shared_criterion.resource_name,
        shared_criterion.criterion_id,
        shared_criterion.type,
        shared_criterion.retail_filter.expression.name,
        shared_criterion.retail_filter.tag.expression_name,
        shared_criterion.retail_filter.tag.value
      FROM shared_criterion
      {build_where_clause(where_conditions)}
      ORDER BY shared_set.name ASC, shared_criterion.criterion_id ASC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "retail_filter_shared_criteria",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
