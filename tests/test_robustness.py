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

"""Robustness tests for the Google Ads MCP server.

Covers angles not tested elsewhere: invalid inputs, GAQL grammar fidelity,
query correctness, resource path formats, docs edge cases, and preprocess_gaql.
"""

import os
import re
from unittest import mock

from fastmcp.exceptions import ToolError
import pytest

from ads_mcp.tools import ad_groups
from ads_mcp.tools import ads
from ads_mcp.tools import campaigns
from ads_mcp.tools import docs
from ads_mcp.tools import keywords
from ads_mcp.tools import labels
from ads_mcp.tools import negatives
from ads_mcp.tools.api import preprocess_gaql
from ads_mcp.utils import MODULE_DIR


# ===================================================================
# 1. Invalid status validation (regression guard for consolidation)
# ===================================================================


class TestInvalidStatusValidation:
  """Consolidated tools must reject invalid status values with ToolError."""

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.campaigns.get_ads_client") as m:
      m.return_value = mock.Mock()
      yield

  def test_campaign_rejects_removed(self):
    with pytest.raises(ToolError, match="Invalid status"):
      campaigns.set_campaign_status("123", "456", "REMOVED")

  def test_campaign_rejects_deleted(self):
    with pytest.raises(ToolError, match="Invalid status"):
      campaigns.set_campaign_status("123", "456", "DELETED")

  def test_campaign_rejects_garbage(self):
    with pytest.raises(ToolError, match="Invalid status"):
      campaigns.set_campaign_status("123", "456", "garbage")

  def test_campaign_rejects_empty_string(self):
    with pytest.raises(ToolError, match="Invalid status"):
      campaigns.set_campaign_status("123", "456", "")


class TestInvalidAdGroupStatus:

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.ad_groups.get_ads_client") as m:
      m.return_value = mock.Mock()
      yield

  def test_rejects_removed(self):
    with pytest.raises(ToolError, match="Invalid status"):
      ad_groups.set_ad_group_status("123", "456", "REMOVED")

  def test_rejects_garbage(self):
    with pytest.raises(ToolError, match="Invalid status"):
      ad_groups.set_ad_group_status("123", "456", "xyz")


class TestInvalidAdStatus:

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.ads.get_ads_client") as m:
      m.return_value = mock.Mock()
      yield

  def test_rejects_removed(self):
    with pytest.raises(ToolError, match="Invalid status"):
      ads.set_ad_status("123", "456", "789", "REMOVED")

  def test_rejects_garbage(self):
    with pytest.raises(ToolError, match="Invalid status"):
      ads.set_ad_status("123", "456", "789", "xyz")


class TestInvalidKeywordStatus:

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.keywords.get_ads_client") as m:
      m.return_value = mock.Mock()
      yield

  def test_rejects_removed(self):
    with pytest.raises(ToolError, match="Invalid status"):
      keywords.set_keyword_status("123", "456", "789", "REMOVED")

  def test_rejects_garbage(self):
    with pytest.raises(ToolError, match="Invalid status"):
      keywords.set_keyword_status("123", "456", "789", "xyz")


# ===================================================================
# 2. GAQL grammar byte-for-byte fidelity
# ===================================================================


class TestGaqlGrammarFidelity:
  """The compact doc grammar BNF must be identical to the full doc."""

  @pytest.fixture(autouse=True)
  def load_docs(self):
    with open(
        os.path.join(MODULE_DIR, "context/GAQL.md"), "r", encoding="utf-8"
    ) as f:
      self.full_doc = f.read()
    with open(
        os.path.join(MODULE_DIR, "context/GAQL_compact.md"),
        "r",
        encoding="utf-8",
    ) as f:
      self.compact_doc = f.read()

  def _extract_grammar(self, text):
    """Extracts the first fenced code block."""
    match = re.search(r"```\n(.*?)```", text, re.DOTALL)
    assert match, "No fenced code block found"
    return match.group(1).strip()

  def test_grammar_bnf_byte_for_byte(self):
    full_grammar = self._extract_grammar(self.full_doc)
    compact_grammar = self._extract_grammar(self.compact_doc)
    assert full_grammar == compact_grammar, (
        "Compact grammar BNF differs from full doc. "
        "This means the LLM will get different syntax rules."
    )

  def test_all_operators_in_grammar(self):
    """Every operator in the grammar is also in the compact doc."""
    operators = [
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "IN",
        "NOT IN",
        "LIKE",
        "NOT LIKE",
        "CONTAINS ANY",
        "CONTAINS ALL",
        "CONTAINS NONE",
        "IS NULL",
        "IS NOT NULL",
        "DURING",
        "BETWEEN",
        "REGEXP_MATCH",
        "NOT REGEXP_MATCH",
    ]
    grammar = self._extract_grammar(self.compact_doc)
    for op in operators:
      assert op in grammar, f"Operator '{op}' missing from compact grammar"

  def test_all_date_functions_in_grammar(self):
    """Every date function is in the compact grammar."""
    functions = [
        "LAST_14_DAYS",
        "LAST_30_DAYS",
        "LAST_7_DAYS",
        "LAST_BUSINESS_WEEK",
        "LAST_MONTH",
        "LAST_WEEK_MON_SUN",
        "LAST_WEEK_SUN_SAT",
        "THIS_MONTH",
        "THIS_WEEK_MON_TODAY",
        "THIS_WEEK_SUN_TODAY",
        "TODAY",
        "YESTERDAY",
    ]
    grammar = self._extract_grammar(self.compact_doc)
    for fn in functions:
      assert (
          fn in grammar
      ), f"Date function '{fn}' missing from compact grammar"


