"""Offline regressions for exact inline continuation of GAQL snapshots."""

# pylint: disable=protected-access

import asyncio
import csv
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
import jsonschema
import pytest

from ads_mcp.tools import api

QUERY = "SELECT campaign.id, campaign.name FROM campaign"


@pytest.fixture(autouse=True)
def isolated_snapshots(monkeypatch):
  """Keeps the immutable snapshot cache private to each test."""
  monkeypatch.setattr(
      api, "get_ads_credential_cache_scope", lambda: "inline-continuation"
  )
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE", api.OrderedDict())
  monkeypatch.setattr(api, "_PAGED_QUERY_LATEST", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_BUILDS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_SNAPSHOT_GROUPS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_GROUP_SNAPSHOTS", {})
  monkeypatch.setattr(api, "_MATERIALIZED_SNAPSHOT_CACHE", api.OrderedDict())


def _assert_page_bounds(response):
  assert api._serialized_json_bytes(response["data"]) <= 32_768
  assert api._serialized_json_bytes(response) <= api.INLINE_RESPONSE_BYTE_LIMIT
  assert response["represented_row_count"] == len(response["data"])
  assert response["returned_row_count"] == sum(
      not api.is_inline_omission(row) for row in response["data"]
  )
  jsonschema.validate(response, api._EXECUTE_GAQL_OUTPUT_SCHEMA)


def _follow(response):
  call = response["next_inline_call"]
  assert call["tool"] == "get_gaql_snapshot_page"
  return api.get_gaql_snapshot_page(**call["arguments"])


def test_nineteen_of_twenty_returns_last_row_without_requery_or_export(
    tmp_path, monkeypatch
):
  rows = [
      {"campaign.id": str(index), "campaign.name": "x" * 1_600}
      for index in range(20)
  ]
  with mock.patch.object(api, "run_gaql_query", return_value=rows) as run:
    first = api.execute_gaql(QUERY, "123")
  run.assert_called_once()
  assert first["returned_row_count"] == 19
  assert first["complete_inline"] is False
  assert first["next_row_offset"] == 19
  with (
      mock.patch.object(api, "run_gaql_query") as requery,
      mock.patch.object(api, "_prepare_public_gaql") as prepare,
      mock.patch.object(api, "_write_csv_rows") as write,
  ):
    last = _follow(first)
  requery.assert_not_called()
  prepare.assert_not_called()
  write.assert_not_called()
  assert last["data"] == rows[19:]
  assert last["row_offset"] == 19
  assert last["returned_row_count"] == 1
  assert last["total_row_count"] == last["export_row_count"] == 20
  assert last["complete_inline"] is True
  assert last["has_more"] is False
  assert last["next_row_offset"] is None
  assert last["next_inline_call"] is None
  assert "from row_offset onward" in last["inline_delivery_scope"]
  _assert_page_bounds(first)
  _assert_page_bounds(last)
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  exported = api.export_gaql_csv(
      **last["bulk_export_call"]["arguments"],
      output_path=str(tmp_path / "original.csv"),
  )
  with open(exported["file_path"], encoding="utf-8", newline="") as stream:
    assert list(csv.DictReader(stream)) == rows


def test_inline_continuation_survives_the_mcp_protocol():
  async def check():
    rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
    async with Client(api.mcp) as client:
      with mock.patch.object(api, "run_gaql_query", return_value=rows):
        first = await client.call_tool(
            "execute_gaql",
            {"query": QUERY, "customer_id": "123", "max_rows": 1},
        )
      call = first.structured_content["next_inline_call"]
      with mock.patch.object(api, "run_gaql_query") as run:
        last = await client.call_tool(call["tool"], call["arguments"])
      run.assert_not_called()
      assert not last.is_error
      assert last.structured_content["data"] == rows[1:]
      assert last.structured_content["complete_inline"] is True
      _assert_page_bounds(last.structured_content)

  asyncio.run(check())


@pytest.mark.parametrize("dispatch", ["direct", "proxy"])
@pytest.mark.parametrize(
    "invalid_arguments",
    [
        {"row_offset": True},
        {"row_offset": "1"},
        {"row_offset": 1.0},
        {"max_rows": True},
        {"max_rows": "1"},
        {"max_rows": 1.0},
    ],
)
def test_snapshot_protocol_does_not_coerce_offsets_or_row_caps(
    dispatch, invalid_arguments
):
  async def check():
    token = api._capture_gaql_response_snapshot(
        [{"campaign.id": "1"}, {"campaign.id": "2"}],
        QUERY,
        "123",
        None,
        {},
    )
    async with Client(api.mcp) as client:
      arguments = {"snapshot_token": token, **invalid_arguments}
      name = "get_gaql_snapshot_page"
      if dispatch == "proxy":
        arguments = {"name": name, "arguments": arguments}
        name = "call_tool"
      with mock.patch.object(api, "_get_export_snapshot_rows") as read:
        result = await client.call_tool(name, arguments, raise_on_error=False)
      assert result.is_error
      read.assert_not_called()

  asyncio.run(check())


@pytest.mark.parametrize("max_rows", [None, 7])
def test_multiple_inline_pages_preserve_every_row_in_source_order(max_rows):
  rows = [
      {"campaign.id": str(index), "campaign.name": "x" * 1_200}
      for index in range(240)
  ]
  with mock.patch.object(api, "run_gaql_query", return_value=rows):
    page = api.execute_gaql(QUERY, "123", max_rows=max_rows)
  delivered = []
  page_count = 0
  with mock.patch.object(api, "run_gaql_query") as run:
    while True:
      _assert_page_bounds(page)
      assert page["row_offset"] == len(delivered)
      assert page["total_row_count"] == len(rows)
      assert page["export_row_count"] == len(rows)
      delivered.extend(page["data"])
      page_count += 1
      if not page["has_more"]:
        break
      assert page["next_row_offset"] == len(delivered)
      page = _follow(page)
  run.assert_not_called()
  assert page_count > 2
  assert delivered == rows
  assert page["complete_inline"] is True


def test_snapshot_page_clamps_row_cap_and_allows_terminal_offset():
  rows = [{"campaign.id": str(index)} for index in range(205)]
  with mock.patch.object(api, "run_gaql_query", return_value=rows):
    first = api.execute_gaql(QUERY, "123", max_rows=1)
  token = first["bulk_export_call"]["arguments"]["snapshot_token"]
  page = api.get_gaql_snapshot_page(token, row_offset=1, max_rows=1_000)
  assert page["data"] == rows[1:101]
  assert page["next_inline_call"]["arguments"]["max_rows"] == 100
  terminal = api.get_gaql_snapshot_page(token, row_offset=205)
  assert terminal["data"] == []
  assert terminal["complete_inline"] is True
  assert terminal["has_more"] is False
  _assert_page_bounds(page)
  _assert_page_bounds(terminal)


def test_snapshot_is_immutable_and_cannot_be_redirected_to_another_account():
  original = [{"campaign.id": "account-a-1"}, {"campaign.id": "account-a-2"}]
  with mock.patch.object(api, "run_gaql_query", return_value=original):
    first = api.execute_gaql(QUERY, "123", max_rows=1, login_customer_id="789")
  first["data"][0]["campaign.id"] = "changed-after-capture"
  other_rows = [{"campaign.id": "account-b-1"}, {"campaign.id": "account-b-2"}]
  with mock.patch.object(api, "run_gaql_query", return_value=other_rows):
    api.execute_gaql(QUERY, "456", max_rows=1, login_customer_id="111")
  with mock.patch.object(api, "run_gaql_query") as run:
    continuation = _follow(first)
  run.assert_not_called()
  assert continuation["data"] == [{"campaign.id": "account-a-2"}]
  token = first["bulk_export_call"]["arguments"]["snapshot_token"]
  assert api.get_gaql_snapshot_page(token)["data"] == [
      {"campaign.id": "account-a-1"},
      {"campaign.id": "account-a-2"},
  ]


def test_snapshot_page_rejects_another_credential_scope(monkeypatch):
  with mock.patch.object(
      api, "run_gaql_query", return_value=[{"id": "1"}, {"id": "2"}]
  ):
    first = api.execute_gaql(QUERY, "123", max_rows=1)
  monkeypatch.setattr(api, "get_ads_credential_cache_scope", lambda: "other")
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="different Google Ads credentials"):
      _follow(first)
  run.assert_not_called()


