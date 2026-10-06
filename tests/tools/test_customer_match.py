"""Offline Customer Match all-job aggregation and exact-delivery regressions."""

# pylint: disable=protected-access

import asyncio
import csv
import json
import re
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.api_core.exceptions import PermissionDenied
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.tools import _gaql
from ads_mcp.tools import api
from ads_mcp.tools import audiences

LIST_FIELD = (
    "offline_user_data_job.customer_match_user_list_metadata.user_list"
)
RATE_FIELD = "offline_user_data_job.operation_metadata.match_rate_range"


@pytest.fixture(autouse=True)
def isolated_snapshots(monkeypatch):
  """Keeps source and derived snapshot exports scoped to this test."""
  monkeypatch.setattr(
      api, "get_ads_credential_cache_scope", lambda: "match-tests"
  )
  monkeypatch.setattr(api, "_PAGED_QUERY_CACHE", api.OrderedDict())
  monkeypatch.setattr(api, "_PAGED_QUERY_LATEST", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_BUILDS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_SNAPSHOT_GROUPS", {})
  monkeypatch.setattr(api, "_PAGED_QUERY_GROUP_SNAPSHOTS", {})
  monkeypatch.setattr(api, "_MATERIALIZED_SNAPSHOT_CACHE", api.OrderedDict())


def _job(job_id, list_id="1", status="SUCCESS", rate="MATCH_RANGE_81_TO_90"):
  return {
      "offline_user_data_job.resource_name": (
          f"customers/123/offlineUserDataJobs/{job_id}"
      ),
      "offline_user_data_job.id": job_id,
      "offline_user_data_job.external_id": 900 + job_id,
      "offline_user_data_job.type": "CUSTOMER_MATCH_USER_LIST",
      "offline_user_data_job.status": status,
      "offline_user_data_job.failure_reason": "UNSPECIFIED",
      LIST_FIELD: f"customers/123/userLists/{list_id}",
      RATE_FIELD: rate,
      "user_list.resource_name": f"customers/123/userLists/{list_id}",
      "user_list.name": f"List {list_id}",
  }


def _summarize(rows, **kwargs):
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=rows
  ) as iterate:
    result = audiences.summarize_customer_match_jobs("123", **kwargs)
  iterate.assert_called_once()
  return result


def _export_source(result, tmp_path, monkeypatch):
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  call = result["bulk_export_call"]
  assert call["tool"] == "export_gaql_csv"
  with mock.patch.object(api, "_iter_gaql_query_attempt") as iterate:
    exported = api.export_gaql_csv(
        **call["arguments"], output_path=str(tmp_path / "source.csv")
    )
  iterate.assert_not_called()
  with open(exported["file_path"], encoding="utf-8", newline="") as stream:
    return exported, list(csv.DictReader(stream))


def test_all_job_counts_are_independent_of_numeric_id_preview():
  failed = _job(2, status="FAILED", rate="UNSPECIFIED")
  failed["offline_user_data_job.failure_reason"] = "UNKNOWN"
  rows = [
      _job(10),
      failed,
      _job(100, status="RUNNING"),
      _job(3),
      _job(7, list_id="2", status="PENDING", rate="UNSPECIFIED"),
  ]
  result = _summarize(rows, jobs_per_list=2)
  first, second = result["user_lists"]
  assert result["source_job_count"] == 5
  assert result["user_list_count"] == 2
  assert result["analysis_complete"] is True
  assert first["job_count"] == 4
  assert first["user_list_name"] == "List 1"
  assert first["status_counts"] == {"SUCCESS": 2, "FAILED": 1, "RUNNING": 1}
  assert first["match_rate_range_counts"] == {
      "MATCH_RANGE_81_TO_90": 3,
      "UNSPECIFIED": 1,
  }
  assert first["failure_reason_counts"] == {"UNKNOWN": 1}
  assert sum(group["job_count"] for group in first["diagnostic_groups"]) == 4
  assert [job["job_id"] for job in first["highest_id_jobs"]] == ["100", "10"]
  assert first["preview_covers_all_jobs"] is False
  assert second["preview_covers_all_jobs"] is True
  assert second["failure_reason_counts"] == {}
  assert result["status_counts"] == {
      "SUCCESS": 2,
      "FAILED": 1,
      "RUNNING": 1,
      "PENDING": 1,
  }
  assert result["recency"]["time_recency_available"] is False
  assert "HEURISTIC" in result["recency"]["preview_selection"]
  assert "do not establish chronology" in result["recency"]["explanation"]
  assert result["complete_inline"] is True
  assert result["truncated"] is False


