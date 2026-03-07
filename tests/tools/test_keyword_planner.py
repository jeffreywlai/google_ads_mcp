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

"""Tests for the keyword planner tools."""

from unittest import mock

from ads_mcp.tools import keyword_planner
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import exceptions as google_exceptions
import pytest


CUSTOMER_ID = "1234567890"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.keyword_planner.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


def _make_result(
    text, avg_searches, competition_name, comp_index, low_bid, high_bid
):
  """Helper to create a mock keyword idea result."""
  result = mock.Mock()
  result.text = text
  result.keyword_idea_metrics.avg_monthly_searches = avg_searches
  result.keyword_idea_metrics.competition.name = competition_name
  result.keyword_idea_metrics.competition_index = comp_index
  result.keyword_idea_metrics.low_top_of_page_bid_micros = low_bid
  result.keyword_idea_metrics.high_top_of_page_bid_micros = high_bid
  return result


class TestGenerateKeywordIdeas:

  def test_with_seed_keywords(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = [
        _make_result("running shoes", 100000, "HIGH", 85, 500_000, 2_000_000),
    ]

    result = keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID, keywords=["running shoes"]
    )

    assert result == {
        "keyword_ideas": [
            {
                "keyword": "running shoes",
                "avg_monthly_searches": 100000,
                "competition": "HIGH",
                "competition_index": 85,
                "low_top_of_page_bid_micros": 500_000,
                "high_top_of_page_bid_micros": 2_000_000,
            }
        ],
        "total_ideas": 1,
    }

  def test_with_page_url(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = [
        _make_result("sneakers", 50000, "MEDIUM", 55, 300_000, 1_500_000),
    ]

    result = keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID, page_url="https://example.com/shoes"
    )
    assert result["total_ideas"] == 1
    assert result["keyword_ideas"][0]["keyword"] == "sneakers"

  def test_with_keywords_and_url(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = [
        _make_result("buy shoes", 80000, "HIGH", 90, 600_000, 2_500_000),
    ]

    result = keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID,
        keywords=["shoes"],
        page_url="https://example.com/shoes",
    )
    assert result["total_ideas"] == 1

  def test_multiple_results(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = [
        _make_result("keyword a", 1000, "LOW", 10, 100_000, 500_000),
        _make_result("keyword b", 2000, "MEDIUM", 50, 200_000, 800_000),
        _make_result("keyword c", 3000, "HIGH", 90, 300_000, 1_200_000),
    ]

    result = keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID, keywords=["test"]
    )
    assert result["total_ideas"] == 3
    assert [i["keyword"] for i in result["keyword_ideas"]] == [
        "keyword a",
        "keyword b",
        "keyword c",
    ]

  def test_no_results(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = []

    result = keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID, keywords=["xyzabc123"]
    )
    assert result == {"keyword_ideas": [], "total_ideas": 0}

  def test_raises_without_keywords_or_url(self, mock_ads_client):
    with pytest.raises(ToolError, match="At least one"):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID)

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = []

    keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID,
        keywords=["test"],
        login_customer_id="999",
    )
    mock_ads_client._mock_get.assert_any_call("999")

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
    mock_service.generate_keyword_ideas.side_effect = exc

    with pytest.raises(ToolError):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID, keywords=["test"])

  def test_custom_geo_and_language(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = []
    mock_request = mock_ads_client.get_type.return_value

    keyword_planner.generate_keyword_ideas(
        CUSTOMER_ID,
        keywords=["test"],
        language_id="1003",
        geo_target_ids=["2826", "2124"],
    )

    assert mock_request.language == "languageConstants/1003"
    mock_request.geo_target_constants.append.assert_any_call(
        "geoTargetConstants/2826"
    )
    mock_request.geo_target_constants.append.assert_any_call(
        "geoTargetConstants/2124"
    )

  def test_raises_tool_error_on_resource_exhausted(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.generate_keyword_ideas.return_value = mock.Mock(
        __iter__=mock.Mock(
            side_effect=google_exceptions.ResourceExhausted("quota exceeded")
        )
    )

    with pytest.raises(ToolError, match="quota exceeded"):
      keyword_planner.generate_keyword_ideas(CUSTOMER_ID, keywords=["test"])
