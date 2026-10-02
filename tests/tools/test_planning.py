"""Offline native v25.2 planning requests, truthful results, and delivery."""

# pylint: disable=protected-access

import asyncio
import csv
import importlib
import json
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.api_core.exceptions import PermissionDenied
from google.ads.googleads.v25.services.types import benchmarks_service as benchmarks
from google.ads.googleads.v25.services.types import content_creator_insights_service as creators
from google.ads.googleads.v25.services.types import reach_plan_service as reach
import jsonschema
import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.tools import api
from ads_mcp.tools import planning


@pytest.fixture(autouse=True)
def isolated_exports(monkeypatch):
  monkeypatch.setattr(
      api, "get_ads_credential_cache_scope", lambda: "planning"
  )
  monkeypatch.setattr(api, "_MATERIALIZED_SNAPSHOT_CACHE", api.OrderedDict())


_REQUESTS = {
    "list_benchmarks_available_dates": {},
    "list_benchmarks_sources": {"benchmarks_sources": ["CATEGORY"]},
    "list_benchmarks_locations": {},
    "list_benchmarks_products": {},
    "generate_benchmarks_metrics": {
        "date_range": {"start_date": "2026-07-01", "end_date": "2026-09-30"},
        "location": {"geo_target_constant": "geoTargetConstants/2840"},
        "benchmarks_source": {"all_advertisers": True},
        "category_filter": {"category_ids": ["Apparel"]},
        "product_filter": {"product_list": {"product_codes": ["IN_STREAM"]}},
        "supplemental_data": ["PERCENTILE_DATA"],
    },
    "generate_creator_insights": {
        "country_locations": [
            {"geo_target_constant": "geoTargetConstants/2840"}
        ],
        "search_channels": {"youtube_channel_handles": ["@example"]},
        "supplemental_data": ["BRAND_SENTIMENT_DATA", "LOCAL_CREATOR_DATA"],
    },
    "generate_trending_insights": {
        "country_location": {"geo_target_constant": "geoTargetConstants/2840"},
        "sub_country_locations": [
            {"geo_target_constant": "geoTargetConstants/21167"}
        ],
        "search_topics": {
            "entities": [{"knowledge_graph_machine_id": "/m/example"}]
        },
        "supplemental_data": ["BRAND_SENTIMENT_DATA", "LOCAL_CREATOR_DATA"],
    },
    "list_audience_insights_attributes": {
        "dimensions": ["KNOWLEDGE_GRAPH"],
        "knowledge_graph_entity_search_options": {
            "get_all_creator_attributes": True
        },
    },
    "generate_reach_forecast": {
        "campaign_duration": {"duration_in_days": 7},
        "targeting": {
            "plannable_location_ids": ["2840"],
            "parental_statuses": [{"type": "PARENT"}],
        },
        "cookie_frequency_cap_setting": {"impressions": 2, "time_unit": "DAY"},
        "planned_products": [
            {
                "plannable_product_code": "TEST",
                "budget_micros": "9007199254740993",
            }
        ],
    },
    "list_plannable_products": {"plannable_location_id": "2840"},
    "list_plannable_locations": {},
}


def _run(tool_name, request, response):
  client = mock.Mock()
  service_name, method_name, _ = planning._PLANNING_METHODS[tool_name]
  service = client.get_service.return_value
  getattr(service, method_name).return_value = response
  with mock.patch.object(
      planning, "get_ads_client", return_value=client
  ) as get:
    result = getattr(planning, tool_name)("12-3", request, "45 6")
  get.assert_called_once_with("456")
  client.get_service.assert_called_once_with(service_name, version="v25")
  return result, getattr(service, method_name).call_args.kwargs["request"]


