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

"""Tests for _campaign_context.py."""

from collections import OrderedDict
from unittest import mock

from ads_mcp.tools import _campaign_context


def setup_function():
  _campaign_context._CAMPAIGN_CONTEXT_CACHE = OrderedDict()  # pylint: disable=protected-access


def teardown_function():
  _campaign_context._CAMPAIGN_CONTEXT_CACHE = OrderedDict()  # pylint: disable=protected-access


def test_get_campaign_context_reuses_cached_rows():
  status_rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "campaign.status": "ENABLED",
      }
  ]
  spend_rows = [
      {
          "campaign.id": "111",
          "metrics.cost_micros": 123456,
      }
  ]

  with mock.patch(
      "ads_mcp.tools._campaign_context.run_gaql_query",
      side_effect=[status_rows, spend_rows],
  ) as mock_query:
    first = _campaign_context.get_campaign_context("123", ["111"])
    second = _campaign_context.get_campaign_context("123", ["111"])

  assert mock_query.call_count == 2
  assert first == second


def test_get_campaign_context_returns_copied_cached_values():
  status_rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Brand",
          "campaign.status": "ENABLED",
      }
  ]
  spend_rows = [
      {
          "campaign.id": "111",
          "metrics.cost_micros": 123456,
      }
  ]

  with mock.patch(
      "ads_mcp.tools._campaign_context.run_gaql_query",
      side_effect=[status_rows, spend_rows],
  ):
    first = _campaign_context.get_campaign_context("123", ["111"])
    first["111"]["campaign.name"] = "Changed"
    second = _campaign_context.get_campaign_context("123", ["111"])

  assert second["111"]["campaign.name"] == "Brand"
