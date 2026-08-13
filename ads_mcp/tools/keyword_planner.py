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

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools.api import applied_inline_page_size
from ads_mcp.tools.api import build_paginated_list_response
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors


def _keyword_idea_response_page(
    response: Any,
) -> tuple[list[Any], int, str | None]:
  """Extracts one API page while retaining a list-compatible test fallback."""
  if getattr(type(response), "pages", None) is not None:
    page = next(iter(response.pages), None)
    if page is None:
      return [], 0, None
    results = list(page.results)
    total_count = max(int(page.total_size), len(results))
    next_page_token = page.next_page_token or None
    return results, total_count, next_page_token

  results = list(response)
  return results, len(results), None


@ads_read_tool(mcp, tags={"planning", "keywords"})
def generate_keyword_ideas(
    customer_id: str,
    keywords: list[str] | None = None,
    page_url: str | None = None,
    language_id: str = "1000",
    geo_target_ids: list[str] | None = None,
    include_adult_keywords: bool = False,
    page_size: int = 25,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Generates keyword ideas using the Google Ads Keyword Planner.

  The API page is token-bounded without limiting data access. Follow
  next_page_token to retrieve every idea Google makes available.

  Args:
      customer_id: Google Ads customer ID.
      keywords: Optional keyword seed.
      page_url: Optional URL seed. At least one seed is required.
      language_id: Language constant ID. Defaults to 1000 (English).
      geo_target_ids: Geo target constant IDs. Defaults to 2840 (US).
      include_adult_keywords: Whether adult keyword ideas may be included.
      page_size: Requested inline page size. Values above the server's
          token-safe presentation cap are clamped; later pages remain
          available.
      page_token: Google continuation token from the previous response.
      login_customer_id: Optional manager account ID.

  Returns:
      One keyword-idea page plus total counts and continuation metadata.
  """
  if not keywords and not page_url:
    raise ToolError(
        "At least one of 'keywords' or 'page_url' must be provided."
    )
  applied_page_size = applied_inline_page_size(page_size)

  ads_client = get_ads_client(login_customer_id)

  keyword_plan_idea_service = ads_client.get_service("KeywordPlanIdeaService")

  request = ads_client.get_type("GenerateKeywordIdeasRequest")
  request.customer_id = customer_id
  request.language = f"languageConstants/{language_id}"
  request.include_adult_keywords = include_adult_keywords
  request.page_size = applied_page_size
  if page_token:
    request.page_token = page_token

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

  with handle_google_ads_errors():
    response = keyword_plan_idea_service.generate_keyword_ideas(
        request=request
    )
    results, total_count, next_page_token = _keyword_idea_response_page(
        response
    )

  ideas = []
  for result in results:
    metrics = result.keyword_idea_metrics
    ideas.append(
        {
            "keyword": result.text,
            "avg_monthly_searches": metrics.avg_monthly_searches,
            "competition": metrics.competition.name,
            "competition_index": metrics.competition_index,
            "low_top_of_page_bid_micros": (metrics.low_top_of_page_bid_micros),
            "high_top_of_page_bid_micros": (
                metrics.high_top_of_page_bid_micros
            ),
        }
    )

  result = build_paginated_list_response(
      "keyword_ideas",
      ideas,
      total_count=total_count,
      page_size=page_size,
      next_page_token=next_page_token,
  )
  result["total_ideas"] = total_count
  result["delivery"] = (
      "google_api_pagination" if next_page_token else "complete_inline"
  )
  return result
