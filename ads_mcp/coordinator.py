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
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.visibility import get_visibility_rules
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
      results = await transform._search(  # pylint: disable=protected-access
          visible_tools, query
      )
      decision = resolve_intent(query)
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
      return await transform._render_results(  # pylint: disable=protected-access
          results
      )

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
