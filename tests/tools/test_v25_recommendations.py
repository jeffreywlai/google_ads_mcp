"""v25 recommendation payloads, native parameters and app goals."""

from unittest import mock

from fastmcp.exceptions import ToolError
import pytest

from ads_mcp.tools import recommendations
from ads_mcp.tools import _gaql


@pytest.mark.parametrize(
    "kind,parameters",
    [
        (
            "RAISE_TARGET_CPA_PERFORMANCE_BID_TOO_LOW",
            {"target_cpa_multiplier": 1.2},
        ),
        (
            "LOWER_TARGET_ROAS_PERFORMANCE_BID_TOO_LOW",
            {"target_roas_multiplier": 0.8},
        ),
        (
            "RAISE_TARGET_CPA_PERFORMANCE_BID_TOO_LOW",
            {"target_cpa_multiplier": "1.2"},
        ),
        (
            "LOWER_TARGET_ROAS_PERFORMANCE_BID_TOO_LOW",
            {"target_roas_multiplier": "0.8"},
        ),
        ("CAMPAIGN_SPECIFIC_APP_GOAL", {}),
    ],
)
def test_new_recommendations_apply_native_parameters(kind, parameters):
  name = "customers/123/recommendations/5"
  with (
      mock.patch.object(
          recommendations,
          "_get_recommendation_type_map",
          return_value={name: kind},
      ),
      mock.patch.object(recommendations, "get_ads_client") as client,
  ):
    service = client.return_value.get_service.return_value
    service.apply_recommendation.return_value.results = []
    service.apply_recommendation.return_value.partial_failure_error = None
    recommendations.apply_recommendations("123", [name], {name: parameters})
  operation = service.apply_recommendation.call_args.kwargs["request"][
      "operations"
  ][0]
  if kind == "CAMPAIGN_SPECIFIC_APP_GOAL":
    assert operation == {"resource_name": name}
  else:
    native = recommendations.ApplyRecommendationOperation(**operation)
    actual = getattr(native, kind.lower())
    field = next(iter(parameters))
    assert getattr(actual, field) == float(parameters[field])
  service.apply_recommendation.assert_called_once()


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_cpa_multiplier", 1),
        ("target_cpa_multiplier", -1),
        ("target_roas_multiplier", 0),
        ("target_roas_multiplier", 1),
        ("target_roas_multiplier", "NaN"),
        ("target_cpa_multiplier", True),
    ],
)
def test_invalid_bid_multipliers_fail_before_mutation_client(field, value):
  kind = (
      "RAISE_TARGET_CPA_PERFORMANCE_BID_TOO_LOW"
      if field == "target_cpa_multiplier"
      else "LOWER_TARGET_ROAS_PERFORMANCE_BID_TOO_LOW"
  )
  name = "customers/123/recommendations/5"
  with (
      mock.patch.object(
          recommendations,
          "_get_recommendation_type_map",
          return_value={name: kind},
      ),
      mock.patch.object(recommendations, "get_ads_client") as client,
  ):
    with pytest.raises(ToolError):
      recommendations.apply_recommendations(
          "123", [name], {name: {field: value}}
      )
  client.assert_not_called()


def test_listing_includes_new_payloads_and_passes_real_preflight():
  with mock.patch.object(
      recommendations,
      "run_gaql_query_page",
      return_value={
          "rows": [],
          "total_results_count": 0,
          "next_page_token": None,
      },
  ) as query:
    recommendations.list_recommendations("123")
  sql = query.call_args.kwargs["query"]
  for field in (
      "campaign_specific_app_goal_recommendation",
      "raise_target_cpa_performance_bid_too_low_recommendation",
      "lower_target_roas_performance_bid_too_low_recommendation",
  ):
    assert "recommendation." + field in sql
  assert _gaql.preprocess_gaql_query(sql)
