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
import csv
import json
import os
import stat
import tempfile
import uuid

from ads_mcp.tools import api
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.protobuf.field_mask_pb2 import FieldMask
import proto
import pytest


real_page_cache_scope = api._page_cache_scope


@pytest.fixture(autouse=True)
def reset_ads_client():
  """Resets cached GoogleAdsClient instances before each test."""
  for artifact_path in list(api._MANAGED_TEMP_ARTIFACTS):
    api.remove_temp_csv_file(artifact_path)
  api._ADS_CLIENTS.clear()
  api._ADS_CLIENT_BUILDS.clear()
  api._ADS_CLIENTS_CREDENTIALS_MTIME = None
  api._ADS_CLIENTS_CREDENTIALS_PATH = None
  api._ADS_CONFIG_CACHE = {}
  api._PAGED_QUERY_CACHE = api.OrderedDict()
  api._PAGED_QUERY_LATEST = {}
  api._PAGED_QUERY_BUILDS = {}
  api._PAGED_QUERY_SNAPSHOT_GROUPS = {}
  api._PAGED_QUERY_GROUP_SNAPSHOTS = {}
  api._ACTIVE_PAGED_QUERY_GROUPS = {}
  api._ACCOUNT_SNAPSHOT_CACHE = api.OrderedDict()
  api._MANAGED_TEMP_ARTIFACTS = api.OrderedDict()
  api._MANAGED_TEMP_ARTIFACT_REAPER = None
  api._MATERIALIZED_SNAPSHOT_CACHE = api.OrderedDict()
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
  api._PAGED_QUERY_SNAPSHOT_GROUPS = {}
  api._PAGED_QUERY_GROUP_SNAPSHOTS = {}
  api._ACTIVE_PAGED_QUERY_GROUPS = {}
  api._ACCOUNT_SNAPSHOT_CACHE = api.OrderedDict()
  for artifact_path in list(api._MANAGED_TEMP_ARTIFACTS):
    api.remove_temp_csv_file(artifact_path)
  api._MANAGED_TEMP_ARTIFACTS = api.OrderedDict()
  api._MANAGED_TEMP_ARTIFACT_REAPER = None
  api._MATERIALIZED_SNAPSHOT_CACHE = api.OrderedDict()
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
  result = api.list_accessible_accounts()
  assert result["accounts"] == ["123", "456"]
  assert result["returned_count"] == 2
  assert result["total_count"] == 2
  assert result["total_page_count"] == 1
  assert result["truncated"] is False
  assert result["has_more"] is False
  assert result["complete_inline"] is True
  assert result["next_page_token"] is None
  assert result["bulk_export_call"]["tool"] == (
      "export_accessible_accounts_csv"
  )


@mock.patch("ads_mcp.tools.api.get_ads_client")
def test_list_accessible_accounts_pages_without_implicit_file_write(
    mock_get_ads_client,
):
  account_ids = [str(1_000_000_000 + index) for index in range(250)]
  customer_service = mock_get_ads_client.return_value.get_service.return_value
  customer_service.list_accessible_customers.return_value.resource_names = [
      f"customers/{account_id}" for account_id in account_ids
  ]

  with mock.patch.object(api, "_write_csv_rows") as mock_write:
    first_page = api.list_accessible_accounts(page_size=1000)
    second_page = api.list_accessible_accounts(
        page_size=1000,
        page_token=first_page["next_page_token"],
    )
    third_page = api.list_accessible_accounts(
        page_size=1000,
        page_token=second_page["next_page_token"],
    )

  mock_write.assert_not_called()
  assert first_page["page_size"] == 100
  assert first_page["page_size_clamped"] is True
  assert first_page["total_count"] == len(account_ids)
  assert first_page["accounts"] == account_ids[:100]
  assert second_page["accounts"] == account_ids[100:200]
  assert third_page["accounts"] == account_ids[200:]
  assert third_page["next_page_token"] is None
  assert customer_service.list_accessible_customers.call_count == 1


@mock.patch("ads_mcp.tools.api.get_ads_client")
def test_export_accessible_accounts_csv_uses_exact_snapshot(
    mock_get_ads_client,
    tmp_path,
):
  customer_service = mock_get_ads_client.return_value.get_service.return_value
  customer_service.list_accessible_customers.return_value.resource_names = [
      "customers/123",
      "customers/456",
  ]
  page = api.list_accessible_accounts(page_size=1)
  snapshot_token = page["bulk_export_call"]["arguments"]["snapshot_token"]

  result = api.export_accessible_accounts_csv(
      snapshot_token,
      output_path=str(tmp_path / "accounts.csv"),
  )

  with open(result["file_path"], newline="", encoding="utf-8") as csv_file:
    rows = list(csv.DictReader(csv_file))
  assert rows == [{"customer_id": "123"}, {"customer_id": "456"}]
  assert result["complete"] is True
  assert result["row_count"] == 2
  assert customer_service.list_accessible_customers.call_count == 1


@mock.patch("ads_mcp.tools.api.get_ads_client")
def test_accessible_account_continuation_stays_on_original_snapshot(
    mock_get_ads_client,
):
  customer_service = mock_get_ads_client.return_value.get_service.return_value
  customer_service.list_accessible_customers.side_effect = [
      mock.Mock(resource_names=["customers/1", "customers/2"]),
      mock.Mock(resource_names=["customers/9", "customers/10"]),
  ]

  old_page = api.list_accessible_accounts(page_size=1)
  fresh_page = api.list_accessible_accounts(page_size=1)
  old_continuation = api.list_accessible_accounts(
      page_size=1,
      page_token=old_page["next_page_token"],
  )

  assert old_page["accounts"] == ["1"]
  assert fresh_page["accounts"] == ["9"]
  assert old_continuation["accounts"] == ["2"]
  assert customer_service.list_accessible_customers.call_count == 2


