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

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from unittest import mock
import os
import stat
import tempfile
import uuid

from ads_mcp.tools import api
from google.ads.googleads.errors import GoogleAdsException
from google.protobuf.field_mask_pb2 import FieldMask
import proto
import pytest


real_page_cache_scope = api._page_cache_scope


@pytest.fixture(autouse=True)
def reset_ads_client():
  """Resets cached GoogleAdsClient instances before each test."""
  api._ADS_CLIENTS.clear()
  api._ADS_CLIENT_BUILDS.clear()
  api._ADS_CLIENTS_CREDENTIALS_MTIME = None
  api._ADS_CLIENTS_CREDENTIALS_PATH = None
  api._ADS_CONFIG_CACHE = {}
  api._PAGED_QUERY_CACHE = api.OrderedDict()
  api._PAGED_QUERY_LATEST = {}
  api._PAGED_QUERY_BUILDS = {}
  api._package_ads_assistant.cache_clear()
  with mock.patch.object(
      api,
      "_page_cache_scope",
      return_value="test-credentials",
  ):
    yield
  api._ADS_CLIENTS.clear()
  api._ADS_CLIENT_BUILDS.clear()
  api._ADS_CLIENTS_CREDENTIALS_MTIME = None
  api._ADS_CLIENTS_CREDENTIALS_PATH = None
  api._ADS_CONFIG_CACHE = {}
  api._PAGED_QUERY_CACHE = api.OrderedDict()
  api._PAGED_QUERY_LATEST = {}
  api._PAGED_QUERY_BUILDS = {}
  api._package_ads_assistant.cache_clear()


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
@mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_list_accessible_accounts(
    mock_google_ads_client, _, mock_getmtime, mock_load_config
):
  """Tests the list_accessible_accounts function."""
  del mock_getmtime, mock_load_config
  mock_client_instance = mock_google_ads_client.load_from_dict.return_value
  mock_service = mock_client_instance.get_service.return_value
  mock_service.list_accessible_customers.return_value.resource_names = [
      "customers/123",
      "customers/456",
  ]
  assert api.list_accessible_accounts() == ["123", "456"]


@mock.patch("ads_mcp.tools.api._load_ads_config", return_value={})
@mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_execute_gaql(
    mock_google_ads_client, _, mock_getmtime, mock_load_config
):
  """Tests the execute_gaql function."""
  del mock_getmtime, mock_load_config
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
  ) as mock_run:
    first_page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=1,
    )
    result = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=2,
        page_token=first_page["next_page_token"],
    )

  mock_run.assert_called_once()
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


def test_run_gaql_query_page_rejects_oversized_offset():
  page_token = "a" * 32 + ":" + "9" * 5000
  with pytest.raises(api.ToolError, match="Invalid page_token"):
    api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=2,
        page_token=page_token,
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
        page_token=first_page["next_page_token"],
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


def test_run_gaql_query_page_rejects_expired_snapshot_token():
  query = "SELECT campaign.id FROM campaign"
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
  ]
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    with mock.patch(
        "ads_mcp.tools.api.time.monotonic",
        side_effect=[100.0, 100.0, 191.0],
    ):
      first_page = api.run_gaql_query_page(
          query,
          "123",
          page_size=1,
      )
      with pytest.raises(api.ToolError, match="snapshot was evicted"):
        api.run_gaql_query_page(
            query,
            "123",
            page_size=1,
            page_token=first_page["next_page_token"],
        )

  mock_run.assert_called_once()
  query_key = api._page_cache_key(query, "123", None)
  assert query_key not in api._PAGED_QUERY_LATEST


def test_run_gaql_query_page_rejects_evicted_snapshot_token():
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
  ]
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=rows,
  ) as mock_run:
    first_page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign", "1", 1
    )
    for index in range(api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE):
      api.run_gaql_query_page(
          f"SELECT campaign.id FROM campaign WHERE campaign.id = {index}",
          str(index + 2),
          1,
      )

    with pytest.raises(
        api.ToolError,
        match="expired or its result snapshot was evicted",
    ):
      api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "1",
          1,
          page_token=first_page["next_page_token"],
      )

  assert mock_run.call_count == (
      api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE + 1
  )


