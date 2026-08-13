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

"""Metamorphic tests for deterministic semantic intent routing."""

from itertools import permutations

import pytest

from ads_mcp.routing.intents import Delivery
from ads_mcp.routing.intents import Effect
from ads_mcp.routing.intents import Operation
from ads_mcp.routing.intents import ROUTE_CATALOG
from ads_mcp.routing.intents import TOOL_CAPABILITIES
from ads_mcp.routing.intents import resolve_intent


_HISTORY_TOOLS = {
    "export_change_history_csv",
    "get_change_history_extended",
    "list_change_events",
    "list_change_statuses",
}
_HISTORY_AND_PRESSURE_TOOLS = {
    *_HISTORY_TOOLS,
    "get_competitive_pressure_report",
}


def test_configuration_history_is_invariant_to_chunk_order():
  for chunks in permutations(("campaign 123", "budget", "history")):
    decision = resolve_intent(" ".join(chunks))
    assert decision.target == "get_change_history_extended"


@pytest.mark.parametrize(
    "query",
    [
        "disable campaign 123",
        "campaign 123 disable",
        "Please disable campaign 123.",
        "Can you disable campaign 123?",
        "Campaign 123: disable.",
    ],
)
def test_mutation_is_invariant_to_scaffolding_and_punctuation(query):
  decision = resolve_intent(query)
  assert decision.target == "set_campaign_status"
  assert decision.requires_mutation_visibility is True
  assert decision.excluded_tools == _HISTORY_AND_PRESSURE_TOOLS


def test_completed_change_overrides_prospective_markers():
  prospective = resolve_intent("all proposed campaign changes")
  completed = resolve_intent(
      "all proposed campaign changes already implemented"
  )

  assert prospective.target is None
  assert prospective.excluded_tools == _HISTORY_AND_PRESSURE_TOOLS
  assert completed.target == "export_change_history_csv"


def test_prospective_change_is_invariant_to_subject_order():
  first = resolve_intent("scheduled campaign changes for next week")
  second = resolve_intent("campaign changes scheduled for next week")

  assert first == second
  assert first.excluded_tools == _HISTORY_AND_PRESSURE_TOOLS


@pytest.mark.parametrize(
    ("query", "expected_tool"),
    [
        ("recent campaign budget history", "get_change_history_extended"),
        ("full campaign budget history", "export_change_history_csv"),
        ("every campaign budget revision", "export_change_history_csv"),
        ("maximum campaign budget history", "export_change_history_csv"),
        ("export campaign budget history", "export_change_history_csv"),
    ],
)
def test_history_fulfillment_selects_preview_or_complete_artifact(
    query, expected_tool
):
  assert resolve_intent(query).target == expected_tool


@pytest.mark.parametrize(
    ("query", "expected_tool"),
    [
        ("export all asset group assets", "list_asset_group_assets"),
        ("download all audience performance", "list_audience_performance"),
        ("full demographic performance", "get_demographic_performance"),
        ("export every campaign audience", "list_campaign_audiences"),
        ("store recommendations locally", "list_recommendations"),
    ],
)
def test_oversized_reads_select_dedicated_snapshot_sources(
    query, expected_tool
):
  assert resolve_intent(query).target == expected_tool


def test_dedicated_source_does_not_reinterpret_recommendation_changes():
  decision = resolve_intent("download all recommendation changes")

  assert decision.target == "list_recommendations"
  assert decision.excluded_tools == _HISTORY_AND_PRESSURE_TOOLS


@pytest.mark.parametrize(
    ("query", "expected_target", "excluded_mutation"),
    [
        ("do not pause campaign 123", None, "set_campaign_status"),
        ("don't pause campaign 123", None, "set_campaign_status"),
        ("should I pause campaign 123", None, "set_campaign_status"),
        (
            "which recommendations should I apply",
            "list_recommendations",
            "apply_recommendations",
        ),
        (
            "show change to campaign 123 budget",
            None,
            "update_campaign_budget",
        ),
    ],
)
def test_negation_and_advisory_language_never_select_mutations(
    query, expected_target, excluded_mutation
):
  decision = resolve_intent(query)

  assert decision.target == expected_target
  assert decision.requires_mutation_visibility is False
  assert excluded_mutation in decision.excluded_tools


def test_suffix_negation_never_selects_a_mutation():
  decision = resolve_intent("pause no campaigns")

  assert decision.target is None
  assert decision.requires_mutation_visibility is False
  assert decision.reason == "negated_mutation"
  assert "set_campaign_status" in decision.excluded_tools


@pytest.mark.parametrize(
    "query",
    [
        "pause none of the campaigns",
        "pause neither campaign",
        "do everything except pause campaign 123",
        "do not delete shared set 123",
        "do not add campaign negative keywords",
        "never remove keyword 123",
    ],
)
def test_generic_negation_guards_every_remote_mutation(query):
  decision = resolve_intent(query)

  assert decision.target is None
  assert decision.requires_mutation_visibility is False
  assert decision.exclude_remote_mutations is True
  assert decision.reason == "negated_mutation"


