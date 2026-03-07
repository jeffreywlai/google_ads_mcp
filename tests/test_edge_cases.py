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

"""Edge case and stress tests for the MCP server tools."""

from unittest import mock

from ads_mcp.tools import ad_groups
from ads_mcp.tools import ads
from ads_mcp.tools import api
from ads_mcp.tools import campaigns
from ads_mcp.tools import keyword_planner
from ads_mcp.tools import keywords
from ads_mcp.tools import labels
from ads_mcp.tools import negatives
from ads_mcp.tools import smart_campaigns
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import exceptions as google_exceptions
import pytest


CUSTOMER_ID = "1234567890"


# =========================================================================
# GAQL Preprocessing Edge Cases
# =========================================================================


class TestPreprocessGaqlEdgeCases:

  def test_empty_query(self):
    result = api.preprocess_gaql("")
    assert result == " PARAMETERS omit_unselected_resource_names=true"

  def test_whitespace_only_query(self):
    result = api.preprocess_gaql("   ")
    assert result == "    PARAMETERS omit_unselected_resource_names=true"

  def test_query_with_parameters_but_no_include_drafts(self):
    query = "SELECT x FROM y PARAMETERS some_param=true"
    result = api.preprocess_gaql(query)
    # No include_drafts, so PARAMETERS block doesn't get appended to
    assert result == query + " PARAMETERS omit_unselected_resource_names=true"

  def test_query_already_has_omit(self):
    query = "SELECT x FROM y" " PARAMETERS omit_unselected_resource_names=true"
    result = api.preprocess_gaql(query)
    assert result == query  # unchanged

  def test_query_with_include_drafts_and_parameters(self):
    query = "SELECT x FROM y PARAMETERS include_drafts=true"
    result = api.preprocess_gaql(query)
    assert result == query + ", omit_unselected_resource_names=true"

  def test_very_long_query(self):
    fields = ", ".join(f"field_{i}" for i in range(200))
    query = f"SELECT {fields} FROM campaign"
    result = api.preprocess_gaql(query)
    assert "omit_unselected_resource_names=true" in result

  def test_query_with_newlines(self):
    query = """SELECT
      campaign.id,
      campaign.name
    FROM campaign
    WHERE campaign.status = 'ENABLED'"""
    result = api.preprocess_gaql(query)
    assert result.endswith(" PARAMETERS omit_unselected_resource_names=true")


# =========================================================================
# Global Client State Pollution
# =========================================================================