@mock.patch("ads_mcp.tools.api.get_ads_client")
def test_list_accessible_accounts_wraps_google_api_errors(mock_get_ads_client):
  customer_service = mock_get_ads_client.return_value.get_service.return_value
  customer_service.list_accessible_customers.side_effect = (
      api.google_exceptions.PermissionDenied("no access")
  )

  with pytest.raises(api.ToolError, match="no access"):
    api.list_accessible_accounts()


@mock.patch("ads_mcp.tools.api.get_ads_client")
def test_list_accessible_accounts_handles_empty_snapshot(mock_get_ads_client):
  customer_service = mock_get_ads_client.return_value.get_service.return_value
  customer_service.list_accessible_customers.return_value.resource_names = []

  result = api.list_accessible_accounts()

  assert not result["accounts"]
  assert result["total_count"] == 0
  assert result["total_page_count"] == 0
  assert result["complete_inline"] is True


def test_accessible_account_snapshot_expires_with_restart_guidance():
  with mock.patch.object(api.time, "monotonic", side_effect=[0.0, 91.0]):
    snapshot_id = api._store_account_snapshot(("123",))
    with pytest.raises(api.ToolError, match="Call list_accessible_accounts"):
      api._get_account_snapshot(snapshot_id)


def test_accessible_account_snapshot_is_credential_scoped():
  with mock.patch.object(api, "_page_cache_scope", return_value="principal-a"):
    snapshot_id = api._store_account_snapshot(("123",))
  with mock.patch.object(api, "_page_cache_scope", return_value="principal-b"):
    with pytest.raises(
        api.ToolError, match="different Google Ads credentials"
    ):
      api._get_account_snapshot(snapshot_id)


def test_accessible_account_snapshot_cache_is_byte_bounded(monkeypatch):
  monkeypatch.setattr(api, "_ACCOUNT_SNAPSHOT_CACHE_MAX_BYTES", 15)
  first_snapshot_id = api._store_account_snapshot(("1234567890",))
  second_snapshot_id = api._store_account_snapshot(("abcdefghij",))

  with pytest.raises(api.ToolError, match="was evicted"):
    api._get_account_snapshot(first_snapshot_id)
  assert api._get_account_snapshot(second_snapshot_id) == ("abcdefghij",)


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


def test_spooled_snapshot_retry_discards_partial_first_attempt():
  transient_error = _google_ads_exception("internal_error: INTERNAL_ERROR")

  def _partial_attempt():
    yield {"campaign.id": "stale-partial"}
    raise transient_error

  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      side_effect=[_partial_attempt(), [{"campaign.id": "complete"}]],
  ) as mock_query:
    with mock.patch.object(api.time, "sleep") as mock_sleep:
      snapshot = api.run_gaql_query_snapshot(
          "SELECT campaign.id FROM campaign",
          "123",
      )

  assert list(snapshot["rows"]) == [{"campaign.id": "complete"}]
  assert snapshot["total_results_count"] == 1
  assert mock_query.call_count == 2
  mock_sleep.assert_called_once_with(1)


def test_failed_spooled_snapshot_build_removes_internal_file(tmp_path):
  spool_path = tmp_path / "failed-snapshot.sqlite3"
  descriptor = os.open(spool_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
  with mock.patch.object(
      api.tempfile,
      "mkstemp",
      return_value=(descriptor, str(spool_path)),
  ):
    with mock.patch.object(
        api,
        "_iter_gaql_query_attempt",
        side_effect=RuntimeError("serialization source failed"),
    ):
      with pytest.raises(RuntimeError, match="serialization source failed"):
        api.run_gaql_query_page(
            "SELECT campaign.id FROM campaign",
            "123",
            page_size=1,
        )

  assert not spool_path.exists()
  assert not api._PAGED_QUERY_BUILDS


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
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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
        page_size=1,
        page_token=first_page["next_page_token"],
    )

  mock_run.assert_called_once()
  assert result["rows"] == [{"campaign.id": "2"}]
  assert result["next_page_token"] is not None
  assert result["total_results_count"] == 3
  assert result["requested_page_size"] == 1
  assert result["page_size"] == 1
  assert result["page_size_clamped"] is False
  assert result["snapshot_token"].startswith("gaql-snapshot-v1:")


def test_run_gaql_query_page_rejects_changed_continuation_page_size():
  rows = [{"campaign.id": str(index)} for index in range(101)]
  with mock.patch(
      "ads_mcp.tools.api._iter_gaql_query_attempt",
      return_value=rows,
  ):
    first_page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=25,
    )
    with pytest.raises(
        api.ToolError,
        match="bound to page_size=25.*same limit/page_size",
    ):
      api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "123",
          page_size=10,
          page_token=first_page["next_page_token"],
      )


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
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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


def test_tokenless_page_refreshes_while_old_snapshot_tokens_stay_exact():
  query = "SELECT campaign.id FROM campaign"
  old_rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  new_rows = [{"campaign.id": "9"}, {"campaign.id": "10"}]

  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      side_effect=[old_rows, new_rows],
  ) as mock_run:
    old_page = api.run_gaql_query_page(query, "123", page_size=1)
    new_page = api.run_gaql_query_page(query, "123", page_size=1)
    old_continuation = api.run_gaql_query_page(
        query,
        "123",
        page_size=1,
        page_token=old_page["next_page_token"],
    )
    old_export_rows = api._get_export_snapshot_rows(  # pylint: disable=protected-access
        old_page["snapshot_token"]
    )

  assert mock_run.call_count == 2
  assert new_page["rows"] == [{"campaign.id": "9"}]
  assert old_continuation["rows"] == [{"campaign.id": "2"}]
  assert old_export_rows == old_rows
  assert old_page["snapshot_token"] != new_page["snapshot_token"]


