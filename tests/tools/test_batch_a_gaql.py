"""Usage-report regressions for narrow GAQL repairs and recovery hints."""

import csv
import json
import asyncio
from datetime import date
from unittest import mock

from fastmcp.exceptions import ToolError
from fastmcp import Client
import jsonschema
import pytest

from ads_mcp.tools import _gaql
from ads_mcp.tools import api
from ads_mcp.tools import changes
from ads_mcp.coordinator import mcp_server


SHOPPING_QUERY = (
    "SELECT segments.product_item_id, metrics.cost_micros, metrics.clicks "
    "FROM shopping_performance_view WHERE campaign.id = 123 "
    "AND segments.date BETWEEN '2026-05-25' AND '2026-06-21' "
    "ORDER BY metrics.cost_micros DESC"
)


@pytest.fixture(autouse=True)
def credential_scope():
  with mock.patch.object(
      api, "get_ads_credential_cache_scope", return_value="batch-a-tests"
  ):
    yield


@pytest.mark.parametrize(
    "start,end", [("2026-05-25", "2026-06-21"), ("2026-06-25", "2026-07-22")]
)
def test_shopping_filter_adds_only_required_campaign_segment(start, end):
  query = SHOPPING_QUERY.replace("2026-05-25", start).replace(
      "2026-06-21", end
  )
  prepared = _gaql.preprocess_gaql_query(query)
  assert prepared.split("FROM")[0].strip() == (
      "SELECT segments.product_item_id, metrics.cost_micros, metrics.clicks, "
      "campaign.id"
  )
  assert _gaql.preprocess_gaql_query(prepared) == prepared


def test_required_field_rewrite_does_not_select_ordinary_attributes():
  query = "SELECT campaign.id FROM ad_group WHERE ad_group.status = ENABLED"
  assert (
      "ad_group.status"
      not in _gaql.preprocess_gaql_query(query).split("FROM")[0]
  )


@pytest.mark.parametrize(
    "field,resource,alternative",
    [
        (
            "asset_group_asset.performance_label",
            "asset_group_asset",
            "asset_group_asset.primary_status",
        ),
        (
            "campaign.url_expansion_opt_out",
            "campaign",
            "campaign.asset_automation_settings",
        ),
    ],
)
def test_removed_fields_have_inline_non_equivalent_alternatives(
    field, resource, alternative
):
  with pytest.raises(ToolError) as error:
    _gaql.preprocess_gaql_query(f"SELECT {field} FROM {resource}")
  assert alternative in str(error.value)
  assert "not equivalent" in str(error.value)
  assert "v24" in str(error.value)


def test_unknown_future_field_passes_but_known_invalid_fields_do_not():
  query = "SELECT campaign.future_valid_field FROM campaign"
  assert "campaign.future_valid_field" in _gaql.preprocess_gaql_query(query)
  with pytest.raises(ToolError, match="not compatible"):
    _gaql.preprocess_gaql_query(
        "SELECT metrics.clicks FROM campaign_criterion"
    )
  with pytest.raises(ToolError, match="not selectable"):
    _gaql.preprocess_gaql_query(
        "SELECT segments.new_versus_returning_customers, "
        "metrics.cost_micros FROM campaign"
    )


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
@pytest.mark.parametrize(
    "query",
    [
        "SELECT metrics.unique_users, segments.future_valid_field "
        "FROM campaign",
        "SELECT metrics.future_valid_field, segments.device FROM campaign",
        "SELECT metrics.future_valid_field, segments.future_valid_field "
        "FROM campaign",
        "SELECT metrics.unique_users FROM campaign "
        "WHERE segments.future_valid_field = 'VALUE' "
        "ORDER BY segments.future_valid_field",
    ],
)
def test_unknown_pairwise_fields_reach_service_unchanged(
    tool_name, query, tmp_path
):
  kwargs = (
      {"output_path": str(tmp_path / "future.csv")}
      if tool_name == "export_gaql_csv"
      else {}
  )
  with mock.patch.object(api, "get_ads_client") as client:
    service = client.return_value.get_service.return_value
    service.search_stream.return_value = []
    getattr(api, tool_name)(query, "123", **kwargs)
  service.search_stream.assert_called_once_with(
      query=query + " PARAMETERS omit_unselected_resource_names=true",
      customer_id="123",
  )


@pytest.mark.parametrize(
    "selected_fields,error",
    [
        (
            "metrics.cost_micros, segments.new_versus_returning_customers",
            "not selectable",
        ),
        ("metrics.unique_users, segments.conversion_action", "not selectable"),
        ("campaign.url_expansion_opt_out", "unavailable in v24"),
    ],
)
def test_unknown_fields_do_not_bypass_known_invalid_checks(
    selected_fields, error
):
  query = (
      f"SELECT {selected_fields}, metrics.future_valid_field, "
      "segments.future_valid_field FROM campaign"
  )
  with mock.patch.object(api, "get_ads_client") as client:
    with pytest.raises(ToolError, match=error):
      api.execute_gaql(query, "123")
  client.assert_not_called()


def test_exact_known_field_suggests_verified_from_resource():
  with pytest.raises(ToolError) as error:
    _gaql.preprocess_gaql_query("SELECT ad_group.id FROM campaign")
  assert "FROM ad_group" in str(error.value)