def test_run_gaql_query_page_rejects_token_from_replaced_snapshot():
  query = "SELECT campaign.id FROM campaign"
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      side_effect=[
          [{"campaign.id": "1"}, {"campaign.id": "2"}],
          [{"campaign.id": "2"}, {"campaign.id": "1"}],
      ],
  ) as mock_run:
    first_page = api.run_gaql_query_page(query, "123", page_size=1)
    api._PAGED_QUERY_CACHE.clear()
    api.run_gaql_query_page(query, "123", page_size=1)

    with pytest.raises(api.ToolError, match="expired result snapshot"):
      api.run_gaql_query_page(
          query,
          "123",
          page_size=1,
          page_token=first_page["next_page_token"],
      )

  assert mock_run.call_count == 2


@pytest.mark.parametrize("offset", [0, 999])
def test_run_gaql_query_page_rejects_impossible_snapshot_offset(offset):
  query = "SELECT campaign.id FROM campaign"
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=rows,
  ):
    first_page = api.run_gaql_query_page(query, "123", page_size=1)
    snapshot_id = first_page["next_page_token"].split(":", maxsplit=1)[0]
    with pytest.raises(api.ToolError, match="Invalid page_token"):
      api.run_gaql_query_page(
          query,
          "123",
          page_size=1,
          page_token=f"{snapshot_id}:{offset}",
      )


def test_run_gaql_query_page_shares_concurrent_snapshot_build():
  query = "SELECT campaign.id FROM campaign"
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
  ]
  query_started = Event()
  release_query = Event()

  def _run_query(*_args, **_kwargs):
    query_started.set()
    release_query.wait(timeout=5)
    return rows

  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      side_effect=_run_query,
  ) as mock_run:
    with ThreadPoolExecutor(max_workers=2) as pool:
      first_future = pool.submit(
          api.run_gaql_query_page,
          query,
          "123",
          1,
      )
      assert query_started.wait(timeout=5)
      second_future = pool.submit(
          api.run_gaql_query_page,
          query,
          "123",
          1,
      )
      release_query.set()
      first_page = first_future.result(timeout=5)
      second_page = second_future.result(timeout=5)

  mock_run.assert_called_once()
  assert first_page["next_page_token"] == second_page["next_page_token"]
  for page in (first_page, second_page):
    continuation = api.run_gaql_query_page(
        query,
        "123",
        1,
        page_token=page["next_page_token"],
    )
    assert continuation["rows"] == [{"campaign.id": "2"}]


def test_returned_token_survives_capacity_and_ttl_churn_during_page_copy():
  page_copy_started = Event()
  release_page_copy = Event()
  monotonic_time = [100.0]
  blocked_first_copy = [False]

  def _query_rows(query, **_kwargs):
    marker = query.rsplit(maxsplit=1)[-1]
    return [{"row": f"{marker}-1"}, {"row": f"{marker}-2"}]

  def _delayed_page_copy(value):
    if value and value[0].get("row") == "A-1" and not blocked_first_copy[0]:
      blocked_first_copy[0] = True
      page_copy_started.set()
      release_page_copy.wait(timeout=5)
    return deepcopy(value)

  with mock.patch.object(
      api,
      "_page_cache_scope",
      return_value="principal-a",
  ):
    with mock.patch(
        "ads_mcp.tools.api.run_gaql_query",
        side_effect=_query_rows,
    ) as mock_run:
      with mock.patch(
          "ads_mcp.tools.api.time.monotonic",
          side_effect=lambda: monotonic_time[0],
      ):
        with mock.patch(
            "ads_mcp.tools.api.deepcopy",
            side_effect=_delayed_page_copy,
        ):
          with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(
                api.run_gaql_query_page,
                "SELECT A",
                "123",
                1,
            )
            assert page_copy_started.wait(timeout=5)
            monotonic_time[0] += api._PAGED_QUERY_CACHE_TTL_SECONDS + 1
            for index in range(api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE):
              api.run_gaql_query_page(f"SELECT B{index}", "123", 1)
            interleaved_page = api.run_gaql_query_page(
                "SELECT A",
                "123",
                1,
            )
            release_page_copy.set()
            first_page = first_future.result(timeout=5)

          assert any(
              cache_key[3] == "SELECT A"
              for cache_key in api._PAGED_QUERY_CACHE
          )
          assert len(api._PAGED_QUERY_CACHE) <= (
              api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE
          )
          continuation = api.run_gaql_query_page(
              "SELECT A",
              "123",
              1,
              page_token=first_page["next_page_token"],
          )

  assert continuation["rows"] == [{"row": "A-2"}]
  assert first_page["next_page_token"] == interleaved_page["next_page_token"]
  assert mock_run.call_count == (
      api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE + 1
  )


