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

# pylint: disable=protected-access

from unittest import mock
import os

from ads_mcp.tools import api
from google.ads.googleads.errors import GoogleAdsException
from google.protobuf.field_mask_pb2 import FieldMask
import proto
import pytest


@pytest.fixture(autouse=True)
def reset_ads_client():
  """Resets the cached GoogleAdsClient instance before each test."""
  api._ADS_CLIENT = None  # pylint: disable=protected-access
  api._ADS_CONFIG_CACHE = {}  # pylint: disable=protected-access
  api._PAGED_QUERY_CACHE = api.OrderedDict()  # pylint: disable=protected-access
  api._package_ads_assistant.cache_clear()  # pylint: disable=protected-access
  yield
  api._ADS_CLIENT = None  # pylint: disable=protected-access
  api._ADS_CONFIG_CACHE = {}  # pylint: disable=protected-access
  api._PAGED_QUERY_CACHE = api.OrderedDict()  # pylint: disable=protected-access
  api._package_ads_assistant.cache_clear()  # pylint: disable=protected-access


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
        (
            "SELECT campaign.id FROM campaign;",
            (
                "SELECT campaign.id FROM campaign PARAMETERS"
                " omit_unselected_resource_names=true"
            ),
        ),
        (
            "SELECT campaign.id FROM campaign PARAMETERS include_drafts=true;",
            (
                "SELECT campaign.id FROM campaign PARAMETERS"
                " include_drafts=true, omit_unselected_resource_names=true"
            ),
        ),
    ],
)
def test_preprocess_gaql(query, expected):
  """Tests the preprocess_gaql function."""
  assert api.preprocess_gaql(query) == expected


def test_preprocess_gaql_rewrites_unsupported_during_range(mocker):
  mocker.patch(
      "ads_mcp.tools._gaql._literal_date_bounds",
      return_value=(
          __import__("datetime").date(2026, 1, 23),
          __import__("datetime").date(2026, 4, 22),
      ),
  )

  result = api.preprocess_gaql(
      "SELECT campaign.id FROM campaign "
      "WHERE segments.date DURING LAST_90_DAYS"
  )

  assert "segments.date BETWEEN '2026-01-23' AND '2026-04-22'" in result


def test_preprocess_gaql_preserves_during_text_inside_string_literals():
  query = (
      "SELECT campaign.id FROM campaign "
      "WHERE campaign.name LIKE '%segments.date DURING LAST_90_DAYS%'"
  )

  result = api.preprocess_gaql(query)

  assert "'%segments.date DURING LAST_90_DAYS%'" in result
  assert "segments.date BETWEEN" not in result
  assert result.endswith("PARAMETERS omit_unselected_resource_names=true")


def test_preprocess_gaql_rejects_group_by():
  with pytest.raises(api.ToolError, match="GAQL does not support aggregate"):
    api.preprocess_gaql(
        "SELECT recommendation.type, COUNT(*) FROM recommendation "
        "GROUP BY recommendation.type"
    )


def test_preprocess_gaql_rejects_or_conditions():
  with pytest.raises(api.ToolError, match="not OR"):
    api.preprocess_gaql(
        "SELECT campaign.id FROM campaign "
        "WHERE campaign.name LIKE '%A%' OR campaign.name LIKE '%B%'"
    )


def test_preprocess_gaql_allows_or_enum_literal():
  result = api.preprocess_gaql(
      "SELECT user_list.resource_name FROM user_list "
      "WHERE user_list.rule_based_user_list.flexible_rule_user_list"
      ".inclusive_rule_operator = or"
  )

  assert "inclusive_rule_operator = OR" in result
  assert result.startswith("SELECT user_list.resource_name FROM user_list")


def test_preprocess_gaql_allows_or_enum_in_list_literal():
  result = api.preprocess_gaql(
      "SELECT user_list.resource_name FROM user_list "
      "WHERE user_list.rule_based_user_list.flexible_rule_user_list"
      ".inclusive_rule_operator IN (or, and)"
  )

  assert "inclusive_rule_operator IN (OR, AND)" in result


def test_preprocess_gaql_does_not_add_regular_filter_field_to_select():
  result = api.preprocess_gaql(
      "SELECT campaign.name FROM campaign WHERE campaign.status = ENABLED"
  )

  assert "campaign.name FROM campaign" in result
  assert "campaign.name, campaign.status" not in result


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


def _google_ads_exception(*messages):
  errors = []
  for message in messages:
    error = mock.Mock()
    error.__str__ = lambda self, message=message: message
    errors.append(error)
  return GoogleAdsException(
      error=mock.Mock(),
      failure=mock.Mock(errors=errors),
      call=mock.Mock(),
      request_id="test",
  )