@pytest.mark.parametrize("tool_name", list(_REQUESTS))
def test_fixed_methods_parse_native_v25_requests_before_call(tool_name):
  _, _, request_type = planning._PLANNING_METHODS[tool_name]
  module = importlib.import_module(request_type.__module__)
  response_type = getattr(
      module, request_type.__name__.replace("Request", "Response")
  )
  result, request = _run(
      tool_name, json.dumps(_REQUESTS[tool_name]), response_type()
  )
  assert isinstance(request, request_type)
  assert result["response"] == {}
  assert result["complete_inline"] is True
  assert result["truncated"] is False
  native_account = "customer_id" in request_type.pb().DESCRIPTOR.fields_by_name
  if native_account:
    assert request.customer_id == "123"
    assert result["account_scope"] == "request.customer_id"
  else:
    assert result["account_scope"] == "global_metadata"
  jsonschema.validate(
      _REQUESTS[tool_name],
      planning.get_planning_request_schema(tool_name)["request_schema"],
  )


def test_percentiles_aggregate_share_and_date_tiers_preserve_presence():
  response = benchmarks.GenerateBenchmarksMetricsResponse(
      customer_metrics={
          "aggregate_metrics": {"cost": 0.0, "impressions": 123.0},
          "share_metrics": {"share_of_voice": 0.2},
          "percentile_metrics": {
              "impressions_percentile_tier": "STRONG_COMPETITOR"
          },
      }
  )
  result, request = _run(
      "generate_benchmarks_metrics",
      _REQUESTS["generate_benchmarks_metrics"],
      response,
  )
  assert request.supplemental_data[0].name == "PERCENTILE_DATA"
  assert request.benchmarks_source.all_advertisers is True
  metrics = result["response"]["customer_metrics"]
  # This native metric is a non-presence proto3 scalar; a default zero
  # cannot prove that Google provided it, so serialization stays conservative.
  assert "cost" not in metrics["aggregate_metrics"]
  assert metrics["share_metrics"] == {"share_of_voice": 0.2}
  assert metrics["percentile_metrics"] == {
      "impressions_percentile_tier": "STRONG_COMPETITOR"
  }
  assert "average_benchmarks_metrics" not in result["response"]
  partial, _ = _run(
      "generate_benchmarks_metrics",
      _REQUESTS["generate_benchmarks_metrics"],
      benchmarks.GenerateBenchmarksMetricsResponse(
          customer_metrics={"aggregate_metrics": {"impressions": 12.0}}
      ),
  )
  assert "share_metrics" not in partial["response"]["customer_metrics"]
  assert "percentile_metrics" not in partial["response"]["customer_metrics"]
  dates, _ = _run(
      "list_benchmarks_available_dates",
      {},
      benchmarks.ListBenchmarksAvailableDatesResponse(
          supported_dates={
              "start_date": "2026-01-01",
              "end_date": "2026-09-30",
          },
          supported_dates_for_all_metrics={
              "start_date": "2026-01-01",
              "end_date": "2026-06-30",
          },
      ),
  )
  assert dates["response"]["supported_dates"]["end_date"] == "2026-09-30"
  assert (
      dates["response"]["supported_dates_for_all_metrics"]["end_date"]
      == "2026-06-30"
  )


def test_creator_handles_and_trending_sentiment_local_fields_are_native():
  result, request = _run(
      "generate_creator_insights",
      _REQUESTS["generate_creator_insights"],
      creators.GenerateCreatorInsightsResponse(
          creator_insights=[
              {
                  "creator_name": "Example",
                  "creator_channels": [{"handle": "@example"}],
              }
          ],
          local_creator_insights=[{"creator_name": "Local"}],
      ),
  )
  assert list(request.search_channels.youtube_channel_handles) == ["@example"]
  assert [value.name for value in request.supplemental_data] == [
      "BRAND_SENTIMENT_DATA",
      "LOCAL_CREATOR_DATA",
  ]
  assert (
      result["response"]["creator_insights"][0]["creator_channels"][0][
          "handle"
      ]
      == "@example"
  )
  trends, typed = _run(
      "generate_trending_insights",
      _REQUESTS["generate_trending_insights"],
      creators.GenerateTrendingInsightsResponse(
          trend_insights=[
              {
                  "brand_sentiment_insights": [
                      {"month": "2026-09-01", "has_insufficient_data": True}
                  ],
                  "related_local_creators": [{"creator_name": "Local"}],
              }
          ],
      ),
  )
  assert len(typed.sub_country_locations) == 1
  assert (
      trends["response"]["trend_insights"][0]["brand_sentiment_insights"][0][
          "has_insufficient_data"
      ]
      is True
  )