def test_same_query_generations_keep_both_continuation_tokens_live():
  query = "SELECT campaign.id FROM campaign"
  continuation_copy_started = Event()
  release_continuation_copy = Event()
  query_build_count = [0]

  def _query_rows(**kwargs):
    current_query = kwargs["query"]
    if current_query == query:
      query_build_count[0] += 1
      marker = "A" if query_build_count[0] == 1 else "B"
    else:
      marker = current_query.rsplit(maxsplit=1)[-1]
    return [
        {"row": f"{marker}-1"},
        {"row": f"{marker}-2"},
        {"row": f"{marker}-3"},
    ]

  def _block_old_continuation(value):
    if value and value[0].get("row") == "A-2":
      continuation_copy_started.set()
      release_continuation_copy.wait(timeout=5)
    return deepcopy(value)

  with mock.patch.object(
      api,
      "_page_cache_scope",
      return_value="same-principal",
  ):
    with mock.patch.object(
        api,
        "run_gaql_query",
        side_effect=_query_rows,
    ):
      with mock.patch.object(
          api,
          "deepcopy",
          side_effect=_block_old_continuation,
      ):
        first_a_page = api.run_gaql_query_page(query, "123", 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
          a_continuation_future = pool.submit(
              api.run_gaql_query_page,
              query,
              "123",
              1,
              first_a_page["next_page_token"],
          )
          assert continuation_copy_started.wait(timeout=5)
          for index in range(api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE):
            api.run_gaql_query_page(f"SELECT CHURN-{index}", "123", 3)

          first_b_page = api.run_gaql_query_page(query, "123", 1)
          release_continuation_copy.set()
          second_a_page = a_continuation_future.result(timeout=5)

        assert (
            second_a_page["next_page_token"] != first_b_page["next_page_token"]
        )
        a_final_page = api.run_gaql_query_page(
            query,
            "123",
            1,
            second_a_page["next_page_token"],
        )
        second_b_page = api.run_gaql_query_page(
            query,
            "123",
            1,
            first_b_page["next_page_token"],
        )

  assert query_build_count[0] == 2
  assert a_final_page["rows"] == [{"row": "A-3"}]
  assert second_b_page["rows"] == [{"row": "B-2"}]
  assert len(api._PAGED_QUERY_CACHE) <= (
      api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE
  )
  cached_snapshot_ids = {
      snapshot_key[5] for snapshot_key in api._PAGED_QUERY_CACHE
  }
  assert second_a_page["next_page_token"].split(":", maxsplit=1)[0] in (
      cached_snapshot_ids
  )
  assert first_b_page["next_page_token"].split(":", maxsplit=1)[0] in (
      cached_snapshot_ids
  )


def test_page_cache_partitions_local_sort_variants():
  query = "SELECT recommendation.resource_name FROM recommendation"
  rows = [
      {"recommendation.resource_name": "customers/123/recommendations/b"},
      {"recommendation.resource_name": "customers/123/recommendations/a"},
      {"recommendation.resource_name": "customers/123/recommendations/c"},
  ]
  with mock.patch.object(
      api,
      "run_gaql_query",
      side_effect=[rows, rows],
  ) as mock_run:
    unsorted_page = api.run_gaql_query_page(query, "123", 1)
    sorted_page = api.run_gaql_query_page(
        query,
        "123",
        1,
        row_sort_fields=("recommendation.resource_name",),
    )
    unsorted_continuation = api.run_gaql_query_page(
        query,
        "123",
        1,
        page_token=unsorted_page["next_page_token"],
    )
    sorted_continuation = api.run_gaql_query_page(
        query,
        "123",
        1,
        page_token=sorted_page["next_page_token"],
        row_sort_fields=("recommendation.resource_name",),
    )

  assert mock_run.call_count == 2
  assert unsorted_page["rows"] == [
      {"recommendation.resource_name": "customers/123/recommendations/b"}
  ]
  assert unsorted_continuation["rows"] == [
      {"recommendation.resource_name": "customers/123/recommendations/a"}
  ]
  assert sorted_page["rows"] == [
      {"recommendation.resource_name": "customers/123/recommendations/a"}
  ]
  assert sorted_continuation["rows"] == [
      {"recommendation.resource_name": "customers/123/recommendations/b"}
  ]


def test_page_cache_remains_process_bounded_across_principals():
  query_count = api._PAGED_QUERY_CACHE_MAX_ENTRIES + 1
  with mock.patch.object(
      api,
      "_page_cache_scope",
      side_effect=[f"principal-{index}" for index in range(query_count)],
  ):
    with mock.patch(
        "ads_mcp.tools.api.run_gaql_query",
        return_value=[{"campaign.id": "1"}],
    ):
      for index in range(query_count):
        api.run_gaql_query_page(
            f"SELECT campaign.id FROM campaign WHERE campaign.id = {index}",
            "123",
            1,
        )

  assert len(api._PAGED_QUERY_CACHE) == api._PAGED_QUERY_CACHE_MAX_ENTRIES
  assert all(
      api._snapshot_cache_key(query_key, snapshot_id) in api._PAGED_QUERY_CACHE
      for query_key, snapshot_id in api._PAGED_QUERY_LATEST.items()
  )


def test_run_gaql_query_page_partitions_snapshots_by_principal():
  query = "SELECT campaign.id FROM campaign"
  principal_a = mock.Mock(token="principal-a-secret-token")
  principal_b = mock.Mock(token="principal-b-secret-token")
  with mock.patch.object(
      api,
      "_page_cache_scope",
      side_effect=real_page_cache_scope,
  ):
    with mock.patch(
        "ads_mcp.tools.api.get_access_token",
        side_effect=[principal_a, principal_b],
    ):
      with mock.patch(
          "ads_mcp.tools.api.run_gaql_query",
          side_effect=[
              [{"campaign.id": "a1"}, {"campaign.id": "a2"}],
              [{"campaign.id": "b1"}, {"campaign.id": "b2"}],
          ],
      ) as mock_run:
        principal_a_page = api.run_gaql_query_page(query, "123", page_size=1)
        principal_b_page = api.run_gaql_query_page(query, "123", page_size=1)

  assert principal_a_page["rows"] == [{"campaign.id": "a1"}]
  assert principal_b_page["rows"] == [{"campaign.id": "b1"}]
  assert (
      principal_a_page["next_page_token"]
      != principal_b_page["next_page_token"]
  )
  assert mock_run.call_count == 2
  cache_scopes = [cache_key[0] for cache_key in api._PAGED_QUERY_CACHE]
  assert len(set(cache_scopes)) == 2
  assert all("secret-token" not in scope for scope in cache_scopes)


def test_run_gaql_query_page_only_copies_the_returned_page():
  query = "SELECT campaign.id FROM campaign"
  rows = [{"campaign.id": str(index)} for index in range(1000)]
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=rows,
  ):
    with mock.patch(
        "ads_mcp.tools.api.deepcopy",
        wraps=deepcopy,
    ) as mock_deepcopy:
      first_page = api.run_gaql_query_page(query, "123", page_size=25)
      api.run_gaql_query_page(
          query,
          "123",
          page_size=25,
          page_token=first_page["next_page_token"],
      )

  copied_list_lengths = [
      len(call.args[0])
      for call in mock_deepcopy.call_args_list
      if isinstance(call.args[0], list)
  ]
  assert copied_list_lengths == [25, 25]


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


