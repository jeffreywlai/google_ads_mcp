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

"""Tests for performance_max.py."""

from unittest import mock

from ads_mcp.tools import performance_max


CUSTOMER_ID = "1234567890"


def test_list_asset_group_assets_builds_query():
  with mock.patch(
      "ads_mcp.tools.performance_max.run_gaql_query",
      return_value=[],
  ) as mock_query:
    performance_max.list_asset_group_assets(
        CUSTOMER_ID,
        campaign_ids=["111"],
        asset_group_ids=["222"],
    )

  query = mock_query.call_args.args[0]
  assert "FROM asset_group_asset" in query
  assert "campaign.id IN (111)" in query
  assert "asset_group.id IN (222)" in query
  assert "asset_group_asset.performance_label" in query


def test_list_asset_group_top_combinations_builds_query():
  with mock.patch(
      "ads_mcp.tools.performance_max.run_gaql_query",
      return_value=[],
  ) as mock_query:
    performance_max.list_asset_group_top_combinations(
        CUSTOMER_ID, asset_group_ids=["222"]
    )

  query = mock_query.call_args.args[0]
  assert "FROM asset_group_top_combination_view" in query
  assert "asset_group.id IN (222)" in query
  assert (
      "asset_group_top_combination_view.asset_group_top_combinations" in query
  )


def test_list_performance_max_placements_builds_query():
  with mock.patch(
      "ads_mcp.tools.performance_max.run_gaql_query",
      return_value=[],
  ) as mock_query:
    performance_max.list_performance_max_placements(
        CUSTOMER_ID,
        campaign_ids=["111"],
        placement_types=["website"],
    )

  query = mock_query.call_args.args[0]
  assert "FROM performance_max_placement_view" in query
  assert "campaign.id IN (111)" in query
  assert "placement_type IN (WEBSITE)" in query
  assert "metrics.impressions" in query