def test_reach_forecast_parental_status_and_exact_int64_budget():
  _, request = _run(
      "generate_reach_forecast",
      _REQUESTS["generate_reach_forecast"],
      reach.GenerateReachForecastResponse(),
  )
  assert request.targeting.parental_statuses[0].type_.name == "PARENT"
  assert list(request.targeting.plannable_location_ids) == ["2840"]
  assert request.planned_products[0].budget_micros == 9_007_199_254_740_993
  products, _ = _run(
      "list_plannable_products",
      {"plannable_location_id": "2840"},
      reach.ListPlannableProductsResponse(
          product_metadata=[
              {
                  "plannable_product_code": "TEST",
                  "plannable_targeting": {
                      "parental_statuses": [{"type_": "PARENT"}]
                  },
              }
          ],
      ),
  )
  assert (
      products["response"]["product_metadata"][0]["plannable_targeting"][
          "parental_statuses"
      ][0]["type_"]
      == "PARENT"
  )


@pytest.mark.parametrize(
    "tool_name,payload",
    [
        ("generate_creator_insights", {"search_brand": {}}),
        (
            "generate_creator_insights",
            {"search_channels": {"youtube_channel_handle": ["@example"]}},
        ),
        (
            "generate_creator_insights",
            {"search_topics": {}, "search_channels": {}},
        ),
        ("generate_reach_forecast", {"cookie_frequency_cap": 2}),
        (
            "generate_reach_forecast",
            {"targeting": {"plannable_location_id": "2840"}},
        ),
        (
            "generate_reach_forecast",
            {"targeting": {"parental_statuses": [{"type": "INVALID"}]}},
        ),
        (
            "generate_reach_forecast",
            {"planned_products": [{"budget_micros": "9223372036854775808"}]},
        ),
        (
            "generate_benchmarks_metrics",
            {"supplemental_data": ["BAD_PERCENTILE"]},
        ),
        (
            "generate_benchmarks_metrics",
            {"benchmarks_source": {"all_advertisers": "false"}},
        ),
        ("generate_benchmarks_metrics", {"customer_id": "456"}),
        ("generate_benchmarks_metrics", {"customerId": "456"}),
        (
            "generate_benchmarks_metrics",
            {"customer_id": "123", "customerId": "123"},
        ),
        (
            "generate_benchmarks_metrics",
            {
                "date_range": {
                    "start_date": "2026-01-01",
                    "startDate": "2026-01-01",
                }
            },
        ),
        ("list_benchmarks_available_dates", {"customer_id": "123"}),
        ("list_plannable_locations", {"customer_id": "456"}),
        ("list_benchmarks_sources", {"benchmarks_sources": "CATEGORY"}),
        (
            "list_benchmarks_sources",
            '{"benchmarks_sources":[],"benchmarks_sources":["CATEGORY"]}',
        ),
        ("list_benchmarks_sources", "[]"),
        ("list_benchmarks_sources", '{"bad":NaN}'),
        ("list_benchmarks_sources", "{"),
    ],
)
def test_invalid_native_requests_fail_before_client(tool_name, payload):
  with mock.patch.object(planning, "get_ads_client") as client:
    with pytest.raises(ToolError):
      getattr(planning, tool_name)("123", payload)
  client.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {"customer_id": True},
        {"customer_id": "0"},
        {"customer_id": "1/2"},
        {"customer_id": "1" * 5_000},
        {"login_customer_id": "0"},
        {"login_customer_id": 1.5},
    ],
)
def test_invalid_accounts_fail_before_client(arguments):
  with mock.patch.object(planning, "get_ads_client") as client:
    with pytest.raises(ToolError):
      planning.list_benchmarks_available_dates(
          **{"customer_id": "123", "request": {}, **arguments}
      )
  client.assert_not_called()


