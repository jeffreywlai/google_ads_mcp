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

"""Tests for the Smart Campaign suggestion tools."""

from unittest import mock

from ads_mcp.tools import smart_campaigns
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import exceptions as google_exceptions
import pytest


CUSTOMER_ID = "1234567890"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.smart_campaigns.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    yield client


class TestSuggestKeywordThemes:

  def test_returns_themes(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value

    theme1 = mock.Mock()
    theme1.free_form_keyword_theme = "plumbing"
    theme1.keyword_theme_constant = None

    theme2 = mock.Mock()
    theme2.free_form_keyword_theme = ""
    theme2.keyword_theme_constant.display_name = "Nike Shoes"

    mock_response = mock_service.suggest_keyword_themes.return_value
    mock_response.keyword_themes = [theme1, theme2]

    result = smart_campaigns.suggest_keyword_themes(
        CUSTOMER_ID, "Joe's Plumbing", "https://joesplumbing.com"
    )
    assert result == {
        "keyword_themes": [
            {"display_name": "plumbing"},
            {"display_name": "Nike Shoes"},
        ]
    }

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.suggest_keyword_themes.return_value
    mock_response.keyword_themes = []

    smart_campaigns.suggest_keyword_themes(
        CUSTOMER_ID,
        "Test Business",
        "https://example.com",
        login_customer_id="999",
    )
    assert mock_ads_client.login_customer_id == "999"

  def test_raises_tool_error_on_api_error(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    error = mock.Mock()
    error.__str__ = lambda self: "API error"
    exc = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_service.suggest_keyword_themes.side_effect = exc

    with pytest.raises(ToolError):
      smart_campaigns.suggest_keyword_themes(
          CUSTOMER_ID, "Test", "https://example.com"
      )

  def test_raises_tool_error_on_google_api_error(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.suggest_keyword_themes.side_effect = (
        google_exceptions.InvalidArgument("bad request")
    )

    with pytest.raises(ToolError, match="bad request"):
      smart_campaigns.suggest_keyword_themes(
          CUSTOMER_ID, "Test", "https://example.com"
      )


class TestSuggestSmartCampaignAd:

  def test_returns_ad_suggestions(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value

    headline1 = mock.Mock()
    headline1.text = "Best Plumber Near You"
    headline2 = mock.Mock()
    headline2.text = "24/7 Emergency Plumbing"
    desc1 = mock.Mock()
    desc1.text = "Licensed and insured plumber."

    mock_response = mock_service.suggest_smart_campaign_ad.return_value
    mock_response.ad_info.headlines = [headline1, headline2]
    mock_response.ad_info.descriptions = [desc1]

    result = smart_campaigns.suggest_smart_campaign_ad(
        CUSTOMER_ID,
        "Joe's Plumbing",
        "https://joesplumbing.com",
        keyword_themes=["plumbing"],
    )
    assert result == {
        "headlines": [
            "Best Plumber Near You",
            "24/7 Emergency Plumbing",
        ],
        "descriptions": ["Licensed and insured plumber."],
    }

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.suggest_smart_campaign_ad.return_value
    mock_response.ad_info.headlines = []
    mock_response.ad_info.descriptions = []

    smart_campaigns.suggest_smart_campaign_ad(
        CUSTOMER_ID,
        "Test",
        "https://example.com",
        login_customer_id="999",
    )
    assert mock_ads_client.login_customer_id == "999"


class TestSuggestSmartCampaignBudget:

  def test_returns_budget_options(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = (
        mock_service.suggest_smart_campaign_budget_options.return_value
    )
    mock_response.low.daily_amount_micros = 5_000_000
    mock_response.recommended.daily_amount_micros = 10_000_000
    mock_response.high.daily_amount_micros = 20_000_000

    result = smart_campaigns.suggest_smart_campaign_budget(
        CUSTOMER_ID,
        "Joe's Plumbing",
        "https://joesplumbing.com",
    )
    assert result == {
        "budget_options": {
            "low": {"daily_amount_micros": 5_000_000},
            "recommended": {"daily_amount_micros": 10_000_000},
            "high": {"daily_amount_micros": 20_000_000},
        }
    }

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = (
        mock_service.suggest_smart_campaign_budget_options.return_value
    )
    mock_response.low.daily_amount_micros = 1_000_000
    mock_response.recommended.daily_amount_micros = 2_000_000
    mock_response.high.daily_amount_micros = 3_000_000

    smart_campaigns.suggest_smart_campaign_budget(
        CUSTOMER_ID,
        "Test",
        "https://example.com",
        login_customer_id="999",
    )
    assert mock_ads_client.login_customer_id == "999"
