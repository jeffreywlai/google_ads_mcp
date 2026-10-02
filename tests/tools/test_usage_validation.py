"""Offline regressions for actionable, aggregated GAQL validation."""

from unittest import mock

from fastmcp.exceptions import ToolError
import pytest

from ads_mcp.tools import _gaql
from ads_mcp.tools import api


@pytest.mark.parametrize("tool_name", ["execute_gaql", "export_gaql_csv"])
def test_reports_all_fields_and_enum_errors_before_service(tool_name):
  query = (
      "SELECT campaign.start_date, campaign.end_date, "
      "campaign.url_expansion_opt_out FROM campaign "
      "WHERE campaign.status IN (ENABLD, PAUSD) "
      "AND campaign.advertising_channel_type = SERCH"
  )
  with mock.patch.object(api, "get_ads_client") as client:
    with pytest.raises(ToolError) as error:
      getattr(api, tool_name)(query, "123")
  client.assert_not_called()
  message = str(error.value)
  for field in (
      "campaign.start_date",
      "campaign.end_date",
      "campaign.url_expansion_opt_out",
  ):
    assert field in message
  for value in ("ENABLD", "PAUSD", "SERCH"):
    assert value in message
  for alternative in (
      "campaign.start_date_time",
      "campaign.end_date_time",
      "campaign.asset_automation_settings",
      "ENABLED",
      "PAUSED",
      "SEARCH",
  ):
    assert alternative in message


def test_enum_normalizer_reports_each_bad_list_item_and_filter():
  with pytest.raises(ToolError) as error:
    _gaql.normalize_gaql_enum_literals(
        "SELECT campaign.id FROM campaign "
        "WHERE campaign.status IN (BAD_ONE, BAD_TWO) "
        "AND campaign.advertising_channel_type = BAD_THREE"
    )
  for value in ("BAD_ONE", "BAD_TWO", "BAD_THREE"):
    assert value in str(error.value)


@pytest.mark.parametrize(
    "query,alternative",
    [
        (
            "SELECT segments.keyword.info.text FROM keyword_view",
            "ad_group_criterion.keyword.text",
        ),
        (
            "SELECT campaign_simulation.target_cpa_point_list "
            "FROM campaign_simulation",
            "campaign_simulation.target_cpa_point_list.points",
        ),
        (
            "SELECT campaign_lifecycle_goal.campaign "
            "FROM campaign_lifecycle_goal WHERE campaign.id = 456",
            "campaign_lifecycle_goal.campaign = "
            "'customers/<CUSTOMER_ID>/campaigns/<CAMPAIGN_ID>'",
        ),
    ],
)
def test_recorded_incompatible_fields_have_verified_recovery(
    query, alternative
):
  with pytest.raises(ToolError) as error:
    _gaql.preprocess_gaql_query(query)
  assert alternative in str(error.value)


def test_multiple_known_pairwise_conflicts_are_reported_together():
  with pytest.raises(ToolError) as error:
    _gaql.preprocess_gaql_query(
        "SELECT segments.new_versus_returning_customers, "
        "metrics.cost_micros, metrics.clicks FROM campaign"
    )
  message = str(error.value)
  assert "metrics.cost_micros is not selectable" in message
  assert "metrics.clicks is not selectable" in message


def test_unknown_fields_and_query_text_do_not_produce_speculative_failures():
  query = (
      "SELECT campaign.future_field, metrics.future_metric FROM campaign "
      "WHERE campaign.name = 'campaign.status = ENABLD'"
  )
  assert query in _gaql.preprocess_gaql_query(query)


def test_error_is_bounded_and_discloses_omitted_diagnostics():
  values = ", ".join(f"BAD_VALUE_{index}" for index in range(250))
  with pytest.raises(ToolError) as error:
    _gaql.preprocess_gaql_query(
        f"SELECT campaign.id FROM campaign WHERE campaign.status IN ({values})"
    )
  message = str(error.value)
  assert len(message.encode("utf-8")) <= 16384
  assert "additional validation" in message