@mock.patch("ads_mcp.tools.api._load_ads_config", return_value={})
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_list_accessible_accounts(mock_google_ads_client, _, mock_load_config):
  """Tests the list_accessible_accounts function."""
  del mock_load_config
  mock_client_instance = mock_google_ads_client.load_from_dict.return_value
  mock_service = mock_client_instance.get_service.return_value
  mock_service.list_accessible_customers.return_value.resource_names = [
      "customers/123",
      "customers/456",
  ]
  assert api.list_accessible_accounts() == ["123", "456"]


@mock.patch("ads_mcp.tools.api._load_ads_config", return_value={})
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_execute_gaql(mock_google_ads_client, _, mock_load_config):
  """Tests the execute_gaql function."""
  del mock_load_config
  mock_client_instance = mock_google_ads_client.load_from_dict.return_value
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


def test_execute_gaql_accepts_max_results_alias():
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    assert api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        max_results=1,
    ) == {
        "data": [{"campaign.id": "1"}],
        "returned_row_count": 1,
        "total_row_count": 2,
        "truncated": True,
        "max_rows_applied": 1,
    }


def test_execute_gaql_warns_on_large_unbounded_result():
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    result = api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        warning_row_threshold=2,
    )

  assert result["data"] == rows
  assert result["returned_row_count"] == 3
  assert result["total_row_count"] == 3
  assert result["truncated"] is False
  assert result["warning_row_threshold"] == 2
  assert "max_rows" in result["token_efficiency_warning"]
  assert "export_gaql_csv" in result["token_efficiency_warning"]


def test_execute_gaql_does_not_warn_at_threshold():
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    assert api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        warning_row_threshold=2,
    ) == {"data": rows}


def test_execute_gaql_warning_threshold_can_be_disabled():
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    assert api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        warning_row_threshold=None,
    ) == {"data": rows}


def test_execute_gaql_rejects_non_positive_warning_threshold():
  with pytest.raises(
      api.ToolError,
      match="warning_row_threshold must be greater than 0",
  ):
    api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        warning_row_threshold=0,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_rows": True}, "max_rows must be an integer"),
        ({"max_rows": 1.5}, "max_rows must be an integer"),
        ({"max_results": True}, "max_results must be an integer"),
        ({"max_results": 1.5}, "max_results must be an integer"),
        (
            {"warning_row_threshold": True},
            "warning_row_threshold must be an integer",
        ),
        (
            {"warning_row_threshold": 1.5},
            "warning_row_threshold must be an integer",
        ),
    ],
)
def test_execute_gaql_rejects_non_integer_row_caps(kwargs, message):
  with pytest.raises(api.ToolError, match=message):
    api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        **kwargs,
    )


def test_execute_gaql_max_rows_suppresses_unbounded_warning():
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    result = api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        max_rows=2,
        warning_row_threshold=1,
    )

  assert "token_efficiency_warning" not in result
  assert result == {
      "data": [{"campaign.id": "1"}, {"campaign.id": "2"}],
      "returned_row_count": 2,
      "total_row_count": 3,
      "truncated": True,
      "max_rows_applied": 2,
  }


def test_execute_gaql_rejects_conflicting_row_caps():
  with pytest.raises(api.ToolError, match="Use only one"):
    api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        max_rows=1,
        max_results=2,
    )


def test_execute_gaql_rejects_non_positive_max_rows():
  with pytest.raises(api.ToolError, match="max_rows must be greater than 0"):
    api.execute_gaql(
        "SELECT campaign.id FROM campaign",
        "123",
        max_rows=0,
    )


def test_run_gaql_query_retries_structured_transient_google_ads_error():
  mock_client = mock.Mock()
  mock_service = mock_client.get_service.return_value
  transient_error = _google_ads_exception("internal_error: INTERNAL_ERROR")
  mock_service.search_stream.side_effect = [
      transient_error,
      [
          mock.Mock(
              results=[mock.Mock()],
              field_mask=mock.Mock(paths=["campaign.id"]),
          )
      ],
  ]

  with mock.patch(
      "ads_mcp.tools.api.get_ads_client", return_value=mock_client
  ):
    with mock.patch("ads_mcp.tools.api.get_nested_attr", return_value="123"):
      with mock.patch("ads_mcp.tools.api.time.sleep") as mock_sleep:
        result = api.run_gaql_query(
            "SELECT campaign.id FROM campaign",
            "123",
        )

  assert result == [{"campaign.id": "123"}]
  assert mock_service.search_stream.call_count == 2
  mock_sleep.assert_called_once_with(1)


