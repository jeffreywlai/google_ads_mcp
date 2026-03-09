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

"""Tests for simulations.py."""

from unittest import mock

from ads_mcp.tools import simulations


CUSTOMER_ID = "1234567890"


def test_list_campaign_simulations_uses_type_specific_fields():
  with mock.patch(
      "ads_mcp.tools.simulations.run_gaql_query",
      return_value=[],
  ) as mock_query:
    simulations.list_campaign_simulations(
        CUSTOMER_ID,
        campaign_ids=["111"],
        simulation_type="budget",
    )

  query = mock_query.call_args.args[0]
  assert "FROM campaign_simulation" in query
  assert "campaign.id IN (111)" in query
  assert "campaign_simulation.type = BUDGET" in query
  assert "campaign_simulation.budget_point_list.points" in query


def test_list_ad_group_simulations_uses_type_specific_fields():
  with mock.patch(
      "ads_mcp.tools.simulations.run_gaql_query",
      return_value=[],
  ) as mock_query:
    simulations.list_ad_group_simulations(
        CUSTOMER_ID,
        ad_group_ids=["222"],
        simulation_type="cpc_bid",
    )

  query = mock_query.call_args.args[0]
  assert "FROM ad_group_simulation" in query
  assert "ad_group.id IN (222)" in query
  assert "ad_group_simulation.cpc_bid_point_list.points" in query


def test_list_campaign_simulations_without_type_stays_lightweight():
  with mock.patch(
      "ads_mcp.tools.simulations.run_gaql_query",
      return_value=[],
  ) as mock_query:
    simulations.list_campaign_simulations(CUSTOMER_ID)

  query = mock_query.call_args.args[0]
  assert "FROM campaign_simulation" in query
  assert "budget_point_list.points" not in query
  assert "cpc_bid_point_list.points" not in query


def test_list_ad_group_criterion_simulations_builds_query():
  with mock.patch(
      "ads_mcp.tools.simulations.run_gaql_query",
      return_value=[],
  ) as mock_query:
    simulations.list_ad_group_criterion_simulations(
        CUSTOMER_ID,
        ad_group_id="333",
        criterion_ids=["444", "555"],
    )

  query = mock_query.call_args.args[0]
  assert "FROM ad_group_criterion_simulation" in query
  assert "ad_group.id = 333" in query
  assert "ad_group_criterion.criterion_id IN (444, 555)" in query
  assert "ad_group_criterion_simulation.cpc_bid_point_list.points" in query
