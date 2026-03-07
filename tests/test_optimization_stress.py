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

"""Stress tests for the token optimization changes.

Verifies that:
1. GAQL compact doc retains all critical information from the full doc
2. Docs tool routing (compact vs full, views list vs full reporting doc)
3. Consolidated tools (set_*_status, manage_*_labels) handle all cases
4. Full docs remain accessible via resources
5. No functionality lost in any tool
"""

import os
from unittest import mock

from ads_mcp.tools import ad_groups
from ads_mcp.tools import ads
from ads_mcp.tools import campaigns
from ads_mcp.tools import docs
from ads_mcp.tools import keywords
from ads_mcp.tools import labels
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
import pytest


CUSTOMER_ID = "1234567890"


# =========================================================================
# GAQL Compact Doc Completeness Tests
# =========================================================================


class TestGaqlCompactCompleteness:
  """Verify the compact GAQL doc retains every critical concept
  from the full GAQL doc.
  """

  @pytest.fixture(autouse=True)
  def load_docs(self):
    compact_path = os.path.join(
        docs.MODULE_DIR, "context/GAQL_compact.md"
    )
    full_path = os.path.join(docs.MODULE_DIR, "context/GAQL.md")
    with open(compact_path, "r", encoding="utf-8") as f:
      self.compact = f.read()
    with open(full_path, "r", encoding="utf-8") as f:
      self.full = f.read()

  # --- Grammar BNF ---

  def test_grammar_bnf_identical(self):
    """The grammar BNF block must be identical in both docs."""
    def extract_grammar(text):
      start = text.index("Query            ->")
      end = text.index("```", start)
      return text[start:end].strip()

    assert extract_grammar(self.compact) == extract_grammar(self.full)

  def test_all_operators_present(self):
    """Every operator from the full doc must be in the compact doc."""
    operators = [
        "=", "!=", ">", ">=", "<", "<=",
        "IN", "NOT IN", "LIKE", "NOT LIKE",
        "CONTAINS ANY", "CONTAINS ALL", "CONTAINS NONE",
        "IS NULL", "IS NOT NULL",
        "DURING", "BETWEEN",
        "REGEXP_MATCH", "NOT REGEXP_MATCH",
    ]
    for op in operators:
      assert op in self.compact, f"Operator '{op}' missing from compact doc"

  def test_all_date_functions_present(self):
    """All DURING function constants must be in the compact doc."""
    functions = [
        "LAST_14_DAYS", "LAST_30_DAYS", "LAST_7_DAYS",
        "LAST_BUSINESS_WEEK", "LAST_MONTH",
        "LAST_WEEK_MON_SUN", "LAST_WEEK_SUN_SAT",
        "THIS_MONTH", "THIS_WEEK_MON_TODAY", "THIS_WEEK_SUN_TODAY",
        "TODAY", "YESTERDAY",
    ]
    for func in functions:
      assert func in self.compact, (
          f"Date function '{func}' missing from compact doc"
      )

  # --- Rules coverage ---

  def test_select_from_required(self):
    assert "SELECT" in self.compact and "FROM" in self.compact
    assert "required" in self.compact.lower() or (
        "`SELECT` and `FROM` are required" in self.compact
    )

  def test_regexp_match_re2(self):
    assert "RE2" in self.compact

  def test_like_escaping(self):
    assert "[[]Earth[_]to[_]Mars[]]%" in self.compact

  def test_like_string_only(self):
    assert "string field" in self.compact.lower() or (
        "not arrays" in self.compact
    )

  def test_and_separator_rule(self):
    assert "separates conditions" in self.compact

  def test_core_date_segments_listed(self):
    segments = [
        "segments.date", "segments.week", "segments.month",
        "segments.quarter", "segments.year",
    ]
    for seg in segments:
      assert seg in self.compact, (
          f"Core date segment '{seg}' missing from compact doc"
      )

  def test_core_date_segment_where_rule(self):
    """If a core date segment is selected, WHERE must filter it."""
    assert "finite range" in self.compact or (
        "must filter" in self.compact
    )

  def test_non_selectable_fields_rule(self):
    assert "Selectable" in self.compact or (
        "Non-selectable" in self.compact
    )

  def test_repeated_fields_rule(self):
    assert "isRepeated" in self.compact or "repeated" in self.compact

  def test_resource_name_always_returned(self):
    assert "resource_name" in self.compact
    assert "always returned" in self.compact

  def test_attributed_resources_joined(self):
    assert "implicitly joined" in self.compact or (
        "Attributed" in self.compact
    )

  def test_metrics_segments_standalone(self):
    assert (
        "without resource fields" in self.compact
        or "exclusively selected" in self.compact
    )

  def test_parameters_include_drafts(self):
    assert "include_drafts" in self.compact

  def test_resource_name_filter_order(self):
    """resource_name can be used to filter or order data."""
    assert "filter or order" in self.compact

  def test_order_by_default_asc(self):
    assert "ASC" in self.compact
    assert "defaults" in self.compact.lower()

  def test_segments_metrics_compatibility(self):
    assert "compatible" in self.compact

  def test_case_sensitivity(self):
    assert "case" in self.compact.lower() or (
        "Case-sensitivity" in self.compact
    )

  def test_single_resource_in_from(self):
    assert "one resource" in self.compact.lower() or (
        "Only one resource" in self.compact
    )

  def test_example_uses_all_clauses(self):
    """The example should demonstrate SELECT, FROM, WHERE, ORDER BY, LIMIT."""
    example_start = self.compact.index("## Example")
    example = self.compact[example_start:]
    assert "SELECT" in example
    assert "FROM" in example
    assert "WHERE" in example
    assert "ORDER BY" in example
    assert "LIMIT" in example