def test_write_rows_to_temp_csv_removes_partial_file_on_failure(tmp_path):
  output_path = tmp_path / "partial.csv"
  file_descriptor = os.open(
      output_path,
      os.O_WRONLY | os.O_CREAT | os.O_EXCL,
      0o600,
  )
  with mock.patch(
      "ads_mcp.tools.api.tempfile.mkstemp",
      return_value=(file_descriptor, str(output_path)),
  ):
    with mock.patch(
        "ads_mcp.tools.api._csv_cell_value",
        side_effect=ValueError("serialization failed"),
    ):
      with pytest.raises(ValueError, match="serialization failed"):
        api.write_rows_to_temp_csv([{"campaign.id": "1"}])

  assert not output_path.exists()


def test_export_gaql_csv_removes_failed_new_explicit_output(
    tmp_path, monkeypatch
):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  output_path = tmp_path / "failed.csv"
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    with mock.patch(
        "ads_mcp.tools.api._csv_cell_value",
        side_effect=ValueError("serialization failed"),
    ):
      with pytest.raises(ValueError, match="serialization failed"):
        api.export_gaql_csv(
            query="SELECT campaign.id FROM campaign",
            customer_id="123",
            output_path=str(output_path),
        )

  assert not output_path.exists()
  assert not list(tmp_path.glob(".google_ads_mcp_*.tmp"))


