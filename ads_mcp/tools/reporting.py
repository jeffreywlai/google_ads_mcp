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

from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import run_gaql_query


def _date_range_condition(date_range: str) -> str:
  return f"segments.date DURING {date_range}"


def _normalize_choice(
    value: str,
    field_name: str,
    allowed_values: set[str],
) -> str:
  normalized_value = value.upper()
  if normalized_value not in allowed_values:
    raise ToolError(
        f"Invalid {field_name}: {value}. "
        f"Use one of: {', '.join(sorted(allowed_values))}."
    )
  return normalized_value


def _validate_quality_score(min_quality_score: int | None) -> None:
  if min_quality_score is None:
    return
  if min_quality_score < 1 or min_quality_score > 10:
    raise ToolError("min_quality_score must be between 1 and 10.")


reporting_tool = ads_read_tool(mcp, tags={"reporting"})


def _limit_clause(limit: int | None) -> str:
  if limit is None:
    return ""
  validate_limit(limit)
  return f"\n      LIMIT {limit}"


@reporting_tool
def list_device_performance(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign performance segmented by device.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum number of rows to return.
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
      LIMIT {limit}
  """

  return {
      "device_performance": run_gaql_query(
          query, customer_id, login_customer_id
      )
  }


@reporting_tool
def list_geographic_performance(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    location_view: str = "USER_LOCATION",
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign performance segmented by geography.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      location_view: USER_LOCATION or GEOGRAPHIC.
      limit: Maximum number of rows to return.
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
      LIMIT {limit}
  """

  return {
      "location_view": normalized_view,
      "geographic_performance": run_gaql_query(
          query, customer_id, login_customer_id
      ),
  }


@reporting_tool
def list_impression_share(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    enabled_only: bool = True,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign impression share metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      enabled_only: Whether to include only ENABLED campaigns.
      limit: Maximum number of rows to return.
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
      LIMIT {limit}
  """

  return {
      "impression_share": run_gaql_query(query, customer_id, login_customer_id)
  }


@reporting_tool
def list_keyword_quality_scores(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    min_quality_score: int | None = None,
    limit: int | None = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists keyword quality score diagnostics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      min_quality_score: Optional minimum quality score from 1 to 10.
      limit: Maximum number of rows to return. Set to None to omit the
          GAQL LIMIT clause.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing keyword quality score rows.
  """
  _validate_quality_score(min_quality_score)

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

  query = f"""
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
      ORDER BY ad_group_criterion.quality_info.quality_score ASC
  """ + _limit_clause(
      limit
  )

  return {
      "keyword_quality_scores": run_gaql_query(
          query, customer_id, login_customer_id
      )
  }


@reporting_tool
def list_rsa_ad_strength(
    customer_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists RSA ad strength diagnostics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      ad_group_ids: Optional ad group IDs to filter to.
      date_range: GAQL date range such as LAST_30_DAYS.
      limit: Maximum number of rows to return.
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
      LIMIT {limit}
  """

  return {
      "rsa_ad_strength": run_gaql_query(query, customer_id, login_customer_id)
  }


@reporting_tool
def list_conversion_actions(
    customer_id: str,
    statuses: list[str] | None = None,
    types: list[str] | None = None,
    limit: int = 100,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists conversion action configuration.

  Args:
      customer_id: Google Ads customer ID.
      statuses: Optional conversion action statuses to filter to.
      types: Optional conversion action types to filter to.
      limit: Maximum number of rows to return.
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
      LIMIT {limit}
  """

  return {
      "conversion_actions": run_gaql_query(
          query, customer_id, login_customer_id
      )
  }


@reporting_tool
def list_audience_performance(
    customer_id: str,
    scope: str = "CAMPAIGN",
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 100,
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
      LIMIT {limit}
  """

  return {
      "scope": normalized_scope,
      "audience_performance": run_gaql_query(
          query, customer_id, login_customer_id
      ),
  }
