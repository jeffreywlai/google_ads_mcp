# Copyright 2026 Google LLC
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

"""Focused discovery regressions for remaining usage-report workflows."""

import asyncio
import itertools

from fastmcp import Client

import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.tools import campaigns  # pylint: disable=unused-import
from ads_mcp.tools import docs  # pylint: disable=unused-import
from ads_mcp.tools import negatives  # pylint: disable=unused-import
from ads_mcp.routing.intents import Effect
from ads_mcp.routing.intents import Operation
from ads_mcp.routing.intents import resolve_intent
from ads_mcp.routing.intents import TOOL_CAPABILITIES


LOCATION_MUTATIONS = {
    "add_campaign_location_targets",
    "remove_campaign_location_targets",
}
COMPARATORS = {
    "compare_performance_periods",
    "compare_performance_around_changes",
}


@pytest.mark.parametrize(
    "query,target",
    [
        (
            "add campaign location targets 2840",
            "add_campaign_location_targets",
        ),
        (
            "add negative campaign country targets 2124",
            "add_campaign_location_targets",
        ),
        (
            "exclude Canada from campaign locations",
            "add_campaign_location_targets",
        ),
        (
            "attach country targets to campaign 111",
            "add_campaign_location_targets",
        ),
        (
            "remove campaign location targets 2840",
            "remove_campaign_location_targets",
        ),
        (
            "delete campaign city targeting criterion 555",
            "remove_campaign_location_targets",
        ),
        (
            "detach country exclusions from campaign 111",
            "remove_campaign_location_targets",
        ),
    ],
)
def test_explicit_location_actions_route_to_visible_mutations(query, target):
  decision = resolve_intent(query)
  assert decision.target == target
  assert decision.requires_mutation_visibility is True
  assert decision.exclude_remote_mutations is False


@pytest.mark.parametrize(
    "query",
    [
        "do not add campaign locations",
        "don't remove campaign countries",
        "never exclude locations from campaign",
        "should I remove campaign location targets",
        "how do I add campaign country exclusions",
        "show campaign locations to remove",
        "what campaign countries were removed yesterday",
        "which locations did we exclude from the campaign last week",
        "show the history of campaign location targets",
        "remove campaign location targets and increase campaign budget",
        "add campaign locations and remove campaign locations",
        "add campaign countries and pause campaign 111",
        "add campaign locations and budget history",
    ],
)
def test_location_advisory_retrospective_and_ambiguity_never_expose_mutations(
    query,
):
  decision = resolve_intent(query)
  assert decision.target not in LOCATION_MUTATIONS
  assert (
      LOCATION_MUTATIONS <= decision.excluded_tools
      or decision.exclude_remote_mutations
  )
  assert decision.requires_mutation_visibility is False


@pytest.mark.parametrize(
    "query",
    [
        "customer match jobs summary",
        "summarize CustomerMatch jobs",
        "show customer match upload job statuses",
        "summarize offline user data jobs by status",
        "offline_user_data_job counts",
        "get offline user job summary",
    ],
)
def test_customer_match_job_summary_is_a_guarded_read(query):
  decision = resolve_intent(query)
  assert decision.target == "summarize_customer_match_jobs"
  assert decision.exclude_remote_mutations is True
  assert LOCATION_MUTATIONS <= decision.excluded_tools
  assert decision.requires_mutation_visibility is False


@pytest.mark.parametrize(
    "query",
    [
        "upload users to a new customer match job",
        "create customer match jobs",
        "compare customer match audience performance",
        "customer match jobs revision history",
    ],
)
def test_job_summary_does_not_claim_mutations_or_unrelated_history(query):
  assert resolve_intent(query).target != "summarize_customer_match_jobs"


@pytest.mark.parametrize(
    "query",
    [
        "compare campaign country performance across periods",
        "compare campaign performance periods by country",
        (
            "compare campaign clicks and ROAS "
            "across explicit date windows by country"
        ),
        "compare campaign performance across periods by countries",
        "compare campaign performance across explicit periods by device",
    ],
)
def test_country_and_device_explicit_periods_are_read_workflows(query):
  decision = resolve_intent(query)
  assert decision.target == "compare_performance_periods"
  assert decision.exclude_remote_mutations is True
  assert LOCATION_MUTATIONS <= decision.excluded_tools


@pytest.mark.parametrize(
    "query",
    [
        "compare campaign performance before and after settings changes",
        "compare campaign country performance before and after budget changes",
        "campaign performance before and after target ROAS changes",
        "compare campaign performance around bid changes",
        (
            "can you compare campaign performance "
            "before and after settings changes"
        ),
        "compare campaign performance before and after a budget change",
        "show campaign performance before and after a budget change",
    ],
)
def test_clear_performance_change_boundaries_use_retained_evidence_comparison(
    query,
):
  decision = resolve_intent(query)
  assert decision.target == "compare_performance_around_changes"
  assert decision.exclude_remote_mutations is True
  assert decision.requires_mutation_visibility is False


