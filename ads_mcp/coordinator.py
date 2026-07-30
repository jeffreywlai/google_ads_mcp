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

"""The coordinator for the Google Ads API MCP."""

from collections.abc import Sequence
import re
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.context import _current_context
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.visibility import get_visibility_rules
from fastmcp.tools.tool import Tool

from ads_mcp.tooling import MUTATE_TAG
from ads_mcp.tooling import compact_search_result_serializer

_SEARCH_RESULT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "mode": {"type": "string"},
        "workflow": {"type": "string"},
        "summary": {"type": "string"},
        "required_args": {"type": "array", "items": {"type": "string"}},
        "optional_args": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "mode", "workflow", "summary"],
    "additionalProperties": False,
}
_SEARCH_TOOL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {
            "type": "array",
            "items": _SEARCH_RESULT_ITEM_SCHEMA,
        }
    },
    "required": ["result"],
    "x-fastmcp-wrap-result": True,
}
_FULL_CHANGE_HISTORY_TOOL = "export_change_history_csv"
_PREVIEW_CHANGE_HISTORY_TOOL = "get_change_history_extended"
_GRANULAR_CHANGE_HISTORY_TOOL = "list_change_events"
_BULK_EXPORT_TOOL = "export_gaql_csv"
_LIST_CAMPAIGN_AUDIENCES_TOOL = "list_campaign_audiences"
_DIFF_CAMPAIGN_AUDIENCES_TOOL = "diff_campaign_audiences"
_AUDIENCE_PERFORMANCE_TOOL = "list_audience_performance"
_CAMPAIGN_PERFORMANCE_TOOL = "get_campaign_performance"
_COMPETITIVE_PRESSURE_TOOL = "get_competitive_pressure_report"
_APPLY_RECOMMENDATIONS_TOOL = "apply_recommendations"
_COPY_CAMPAIGN_AUDIENCES_TOOL = "copy_audiences_between_campaigns"
_REMOVE_CAMPAIGN_AUDIENCES_TOOL = "remove_campaign_audiences"
_SET_CAMPAIGN_STATUS_TOOL = "set_campaign_status"
_UPDATE_CAMPAIGN_BUDGET_TOOL = "update_campaign_budget"
_CHANGE_HISTORY_TOOLS = {
    _FULL_CHANGE_HISTORY_TOOL,
    _PREVIEW_CHANGE_HISTORY_TOOL,
    _GRANULAR_CHANGE_HISTORY_TOOL,
    "list_change_statuses",
}
_FULL_HISTORY_TERMS = {
    "all",
    "complete",
    "entire",
    "every",
    "everything",
    "exhaustive",
    "full",
    "max",
    "maximum",
    "whole",
}
_CHANGE_HISTORY_PHRASES = (
    r"\baudit (?:log|trail)\b",
    r"\b(?:audit|edit|modification|revision) history\b",
    r"\bchange (?:history|log|record|records)\b",
    r"\bchangelogs?\b",
)
_NOUN_CHANGE_TERMS = r"(?:changes?|edits?|modifications?|revisions?)"
_MUTATION_PREFIX = re.compile(
    r"^(?:"
    r"accept|add|adjust|apply|attach|change|clear|consider|copy|create|"
    r"deactivate|decrease|delete|detach|disable|dismiss|edit|enable|implement|"
    r"increase|link|make|pause|purge|reactivate|remove|replace|set|stop|switch|"
    r"take|turn|unlink|unpause|wipe|advise|propose|recommend|suggest|update|"
    r"upload"
    r")\b"
)
_REQUEST_SCAFFOLD = re.compile(
    r"^(?:(?:please|kindly)\s+|"
    r"would it be possible to\s+|"
    r"would you mind\s+|"
    r"would (?:i|we|you) be able to\s+|"
    r"(?:can|could|would|will|may|should)\s+(?:i|we|you)\s+|"
    r"(?:how|what|when|where|why)\s+"
    r"(?:can|could|would|will|may|should)\s+(?:i|we|you)\s+|"
    r"how do (?:i|we|you)\s+|"
    r"what is (?:the )?best way to\s+|"
    r"(?:i|we)\s+(?:need|want|would like)\s+to\s+|"
    r"(?:i|we)\s+(?:need|want|would like)\s+you\s+to\s+|"
    r"(?:i|we)\s+(?:am|are|m)\s+(?:hoping|planning|trying)\s+to\s+|"
    r"(?:i|we)\s+(?:was|were)\s+(?:hoping|planning|trying)\s+to\s+|"
    r"(?:i|we)\s+(?:intend|plan)\s+to\s+|"
    r"(?:i|we)\s+should\s+|"
    r"i d like to\s+|help (?:me|us)\s+|go ahead(?: and)?\s+|"
    r"tell me how to\s+|what if (?:i|we|you)\s+|let (?:s|us)\s+)+"
)
_PROSPECTIVE_CHANGE_PATTERNS = (
    rf"\b(?:advice|advise|plan|propose|recommend|suggest)\b.*"
    rf"\b{_NOUN_CHANGE_TERMS}\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b.*"
    rf"\b(?:advice|advised|advise|plan|planned|propose|proposed|recommend|"
    r"recommended|scheduled|should|suggest|suggested|upcoming)\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b.*\b(?:can|could|might|should|would|will)\b",
    rf"\b(?:advised|planned|potential|proposed|recommended|suggested)\b"
    rf"(?: [a-z0-9]+){{0,3}} \b{_NOUN_CHANGE_TERMS}\b",
    r"\brecommendations? changes?\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b.*\b(?:need|ought|want)\b.*"
    r"\b(?:apply|applying|implement|implementing|make|making)\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b to \b(?:apply|implement|make)\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b.*\bworth\b.*\bmaking\b",
    rf"\bconsidering\b.*\b{_NOUN_CHANGE_TERMS}\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b.*\b(?:consideration|considering)\b",
    rf"\b(?:future|pending|scheduled|upcoming)\b(?: [a-z0-9]+){{0,3}} "
    rf"\b{_NOUN_CHANGE_TERMS}\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b.*\bneeding\b.*"
    r"\b(?:application|implementation)\b",
)
_COMPLETED_CHANGE_PATTERNS = (
    rf"\b{_NOUN_CHANGE_TERMS}\b.*\balready\b.*"
    r"\b(?:applied|implemented|made)\b",
    rf"\b{_NOUN_CHANGE_TERMS}\b.*\b(?:applied|implemented|made)\b.*"
    r"\b(?:already|yesterday|last (?:day|week|month|quarter|year)|"
    r"\d+ (?:days?|weeks?|months?|quarters?|years?) ago|"
    r"(?:19|20)\d{2} \d{2} \d{2})\b",
)
_BULK_EXPORT_PATTERN = re.compile(
    r"\b(?:csv|download|dump|excel|export|spreadsheet|xlsx)\b|"
    r"\b(?:archive|persist|save|send|store|write)\b.*"
    r"\b(?:csv|disk|excel|file|spreadsheet|xlsx)\b|"
    r"\b(?:archive|download|save|store|write)\b.*"
    r"\b(?:local output|locally)\b|"
    r"\blocally\b.*\b(?:archive|download|save|store|write)\b|"
    r"\b(?:archived|downloaded|saved|stored|written)\b.*"
    r"\b(?:local output|locally|to (?:a )?local file)\b|"
    r"\bto (?:a )?(?:local )?file\b"
)
_OVERSIZED_TOOL_FAMILY_PATTERNS = (
    r"\basset group assets?\b",
    r"\baudiences? performance\b",
    r"\bcampaign audiences?\b",
    r"\bdemographic(?:s| performance)?\b",
    r"\brecommendations?\b",
)
_REPORTING_METRIC_TERMS = (
    r"(?:"
    r"average cpc|cpa|cpc|ctr|roas|click through rate|"
    r"conversion rate|conversion value|conversion volume|"
    r"cost per (?:acquisition|conversion)|impression share|"
    r"return on ad spend|"
    r"(?:ad|audience|campaign|demographic|device|geographic|keyword|"
    r"landing page) performance|"
    r"clicks?|conversions?|cost|impressions?|metrics?|performance|results?|"
    r"spend"
    r")"
)
_METRIC_CHANGE_PATTERNS = (
    r"\b(?:day|week|month|quarter|year) over "
    r"(?:day|week|month|quarter|year)\b.*\bchanges?\b",
    r"\bchanges?\b.*\b(?:day|week|month|quarter|year) over "
    r"(?:day|week|month|quarter|year)\b",
    rf"\bchanges?\b (?:in|of|to) \b{_REPORTING_METRIC_TERMS}\b",
    rf"\b{_REPORTING_METRIC_TERMS}\b changes?\b",
)
_MUTABLE_CONFIGURATION_SUBJECT = (
    r"(?:"
    r"(?:account|campaign) (?:budgets?|bid strateg(?:y|ies)|configuration|"
    r"settings?|status(?:es)?|targeting)|"
    r"(?:ads?|ad groups?|keywords?) status(?:es)?"
    r")"
)
_ACCOUNT_CONFIGURATION_HISTORY_PATTERNS = (
    rf"\b{_MUTABLE_CONFIGURATION_SUBJECT} history\b",
    rf"\bhistory (?:for|of) (?:the )?{_MUTABLE_CONFIGURATION_SUBJECT}\b",
    rf"\bhistorical {_MUTABLE_CONFIGURATION_SUBJECT}\b",
    r"\b(?:configuration|settings?|status(?:es)?) history "
    r"(?:for|of) (?:the )?(?:account|campaign)\b",
    r"\b(?:budgets?|bid strateg(?:y|ies)|targeting) history "
    r"(?:for|of) (?:the )?campaigns?(?: \d+)?\b",
    r"\bstatus(?:es)? history (?:for|of) (?:the )?"
    r"(?:ads?|ad groups?|keywords?)(?: \d+)?\b",
    r"\bcampaigns?(?: \d+)?(?: s)? "
    r"(?:budgets?|bid strateg(?:y|ies)|configuration|settings?|"
    r"status(?:es)?|targeting) history\b",
    r"\bhistory (?:for|of) (?:the )?campaigns?(?: \d+)?(?: s)? "
    r"(?:budgets?|bid strateg(?:y|ies)|configuration|settings?|"
    r"status(?:es)?|targeting)\b",
    r"\b(?:ads?|ad groups?|keywords?)(?: \d+)?(?: s)? "
    r"status(?:es)? history\b",
)
_UNRELATED_CHANGE_SUBJECT_PATTERNS = (
    r"\b(?:google ads )?api (?:changes?|changelogs?)\b",
    r"\b(?:changes?|changelogs?)\b.*\b(?:api|releases?|versions?|v\d+)\b",
    r"\b(?:billing|browser) (?:changes?|changelogs?)\b",
    r"\b(?:google ads )?api\b.*" r"\b(?:edit|modification|revision) history\b",
    r"\b(?:billing|browser)\b.*" r"\b(?:edit|modification|revision) history\b",
    r"\b(?:edit|modification|revision) history\b.*" r"\b(?:google ads )?api\b",
    r"\b(?:edit|modification|revision) history\b.*" r"\b(?:billing|browser)\b",
    r"\b(?:v\d+|version \d+)\b.*"
    r"\b(?:edit|modification|revision) history\b",
    r"\b(?:edit|modification|revision) history\b.*"
    r"\b(?:v\d+|version \d+)\b",
    r"\b(?:v\d+|version \d+)\b.*\b(?:changes?|changelogs?)\b",
)
_SUBJECT_FIRST_CAMPAIGN_STATUS_PATTERN = re.compile(
    r"^campaigns?(?: \d+)? "
    r"(?:(?:deactivate|disable|enable|pause|reactivate|resume|stop|"
    r"unpause)|switch (?:off|on))\b"
)
_SUBJECT_FIRST_CAMPAIGN_AUDIENCE_REMOVE_PATTERN = re.compile(
    r"^campaigns?(?: \d+)? audiences? "
    r"(?:clear|delete|detach|purge|remove|wipe)\b"
)
_SUBJECT_FIRST_RECOMMENDATION_APPLY_PATTERN = re.compile(
    r"^(?:all )?(?:recommendations?|recs?) " r"(?:accept|apply|implement)\b"
)


