# Copyright 2026 Google LLC
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

"""Explicit-window asset-group performance and exact snapshot continuation."""

import csv
from pathlib import Path
import re
from types import SimpleNamespace
from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
from google.api_core.exceptions import PermissionDenied
from google.protobuf.field_mask_pb2 import FieldMask
import pytest

from ads_mcp.tools import api
from ads_mcp.tools import reporting
from ads_mcp.tools._gaql import preprocess_gaql_query

WINDOW = {"start_date": "2026-10-01", "end_date": "2026-10-21"}


@pytest.fixture(autouse=True)
def offline_credentials():
  with mock.patch.object(
      api, "get_ads_credential_cache_scope", return_value="asset-group-report"
  ):
    yield


def _selected_fields(query):
  return [
      field.strip()
      for field in re.search(r"SELECT\s+(.+?)\s+FROM", query, re.I | re.S)[
          1
      ].split(",")
  ]


def test_asset_group_report_builds_compatible_query_with_explicit_filters():
  with mock.patch.object(
      reporting,
      "run_gaql_query_page",
      return_value={
          "rows": [],
          "total_results_count": 0,
          "next_page_token": None,
      },
  ) as read:
    result = reporting.get_asset_group_performance(
        "123-456-7890",
        WINDOW,
        campaign_ids='["11", "12"]',
        asset_group_ids=["21"],
        limit=50,
        page_token="next-page",
        login_customer_id="999",
    )
  query = read.call_args.kwargs["query"]
  preprocess_gaql_query(query)
  for field in _selected_fields(query):
    descriptor = GoogleAdsRow.pb().DESCRIPTOR
    for component in field.split("."):
      member = descriptor.fields_by_name.get(component)
      if member is None:
        member = descriptor.fields_by_name[component + "_"]
      descriptor = member.message_type
  assert "FROM asset_group" in query
  assert "segments.date BETWEEN '2026-10-01' AND '2026-10-21'" in query
  assert "campaign.id IN (11, 12)" in query
  assert "asset_group.id IN (21)" in query
  assert (
      "ORDER BY metrics.cost_micros DESC, campaign.id, asset_group.id" in query
  )
  assert " LIMIT " not in query.upper()
  assert "metrics.cost_micros" in query
  assert "metrics.conversions_value" in query
  assert read.call_args.kwargs["customer_id"] == "1234567890"
  assert read.call_args.kwargs["page_size"] == 50
  assert read.call_args.kwargs["page_token"] == "next-page"
  assert read.call_args.kwargs["login_customer_id"] == "999"
  assert result["date_range"] == WINDOW
  assert result["asset_group_performance"] == []


@pytest.mark.parametrize(
    "date_range",
    [
        "LAST_30_DAYS",
        "ALL_TIME",
        {},
        {"start_date": "2026-10-01"},
        {"start_date": "2026-10-22", "end_date": "2026-10-01"},
        {"start_date": "2026-02-30", "end_date": "2026-10-01"},
        {"start_date": "2026-10-01' OR 1=1", "end_date": "2026-10-21"},
    ],
)
def test_asset_group_report_rejects_invalid_window_before_network(date_range):
  with mock.patch.object(reporting, "run_gaql_query_page") as read:
    with pytest.raises(ToolError):
      reporting.get_asset_group_performance("123", date_range)
  read.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"campaign_ids": ["1 OR 1=1"]},
        {"asset_group_ids": [True]},
        {"limit": 0},
    ],
)
def test_asset_group_report_rejects_bad_filters_before_network(kwargs):
  with mock.patch.object(reporting, "run_gaql_query_page") as read:
    with pytest.raises(ToolError):
      reporting.get_asset_group_performance("123", WINDOW, **kwargs)
  read.assert_not_called()


def test_asset_group_report_maps_service_errors():
  with mock.patch.object(
      reporting,
      "run_gaql_query_page",
      side_effect=PermissionDenied("offline denied"),
  ):
    with pytest.raises(ToolError, match="offline denied"):
      reporting.get_asset_group_performance("123", WINDOW)


def test_asset_group_report_pages_and_exports_the_same_captured_rows(
    tmp_path, monkeypatch
):
  source = [
      GoogleAdsRow(
          campaign={"id": 1, "name": "PMax", "status": "ENABLED"},
          asset_group={
              "id": i,
              "name": f"Group {i}",
              "status": "ENABLED",
          },
          metrics={
              "cost_micros": i * 1_000_000,
              "conversions": i,
              "conversions_value": i * 4,
          },
      )
      for i in (3, 2, 1)
  ]

  def _stream(*, query, customer_id):
    assert customer_id == "123"
    preprocess_gaql_query(query)
    return [
        SimpleNamespace(
            results=source,
            field_mask=FieldMask(paths=_selected_fields(query)),
        )
    ]

  client = mock.Mock()
  stream = client.get_service.return_value.search_stream
  stream.side_effect = _stream
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  with mock.patch.object(api, "get_ads_client", return_value=client):
    first = reporting.get_asset_group_performance("123", WINDOW, limit=2)
    assert first["total_count"] == 3
    assert first["returned_count"] == 2
    assert first["has_more"] is True
    second = reporting.get_asset_group_performance(
        "123", WINDOW, limit=2, page_token=first["next_page_token"]
    )
    assert second["returned_count"] == 1
    assert second["has_more"] is False
    rows = first["asset_group_performance"] + second["asset_group_performance"]
    assert [row["asset_group.id"] for row in rows] == [3, 2, 1]
    assert second["bulk_export_call"] == first["bulk_export_call"]
    exported = api.export_gaql_csv(
        **first["bulk_export_call"]["arguments"],
        output_path=str(tmp_path / "asset-groups.csv"),
    )
    stream.assert_called_once()
  with Path(exported["file_path"]).open(encoding="utf-8", newline="") as file:
    captured = list(csv.DictReader(file))
  assert exported["row_count"] == len(captured) == 3
  assert [row["asset_group.id"] for row in captured] == ["3", "2", "1"]
  assert captured[-1]["metrics.conversions_value"] == "4.0"