def test_run_gaql_query_page_expires_cache_after_ttl():
  rows = [{"campaign.id": "1"}]

  with mock.patch(
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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
def test_run_gaql_query_page_rejects_impossible_snapshot_offset(
    offset,
):
  query = "SELECT campaign.id FROM campaign"
  rows = [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
      {"campaign.id": "3"},
  ]
  with mock.patch(
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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

  def _query_rows(query, *_args, **_kwargs):
    marker = query.rsplit(maxsplit=1)[-1]
    return [{"row": f"{marker}-1"}, {"row": f"{marker}-2"}]

  original_page = api._SpooledGaqlSnapshot.page

  def _delayed_page_read(snapshot, page_size, offset):
    page = original_page(snapshot, page_size, offset)
    if (
        page
        and page["rows"]
        and page["rows"][0].get("row") == "A-1"
        and not blocked_first_copy[0]
    ):
      blocked_first_copy[0] = True
      page_copy_started.set()
      release_page_copy.wait(timeout=5)
    return page

  with mock.patch.object(
      api,
      "_page_cache_scope",
      return_value="principal-a",
  ):
    with mock.patch(
        "ads_mcp.tools.api._iter_gaql_query_attempt",
        side_effect=_query_rows,
    ) as mock_run:
      with mock.patch(
          "ads_mcp.tools.api.time.monotonic",
          side_effect=lambda: monotonic_time[0],
      ):
        with mock.patch.object(
            api._SpooledGaqlSnapshot,
            "page",
            autospec=True,
            side_effect=_delayed_page_read,
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
        "_iter_gaql_query_attempt",
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
      "_iter_gaql_query_attempt",
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
        "ads_mcp.tools.api._iter_gaql_query_attempt",
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
          "ads_mcp.tools.api._iter_gaql_query_attempt",
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
      "ads_mcp.tools.api._iter_gaql_query_attempt",
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
  snapshot_id = "a" * 32
  assert api.build_paginated_list_response(
      "campaigns",
      rows=[{"campaign.id": "1"}, {"campaign.id": "2"}],
      total_count=5,
      page_size=2,
      next_page_token=f"{snapshot_id}:2",
  ) == {
      "campaigns": [
          {"campaign.id": "1"},
          {"campaign.id": "2"},
      ],
      "returned_count": 2,
      "total_count": 5,
      "total_page_count": 3,
      "truncated": True,
      "has_more": True,
      "complete_inline": False,
      "next_page_token": f"{snapshot_id}:2",
      "page_size": 2,
      "requested_page_size": 2,
      "page_size_clamped": False,
      "bulk_export_call": {
          "tool": "export_gaql_csv",
          "arguments": {
              "snapshot_token": f"gaql-snapshot-v1:{snapshot_id}",
          },
      },
  }


@pytest.mark.parametrize(
    ("row_count", "complete_inline"),
    [
        (0, True),
        (1, True),
        (100, True),
        (101, False),
    ],
)
def test_paged_delivery_bounds_inline_rows_without_capping_access(
    row_count,
    complete_inline,
):
  rows = [{"campaign.id": str(index)} for index in range(row_count)]
  continuation = None
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=rows
  ) as mock_run:
    page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=10_000,
    )
    result = api.build_paginated_list_response(
        "campaigns",
        page["rows"],
        total_count=page["total_results_count"],
        page_size=10_000,
        next_page_token=page["next_page_token"],
        snapshot_token=page["snapshot_token"],
    )
    if page["next_page_token"]:
      continuation = api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "123",
          page_size=10_000,
          page_token=page["next_page_token"],
      )

  mock_run.assert_called_once()
  assert len(page["rows"]) == min(row_count, 100)
  assert page["requested_page_size"] == 10_000
  assert page["page_size"] == 100
  assert page["page_size_clamped"] is True
  assert result["requested_page_size"] == 10_000
  assert result["page_size"] == 100
  assert result["page_size_clamped"] is True
  assert result["complete_inline"] is complete_inline
  assert result["has_more"] is (not complete_inline)
  assert result["bulk_export_call"]["tool"] == "export_gaql_csv"
  if not complete_inline:
    assert continuation is not None
    assert continuation["rows"] == [{"campaign.id": "100"}]
    assert continuation["snapshot_token"] == page["snapshot_token"]


def test_paged_delivery_bounds_heterogeneous_rows_by_serialized_bytes():
  rows = [
      {"campaign.id": str(index), "payload": "x" * size}
      for index, size in enumerate([20_000, 20_000, 20_000, 5, 20_000])
  ]
  pages = []
  page_token = None
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=rows
  ) as mock_run:
    while True:
      page = api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "123",
          page_size=100,
          page_token=page_token,
      )
      pages.append(page)
      page_token = page["next_page_token"]
      if page_token is None:
        break

  mock_run.assert_called_once()
  assert len(pages) > 1
  assert {page["total_page_count"] for page in pages} == {len(pages)}
  assert all(
      page["inline_bytes"] <= page["inline_byte_limit"] for page in pages
  )
  assert all(page["byte_limited_pagination"] for page in pages)
  assert len({page["snapshot_token"] for page in pages}) == 1
  assert [row["campaign.id"] for page in pages for row in page["rows"]] == [
      "0",
      "1",
      "2",
      "3",
      "4",
  ]