def test_filtered_query_is_uncapped_and_valid_for_installed_v24():
  with mock.patch.object(
      audiences,
      "run_gaql_query_snapshot",
      return_value={"rows": [], "snapshot_token": "source-exact"},
  ) as run:
    audiences.summarize_customer_match_jobs(
        "00-01 23",
        '["02", "2", 10]',
        jobs_per_list=1,
        login_customer_id="00 04-56",
    )
  query, customer_id, manager_id = run.call_args.args
  assert (customer_id, manager_id) == ("123", "456")
  assert "user_list.id IN (2, 10)" in query
  assert "offline_user_data_job.type = CUSTOMER_MATCH_USER_LIST" in query
  assert "ORDER BY offline_user_data_job.id DESC" in query
  assert "LIMIT" not in query
  assert "create_time" not in query
  _gaql.preprocess_gaql_query(query)
  selected = re.search(r"SELECT\s+(.+?)\s+FROM", query).group(1)
  for field in selected.split(","):
    descriptor = GoogleAdsRow.pb().DESCRIPTOR
    for part in field.strip().split("."):
      python_part = part if part in descriptor.fields_by_name else part + "_"
      descriptor = descriptor.fields_by_name[python_part].message_type


@pytest.mark.parametrize(
    "arguments",
    [
        {"customer_id": "123 OR 1=1"},
        {"customer_id": "0"},
        {"customer_id": True},
        {"customer_id": 1.5},
        {"login_customer_id": "-1"},
        {"login_customer_id": "invalid"},
        {"jobs_per_list": True},
        {"jobs_per_list": 1.5},
        {"jobs_per_list": 0},
        {"jobs_per_list": 101},
        {"user_list_ids": []},
        {"user_list_ids": "[]"},
        {"user_list_ids": "["},
        {"user_list_ids": [True]},
        {"user_list_ids": ["0"]},
        {"user_list_ids": ["1); SELECT customer.id FROM customer"]},
    ],
)
def test_invalid_arguments_fail_before_client_or_snapshot(arguments):
  with (
      mock.patch.object(audiences, "run_gaql_query_snapshot") as run,
      mock.patch.object(api, "get_ads_client") as client,
  ):
    with pytest.raises(ToolError):
      audiences.summarize_customer_match_jobs(
          **{"customer_id": "123", **arguments}
      )
  run.assert_not_called()
  client.assert_not_called()


def test_empty_source_does_not_invent_user_lists_or_time_recency():
  result = _summarize([])
  assert result["source_job_count"] == result["user_list_count"] == 0
  assert result["user_lists"] == []
  assert result["status_counts"] == result["match_rate_range_counts"] == {}
  assert "Lists without returned jobs are absent" in result["methodology"]
  assert "historical completeness" in result["methodology"]


def test_missing_name_and_diagnostics_remain_explicit():
  row = _job(1)
  for field in (
      LIST_FIELD,
      RATE_FIELD,
      "user_list.resource_name",
      "user_list.name",
  ):
    del row[field]
  row["offline_user_data_job.status"] = None
  row["offline_user_data_job.failure_reason"] = ""
  result = _summarize([row])
  summary = result["user_lists"][0]
  assert summary["user_list_resource_name"] == "UNAVAILABLE"
  assert summary["user_list_name"] is None
  assert summary["status_counts"] == {"UNAVAILABLE": 1}
  assert summary["match_rate_range_counts"] == {"UNAVAILABLE": 1}
  assert summary["diagnostic_groups"] == [
      {
          "status": "UNAVAILABLE",
          "match_rate_range": "UNAVAILABLE",
          "failure_reason": "UNAVAILABLE",
          "job_count": 1,
      }
  ]


def test_attributed_name_cannot_be_attached_to_different_list():
  row = _job(1)
  row["user_list.resource_name"] = "customers/123/userLists/2"
  with pytest.raises(ToolError, match="different user-list name owner"):
    _summarize([row])