def test_run_gaql_query_does_not_retry_quota_errors():
  mock_client = mock.Mock()
  mock_service = mock_client.get_service.return_value
  quota_error = _google_ads_exception("quota_error: RESOURCE_EXHAUSTED")
  mock_service.search_stream.side_effect = quota_error

  with mock.patch(
      "ads_mcp.tools.api.get_ads_client", return_value=mock_client
  ):
    with mock.patch("ads_mcp.tools.api.time.sleep") as mock_sleep:
      with pytest.raises(api.ToolError, match="RESOURCE_EXHAUSTED"):
        api.run_gaql_query(
            "SELECT campaign.id FROM campaign",
            "123",
        )

  assert mock_service.search_stream.call_count == 1
  mock_sleep.assert_not_called()


def test_run_gaql_query_rejects_bad_enum_before_client_call():
  with mock.patch("ads_mcp.tools.api.get_ads_client") as mock_get_client:
    with pytest.raises(
        api.ToolError,
        match="Invalid enum literal 'ENABLD' for campaign.status",
    ):
      api.run_gaql_query(
          "SELECT campaign.id FROM campaign " "WHERE campaign.status = ENABLD",
          "123",
      )

  mock_get_client.assert_not_called()


def test_run_gaql_query_rejects_incompatible_fields_before_client_call():
  with mock.patch("ads_mcp.tools.api.get_ads_client") as mock_get_client:
    with pytest.raises(
        api.ToolError,
        match="metrics.clicks is not compatible with FROM campaign_criterion",
    ):
      api.run_gaql_query(
          "SELECT campaign_criterion.criterion_id, metrics.clicks "
          "FROM campaign_criterion",
          "123",
      )

  mock_get_client.assert_not_called()


def test_run_gaql_query_sends_canonical_enum_filters():
  mock_client = mock.Mock()
  mock_service = mock_client.get_service.return_value
  mock_service.search_stream.return_value = []

  with mock.patch(
      "ads_mcp.tools.api.get_ads_client",
      return_value=mock_client,
  ):
    assert not api.run_gaql_query(
        "SELECT campaign.id FROM campaign "
        "WHERE campaign.status IN ('enabled', paused)",
        "123",
    )

  sent_query = mock_service.search_stream.call_args.kwargs["query"]
  assert "campaign.status IN (ENABLED, PAUSED)" in sent_query
  assert "campaign.id FROM campaign" in sent_query
  assert "campaign.id, campaign.status" not in sent_query


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


def test_run_gaql_query_page_reuses_short_lived_cache():
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]

  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    first_page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=2,
    )
    second_page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=2,
        page_token="2",
    )

  assert mock_run.call_count == 1
  assert first_page["rows"] == [{"campaign.id": "1"}, {"campaign.id": "2"}]
  assert second_page["rows"] == [{"campaign.id": "3"}]


def test_run_gaql_query_page_expires_cache_after_ttl():
  rows = [{"campaign.id": "1"}]

  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    with mock.patch(
        "ads_mcp.tools.api.time.monotonic",
        side_effect=[100.0, 191.0, 191.0],
    ):
      api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "123",
          page_size=1,
      )
      api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "123",
          page_size=1,
      )

  assert mock_run.call_count == 2


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


def test_export_gaql_csv_writes_file_and_metadata(tmp_path):
  output_path = tmp_path / "export.csv"
  rows = [
      {
          "campaign.id": "1",
          "metrics.clicks": 10,
          "nested": {"a": 1},
      },
      {
          "campaign.id": "2",
          "metrics.clicks": 5,
          "nested": {"a": 2},
      },
  ]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    result = api.export_gaql_csv(
        query="SELECT campaign.id, metrics.clicks FROM campaign",
        customer_id="123",
        output_path=str(output_path),
    )

  assert result["file_path"] == str(output_path)
  assert result["row_count"] == 2
  assert result["total_row_count"] == 2
  assert result["truncated"] is False
  assert result["columns"] == [
      "campaign.id",
      "metrics.clicks",
      "nested",
  ]
  assert result["bytes_written"] == os.path.getsize(output_path)
  assert output_path.read_text(encoding="utf-8").splitlines() == [
      "campaign.id,metrics.clicks,nested",
      '1,10,"{""a"":1}"',
      '2,5,"{""a"":2}"',
  ]


def test_export_gaql_csv_applies_max_rows(tmp_path):
  output_path = tmp_path / "limited.csv"
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=rows):
    result = api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
        output_path=str(output_path),
        max_rows=2,
    )

  assert result["row_count"] == 2
  assert result["total_row_count"] == 3
  assert result["truncated"] is True
  assert result["max_rows_applied"] == 2