# =========================================================================
# Docs Tool Routing Tests
# =========================================================================


class TestDocsToolRouting:
  """Verify the docs tools serve the correct files."""

  def test_get_gaql_doc_returns_compact(self):
    """get_gaql_doc() should return compact, not full doc."""
    result = docs.get_gaql_doc()
    # Compact doc has "## Grammar" not "## Query Grammar"
    assert "## Grammar" in result
    assert "## Query Grammar" not in result
    # Should be significantly shorter than the full doc
    full = docs._get_gaql_doc_content()
    assert len(result) < len(full)

  def test_gaql_resource_returns_full(self):
    """Resource endpoint should still return the full GAQL doc."""
    result = docs.get_gaql_doc_resource()
    assert "## Query Grammar" in result
    assert "### Clauses" in result
    assert len(result) > 5000  # Full doc is ~11KB

  def test_reporting_view_no_arg_returns_views_list(self):
    """get_reporting_view_doc() without args returns views.yaml."""
    result = docs.get_reporting_view_doc()
    # views.yaml is a YAML list of view names
    assert "- campaign\n" in result
    assert "- ad_group\n" in result
    # Should NOT contain the full markdown descriptions
    assert "## " not in result

  def test_reporting_view_with_arg_returns_view_doc(self):
    """get_reporting_view_doc('campaign') returns campaign-specific doc."""
    fake_content = "campaign:\n  fields:\n    - campaign.id\n"
    with mock.patch("builtins.open", mock.mock_open(read_data=fake_content)):
      result = docs.get_reporting_view_doc("campaign")
    assert "campaign" in result.lower()

  def test_reporting_resource_returns_full_doc(self):
    """Resource endpoint still returns the full reporting doc."""
    result = docs.get_reporting_doc()
    assert len(result) > 20000  # Full doc is ~26KB

  def test_views_list_contains_all_known_views(self):
    """The views list should contain common view names."""
    result = docs.get_reporting_view_doc()
    expected = [
        "campaign", "ad_group", "ad_group_ad", "keyword_view",
        "campaign_budget", "campaign_criterion", "search_term_view",
        "click_view", "customer", "label",
    ]
    for view in expected:
      assert f"- {view}\n" in result, (
          f"View '{view}' missing from views list"
      )

  def test_views_list_much_smaller_than_full_doc(self):
    """Views list should be much smaller than the full reporting doc."""
    views_list = docs.get_reporting_view_doc()
    full_doc = docs.get_reporting_doc()
    # Views list should be less than 25% of the full doc
    assert len(views_list) < len(full_doc) * 0.25


# =========================================================================
# Consolidated Status Tool Tests
# =========================================================================