@pytest.mark.parametrize("unavailable", ["expired", "evicted"])
def test_snapshot_page_fails_with_restart_guidance_without_requery(
    unavailable, monkeypatch
):
  now = [100.0]
  monkeypatch.setattr(api.time, "monotonic", lambda: now[0])
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE", 1)
  with mock.patch.object(
      api, "run_gaql_query", return_value=[{"id": "1"}, {"id": "2"}]
  ):
    first = api.execute_gaql(QUERY, "123", max_rows=1)
    if unavailable == "evicted":
      api.execute_gaql(QUERY, "456", max_rows=1)
    else:
      now[0] += 901
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="Call the original list or report"):
      _follow(first)
  run.assert_not_called()


@pytest.mark.parametrize("row_offset", [True, 1.5, "1", None, -1, 3])
def test_invalid_source_offsets_are_rejected_without_api_work(row_offset):
  token = api._capture_gaql_response_snapshot(
      [{"id": "1"}, {"id": "2"}], QUERY, "123", None, {}
  )
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="row_offset"):
      api.get_gaql_snapshot_page(token, row_offset=row_offset)
  run.assert_not_called()


@pytest.mark.parametrize("max_rows", [True, 1.5, 0, -1])
def test_invalid_snapshot_row_caps_are_rejected_before_cache_access(max_rows):
  with mock.patch.object(api, "_get_export_snapshot_rows") as read:
    with pytest.raises(ToolError, match="max_rows"):
      api.get_gaql_snapshot_page("unused", max_rows=max_rows)
  read.assert_not_called()


