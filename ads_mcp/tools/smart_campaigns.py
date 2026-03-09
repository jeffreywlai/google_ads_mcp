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

"""Tools for Smart Campaign suggestions in Google Ads."""

from typing import Any

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import exceptions as google_exceptions

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools.api import get_ads_client


def _populate_suggestion_info(
    ads_client,
    suggestion_info,
    business_name: str,
    final_url: str | None = None,
    keyword_themes: list[str] | None = None,
    geo_target_id: str = "2840",
    language_code: str = "en",
):
  """Populates a suggestion_info field on a request message."""
  suggestion_info.language_code = language_code

  if final_url:
    suggestion_info.final_url = final_url

  if business_name:
    suggestion_info.business_context.business_name = business_name

  location_info = ads_client.get_type("LocationInfo")
  location_info.geo_target_constant = f"geoTargetConstants/{geo_target_id}"
  suggestion_info.location_list.locations.append(location_info)

  if keyword_themes:
    for theme_text in keyword_themes:
      theme = ads_client.get_type("KeywordThemeInfo")
      theme.free_form_keyword_theme = theme_text
      suggestion_info.keyword_themes.append(theme)


smart_campaign_tool = ads_read_tool(mcp, tags={"smart_campaigns"})


@smart_campaign_tool
def suggest_keyword_themes(
    customer_id: str,
    business_name: str,
    final_url: str,
    geo_target_id: str = "2840",
    language_code: str = "en",
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Suggests keyword themes for a Smart Campaign."""
  ads_client = get_ads_client(login_customer_id)

  suggest_service = ads_client.get_service("SmartCampaignSuggestService")

  request = ads_client.get_type("SuggestKeywordThemesRequest")
  request.customer_id = customer_id
  _populate_suggestion_info(
      ads_client,
      request.suggestion_info,
      business_name,
      final_url,
      geo_target_id=geo_target_id,
      language_code=language_code,
  )

  try:
    response = suggest_service.suggest_keyword_themes(request=request)
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e
  except google_exceptions.GoogleAPICallError as e:
    raise ToolError(str(e)) from e

  themes = []
  for theme in response.keyword_themes:
    if theme.free_form_keyword_theme:
      display_name = theme.free_form_keyword_theme
    elif theme.keyword_theme_constant:
      display_name = theme.keyword_theme_constant.display_name
    else:
      display_name = ""
    themes.append({"display_name": display_name})

  return {"keyword_themes": themes}


@smart_campaign_tool
def suggest_smart_campaign_ad(
    customer_id: str,
    business_name: str,
    final_url: str,
    keyword_themes: list[str] | None = None,
    geo_target_id: str = "2840",
    language_code: str = "en",
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Suggests ad creative (headlines and descriptions) for a Smart Campaign."""
  ads_client = get_ads_client(login_customer_id)

  suggest_service = ads_client.get_service("SmartCampaignSuggestService")

  request = ads_client.get_type("SuggestSmartCampaignAdRequest")
  request.customer_id = customer_id
  _populate_suggestion_info(
      ads_client,
      request.suggestion_info,
      business_name,
      final_url,
      keyword_themes,
      geo_target_id,
      language_code,
  )

  try:
    response = suggest_service.suggest_smart_campaign_ad(request=request)
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e
  except google_exceptions.GoogleAPICallError as e:
    raise ToolError(str(e)) from e

  ad_info = response.ad_info
  return {
      "headlines": [h.text for h in ad_info.headlines],
      "descriptions": [d.text for d in ad_info.descriptions],
  }


@smart_campaign_tool
def suggest_smart_campaign_budget(
    customer_id: str,
    business_name: str,
    final_url: str,
    keyword_themes: list[str] | None = None,
    geo_target_id: str = "2840",
    language_code: str = "en",
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Suggests budget options (low, recommended, high) for a Smart Campaign."""
  ads_client = get_ads_client(login_customer_id)

  suggest_service = ads_client.get_service("SmartCampaignSuggestService")

  request = ads_client.get_type("SuggestSmartCampaignBudgetOptionsRequest")
  request.customer_id = customer_id
  _populate_suggestion_info(
      ads_client,
      request.suggestion_info,
      business_name,
      final_url,
      keyword_themes,
      geo_target_id,
      language_code,
  )

  try:
    response = suggest_service.suggest_smart_campaign_budget_options(
        request=request
    )
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e
  except google_exceptions.GoogleAPICallError as e:
    raise ToolError(str(e)) from e

  budget_options = {}
  if response.low:
    budget_options["low"] = {
        "daily_amount_micros": response.low.daily_amount_micros,
    }
  if response.recommended:
    budget_options["recommended"] = {
        "daily_amount_micros": (response.recommended.daily_amount_micros),
    }
  if response.high:
    budget_options["high"] = {
        "daily_amount_micros": response.high.daily_amount_micros,
    }

  return {"budget_options": budget_options}