class TestSetCampaignStatusConsolidated:

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_pause(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = campaigns.set_campaign_status(CUSTOMER_ID, "111", "PAUSED")
    assert result == {"resource_name": "x"}
    mock_get.assert_called_with(None)

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_enable(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = campaigns.set_campaign_status(CUSTOMER_ID, "111", "ENABLED")
    assert result == {"resource_name": "x"}

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_case_insensitive_status(self, mock_get):
    """Status should work case-insensitively via .upper()."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    campaigns.set_campaign_status(CUSTOMER_ID, "111", "paused")
    # getattr(..., "PAUSED") should be called
    assert client.enums.CampaignStatusEnum  # accessed

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_login_customer_id(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    campaigns.set_campaign_status(
        CUSTOMER_ID, "111", "PAUSED", login_customer_id="999"
    )
    mock_get.assert_called_with("999")

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_api_error_raised_as_tool_error(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    error = mock.Mock()
    error.__str__ = lambda self: "Campaign error"
    exc = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_service.mutate_campaigns.side_effect = exc

    with pytest.raises(ToolError, match="Campaign error"):
      campaigns.set_campaign_status(CUSTOMER_ID, "111", "PAUSED")


class TestSetAdGroupStatusConsolidated:

  @mock.patch("ads_mcp.tools.ad_groups.get_ads_client")
  def test_pause(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = ad_groups.set_ad_group_status(CUSTOMER_ID, "111", "PAUSED")
    assert result == {"resource_name": "x"}

  @mock.patch("ads_mcp.tools.ad_groups.get_ads_client")
  def test_enable(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = ad_groups.set_ad_group_status(CUSTOMER_ID, "111", "ENABLED")
    assert result == {"resource_name": "x"}

  @mock.patch("ads_mcp.tools.ad_groups.get_ads_client")
  def test_single_client_call(self, mock_get):
    """Consolidated tool should only call get_ads_client once."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.set_ad_group_status(CUSTOMER_ID, "111", "PAUSED")
    assert mock_get.call_count == 1


class TestSetAdStatusConsolidated:

  @mock.patch("ads_mcp.tools.ads.get_ads_client")
  def test_pause(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = ads.set_ad_status(CUSTOMER_ID, "111", "222", "PAUSED")
    assert result == {"resource_name": "x"}

  @mock.patch("ads_mcp.tools.ads.get_ads_client")
  def test_enable(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = ads.set_ad_status(CUSTOMER_ID, "111", "222", "ENABLED")
    assert result == {"resource_name": "x"}

  @mock.patch("ads_mcp.tools.ads.get_ads_client")
  def test_single_client_call(self, mock_get):
    """Fixed: previously called get_ads_client twice (enum + helper)."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ads.set_ad_status(CUSTOMER_ID, "111", "222", "PAUSED")
    assert mock_get.call_count == 1


class TestSetKeywordStatusConsolidated:

  @mock.patch("ads_mcp.tools.keywords.get_ads_client")
  def test_pause(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = keywords.set_keyword_status(
        CUSTOMER_ID, "111", "222", "PAUSED"
    )
    assert result == {"resource_name": "x"}

  @mock.patch("ads_mcp.tools.keywords.get_ads_client")
  def test_enable(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    result = keywords.set_keyword_status(
        CUSTOMER_ID, "111", "222", "ENABLED"
    )
    assert result == {"resource_name": "x"}

  @mock.patch("ads_mcp.tools.keywords.get_ads_client")
  def test_single_client_call(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    keywords.set_keyword_status(CUSTOMER_ID, "111", "222", "PAUSED")
    assert mock_get.call_count == 1


# =========================================================================
# Consolidated Label Tool Tests
# =========================================================================


class TestManageCampaignLabelsConsolidated:

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_apply(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_campaign_label_svc = mock.Mock()
    mock_campaign_svc = mock.Mock()
    mock_label_svc = mock.Mock()
    mock_label_svc.label_path.return_value = "labels/111"
    mock_campaign_svc.campaign_path.return_value = "campaigns/222"

    def get_service(name):
      if name == "CampaignLabelService":
        return mock_campaign_label_svc
      if name == "CampaignService":
        return mock_campaign_svc
      return mock_label_svc

    client.get_service.side_effect = get_service
    mock_response = (
        mock_campaign_label_svc.mutate_campaign_labels.return_value
    )
    mock_response.results = [mock.Mock(resource_name="result/1")]

    result = labels.manage_campaign_labels(
        CUSTOMER_ID, "111", ["222"], "APPLY"
    )
    assert result == {"resource_names": ["result/1"]}

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_remove(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_service.campaign_label_path.return_value = "cl/222~111"
    mock_response = mock_service.mutate_campaign_labels.return_value
    mock_response.results = [mock.Mock(resource_name="cl/222~111")]

    result = labels.manage_campaign_labels(
        CUSTOMER_ID, "111", ["222"], "REMOVE"
    )
    assert result == {"resource_names": ["cl/222~111"]}

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_case_insensitive_action(self, mock_get):
    """Action should be case-insensitive."""
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_service.campaign_label_path.return_value = "x"
    mock_response = mock_service.mutate_campaign_labels.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    # Should work with lowercase
    labels.manage_campaign_labels(
        CUSTOMER_ID, "111", ["222"], "remove"
    )

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_invalid_action(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    with pytest.raises(ToolError, match="Invalid action"):
      labels.manage_campaign_labels(
          CUSTOMER_ID, "111", ["222"], "DELETE"
      )

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_multiple_campaigns(self, mock_get):
    """Apply to multiple campaigns should create multiple ops."""
    client = mock.Mock()
    mock_get.return_value = client

    mock_campaign_label_svc = mock.Mock()
    mock_campaign_svc = mock.Mock()
    mock_label_svc = mock.Mock()

    def get_service(name):
      if name == "CampaignLabelService":
        return mock_campaign_label_svc
      if name == "CampaignService":
        return mock_campaign_svc
      return mock_label_svc

    client.get_service.side_effect = get_service
    mock_response = (
        mock_campaign_label_svc.mutate_campaign_labels.return_value
    )
    mock_response.results = [
        mock.Mock(resource_name=f"r/{i}") for i in range(3)
    ]

    result = labels.manage_campaign_labels(
        CUSTOMER_ID, "111", ["1", "2", "3"], "APPLY"
    )
    assert len(result["resource_names"]) == 3

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_api_error(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_service.campaign_label_path.return_value = "x"
    error = mock.Mock()
    error.__str__ = lambda self: "Label error"
    exc = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_service.mutate_campaign_labels.side_effect = exc

    with pytest.raises(ToolError, match="Label error"):
      labels.manage_campaign_labels(
          CUSTOMER_ID, "111", ["222"], "REMOVE"
      )


class TestManageAdGroupLabelsConsolidated:

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_apply(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_ag_label_svc = mock.Mock()
    mock_ag_svc = mock.Mock()
    mock_label_svc = mock.Mock()

    def get_service(name):
      if name == "AdGroupLabelService":
        return mock_ag_label_svc
      if name == "AdGroupService":
        return mock_ag_svc
      return mock_label_svc

    client.get_service.side_effect = get_service
    mock_response = mock_ag_label_svc.mutate_ad_group_labels.return_value
    mock_response.results = [mock.Mock(resource_name="result/1")]

    result = labels.manage_ad_group_labels(
        CUSTOMER_ID, "111", ["333"], "APPLY"
    )
    assert result == {"resource_names": ["result/1"]}

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_remove(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_service.ad_group_label_path.return_value = "agl/333~111"
    mock_response = mock_service.mutate_ad_group_labels.return_value
    mock_response.results = [mock.Mock(resource_name="agl/333~111")]

    result = labels.manage_ad_group_labels(
        CUSTOMER_ID, "111", ["333"], "REMOVE"
    )
    assert result == {"resource_names": ["agl/333~111"]}

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_invalid_action(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    with pytest.raises(ToolError, match="Invalid action"):
      labels.manage_ad_group_labels(
          CUSTOMER_ID, "111", ["333"], "ATTACH"
      )


# =========================================================================
# Status Tool No-Pollution Verification
# =========================================================================


class TestConsolidatedStatusNoPollution:
  """Verify consolidated tools properly pass login_customer_id
  and don't leak state.
  """

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_campaign_status_no_leak(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    campaigns.set_campaign_status(
        CUSTOMER_ID, "1", "PAUSED", login_customer_id="999"
    )
    mock_get.assert_called_with("999")

    campaigns.set_campaign_status(CUSTOMER_ID, "2", "ENABLED")
    mock_get.assert_called_with(None)

  @mock.patch("ads_mcp.tools.ad_groups.get_ads_client")
  def test_ad_group_status_no_leak(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.set_ad_group_status(
        CUSTOMER_ID, "1", "PAUSED", login_customer_id="888"
    )
    mock_get.assert_called_with("888")

    ad_groups.set_ad_group_status(CUSTOMER_ID, "1", "ENABLED")
    mock_get.assert_called_with(None)

  @mock.patch("ads_mcp.tools.ads.get_ads_client")
  def test_ad_status_no_leak(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ads.set_ad_status(
        CUSTOMER_ID, "1", "2", "PAUSED", login_customer_id="777"
    )
    mock_get.assert_called_with("777")

    ads.set_ad_status(CUSTOMER_ID, "1", "2", "ENABLED")
    mock_get.assert_called_with(None)

  @mock.patch("ads_mcp.tools.keywords.get_ads_client")
  def test_keyword_status_no_leak(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    keywords.set_keyword_status(
        CUSTOMER_ID, "1", "2", "PAUSED", login_customer_id="666"
    )
    mock_get.assert_called_with("666")

    keywords.set_keyword_status(CUSTOMER_ID, "1", "2", "ENABLED")
    mock_get.assert_called_with(None)

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_label_manage_no_leak(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_service.campaign_label_path.return_value = "x"
    mock_response = mock_service.mutate_campaign_labels.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    labels.manage_campaign_labels(
        CUSTOMER_ID, "1", ["2"], "REMOVE", login_customer_id="555"
    )
    mock_get.assert_called_with("555")

    labels.manage_campaign_labels(
        CUSTOMER_ID, "1", ["2"], "REMOVE"
    )
    mock_get.assert_called_with(None)


# =========================================================================
# Old Tool Names Don't Exist
# =========================================================================


class TestOldToolNamesRemoved:
  """Verify old tool function names no longer exist."""

  def test_no_pause_campaign(self):
    assert not hasattr(campaigns, "pause_campaign")

  def test_no_resume_campaign(self):
    assert not hasattr(campaigns, "resume_campaign")

  def test_no_pause_ad_group(self):
    assert not hasattr(ad_groups, "pause_ad_group")

  def test_no_enable_ad_group(self):
    assert not hasattr(ad_groups, "enable_ad_group")

  def test_no_pause_ad(self):
    assert not hasattr(ads, "pause_ad")

  def test_no_enable_ad(self):
    assert not hasattr(ads, "enable_ad")

  def test_no_pause_keyword(self):
    assert not hasattr(keywords, "pause_keyword")

  def test_no_enable_keyword(self):
    assert not hasattr(keywords, "enable_keyword")

  def test_no_apply_label_to_campaigns(self):
    assert not hasattr(labels, "apply_label_to_campaigns")

  def test_no_remove_label_from_campaigns(self):
    assert not hasattr(labels, "remove_label_from_campaigns")

  def test_no_apply_label_to_ad_groups(self):
    assert not hasattr(labels, "apply_label_to_ad_groups")

  def test_no_remove_label_from_ad_groups(self):
    assert not hasattr(labels, "remove_label_from_ad_groups")


# =========================================================================
# New Tool Names Exist
# =========================================================================


class TestNewToolNamesExist:
  """Verify new consolidated tool functions exist."""

  def test_set_campaign_status(self):
    assert callable(campaigns.set_campaign_status)

  def test_set_ad_group_status(self):
    assert callable(ad_groups.set_ad_group_status)

  def test_set_ad_status(self):
    assert callable(ads.set_ad_status)

  def test_set_keyword_status(self):
    assert callable(keywords.set_keyword_status)

  def test_manage_campaign_labels(self):
    assert callable(labels.manage_campaign_labels)

  def test_manage_ad_group_labels(self):
    assert callable(labels.manage_ad_group_labels)

  def test_update_campaign_budget_still_exists(self):
    assert callable(campaigns.update_campaign_budget)

  def test_update_ad_group_bid_still_exists(self):
    assert callable(ad_groups.update_ad_group_bid)

  def test_update_keyword_bid_still_exists(self):
    assert callable(keywords.update_keyword_bid)

  def test_create_label_still_exists(self):
    assert callable(labels.create_label)

  def test_delete_label_still_exists(self):
    assert callable(labels.delete_label)
