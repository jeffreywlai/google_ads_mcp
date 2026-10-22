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
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.context import _current_context
from fastmcp.server.server import _is_backend_tool
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.visibility import get_session_transforms
from fastmcp.server.transforms.visibility import is_enabled
from fastmcp.server.transforms.visibility import Visibility
from fastmcp.tools.tool import Tool

from ads_mcp.routing import resolve_intent
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
        "locked": {"type": "boolean"},
        "required_action": {"type": "string"},
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


def _prioritize_search_tools(
    tool_names: Sequence[str],
    tools: Sequence[Tool],
    results: Sequence[Tool],
) -> list[Tool]:
  """Places intent-selected tools first without changing other rankings."""
  tools_by_name = {tool.name: tool for tool in tools}
  selected_tools = [
      tools_by_name[tool_name]
      for tool_name in tool_names
      if tool_name in tools_by_name
  ]
  selected_names = {tool.name for tool in selected_tools}
  other_results = [tool for tool in results if tool.name not in selected_names]
  return [*selected_tools, *other_results]


async def _mutation_tools_unlocked() -> bool:
  """Returns whether mutate-tagged tools are unlocked for the session."""
  current_ctx = _current_context.get()
  if current_ctx is None:
    return False

  try:
    rules = await get_session_transforms(current_ctx)
  except RuntimeError:
    return False

  mutation_tools_unlocked = False
  for rule in rules:
    if _is_mutation_lock_rule(rule):
      mutation_tools_unlocked = rule._enabled  # pylint: disable=protected-access

  return mutation_tools_unlocked


def _is_mutation_lock_rule(rule: Visibility) -> bool:
  """Matches only the project's tag-wide session mutation lock controls."""
  return (
      rule.tags == {MUTATE_TAG}
      and rule.components == {"tool"}
      and rule.names is None
      and rule.keys is None
      and rule.version is None
      and not rule.match_all
  )


class NonMutationVisibleSearchTransform(BM25SearchTransform):
  """BM25 search with deterministic semantic routing and visibility safety."""

  def _make_search_tool(self) -> Tool:
    transform = self

    async def search_tools(
        query: Annotated[str, "Natural language query to search for tools"],
        ctx: Context = None,  # type: ignore[assignment]
    ) -> list[dict[str, object]]:
      """Search for tools using natural language."""
      visible_tools = await transform._get_visible_tools(  # pylint: disable=protected-access
          ctx
      )
      decision = resolve_intent(query)
      if decision.exclude_remote_mutations:
        visible_tools = [
            tool
            for tool in visible_tools
            if MUTATE_TAG not in set(tool.tags or [])
        ]
      results = await transform._search(  # pylint: disable=protected-access
          visible_tools, query
      )
      preferred_targets = decision.preferred_targets or (
          (decision.target,) if decision.target else ()
      )
      visible_target_count = sum(
          tool.name in preferred_targets for tool in visible_tools
      )
      if preferred_targets and visible_target_count:
        results = _prioritize_search_tools(
            preferred_targets,
            visible_tools,
            results,
        )[: max(visible_target_count, len(results))]
      elif decision.requires_mutation_visibility:
        results = []
      if decision.excluded_tools:
        results = [
            tool
            for tool in results
            if tool.name not in decision.excluded_tools
        ]
      if decision.exclude_remote_mutations:
        results = [
            tool for tool in results if MUTATE_TAG not in set(tool.tags or [])
        ]
      rendered = await transform._render_results(  # pylint: disable=protected-access
          results
      )
      unlocked = await _mutation_tools_unlocked()
      for item in rendered:
        if item["mode"] == "mutate":
          item["locked"] = not unlocked
          if not unlocked:
            item["required_action"] = "Call unlock_mutation_tools before use."
      return rendered

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
    """Adds authorized local mutation descriptions without unlocking calls."""
    catalog = list(await self.get_tool_catalog(ctx))
    names = {tool.name for tool in catalog}
    # These are public schema descriptions, not callable registrations.
    # Keep authorization checks while looking past the session mutation lock.
    # FastMCP's private provider APIs are pinned and covered by access tests.
    descriptors = []
    local_tools = await ctx.fastmcp._local_provider.list_tools()  # pylint: disable=protected-access
    for tool in local_tools:
      if tool.name in names or MUTATE_TAG not in set(tool.tags or []):
        continue
      authorized = await ctx.fastmcp._get_tool(tool.name)  # pylint: disable=protected-access
      if authorized is not None:
        descriptors.append(authorized)
    # _get_tool retains auth but skips server/session visibility. Replay all
    # explicit visibility rules except the exact tag-wide mutation lock so
    # search cannot reveal tools separately disabled by name, key, or tag.
    visibility = [
        transform
        for transform in ctx.fastmcp.transforms
        if isinstance(transform, Visibility)
    ]
    visibility.extend(await get_session_transforms(ctx))
    for rule in visibility:
      if not _is_mutation_lock_rule(rule):
        descriptors = list(await rule.list_tools(descriptors))
    return [
        *catalog,
        *[
            tool
            for tool in descriptors
            if is_enabled(tool) and not _is_backend_tool(tool)
        ],
    ]


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
        " get_campaign_settings for current campaign configuration and"
        " lifecycle goals. Use compare_performance_periods for complete"
        " comparisons over explicit daily date windows, optionally by DEVICE"
        " or physical-user COUNTRY. Use compare_performance_around_changes"
        " for complete retained change evidence; it excludes boundary days"
        " and does not establish causality or historical goal switches. Use"
        " summarize_customer_match_jobs for all-job counts and highest-ID"
        " previews; numeric IDs do not prove time recency. For scoped change"
        " history, pass campaign_id or ad_group_id and include_values for"
        " compact field diffs. Incomplete execute_gaql previews provide an"
        " exact bulk_export_call and next_inline_call. Follow next_inline_call"
        " through get_gaql_snapshot_page to read the remaining captured rows"
        " before making claims about all results; max_rows limits only inline"
        " rows. Use"
        " export_gaql_csv instead of execute_gaql when a bulk extract"
        " would be too large for normal JSON tool output. Keep"
        " the user's requested date range for change-history questions."
        " When they ask for full, all, or maximum change history without"
        " dates, use export_change_history_csv so the result covers the"
        " 90-day change_status window plus the 30-day granular"
        " change_event overlay; do not treat change_event retention as"
        " the limit for all change history. For recent granular events,"
        " prefer list_change_events lookback_days over calculating dates. Use"
        " get_change_history_extended for a bounded preview. Keep"
        " call_tool for discovery compatibility, but prefer direct tool"
        " calls once tool names are known. When a list tool returns"
        " returned_count, total_count,"
        " total_page_count, truncated, or next_page_token, use that"
        " metadata to decide whether more pages are needed. Mutation tools"
        " appear in search with a locked marker; direct calls require"
        " unlock_mutation_tools. Use get_resource_mutation_schema then"
        " mutate_ads_resources (validate_only=True by default) for campaign"
        " geo_target_type_setting, target ROAS and status changes, campaign"
        " and budget creation, asset-group cloning with listing filters,"
        " asset links and signals, and YouTube assets or Demand Gen video ads."
        " An asset group's campaign is immutable: clone into the destination"
        " then pause the source only when requested. Use upload_youtube_video"
        " for local video files and get_youtube_video_upload for processing"
        " status. Use get_asset_group_performance for explicit date windows."
        " Location additions require"
        " explicit negative; removals use campaign criterion_ids from"
        " get_campaign_settings, not geo IDs."
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
