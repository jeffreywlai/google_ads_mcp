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

"""Tools for generating keyword ideas via the Google Ads Keyword Planner."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.api_core import exceptions as google_exceptions
from google.ads.googleads.errors import GoogleAdsException

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tools.api import get_ads_client


@mcp.tool()
def generate_keyword_ideas(
    customer_id: str,
    keywords: list[str] | None = None,
    page_url: str | None = None,
    language_id: str = "1000",
    geo_target_ids: list[str] | None = None,
    include_adult_keywords: bool = False,
    page_size: int = 25,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Generates keyword ideas using the Google Ads Keyword Planner.

  Provide seed keywords and/or a page URL to discover new keyword
  opportunities with search volume, competition, and bid estimates.

  At least one of ``keywords`` or ``page_url`` must be provided.

  Args:
      customer_id: The Google Ads customer ID (digits only).
      keywords: Seed keywords to generate ideas from.
      page_url: A page URL related to your business to generate ideas
          from.
      language_id: Language constant ID (default "1000" for English).
          Common values: 1000=English, 1001=French, 1003=Spanish,
          1004=Italian, 1005=German, 1009=Portuguese, 1010=Korean,
          1011=Japanese, 1017=Chinese (Simplified).
      geo_target_ids: Geo target constant IDs for location targeting.
          Defaults to ["2840"] (United States). Examples: 2840=US,
          2826=UK, 2124=Canada, 2036=Australia.
      include_adult_keywords: Whether to include adult keywords.
      page_size: Maximum number of keyword ideas to return (default 25).
      login_customer_id: Optional manager account ID used to access
          the customer account.

  Returns:
      A dict with keyword_ideas (list of keyword idea dicts with
      keyword text, avg_monthly_searches, competition, and bid
      estimates) and total_ideas count.
  """
  if not keywords and not page_url:
    raise ToolError(
        "At least one of 'keywords' or 'page_url' must be provided."
    )

  ads_client = get_ads_client()
  if login_customer_id:
    ads_client.login_customer_id = login_customer_id

  keyword_plan_idea_service = ads_client.get_service("KeywordPlanIdeaService")

  request = ads_client.get_type("GenerateKeywordIdeasRequest")
  request.customer_id = customer_id
  request.language = f"languageConstants/{language_id}"
  request.include_adult_keywords = include_adult_keywords
  request.page_size = page_size

  if geo_target_ids is None:
    geo_target_ids = ["2840"]
  for geo_id in geo_target_ids:
    request.geo_target_constants.append(f"geoTargetConstants/{geo_id}")

  request.keyword_plan_network = (
      ads_client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS
  )

  if keywords and page_url:
    request.keyword_and_url_seed.url = page_url
    request.keyword_and_url_seed.keywords.extend(keywords)
  elif keywords:
    request.keyword_seed.keywords.extend(keywords)
  elif page_url:
    request.url_seed.url = page_url

  try:
    response = keyword_plan_idea_service.generate_keyword_ideas(
        request=request
    )
    ideas = []
    for result in response:
      metrics = result.keyword_idea_metrics
      ideas.append(
          {
              "keyword": result.text,
              "avg_monthly_searches": metrics.avg_monthly_searches,
              "competition": metrics.competition.name,
              "competition_index": metrics.competition_index,
              "low_top_of_page_bid_micros": (
                  metrics.low_top_of_page_bid_micros
              ),
              "high_top_of_page_bid_micros": (
                  metrics.high_top_of_page_bid_micros
              ),
          }
      )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e
  except google_exceptions.GoogleAPICallError as e:
    raise ToolError(str(e)) from e

  return {
      "keyword_ideas": ideas,
      "total_ideas": len(ideas),
  }