@pytest.mark.parametrize(
    "query",
    [
        "should I detach shared set from campaign",
        "should I upload these conversions",
        "whether to create this audience",
    ],
)
def test_generic_advisory_speech_guards_every_remote_mutation(query):
  decision = resolve_intent(query)

  assert decision.requires_mutation_visibility is False
  assert decision.exclude_remote_mutations is True
  assert decision.reason == "advisory_mutation_mention"


@pytest.mark.parametrize(
    "query",
    [
        "did we pause campaign 123 yesterday",
        "when did you pause campaign 123",
        "did we apply recommendations yesterday",
        "did we increase campaign 123 budget yesterday",
        "did we remove campaign audiences yesterday",
        "did we delete shared set yesterday",
        "did we change campaign 123 budget yesterday",
        "when did we edit campaign 123",
        "did campaign 123 budget change",
        "did the campaign stop",
    ],
)
def test_retrospective_action_questions_never_mutate(query):
  decision = resolve_intent(query)

  assert decision.target == "get_change_history_extended"
  assert decision.requires_mutation_visibility is False
  assert decision.exclude_remote_mutations is True
  assert decision.reason == "retrospective_action"


@pytest.mark.parametrize(
    "query",
    [
        "did spend increase yesterday",
        "did spend increase",
        "did CTR change yesterday",
    ],
)
def test_retrospective_metric_questions_use_performance_not_mutations(query):
  decision = resolve_intent(query)

  assert decision.target == "get_campaign_performance"
  assert decision.requires_mutation_visibility is False
  assert decision.exclude_remote_mutations is True
  assert decision.reason == "retrospective_metric"


def test_second_person_action_request_remains_a_mutation():
  decision = resolve_intent("can you pause campaign 123")

  assert decision.target == "set_campaign_status"
  assert decision.requires_mutation_visibility is True


@pytest.mark.parametrize(
    "query",
    [
        "pause campaign 123 and apply recommendations",
        "remove campaign audiences and pause campaign 123",
    ],
)
def test_multiple_independent_mutations_require_disambiguation(query):
  decision = resolve_intent(query)

  assert decision.target is None
  assert decision.reason == "ambiguous_multiple_mutations"
  assert decision.requires_mutation_visibility is False
  assert {
      "apply_recommendations",
      "remove_campaign_audiences",
      "set_campaign_status",
  }.issubset(decision.excluded_tools)


@pytest.mark.parametrize(
    ("query", "expected_target"),
    [
        ("campaign audience changes last week", "get_change_history_extended"),
        ("export all campaign audience changes", "export_change_history_csv"),
        ("export all asset group asset changes", "export_change_history_csv"),
        (
            "full demographic targeting change history",
            "export_change_history_csv",
        ),
    ],
)
def test_historical_semantics_override_specialized_resource_nouns(
    query, expected_target
):
  assert resolve_intent(query).target == expected_target


@pytest.mark.parametrize(
    ("query", "expected_target"),
    [
        ("campaign audience history", "get_change_history_extended"),
        ("full campaign audience history", "export_change_history_csv"),
        ("asset group asset history", "get_change_history_extended"),
        ("history of applied recommendations", "get_change_history_extended"),
        ("demographic audience history", "get_change_history_extended"),
    ],
)
def test_explicit_account_history_overrides_specialized_current_state_tools(
    query, expected_target
):
  assert resolve_intent(query).target == expected_target


def test_api_transport_word_does_not_veto_ads_data_history():
  history = resolve_intent("Google Ads API campaign budget history")
  audience_export = resolve_intent("export all campaign audiences via the API")
  api_changelog = resolve_intent("Google Ads API revision history")

  assert history.target == "get_change_history_extended"
  assert audience_export.target == "list_campaign_audiences"
  assert api_changelog.target is None
  assert api_changelog.excluded_tools == _HISTORY_AND_PRESSURE_TOOLS


@pytest.mark.parametrize(
    ("query", "expected_target"),
    [
        (
            "Google Ads API v24 campaign budget history",
            "get_change_history_extended",
        ),
        ("campaign change history using v24", "get_change_history_extended"),
        ("v24 full campaign change history", "export_change_history_csv"),
        (
            "full campaign change history for API version 24",
            "export_change_history_csv",
        ),
    ],
)
def test_api_version_tokens_do_not_veto_in_account_history(
    query, expected_target
):
  assert resolve_intent(query).target == expected_target


@pytest.mark.parametrize(
    "query",
    [
        "show implemented changes",
        "show changes made today",
        "changes since Monday",
        "what changes can I see from last week",
        "what changed yesterday",
        "what changed",
        "show updated account settings",
    ],
)
def test_completed_and_historical_time_language_selects_history(query):
  assert resolve_intent(query).target == "get_change_history_extended"


@pytest.mark.parametrize(
    "query", ["audit account changes", "account change audit"]
)
def test_account_change_audits_are_not_optimization_audits(query):
  assert resolve_intent(query).target == "get_change_history_extended"