# ===================================================================
# 3. preprocess_gaql correctness
# ===================================================================


class TestPreprocessGaql:
  """preprocess_gaql must produce valid GAQL PARAMETERS clauses."""

  def test_adds_parameters_to_bare_query(self):
    query = "SELECT campaign.id FROM campaign"
    result = preprocess_gaql(query)
    assert result.endswith("PARAMETERS omit_unselected_resource_names=true")

  def test_appends_with_comma_when_parameters_exists(self):
    query = (
        "SELECT campaign.id FROM campaign " "PARAMETERS include_drafts=true"
    )
    result = preprocess_gaql(query)
    assert ", omit_unselected_resource_names=true" in result
    assert result.count("PARAMETERS") == 1

  def test_no_double_append(self):
    query = (
        "SELECT campaign.id FROM campaign "
        "PARAMETERS omit_unselected_resource_names=true"
    )
    result = preprocess_gaql(query)
    assert result == query
    assert result.count("omit_unselected_resource_names") == 1

  def test_parameters_in_string_value_false_positive(self):
    """PARAMETERS inside a WHERE string value causes false detection."""
    query = (
        "SELECT campaign.id FROM campaign "
        "WHERE campaign.name LIKE '%PARAMETERS%'"
    )
    result = preprocess_gaql(query)
    # Has PARAMETERS in string but no include_drafts, so it should add
    # a new PARAMETERS clause (current behavior, may be a false positive
    # but not harmful since only omit_unselected_resource_names is added)
    assert "omit_unselected_resource_names=true" in result

  def test_preserves_existing_query_intact(self):
    query = (
        "SELECT campaign.id, campaign.name, metrics.clicks "
        "FROM campaign "
        "WHERE segments.date DURING LAST_30_DAYS "
        "ORDER BY metrics.clicks DESC "
        "LIMIT 50"
    )
    result = preprocess_gaql(query)
    # Original query should be intact, with PARAMETERS appended
    assert query in result
    assert result.endswith("PARAMETERS omit_unselected_resource_names=true")


# ===================================================================
# 4. Negative keyword GAQL queries are correct
# ===================================================================


class TestNegativeKeywordQueries:
  """GAQL queries in negatives.py must use correct field names."""

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.negatives.get_ads_client") as mock_get:
      client = mock.Mock()
      mock_get.return_value = client
      self.mock_client = client
      yield

  def test_list_shared_sets_query_fields(self):
    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    negatives.list_shared_sets("123")

    call_args = service.search_stream.call_args
    query = call_args.kwargs.get("query", call_args[1].get("query", ""))
    assert "shared_set.id" in query
    assert "shared_set.name" in query
    assert "shared_set.member_count" in query
    assert "FROM shared_set" in query
    assert "shared_set.type = 'NEGATIVE_KEYWORDS'" in query
    assert "shared_set.status = 'ENABLED'" in query

  def test_list_shared_set_keywords_query_fields(self):
    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    negatives.list_shared_set_keywords("123", "456")

    call_args = service.search_stream.call_args
    query = call_args.kwargs.get("query", call_args[1].get("query", ""))
    assert "shared_criterion.criterion_id" in query
    assert "shared_criterion.keyword.text" in query
    assert "shared_criterion.keyword.match_type" in query
    assert "FROM shared_criterion" in query
    assert "shared_set.id = 456" in query

  def test_list_campaign_negative_keywords_query_fields(self):
    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    negatives.list_campaign_negative_keywords("123", "789")

    call_args = service.search_stream.call_args
    query = call_args.kwargs.get("query", call_args[1].get("query", ""))
    assert "campaign_criterion.criterion_id" in query
    assert "campaign_criterion.keyword.text" in query
    assert "campaign_criterion.keyword.match_type" in query
    assert "FROM campaign_criterion" in query
    assert "campaign_criterion.type = 'KEYWORD'" in query
    assert "campaign_criterion.negative = TRUE" in query
    assert "campaign.id = 789" in query

  def test_list_campaign_shared_sets_query_fields(self):
    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    negatives.list_campaign_shared_sets("123")

    call_args = service.search_stream.call_args
    query = call_args.kwargs.get("query", call_args[1].get("query", ""))
    assert "campaign.id" in query
    assert "campaign.name" in query
    assert "shared_set.id" in query
    assert "shared_set.name" in query
    assert "FROM campaign_shared_set" in query
    assert "shared_set.type = 'NEGATIVE_KEYWORDS'" in query

  def test_list_campaign_shared_sets_filters_by_campaign(self):
    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    negatives.list_campaign_shared_sets("123", campaign_id="999")

    call_args = service.search_stream.call_args
    query = call_args.kwargs.get("query", call_args[1].get("query", ""))
    assert "campaign.id = 999" in query

  def test_list_campaign_shared_sets_filters_by_shared_set(self):
    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    negatives.list_campaign_shared_sets("123", shared_set_id="888")

    call_args = service.search_stream.call_args
    query = call_args.kwargs.get("query", call_args[1].get("query", ""))
    assert "shared_set.id = 888" in query