def test_export_gaql_csv_preserves_existing_output_when_overwrite_fails(
    tmp_path, monkeypatch
):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  output_path = tmp_path / "existing.csv"
  output_path.write_text("original\n", encoding="utf-8")
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    with mock.patch(
        "ads_mcp.tools.api._csv_cell_value",
        side_effect=ValueError("serialization failed"),
    ):
      with pytest.raises(ValueError, match="serialization failed"):
        api.export_gaql_csv(
            query="SELECT campaign.id FROM campaign",
            customer_id="123",
            output_path=str(output_path),
            overwrite=True,
        )

  assert output_path.read_text(encoding="utf-8") == "original\n"
  assert not list(tmp_path.glob(".google_ads_mcp_*.tmp"))


def test_export_gaql_csv_preserves_existing_output_permissions(
    tmp_path, monkeypatch
):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  output_path = tmp_path / "private.csv"
  output_path.write_text("original\n", encoding="utf-8")
  output_path.chmod(0o600)
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
        output_path=str(output_path),
        overwrite=True,
    )

  assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


def test_export_gaql_csv_keeps_new_explicit_output_private(
    tmp_path, monkeypatch
):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  output_path = tmp_path / "new-private.csv"
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
        output_path=str(output_path),
    )

  assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


def test_export_gaql_csv_succeeds_if_post_link_cleanup_fails(
    tmp_path, monkeypatch
):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  output_path = tmp_path / "committed.csv"
  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    with mock.patch(
        "ads_mcp.tools.api.os.remove",
        side_effect=OSError("post-link cleanup failed"),
    ) as mock_remove:
      result = api.export_gaql_csv(
          query="SELECT campaign.id FROM campaign",
          customer_id="123",
          output_path=str(output_path),
      )

  assert result["file_path"] == str(output_path)
  assert output_path.read_text(encoding="utf-8").splitlines() == [
      "campaign.id",
      "1",
  ]
  assert mock_remove.call_count >= 2
  for temp_path in tmp_path.glob(".google_ads_mcp_*.tmp"):
    temp_path.unlink()


def test_export_gaql_csv_writes_file_and_metadata(tmp_path, monkeypatch):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
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


def test_export_gaql_csv_applies_max_rows(tmp_path, monkeypatch):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
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


def test_export_gaql_csv_rejects_path_outside_allowlist(tmp_path, monkeypatch):
  allowed_dir = tmp_path / "allowed"
  allowed_dir.mkdir()
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(allowed_dir))

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=[]):
    with pytest.raises(api.ToolError, match="GOOGLE_ADS_MCP_EXPORT_DIR"):
      api.export_gaql_csv(
          query="SELECT campaign.id FROM campaign",
          customer_id="123",
          output_path=str(tmp_path / "outside.csv"),
      )


def test_export_gaql_csv_honors_env_allowlist(tmp_path, monkeypatch):
  export_dir = tmp_path / "exports"
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(export_dir))
  output_path = export_dir / "nested" / "export.csv"

  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    result = api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
        output_path=str(output_path),
    )

  assert result["file_path"] == str(output_path)
  assert output_path.is_file()