def _prioritize_search_tool(
    tool_name: str,
    tools: Sequence[Tool],
    results: Sequence[Tool],
) -> list[Tool]:
  """Places one intent-selected tool first without changing other rankings."""
  selected_tool = next(
      (tool for tool in tools if tool.name == tool_name),
      None,
  )
  if selected_tool is None:
    return list(results)
  other_results = [tool for tool in results if tool.name != tool_name]
  return [selected_tool, *other_results]


def _deprioritize_change_history_tools(
    results: Sequence[Tool],
    *,
    exclude_change_history: bool = False,
    include_competitive_pressure: bool = False,
) -> list[Tool]:
  """Moves change-history matches behind results for unrelated requests."""
  deprioritized_names = set(_CHANGE_HISTORY_TOOLS)
  if include_competitive_pressure:
    deprioritized_names.add(_COMPETITIVE_PRESSURE_TOOL)
  if exclude_change_history or include_competitive_pressure:
    return [tool for tool in results if tool.name not in deprioritized_names]
  return [tool for tool in results if tool.name not in deprioritized_names] + [
      tool for tool in results if tool.name in deprioritized_names
  ]


def _normalized_search_query(query: str) -> str:
  """Normalizes natural-language search text for phrase matching."""
  return " ".join(re.findall(r"[a-z0-9]+", query.lower()))


