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

"""Order-independent semantic intent extraction and tool routing."""

from dataclasses import dataclass
from dataclasses import replace
from enum import Enum
import re
from types import MappingProxyType


class Domain(str, Enum):
  """Semantic domains that require deterministic routing."""

  CHANGE_CONFIGURATION = "change_configuration"
  REPORTING_METRIC = "reporting_metric"
  CAMPAIGN_AUDIENCE = "campaign_audience"
  RECOMMENDATION = "recommendation"
  DEMOGRAPHIC = "demographic"
  ASSET_GROUP_ASSET = "asset_group_asset"
  EXTERNAL_SYSTEM = "external_system"
  UNKNOWN = "unknown"


class Operation(str, Enum):
  """Data semantics requested from a tool."""

  READ = "read"
  COMPARE = "compare"
  MUTATE = "mutate"


class Effect(str, Enum):
  """Side effects caused while fulfilling an operation."""

  NONE = "none"
  LOCAL_WRITE = "local_write"
  REMOTE_MUTATION = "remote_mutation"


class Delivery(str, Enum):
  """How a result should be delivered."""

  INLINE = "inline"
  PAGINATED = "paginated"
  ARTIFACT = "artifact"


class Detail(str, Enum):
  """The semantic detail requested from a tool."""

  SUMMARY = "summary"
  CONFIGURATION = "configuration"
  PERFORMANCE = "performance"
  GRANULAR = "granular"
  EXPANSION = "expansion"
  STATUS = "status"
  BUDGET = "budget"
  COPY = "copy"
  REMOVAL = "removal"
  APPLICATION = "application"


@dataclass(frozen=True)
class IntentFeatures:
  """Canonical semantic features extracted from a natural-language query."""

  normalized_query: str
  tokens: tuple[str, ...]
  token_set: frozenset[str]
  domain: Domain
  has_change_noun: bool
  explicit_history: bool
  configuration_subject: bool
  metric_subject: bool
  external_subject: bool
  completed: bool
  prospective: bool
  exhaustive: bool
  artifact_requested: bool
  page_requested: bool
  granular_requested: bool
  compare_requested: bool
  performance_requested: bool
  expansion_requested: bool
  read_cue: bool
  explicit_competitive: bool
  account_audit: bool
  recommendation_apply: bool
  campaign_status_mutation: bool
  campaign_budget_mutation: bool
  campaign_audience_copy: bool
  campaign_audience_remove: bool
  mutation_negated: bool
  mutation_advisory: bool
  remote_mutation_guarded: bool
  retrospective_action: bool
  retrospective_speech: bool
  local_write_negated: bool
  continuation_requested: bool
  mixed_history_mutation: bool


@dataclass(frozen=True)
class ToolCapability:
  """Declarative semantic capability of a deterministically routed tool."""

  name: str
  domain: Domain
  operation: Operation
  delivery: Delivery
  detail: Detail
  effect: Effect = Effect.NONE


@dataclass(frozen=True)
class RoutingDecision:
  """A target tool plus unsafe BM25 fallback tools to exclude."""

  target: str | None = None
  preferred_targets: tuple[str, ...] = ()
  excluded_tools: frozenset[str] = frozenset()
  requires_mutation_visibility: bool = False
  exclude_remote_mutations: bool = False
  reason: str = "bm25_fallback"


ROUTE_CATALOG = MappingProxyType(
    {
        "account_audit": "get_optimization_score_summary",
        "asset_group_assets": "list_asset_group_assets",
        "audience_compare": "diff_campaign_audiences",
        "audience_copy": "copy_audiences_between_campaigns",
        "audience_expansion": "list_targeting_expansion_performance",
        "audience_list": "list_campaign_audiences",
        "audience_performance": "list_audience_performance",
        "audience_remove": "remove_campaign_audiences",
        "campaign_budget_mutation": "update_campaign_budget",
        "campaign_status_mutation": "set_campaign_status",
        "change_history_artifact": "export_change_history_csv",
        "change_history_events": "list_change_events",
        "change_history_preview": "get_change_history_extended",
        "change_history_statuses": "list_change_statuses",
        "competitive_pressure": "get_competitive_pressure_report",
        "demographic_performance": "get_demographic_performance",
        "metric_artifact": "export_gaql_csv",
        "metric_preview": "get_campaign_performance",
        "recommendation_apply": "apply_recommendations",
        "recommendation_list": "list_recommendations",
    }
)