def test_unqualified_account_audit_keeps_optimization_semantics():
  assert (
      resolve_intent("complete account audit").target
      == "get_optimization_score_summary"
  )


@pytest.mark.parametrize(
    "chunks",
    [
        ("full campaign", "max", "CPC", "change history"),
        ("full campaign", "maximum", "CPC", "change history"),
        ("full campaign", "target", "CPA", "change history"),
    ],
)
def test_configuration_bid_metric_terms_are_order_independent(chunks):
  for ordered_chunks in permutations(chunks):
    assert (
        resolve_intent(" ".join(ordered_chunks)).target
        == "export_change_history_csv"
    )


@pytest.mark.parametrize(
    "query",
    [
        "export full change history including campaign cost changes",
        "full history of campaign budget and spend changes",
    ],
)
def test_full_mixed_history_preserves_maximum_available_history(query):
  decision = resolve_intent(query)

  assert decision.target == "export_change_history_csv"
  assert decision.preferred_targets == (
      "export_change_history_csv",
      "get_competitive_pressure_report",
      "export_gaql_csv",
  )
  assert decision.reason == "full_mixed_history_multi_capability"


def test_bounded_mixed_history_selects_combined_report():
  decision = resolve_intent("campaign budget and spend history")

  assert decision.target == "get_competitive_pressure_report"
  assert decision.reason == "mixed_history_metric"


@pytest.mark.parametrize(
    "query",
    [
        "full granular change history",
        "export full granular change history",
        "all change events history",
        "download every field-level change",
    ],
)
def test_complete_granular_history_uses_cap_subdividing_export(query):
  assert resolve_intent(query).target == "export_change_history_csv"


@pytest.mark.parametrize(
    "query",
    [
        "export full campaign budget change history",
        "export full campaign budget change log",
        "export all campaign budget change events",
    ],
)
def test_change_record_noun_does_not_become_budget_mutation(query):
  assert resolve_intent(query).target == "export_change_history_csv"


@pytest.mark.parametrize(
    "query",
    [
        "do not export change history, just show a preview",
        "full change history without writing a file",
        "never save change history to CSV",
        "show full change history inline only",
    ],
)
def test_negated_local_write_selects_inline_history_preview(query):
  decision = resolve_intent(query)

  assert decision.target == "get_change_history_extended"
  assert decision.requires_mutation_visibility is False


@pytest.mark.parametrize(
    "query",
    [
        "pause campaign 123 and export full change history",
        "export full change history and pause campaign 123",
        "change campaign 123 budget and export full history",
    ],
)
def test_history_plus_mutation_requires_disambiguation(query):
  decision = resolve_intent(query)

  assert decision.target is None
  assert decision.exclude_remote_mutations is True
  assert decision.reason == "ambiguous_history_and_mutation"


@pytest.mark.parametrize(
    "query",
    [
        "next page of campaign change history",
        "next page of campaign budget change events",
    ],
)
def test_page_delivery_request_selects_paginated_history_capability(query):
  assert resolve_intent(query).target == "list_change_events"


def test_domain_substitution_changes_route_without_history_leakage():
  configuration = resolve_intent("campaign budget history")
  metric = resolve_intent("campaign spend history")
  external = resolve_intent("Google Ads API revision history")

  assert configuration.target == "get_change_history_extended"
  assert metric.target == "get_competitive_pressure_report"
  assert external.target is None
  assert external.excluded_tools == _HISTORY_AND_PRESSURE_TOOLS


@pytest.mark.parametrize(
    ("query", "expected_tool"),
    [
        ("next campaign audience page", "list_campaign_audiences"),
        ("compare campaign audiences", "diff_campaign_audiences"),
        ("campaign audience performance", "list_audience_performance"),
        (
            "campaign audience targeting expansion performance",
            "list_targeting_expansion_performance",
        ),
    ],
)
def test_audience_detail_features_select_disjoint_capabilities(
    query, expected_tool
):
  assert resolve_intent(query).target == expected_tool


def test_route_catalog_and_capabilities_are_structurally_consistent():
  capability_names = {capability.name for capability in TOOL_CAPABILITIES}
  assert set(ROUTE_CATALOG.values()) == capability_names
  assert len(capability_names) == len(TOOL_CAPABILITIES)
  assert all(
      capability.operation == Operation.READ
      for capability in TOOL_CAPABILITIES
      if capability.delivery == Delivery.ARTIFACT
  )
  assert all(
      capability.effect == Effect.LOCAL_WRITE
      for capability in TOOL_CAPABILITIES
      if capability.delivery == Delivery.ARTIFACT
  )
  assert all(
      capability.effect == Effect.REMOTE_MUTATION
      for capability in TOOL_CAPABILITIES
      if capability.operation == Operation.MUTATE
  )
  assert all(
      capability.operation == Operation.READ
      for capability in TOOL_CAPABILITIES
      if capability.effect == Effect.LOCAL_WRITE
  )
