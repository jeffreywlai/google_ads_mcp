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

from fastmcp import FastMCP
from fastmcp.server.context import _current_context
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.visibility import get_visibility_rules
from fastmcp.tools.tool import Tool

from ads_mcp.tooling import MUTATE_TAG
from ads_mcp.tooling import compact_search_result_serializer


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
        " ambiguous. Use execute_gaql only for custom read queries not"
        " covered by dedicated tools. Keep call_tool for discovery"
        " compatibility, but prefer direct tool calls once tool names are"
        " known. Mutation tools stay hidden until unlock_mutation_tools."
        " Requires a configured google-ads.yaml credentials file."
    ),
    mask_error_details=True,
    transforms=[
        NonMutationVisibleSearchTransform(
            max_results=8,
            search_result_serializer=compact_search_result_serializer,
        )
    ],
)

mcp_server.disable(tags={MUTATE_TAG}, components={"tool"})