def _strip_request_scaffolding(query: str) -> str:
  """Removes common conversational prefixes before intent classification."""
  return _REQUEST_SCAFFOLD.sub("", query)


def _has_completed_change_context(query: str) -> bool:
  """Returns whether change wording names an already completed action."""
  return any(
      re.search(pattern, query) for pattern in _COMPLETED_CHANGE_PATTERNS
  )


def _is_subject_first_mutation_request(query: str) -> bool:
  """Returns whether a bounded subject-first mutation form is requested."""
  imperative = _strip_request_scaffolding(query)
  return bool(
      _SUBJECT_FIRST_CAMPAIGN_STATUS_PATTERN.match(imperative)
      or _SUBJECT_FIRST_CAMPAIGN_AUDIENCE_REMOVE_PATTERN.match(imperative)
      or _SUBJECT_FIRST_RECOMMENDATION_APPLY_PATTERN.match(imperative)
  )


def _is_non_history_change_request(query: str) -> bool:
  """Returns whether change wording is prospective or imperative."""
  if any(re.search(pattern, query) for pattern in _CHANGE_HISTORY_PHRASES):
    return False
  if re.search(r"\bwhat (?:has )?changed\b", query):
    return False
  if re.search(r"\bchange events?\b", query):
    return False
  if _has_completed_change_context(query):
    return False
  if _is_subject_first_mutation_request(query):
    return True
  if _MUTATION_PREFIX.search(_strip_request_scaffolding(query)):
    return True
  return any(
      re.search(pattern, query) for pattern in _PROSPECTIVE_CHANGE_PATTERNS
  )


