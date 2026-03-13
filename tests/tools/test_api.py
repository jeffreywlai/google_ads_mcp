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

"""Tests for the API tools."""

from unittest import mock

from ads_mcp.tools import api
from google.protobuf.field_mask_pb2 import FieldMask
import proto
import pytest


@pytest.fixture(autouse=True)
def reset_ads_client():
  """Resets the cached GoogleAdsClient instance before each test."""
  api._ADS_CLIENT = None  # pylint: disable=protected-access
  api._ADS_CONFIG_CACHE = {}  # pylint: disable=protected-access
  yield
  api._ADS_CLIENT = None  # pylint: disable=protected-access
  api._ADS_CONFIG_CACHE = {}  # pylint: disable=protected-access


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "SELECT campaign.id FROM campaign",
            (
                "SELECT campaign.id FROM campaign PARAMETERS"
                " omit_unselected_resource_names=true"
            ),
        ),
        (
            "SELECT campaign.id FROM campaign PARAMETERS include_drafts=true",
            (
                "SELECT campaign.id FROM campaign PARAMETERS"
                " include_drafts=true, omit_unselected_resource_names=true"
            ),
        ),
        (
            (
                "SELECT campaign.id FROM campaign PARAMETERS"
                " omit_unselected_resource_names=true"
            ),
            (
                "SELECT campaign.id FROM campaign PARAMETERS"
                " omit_unselected_resource_names=true"
            ),
        ),
    ],
)
def test_preprocess_gaql(query, expected):
  """Tests the preprocess_gaql function."""
  assert api.preprocess_gaql(query) == expected


def test_format_value():
  """Tests the format_value function."""
  # Test with a proto.Message
  mock_message = mock.Mock(spec=proto.Message)
  with mock.patch.object(
      proto.Message, "to_json", return_value='{"key": "value"}'
  ):
    assert api.format_value(mock_message) == {"key": "value"}

  # Test with a proto.Enum
  mock_enum = mock.Mock(spec=proto.Enum)
  mock_enum.name = "ENUM_VALUE"
  assert api.format_value(mock_enum) == "ENUM_VALUE"

  # Test with a google.protobuf Message
  assert api.format_value(FieldMask(paths=["campaign.status"])) == {
      "paths": ["campaign.status"]
  }

  # Test with a simple type
  assert api.format_value("string") == "string"
  assert api.format_value(123) == 123


@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_list_accessible_accounts(mock_google_ads_client, _):
  """Tests the list_accessible_accounts function."""
  mock_client_instance = mock_google_ads_client.load_from_storage.return_value
  mock_service = mock_client_instance.get_service.return_value
  mock_service.list_accessible_customers.return_value.resource_names = [
      "customers/123",
      "customers/456",
  ]
  assert api.list_accessible_accounts() == ["123", "456"]


@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_execute_gaql(mock_google_ads_client, _):
  """Tests the execute_gaql function."""
  mock_client_instance = mock_google_ads_client.load_from_storage.return_value
  mock_ads_service = mock_client_instance.get_service.return_value
  mock_ads_service.search_stream.return_value = [
      mock.Mock(
          results=[mock.Mock()], field_mask=mock.Mock(paths=["campaign.id"])
      )
  ]
  with mock.patch("ads_mcp.tools.api.get_nested_attr", return_value="123"):
    assert api.execute_gaql("SELECT campaign.id FROM campaign", "123") == {
        "data": [{"campaign.id": "123"}]
    }


def test_execute_gaql_applies_max_rows_and_returns_metadata():
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    assert api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        max_rows=2,
    ) == {
        "data": [{"campaign.id": "1"}, {"campaign.id": "2"}],
        "returned_row_count": 2,
        "total_row_count": 3,
        "truncated": True,
        "max_rows_applied": 2,
    }


def test_execute_gaql_rejects_non_positive_max_rows():
  with pytest.raises(api.ToolError, match="max_rows must be greater than 0"):
    api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        max_rows=0,
    )


def test_run_gaql_query_page_returns_rows_and_metadata():
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[
          {"campaign.id": "1"},
          {"campaign.id": "2"},
          {"campaign.id": "3"},
      ],
  ):
    result = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=2,
        page_token="1",
    )

  assert result == {
      "rows": [{"campaign.id": "2"}, {"campaign.id": "3"}],
      "next_page_token": None,
      "total_results_count": 3,
  }


def test_run_gaql_query_page_rejects_invalid_page_token():
  with pytest.raises(api.ToolError, match="Invalid page_token"):
    api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=2,
        page_token="bad-token",
    )


def test_build_paginated_list_response_returns_completeness_metadata():
  assert api.build_paginated_list_response(
      "campaigns",
      rows=[{"campaign.id": "1"}, {"campaign.id": "2"}],
      total_count=5,
      page_size=2,
      next_page_token="2",
  ) == {
      "campaigns": [
          {"campaign.id": "1"},
          {"campaign.id": "2"},
      ],
      "returned_count": 2,
      "total_count": 5,
      "total_page_count": 3,
      "truncated": True,
      "next_page_token": "2",
      "page_size": 2,
  }


@mock.patch("ads_mcp.tools.api.Credentials")
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
@mock.patch("ads_mcp.tools.api.get_access_token")
@mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
def test_get_ads_client_caches_yaml_config_for_access_token(
    _mock_isfile,
    _mock_getmtime,
    mock_get_access_token,
    mock_google_ads_client,
    mock_credentials,
):
  mock_get_access_token.return_value = mock.Mock(token="access-token")
  mock_credentials.return_value = mock.Mock()

  with mock.patch(
      "builtins.open",
      new_callable=mock.mock_open,
      read_data="developer_token: dev-token\n",
  ) as mock_file:
    api.get_ads_client()
    api.get_ads_client()

  assert mock_file.call_count == 1
  assert mock_google_ads_client.call_count == 2