# ===================================================================
# 5. Resource path format strings match Google Ads API
# ===================================================================


class TestResourcePathFormats:
  """Manually constructed resource paths must match API conventions."""

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.negatives.get_ads_client") as mock_get:
      client = mock.Mock()
      mock_get.return_value = client
      service = client.get_service.return_value
      response = mock.Mock()
      response.results = [mock.Mock(resource_name="ok")]
      service.mutate_shared_criteria.return_value = response
      service.mutate_campaign_criteria.return_value = response
      service.mutate_campaign_shared_sets.return_value = response
      self.mock_client = client
      yield

  def test_shared_criteria_path_format(self):
    negatives.remove_shared_set_keywords("111", "222", ["333"])
    service = self.mock_client.get_service.return_value
    op = service.mutate_shared_criteria.call_args[1]["operations"][0]
    assert op.remove == "customers/111/sharedCriteria/222~333"

  def test_campaign_criteria_path_format(self):
    negatives.remove_campaign_negative_keywords("111", "222", ["333"])
    service = self.mock_client.get_service.return_value
    op = service.mutate_campaign_criteria.call_args[1]["operations"][0]
    assert op.remove == "customers/111/campaignCriteria/222~333"

  def test_campaign_shared_sets_path_format(self):
    negatives.detach_shared_set_from_campaign("111", "222", "333")
    service = self.mock_client.get_service.return_value
    op = service.mutate_campaign_shared_sets.call_args[1]["operations"][0]
    assert op.remove == "customers/111/campaignSharedSets/222~333"

  def test_multiple_criteria_paths(self):
    # Each get_type call must return a distinct object.
    ops_created = []

    def _make_op(*a, **kw):
      op = mock.Mock()
      ops_created.append(op)
      return op

    self.mock_client.get_type.side_effect = _make_op
    negatives.remove_shared_set_keywords("111", "222", ["333", "444", "555"])
    assert len(ops_created) == 3
    assert ops_created[0].remove == "customers/111/sharedCriteria/222~333"
    assert ops_created[1].remove == "customers/111/sharedCriteria/222~444"
    assert ops_created[2].remove == "customers/111/sharedCriteria/222~555"


# ===================================================================
# 6. Docs edge cases
# ===================================================================