def _is_metric_change_query(query: str) -> bool:
  """Returns whether change wording describes reporting metric movement."""
  if re.search(r"\b(?:max|maximum|target) (?:cpa|cpc|roas)\b", query):
    return False
  return any(re.search(pattern, query) for pattern in _METRIC_CHANGE_PATTERNS)


def _has_account_configuration_history_context(query: str) -> bool:
  """Returns whether history refers to mutable account configuration."""
  if re.search(r"\b(?:metrics?|performance|spend)\b", query):
    return False
  return any(
      re.search(pattern, query)
      for pattern in _ACCOUNT_CONFIGURATION_HISTORY_PATTERNS
  )


def _is_unrelated_subject_change_query(query: str) -> bool:
  """Returns whether change wording refers outside account configuration."""
  return any(
      re.search(pattern, query)
      for pattern in _UNRELATED_CHANGE_SUBJECT_PATTERNS
  )


def _has_change_history_context(query: str) -> bool:
  """Returns whether a query asks about historical account changes."""
  if _is_unrelated_subject_change_query(query):
    return False
  if any(re.search(pattern, query) for pattern in _CHANGE_HISTORY_PHRASES):
    return True
  if re.search(r"\bwhat (?:has )?changed\b", query):
    return True
  if _is_metric_change_query(query):
    return False
  if _is_non_history_change_request(query):
    return False
  if _has_account_configuration_history_context(query):
    return True
  return bool(
      re.search(rf"\b{_NOUN_CHANGE_TERMS}\b", query)
      or (
          re.search(r"\b\d{4} \d{2} \d{2}\b", query)
          and re.search(r"\bchange\b", query)
      )
  )