@pytest.mark.parametrize(
    "token", [None, True, "other", "gaql-snapshot-v1:abc"]
)
def test_invalid_snapshot_tokens_do_not_run_queries(token):
  with mock.patch.object(api, "run_gaql_query") as run:
    with pytest.raises(ToolError, match="Invalid snapshot_token"):
      api.get_gaql_snapshot_page(token)
  run.assert_not_called()


def test_empty_snapshot_has_complete_empty_delivery_and_exact_export():
  token = api._capture_gaql_response_snapshot([], QUERY, "123", None, {})
  page = api.get_gaql_snapshot_page(token)
  assert page["data"] == []
  assert page["returned_row_count"] == 0
  assert page["total_row_count"] == page["export_row_count"] == 0
  assert page["complete_inline"] is True
  assert page["has_more"] is False
  assert page["next_inline_call"] is None
  assert page["bulk_export_call"]["arguments"]["snapshot_token"] == token
  _assert_page_bounds(page)


def test_inline_completion_preserves_incomplete_source_coverage():
  rows = [{"campaign.id": "1"}, {"campaign.id": "2"}]
  metadata = {
      "executed_query": QUERY + " LIMIT 2",
      "query_result_limit": 2,
      "retention": {"unavailable_ranges": []},
  }
  with (
      mock.patch.object(api, "run_gaql_query", return_value=rows),
      mock.patch.object(
          api, "_prepare_public_gaql", return_value=(QUERY, metadata)
      ),
  ):
    first = api.execute_gaql(QUERY, "123", max_rows=1)
  with mock.patch.object(api._history, "add_result_coverage") as coverage:
    last = _follow(first)
  coverage.assert_not_called()
  assert first["source_complete"] is False
  assert last["source_complete"] is False
  assert last["requested_range_complete"] is False
  assert last["complete_inline"] is True
  assert last["total_row_count"] == 2


def test_oversized_row_is_explicit_and_does_not_skip_the_following_row():
  rows = [
      {"campaign.name": "first"},
      {"campaign.name": "🚆" * 12_000},
      {"campaign.name": "last"},
  ]
  with mock.patch.object(api, "run_gaql_query", return_value=rows):
    first = api.execute_gaql(QUERY, "123", max_rows=1)
  placeholder = _follow(first)
  assert placeholder["row_offset"] == 1
  assert placeholder["returned_row_count"] == 0
  assert placeholder["represented_row_count"] == 1
  assert placeholder["inline_omitted_row_count"] == 1
  assert placeholder["data"][0][api._INLINE_OMISSION_KEY]["row_index"] == 1
  assert placeholder["next_row_offset"] == 2
  assert placeholder["complete_inline"] is False
  assert "bulk_export_call" in placeholder["inline_omission_note"]
  last = _follow(placeholder)
  assert last["data"] == rows[2:]
  assert last["has_more"] is False
  assert "check every page" in last["inline_delivery_scope"]
  token = last["bulk_export_call"]["arguments"]["snapshot_token"]
  assert list(api._get_export_snapshot_rows(token)) == rows
  _assert_page_bounds(placeholder)
  _assert_page_bounds(last)


def test_whole_response_bounding_updates_offset_without_skipping_rows(
    monkeypatch,
):
  monkeypatch.setattr(api, "INLINE_RESPONSE_BYTE_LIMIT", 12 * 1024)
  rows = [
      {"campaign.id": str(index), "campaign.name": "x" * 1_200}
      for index in range(25)
  ]
  metadata = {
      "original_query": QUERY + " " * 60_000,
      "executed_query": QUERY,
      "retention": {
          "source_coverage": "captured-original-coverage",
          "unavailable_ranges": [],
      },
  }
  with (
      mock.patch.object(api, "run_gaql_query", return_value=rows),
      mock.patch.object(
          api, "_prepare_public_gaql", return_value=(QUERY, metadata)
      ),
  ):
    page = api.execute_gaql(QUERY, "123")
  assert page["returned_row_count"] < len(rows)
  delivered = []
  with (
      mock.patch.object(api, "run_gaql_query") as run,
      mock.patch.object(api, "_prepare_public_gaql") as prepare,
  ):
    for _ in range(len(rows) + 1):
      _assert_page_bounds(page)
      assert page["row_offset"] == len(delivered)
      assert page["retention"] == metadata["retention"]
      delivered.extend(page["data"])
      if not page["has_more"]:
        break
      assert page["data"], "Continuation must make forward progress."
      assert page["next_row_offset"] == len(delivered)
      page = _follow(page)
    else:
      pytest.fail("Inline continuation failed to reach the last source row.")
  run.assert_not_called()
  prepare.assert_not_called()
  assert delivered == rows
  assert page["total_row_count"] == 25
  assert page["complete_inline"] is True