class TestLoginCustomerIdNoStatePollution:
  """Tests that login_customer_id doesn't leak between calls.

  get_ads_client() now accepts login_customer_id and resets it on each
  call, preventing state pollution between tool invocations.
  """

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_login_customer_id_passed_to_get_ads_client(self, mock_get):
    """login_customer_id is passed directly to get_ads_client."""
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    # First call with login_customer_id
    campaigns.set_campaign_status(
        CUSTOMER_ID, "111", "PAUSED", login_customer_id="999"
    )
    mock_get.assert_called_with("999")

    # Second call without — should pass None (no pollution)
    campaigns.set_campaign_status(CUSTOMER_ID, "222", "PAUSED")
    mock_get.assert_called_with(None)

  @mock.patch("ads_mcp.tools.ad_groups.get_ads_client")
  def test_ad_group_login_customer_id_no_pollution(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.set_ad_group_status(
        CUSTOMER_ID, "111", "PAUSED", login_customer_id="888"
    )
    mock_get.assert_called_with("888")

    ad_groups.set_ad_group_status(CUSTOMER_ID, "111", "ENABLED")
    mock_get.assert_called_with(None)

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_label_login_customer_id_no_pollution(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    labels.create_label(CUSTOMER_ID, "Test", login_customer_id="777")
    mock_get.assert_called_with("777")

    labels.create_label(CUSTOMER_ID, "Test2")
    mock_get.assert_called_with(None)


# =========================================================================
# Empty String login_customer_id
# =========================================================================


class TestEmptyStringLoginCustomerId:
  """Empty string is passed through to get_ads_client."""

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_empty_string_passed_to_get_ads_client(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaigns.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    campaigns.set_campaign_status(
        CUSTOMER_ID, "111", "PAUSED", login_customer_id=""
    )
    # Empty string is passed through to get_ads_client
    mock_get.assert_called_with("")


# =========================================================================
# Error Handling Edge Cases
# =========================================================================


class TestErrorHandlingEdgeCases:

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_multiple_errors_in_exception(self, mock_get):
    """GoogleAdsException with multiple errors should join them."""
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    error1 = mock.Mock()
    error1.__str__ = lambda self: "Error one"
    error2 = mock.Mock()
    error2.__str__ = lambda self: "Error two"
    exc = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error1, error2]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_service.mutate_campaigns.side_effect = exc

    with pytest.raises(ToolError, match="Error one\nError two"):
      campaigns.set_campaign_status(CUSTOMER_ID, "111", "PAUSED")

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_permission_denied_error(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_service.generate_keyword_ideas.side_effect = (
        google_exceptions.PermissionDenied("Access denied")
    )

    with pytest.raises(ToolError, match="Access denied"):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID, keywords=["test"])

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_not_found_error(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_service.generate_keyword_ideas.side_effect = (
        google_exceptions.NotFound("Customer not found")
    )

    with pytest.raises(ToolError, match="Customer not found"):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID, keywords=["test"])

  @mock.patch("ads_mcp.tools.smart_campaigns.get_ads_client")
  def test_internal_server_error(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = client.get_service.return_value
    mock_service.suggest_keyword_themes.side_effect = (
        google_exceptions.InternalServerError("Server error")
    )

    with pytest.raises(ToolError, match="Server error"):
      smart_campaigns.suggest_keyword_themes(
          CUSTOMER_ID, "Test Biz", "https://example.com"
      )


# =========================================================================
# Keyword Planner Edge Cases
# =========================================================================


class TestKeywordPlannerEdgeCases:

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_empty_keyword_list(self, mock_get):
    """Empty list is falsy, should fail validation."""
    with pytest.raises(ToolError, match="At least one"):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID, keywords=[])

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_empty_page_url_string(self, mock_get):
    """Empty string URL is falsy, should fail validation."""
    with pytest.raises(ToolError, match="At least one"):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID, page_url="")

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_both_none(self, mock_get):
    with pytest.raises(ToolError, match="At least one"):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID)

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_page_size_zero(self, mock_get):
    """page_size=0 is accepted (API will handle it)."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = iter([])

    result = keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID, keywords=["test"], page_size=0
    )
    assert result == {"keyword_ideas": [], "total_ideas": 0}

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_very_large_page_size(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = iter([])

    result = keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID, keywords=["test"], page_size=10000
    )
    assert result["total_ideas"] == 0

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_keyword_with_special_characters(self, mock_get):
    """Keywords with special chars should pass through to API."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = iter([])

    keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID,
        keywords=["plumber's tools", "a&b", "test<script>"],
    )
    # Should not raise - the API will handle validation

  @mock.patch("ads_mcp.tools.keyword_planner.get_ads_client")
  def test_many_geo_target_ids(self, mock_get):
    """Multiple geo target IDs should all be appended."""
    client = mock.Mock()
    mock_get.return_value = client
    request = client.get_type.return_value
    mock_service = client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = iter([])

    keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID,
        keywords=["test"],
        geo_target_ids=["2840", "2826", "2124"],
    )
    assert request.geo_target_constants.append.call_count == 3


# =========================================================================
# Negatives: GAQL Interpolation Edge Cases
# =========================================================================