def _is_full_change_history_query(query: str) -> bool:
  """Returns whether all available change-history rows are requested."""
  if not _has_change_history_context(query):
    return False
  query_terms = set(query.split())
  if query_terms & _FULL_HISTORY_TERMS:
    return True
  if query_terms & {"download", "export"}:
    return True
  return bool(
      re.search(r"\bmost\b.*\b(?:available|possible)\b", query)
      or re.search(
          r"\bas (?:many|much)\b.*\bas (?:available|possible)\b",
          query,
      )
      or re.search(r"\blongest\b.*\b(?:available|possible)\b", query)
      or re.search(r"\bas far back as (?:available|possible)\b", query)
      or re.search(r"\bas far back as (?:i|we|you) can\b", query)
      or re.search(
          r"\bgoing back as far as (?:available|possible)\b",
          query,
      )
      or re.search(
          r"\bwhatever\b.*\bchange history\b.*\bavailable\b",
          query,
      )
      or re.search(r"\boldest\b.*\b(?:available|possible)\b", query)
  )


def _is_granular_change_history_query(query: str) -> bool:
  """Returns whether a bounded granular change-event read is requested."""
  if re.search(r"\bchange events?\b", query):
    return True
  detail_terms = r"(?:event level|field level|granular)"
  return bool(
      re.search(rf"\b{detail_terms}\b.*\b{_NOUN_CHANGE_TERMS}\b", query)
      or re.search(rf"\b{_NOUN_CHANGE_TERMS}\b.*\b{detail_terms}\b", query)
  )


def _is_oversized_bulk_export_query(query: str) -> bool:
  """Returns whether a known large-result family should use CSV export."""
  return bool(_BULK_EXPORT_PATTERN.search(query)) and any(
      re.search(pattern, query) for pattern in _OVERSIZED_TOOL_FAMILY_PATTERNS
  )


def _is_campaign_audience_list_query(query: str) -> bool:
  """Returns whether campaign audiences are explicitly being listed."""
  if _BULK_EXPORT_PATTERN.search(query):
    return False
  if re.search(
      r"\b(?:between|compare|comparisons?|copy|copied|diff|differences?|"
      r"expansion|metrics?|missing|performance|results?|stats?)\b",
      query,
  ):
    return False
  if not (
      re.search(r"\baudiences?\b", query)
      and re.search(r"\bcampaigns?\b", query)
  ):
    return False
  return bool(
      re.search(r"\b(?:get|list|show)\b", query)
      or re.search(r"\b(?:what|which)\b.*\baudiences?\b", query)
      or re.fullmatch(r"campaign(?: \d+)? audiences?", query)
      or re.search(
          r"\baudiences?\b (?:for|in|on) (?:the )?campaigns?\b",
          query,
      )
      or re.search(r"\bcampaigns? audiences?\b.*\bfor \d+\b", query)
      or re.search(
          r"\bcampaign(?: \d+)? audiences?\b.*"
          r"\b(?:bid modifiers?|criteria|targeting)\b",
          query,
      )
      or re.search(
          r"\baudience targeting\b.*\b(?:campaigns?|campaign \d+)\b",
          query,
      )
      or re.search(r"\bcompact\b.*\bcampaign audiences?\b", query)
      or re.search(
          r"\baudiences?\b.*\btargeted by (?:the )?campaigns?\b",
          query,
      )
      or re.search(r"\bcampaign \d+ s audiences?\b", query)
      or re.search(
          r"\baudience bid modifiers?\b.*\bcampaigns?\b",
          query,
      )
      or re.search(
          r"\b(?:first|next|previous) page\b.*\bcampaign audiences?\b",
          query,
      )
      or re.search(
          r"\b(?:first|next|previous) campaign audiences? page\b",
          query,
      )
      or re.search(r"\bcampaign audiences?\b.*\bpage \d+\b", query)
  )