def test_spooled_page_plan_inserts_boundaries_in_bounded_batches():
  rows = [{"campaign.id": str(index)} for index in range(600)]
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      return_value=rows,
  ):
    snapshot = api._build_spooled_gaql_snapshot(
        "SELECT campaign.id FROM campaign",
        "123",
        None,
        None,
    )

  batch_sizes = []
  original_insert = api._insert_page_plan_batch

  def _record_batch(connection, plans):
    batch_sizes.append(len(plans))
    return original_insert(connection, plans)

  with mock.patch.object(
      api,
      "_insert_page_plan_batch",
      side_effect=_record_batch,
  ):
    page = snapshot.page(page_size=1, offset=0)

  assert page["rows"] == [rows[0]]
  assert len(batch_sizes) > 1
  assert sum(batch_sizes) == len(rows)
  assert max(batch_sizes) <= api._PAGE_PLAN_INSERT_BATCH_SIZE


def test_single_oversized_row_uses_placeholder_and_exact_export_path():
  original_row = {"campaign.id": "1", "payload": "x" * 60_000}
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      return_value=[original_row],
  ):
    page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=100,
    )
    result = api.build_paginated_list_response(
        "campaigns",
        page["rows"],
        total_count=page["total_results_count"],
        page_size=100,
        next_page_token=page["next_page_token"],
        snapshot_token=page["snapshot_token"],
    )

  assert page["next_page_token"] is None
  assert page["inline_bytes"] <= page["inline_byte_limit"]
  assert api._INLINE_OMISSION_KEY in page["rows"][0]
  assert result["returned_count"] == 0
  assert result["represented_row_count"] == 1
  assert result["inline_omitted_row_count"] == 1
  assert result["complete_inline"] is False
  assert result["truncated"] is True
  assert result["bulk_export_call"]["arguments"]["snapshot_token"] == (
      page["snapshot_token"]
  )


def test_oversized_row_after_nonempty_page_uses_bounded_placeholder():
  rows = [
      {"campaign.id": "1", "payload": "small"},
      {"campaign.id": "2", "payload": "x" * 60_000},
  ]
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      return_value=rows,
  ):
    first_page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=100,
    )
    second_page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=100,
        page_token=first_page["next_page_token"],
    )

  assert first_page["rows"] == [rows[0]]
  assert second_page["inline_bytes"] <= second_page["inline_byte_limit"]
  assert second_page["inline_omitted_row_count"] == 1
  assert api._INLINE_OMISSION_KEY in second_page["rows"][0]


def test_failed_spool_page_read_does_not_leave_sticky_build():
  query = "SELECT campaign.id FROM campaign"
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  original_page = api._SpooledGaqlSnapshot.page
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      return_value=rows,
  ) as mock_query:
    with mock.patch.object(
        api._SpooledGaqlSnapshot,
        "page",
        side_effect=RuntimeError("disk read failed"),
    ):
      with pytest.raises(RuntimeError, match="disk read failed"):
        api.run_gaql_query_page(query, "123", page_size=1)
    assert not api._PAGED_QUERY_BUILDS
    with mock.patch.object(api._SpooledGaqlSnapshot, "page", original_page):
      result = api.run_gaql_query_page(query, "123", page_size=1)

  assert result["rows"] == [rows[0]]
  assert mock_query.call_count == 2


def test_bound_inline_sections_shares_one_byte_budget_across_fanout():
  sections = {
      "AGE": [{"type": "AGE", "payload": "a" * 15_000}],
      "GENDER": [{"type": "GENDER", "payload": "b" * 15_000}],
      "INCOME": [{"type": "INCOME", "payload": "c" * 15_000}],
  }

  bounded = api.bound_inline_sections(sections)

  assert bounded["sections"]["AGE"] == sections["AGE"]
  assert bounded["sections"]["GENDER"] == sections["GENDER"]
  assert bounded["sections"]["INCOME"] == []
  assert bounded["omitted_counts"] == {
      "AGE": 0,
      "GENDER": 0,
      "INCOME": 1,
  }
  assert bounded["limited_by_bytes"] is True
  assert bounded["inline_bytes"] <= bounded["inline_byte_limit"]


def test_bounded_materialized_response_spills_oversized_metadata():
  result = api.build_bounded_materialized_response(
      {
          "items": ["kept"],
          "diagnostic": {"message": "x" * 60_000},
      },
      ("items",),
      artifact_key="full_result_artifact",
      truncation_note="Full response is in the artifact.",
  )

  assert len(json.dumps(result).encode("utf-8")) <= (
      api.INLINE_RESPONSE_BYTE_LIMIT
  )
  assert result["truncated"] is True
  assert result["diagnostic"]["inline_omitted"] is True
  artifact = result["full_result_artifact"]
  with open(artifact["file_path"], newline="", encoding="utf-8") as csv_file:
    exported_rows = list(csv.DictReader(csv_file))
  metadata_row = next(
      row for row in exported_rows if row["result_type"] == "response_metadata"
  )
  assert json.loads(metadata_row["result"])["diagnostic"]["message"] == (
      "x" * 60_000
  )