@pytest.mark.parametrize(
    "grain",
    [
        "city",
        "cities",
        "county",
        "counties",
        "region",
        "regions",
        "network",
        "networks",
        "hour",
        "hourly",
        "ad group",
        "conversion action",
        "keyword",
    ],
)
def test_unsupported_grains_keep_both_comparators_out_of_deterministic_route(
    grain,
):
  for query in (
      f"compare campaign performance across periods by {grain}",
      (
          f"compare campaign performance before and after settings changes "
          f"by {grain}"
      ),
  ):
    assert resolve_intent(query).target not in COMPARATORS


@pytest.mark.parametrize(
    "query",
    [
        (
            "compare campaign performance "
            "before and after proposed settings changes"
        ),
        "compare campaign performance before and after future budget changes",
        "compare campaign performance before and after conversion goal changes",
        "compare campaign performance before and after lifecycle goal changes",
        "export campaign performance before and after settings changes",
        "compare campaign audiences before and after settings changes",
    ],
)
def test_evidence_comparison_does_not_claim_future_goals_or_other_operations(
    query,
):
  assert resolve_intent(query).target != "compare_performance_around_changes"


def test_catalog_effects_include_location_mutations_in_all_read_guards():
  capabilities = {item.name: item for item in TOOL_CAPABILITIES}
  for name in LOCATION_MUTATIONS:
    assert capabilities[name].operation == Operation.MUTATE
    assert capabilities[name].effect == Effect.REMOTE_MUTATION
  for name in (
      "summarize_customer_match_jobs",
      "compare_performance_around_changes",
  ):
    assert capabilities[name].effect == Effect.NONE


def test_location_and_comparison_routes_are_order_independent():
  for chunks, target in (
      (("add", "campaign", "locations"), "add_campaign_location_targets"),
      (
          ("remove", "campaign", "countries"),
          "remove_campaign_location_targets",
      ),
      (
          (
              "compare",
              "campaign performance",
              "before and after settings changes",
          ),
          "compare_performance_around_changes",
      ),
  ):
    for ordered in itertools.permutations(chunks):
      assert resolve_intent(" ".join(ordered)).target == target


@pytest.mark.parametrize(
    "query,target",
    [
        (
            "remove campaign country negative keywords",
            "remove_campaign_negative_keywords",
        ),
        (
            "add campaign negative keywords for country targets",
            "add_campaign_negative_keywords",
        ),
    ],
)
def test_keyword_actions_with_incidental_country_keep_keyword_search(
    query, target
):
  decision = resolve_intent(query)
  assert decision.target not in LOCATION_MUTATIONS
  assert LOCATION_MUTATIONS <= decision.excluded_tools

  async def check():
    async with Client(mcp_server) as client:
      await client.call_tool("unlock_mutation_tools", {})
      result = await client.call_tool("search_tools", {"query": query})
      matches = result.structured_content["result"]
      assert matches[0]["name"] == target
      assert LOCATION_MUTATIONS.isdisjoint({item["name"] for item in matches})

  asyncio.run(check())


@pytest.mark.parametrize(
    "query",
    [
        "remove campaign audiences for country targets",
        "add campaign ad group keywords for country targets",
        "apply campaign recommendations for location changes",
    ],
)
def test_other_action_subjects_never_promote_location_mutations(query):
  decision = resolve_intent(query)
  assert decision.target not in LOCATION_MUTATIONS
  assert LOCATION_MUTATIONS <= decision.excluded_tools


def test_location_search_preserves_visibility_and_advisory_guards():
  async def check():
    async with Client(mcp_server) as client:
      query = "add campaign location targets"
      locked = await client.call_tool("search_tools", {"query": query})
      assert locked.structured_content["result"] == []
      await client.call_tool("unlock_mutation_tools", {})
      active = await client.call_tool("search_tools", {"query": query})
      assert (
          active.structured_content["result"][0]["name"]
          == "add_campaign_location_targets"
      )
      for advisory in (
          "do not add campaign locations",
          "should I remove campaign countries",
          "what campaign locations were removed yesterday",
          "add campaign locations and remove campaign locations",
      ):
        result = await client.call_tool("search_tools", {"query": advisory})
        assert LOCATION_MUTATIONS.isdisjoint(
            {item["name"] for item in result.structured_content["result"]}
        )

  asyncio.run(check())