def test_protobuf_rows_and_centralized_error_handling():
  row = GoogleAdsRow(
      offline_user_data_job={
          "id": 10,
          "external_id": 7,
          "type_": "CUSTOMER_MATCH_USER_LIST",
          "status": "RUNNING",
          "resource_name": "customers/123/offlineUserDataJobs/10",
          "customer_match_user_list_metadata": {
              "user_list": "customers/123/userLists/1"
          },
          "operation_metadata": {"match_rate_range": "MATCH_RANGE_41_TO_50"},
      },
      user_list={
          "resource_name": "customers/123/userLists/1",
          "name": "Real SDK",
      },
  )
  batch = mock.Mock()
  batch.results = [row]
  batch.field_mask.paths = audiences._CUSTOMER_MATCH_JOB_FIELDS
  client = mock.Mock()
  client.get_service.return_value.search_stream.return_value = [batch]
  with mock.patch.object(api, "get_ads_client", return_value=client):
    result = audiences.summarize_customer_match_jobs("123")
  summary = result["user_lists"][0]
  assert summary["user_list_name"] == "Real SDK"
  assert summary["status_counts"] == {"RUNNING": 1}
  assert summary["match_rate_range_counts"] == {"MATCH_RANGE_41_TO_50": 1}
  assert summary["highest_id_jobs"][0]["job_id"] == "10"
  with mock.patch.object(
      audiences,
      "run_gaql_query_snapshot",
      side_effect=PermissionDenied("denied"),
  ):
    with pytest.raises(ToolError, match="denied"):
      audiences.summarize_customer_match_jobs("123")


def test_source_csv_is_exact_complete_immutable_and_credential_scoped(
    tmp_path, monkeypatch
):
  rows = [_job(index) for index in range(1, 7)]
  with mock.patch.object(api, "_write_csv_rows") as write:
    result = _summarize(rows, jobs_per_list=1)
  write.assert_not_called()
  result["user_lists"][0]["highest_id_jobs"][0]["status"] = "changed"
  exported, csv_rows = _export_source(result, tmp_path, monkeypatch)
  assert exported["row_count"] == 6
  assert exported["truncated"] is False
  assert csv_rows == [
      {key: str(value) for key, value in row.items()} for row in rows
  ]
  monkeypatch.setattr(api, "get_ads_credential_cache_scope", lambda: "other")
  with pytest.raises(ToolError, match="different Google Ads credentials"):
    api.export_gaql_csv(**result["bulk_export_call"]["arguments"])


def test_large_derived_response_is_bounded_with_exact_summary_export(
    tmp_path, monkeypatch
):
  rows = [_job(index, list_id=str(index)) for index in range(1, 81)]
  for row in rows:
    row["user_list.name"] = "🧭" * 500
  with mock.patch.object(api, "_write_csv_rows") as write:
    result = _summarize(rows)
  write.assert_not_called()
  assert api._serialized_json_bytes(result) <= api.INLINE_PAGE_BYTE_LIMIT
  assert result["source_job_count"] == 80
  assert result["analysis_complete"] is True
  assert result["truncated"] is True
  assert result["complete_inline"] is False
  exported, csv_rows = _export_source(result, tmp_path, monkeypatch)
  assert exported["row_count"] == len(csv_rows) == 80
  call = result["full_materialized_response_export"]["export_call"]
  assert call["tool"] == "export_materialized_response_csv"
  artifact = api.export_materialized_response_csv(
      **call["arguments"], output_path=str(tmp_path / "summaries.csv")
  )
  with open(artifact["file_path"], encoding="utf-8", newline="") as stream:
    summaries = [
        json.loads(row["result"])
        for row in csv.DictReader(stream)
        if row["result_type"] == "user_lists"
    ]
  assert len(summaries) == 80
  assert sum(summary["job_count"] for summary in summaries) == 80
  assert all(summary["user_list_name"] == "🧭" * 500 for summary in summaries)
  assert all(summary["preview_covers_all_jobs"] for summary in summaries)
  assert artifact["truncated"] is False


def test_registered_tool_survives_mcp_protocol_and_is_read_only():
  async def check():
    async with Client(mcp_server) as client:
      tools = await client.list_tools()
      tool = next(
          item
          for item in tools
          if item.name == "summarize_customer_match_jobs"
      )
      assert tool.annotations.readOnlyHint is True
      with mock.patch.object(
          api, "_iter_gaql_query_attempt", return_value=[_job(1)]
      ):
        response = await client.call_tool(
            "summarize_customer_match_jobs", {"customer_id": "123"}
        )
      assert not response.is_error
      assert response.structured_content["source_job_count"] == 1
      assert (
          response.structured_content["recency"]["time_recency_available"]
          is False
      )

  asyncio.run(check())
