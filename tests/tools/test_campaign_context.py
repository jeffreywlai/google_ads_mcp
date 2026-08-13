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
from fastmcp.exceptions import ToolError
import pytest


@pytest.fixture(autouse=True)
def credential_scope():
  with mock.patch(
      "ads_mcp.tools._campaign_context.get_ads_credential_cache_scope",
      return_value="test-credentials",
  ):
    yield


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


def test_campaign_context_cache_key_canonicalizes_date_range_dicts():
  first_key = _campaign_context._campaign_context_cache_key(  # pylint: disable=protected-access
      "test-credentials",
      "123",
      ["222", "111"],
      None,
      {"start_date": "2026-04-01", "end_date": "2026-04-30"},
  )
  second_key = _campaign_context._campaign_context_cache_key(  # pylint: disable=protected-access
      "test-credentials",
      "123",
      ["111", "222"],
      None,
      {"end_date": "2026-04-30", "start_date": "2026-04-01"},
  )

  assert first_key == second_key


def test_campaign_context_cache_isolated_by_credential_scope():
  status_rows = [
      {
          "campaign.id": "111",
          "campaign.name": "Principal A",
          "campaign.status": "ENABLED",
      },
      {
          "campaign.id": "111",
          "campaign.name": "Principal B",
          "campaign.status": "PAUSED",
      },
  ]
  spend_rows = [
      {"campaign.id": "111", "metrics.cost_micros": 1},
      {"campaign.id": "111", "metrics.cost_micros": 2},
  ]
  with (
      mock.patch(
          "ads_mcp.tools._campaign_context.get_ads_credential_cache_scope",
          side_effect=["principal-a", "principal-b"],
      ),
      mock.patch(
          "ads_mcp.tools._campaign_context.run_gaql_query",
          side_effect=[
              status_rows[0:1],
              spend_rows[0:1],
              status_rows[1:2],
              spend_rows[1:2],
          ],
      ) as mock_query,
  ):
    principal_a = _campaign_context.get_campaign_context("123", ["111"])
    principal_b = _campaign_context.get_campaign_context("123", ["111"])

  assert mock_query.call_count == 4
  assert principal_a["111"]["campaign.name"] == "Principal A"
  assert principal_b["111"]["campaign.name"] == "Principal B"


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


def test_get_campaign_context_rejects_invalid_spend_date_range():
  with pytest.raises(ToolError, match="Invalid date_range"):
    _campaign_context.get_campaign_context(
        "123",
        ["111"],
        spend_date_range="LAST_30_DAYS OR campaign.id > 0",
    )