def test_service_errors_remain_tool_errors_without_retries():
  service = mock.Mock()
  service.generate_creator_insights.side_effect = PermissionDenied(
      "Not eligible"
  )
  client = mock.Mock()
  client.get_service.return_value = service
  with mock.patch.object(planning, "get_ads_client", return_value=client):
    with pytest.raises(ToolError, match="Not eligible"):
      planning.generate_creator_insights(
          "123", _REQUESTS["generate_creator_insights"]
      )
  service.generate_creator_insights.assert_called_once()


def test_large_service_response_is_exact_immutable_scoped_and_never_auto_writes(
    tmp_path, monkeypatch
):
  original = [
      {
          "creator_name": f"Creator {index}",
          "creator_channels": [
              {
                  "handle": f"@creator_{index}",
                  "channel_description": "🧭" * 500,
              }
          ],
      }
      for index in range(80)
  ]
  native = creators.GenerateCreatorInsightsResponse(creator_insights=original)
  with mock.patch.object(api, "_write_csv_rows") as write:
    result, _ = _run(
        "generate_creator_insights",
        _REQUESTS["generate_creator_insights"],
        native,
    )
  write.assert_not_called()
  assert api._serialized_json_bytes(result) <= api.INLINE_PAGE_BYTE_LIMIT
  assert result["truncated"] is True
  assert result["complete_inline"] is False
  token = result["full_materialized_response_export"]["export_call"][
      "arguments"
  ]["snapshot_token"]
  list(native.creator_insights)[0].creator_name = "Changed"
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(tmp_path))
  with mock.patch.object(planning, "get_ads_client") as client:
    artifact = api.export_materialized_response_csv(
        token, str(tmp_path / "exact.csv")
    )
  client.assert_not_called()
  previous_limit = csv.field_size_limit(1_000_000)
  try:
    with open(artifact["file_path"], encoding="utf-8", newline="") as stream:
      exported = next(
          json.loads(row["result"])
          for row in csv.DictReader(stream)
          if row["result_type"] == "response"
      )
  finally:
    csv.field_size_limit(previous_limit)
  assert exported == {"entry_key": "creator_insights", "value": original}
  assert artifact["truncated"] is False
  monkeypatch.setattr(api, "get_ads_credential_cache_scope", lambda: "other")
  with pytest.raises(ToolError, match="different Google Ads credentials"):
    api.export_materialized_response_csv(token)


def test_schema_allowlist_is_local_and_contains_current_nested_fields():
  with mock.patch.object(planning, "get_ads_client") as client:
    schema = planning.get_planning_request_schema("generate_creator_insights")
    with pytest.raises(ToolError, match="Unsupported planning tool_name"):
      planning.get_planning_request_schema("mutate_campaigns")
  client.assert_not_called()
  serialized = json.dumps(schema)
  assert "youtube_channel_handles" in serialized
  assert "BRAND_SENTIMENT_DATA" in serialized
  assert "search_brand" not in serialized
  assert "oneof_groups" in serialized


def test_planning_tools_survive_mcp_and_schema_is_local_read():
  async def check():
    async with Client(mcp_server) as client:
      tools = {tool.name: tool for tool in await client.list_tools()}
      for name in _REQUESTS:
        assert tools[name].annotations.readOnlyHint is True
      assert (
          tools["get_planning_request_schema"].annotations.openWorldHint
          is False
      )
      with mock.patch.object(planning, "get_ads_client") as get:
        service = get.return_value.get_service.return_value
        service.generate_benchmarks_metrics.return_value = (
            benchmarks.GenerateBenchmarksMetricsResponse(
                customer_metrics={
                    "percentile_metrics": {
                        "cost_percentile_tier": "COMPETITOR"
                    }
                }
            )
        )
        result = await client.call_tool(
            "generate_benchmarks_metrics",
            {
                "customer_id": "123",
                "request": _REQUESTS["generate_benchmarks_metrics"],
            },
        )
      assert not result.is_error
      assert (
          result.structured_content["response"]["customer_metrics"][
              "percentile_metrics"
          ]["cost_percentile_tier"]
          == "COMPETITOR"
      )

  asyncio.run(check())