def _is_campaign_audience_performance_query(query: str) -> bool:
  """Returns whether campaign audience performance metrics are requested."""
  if re.search(r"\bexpansion\b", query):
    return False
  if not (
      re.search(r"\baudiences?\b", query)
      and re.search(r"\bcampaigns?\b", query)
  ):
    return False
  return bool(
      re.search(r"\b(?:metrics?|performance|results?|stats?)\b", query)
  )


def _is_campaign_audience_diff_query(query: str) -> bool:
  """Returns whether campaign audience configurations are being compared."""
  if _BULK_EXPORT_PATTERN.search(query):
    return False
  if _is_campaign_audience_performance_query(query):
    return False
  if not (
      re.search(r"\baudiences?\b", query)
      and re.search(r"\bcampaigns?\b", query)
  ):
    return False
  return bool(
      re.search(
          r"\b(?:between|compare|comparisons?|copy|copied|diff|differences?|"
          r"missing)\b",
          query,
      )
  )


def _is_recommendation_apply_query(query: str) -> bool:
  """Returns whether recommendations are being accepted or applied."""
  imperative = _strip_request_scaffolding(query)
  return bool(
      re.match(r"(?:accept|apply|implement)\b", imperative)
      and re.search(r"\b(?:recommendations?|recs?)\b", imperative)
  ) or bool(_SUBJECT_FIRST_RECOMMENDATION_APPLY_PATTERN.match(imperative))


def _is_campaign_status_mutation_query(query: str) -> bool:
  """Returns whether a campaign status mutation is requested."""
  imperative = _strip_request_scaffolding(query)
  return (
      bool(
          re.match(
              r"(?:deactivate|disable|enable|pause|reactivate|resume|stop|"
              r"unpause|turn off)\b",
              imperative,
          )
          and re.search(r"\bcampaigns?\b", imperative)
      )
      or bool(
          re.match(r"switch\b", imperative)
          and re.search(r"\bcampaigns?\b", imperative)
          and re.search(r"\b(?:off|on)\b", imperative)
      )
      or bool(
          re.match(r"set\b", imperative)
          and re.search(r"\bcampaigns?\b", imperative)
          and re.search(r"\b(?:enabled|paused|status)\b", imperative)
      )
      or bool(_SUBJECT_FIRST_CAMPAIGN_STATUS_PATTERN.match(imperative))
  )


def _is_campaign_budget_mutation_query(query: str) -> bool:
  """Returns whether campaign budget values are being mutated."""
  imperative = _strip_request_scaffolding(query)
  return bool(
      re.match(
          r"(?:adjust|change|decrease|increase|set|update)\b",
          imperative,
      )
      and re.search(r"\bcampaigns?\b", imperative)
      and re.search(r"\bbudgets?\b", imperative)
  )


def _is_campaign_audience_copy_query(query: str) -> bool:
  """Returns whether audiences are being copied between campaigns."""
  imperative = _strip_request_scaffolding(query)
  return bool(
      re.match(r"copy\b", imperative)
      and re.search(r"\baudiences?\b", imperative)
      and re.search(r"\bcampaigns?\b", imperative)
  )