TOOL_CAPABILITIES = (
    ToolCapability(
        "get_optimization_score_summary",
        Domain.RECOMMENDATION,
        Operation.READ,
        Delivery.INLINE,
        Detail.SUMMARY,
    ),
    ToolCapability(
        "get_competitive_pressure_report",
        Domain.REPORTING_METRIC,
        Operation.READ,
        Delivery.INLINE,
        Detail.SUMMARY,
    ),
    ToolCapability(
        "export_change_history_csv",
        Domain.CHANGE_CONFIGURATION,
        Operation.READ,
        Delivery.ARTIFACT,
        Detail.CONFIGURATION,
        Effect.LOCAL_WRITE,
    ),
    ToolCapability(
        "get_change_history_extended",
        Domain.CHANGE_CONFIGURATION,
        Operation.READ,
        Delivery.INLINE,
        Detail.CONFIGURATION,
    ),
    ToolCapability(
        "list_change_events",
        Domain.CHANGE_CONFIGURATION,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.GRANULAR,
    ),
    ToolCapability(
        "list_change_statuses",
        Domain.CHANGE_CONFIGURATION,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.SUMMARY,
    ),
    ToolCapability(
        "export_gaql_csv",
        Domain.REPORTING_METRIC,
        Operation.READ,
        Delivery.ARTIFACT,
        Detail.PERFORMANCE,
        Effect.LOCAL_WRITE,
    ),
    ToolCapability(
        "get_campaign_performance",
        Domain.REPORTING_METRIC,
        Operation.READ,
        Delivery.INLINE,
        Detail.PERFORMANCE,
    ),
    ToolCapability(
        "list_campaign_audiences",
        Domain.CAMPAIGN_AUDIENCE,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.CONFIGURATION,
    ),
    ToolCapability(
        "diff_campaign_audiences",
        Domain.CAMPAIGN_AUDIENCE,
        Operation.COMPARE,
        Delivery.INLINE,
        Detail.CONFIGURATION,
    ),
    ToolCapability(
        "list_audience_performance",
        Domain.CAMPAIGN_AUDIENCE,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.PERFORMANCE,
    ),
    ToolCapability(
        "list_targeting_expansion_performance",
        Domain.CAMPAIGN_AUDIENCE,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.EXPANSION,
    ),
    ToolCapability(
        "copy_audiences_between_campaigns",
        Domain.CAMPAIGN_AUDIENCE,
        Operation.MUTATE,
        Delivery.INLINE,
        Detail.COPY,
        Effect.REMOTE_MUTATION,
    ),
    ToolCapability(
        "remove_campaign_audiences",
        Domain.CAMPAIGN_AUDIENCE,
        Operation.MUTATE,
        Delivery.INLINE,
        Detail.REMOVAL,
        Effect.REMOTE_MUTATION,
    ),
    ToolCapability(
        "apply_recommendations",
        Domain.RECOMMENDATION,
        Operation.MUTATE,
        Delivery.INLINE,
        Detail.APPLICATION,
        Effect.REMOTE_MUTATION,
    ),
    ToolCapability(
        "list_recommendations",
        Domain.RECOMMENDATION,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.CONFIGURATION,
    ),
    ToolCapability(
        "set_campaign_status",
        Domain.CHANGE_CONFIGURATION,
        Operation.MUTATE,
        Delivery.INLINE,
        Detail.STATUS,
        Effect.REMOTE_MUTATION,
    ),
    ToolCapability(
        "update_campaign_budget",
        Domain.CHANGE_CONFIGURATION,
        Operation.MUTATE,
        Delivery.INLINE,
        Detail.BUDGET,
        Effect.REMOTE_MUTATION,
    ),
    ToolCapability(
        "get_demographic_performance",
        Domain.DEMOGRAPHIC,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.PERFORMANCE,
    ),
    ToolCapability(
        "list_asset_group_assets",
        Domain.ASSET_GROUP_ASSET,
        Operation.READ,
        Delivery.PAGINATED,
        Detail.CONFIGURATION,
    ),
)

_CAPABILITIES_BY_NAME = MappingProxyType(
    {capability.name: capability for capability in TOOL_CAPABILITIES}
)
_CHANGE_HISTORY_TOOLS = frozenset(
    capability.name
    for capability in TOOL_CAPABILITIES
    if capability.domain == Domain.CHANGE_CONFIGURATION
    and capability.operation == Operation.READ
)
_MUTATION_TOOLS = frozenset(
    capability.name
    for capability in TOOL_CAPABILITIES
    if capability.effect == Effect.REMOTE_MUTATION
)
_COMPETITIVE_PRESSURE_TOOL = ROUTE_CATALOG["competitive_pressure"]
_HISTORY_AND_PRESSURE_TOOLS = frozenset(
    {*_CHANGE_HISTORY_TOOLS, _COMPETITIVE_PRESSURE_TOOL}
)


def _select_capability(
    *,
    domain: Domain,
    operation: Operation,
    delivery: Delivery,
    detail: Detail,
) -> str:
  """Selects the unique tool that declares the requested semantics."""
  matches = [
      capability.name
      for capability in TOOL_CAPABILITIES
      if capability.domain == domain
      and capability.operation == operation
      and capability.delivery == delivery
      and capability.detail == detail
  ]
  if len(matches) != 1:
    raise RuntimeError(
        "Routing capability is missing or ambiguous for "
        f"{domain.value}/{operation.value}/{delivery.value}/{detail.value}."
    )
  target = matches[0]
  if (
      target not in _CAPABILITIES_BY_NAME
      or target not in ROUTE_CATALOG.values()
  ):
    raise RuntimeError(f"Routing capability {target!r} is not cataloged.")
  return target


