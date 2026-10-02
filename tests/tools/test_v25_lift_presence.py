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

"""Missing native lift observations stay distinct from explicit zero values."""

# pylint: disable=protected-access

import csv
from pathlib import Path
import re
from types import SimpleNamespace
from unittest import mock

from google.ads.googleads.v25.common.types.metrics import Metrics
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
from google.protobuf.field_mask_pb2 import FieldMask
import pytest

from ads_mcp.tools import api
from ads_mcp.tools import reporting


LIFT_FIELDS = (
    reporting._BRAND_LIFT_METRICS + reporting._CONVERSION_LIFT_METRICS
)
QUERY = (
    "SELECT lift_measurement_config.lift_measurement_config_id, "
    "metrics.absolute_brand_lift_p_value, "
    "metrics.absolute_brand_lift_p90_lower_bound "
    "FROM lift_measurement_config"
)
QUERY_FIELDS = [
    "lift_measurement_config.lift_measurement_config_id",
    "metrics.absolute_brand_lift_p_value",
    "metrics.absolute_brand_lift_p90_lower_bound",
]


@pytest.fixture(autouse=True)
def isolated_snapshots(monkeypatch):
  """Keeps mocked read snapshots independent of credentials and other tests."""
  monkeypatch.setattr(
      api, "get_ads_credential_cache_scope", lambda: "v25-lift-presence"
  )
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE", api.OrderedDict())
  monkeypatch.setattr(api, "_PAGED_QUERY_LATEST", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_BUILDS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_SNAPSHOT_GROUPS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_GROUP_SNAPSHOTS", {})


def test_presence_scope_matches_only_the_59_native_lift_metrics():
  descriptor = Metrics.pb().DESCRIPTOR
  matched_fields = {
      f"metrics.{name}"
      for name in descriptor.fields_by_name
      if name.startswith(api._LIFT_METRIC_PREFIXES)
  }
  assert matched_fields == set(LIFT_FIELDS)
  assert len(matched_fields) == 59
  assert all(
      descriptor.fields_by_name[field.removeprefix("metrics.")].has_presence
      for field in matched_fields
  )
  assert "metrics.optimization_score_uplift" not in matched_fields


@pytest.mark.parametrize("protobuf_row", [False, True])
def test_all_optional_lift_metrics_distinguish_absence_from_present_zero(
    protobuf_row,
):
  absent = GoogleAdsRow()
  present_zero = GoogleAdsRow(
      metrics={field.removeprefix("metrics."): 0 for field in LIFT_FIELDS}
  )
  if protobuf_row:
    absent = GoogleAdsRow.pb(absent)
    present_zero = GoogleAdsRow.pb(present_zero)
  for field in LIFT_FIELDS:
    assert api._extract_gaql_field_value(absent, field) is None
    assert api._extract_gaql_field_value(present_zero, field) == 0
  # Established default behavior for traffic and unrelated metrics stays intact.
  assert api._extract_gaql_field_value(absent, "metrics.impressions") == 0
  assert (
      api._extract_gaql_field_value(
          absent, "metrics.optimization_score_uplift"
      )
      == 0
  )


def test_non_native_mock_rows_retain_existing_conversion_behavior():
  row = SimpleNamespace(
      metrics=SimpleNamespace(absolute_brand_lift_p_value=0.0)
  )
  assert (
      api._extract_gaql_field_value(row, "metrics.absolute_brand_lift_p_value")
      == 0.0
  )


@pytest.mark.parametrize("protobuf_row", [False, True])
def test_list_and_streaming_converters_have_identical_presence_semantics(
    protobuf_row,
):
  rows = [
      GoogleAdsRow(lift_measurement_config={"lift_measurement_config_id": 1}),
      GoogleAdsRow(
          lift_measurement_config={"lift_measurement_config_id": 2},
          metrics={"absolute_brand_lift_p_value": 0},
      ),
      GoogleAdsRow(
          lift_measurement_config={"lift_measurement_config_id": 3},
          metrics={
              "absolute_brand_lift_p_value": 0.07,
              "absolute_brand_lift_p90_lower_bound": -0.1,
          },
      ),
  ]
  if protobuf_row:
    rows = [GoogleAdsRow.pb(row) for row in rows]
  batches = [
      SimpleNamespace(results=rows, field_mask=FieldMask(paths=QUERY_FIELDS))
  ]
  expected = api.gaql_results_to_dicts(batches)
  client = mock.Mock()
  client.get_service.return_value.search_stream.return_value = batches
  with mock.patch.object(api, "get_ads_client", return_value=client):
    streamed = list(api._iter_gaql_query_attempt(QUERY, "123"))
  assert streamed == expected
  assert streamed[0]["metrics.absolute_brand_lift_p_value"] is None
  assert streamed[1]["metrics.absolute_brand_lift_p_value"] == 0
  assert streamed[1]["metrics.absolute_brand_lift_p90_lower_bound"] is None
  assert streamed[2]["metrics.absolute_brand_lift_p_value"] == 0.07
  assert streamed[2]["metrics.absolute_brand_lift_p90_lower_bound"] == -0.1


@pytest.mark.parametrize(
    "measurement_type,metric_names",
    [
        (
            "BRAND",
            [
                "absolute_brand_lift_p_value",
                "absolute_brand_lift_p90_lower_bound",
            ],
        ),
        (
            "CONVERSION",
            [
                "incremental_conversions_p_value",
                "incremental_conversions_p90_lower_bound",
                "incremental_conversions_winner_score",
            ],
        ),
    ],
)
def test_lift_tool_and_exact_csv_preserve_missing_and_zero_statistics(
    measurement_type, metric_names, tmp_path, monkeypatch
):
  source_rows = [
      GoogleAdsRow(lift_measurement_config={"lift_measurement_config_id": 1}),
      GoogleAdsRow(
          lift_measurement_config={"lift_measurement_config_id": 2},
          metrics={name: 0 for name in metric_names},
      ),
  ]

  def _stream(*, query, customer_id):
    assert customer_id == "123"
    fields = [
        field.strip()
        for field in re.search(r"SELECT\s+(.+?)\s+FROM", query, re.S)[1].split(
            ","
        )
    ]
    return [
        SimpleNamespace(
            results=source_rows, field_mask=FieldMask(paths=fields)
        )
    ]

  client = mock.Mock()
  client.get_service.return_value.search_stream.side_effect = _stream
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  with mock.patch.object(api, "get_ads_client", return_value=client):
    result = reporting.list_lift_measurements(
        "123", measurement_type=measurement_type
    )
    assert result["total_count"] == 2
    for name in metric_names:
      assert result["lift_measurements"][0][f"metrics.{name}"] is None
      assert result["lift_measurements"][1][f"metrics.{name}"] == 0
    with mock.patch.object(api, "run_gaql_query") as refetch:
      artifact = api.export_gaql_csv(
          **result["bulk_export_call"]["arguments"],
          output_path=str(tmp_path / "lift.csv"),
      )
    refetch.assert_not_called()
  with Path(artifact["file_path"]).open(
      encoding="utf-8", newline=""
  ) as stream:
    csv_rows = list(csv.DictReader(stream))
  assert artifact["row_count"] == len(csv_rows) == 2
  for name in metric_names:
    assert csv_rows[0][f"metrics.{name}"] == ""
    assert csv_rows[1][f"metrics.{name}"] == "0.0"
  client.get_service.return_value.search_stream.assert_called_once()


def test_lift_no_data_does_not_fabricate_statistics():
  client = mock.Mock()
  client.get_service.return_value.search_stream.return_value = []
  with mock.patch.object(api, "get_ads_client", return_value=client):
    result = reporting.list_lift_measurements("123", measurement_type="BRAND")
  assert result["lift_measurements"] == []
  assert result["returned_count"] == result["total_count"] == 0