def test_export_gaql_csv_requires_explicit_overwrite(tmp_path, monkeypatch):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  output_path = tmp_path / "existing.csv"
  output_path.write_text("original\n", encoding="utf-8")

  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    with pytest.raises(api.ToolError, match="overwrite=True"):
      api.export_gaql_csv(
          query="SELECT campaign.id FROM campaign",
          customer_id="123",
          output_path=str(output_path),
      )
    result = api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
        output_path=str(output_path),
        overwrite=True,
    )

  assert result["file_path"] == str(output_path)
  assert output_path.read_text(encoding="utf-8").splitlines() == [
      "campaign.id",
      "1",
  ]


def test_export_gaql_csv_defaults_to_temp_file(monkeypatch):
  monkeypatch.delenv("GOOGLE_ADS_MCP_EXPORT_DIR", raising=False)

  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    result = api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
    )

  try:
    temp_dir = os.path.realpath(tempfile.gettempdir())
    assert (
        os.path.commonpath([temp_dir, os.path.realpath(result["file_path"])])
        == temp_dir
    )
    assert os.path.isfile(result["file_path"])
  finally:
    os.unlink(result["file_path"])


def test_export_gaql_csv_rejects_directory_output_path(tmp_path, monkeypatch):
  """An existing directory is rejected even when overwrite is requested."""
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))

  with mock.patch("ads_mcp.tools.api.run_gaql_query", return_value=[]):
    with pytest.raises(api.ToolError, match="not a directory"):
      api.export_gaql_csv(
          query="SELECT campaign.id FROM campaign",
          customer_id="123",
          output_path=str(tmp_path),
          overwrite=True,
      )


def test_export_gaql_csv_validates_path_before_query(tmp_path, monkeypatch):
  """A disallowed output_path fails before any GAQL work runs."""
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path / "allowed"))

  with mock.patch("ads_mcp.tools.api.run_gaql_query") as mock_query:
    with pytest.raises(api.ToolError, match="GOOGLE_ADS_MCP_EXPORT_DIR"):
      api.export_gaql_csv(
          query="SELECT campaign.id FROM campaign",
          customer_id="123",
          output_path=str(tmp_path / "outside.csv"),
      )

  mock_query.assert_not_called()


@pytest.mark.skipif(not os.path.isdir("/tmp"), reason="POSIX /tmp required")
def test_export_gaql_csv_allows_posix_tmp_by_default(monkeypatch):
  """Explicit /tmp paths count as the system temp directory."""
  monkeypatch.delenv("GOOGLE_ADS_MCP_EXPORT_DIR", raising=False)
  output_path = f"/tmp/google_ads_mcp_test_{uuid.uuid4().hex}.csv"

  with mock.patch(
      "ads_mcp.tools.api.run_gaql_query",
      return_value=[{"campaign.id": "1"}],
  ):
    result = api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign",
        customer_id="123",
        output_path=output_path,
    )

  try:
    assert os.path.isfile(result["file_path"])
  finally:
    os.unlink(result["file_path"])


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW required"
)
def test_open_export_file_refuses_symlink_targets(tmp_path):
  """A symlink planted at the export path cannot redirect the write."""
  victim_path = tmp_path / "victim.txt"
  victim_path.write_text("precious\n", encoding="utf-8")
  link_path = tmp_path / "export.csv"
  link_path.symlink_to(victim_path)

  with pytest.raises(api.ToolError, match="Unable to write"):
    api._open_export_file(str(link_path), overwrite=True)

  assert victim_path.read_text(encoding="utf-8") == "precious\n"


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
@mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_get_ads_client_coerces_yaml_login_id_and_forces_proto_plus(
    mock_google_ads_client,
    mock_isfile_unused,
    mock_getmtime_unused,
    mock_get_access_token_unused,
    mock_default_ads_assistant_unused,
):
  del (
      mock_isfile_unused,
      mock_getmtime_unused,
      mock_get_access_token_unused,
      mock_default_ads_assistant_unused,
  )
  mock_client_instance = mock_google_ads_client.load_from_dict.return_value
  mock_client_instance.login_customer_id = "123456"

  with mock.patch(
      "ads_mcp.tools.api._load_ads_config",
      return_value={
          "developer_token": "dev-token",
          "refresh_token": "refresh",
          "client_id": "client-id",
          "client_secret": "client-secret",
          "use_proto_plus": False,
          "login_customer_id": 123456,
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
          "login_customer_id": "123456",
          "ads_assistant": "assistant-tag",
      }
  )
  assert client.login_customer_id == "123456"


