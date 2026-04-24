# Copyright 2025 Google LLC
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

"""Tests for shared GAQL helper functions."""

from datetime import date

from ads_mcp.tools._gaql import build_date_range_condition
from ads_mcp.tools._gaql import date_range_bounds
from ads_mcp.tools._gaql import gaql_like_substring_pattern
from ads_mcp.tools._gaql import normalize_gaql_enum_literals
from ads_mcp.tools._gaql import normalize_list_arg
from ads_mcp.tools._gaql import preprocess_gaql_query
from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import validate_gaql_field_compatibility
from ads_mcp.tools._gaql import validate_date_range
from ads_mcp.tools._gaql import validate_limit
from ads_mcp.tools._gaql import validate_non_negative_number
from fastmcp.exceptions import ToolError
import pytest


def test_quote_enum_values_normalizes_names():
  assert quote_enum_values(["enabled", "PAUSED"]) == "ENABLED, PAUSED"


def test_quote_enum_values_rejects_malformed_names():
  with pytest.raises(ToolError, match="Invalid enum value"):
    quote_enum_values(["ENABLED) OR campaign.id > 0"])


def test_quote_int_values_rejects_malformed_ids():
  with pytest.raises(ToolError, match="values must be an integer"):
    quote_int_values(["123 OR metrics.clicks > 0"])


def test_quote_int_values_uses_field_name_in_errors():
  with pytest.raises(ToolError, match="campaign_ids must be an integer"):
    quote_int_values(["123 OR metrics.clicks > 0"], "campaign_ids")


def test_validate_limit_rejects_non_integer_values():
  with pytest.raises(ToolError, match="limit must be an integer"):
    validate_limit("25")


def test_validate_date_range_normalizes_supported_function():
  assert validate_date_range("last_30_days") == "LAST_30_DAYS"


def test_validate_date_range_rejects_malformed_function():
  with pytest.raises(ToolError, match="Invalid date_range"):
    validate_date_range("LAST_30_DAYS OR metrics.clicks > 0")


def test_build_date_range_condition_rewrites_extended_range(mocker):
  class FixedDate(date):

    @classmethod
    def today(cls):
      return cls(2026, 4, 23)

  mocker.patch("ads_mcp.tools._gaql.date", FixedDate)
  assert build_date_range_condition("segments.date", "LAST_90_DAYS") == (
      "segments.date BETWEEN '2026-01-23' AND '2026-04-22'"
  )


def test_build_date_range_condition_rejects_zero_day_range():
  with pytest.raises(ToolError, match="LAST_N_DAYS"):
    build_date_range_condition("segments.date", "LAST_0_DAYS")


def test_build_date_range_condition_rejects_huge_day_range():
  with pytest.raises(ToolError, match="3650 days or fewer"):
    build_date_range_condition(
        "segments.date",
        "LAST_999999999999999999999999999999999999_DAYS",
    )


def test_preprocess_gaql_query_rejects_zero_day_range():
  with pytest.raises(ToolError, match="LAST_N_DAYS"):
    preprocess_gaql_query(
        "SELECT campaign.id FROM campaign "
        "WHERE segments.date DURING LAST_0_DAYS"
    )


def test_gaql_like_substring_pattern_escapes_wildcards():
  assert gaql_like_substring_pattern("%_[]") == "%[%][_][[][]]%"


def test_build_date_range_condition_accepts_explicit_object():
  assert (
      build_date_range_condition(
          "segments.date",
          {"start_date": "2026-01-01", "end_date": "2026-01-31"},
      )
      == "segments.date BETWEEN '2026-01-01' AND '2026-01-31'"
  )


def test_date_range_bounds_resolves_extended_range(mocker):
  class FixedDate(date):

    @classmethod
    def today(cls):
      return cls(2026, 4, 23)

  mocker.patch("ads_mcp.tools._gaql.date", FixedDate)
  assert date_range_bounds("LAST_90_DAYS") == ("2026-01-23", "2026-04-22")


def test_date_range_bounds_resolves_native_calendar_ranges(mocker):
  class FixedDate(date):

    @classmethod
    def today(cls):
      return cls(2026, 4, 23)

  mocker.patch("ads_mcp.tools._gaql.date", FixedDate)
  assert date_range_bounds("LAST_MONTH") == ("2026-03-01", "2026-03-31")
  assert date_range_bounds("THIS_MONTH") == ("2026-04-01", "2026-04-23")
  assert date_range_bounds("LAST_WEEK_MON_SUN") == (
      "2026-04-13",
      "2026-04-19",
  )


def test_normalize_list_arg_accepts_json_string():
  assert normalize_list_arg('["111", "222"]', "campaign_ids") == [
      "111",
      "222",
  ]


@pytest.mark.parametrize(
    ("query", "expected_query"),
    [
        (
            "SELECT campaign.id FROM campaign "
            "WHERE campaign.status = enabled",
            "SELECT campaign.id FROM campaign "
            "WHERE campaign.status = ENABLED",
        ),
        (
            "SELECT campaign.id FROM campaign "
            "WHERE campaign.status IN ('enabled', \"PAUSED\")",
            "SELECT campaign.id FROM campaign "
            "WHERE campaign.status IN (ENABLED, PAUSED)",
        ),
        (
            "SELECT campaign.id FROM campaign "
            "WHERE campaign.status NOT IN (removed, PAUSED)",
            "SELECT campaign.id FROM campaign "
            "WHERE campaign.status NOT IN (REMOVED, PAUSED)",
        ),
    ],
)
def test_normalize_gaql_enum_literals_canonicalizes_enum_filters(
    query,
    expected_query,
):
  assert normalize_gaql_enum_literals(query) == expected_query