class TestGaqlInterpolation:
  """Tests for f-string interpolation into GAQL queries.

  Several tools interpolate IDs directly into GAQL strings. While GAQL
  is not SQL and the Google Ads API rejects malformed queries, we should
  verify the behavior.
  """

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_shared_set_id_with_spaces(self, mock_get):
    """shared_set_id with spaces creates malformed query."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value

    # The API should be called with the malformed query - it's the
    # API's job to reject it, not ours.
    mock_service.search_stream.return_value = []

    result = negatives.list_shared_set_keywords(CUSTOMER_ID, "123 OR 1=1")
    # Query is constructed but the API would reject it
    call_args = mock_service.search_stream.call_args
    assert "123 OR 1=1" in call_args.kwargs["query"]

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_campaign_id_with_injection_attempt(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_service.search_stream.return_value = []

    negatives.list_campaign_negative_keywords(CUSTOMER_ID, "123; DROP TABLE")
    call_args = mock_service.search_stream.call_args
    assert "123; DROP TABLE" in call_args.kwargs["query"]


# =========================================================================
# Empty List Operations
# =========================================================================


class TestEmptyListOperations:

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_add_empty_keywords_list(self, mock_get):
    """Adding empty keywords list should call mutate with no ops."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_shared_criteria.return_value
    mock_response.results = []

    result = negatives.add_shared_set_keywords(CUSTOMER_ID, "111", keywords=[])
    mock_service.mutate_shared_criteria.assert_called_once()
    call_args = mock_service.mutate_shared_criteria.call_args
    assert call_args.kwargs["operations"] == []
    assert result == {"resource_names": []}

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_remove_empty_criterion_ids(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_shared_criteria.return_value
    mock_response.results = []

    result = negatives.remove_shared_set_keywords(
        CUSTOMER_ID, "111", criterion_ids=[]
    )
    assert result == {"resource_names": []}

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_manage_campaign_labels_empty_ids(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = mock.Mock()
    mock_response = mock_service.mutate_campaign_labels.return_value
    mock_response.results = []

    def get_service(name):
      if name == "CampaignLabelService":
        return mock_service
      return mock.Mock()

    client.get_service.side_effect = get_service

    result = labels.manage_campaign_labels(
        CUSTOMER_ID, "111", campaign_ids=[], action="APPLY"
    )
    assert result == {"resource_names": []}

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_manage_ad_group_labels_empty_ids(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client

    mock_service = mock.Mock()
    mock_response = mock_service.mutate_ad_group_labels.return_value
    mock_response.results = []

    def get_service(name):
      if name == "AdGroupLabelService":
        return mock_service
      return mock.Mock()

    client.get_service.side_effect = get_service

    result = labels.manage_ad_group_labels(
        CUSTOMER_ID, "111", ad_group_ids=[], action="APPLY"
    )
    assert result == {"resource_names": []}

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_remove_campaign_negatives_empty_list(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaign_criteria.return_value
    mock_response.results = []

    result = negatives.remove_campaign_negative_keywords(
        CUSTOMER_ID, "111", criterion_ids=[]
    )
    assert result == {"resource_names": []}


# =========================================================================
# Budget / Bid Micros Edge Cases
# =========================================================================


class TestMicrosEdgeCases:

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_zero_budget_micros(self, mock_get):
    """Zero budget should be passed to API (API validates)."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_op = client.get_type.return_value
    mock_response = mock_service.mutate_campaign_budgets.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    campaigns.update_campaign_budget(CUSTOMER_ID, "111", 0)
    assert mock_op.update.amount_micros == 0

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_negative_budget_micros(self, mock_get):
    """Negative budget should be passed to API (API validates)."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_op = client.get_type.return_value
    mock_response = mock_service.mutate_campaign_budgets.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    campaigns.update_campaign_budget(CUSTOMER_ID, "111", -1_000_000)
    assert mock_op.update.amount_micros == -1_000_000

  @mock.patch("ads_mcp.tools.campaigns.get_ads_client")
  def test_very_large_budget_micros(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_op = client.get_type.return_value
    mock_response = mock_service.mutate_campaign_budgets.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    huge = 999_999_999_999_999
    campaigns.update_campaign_budget(CUSTOMER_ID, "111", huge)
    assert mock_op.update.amount_micros == huge

  @mock.patch("ads_mcp.tools.ad_groups.get_ads_client")
  def test_zero_cpc_bid(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_op = client.get_type.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.update_ad_group_bid(CUSTOMER_ID, "111", 0)
    assert mock_op.update.cpc_bid_micros == 0

  @mock.patch("ads_mcp.tools.keywords.get_ads_client")
  def test_negative_keyword_bid(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_op = client.get_type.return_value
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    keywords.update_keyword_bid(CUSTOMER_ID, "111", "222", -500_000)
    assert mock_op.update.cpc_bid_micros == -500_000


# =========================================================================
# Smart Campaign Edge Cases
# =========================================================================


class TestSmartCampaignEdgeCases:

  @mock.patch("ads_mcp.tools.smart_campaigns.get_ads_client")
  def test_empty_keyword_themes_list(self, mock_get):
    """Empty list is falsy, so keyword_themes loop is skipped."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.suggest_smart_campaign_ad.return_value
    mock_response.ad_info.headlines = []
    mock_response.ad_info.descriptions = []

    result = smart_campaigns.suggest_smart_campaign_ad(
        CUSTOMER_ID,
        "Test Business",
        "https://example.com",
        keyword_themes=[],
    )
    assert result == {"headlines": [], "descriptions": []}

  @mock.patch("ads_mcp.tools.smart_campaigns.get_ads_client")
  def test_unicode_business_name(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.suggest_keyword_themes.return_value
    mock_response.keyword_themes = []

    result = smart_campaigns.suggest_keyword_themes(
        CUSTOMER_ID,
        "Tokyo Ramen \u6771\u4eac\u30e9\u30fc\u30e1\u30f3",
        "https://example.com",
    )
    assert result == {"keyword_themes": []}

  @mock.patch("ads_mcp.tools.smart_campaigns.get_ads_client")
  def test_keyword_theme_with_empty_fields(self, mock_get):
    """Theme with both fields empty should return empty string."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value

    theme = mock.Mock()
    theme.free_form_keyword_theme = ""
    theme.keyword_theme_constant = None

    mock_response = mock_service.suggest_keyword_themes.return_value
    mock_response.keyword_themes = [theme]

    result = smart_campaigns.suggest_keyword_themes(
        CUSTOMER_ID, "Test", "https://example.com"
    )
    assert result == {"keyword_themes": [{"display_name": ""}]}

  @mock.patch("ads_mcp.tools.smart_campaigns.get_ads_client")
  def test_budget_with_missing_tiers(self, mock_get):
    """Response with falsy budget tiers should omit them."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = (
        mock_service.suggest_smart_campaign_budget_options.return_value
    )
    # Simulate only "recommended" being present
    mock_response.low = None
    mock_response.recommended.daily_amount_micros = 10_000_000
    mock_response.high = None

    result = smart_campaigns.suggest_smart_campaign_budget(
        CUSTOMER_ID, "Test", "https://example.com"
    )
    assert result == {
        "budget_options": {
            "recommended": {"daily_amount_micros": 10_000_000},
        }
    }


# =========================================================================
# Labels Edge Cases
# =========================================================================


class TestLabelEdgeCases:

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_label_with_empty_name(self, mock_get):
    """Empty name should be passed to API (API validates)."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_op = client.get_type.return_value
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    labels.create_label(CUSTOMER_ID, "")
    assert mock_op.create.name == ""

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_label_with_very_long_name(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_op = client.get_type.return_value
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    long_name = "A" * 1000
    labels.create_label(CUSTOMER_ID, long_name)
    assert mock_op.create.name == long_name

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_label_with_unicode_name(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_op = client.get_type.return_value
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    labels.create_label(CUSTOMER_ID, "\u2728 Promo \u2728")
    assert mock_op.create.name == "\u2728 Promo \u2728"

  @mock.patch("ads_mcp.tools.labels.get_ads_client")
  def test_manage_campaign_labels_many_campaigns(self, mock_get):
    """Applying label to 100 campaigns should create 100 operations."""
    client = mock.Mock()
    mock_get.return_value = client

    mock_label_service = mock.Mock()
    mock_campaign_service = mock.Mock()
    mock_campaign_label_service = mock.Mock()

    campaign_ids = [str(i) for i in range(100)]
    mock_response = (
        mock_campaign_label_service.mutate_campaign_labels.return_value
    )
    mock_response.results = [
        mock.Mock(resource_name=f"x~{i}") for i in campaign_ids
    ]

    def get_service(name):
      if name == "CampaignLabelService":
        return mock_campaign_label_service
      if name == "CampaignService":
        return mock_campaign_service
      return mock_label_service

    client.get_service.side_effect = get_service

    result = labels.manage_campaign_labels(
        CUSTOMER_ID, "111", campaign_ids, "APPLY"
    )
    assert len(result["resource_names"]) == 100


# =========================================================================
# Ads Module: Single get_ads_client Call
# =========================================================================


class TestAdsSingleClientFetch:
  """set_ad_status calls get_ads_client once (consolidated tool)."""

  @mock.patch("ads_mcp.tools.ads.get_ads_client")
  def test_set_ad_status_calls_get_client_once(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ads.set_ad_status(CUSTOMER_ID, "111", "222", "PAUSED")
    assert mock_get.call_count == 1

  @mock.patch("ads_mcp.tools.ads.get_ads_client")
  def test_set_ad_status_login_customer_id(self, mock_get):
    """login_customer_id is passed to get_ads_client."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_ad_group_ads.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ads.set_ad_status(
        CUSTOMER_ID, "111", "222", "PAUSED", login_customer_id="999"
    )
    mock_get.assert_any_call("999")


# =========================================================================
# Negatives: Large Batch Operations
# =========================================================================


class TestLargeBatchOperations:

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_add_many_keywords_to_shared_set(self, mock_get):
    """Adding 500 keywords should create 500 operations."""
    client = mock.Mock()
    client.enums.KeywordMatchTypeEnum.__getitem__ = mock.Mock(
        return_value=mock.Mock(value=1)
    )
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_shared_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name=f"x/{i}") for i in range(500)
    ]

    kws = [{"text": f"keyword_{i}", "match_type": "BROAD"} for i in range(500)]
    result = negatives.add_shared_set_keywords(
        CUSTOMER_ID, "111", keywords=kws
    )
    assert len(result["resource_names"]) == 500

    call_args = mock_service.mutate_shared_criteria.call_args
    assert len(call_args.kwargs["operations"]) == 500

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_add_campaign_negatives_large_batch(self, mock_get):
    client = mock.Mock()
    client.enums.KeywordMatchTypeEnum.__getitem__ = mock.Mock(
        return_value=mock.Mock(value=2)
    )
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaign_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name=f"x/{i}") for i in range(200)
    ]

    kws = [{"text": f"kw_{i}", "match_type": "EXACT"} for i in range(200)]
    result = negatives.add_campaign_negative_keywords(
        CUSTOMER_ID, "111", keywords=kws
    )
    assert len(result["resource_names"]) == 200


# =========================================================================
# Resource Name Construction Edge Cases
# =========================================================================


class TestResourceNameConstruction:

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_remove_shared_set_keywords_resource_format(self, mock_get):
    """Verify resource name format for shared criteria removal."""
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_shared_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    negatives.remove_shared_set_keywords(
        CUSTOMER_ID, "555", criterion_ids=["999"]
    )
    call_args = mock_service.mutate_shared_criteria.call_args
    op = call_args.kwargs["operations"][0]
    assert op.remove == (f"customers/{CUSTOMER_ID}/sharedCriteria/555~999")

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_detach_shared_set_resource_format(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaign_shared_sets.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    negatives.detach_shared_set_from_campaign(CUSTOMER_ID, "222", "333")
    call_args = mock_service.mutate_campaign_shared_sets.call_args
    op = call_args.kwargs["operations"][0]
    assert op.remove == (f"customers/{CUSTOMER_ID}/campaignSharedSets/222~333")

  @mock.patch("ads_mcp.tools.negatives.get_ads_client")
  def test_remove_campaign_negatives_resource_format(self, mock_get):
    client = mock.Mock()
    mock_get.return_value = client
    mock_service = client.get_service.return_value
    mock_response = mock_service.mutate_campaign_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    negatives.remove_campaign_negative_keywords(
        CUSTOMER_ID, "444", criterion_ids=["888"]
    )
    call_args = mock_service.mutate_campaign_criteria.call_args
    op = call_args.kwargs["operations"][0]
    assert op.remove == (f"customers/{CUSTOMER_ID}/campaignCriteria/444~888")