@mock.patch(
    "ads_mcp.tools.api._default_ads_assistant", return_value="assistant-tag"
)
@mock.patch("ads_mcp.tools.api.get_access_token", return_value=None)
@mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0)
@mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True)
@mock.patch("ads_mcp.tools.api.GoogleAdsClient")
def test_get_ads_client_caches_storage_client_initialized_with_proto_plus(
    mock_google_ads_client,
    mock_isfile_unused,
    mock_getmtime_unused,
    mock_get_access_token_unused,
    mock_default_ads_assistant_unused,
):
  del (
      mock_isfile_unused,
      mock_getmtime_unused,
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


def test_get_ads_client_isolates_concurrent_login_customer_ids():
  """Concurrent callers get immutable clients for their own manager IDs."""

  def build_client(config):
    client = mock.Mock()
    client.login_customer_id = config.get("login_customer_id")
    return client

  requested_ids = ["111", "222"] * 50
  with (
      mock.patch("ads_mcp.tools.api.get_access_token", return_value=None),
      mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True),
      mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0),
      mock.patch(
          "ads_mcp.tools.api._load_ads_config",
          return_value={"developer_token": "token"},
      ),
      mock.patch(
          "ads_mcp.tools.api._default_ads_assistant", return_value=None
      ),
      mock.patch.object(
          api.GoogleAdsClient, "load_from_dict", side_effect=build_client
      ) as mock_load,
  ):
    with ThreadPoolExecutor(max_workers=16) as executor:
      clients = list(executor.map(api.get_ads_client, requested_ids))

  assert [client.login_customer_id for client in clients] == requested_ids
  assert mock_load.call_count == 2
  assert {
      call.args[0]["login_customer_id"] for call in mock_load.call_args_list
  } == {"111", "222"}


def test_get_ads_client_invalidates_cache_when_credentials_change():
  """A credentials mtime change rebuilds the per-login client cache."""

  def build_client(config):
    client = mock.Mock()
    client.login_customer_id = config["login_customer_id"]
    return client

  with (
      mock.patch("ads_mcp.tools.api.get_access_token", return_value=None),
      mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True),
      mock.patch(
          "ads_mcp.tools.api.os.path.getmtime",
          side_effect=[100.0, 100.0, 200.0],
      ),
      mock.patch(
          "ads_mcp.tools.api._load_ads_config",
          return_value={"login_customer_id": "default-login"},
      ),
      mock.patch(
          "ads_mcp.tools.api._default_ads_assistant", return_value=None
      ),
      mock.patch.object(
          api.GoogleAdsClient, "load_from_dict", side_effect=build_client
      ) as mock_load,
  ):
    first_client = api.get_ads_client()
    cached_client = api.get_ads_client()
    refreshed_client = api.get_ads_client()

  assert first_client is cached_client
  assert refreshed_client is not first_client
  assert mock_load.call_count == 2


def test_get_ads_client_omits_missing_default_login_customer_id():
  """A missing YAML default is omitted rather than passed through as None."""
  mock_client = mock.Mock(login_customer_id=None)
  with (
      mock.patch("ads_mcp.tools.api.get_access_token", return_value=None),
      mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True),
      mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0),
      mock.patch("ads_mcp.tools.api._load_ads_config", return_value={}),
      mock.patch(
          "ads_mcp.tools.api._default_ads_assistant", return_value=None
      ),
      mock.patch.object(
          api.GoogleAdsClient, "load_from_dict", return_value=mock_client
      ) as mock_load,
  ):
    assert api.get_ads_client() is mock_client

  assert "login_customer_id" not in mock_load.call_args.args[0]


