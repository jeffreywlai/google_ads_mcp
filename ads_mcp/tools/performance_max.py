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

"""Performance Max diagnostic read tools."""

from typing import Any

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import run_gaql_query_page


def _date_range_condition(date_range: str) -> str:
  return f"segments.date DURING {date_range}"


performance_max_tool = ads_read_tool(mcp, tags={"performance_max"})


def _merge_ids(
    single_id: str | None,
    multiple_ids: list[str] | None,
) -> list[str] | None:
  if single_id:
    if multiple_ids:
      return [single_id, *multiple_ids]
    return [single_id]
  return multiple_ids


@performance_max_tool
def list_asset_group_assets(
    customer_id: str,
    campaign_id: str | None = None,
    campaign_ids: list[str] | None = None,
    asset_group_id: str | None = None,
    asset_group_ids: list[str] | None = None,
    date_range: str | None = None,
    limit: int = 50,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists Performance Max asset links and their serving diagnostics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Optional single campaign ID filter.
      campaign_ids: Optional campaign IDs to filter to.
      asset_group_id: Optional single asset group ID filter.
      asset_group_ids: Optional asset group IDs to filter to.
      date_range: Optional GAQL date range used for conversion metrics.
          Leave unset to list linked assets regardless of recent activity.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing asset group asset diagnostics.
  """
  validate_limit(limit)
  campaign_ids = _merge_ids(campaign_id, campaign_ids)
  asset_group_ids = _merge_ids(asset_group_id, asset_group_ids)

  where_conditions = ["asset_group_asset.status != REMOVED"]
  if date_range:
    where_conditions.append(_date_range_condition(date_range))
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if asset_group_ids:
    where_conditions.append(
        f"asset_group.id IN ({quote_int_values(asset_group_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        asset_group.id,
        asset_group.name,
        asset.id,
        asset.name,
        asset.type,
        asset_group_asset.asset,
        asset_group_asset.field_type,
        asset_group_asset.primary_status,
        asset_group_asset.primary_status_details,
        asset_group_asset.status,
        metrics.conversions,
        metrics.conversions_value,
        metrics.value_per_conversion
      FROM asset_group_asset
      {build_where_clause(where_conditions)}
      ORDER BY metrics.conversions DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "asset_group_assets",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@performance_max_tool
def list_asset_group_top_combinations(
    customer_id: str,
    campaign_id: str | None = None,
    campaign_ids: list[str] | None = None,
    asset_group_id: str | None = None,
    asset_group_ids: list[str] | None = None,
    limit: int = 25,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists top served asset combinations for Performance Max.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Optional single campaign ID filter.
      campaign_ids: Optional campaign IDs to filter to.
      asset_group_id: Optional single asset group ID filter.
      asset_group_ids: Optional asset group IDs to filter to.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing top asset combination rows.
  """
  validate_limit(limit)
  campaign_ids = _merge_ids(campaign_id, campaign_ids)
  asset_group_ids = _merge_ids(asset_group_id, asset_group_ids)

  where_conditions = []
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if asset_group_ids:
    where_conditions.append(
        f"asset_group.id IN ({quote_int_values(asset_group_ids)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        asset_group.id,
        asset_group.name,
        asset_group_top_combination_view.resource_name,
        asset_group_top_combination_view.asset_group_top_combinations
      FROM asset_group_top_combination_view
      {build_where_clause(where_conditions)}
      ORDER BY asset_group.id
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "asset_group_top_combinations",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@performance_max_tool
def list_performance_max_placements(
    customer_id: str,
    campaign_id: str | None = None,
    campaign_ids: list[str] | None = None,
    placement_types: list[str] | None = None,
    date_range: str = "LAST_30_DAYS",
    limit: int = 50,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists Performance Max placements with impression metrics.

  Args:
      customer_id: Google Ads customer ID.
      campaign_id: Optional single campaign ID filter.
      campaign_ids: Optional campaign IDs to filter to.
      placement_types: Optional types such as WEBSITE or YOUTUBE_VIDEO.
      date_range: GAQL date range used for impression metrics.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing Performance Max placement rows.
  """
  validate_limit(limit)
  campaign_ids = _merge_ids(campaign_id, campaign_ids)

  where_conditions = [_date_range_condition(date_range)]
  if campaign_ids:
    where_conditions.append(
        f"campaign.id IN ({quote_int_values(campaign_ids)})"
    )
  if placement_types:
    where_conditions.append(
        "performance_max_placement_view.placement_type IN "
        f"({quote_enum_values(placement_types)})"
    )

  query = f"""
      SELECT
        campaign.id,
        campaign.name,
        performance_max_placement_view.display_name,
        performance_max_placement_view.placement,
        performance_max_placement_view.placement_type,
        performance_max_placement_view.target_url,
        metrics.impressions
      FROM performance_max_placement_view
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
      "performance_max_placements",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
