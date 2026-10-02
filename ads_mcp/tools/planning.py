"""Typed benchmarks, creator insights, and reach-planning service reads."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types import audience_insights_service
from google.ads.googleads.v25.services.types import benchmarks_service
from google.ads.googleads.v25.services.types import content_creator_insights_service
from google.ads.googleads.v25.services.types import reach_plan_service

from ads_mcp.api_version import API_VERSION
from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tooling import local_read_tool
from ads_mcp.tools._service import bounded_service_response
from ads_mcp.tools._service import normalized_account_id
from ads_mcp.tools._service import request_schema
from ads_mcp.tools._service import scoped_service_request
from ads_mcp.tools.api import INLINE_PAGE_BYTE_LIMIT
from ads_mcp.tools.api import finalize_bounded_response
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors


planning_read_tool = ads_read_tool(mcp, tags={"planning", "insights"})

_PLANNING_METHODS = {
    "list_benchmarks_available_dates": (
        "BenchmarksService",
        "list_benchmarks_available_dates",
        benchmarks_service.ListBenchmarksAvailableDatesRequest,
    ),
    "list_benchmarks_sources": (
        "BenchmarksService",
        "list_benchmarks_sources",
        benchmarks_service.ListBenchmarksSourcesRequest,
    ),
    "list_benchmarks_locations": (
        "BenchmarksService",
        "list_benchmarks_locations",
        benchmarks_service.ListBenchmarksLocationsRequest,
    ),
    "list_benchmarks_products": (
        "BenchmarksService",
        "list_benchmarks_products",
        benchmarks_service.ListBenchmarksProductsRequest,
    ),
    "generate_benchmarks_metrics": (
        "BenchmarksService",
        "generate_benchmarks_metrics",
        benchmarks_service.GenerateBenchmarksMetricsRequest,
    ),
    "generate_creator_insights": (
        "ContentCreatorInsightsService",
        "generate_creator_insights",
        content_creator_insights_service.GenerateCreatorInsightsRequest,
    ),
    "generate_trending_insights": (
        "ContentCreatorInsightsService",
        "generate_trending_insights",
        content_creator_insights_service.GenerateTrendingInsightsRequest,
    ),
    "list_audience_insights_attributes": (
        "AudienceInsightsService",
        "list_audience_insights_attributes",
        audience_insights_service.ListAudienceInsightsAttributesRequest,
    ),
    "generate_reach_forecast": (
        "ReachPlanService",
        "generate_reach_forecast",
        reach_plan_service.GenerateReachForecastRequest,
    ),
    "list_plannable_products": (
        "ReachPlanService",
        "list_plannable_products",
        reach_plan_service.ListPlannableProductsRequest,
    ),
    "list_plannable_locations": (
        "ReachPlanService",
        "list_plannable_locations",
        reach_plan_service.ListPlannableLocationsRequest,
    ),
}


def _planning_read(
    tool_name: str,
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None,
) -> dict[str, Any]:
  """Calls one fixed native method after request and account validation."""
  service_name, method_name, request_type = _PLANNING_METHODS[tool_name]
  customer_id, typed_request = scoped_service_request(
      customer_id, request, request_type
  )
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(
        login_customer_id, "login_customer_id"
    )
  client = get_ads_client(login_customer_id)
  service = client.get_service(service_name, version=API_VERSION)
  with handle_google_ads_errors():
    response = getattr(service, method_name)(request=typed_request)
  return bounded_service_response(
      response,
      customer_id=customer_id,
      service_name=service_name,
      method_name=method_name,
      account_scoped="customer_id"
      in request_type.pb().DESCRIPTOR.fields_by_name,
  )


@local_read_tool(mcp, tags={"planning", "docs"})
def get_planning_request_schema(tool_name: str) -> dict[str, Any]:
  """Returns installed native request fields and enums for one planning tool.

  Args:
      tool_name: Exact benchmarks, creator/trending, audience-attribute, or
          reach-planning tool name. Only the fixed allowlist is accepted.

  Returns:
      Protobuf JSON schema, oneof groups, request type, and whether customer_id
      is a native request field. Unknown fields and retired names are absent.
      This describes serialization, not service eligibility or required inputs.
      Oversized schemas have exact deferred export and no implicit file writes.
  """
  if not isinstance(tool_name, str) or tool_name not in _PLANNING_METHODS:
    raise ToolError(
        "Unsupported planning tool_name. Use one of: "
        + ", ".join(sorted(_PLANNING_METHODS))
    )
  service_name, method_name, request_type = _PLANNING_METHODS[tool_name]
  return finalize_bounded_response(
      {
          "tool_name": tool_name,
          "service": service_name,
          "method": method_name,
          "request_type": request_type.__name__,
          "account_scoped": "customer_id"
          in request_type.pb().DESCRIPTOR.fields_by_name,
          "request_schema": request_schema(request_type),
      },
      ("request_schema",),
      max_bytes=INLINE_PAGE_BYTE_LIMIT,
      public_schema=True,
  )


@planning_read_tool
def list_benchmarks_available_dates(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Reads benchmark dates, including the narrower all-metrics date range.

  Args:
      customer_id: Validated context; this global RPC has no account ID.
      request: Native object/JSON; see get_planning_request_schema.
      login_customer_id: Optional manager account ID.

  Returns:
      supported_dates and supported_dates_for_all_metrics when provided by
      Google. Open-quarter share/source-rate metrics may be unavailable even
      when aggregate/customer-rate/percentile metrics are supported. No ranges
      or availability are synthesized. Access remains subject to Google policy.
  """
  return _planning_read(
      "list_benchmarks_available_dates",
      customer_id,
      request,
      login_customer_id,
  )