@pytest.mark.parametrize("operator", ["=", "!=", "<>"])
@pytest.mark.parametrize("literal", ["''", '""'])
def test_empty_resource_reference_rejected_without_executing_broader_query(
    operator, literal
):
  query = (
      "SELECT asset_group_signal.audience.audience FROM asset_group_signal "
      f"WHERE asset_group_signal.audience.audience {operator} {literal} "
      "AND campaign.id = 123"
  )
  with mock.patch.object(api, "get_ads_client") as client:
    with pytest.raises(ToolError) as error:
      api.execute_gaql(query, "123")
  client.assert_not_called()
  assert "empty resource" in str(error.value).lower()
  assert "broader" in str(error.value).lower()
  assert "client-side" in str(error.value)
  assert "WHERE campaign.id = 123" in str(error.value)


def test_empty_text_and_query_text_in_literals_remain_valid():
  for query in (
      "SELECT campaign.name FROM campaign WHERE campaign.name != ''",
      "SELECT campaign.name FROM campaign WHERE campaign.name = "
      "\"asset_group_signal.audience.audience != ''\"",
  ):
    assert query in _gaql.preprocess_gaql_query(query)


def test_execute_and_export_report_identical_required_select_adjustments(
    tmp_path,
):
  with mock.patch.object(
      api, "run_gaql_query", return_value=[{"campaign.id": "123"}]
  ):
    result = api.execute_gaql(SHOPPING_QUERY, "123")
    exported = api.export_gaql_csv(
        SHOPPING_QUERY, "123", str(tmp_path / "shopping.csv")
    )
  assert result["query_adjustments"] == exported["query_adjustments"]
  assert result["query_adjustments"][0]["field"] == "campaign.id"
  assert "campaign.id" in result["executed_query"].split("FROM")[0]
  assert result["original_query"] == SHOPPING_QUERY


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
def test_metadata_free_query_is_preprocessed_at_service_boundary(
    tool_name, tmp_path
):
  query = (
      "SELECT campaign.id FROM campaign WHERE campaign.status = 'enabled' "
      "AND segments.date DURING LAST_90_DAYS"
  )
  kwargs = (
      {"output_path": str(tmp_path / "normalized.csv")}
      if tool_name == "export_gaql_csv"
      else {}
  )
  with (
      mock.patch.object(api, "get_ads_client") as client,
      mock.patch.object(
          _gaql,
          "_literal_date_bounds",
          return_value=(date(2026, 5, 15), date(2026, 8, 12)),
      ),
  ):
    service = client.return_value.get_service.return_value
    service.search_stream.return_value = []
    result = getattr(api, tool_name)(
        query, "123", login_customer_id="456", **kwargs
    )
  assert "query_adjustments" not in result
  client.assert_called_once_with("456")
  service.search_stream.assert_called_once_with(
      query=(
          "SELECT campaign.id FROM campaign WHERE campaign.status = ENABLED "
          "AND segments.date BETWEEN '2026-05-15' AND '2026-08-12' "
          "PARAMETERS omit_unselected_resource_names=true"
      ),
      customer_id="123",
  )


def test_adjustment_metadata_is_bounded_and_exactly_exportable(tmp_path):
  query = SHOPPING_QUERY + " " * 60000
  with mock.patch.object(api, "run_gaql_query", return_value=[]):
    result = api.execute_gaql(query, "123")
  assert len(json.dumps(result).encode()) <= api.INLINE_RESPONSE_BYTE_LIMIT
  jsonschema.validate(
      result, api._EXECUTE_GAQL_OUTPUT_SCHEMA  # pylint: disable=protected-access
  )
  export_call = result["full_materialized_response_export"]["export_call"]
  exported = api.export_materialized_response_csv(
      **export_call["arguments"], output_path=str(tmp_path / "metadata.csv")
  )
  with open(exported["file_path"], encoding="utf-8") as stream:
    rows = list(csv.DictReader(stream))
  metadata = next(
      row for row in rows if row["result_type"] == "response_metadata"
  )
  assert json.loads(metadata["result"])["original_query"] == query


def test_query_safety_tools_expose_retention_enum_and_preserve_annotations():
  async def check():
    async with Client(mcp_server) as client:
      tools = {tool.name: tool for tool in await client.list_tools()}
      for function in (
          api.execute_gaql,
          api.export_gaql_csv,
          changes.list_change_events,
      ):
        policy = tools[function.__name__].inputSchema["properties"][
            "retention_policy"
        ]
        assert policy["enum"] == ["error", "clamp"]
        assert policy["default"] == "error"
      assert tools["execute_gaql"].annotations.readOnlyHint is True
      assert tools["list_change_events"].annotations.readOnlyHint is True
      assert tools["export_gaql_csv"].annotations.readOnlyHint is False

  asyncio.run(check())


def test_prepared_query_and_deferred_metadata_survive_mcp_protocol():
  async def check():
    async with Client(mcp_server) as client:
      with mock.patch.object(api, "run_gaql_query", return_value=[]):
        result = await client.call_tool(
            "execute_gaql",
            {"query": SHOPPING_QUERY + " " * 60000, "customer_id": "123"},
        )
      assert not result.is_error
      assert result.structured_content["data"] == []
      assert result.structured_content["full_materialized_response_export"][
          "available"
      ]

  asyncio.run(check())
