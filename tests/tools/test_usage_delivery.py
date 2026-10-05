"""Offline regressions for bounded raw GAQL delivery and exact exports."""

# pylint: disable=protected-access

import asyncio
import csv
import json
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
import jsonschema
import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.tools import api

QUERY = "SELECT campaign.id, campaign.name FROM campaign"


@pytest.fixture(autouse=True)
def isolated_snapshots(monkeypatch):
  """Keeps export state credential-scoped and independent of other tests."""
  monkeypatch.setattr(
      api, "get_ads_credential_cache_scope", lambda: "usage-delivery"
  )
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE", api.OrderedDict())
  monkeypatch.setattr(api, "_PAGED_QUERY_LATEST", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_BUILDS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_SNAPSHOT_GROUPS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_GROUP_SNAPSHOTS", {})
  monkeypatch.setattr(api, "_MATERIALIZED_SNAPSHOT_CACHE", api.OrderedDict())


def read_export(response, tmp_path, monkeypatch):
  """Uses only the advertised call and reads its exact conventional CSV."""
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  call = response["bulk_export_call"]
  assert call["tool"] == "export_gaql_csv"
  with mock.patch.object(api, "run_gaql_query") as run:
    exported = api.export_gaql_csv(
        **call["arguments"], output_path=str(tmp_path / "exact.csv")
    )
  run.assert_not_called()
  with open(exported["file_path"], encoding="utf-8", newline="") as stream:
    return exported, list(csv.DictReader(stream))


@pytest.mark.parametrize("max_rows", [None, 400])
def test_multimegabyte_response_is_bounded_and_exactly_exportable(
    max_rows, tmp_path, monkeypatch
):
  rows = [
      {"campaign.id": str(index), "campaign.name": "x" * 16_000}
      for index in range(400)
  ]
  with (
      mock.patch.object(api, "run_gaql_query", return_value=rows) as run,
      mock.patch.object(api, "_write_csv_rows") as write,
      mock.patch.object(api, "_iter_gaql_query_attempt") as iterate,
  ):
    result = api.execute_gaql(
        QUERY, "123", max_rows=max_rows, warning_row_threshold=None
    )
  run.assert_called_once()
  iterate.assert_not_called()
  write.assert_not_called()
  assert api._serialized_json_bytes(result["data"]) <= 32_768
  assert api._serialized_json_bytes(result) <= api.INLINE_RESPONSE_BYTE_LIMIT
  assert result["complete_inline"] is False
  assert result["truncated"] is True
  assert result["total_row_count"] == 400
  assert result["export_row_count"] == 400
  assert "API order" in result["ordering_warning"]
  jsonschema.validate(result, api._EXECUTE_GAQL_OUTPUT_SCHEMA)
  exported, csv_rows = read_export(result, tmp_path, monkeypatch)
  assert exported["row_count"] == 400
  assert exported["truncated"] is False
  assert csv_rows == rows


def test_explicit_row_cap_keeps_the_full_retrieved_snapshot(
    tmp_path, monkeypatch
):
  rows = [{"campaign.id": str(index)} for index in range(6)]
  with mock.patch.object(api, "run_gaql_query", return_value=rows):
    result = api.execute_gaql(QUERY, "123", max_results=2)
  assert result["data"] == rows[:2]
  assert result["max_rows_applied"] == 2
  assert result["returned_row_count"] == 2
  assert result["total_row_count"] == 6
  assert "max_rows only caps inline" in result["export_scope"]
  exported, csv_rows = read_export(result, tmp_path, monkeypatch)
  assert exported["row_count"] == 6
  assert csv_rows == rows


def test_one_oversized_row_has_an_explicit_placeholder_and_exact_export(
    tmp_path, monkeypatch
):
  rows = [{"campaign.name": "🚆" * 12_000}]
  with mock.patch.object(api, "run_gaql_query", return_value=rows):
    result = api.execute_gaql(QUERY, "123")
  assert result["returned_row_count"] == 0
  assert result["represented_row_count"] == 1
  assert result["inline_omitted_row_count"] == 1
  assert api.is_inline_omission(result["data"][0])
  assert result["inline_bytes"] <= 32_768
  exported, csv_rows = read_export(result, tmp_path, monkeypatch)
  assert exported["row_count"] == 1
  assert csv_rows == rows


@pytest.mark.parametrize("ordered", [False, True])
def test_order_warning_uses_query_syntax_and_keeps_limit_scope(ordered):
  query = QUERY + " WHERE campaign.name != 'ORDER BY'"
  if ordered:
    query += " ORDER BY campaign.id"
  query += " LIMIT 4"
  with mock.patch.object(
      api,
      "run_gaql_query",
      return_value=[{"campaign.id": str(index)} for index in range(4)],
  ):
    result = api.execute_gaql(query, "123", max_rows=1)
  assert ("ordering_warning" in result) is not ordered
  assert "LIMIT" in result["export_scope"]
  assert result["total_row_count"] == 4


def test_small_complete_response_keeps_its_shape_and_avoids_snapshots():
  rows = [{"campaign.id": "123"}]
  with (
      mock.patch.object(api, "run_gaql_query", return_value=rows),
      mock.patch.object(api, "_build_spooled_gaql_snapshot") as build,
  ):
    assert api.execute_gaql(QUERY, "123") == {"data": rows}
  build.assert_not_called()


def test_snapshot_survives_two_query_exports_and_more_than_ten_minutes(
    tmp_path, monkeypatch
):
  now = [100.0]
  monkeypatch.setattr(api.time, "monotonic", lambda: now[0])
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  rows = [{"campaign.id": str(index)} for index in range(5)]
  with mock.patch.object(api, "run_gaql_query", return_value=rows) as run:
    result = api.execute_gaql(QUERY, "123", max_rows=1)
    now[0] += 60
    api.export_gaql_csv(QUERY, "123", str(tmp_path / "other-one.csv"))
    now[0] += 60
    api.export_gaql_csv(QUERY, "123", str(tmp_path / "other-two.csv"))
    now[0] = 701.0
    exported = api.export_gaql_csv(
        **result["bulk_export_call"]["arguments"],
        output_path=str(tmp_path / "surviving.csv"),
    )
  assert run.call_count == 3
  assert exported["row_count"] == 5
  assert exported["truncated"] is False
  assert result["snapshot_lifetime"]["expires_after_seconds"] == 900
  assert result["snapshot_lifetime"]["may_be_evicted_earlier"] is True
  now[0] = 1_001.0
  with pytest.raises(ToolError, match="expired"):
    api.export_gaql_csv(**result["bulk_export_call"]["arguments"])


def test_paged_and_materialized_snapshots_survive_ten_minutes(monkeypatch):
  now = [100.0]
  monkeypatch.setattr(api.time, "monotonic", lambda: now[0])
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  with mock.patch.object(api, "_iter_gaql_query_attempt", return_value=rows):
    page = api.run_gaql_query_page(QUERY, "123", page_size=1)
  envelope = api.build_paginated_list_response(
      "data",
      page["rows"],
      2,
      1,
      page["next_page_token"],
      page["snapshot_token"],
  )
  token = api._store_materialized_snapshot(rows)
  now[0] += 601
  assert list(api._get_export_snapshot_rows(page["snapshot_token"])) == rows
  assert api._get_materialized_snapshot_rows(token) == rows
  assert envelope["snapshot_lifetime"]["expires_after_seconds"] == 900
  now[0] += 300
  with pytest.raises(ToolError, match="expired"):
    api._get_materialized_snapshot_rows(token)


def test_raw_snapshot_is_immutable_and_credential_isolated(monkeypatch):
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  with mock.patch.object(api, "run_gaql_query", return_value=rows):
    result = api.execute_gaql(QUERY, "123", max_rows=1)
  result["data"][0]["campaign.id"] = "changed"
  token = result["bulk_export_call"]["arguments"]["snapshot_token"]
  assert list(api._get_export_snapshot_rows(token)) == [
      {"campaign.id": "1"},
      {"campaign.id": "2"},
  ]
  monkeypatch.setattr(api, "get_ads_credential_cache_scope", lambda: "other")
  with pytest.raises(ToolError, match="different Google Ads credentials"):
    api._get_export_snapshot_rows(token)


def test_bounded_response_survives_the_mcp_protocol():
  async def check():
    async with Client(mcp_server) as client:
      with mock.patch.object(
          api, "run_gaql_query", return_value=[{"campaign.name": "x" * 60_000}]
      ):
        result = await client.call_tool(
            "execute_gaql", {"query": QUERY, "customer_id": "123"}
        )
      assert not result.is_error
      response = result.structured_content
      assert response["returned_row_count"] == 0
      assert response["bulk_export_call"]["tool"] == "export_gaql_csv"
      jsonschema.validate(response, api._EXECUTE_GAQL_OUTPUT_SCHEMA)

  asyncio.run(check())


def test_oversized_metadata_keeps_exact_raw_rows_and_preparation_facts(
    tmp_path, monkeypatch
):
  rows = [{"campaign.name": "x" * 40_000}, {"campaign.name": "second"}]
  metadata = {
      "original_query": QUERY + " " * 60_000,
      "executed_query": QUERY,
      "query_adjustments": [{"field": "campaign.id"}],
  }
  with (
      mock.patch.object(api, "run_gaql_query", return_value=rows),
      mock.patch.object(
          api, "_prepare_public_gaql", return_value=(QUERY, metadata)
      ) as prepare,
  ):
    result = api.execute_gaql(QUERY, "123")
  prepare.assert_called_once()
  assert api._serialized_json_bytes(result) <= api.INLINE_RESPONSE_BYTE_LIMIT
  assert result["inline_bytes"] <= 32_768
  assert result["returned_row_count"] == sum(
      not api.is_inline_omission(row) for row in result["data"]
  )
  assert result["represented_row_count"] == len(result["data"])
  assert result["full_materialized_response_export"]["available"] is True
  token = result["bulk_export_call"]["arguments"]["snapshot_token"]
  snapshot = api._get_export_snapshot_rows(token)
  assert snapshot.query_metadata == metadata
  exported, csv_rows = read_export(result, tmp_path, monkeypatch)
  assert exported["row_count"] == 2
  assert csv_rows == rows


def test_smaller_whole_response_budget_preserves_all_setting_values(
    tmp_path, monkeypatch
):
  value = "x" * 35_000
  response = {"settings": {"campaign.id": "123", "notes": value}}
  assert 32_768 < api._serialized_json_bytes(response) < 49_152
  with mock.patch.object(api, "_write_csv_rows") as write:
    result = api.finalize_bounded_response(
        response, ("settings",), max_bytes=api.INLINE_PAGE_BYTE_LIMIT
    )
  write.assert_not_called()
  assert api._serialized_json_bytes(result) <= api.INLINE_PAGE_BYTE_LIMIT
  assert result["complete_counts"]["settings"] == 2
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  call = result["full_materialized_response_export"]["export_call"]
  exported = api.export_materialized_response_csv(
      **call["arguments"], output_path=str(tmp_path / "settings.csv")
  )
  with open(exported["file_path"], encoding="utf-8", newline="") as stream:
    csv_rows = list(csv.DictReader(stream))
  values = {
      record["entry_key"]: record["value"]
      for row in csv_rows
      if row["result_type"] == "settings"
      for record in [json.loads(row["result"])]
  }
  assert values == response["settings"]


@pytest.mark.parametrize("max_bytes", [True, 8191, 49_153, 32_768.0])
def test_invalid_whole_response_budgets_fail_before_capturing_snapshots(
    max_bytes,
):
  with mock.patch.object(api, "_store_materialized_snapshot") as store:
    with pytest.raises(ValueError, match="max_bytes must be an integer"):
      api.finalize_bounded_response(
          {"data": []}, ("data",), max_bytes=max_bytes
      )
  store.assert_not_called()