def test_get_ads_client_normalizes_dashed_login_id():
  """Dashed manager IDs share a cache entry with their plain form."""

  def build_client(config):
    client = mock.Mock()
    client.login_customer_id = config.get("login_customer_id")
    return client

  with (
      mock.patch("ads_mcp.tools.api.get_access_token", return_value=None),
      mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True),
      mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0),
      mock.patch(
          "ads_mcp.tools.api._load_ads_config",
          return_value={"developer_token": "token"},
      ),
      mock.patch(
          "ads_mcp.tools.api._default_ads_assistant", return_value=None
      ),
      mock.patch.object(
          api.GoogleAdsClient, "load_from_dict", side_effect=build_client
      ) as mock_load,
  ):
    dashed_client = api.get_ads_client("123-456-7890")
    plain_client = api.get_ads_client("1234567890")

  assert dashed_client is plain_client
  assert dashed_client.login_customer_id == "1234567890"
  assert mock_load.call_count == 1


def test_get_ads_client_rejects_non_numeric_login_id():
  """Malformed manager IDs raise ToolError before any client build."""
  with (
      mock.patch("ads_mcp.tools.api.get_access_token", return_value=None),
      mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True),
      mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0),
      mock.patch(
          "ads_mcp.tools.api._load_ads_config",
          return_value={"developer_token": "token"},
      ),
      mock.patch(
          "ads_mcp.tools.api._default_ads_assistant", return_value=None
      ),
      mock.patch.object(api.GoogleAdsClient, "load_from_dict") as mock_load,
  ):
    with pytest.raises(api.ToolError, match="login_customer_id"):
      api.get_ads_client("not-a-customer")

  mock_load.assert_not_called()


def test_get_ads_client_wraps_config_validation_errors():
  """Client config validation failures surface as retryable ToolErrors."""
  with (
      mock.patch("ads_mcp.tools.api.get_access_token", return_value=None),
      mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True),
      mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0),
      mock.patch(
          "ads_mcp.tools.api._load_ads_config",
          return_value={"developer_token": "token"},
      ),
      mock.patch(
          "ads_mcp.tools.api._default_ads_assistant", return_value=None
      ),
      mock.patch.object(
          api.GoogleAdsClient,
          "load_from_dict",
          side_effect=ValueError("login customer ID is invalid"),
      ) as mock_load,
  ):
    with pytest.raises(
        api.ToolError, match="Invalid Google Ads client config"
    ):
      api.get_ads_client("12345678901")
    with pytest.raises(
        api.ToolError, match="Invalid Google Ads client config"
    ):
      api.get_ads_client("12345678901")

  assert mock_load.call_count == 2


def test_get_ads_client_evicts_least_recently_used_clients():
  """The per-login client cache stays bounded under many manager IDs."""

  def build_client(config):
    client = mock.Mock()
    client.login_customer_id = config.get("login_customer_id")
    return client

  login_ids = [
      str(1000000000 + index)
      for index in range(api._ADS_CLIENTS_MAX_ENTRIES + 1)
  ]
  with (
      mock.patch("ads_mcp.tools.api.get_access_token", return_value=None),
      mock.patch("ads_mcp.tools.api.os.path.isfile", return_value=True),
      mock.patch("ads_mcp.tools.api.os.path.getmtime", return_value=123.0),
      mock.patch(
          "ads_mcp.tools.api._load_ads_config",
          return_value={"developer_token": "token"},
      ),
      mock.patch(
          "ads_mcp.tools.api._default_ads_assistant", return_value=None
      ),
      mock.patch.object(
          api.GoogleAdsClient, "load_from_dict", side_effect=build_client
      ) as mock_load,
  ):
    for login_id in login_ids:
      api.get_ads_client(login_id)

    assert len(api._ADS_CLIENTS) == api._ADS_CLIENTS_MAX_ENTRIES
    assert mock_load.call_count == len(login_ids)

    api.get_ads_client(login_ids[-1])
    assert mock_load.call_count == len(login_ids)

    api.get_ads_client(login_ids[0])
    assert mock_load.call_count == len(login_ids) + 1


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
