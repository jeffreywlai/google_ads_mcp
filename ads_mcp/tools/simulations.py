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

"""Read tools for campaign, ad group, and keyword simulations."""

from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._gaql import build_where_clause
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import quote_int_value
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import run_gaql_query_page


_CAMPAIGN_SIMULATION_FIELDS = {
    "BUDGET": ["campaign_simulation.budget_point_list.points"],
    "CPC_BID": ["campaign_simulation.cpc_bid_point_list.points"],
    "TARGET_CPA": ["campaign_simulation.target_cpa_point_list.points"],
    "TARGET_IMPRESSION_SHARE": [
        "campaign_simulation.target_impression_share_point_list.points"
    ],
    "TARGET_ROAS": ["campaign_simulation.target_roas_point_list.points"],
}

_AD_GROUP_SIMULATION_FIELDS = {
    "CPC_BID": ["ad_group_simulation.cpc_bid_point_list.points"],
    "CPV_BID": ["ad_group_simulation.cpv_bid_point_list.points"],
    "TARGET_CPA": ["ad_group_simulation.target_cpa_point_list.points"],
    "TARGET_ROAS": ["ad_group_simulation.target_roas_point_list.points"],
}


def _selected_point_fields(
    allowed_fields: dict[str, list[str]],
    simulation_type: str | None,
) -> list[str]:
  if simulation_type is None:
    return []
  if not isinstance(simulation_type, str) or not simulation_type.strip():
    raise ToolError("simulation_type must be a non-empty string.")

  normalized_type = simulation_type.strip().upper()
  if normalized_type not in allowed_fields:
    raise ToolError(
        "Unsupported simulation_type. Use one of: "
        + ", ".join(sorted(allowed_fields))
    )
  return allowed_fields[normalized_type]


simulation_tool = ads_read_tool(mcp, tags={"simulations"})


@simulation_tool
def list_campaign_simulations(
    customer_id: str,
    campaign_ids: list[str] | str | None = None,
    simulation_type: str | None = None,
    limit: int = 25,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists campaign_simulation rows and their point lists.

  Args:
      customer_id: Google Ads customer ID.
      campaign_ids: Optional campaign IDs to filter to.
      simulation_type: Optional type such as BUDGET or CPC_BID.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing campaign simulation rows.
  """
  validate_limit(limit)

  selected_fields = _selected_point_fields(
      _CAMPAIGN_SIMULATION_FIELDS, simulation_type
  )
  where_conditions = []
  campaign_ids = normalize_list_arg(campaign_ids, "campaign_ids")
  if campaign_ids:
    campaign_id_values = quote_int_values(campaign_ids, "campaign_ids")
    where_conditions.append(f"campaign.id IN ({campaign_id_values})")
  if simulation_type:
    where_conditions.append(
        f"campaign_simulation.type = {simulation_type.strip().upper()}"
    )

  select_fields = [
      "campaign.id",
      "campaign.name",
      "campaign_simulation.start_date",
      "campaign_simulation.end_date",
      "campaign_simulation.modification_method",
      "campaign_simulation.type",
  ]
  select_fields.extend(selected_fields)

  query = f"""
      SELECT
        {", ".join(select_fields)}
      FROM campaign_simulation
      {build_where_clause(where_conditions)}
      ORDER BY campaign_simulation.end_date DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "campaign_simulations",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@simulation_tool
def list_ad_group_simulations(
    customer_id: str,
    ad_group_ids: list[str] | str | None = None,
    simulation_type: str | None = None,
    limit: int = 25,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists ad_group_simulation rows and their point lists.

  Args:
      customer_id: Google Ads customer ID.
      ad_group_ids: Optional ad group IDs to filter to.
      simulation_type: Optional type such as CPC_BID or TARGET_ROAS.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing ad group simulation rows.
  """
  validate_limit(limit)

  selected_fields = _selected_point_fields(
      _AD_GROUP_SIMULATION_FIELDS, simulation_type
  )
  where_conditions = []
  ad_group_ids = normalize_list_arg(ad_group_ids, "ad_group_ids")
  if ad_group_ids:
    ad_group_id_values = quote_int_values(ad_group_ids, "ad_group_ids")
    where_conditions.append(f"ad_group.id IN ({ad_group_id_values})")
  if simulation_type:
    where_conditions.append(
        f"ad_group_simulation.type = {simulation_type.strip().upper()}"
    )

  select_fields = [
      "campaign.id",
      "campaign.name",
      "ad_group.id",
      "ad_group.name",
      "ad_group_simulation.start_date",
      "ad_group_simulation.end_date",
      "ad_group_simulation.modification_method",
      "ad_group_simulation.type",
  ]
  select_fields.extend(selected_fields)

  query = f"""
      SELECT
        {", ".join(select_fields)}
      FROM ad_group_simulation
      {build_where_clause(where_conditions)}
      ORDER BY ad_group_simulation.end_date DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "ad_group_simulations",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )


@simulation_tool
def list_ad_group_criterion_simulations(
    customer_id: str,
    ad_group_id: str | None = None,
    criterion_ids: list[str] | str | None = None,
    limit: int = 25,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists ad_group_criterion_simulation CPC bid point lists.

  Args:
      customer_id: Google Ads customer ID.
      ad_group_id: Optional ad group ID filter.
      criterion_ids: Optional criterion IDs to filter to.
      limit: Maximum number of rows to return.
      page_token: Token for the next page of results.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict containing keyword-level simulation rows.
  """
  validate_limit(limit)

  where_conditions = []
  if ad_group_id:
    ad_group_id_filter = quote_int_value(ad_group_id, "ad_group_id")
    where_conditions.append(f"ad_group.id = {ad_group_id_filter}")
  criterion_ids = normalize_list_arg(criterion_ids, "criterion_ids")
  if criterion_ids:
    criterion_id_values = quote_int_values(criterion_ids, "criterion_ids")
    where_conditions.append(
        "ad_group_criterion.criterion_id IN " f"({criterion_id_values})"
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
        ad_group_criterion_simulation.start_date,
        ad_group_criterion_simulation.end_date,
        ad_group_criterion_simulation.modification_method,
        ad_group_criterion_simulation.type,
        ad_group_criterion_simulation.cpc_bid_point_list.points
      FROM ad_group_criterion_simulation
      {build_where_clause(where_conditions)}
      ORDER BY ad_group_criterion_simulation.end_date DESC
  """
  page = run_gaql_query_page(
      query=query,
      customer_id=customer_id,
      page_size=limit,
      page_token=page_token,
      login_customer_id=login_customer_id,
  )
  return build_paginated_list_response(
      "ad_group_criterion_simulations",
      page["rows"],
      total_count=page["total_results_count"],
      page_size=limit,
      next_page_token=page["next_page_token"],
  )