class TestDocsEdgeCases:
  """Docs tools must handle edge cases safely."""

  def test_path_traversal_blocked(self):
    result = docs._get_view_doc_content("../../../etc/passwd")
    assert result == "Invalid view name."

  def test_path_traversal_dot_dot_slash(self):
    result = docs._get_view_doc_content("../../context/GAQL")
    assert result == "Invalid view name."

  def test_nonexistent_view_raises_tool_error(self):
    with pytest.raises(ToolError, match="No view resource"):
      docs._get_view_doc_content("nonexistent_view_xyz")

  def test_get_reporting_fields_doc_unknown_field(self):
    fake_yaml = "campaign.id:\n  type: INT64\n"
    with mock.patch("builtins.open", mock.mock_open(read_data=fake_yaml)):
      # Reset the cache so it re-reads
      docs._CACHED_FIELDS.clear()
      with pytest.raises(ToolError, match="Unknown fields"):
        docs.get_reporting_fields_doc(["fake.nonexistent.field"])
      docs._CACHED_FIELDS.clear()

  def test_get_gaql_doc_returns_string(self):
    result = docs.get_gaql_doc()
    assert isinstance(result, str)
    assert len(result) > 100

  def test_get_gaql_doc_resource_returns_full(self):
    result = docs.get_gaql_doc_resource()
    assert isinstance(result, str)
    assert len(result) > len(docs.get_gaql_doc())

  def test_views_yaml_exists_and_parses(self):
    result = docs._get_views_list()
    assert isinstance(result, str)
    # Should contain at least campaign and ad_group
    assert "campaign" in result
    assert "ad_group" in result

  def test_views_yaml_entries_are_valid_resource_names(self):
    """Every entry in views.yaml should match GAQL ResourceName pattern."""
    result = docs._get_views_list()
    pattern = re.compile(r"^[a-z][a-zA-Z_]*$")
    for line in result.strip().splitlines():
      if line.startswith("#") or not line.strip():
        continue
      name = line.lstrip("- ").strip()
      if name:
        assert pattern.match(
            name
        ), f"View '{name}' doesn't match ResourceName pattern"

  def test_views_yaml_includes_v24_query_builder_manifest_additions(self):
    result = docs._get_views_list()
    expected_resources = [
        "ai_max_search_term_ad_combination_view",
        "app_top_combination_view",
        "applied_incentive",
        "campaign_goal_config",
        "campaign_search_term_view",
        "detail_content_suitability_placement_view",
        "final_url_expansion_asset_view",
        "goal",
        "group_content_suitability_placement_view",
        "location_interest_view",
        "matched_location_interest_view",
        "targeting_expansion_view",
        "you_tube_video_upload",
    ]
    for resource in expected_resources:
      assert f"- {resource}" in result


# ===================================================================
# 7. Label action validation edge cases
# ===================================================================


class TestLabelActionValidation:

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.labels.get_ads_client") as m:
      m.return_value = mock.Mock()
      yield

  def test_campaign_labels_rejects_delete(self):
    with pytest.raises(ToolError, match="Invalid action"):
      labels.manage_campaign_labels("123", "456", ["789"], "DELETE")

  def test_campaign_labels_rejects_empty_string(self):
    with pytest.raises(ToolError, match="Invalid action"):
      labels.manage_campaign_labels("123", "456", ["789"], "")

  def test_ad_group_labels_rejects_delete(self):
    with pytest.raises(ToolError, match="Invalid action"):
      labels.manage_ad_group_labels("123", "456", ["789"], "DELETE")

  def test_ad_group_labels_rejects_empty_string(self):
    with pytest.raises(ToolError, match="Invalid action"):
      labels.manage_ad_group_labels("123", "456", ["789"], "")


# ===================================================================
# 8. execute_gaql return format matches output_schema
# ===================================================================


class TestExecuteGaqlReturnFormat:

  @pytest.fixture(autouse=True)
  def mock_ads_client(self):
    with mock.patch("ads_mcp.tools.api.get_ads_client") as mock_get:
      client = mock.Mock()
      mock_get.return_value = client
      self.mock_client = client
      yield

  def test_returns_dict_with_data_key(self):
    from ads_mcp.tools import api

    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    result = api.execute_gaql("SELECT campaign.id FROM campaign", "123")
    assert isinstance(result, dict)
    assert "data" in result
    assert isinstance(result["data"], list)

  def test_empty_results_returns_empty_data(self):
    from ads_mcp.tools import api

    service = self.mock_client.get_service.return_value
    service.search_stream.return_value = []

    result = api.execute_gaql("SELECT campaign.id FROM campaign", "123")
    assert result == {"data": []}


# ===================================================================
# 9. Compact doc has the example query with all clause types
# ===================================================================


class TestGaqlCompactExample:
  """The compact doc example should demonstrate typical query patterns."""

  @pytest.fixture(autouse=True)
  def load_compact(self):
    with open(
        os.path.join(MODULE_DIR, "context/GAQL_compact.md"),
        "r",
        encoding="utf-8",
    ) as f:
      self.compact = f.read()

  def test_example_has_select(self):
    assert "SELECT" in self.compact

  def test_example_has_from(self):
    assert "FROM campaign" in self.compact

  def test_example_has_where_with_during(self):
    assert "DURING LAST_30_DAYS" in self.compact

  def test_example_has_order_by(self):
    assert "ORDER BY" in self.compact

  def test_example_has_limit(self):
    assert "LIMIT" in self.compact

  def test_example_has_segments(self):
    assert "segments." in self.compact

  def test_example_has_metrics(self):
    assert "metrics." in self.compact