def _is_campaign_audience_remove_query(query: str) -> bool:
  """Returns whether campaign audiences are being removed."""
  imperative = _strip_request_scaffolding(query)
  return (
      bool(
          re.match(r"(?:clear|delete|detach|purge|remove|wipe)\b", imperative)
          and re.search(r"\baudiences?\b", imperative)
          and re.search(r"\bcampaigns?\b", imperative)
      )
      or bool(
          re.match(r"take\b", imperative)
          and re.search(r"\baudiences?\b.*\boff\b", imperative)
          and re.search(r"\bcampaigns?\b", imperative)
      )
      or bool(
          _SUBJECT_FIRST_CAMPAIGN_AUDIENCE_REMOVE_PATTERN.match(imperative)
      )
  )


def _search_intent_target(query: str) -> str | None:
  """Selects an intent-specific tool only when the request is unambiguous."""
  if _is_metric_change_query(query):
    query_terms = set(query.split())
    if _BULK_EXPORT_PATTERN.search(query) or query_terms & _FULL_HISTORY_TERMS:
      return _BULK_EXPORT_TOOL
    return _CAMPAIGN_PERFORMANCE_TOOL
  if _is_full_change_history_query(query):
    return _FULL_CHANGE_HISTORY_TOOL
  if _is_oversized_bulk_export_query(query):
    return _BULK_EXPORT_TOOL
  if _is_recommendation_apply_query(query):
    return _APPLY_RECOMMENDATIONS_TOOL
  if _is_campaign_status_mutation_query(query):
    return _SET_CAMPAIGN_STATUS_TOOL
  if _is_campaign_budget_mutation_query(query):
    return _UPDATE_CAMPAIGN_BUDGET_TOOL
  if _is_campaign_audience_copy_query(query):
    return _COPY_CAMPAIGN_AUDIENCES_TOOL
  if _is_campaign_audience_remove_query(query):
    return _REMOVE_CAMPAIGN_AUDIENCES_TOOL
  if _is_campaign_audience_performance_query(query):
    return _AUDIENCE_PERFORMANCE_TOOL
  if _is_campaign_audience_diff_query(query):
    return _DIFF_CAMPAIGN_AUDIENCES_TOOL
  if _is_campaign_audience_list_query(query):
    return _LIST_CAMPAIGN_AUDIENCES_TOOL
  if _is_granular_change_history_query(query):
    return _GRANULAR_CHANGE_HISTORY_TOOL
  if _has_change_history_context(query):
    return _PREVIEW_CHANGE_HISTORY_TOOL
  return None


def _should_deprioritize_change_history(query: str) -> bool:
  """Returns whether BM25 history matches conflict with the request."""
  if (
      _is_non_history_change_request(query)
      or _is_metric_change_query(query)
      or _is_unrelated_subject_change_query(query)
  ):
    return True
  return _is_unrelated_subject_history(query)


def _is_unrelated_subject_history(query: str) -> bool:
  """Returns whether history refers to a subject other than account changes."""
  return bool(re.search(r"\b(?:historical|history)\b", query)) and not (
      _has_change_history_context(query)
  )


def _has_competitive_pressure_context(query: str) -> bool:
  """Returns whether a history query plausibly requests campaign pressure."""
  if _has_explicit_competitive_pressure_context(query):
    return True
  return bool(re.search(r"\bcampaigns?\b", query)) and bool(
      re.search(r"\b(?:historical|history|performance)\b", query)
  )


def _has_explicit_competitive_pressure_context(query: str) -> bool:
  """Returns whether competitive pressure is named directly."""
  return bool(
      re.search(
          r"\b(?:auction|competition|competitive|impression share|pressure)\b",
          query,
      )
  )


async def _mutation_tools_unlocked() -> bool:
  """Returns whether mutate-tagged tools are unlocked for the session."""
  current_ctx = _current_context.get()
  if current_ctx is None:
    return False

  try:
    rules = await get_visibility_rules(current_ctx)
  except RuntimeError:
    return False

  mutation_tools_unlocked = False
  for rule in rules:
    if set(rule.get("tags", [])) == {MUTATE_TAG} and set(
        rule.get("components", [])
    ) == {"tool"}:
      mutation_tools_unlocked = bool(rule.get("enabled"))

  return mutation_tools_unlocked