def test_finalize_bounded_response_defers_exact_export_until_local_write(
    tmp_path,
    monkeypatch,
):
  items = [{"id": index, "payload": "x" * 1_000} for index in range(200)]
  campaign_context = {
      str(index): {"campaign.name": "name" * 40} for index in range(200)
  }
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  with (
      mock.patch.object(
          api,
          "get_ads_credential_cache_scope",
          return_value="scope",
      ),
      mock.patch.object(api, "write_rows_to_temp_csv") as mock_implicit_write,
  ):
    result = api.finalize_bounded_response(
        {
            "items": items,
            "campaign_context": campaign_context,
            "bulk_export_call": {
                "tool": "export_gaql_csv",
                "arguments": {
                    "snapshot_token": "gaql-snapshot-v1:" + ("a" * 32)
                },
            },
        },
        ("items", "campaign_context"),
    )

    mock_implicit_write.assert_not_called()
    assert (
        len(
            json.dumps(
                result, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )
        <= api.INLINE_RESPONSE_BYTE_LIMIT
    )
    assert result["complete_counts"] == {
        "items": 200,
        "campaign_context": 200,
    }
    assert result["bulk_export_call"]["tool"] == "export_gaql_csv"
    export_call = result["full_materialized_response_export"]["export_call"]
    exported = api.export_materialized_response_csv(**export_call["arguments"])

  assert exported["row_count"] == 400
  with open(exported["file_path"], newline="", encoding="utf-8") as csv_file:
    exported_rows = list(csv.DictReader(csv_file))
  assert sum(row["result_type"] == "items" for row in exported_rows) == 200
  context_rows = [
      row for row in exported_rows if row["result_type"] == "campaign_context"
  ]
  assert len(context_rows) == 200
  first_context = json.loads(context_rows[0]["result"])
  assert first_context["entry_key"] == "0"
  assert first_context["value"] == campaign_context["0"]


def test_finalize_bounded_response_bounds_omitted_metadata_field_names():
  response = {
      "items": [{"id": index} for index in range(200)],
      **{
          ("metadata_" + str(index) + "_" + "n" * 200): "x" * 5_000
          for index in range(100)
      },
  }
  with mock.patch.object(
      api,
      "get_ads_credential_cache_scope",
      return_value="scope",
  ):
    result = api.finalize_bounded_response(response, ("items",))

  delivery = result["shared_inline_delivery"]
  assert delivery["metadata_omitted_field_count"] == 100
  assert delivery["metadata_omitted_fields_truncated"] is True
  assert len(delivery["metadata_omitted_fields"]) <= 25
  assert (
      len(
          json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(
              "utf-8"
          )
      )
      <= api.INLINE_RESPONSE_BYTE_LIMIT
  )


def test_materialized_snapshot_is_credential_scoped_and_expires():
  rows = [{"result_type": "items", "result_index": 0, "result": "one"}]
  monotonic_time = [100.0]
  with (
      mock.patch.object(
          api,
          "get_ads_credential_cache_scope",
          return_value="scope-a",
      ),
      mock.patch.object(
          api.time,
          "monotonic",
          side_effect=lambda: monotonic_time[0],
      ),
  ):
    token = api._store_materialized_snapshot(rows)
    assert api._get_materialized_snapshot_rows(token) == rows

    with mock.patch.object(
        api,
        "get_ads_credential_cache_scope",
        return_value="scope-b",
    ):
      with pytest.raises(ToolError, match="different Google Ads credentials"):
        api._get_materialized_snapshot_rows(token)

    monotonic_time[0] += api._MATERIALIZED_SNAPSHOT_CACHE_TTL_SECONDS + 1
    with pytest.raises(ToolError, match="expired"):
      api._get_materialized_snapshot_rows(token)


def test_materialized_snapshot_cache_evicts_by_serialized_bytes(monkeypatch):
  monkeypatch.setattr(api, "_MATERIALIZED_SNAPSHOT_CACHE_MAX_BYTES", 2_500)
  with mock.patch.object(
      api,
      "get_ads_credential_cache_scope",
      return_value="scope",
  ):
    old_token = api._store_materialized_snapshot([{"result": "a" * 1_500}])
    new_rows = [{"result": "b" * 1_500}]
    new_token = api._store_materialized_snapshot(new_rows)

    with pytest.raises(ToolError, match="evicted"):
      api._get_materialized_snapshot_rows(old_token)
    assert api._get_materialized_snapshot_rows(new_token) == new_rows


def test_page_snapshot_cache_evicts_by_spool_bytes(monkeypatch, tmp_path):
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE_MAX_BYTES", 2_500)
  first_key = api._page_cache_key("SELECT first", "123", None)
  second_key = api._page_cache_key("SELECT second", "123", None)
  snapshots = []
  for index, marker in enumerate(("a", "b")):
    spool_path = tmp_path / f"snapshot-{index}.sqlite3"
    spool_path.write_bytes(marker.encode() * 1_500)
    snapshots.append(api._SpooledGaqlSnapshot(str(spool_path), 1, ()))
  with api._PAGED_QUERY_CACHE_LOCK:
    api._publish_page_snapshot_unlocked(
        first_key,
        "a" * 32,
        snapshots[0],
    )
    api._publish_page_snapshot_unlocked(
        second_key,
        "b" * 32,
        snapshots[1],
    )

  assert api._snapshot_cache_key(first_key, "a" * 32) not in (
      api._PAGED_QUERY_CACHE
  )
  assert api._snapshot_cache_key(second_key, "b" * 32) in (
      api._PAGED_QUERY_CACHE
  )


def test_snapshot_cache_keeps_composite_response_group_atomic(
    monkeypatch,
    tmp_path,
):
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE_MAX_BYTES", 2_500)
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE", 1)
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE_MAX_ENTRIES", 1)
  query_keys = [
      api._page_cache_key(f"SELECT {marker}", "123", None)
      for marker in ("first", "second", "later")
  ]
  snapshot_ids = ["a" * 32, "b" * 32, "c" * 32]
  snapshots = []
  for index, marker in enumerate(("a", "b", "c")):
    spool_path = tmp_path / f"grouped-snapshot-{index}.sqlite3"
    spool_path.write_bytes(marker.encode() * 1_500)
    snapshots.append(api._SpooledGaqlSnapshot(str(spool_path), 1, ()))

  with api.gaql_snapshot_group():
    with api._PAGED_QUERY_CACHE_LOCK:
      api._publish_page_snapshot_unlocked(
          query_keys[0],
          snapshot_ids[0],
          snapshots[0],
      )
      api._publish_page_snapshot_unlocked(
          query_keys[1],
          snapshot_ids[1],
          snapshots[1],
      )

  first_rows = api._get_export_snapshot_rows(
      api._encode_snapshot_token(snapshot_ids[0])
  )
  second_rows = api._get_export_snapshot_rows(
      api._encode_snapshot_token(snapshot_ids[1])
  )
  assert first_rows._snapshot is snapshots[0]
  assert second_rows._snapshot is snapshots[1]
  assert len(api._PAGED_QUERY_CACHE) == 2
  assert (
      len(
          {
              group_id
              for group_ids in api._PAGED_QUERY_SNAPSHOT_GROUPS.values()
              for group_id in group_ids
          }
      )
      == 1
  )

  with api._PAGED_QUERY_CACHE_LOCK:
    api._publish_page_snapshot_unlocked(
        query_keys[2],
        snapshot_ids[2],
        snapshots[2],
    )

  assert list(api._PAGED_QUERY_CACHE) == [
      api._snapshot_cache_key(query_keys[2], snapshot_ids[2])
  ]


def test_snapshot_cache_keeps_active_composite_group_past_ttl(
    monkeypatch,
    tmp_path,
):
  monotonic_time = [100.0]
  monkeypatch.setattr(
      api.time,
      "monotonic",
      lambda: monotonic_time[0],
  )
  first_key = api._page_cache_key("SELECT first", "123", None)
  second_key = api._page_cache_key("SELECT second", "123", None)
  first_id = "a" * 32
  second_id = "b" * 32
  snapshots = []
  for index, marker in enumerate(("a", "b")):
    spool_path = tmp_path / f"ttl-group-{index}.sqlite3"
    spool_path.write_bytes(marker.encode() * 100)
    snapshots.append(api._SpooledGaqlSnapshot(str(spool_path), 1, ()))

  with api.gaql_snapshot_group():
    with api._PAGED_QUERY_CACHE_LOCK:
      api._publish_page_snapshot_unlocked(
          first_key,
          first_id,
          snapshots[0],
      )
    monotonic_time[0] += api._PAGED_QUERY_CACHE_TTL_SECONDS + 1
    with api._PAGED_QUERY_CACHE_LOCK:
      api._publish_page_snapshot_unlocked(
          second_key,
          second_id,
          snapshots[1],
      )
    assert (
        api._get_export_snapshot_rows(
            api._encode_snapshot_token(first_id)
        )._snapshot
        is snapshots[0]
    )

  monotonic_time[0] += 1
  with pytest.raises(ToolError, match="expired"):
    api._get_export_snapshot_rows(api._encode_snapshot_token(first_id))


def test_snapshot_cache_evicts_overlapping_response_groups_atomically(
    monkeypatch,
    tmp_path,
):
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE", 1)
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE_MAX_ENTRIES", 1)
  query_keys = [
      api._page_cache_key(f"SELECT {index}", "123", None) for index in range(4)
  ]
  snapshot_ids = [character * 32 for character in "abcd"]
  snapshots = []
  for index, marker in enumerate("abcd"):
    spool_path = tmp_path / f"overlap-{index}.sqlite3"
    spool_path.write_bytes(marker.encode() * 100)
    snapshots.append(api._SpooledGaqlSnapshot(str(spool_path), 1, ()))

  with api.gaql_snapshot_group():
    with api._PAGED_QUERY_CACHE_LOCK:
      api._publish_page_snapshot_unlocked(
          query_keys[0], snapshot_ids[0], snapshots[0]
      )
      api._publish_page_snapshot_unlocked(
          query_keys[1], snapshot_ids[1], snapshots[1]
      )

  with api.gaql_snapshot_group():
    with api._PAGED_QUERY_CACHE_LOCK:
      assert (
          api._get_page_snapshot_unlocked(query_keys[1], snapshot_ids[1])
          is snapshots[1]
      )
      api._publish_page_snapshot_unlocked(
          query_keys[2], snapshot_ids[2], snapshots[2]
      )

  assert len(api._PAGED_QUERY_CACHE) == 3
  with api._PAGED_QUERY_CACHE_LOCK:
    api._publish_page_snapshot_unlocked(
        query_keys[3], snapshot_ids[3], snapshots[3]
    )

  assert list(api._PAGED_QUERY_CACHE) == [
      api._snapshot_cache_key(query_keys[3], snapshot_ids[3])
  ]


def test_mutation_artifact_failure_does_not_make_mutation_retryable():
  with mock.patch.object(
      api,
      "write_rows_to_temp_csv",
      side_effect=OSError("disk full"),
  ):
    result = api.build_bounded_mutation_response(
        {
            "resource_names": [
                f"customers/123/resources/{i}" for i in range(500)
            ]
        },
        ("resource_names",),
    )

  artifact = result["full_mutation_result_artifact"]
  assert artifact["available"] is False
  assert artifact["mutation_completed"] is True
  assert artifact["do_not_retry_mutation"] is True
  assert "Do not repeat" in artifact["recovery"]
  assert len(json.dumps(result).encode("utf-8")) <= (
      api.INLINE_RESPONSE_BYTE_LIMIT
  )


def test_mutation_artifact_reports_bounded_lifecycle_metadata():
  with mock.patch.object(api.threading, "Thread"):
    result = api.build_bounded_mutation_response(
        {
            "resource_names": [
                f"customers/123/resources/{i}" for i in range(500)
            ]
        },
        ("resource_names",),
    )

  artifact = result["full_mutation_result_artifact"]
  assert artifact["available"] is True
  assert artifact["automatic_cleanup"] is True
  assert artifact["expires_after_seconds"] == (
      api._MANAGED_TEMP_ARTIFACT_TTL_SECONDS
  )
  assert artifact["expires_at_epoch_seconds"] > api.time.time()
  assert artifact["may_be_evicted_earlier"] is True
  assert artifact["file_path"] in api._MANAGED_TEMP_ARTIFACTS


def test_snapshot_export_writes_all_rows_from_exact_original_snapshot(
    tmp_path,
    monkeypatch,
):
  original_rows = [
      {"campaign.id": "1", "campaign.name": "Original"},
      {"campaign.id": "2", "campaign.name": "Original"},
  ]
  changed_rows = [{"campaign.id": "9", "campaign.name": "Changed"}]
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      side_effect=[original_rows, changed_rows],
  ) as mock_run:
    page = api.run_gaql_query_page(
        "SELECT campaign.id, campaign.name FROM campaign",
        "123",
        page_size=1,
    )
    envelope = api.build_paginated_list_response(
        "campaigns",
        page["rows"],
        total_count=page["total_results_count"],
        page_size=1,
        next_page_token=page["next_page_token"],
        snapshot_token=page["snapshot_token"],
    )
    export_call = envelope["bulk_export_call"]
    result = api.export_gaql_csv(
        output_path=str(tmp_path / "snapshot.csv"),
        **export_call["arguments"],
    )

  mock_run.assert_called_once()
  assert result["row_count"] == 2
  assert result["total_row_count"] == 2
  assert result["truncated"] is False
  with open(result["file_path"], newline="", encoding="utf-8") as csv_file:
    assert list(csv.DictReader(csv_file)) == [
        {"campaign.id": "1", "campaign.name": "Original"},
        {"campaign.id": "2", "campaign.name": "Original"},
    ]