_FULL_TERMS = frozenset(
    {
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
)
_CHANGE_NOUNS = frozenset(
    {
        "change",
        "changed",
        "changes",
        "edit",
        "edited",
        "edits",
        "modification",
        "modifications",
        "modified",
        "revised",
        "revision",
        "revisions",
        "updated",
    }
)
_CONFIGURATION_TERMS = frozenset(
    {
        "budget",
        "budgets",
        "configuration",
        "bid",
        "bids",
        "setting",
        "settings",
        "status",
        "statuses",
        "targeting",
    }
)
_METRIC_TERMS = frozenset(
    {
        "click",
        "clicks",
        "cpa",
        "cpc",
        "conversion",
        "conversions",
        "cost",
        "ctr",
        "impression",
        "impressions",
        "metric",
        "metrics",
        "performance",
        "result",
        "results",
        "roas",
        "spend",
        "stat",
        "stats",
    }
)
_PROSPECTIVE_TERMS = frozenset(
    {
        "advice",
        "advised",
        "advise",
        "apply",
        "applying",
        "can",
        "consideration",
        "considering",
        "could",
        "future",
        "might",
        "make",
        "making",
        "need",
        "needing",
        "ought",
        "pending",
        "plan",
        "planned",
        "potential",
        "propose",
        "proposed",
        "recommend",
        "recommended",
        "scheduled",
        "should",
        "suggest",
        "suggested",
        "upcoming",
        "want",
        "will",
        "worth",
        "would",
        "improve",
    }
)
_COMPLETED_ACTIONS = frozenset(
    {
        "applied",
        "changed",
        "edited",
        "implemented",
        "made",
        "modified",
        "revised",
        "updated",
    }
)
_PAST_MARKERS = frozenset(
    {
        "ago",
        "already",
        "earlier",
        "last",
        "previously",
        "recent",
        "since",
        "today",
        "yesterday",
    }
)
_WEEKDAY_TERMS = frozenset(
    {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    }
)
_READ_CUES = frozenset(
    {
        "get",
        "list",
        "show",
        "what",
        "which",
    }
)
_FILE_ACTIONS = frozenset(
    {
        "archive",
        "archived",
        "download",
        "downloaded",
        "dump",
        "export",
        "persist",
        "save",
        "saved",
        "send",
        "store",
        "stored",
        "write",
        "writing",
        "written",
    }
)
_FILE_DESTINATIONS = frozenset(
    {
        "csv",
        "disk",
        "excel",
        "file",
        "local",
        "locally",
        "spreadsheet",
        "xlsx",
    }
)
_STATUS_ACTIONS = frozenset(
    {
        "deactivate",
        "disable",
        "enable",
        "pause",
        "reactivate",
        "resume",
        "stop",
        "switch",
        "unpause",
    }
)
_BUDGET_ACTIONS = frozenset(
    {
        "adjust",
        "change",
        "decrease",
        "increase",
        "set",
        "update",
    }
)
_AUDIENCE_REMOVE_ACTIONS = frozenset(
    {
        "clear",
        "delete",
        "detach",
        "purge",
        "remove",
        "wipe",
    }
)
_EXTERNAL_SYSTEM_TERMS = frozenset({"billing", "browser"})
_API_METADATA_TERMS = frozenset(
    {
        "changelog",
        "changelogs",
        "release",
        "releases",
        "revision",
        "revisions",
        "version",
        "versions",
    }
)
_GENERIC_MUTATION_ACTIONS = frozenset(
    {
        "accept",
        "add",
        "adjust",
        "apply",
        "attach",
        "copy",
        "create",
        "delete",
        "decrease",
        "detach",
        "dismiss",
        "exclude",
        "implement",
        "increase",
        "label",
        "manage",
        "bid",
        "remove",
        "set",
        "upload",
        "update",
        *_STATUS_ACTIONS,
        *_AUDIENCE_REMOVE_ACTIONS,
    }
)
_NEGATION_TERMS = frozenset(
    {"except", "neither", "never", "no", "none", "not", "without"}
)


def _normalize_query(query: str) -> tuple[str, tuple[str, ...]]:
  tokens = tuple(re.findall(r"[a-z0-9]+", query.lower()))
  return " ".join(tokens), tokens


def _contains_sequence(
    tokens: tuple[str, ...],
    *sequence: str,
) -> bool:
  width = len(sequence)
  return any(
      tokens[index : index + width] == sequence
      for index in range(len(tokens) - width + 1)
  )


def _has_change_record_phrase(tokens: tuple[str, ...]) -> bool:
  """Returns whether change/edit is a noun modifying a history record."""
  record_terms = {
      "audit",
      "event",
      "events",
      "history",
      "log",
      "logs",
      "trail",
  }
  for index, token in enumerate(tokens):
    if token not in {"change", "edit"}:
      continue
    if bool(set(tokens[index + 1 : index + 3]) & record_terms):
      return True
  return False


def _has_version_subject(tokens: tuple[str, ...]) -> bool:
  return any(re.fullmatch(r"v\d+", token) for token in tokens) or (
      "version" in tokens and any(token.isdigit() for token in tokens)
  )


def _has_negated_action(
    tokens: tuple[str, ...],
    actions: frozenset[str],
) -> bool:
  """Returns whether a nearby negation scopes a mutation action."""
  for index, token in enumerate(tokens):
    if token not in actions:
      continue
    prefix = tokens[max(0, index - 4) : index]
    if bool(set(prefix) & _NEGATION_TERMS):
      return True
    if _contains_sequence(prefix, "don", "t"):
      return True
    suffix = tokens[index + 1 : index + 4]
    if bool(set(suffix) & _NEGATION_TERMS):
      return True
  return False


def _has_retrospective_action_question(tokens: tuple[str, ...]) -> bool:
  """Returns whether an action verb asks about the past instead of acting."""
  retrospective_actions = _GENERIC_MUTATION_ACTIONS | {"change", "edit"}
  if not bool(set(tokens) & retrospective_actions):
    return False
  if "did" in tokens:
    return True
  return _contains_sequence(tokens, "when", "did")


def _has_retrospective_speech(tokens: tuple[str, ...]) -> bool:
  """Returns whether a question asks what happened rather than acting now."""
  return "did" in tokens or _contains_sequence(tokens, "when", "did")


def _has_negated_local_write(tokens: tuple[str, ...]) -> bool:
  """Returns whether the user explicitly rejects artifact creation."""
  if _has_negated_action(tokens, _FILE_ACTIONS):
    return True
  if "without" in tokens and bool(set(tokens) & _FILE_DESTINATIONS):
    return True
  return (
      "inline" in tokens
      and "only" in tokens
      or "preview" in tokens
      and bool(set(tokens) & _NEGATION_TERMS)
  )


def _is_advisory_speech(
    tokens: tuple[str, ...],
    read_cue: bool,
) -> bool:
  """Distinguishes deliberation from a request to perform an action."""
  if read_cue or bool(set(tokens) & {"advice", "advise", "how", "whether"}):
    return True
  return any(
      _contains_sequence(tokens, modal, subject)
      for modal in ("can", "could", "should", "would")
      for subject in ("i", "we")
  )


def extract_intent_features(query: str) -> IntentFeatures:
  """Extracts order-independent semantic features from a query."""
  normalized_query, tokens = _normalize_query(query)
  token_set = frozenset(tokens)

  has_campaign = bool(token_set & {"campaign", "campaigns"})
  has_audience = bool(token_set & {"audience", "audiences"})
  has_recommendation = bool(
      token_set
      & {
          "recommendation",
          "recommendations",
          "rec",
          "recs",
      }
  )
  has_demographic = bool(token_set & {"demographic", "demographics"})
  has_asset_group = _contains_sequence(tokens, "asset", "group") and bool(
      token_set & {"asset", "assets"}
  )
  has_change_noun = bool(token_set & _CHANGE_NOUNS)
  change_record_phrase = _has_change_record_phrase(tokens)

  explicit_history = (
      bool(token_set & {"history", "historical", "changelog", "changelogs"})
      or change_record_phrase
      or (
          "audit" in token_set
          and (
              bool(token_set & {"log", "trail"})
              or ("account" in token_set and has_change_noun)
          )
      )
  )
  configuration_bid_metric = (
      "cpc" in token_set and bool(token_set & {"max", "maximum"})
  ) or (token_set.issuperset({"target", "cpa"}))
  configuration_subject = bool(token_set & _CONFIGURATION_TERMS) or (
      _contains_sequence(tokens, "bid", "strategy") or configuration_bid_metric
  )
  metric_subject = bool(token_set & _METRIC_TERMS) or any(
      _contains_sequence(tokens, *phrase)
      for phrase in (
          ("average", "cpc"),
          ("click", "through", "rate"),
          ("conversion", "rate"),
          ("conversion", "value"),
          ("impression", "share"),
          ("return", "on", "ad", "spend"),
      )
  )
  if configuration_bid_metric:
    metric_subject = False

  version_subject = _has_version_subject(tokens)
  account_data_subject = bool(
      token_set
      & {
          "account",
          "ad",
          "campaign",
          "campaigns",
          "keyword",
          "keywords",
      }
  ) or any(
      (
          has_audience,
          has_recommendation,
          has_demographic,
          has_asset_group,
          configuration_subject,
      )
  )
  api_metadata_subject = bool(token_set & _API_METADATA_TERMS) and (
      "api" in token_set or version_subject
  )
  external_subject = (
      bool(token_set & _EXTERNAL_SYSTEM_TERMS)
      or (api_metadata_subject and not account_data_subject)
      or ("api" in token_set and version_subject and not account_data_subject)
  )
  read_cue = bool(token_set & _READ_CUES)
  historical_timeframe = bool(
      token_set & (_PAST_MARKERS | _WEEKDAY_TERMS)
  ) or any(re.fullmatch(r"(?:19|20)\d{2}", token) for token in tokens)
  completed = bool(token_set & _COMPLETED_ACTIONS) or (
      has_change_noun
      and bool(
          token_set
          & {
              "changed",
              "changes",
              "edits",
              "modifications",
              "revisions",
          }
      )
      and historical_timeframe
  )
  prospective = (
      has_change_noun
      and bool(token_set & _PROSPECTIVE_TERMS)
      and not explicit_history
      and not completed
  )
  prospective = prospective or (
      has_change_noun
      and bool(token_set & {"change", "edit"})
      and (
          configuration_subject
          or bool(
              token_set
              & {
                  "ad",
                  "ads",
                  "campaign",
                  "campaigns",
                  "keyword",
                  "keywords",
              }
          )
      )
      and not read_cue
      and not explicit_history
      and not completed
  )

  exhaustive = (
      bool(token_set & _FULL_TERMS)
      or (
          bool(token_set & {"available", "possible"})
          and bool(token_set & {"far", "longest", "much", "many", "oldest"})
      )
      or (
          token_set.issuperset({"far", "back", "can"})
          or token_set.issuperset({"whatever", "available"})
      )
  )
  artifact_requested = bool(
      token_set
      & {
          "csv",
          "download",
          "dump",
          "excel",
          "export",
          "spreadsheet",
          "xlsx",
      }
  ) or (
      bool(token_set & _FILE_ACTIONS) and bool(token_set & _FILE_DESTINATIONS)
  )
  local_write_negated = _has_negated_local_write(tokens)
  page_requested = bool(token_set & {"page", "compact"})
  continuation_requested = "page" in token_set and bool(
      token_set & {"after", "continue", "continuation", "next"}
  )
  granular_requested = bool(token_set & {"event", "events", "granular"}) or (
      bool(token_set & {"field", "event"}) and "level" in token_set
  )
  compare_requested = bool(
      token_set
      & {
          "between",
          "compare",
          "comparison",
          "comparisons",
          "copied",
          "diff",
          "difference",
          "differences",
          "missing",
      }
  )
  performance_requested = bool(
      token_set
      & {
          "metric",
          "metrics",
          "performance",
          "result",
          "results",
          "stat",
          "stats",
      }
  )
  expansion_requested = "expansion" in token_set
  explicit_competitive = bool(
      token_set
      & {
          "auction",
          "competition",
          "competitive",
          "pressure",
      }
  ) or _contains_sequence(tokens, "impression", "share")
  account_audit = (
      token_set.issuperset({"account", "audit"})
      and not explicit_history
      and not has_change_noun
      and not configuration_subject
  )

  mutation_actions = _GENERIC_MUTATION_ACTIONS | {"turn"}
  has_status_action = has_campaign and (
      bool(token_set & _STATUS_ACTIONS)
      or ("turn" in token_set and bool(token_set & {"off", "on"}))
      or (
          "set" in token_set
          and bool(token_set & {"enabled", "paused", "status"})
      )
  )
  budget_actions = token_set & _BUDGET_ACTIONS
  has_budget_action = (
      has_campaign
      and bool(token_set & {"budget", "budgets"})
      and bool(budget_actions)
      and not (budget_actions == {"change"} and change_record_phrase)
  )
  has_audience_copy_action = (
      has_campaign and has_audience and ("copy" in token_set)
  )
  has_audience_remove_action = (
      has_campaign
      and has_audience
      and (
          bool(token_set & _AUDIENCE_REMOVE_ACTIONS)
          or ("take" in token_set and "off" in token_set)
      )
  )
  has_recommendation_action = has_recommendation and bool(
      token_set & {"accept", "apply", "implement"}
  )
  has_mutation_subject = (
      has_status_action
      or has_budget_action
      or has_audience_copy_action
      or has_audience_remove_action
      or has_recommendation_action
  )
  mutation_negated = has_mutation_subject and _has_negated_action(
      tokens, mutation_actions
  )
  mutation_advisory = (
      has_mutation_subject
      and _is_advisory_speech(tokens, read_cue)
      and not explicit_history
      and not completed
  )
  mutation_guarded = mutation_negated or mutation_advisory
  retrospective_action = _has_retrospective_action_question(tokens)
  retrospective_speech = _has_retrospective_speech(tokens)
  generic_mutation_negated = bool(token_set & _NEGATION_TERMS) or (
      _contains_sequence(tokens, "don", "t")
  )
  generic_mutation_advisory = (
      _is_advisory_speech(tokens, read_cue) and not retrospective_speech
  )
  remote_mutation_guarded = (
      mutation_negated
      or mutation_advisory
      or generic_mutation_negated
      or generic_mutation_advisory
      or retrospective_speech
  )
  mutation_guarded = mutation_guarded or remote_mutation_guarded

  recommendation_apply = (
      has_recommendation_action
      and not explicit_history
      and not completed
      and not artifact_requested
      and not mutation_guarded
      and not retrospective_action
  )
  campaign_status_mutation = (
      has_status_action
      and not explicit_history
      and not completed
      and not mutation_guarded
      and not retrospective_action
  )
  campaign_budget_mutation = (
      has_budget_action
      and not explicit_history
      and not completed
      and not mutation_guarded
      and not retrospective_action
  )
  campaign_audience_copy = (
      has_audience_copy_action
      and not read_cue
      and not explicit_history
      and not mutation_guarded
      and not retrospective_action
  )
  campaign_audience_remove = (
      has_audience_remove_action
      and not explicit_history
      and not completed
      and not mutation_guarded
      and not retrospective_action
  )
  mixed_history_mutation = (
      explicit_history
      and has_mutation_subject
      and not completed
      and not remote_mutation_guarded
  )

  if external_subject:
    domain = Domain.EXTERNAL_SYSTEM
  elif has_audience and (has_campaign or performance_requested):
    domain = Domain.CAMPAIGN_AUDIENCE
  elif has_recommendation:
    domain = Domain.RECOMMENDATION
  elif has_demographic:
    domain = Domain.DEMOGRAPHIC
  elif has_asset_group:
    domain = Domain.ASSET_GROUP_ASSET
  elif metric_subject:
    domain = Domain.REPORTING_METRIC
  elif configuration_subject or has_change_noun or explicit_history:
    domain = Domain.CHANGE_CONFIGURATION
  else:
    domain = Domain.UNKNOWN

  return IntentFeatures(
      normalized_query=normalized_query,
      tokens=tokens,
      token_set=token_set,
      domain=domain,
      has_change_noun=has_change_noun,
      explicit_history=explicit_history,
      configuration_subject=configuration_subject,
      metric_subject=metric_subject,
      external_subject=external_subject,
      completed=completed,
      prospective=prospective,
      exhaustive=exhaustive,
      artifact_requested=artifact_requested,
      page_requested=page_requested,
      granular_requested=granular_requested,
      compare_requested=compare_requested,
      performance_requested=performance_requested,
      expansion_requested=expansion_requested,
      read_cue=read_cue,
      explicit_competitive=explicit_competitive,
      account_audit=account_audit,
      recommendation_apply=recommendation_apply,
      campaign_status_mutation=campaign_status_mutation,
      campaign_budget_mutation=campaign_budget_mutation,
      campaign_audience_copy=campaign_audience_copy,
      campaign_audience_remove=campaign_audience_remove,
      mutation_negated=mutation_negated,
      mutation_advisory=mutation_advisory,
      remote_mutation_guarded=remote_mutation_guarded,
      retrospective_action=retrospective_action,
      retrospective_speech=retrospective_speech,
      local_write_negated=local_write_negated,
      continuation_requested=continuation_requested,
      mixed_history_mutation=mixed_history_mutation,
  )


def _has_change_history_context(features: IntentFeatures) -> bool:
  if features.external_subject or features.metric_subject:
    return False
  if features.retrospective_action:
    return True
  if (
      features.domain == Domain.RECOMMENDATION
      and not features.completed
      and not features.explicit_history
  ):
    return False
  if (
      features.recommendation_apply
      or features.campaign_status_mutation
      or features.campaign_budget_mutation
      or features.campaign_audience_copy
      or features.campaign_audience_remove
      or (features.prospective and not features.completed)
  ):
    return False
  if features.completed and features.has_change_noun:
    return True
  if features.granular_requested and features.has_change_noun:
    return True
  if bool(features.token_set & {"changelog", "changelogs"}) or (
      "audit" in features.token_set
      and bool(features.token_set & {"log", "trail"})
  ):
    return True
  if features.configuration_subject and features.explicit_history:
    return True
  if features.explicit_history and features.domain in {
      Domain.ASSET_GROUP_ASSET,
      Domain.CAMPAIGN_AUDIENCE,
      Domain.DEMOGRAPHIC,
  }:
    return True
  if features.explicit_history and features.domain == Domain.RECOMMENDATION:
    return features.completed or bool(
        features.token_set & {"application", "applied", "change", "changes"}
    )
  if features.has_change_noun and (
      features.explicit_history
      or features.exhaustive
      or bool(
          features.token_set
          & {
              "ago",
              "already",
              "earlier",
              "last",
              "recent",
              "since",
              "today",
              "yesterday",
          }
      )
      or bool(features.token_set & _WEEKDAY_TERMS)
      or any(
          re.fullmatch(r"(?:19|20)\d{2}", token) for token in features.tokens
      )
  ):
    return True
  return (
      features.explicit_history
      and features.domain == Domain.CHANGE_CONFIGURATION
      and features.configuration_subject
  )


def _mutation_targets(features: IntentFeatures) -> tuple[str, ...]:
  """Returns every independently requested remote mutation capability."""
  targets = []
  if features.recommendation_apply:
    targets.append(
        _select_capability(
            domain=Domain.RECOMMENDATION,
            operation=Operation.MUTATE,
            delivery=Delivery.INLINE,
            detail=Detail.APPLICATION,
        )
    )
  if features.campaign_status_mutation:
    targets.append(
        _select_capability(
            domain=Domain.CHANGE_CONFIGURATION,
            operation=Operation.MUTATE,
            delivery=Delivery.INLINE,
            detail=Detail.STATUS,
        )
    )
  if features.campaign_budget_mutation:
    targets.append(
        _select_capability(
            domain=Domain.CHANGE_CONFIGURATION,
            operation=Operation.MUTATE,
            delivery=Delivery.INLINE,
            detail=Detail.BUDGET,
        )
    )
  if features.campaign_audience_copy:
    targets.append(
        _select_capability(
            domain=Domain.CAMPAIGN_AUDIENCE,
            operation=Operation.MUTATE,
            delivery=Delivery.INLINE,
            detail=Detail.COPY,
        )
    )
  if features.campaign_audience_remove:
    targets.append(
        _select_capability(
            domain=Domain.CAMPAIGN_AUDIENCE,
            operation=Operation.MUTATE,
            delivery=Delivery.INLINE,
            detail=Detail.REMOVAL,
        )
    )
  return tuple(dict.fromkeys(targets))


def _dedicated_large_read_target(features: IntentFeatures) -> str | None:
  if features.domain == Domain.ASSET_GROUP_ASSET:
    return _select_capability(
        domain=Domain.ASSET_GROUP_ASSET,
        operation=Operation.READ,
        delivery=Delivery.PAGINATED,
        detail=Detail.CONFIGURATION,
    )
  if features.domain == Domain.DEMOGRAPHIC:
    return _select_capability(
        domain=Domain.DEMOGRAPHIC,
        operation=Operation.READ,
        delivery=Delivery.PAGINATED,
        detail=Detail.PERFORMANCE,
    )
  if features.domain == Domain.RECOMMENDATION:
    return _select_capability(
        domain=Domain.RECOMMENDATION,
        operation=Operation.READ,
        delivery=Delivery.PAGINATED,
        detail=Detail.CONFIGURATION,
    )
  if features.domain == Domain.CAMPAIGN_AUDIENCE:
    if features.expansion_requested:
      return _select_capability(
          domain=Domain.CAMPAIGN_AUDIENCE,
          operation=Operation.READ,
          delivery=Delivery.PAGINATED,
          detail=Detail.EXPANSION,
      )
    if features.performance_requested:
      return _select_capability(
          domain=Domain.CAMPAIGN_AUDIENCE,
          operation=Operation.READ,
          delivery=Delivery.PAGINATED,
          detail=Detail.PERFORMANCE,
      )
    return _select_capability(
        domain=Domain.CAMPAIGN_AUDIENCE,
        operation=Operation.READ,
        delivery=Delivery.PAGINATED,
        detail=Detail.CONFIGURATION,
    )
  return None


def _resolve_intent(query: str) -> RoutingDecision:
  """Resolves a natural-language query using explicit semantic precedence."""
  features = extract_intent_features(query)

  if features.external_subject:
    return RoutingDecision(
        excluded_tools=_HISTORY_AND_PRESSURE_TOOLS,
        reason="external_subject_veto",
    )

  if features.mixed_history_mutation:
    return RoutingDecision(
        excluded_tools=_MUTATION_TOOLS | _HISTORY_AND_PRESSURE_TOOLS,
        exclude_remote_mutations=True,
        reason="ambiguous_history_and_mutation",
    )

  if features.account_audit:
    return RoutingDecision(
        target=_select_capability(
            domain=Domain.RECOMMENDATION,
            operation=Operation.READ,
            delivery=Delivery.INLINE,
            detail=Detail.SUMMARY,
        ),
        reason="account_audit",
    )

  if (
      (features.mutation_negated or features.mutation_advisory)
      and not features.retrospective_action
      and not features.local_write_negated
  ):
    advisory_target = (
        _select_capability(
            domain=Domain.RECOMMENDATION,
            operation=Operation.READ,
            delivery=Delivery.PAGINATED,
            detail=Detail.CONFIGURATION,
        )
        if features.domain == Domain.RECOMMENDATION
        else None
    )
    return RoutingDecision(
        target=advisory_target,
        excluded_tools=_MUTATION_TOOLS
        | (
            _HISTORY_AND_PRESSURE_TOOLS
            if features.has_change_noun
            else frozenset()
        ),
        reason=(
            "negated_mutation"
            if features.mutation_negated
            else "advisory_mutation_mention"
        ),
    )

  if features.retrospective_action:
    if features.metric_subject and not features.configuration_subject:
      return RoutingDecision(
          target=_select_capability(
              domain=Domain.REPORTING_METRIC,
              operation=Operation.READ,
              delivery=Delivery.INLINE,
              detail=Detail.PERFORMANCE,
          ),
          excluded_tools=_MUTATION_TOOLS | _CHANGE_HISTORY_TOOLS,
          exclude_remote_mutations=True,
          reason="retrospective_metric",
      )
    return RoutingDecision(
        target=_select_capability(
            domain=Domain.CHANGE_CONFIGURATION,
            operation=Operation.READ,
            delivery=Delivery.INLINE,
            detail=Detail.CONFIGURATION,
        ),
        excluded_tools=_MUTATION_TOOLS | {_COMPETITIVE_PRESSURE_TOOL},
        exclude_remote_mutations=True,
        reason="retrospective_action",
    )

  mutation_targets = _mutation_targets(features)
  if len(mutation_targets) > 1:
    return RoutingDecision(
        excluded_tools=_MUTATION_TOOLS | _HISTORY_AND_PRESSURE_TOOLS,
        reason="ambiguous_multiple_mutations",
    )
  if mutation_targets:
    excluded = (
        frozenset()
        if features.explicit_competitive
        else _HISTORY_AND_PRESSURE_TOOLS
    )
    return RoutingDecision(
        target=mutation_targets[0],
        excluded_tools=excluded,
        requires_mutation_visibility=True,
        reason="mutation",
    )

  if features.prospective and not features.completed:
    excluded = (
        frozenset()
        if features.explicit_competitive
        else _HISTORY_AND_PRESSURE_TOOLS
    )
    return RoutingDecision(
        excluded_tools=excluded,
        reason="prospective_change",
    )

  mixed_history_metric = (
      features.metric_subject
      and features.explicit_history
      and (
          features.configuration_subject
          or "including" in features.token_set
          or "include" in features.token_set
      )
  )
  if mixed_history_metric:
    if (
        features.artifact_requested or features.exhaustive
    ) and not features.local_write_negated:
      return RoutingDecision(
          target=_select_capability(
              domain=Domain.CHANGE_CONFIGURATION,
              operation=Operation.READ,
              delivery=Delivery.ARTIFACT,
              detail=Detail.CONFIGURATION,
          ),
          preferred_targets=(
              _select_capability(
                  domain=Domain.CHANGE_CONFIGURATION,
                  operation=Operation.READ,
                  delivery=Delivery.ARTIFACT,
                  detail=Detail.CONFIGURATION,
              ),
              _select_capability(
                  domain=Domain.REPORTING_METRIC,
                  operation=Operation.READ,
                  delivery=Delivery.INLINE,
                  detail=Detail.SUMMARY,
              ),
              _select_capability(
                  domain=Domain.REPORTING_METRIC,
                  operation=Operation.READ,
                  delivery=Delivery.ARTIFACT,
                  detail=Detail.PERFORMANCE,
              ),
          ),
          reason="full_mixed_history_multi_capability",
      )
    return RoutingDecision(
        target=_select_capability(
            domain=Domain.REPORTING_METRIC,
            operation=Operation.READ,
            delivery=Delivery.INLINE,
            detail=Detail.SUMMARY,
        ),
        reason="mixed_history_metric",
    )

  metric_change = features.metric_subject and features.has_change_noun
  if metric_change:
    target = (
        _select_capability(
            domain=Domain.REPORTING_METRIC,
            operation=Operation.READ,
            delivery=Delivery.ARTIFACT,
            detail=Detail.PERFORMANCE,
        )
        if features.artifact_requested or features.exhaustive
        else _select_capability(
            domain=Domain.REPORTING_METRIC,
            operation=Operation.READ,
            delivery=Delivery.INLINE,
            detail=Detail.PERFORMANCE,
        )
    )
    return RoutingDecision(
        target=target,
        excluded_tools=_CHANGE_HISTORY_TOOLS,
        reason="metric_change",
    )

  change_history = _has_change_history_context(features)
  if change_history and features.continuation_requested:
    return RoutingDecision(
        target=_select_capability(
            domain=Domain.CHANGE_CONFIGURATION,
            operation=Operation.READ,
            delivery=Delivery.PAGINATED,
            detail=Detail.GRANULAR,
        ),
        reason="granular_change_history",
    )
  if change_history:
    target = (
        _select_capability(
            domain=Domain.CHANGE_CONFIGURATION,
            operation=Operation.READ,
            delivery=Delivery.ARTIFACT,
            detail=Detail.CONFIGURATION,
        )
        if (features.artifact_requested or features.exhaustive)
        and not features.local_write_negated
        else _select_capability(
            domain=Domain.CHANGE_CONFIGURATION,
            operation=Operation.READ,
            delivery=Delivery.INLINE,
            detail=Detail.CONFIGURATION,
        )
    )
    if (features.granular_requested or features.page_requested) and not (
        (features.artifact_requested or features.exhaustive)
        and not features.local_write_negated
    ):
      target = _select_capability(
          domain=Domain.CHANGE_CONFIGURATION,
          operation=Operation.READ,
          delivery=Delivery.PAGINATED,
          detail=Detail.GRANULAR,
      )
    return RoutingDecision(target=target, reason="change_history")

  if (
      features.artifact_requested or features.exhaustive
  ) and not features.explicit_history:
    dedicated_target = _dedicated_large_read_target(features)
    if dedicated_target:
      return RoutingDecision(
          target=dedicated_target,
          excluded_tools=(
              _HISTORY_AND_PRESSURE_TOOLS
              if features.has_change_noun
              else frozenset()
          ),
          reason="dedicated_large_read",
      )

  if features.domain == Domain.CAMPAIGN_AUDIENCE:
    if features.expansion_requested:
      return RoutingDecision(
          target=_select_capability(
              domain=Domain.CAMPAIGN_AUDIENCE,
              operation=Operation.READ,
              delivery=Delivery.PAGINATED,
              detail=Detail.EXPANSION,
          ),
          reason="audience_expansion",
      )
    if features.performance_requested:
      return RoutingDecision(
          target=_select_capability(
              domain=Domain.CAMPAIGN_AUDIENCE,
              operation=Operation.READ,
              delivery=Delivery.PAGINATED,
              detail=Detail.PERFORMANCE,
          ),
          reason="audience_performance",
      )
    if features.compare_requested or (
        "copy" in features.token_set and features.read_cue
    ):
      return RoutingDecision(
          target=_select_capability(
              domain=Domain.CAMPAIGN_AUDIENCE,
              operation=Operation.COMPARE,
              delivery=Delivery.INLINE,
              detail=Detail.CONFIGURATION,
          ),
          reason="audience_comparison",
      )
    return RoutingDecision(
        target=_select_capability(
            domain=Domain.CAMPAIGN_AUDIENCE,
            operation=Operation.READ,
            delivery=Delivery.PAGINATED,
            detail=Detail.CONFIGURATION,
        ),
        reason="audience_list",
    )

  if (
      features.domain == Domain.RECOMMENDATION
      and not features.explicit_history
  ):
    return RoutingDecision(
        target=_select_capability(
            domain=Domain.RECOMMENDATION,
            operation=Operation.READ,
            delivery=Delivery.PAGINATED,
            detail=Detail.CONFIGURATION,
        ),
        reason="recommendation_list",
    )

  if features.explicit_history:
    allow_pressure = bool(features.token_set & {"campaign", "campaigns"}) and (
        features.metric_subject
        or bool(features.token_set & {"performance", "spend"})
        or features.token_set.issuperset({"all", "campaigns"})
    )
    excluded = (
        _CHANGE_HISTORY_TOOLS
        if allow_pressure
        else _HISTORY_AND_PRESSURE_TOOLS
    )
    return RoutingDecision(
        target=(
            _select_capability(
                domain=Domain.REPORTING_METRIC,
                operation=Operation.READ,
                delivery=Delivery.INLINE,
                detail=Detail.SUMMARY,
            )
            if allow_pressure
            else None
        ),
        excluded_tools=excluded,
        reason="unrelated_history",
    )

  if features.has_change_noun:
    return RoutingDecision(
        excluded_tools=_HISTORY_AND_PRESSURE_TOOLS,
        reason="non_historical_change",
    )

  return RoutingDecision()


def resolve_intent(query: str) -> RoutingDecision:
  """Resolves intent and applies effect-level remote-mutation safety."""
  features = extract_intent_features(query)
  decision = _resolve_intent(query)
  if not features.remote_mutation_guarded:
    return decision
  reason = decision.reason
  if reason == "bm25_fallback":
    reason = (
        "retrospective_question"
        if features.retrospective_speech
        else "negated_mutation"
        if bool(features.token_set & _NEGATION_TERMS)
        else "advisory_mutation_mention"
    )
  return replace(
      decision,
      excluded_tools=decision.excluded_tools | _MUTATION_TOOLS,
      requires_mutation_visibility=False,
      exclude_remote_mutations=True,
      reason=reason,
  )