@pytest.mark.parametrize("max_rows", [True, 1.5])
def test_export_gaql_csv_rejects_non_integer_max_rows(max_rows, tmp_path):
  with pytest.raises(api.ToolError, match="max_rows must be an integer"):
    api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
        output_path=str(tmp_path / "out.csv"),
        max_rows=max_rows,
    )


@mock.patch("ads_mcp.tools.api.Credentials")
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
@mock.patch(
    "ads_mcp.tools.api._default_ads_assistant", return_value="assistant-tag"
)
@mock.patch("ads_mcp.tools.api.get_access_token")
@mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
def test_get_ads_client_caches_yaml_config_for_access_token(
    mock_isfile_unused,
    mock_getmtime_unused,
    mock_get_access_token,
    mock_default_ads_assistant_unused,
    mock_google_ads_client,
    mock_credentials,
):
  del (
      mock_isfile_unused,
      mock_getmtime_unused,
      mock_default_ads_assistant_unused,
  )
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
  constructor_kwargs = mock_google_ads_client.call_args.kwargs
  assert constructor_kwargs["developer_token"] == "dev-token"
  assert constructor_kwargs["use_proto_plus"] is True
  assert constructor_kwargs["ads_assistant"] == "assistant-tag"


@mock.patch(
    "ads_mcp.tools.api._default_ads_assistant", return_value="assistant-tag"
)
@mock.patch("ads_mcp.tools.api.get_access_token", return_value=None)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_get_ads_client_forces_proto_plus_before_storage_client_init(
    mock_google_ads_client,
    mock_isfile_unused,
    mock_get_access_token_unused,
    mock_default_ads_assistant_unused,
):
  del (
      mock_isfile_unused,
      mock_get_access_token_unused,
      mock_default_ads_assistant_unused,
  )
  mock_client_instance = mock_google_ads_client.load_from_dict.return_value
  mock_client_instance.login_customer_id = "default-login"

  with mock.patch(
      "ads_mcp.tools.api._load_ads_config",
      return_value={
          "developer_token": "dev-token",
          "refresh_token": "refresh",
          "client_id": "client-id",
          "client_secret": "client-secret",
          "use_proto_plus": False,
          "login_customer_id": "default-login",
      },
  ) as mock_load_config:
    client = api.get_ads_client()

  assert client is mock_client_instance
  mock_load_config.assert_called_once()
  mock_google_ads_client.load_from_dict.assert_called_once_with(
      {
          "developer_token": "dev-token",
          "refresh_token": "refresh",
          "client_id": "client-id",
          "client_secret": "client-secret",
          "use_proto_plus": True,
          "login_customer_id": "default-login",
          "ads_assistant": "assistant-tag",
      }
  )
  assert client.login_customer_id == "default-login"


@mock.patch(
    "ads_mcp.tools.api._default_ads_assistant", return_value="assistant-tag"
)
@mock.patch("ads_mcp.tools.api.get_access_token", return_value=None)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_get_ads_client_caches_storage_client_initialized_with_proto_plus(
    mock_google_ads_client,
    mock_isfile_unused,
    mock_get_access_token_unused,
    mock_default_ads_assistant_unused,
):
  del (
      mock_isfile_unused,
      mock_get_access_token_unused,
      mock_default_ads_assistant_unused,
  )
  mock_client_instance = mock_google_ads_client.load_from_dict.return_value
  mock_client_instance.login_customer_id = "default-login"

  with mock.patch(
      "ads_mcp.tools.api._load_ads_config",
      return_value={
          "use_proto_plus": False,
          "login_customer_id": "default-login",
      },
  ):
    client = api.get_ads_client()
    second_client = api.get_ads_client()

  assert client is mock_client_instance
  assert second_client is mock_client_instance
  assert client.login_customer_id == "default-login"
  mock_google_ads_client.load_from_dict.assert_called_once_with(
      {
          "use_proto_plus": True,
          "login_customer_id": "default-login",
          "ads_assistant": "assistant-tag",
      }
  )


def test_apply_ads_client_defaults_preserves_explicit_assistant():
  assert api._apply_ads_client_defaults(  # pylint: disable=protected-access
      {"use_proto_plus": False, "ads_assistant": "yaml-tag"}
  ) == {
      "use_proto_plus": True,
      "ads_assistant": "yaml-tag",
  }


def test_default_ads_assistant_caches_package_lookup():
  with mock.patch.dict(os.environ, {}, clear=True):
    with mock.patch(
        "ads_mcp.tools.api.importlib.metadata.version",
        return_value="0.6.3",
    ) as mock_version:
      assert api._default_ads_assistant() == "google-ads-mcp-0.6.3"
      assert api._default_ads_assistant() == "google-ads-mcp-0.6.3"

    mock_version.assert_called_once_with("google-ads-mcp")