def test_complete_analysis_snapshot_exports_without_rerunning_query(
    tmp_path,
    monkeypatch,
):
  original_rows = [
      {"search_term_view.search_term": "alpha", "metrics.clicks": 10},
      {"search_term_view.search_term": "beta", "metrics.clicks": 5},
  ]
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      return_value=original_rows,
  ) as mock_run:
    snapshot = api.run_gaql_query_snapshot(
        "SELECT search_term_view.search_term FROM search_term_view",
        "123",
    )
    result = api.export_gaql_csv(
        snapshot_token=snapshot["snapshot_token"],
        output_path=str(tmp_path / "analysis.csv"),
    )

  mock_run.assert_called_once()
  assert snapshot["rows"] == original_rows
  assert result["row_count"] == 2
  with open(
      result["file_path"],
      newline="",
      encoding="utf-8",
  ) as export_file:
    csv_rows = list(csv.DictReader(export_file))
  assert [row["search_term_view.search_term"] for row in csv_rows] == [
      "alpha",
      "beta",
  ]


def test_active_spooled_rows_survive_cache_eviction():
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      return_value=rows,
  ):
    snapshot = api.run_gaql_query_snapshot(
        "SELECT campaign.id FROM campaign",
        "123",
    )
  spool_path = snapshot["rows"]._snapshot.file_path

  api._PAGED_QUERY_CACHE.clear()

  assert os.path.exists(spool_path)
  assert list(snapshot["rows"]) == rows