def test_normalize_gaql_enum_literals_rejects_bad_enum_value():
  with pytest.raises(
      ToolError,
      match="Invalid enum literal 'ENABLD' for campaign.status",
  ):
    normalize_gaql_enum_literals(
        "SELECT campaign.id FROM campaign " "WHERE campaign.status = ENABLD"
    )


@pytest.mark.parametrize(
    "query",
    [
        "SELECT campaign.id FROM campaign WHERE campaign.name = 'enabled'",
        (
            "SELECT campaign.id FROM campaign "
            "WHERE campaign.name LIKE 'campaign.status = ENABLD'"
        ),
        (
            "SELECT campaign.id FROM campaign "
            "WHERE future_resource.status = MAYBE_VALID"
        ),
    ],
)
def test_normalize_gaql_enum_literals_ignores_unvalidated_filters(query):
  assert normalize_gaql_enum_literals(query) == query


def test_preprocess_gaql_query_normalizes_enums_before_select_rewrite():
  query = (
      "SELECT campaign.id FROM campaign "
      "WHERE campaign.status IN ('enabled', paused)"
  )

  assert preprocess_gaql_query(query) == (
      "SELECT campaign.id FROM campaign "
      "WHERE campaign.status IN (ENABLED, PAUSED) "
      "PARAMETERS omit_unselected_resource_names=true"
  )


def test_preprocess_gaql_query_adds_missing_segment_filters_to_select():
  query = "SELECT campaign.id FROM campaign " "WHERE segments.device = mobile"

  assert preprocess_gaql_query(query) == (
      "SELECT campaign.id, segments.device FROM campaign "
      "WHERE segments.device = MOBILE "
      "PARAMETERS omit_unselected_resource_names=true"
  )


def test_preprocess_gaql_query_does_not_add_regular_filter_to_select():
  query = "SELECT ad_group.id FROM ad_group WHERE ad_group.status = paused"

  assert preprocess_gaql_query(query) == (
      "SELECT ad_group.id FROM ad_group WHERE ad_group.status = PAUSED "
      "PARAMETERS omit_unselected_resource_names=true"
  )


def test_normalize_gaql_enum_literals_rejects_empty_enum_list():
  with pytest.raises(ToolError, match="enum list cannot be empty"):
    normalize_gaql_enum_literals(
        "SELECT campaign.id FROM campaign WHERE campaign.status IN ()"
    )


def test_normalize_gaql_enum_literals_validates_contains_any_lists():
  assert normalize_gaql_enum_literals(
      "SELECT campaign.id FROM campaign "
      "WHERE metrics.interaction_event_types CONTAINS ANY (click, video_view)"
  ) == (
      "SELECT campaign.id FROM campaign "
      "WHERE metrics.interaction_event_types "
      "CONTAINS ANY (CLICK, VIDEO_VIEW)"
  )

  with pytest.raises(
      ToolError,
      match=(
          "Invalid enum literal 'BAD_ENUM' for "
          "metrics.interaction_event_types"
      ),
  ):
    normalize_gaql_enum_literals(
        "SELECT campaign.id FROM campaign "
        "WHERE metrics.interaction_event_types CONTAINS ANY (BAD_ENUM)"
    )


def test_validate_gaql_field_compatibility_accepts_valid_report_fields():
  validate_gaql_field_compatibility(
      "SELECT campaign.id, metrics.clicks "
      "FROM campaign "
      "WHERE segments.date DURING LAST_30_DAYS "
      "ORDER BY metrics.clicks DESC"
  )


def test_validate_gaql_field_compatibility_accepts_attributed_resource():
  validate_gaql_field_compatibility(
      "SELECT campaign.id, customer.id FROM campaign"
  )


def test_validate_gaql_field_compatibility_rejects_bad_metric():
  with pytest.raises(
      ToolError,
      match="metrics.clicks is not compatible with FROM campaign_criterion",
  ):
    validate_gaql_field_compatibility(
        "SELECT campaign_criterion.criterion_id, metrics.clicks "
        "FROM campaign_criterion"
    )


def test_validate_gaql_field_compatibility_rejects_bad_segment():
  with pytest.raises(
      ToolError,
      match="segments.date is not compatible with FROM campaign_criterion",
  ):
    validate_gaql_field_compatibility(
        "SELECT campaign_criterion.criterion_id "
        "FROM campaign_criterion "
        "WHERE segments.date DURING LAST_30_DAYS"
    )


def test_validate_gaql_field_compatibility_rejects_bad_attribute_resource():
  with pytest.raises(
      ToolError,
      match="ad_group.id is not compatible with FROM campaign",
  ):
    validate_gaql_field_compatibility(
        "SELECT campaign.id, ad_group.id FROM campaign"
    )


def test_validate_gaql_field_compatibility_ignores_unknown_from_resource():
  validate_gaql_field_compatibility(
      "SELECT future_resource.id FROM future_resource"
  )


def test_validate_gaql_field_compatibility_rejects_unique_user_pairing():
  with pytest.raises(
      ToolError,
      match=(
          "metrics.unique_users is not selectable with "
          "segments.conversion_action"
      ),
  ):
    validate_gaql_field_compatibility(
        "SELECT campaign.id, metrics.unique_users "
        "FROM campaign "
        "WHERE segments.conversion_action = 'customers/123/conversionActions/1'"
    )


def test_preprocess_gaql_query_rejects_incompatible_fields_before_api():
  with pytest.raises(
      ToolError,
      match="metrics.clicks is not compatible with FROM campaign_criterion",
  ):
    preprocess_gaql_query(
        "SELECT campaign_criterion.criterion_id, metrics.clicks "
        "FROM campaign_criterion"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_validate_non_negative_number_rejects_non_finite(value):
  with pytest.raises(ToolError, match="must be finite"):
    validate_non_negative_number(value, "min_clicks")