@planning_read_tool
def list_benchmarks_sources(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists native benchmark sources, including product/service categories.

  Args:
      customer_id: Validated context; this global RPC has no account ID.
      request: Native object/JSON; benchmarks_sources accepts CATEGORY or
          INDUSTRY_VERTICAL. See get_planning_request_schema for exact fields.
      login_customer_id: Optional manager account ID.

  Returns:
      The provided source metadata, bounded with exact deferred export. Google
      controls service access and available categories; no custom list is
      invented.
  """
  return _planning_read(
      "list_benchmarks_sources", customer_id, request, login_customer_id
  )


@planning_read_tool
def list_benchmarks_locations(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists locations accepted by the benchmarks planning service.

  Args:
      customer_id: Validated context; this global RPC has no account ID.
      request: Native object/JSON; see get_planning_request_schema.
      login_customer_id: Optional manager account ID.

  Returns:
      Native location metadata with exact export on overflow. Access and
      supported locations are determined by Google, not this server.
  """
  return _planning_read(
      "list_benchmarks_locations", customer_id, request, login_customer_id
  )


@planning_read_tool
def list_benchmarks_products(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists product metadata used to construct benchmark product filters.

  Args:
      customer_id: Validated context; this global RPC has no account ID.
      request: Native object/JSON; see get_planning_request_schema.
      login_customer_id: Optional manager account ID.

  Returns:
      Provided product metadata, bounded with exact export. Google determines
      eligibility and product availability; forecasts are not produced here.
  """
  return _planning_read(
      "list_benchmarks_products", customer_id, request, login_customer_id
  )


@planning_read_tool
def generate_benchmarks_metrics(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Reads native category, aggregate/share, and percentile benchmark metrics.

  Args:
      customer_id: Client customer ID; injected into the native request.
      request: Native object/JSON string; use get_planning_request_schema.
          category_filter applies to all_advertisers benchmarks. Optional
          supplemental_data=["PERCENTILE_DATA"] requests percentile metrics.
          Check list_benchmarks_available_dates before choosing the date range.
      login_customer_id: Optional manager account ID.

  Returns:
      Only provided metrics. Google controls access, thresholds, and metric
      availability; share/source-rate metrics require the narrower
      supported_dates_for_all_metrics range. Missing percentile/share messages
      remain absent, not zero. Manager accounts have no direct campaign
      metrics. Overflow has exact deferred export.
  """
  return _planning_read(
      "generate_benchmarks_metrics", customer_id, request, login_customer_id
  )


@planning_read_tool
def generate_creator_insights(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Reads creator insights by native channel IDs/handles, audiences or topics.

  Args:
      customer_id: Customer ID; injected into the native request.
      request: Native object/JSON string; see get_planning_request_schema.
          search_channels.youtube_channel_handles accepts @handles.
          search_topics replaces removed search_brand. Optional
          supplemental_data includes BRAND_SENTIMENT_DATA and
          LOCAL_CREATOR_DATA; sub-country locations and audience combinations
          use native fields. Discover entities with
          list_audience_insights_attributes instead of guessing topic IDs.
      login_customer_id: Optional manager account ID.

  Returns:
      Only returned creator/local/sentiment insights, with exact deferred
      export on overflow. Eligibility, privacy thresholds, capabilities and
      country restrictions are enforced by Google; absent insights are not
      synthesized.
  """
  return _planning_read(
      "generate_creator_insights", customer_id, request, login_customer_id
  )


@planning_read_tool
def generate_trending_insights(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Reads native audience/topic trends with optional sentiment and local data.

  Args:
      customer_id: Customer ID; injected into the native request.
      request: Native object/JSON string; see get_planning_request_schema.
          Supports native search_audience/search_topics, sub_country_locations,
          audience combinations, and optional
          BRAND_SENTIMENT_DATA/LOCAL_CREATOR_DATA.
      login_customer_id: Optional manager account ID.

  Returns:
      Provided trending insights and time-series metrics when available,
      bounded with exact export. Google enforces access, supported
      geography/topics and privacy thresholds. Missing trend/sentiment data is
      not invented.
  """
  return _planning_read(
      "generate_trending_insights", customer_id, request, login_customer_id
  )


@planning_read_tool
def list_audience_insights_attributes(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Discovers native audience/topic attributes and supported capabilities.

  Args:
      customer_id: Customer ID; injected into the native request.
      request: Native object/JSON string; use get_planning_request_schema.
          KNOWLEDGE_GRAPH dimensions and knowledge_graph_entity_search_options
          discover supported creator topics and brand entities/capabilities.
      login_customer_id: Optional manager account ID.

  Returns:
      Provided attribute metadata, bounded with exact export. Eligibility and
      supported entity capabilities are determined by Google, not guessed here.
  """
  return _planning_read(
      "list_audience_insights_attributes",
      customer_id,
      request,
      login_customer_id,
  )


@planning_read_tool
def generate_reach_forecast(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Reads a native reach forecast with supported parental-status targeting.

  Args:
      customer_id: Customer ID; injected into the native request.
      request: Native object/JSON string; see get_planning_request_schema.
          Targeting uses plannable_location_ids and parental_statuses. Retired
          plannable_location_id/cookie_frequency_cap fields are rejected; use
          cookie_frequency_cap_setting. Discover locations/products through
          planning metadata tools instead of assuming campaign targets apply.
      login_customer_id: Optional manager account ID.

  Returns:
      Only returned forecast/audience metrics, with exact export on overflow.
      ReachPlanService requires Google allowlisting; forecasts are predictions,
      not observed campaign outcomes or guaranteed delivery.
  """
  return _planning_read(
      "generate_reach_forecast", customer_id, request, login_customer_id
  )


@planning_read_tool
def list_plannable_products(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists reach products and targeting metadata, including parental statuses.

  Args:
      customer_id: Validated context; this global RPC has no account ID.
      request: Native object/JSON. This lookup still uses the singular
          plannable_location_id from list_plannable_locations; see
          get_planning_request_schema for its native fields.
      login_customer_id: Optional manager account ID.

  Returns:
      Provided product/targeting metadata with exact export on overflow.
      Requires Google ReachPlanService access; availability is not guaranteed.
  """
  return _planning_read(
      "list_plannable_products", customer_id, request, login_customer_id
  )


@planning_read_tool
def list_plannable_locations(
    customer_id: str,
    request: dict[str, Any] | str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Lists locations supported by Google's reach-planning service.

  Args:
      customer_id: Validated context; this global RPC has no account ID.
      request: Native object/JSON; see get_planning_request_schema.
      login_customer_id: Optional manager account ID.

  Returns:
      Only returned location metadata, bounded with exact export. Requires
      Google ReachPlanService access; campaign geo IDs are not presumed valid.
  """
  return _planning_read(
      "list_plannable_locations", customer_id, request, login_customer_id
  )