def test_spooled_local_sort_matches_previous_mixed_value_semantics():
  rows = [
      {"campaign.id": "none", "sort.value": None},
      {"campaign.id": "number", "sort.value": 10},
      {"campaign.id": "text", "sort.value": "2"},
      {"campaign.id": "missing"},
  ]
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      return_value=rows,
  ):
    snapshot = api.run_gaql_query_snapshot(
        "SELECT campaign.id FROM campaign",
        "123",
        row_sort_fields=("sort.value",),
    )

  assert [row["campaign.id"] for row in snapshot["rows"]] == [
      "missing",
      "number",
      "text",
      "none",
  ]


def test_empty_spooled_page_has_exact_zero_metadata():
  with mock.patch.object(api, "_iter_gaql_query_attempt", return_value=[]):
    page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=100,
    )

  assert page["rows"] == []
  assert page["total_results_count"] == 0
  assert page["total_page_count"] == 0
  assert page["next_page_token"] is None
  assert page["inline_bytes"] == 2


def test_snapshot_export_isolated_by_credential_scope():
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  with mock.patch.object(
      api,
      "_page_cache_scope",
      side_effect=["principal-a", "principal-b"],
  ):
    with mock.patch.object(api, "_iter_gaql_query_attempt", return_value=rows):
      page = api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "123",
          page_size=1,
      )
      with pytest.raises(
          api.ToolError,
          match="belongs to different Google Ads credentials",
      ):
        api.export_gaql_csv(snapshot_token=page["snapshot_token"])