class NonMutationVisibleSearchTransform(BM25SearchTransform):
  """BM25 search that keeps all non-mutation tools directly visible."""

  def _make_search_tool(self) -> Tool:
    transform = self

    async def search_tools(
        query: Annotated[str, "Natural language query to search for tools"],
        ctx: Context = None,  # type: ignore[assignment]
    ) -> list[dict[str, object]]:
      """Search for tools using natural language."""
      visible_tools = await transform._get_visible_tools(ctx)
      results = await transform._search(visible_tools, query)
      normalized_query = _normalized_search_query(query)
      target_tool = _search_intent_target(normalized_query)
      target_is_visible = target_tool and any(
          tool.name == target_tool for tool in visible_tools
      )
      if target_tool and target_is_visible:
        results = _prioritize_search_tool(
            target_tool,
            visible_tools,
            results,
        )[: max(1, len(results))]
      if _should_deprioritize_change_history(normalized_query):
        unrelated_history = _is_unrelated_subject_history(normalized_query)
        unrelated_change = _is_unrelated_subject_change_query(normalized_query)
        non_history_change = _is_non_history_change_request(normalized_query)
        results = _deprioritize_change_history_tools(
            results,
            exclude_change_history=_is_metric_change_query(normalized_query),
            include_competitive_pressure=(
                unrelated_change
                or (
                    non_history_change
                    and not _has_explicit_competitive_pressure_context(
                        normalized_query
                    )
                )
                or (
                    unrelated_history
                    and not _has_competitive_pressure_context(normalized_query)
                )
            ),
        )
      return await transform._render_results(results)

    return Tool.from_function(
        fn=search_tools,
        name=self._search_tool_name,
        output_schema=_SEARCH_TOOL_OUTPUT_SCHEMA,
    )

  async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
    if await _mutation_tools_unlocked():
      visible_tools = list(tools)
    else:
      visible_tools = [
          tool for tool in tools if MUTATE_TAG not in set(tool.tags or [])
      ]

    self._always_visible = {tool.name for tool in visible_tools}
    return [*visible_tools, self._make_search_tool(), self._make_call_tool()]

  async def _get_visible_tools(self, ctx) -> Sequence[Tool]:
    """Searches the full enabled catalog, including directly visible tools."""
    return await self.get_tool_catalog(ctx)


# Initialize FastMCP server
mcp_server = FastMCP(
    name="Google Ads API",
    instructions=(
        "Google Ads API MCP server. Read/reporting and docs tools are"
        " directly visible, so call them directly once you know the right"
        " tool. Use search_tools only when the right tool is unclear."
        " Most Google Ads tools take customer_id and optional"
        " login_customer_id, so focus on the other args when choosing a"
        " tool. Use get_tool_guide(topic) only when search results are"
        " ambiguous. Use get_resource_metadata or"
        " search_google_ads_fields when a GAQL query needs"
        " resource-specific field discovery. Use execute_gaql only for"
        " custom read queries not covered by dedicated tools. Use"
        " export_gaql_csv instead of execute_gaql when a bulk extract"
        " would be too large for normal JSON tool output. Keep"
        " the user's requested date range for change-history questions."
        " When they ask for full, all, or maximum change history without"
        " dates, use export_change_history_csv so the result covers the"
        " 90-day change_status window plus the 30-day granular"
        " change_event overlay; do not treat change_event retention as"
        " the limit for all change history. Use"
        " get_change_history_extended for a bounded preview. Keep"
        " call_tool for discovery compatibility, but prefer direct tool"
        " calls once tool names are known. When a list tool returns"
        " returned_count, total_count,"
        " total_page_count, truncated, or next_page_token, use that"
        " metadata to decide whether more pages are needed. Mutation tools"
        " stay hidden until unlock_mutation_tools."
        " Requires a configured google-ads.yaml credentials file."
    ),
    mask_error_details=False,
    client_log_level="error",
    transforms=[
        NonMutationVisibleSearchTransform(
            max_results=8,
            search_result_serializer=compact_search_result_serializer,
        )
    ],
)

mcp_server.disable(tags={MUTATE_TAG}, components={"tool"})