def test_snapshot_export_expiry_has_actionable_restart_guidance():
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  with mock.patch.object(api, "_iter_gaql_query_attempt", return_value=rows):
    with mock.patch.object(
        api.time,
        "monotonic",
        side_effect=[100.0, 100.0, 191.0],
    ):
      page = api.run_gaql_query_page(
          "SELECT campaign.id FROM campaign",
          "123",
          page_size=1,
      )
      with pytest.raises(
          api.ToolError,
          match="Call the original list or report tool again",
      ):
        api.export_gaql_csv(snapshot_token=page["snapshot_token"])


def test_snapshot_export_eviction_has_actionable_restart_guidance():
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  with mock.patch.object(api, "_iter_gaql_query_attempt", return_value=rows):
    page = api.run_gaql_query_page(
        "SELECT campaign.id FROM campaign",
        "123",
        page_size=1,
    )
    for index in range(api._PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE):
      api.run_gaql_query_page(
          f"SELECT campaign.id FROM campaign WHERE campaign.id = {index}",
          "123",
          page_size=1,
      )

    with pytest.raises(
        api.ToolError,
        match="Call the original list or report tool again",
    ):
      api.export_gaql_csv(snapshot_token=page["snapshot_token"])


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


def test_managed_temp_artifact_expires_and_is_deleted(monkeypatch):
  monkeypatch.setattr(api, "_MANAGED_TEMP_ARTIFACT_TTL_SECONDS", 1.0)
  with mock.patch.object(api.threading, "Thread"):
    with mock.patch.object(api.time, "monotonic", side_effect=[0.0, 2.0]):
      file_path, _, _ = api.write_rows_to_temp_csv([{"id": "1"}])
      metadata = api.managed_temp_artifact_metadata(file_path)

  assert metadata["automatic_cleanup"] is True
  assert metadata["available"] is False
  assert not os.path.exists(file_path)
  assert file_path not in api._MANAGED_TEMP_ARTIFACTS


def test_managed_temp_artifact_count_bound_evicts_oldest(monkeypatch):
  monkeypatch.setattr(api, "_MANAGED_TEMP_ARTIFACT_MAX_ENTRIES", 1)
  with mock.patch.object(api.threading, "Thread"):
    first_path, _, _ = api.write_rows_to_temp_csv([{"id": "first"}])
    second_path, _, _ = api.write_rows_to_temp_csv([{"id": "second"}])

  assert not os.path.exists(first_path)
  assert os.path.exists(second_path)
  assert list(api._MANAGED_TEMP_ARTIFACTS) == [second_path]


def test_managed_temp_artifact_byte_bound_rejects_oversized_file(monkeypatch):
  monkeypatch.setattr(api, "_MANAGED_TEMP_ARTIFACT_MAX_BYTES", 1)
  with mock.patch.object(api.threading, "Thread"):
    with pytest.raises(api.ToolError, match="temporary-file byte budget"):
      api.write_rows_to_temp_csv([{"id": "too large"}])

  assert not api._MANAGED_TEMP_ARTIFACTS


def test_explicit_temp_export_is_not_registered_or_auto_evicted(monkeypatch):
  monkeypatch.setattr(api, "_MANAGED_TEMP_ARTIFACT_MAX_ENTRIES", 1)
  explicit_path, _, _ = api.write_rows_to_explicit_csv([{"id": "explicit"}])
  try:
    with mock.patch.object(api.threading, "Thread"):
      api.write_rows_to_temp_csv([{"id": "first"}])
      api.write_rows_to_temp_csv([{"id": "second"}])

    assert explicit_path not in api._MANAGED_TEMP_ARTIFACTS
    assert os.path.exists(explicit_path)
  finally:
    os.remove(explicit_path)


def test_explicit_overwrite_survives_delayed_managed_artifact_cleanup():
  with mock.patch.object(api.threading, "Thread"):
    managed_path, _, _ = api.write_rows_to_temp_csv([{"id": "managed"}])
  managed_entry = api._MANAGED_TEMP_ARTIFACTS[managed_path]
  delayed_cleanup = [(managed_path, managed_entry[3], managed_entry[4])]

  output_path, _, _ = api._write_csv_rows(
      [{"id": "explicit"}],
      resolved_output_path=managed_path,
      overwrite=True,
  )
  api._unlink_temp_artifacts(delayed_cleanup)

  try:
    assert output_path == managed_path
    assert managed_path not in api._MANAGED_TEMP_ARTIFACTS
    with open(managed_path, newline="", encoding="utf-8") as csv_file:
      assert list(csv.DictReader(csv_file)) == [{"id": "explicit"}]
  finally:
    os.remove(managed_path)


def test_merge_managed_fragments_preserves_first_row_and_unregisters():
  columns = ["id", "value"]
  with mock.patch.object(api.threading, "Thread"):
    first_path, _, _ = api.write_rows_to_temp_csv(
        [{"id": 1, "value": "first"}],
        columns=columns,
    )
    second_path, _, _ = api.write_rows_to_temp_csv(
        [{"id": 2, "value": "second"}],
        columns=columns,
    )
  output_path, _, _ = api.merge_temp_csv_files(
      [first_path, second_path],
      columns,
  )
  try:
    with open(output_path, newline="", encoding="utf-8") as csv_file:
      assert list(csv.reader(csv_file)) == [
          columns,
          ["1", "first"],
          ["2", "second"],
      ]
    assert first_path not in api._MANAGED_TEMP_ARTIFACTS
    assert second_path not in api._MANAGED_TEMP_ARTIFACTS
  finally:
    os.remove(output_path)


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
