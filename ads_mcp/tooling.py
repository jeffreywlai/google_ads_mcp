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

"""Shared FastMCP tool metadata helpers."""

from collections.abc import Callable
from collections.abc import Sequence
import re
from typing import Any

from mcp.types import ToolAnnotations


READ_TAG = "read"
MUTATE_TAG = "mutate"

_COMMON_DISCOVERY_ARGS = {
    "customer_id",
    "login_customer_id",
    "limit",
    "partial_failure",
}
_GENERIC_TAGS = {READ_TAG, MUTATE_TAG, "control"}
_WORKFLOW_TAG_PRIORITY = [
    "guide",
    "profiles",
    "docs",
    "discovery",
    "export",
    "reporting",
    "gaql",
    "optimization",
    "campaigns",
    "ad_groups",
    "ads",
    "keywords",
    "negatives",
    "labels",
    "planning",
    "smart_campaigns",
    "search_terms",
    "simulations",
    "changes",
    "audit",
    "performance_max",
    "accounts",
    "fields",
    "visibility",
]


def _build_annotations(
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool,
) -> ToolAnnotations:
  """Builds standard FastMCP annotations for a tool."""
  return ToolAnnotations(
      readOnlyHint=read_only,
      destructiveHint=destructive,
      idempotentHint=idempotent,
      openWorldHint=open_world,
  )


def _merge_tags(
    base_tags: Sequence[str],
    extra_tags: set[str] | None = None,
) -> set[str]:
  """Merges base tags with module-specific tags."""
  merged_tags = set(base_tags)
  if extra_tags:
    merged_tags.update(extra_tags)
  return merged_tags


def ads_read_tool(
    mcp: Any,
    *,
    tags: set[str] | None = None,
    **kwargs: Any,
) -> Callable[..., Any]:
  """Registers a read-only Google Ads API tool."""
  return mcp.tool(
      tags=_merge_tags([READ_TAG], tags),
      annotations=_build_annotations(
          read_only=True,
          destructive=False,
          idempotent=True,
          open_world=True,
      ),
      **kwargs,
  )


def ads_mutation_tool(
    mcp: Any,
    *,
    tags: set[str] | None = None,
    destructive: bool = False,
    idempotent: bool = False,
    **kwargs: Any,
) -> Callable[..., Any]:
  """Registers a state-changing Google Ads API tool."""
  return mcp.tool(
      tags=_merge_tags([MUTATE_TAG], tags),
      annotations=_build_annotations(
          read_only=False,
          destructive=destructive,
          idempotent=idempotent,
          open_world=True,
      ),
      **kwargs,
  )


def local_read_tool(
    mcp: Any,
    *,
    tags: set[str] | None = None,
    **kwargs: Any,
) -> Callable[..., Any]:
  """Registers a local read-only tool with no external side effects."""
  return mcp.tool(
      tags=_merge_tags([READ_TAG], tags),
      annotations=_build_annotations(
          read_only=True,
          destructive=False,
          idempotent=True,
          open_world=False,
      ),
      **kwargs,
  )


def session_control_tool(
    mcp: Any,
    *,
    tags: set[str] | None = None,
    **kwargs: Any,
) -> Callable[..., Any]:
  """Registers a per-session server control tool."""
  return mcp.tool(
      tags=_merge_tags(["control"], tags),
      annotations=_build_annotations(
          read_only=False,
          destructive=False,
          idempotent=True,
          open_world=False,
      ),
      **kwargs,
  )


def _first_sentence(description: str) -> str:
  """Reduces a docstring to its first sentence for search results."""
  normalized = " ".join(description.split())
  sentence = re.match(r"(.+?[.!?])(?:\s|$)", normalized)
  if sentence:
    return sentence.group(1)
  return normalized


def _workflow_tag(tags: Sequence[str]) -> str:
  """Returns the most useful workflow tag for discovery output."""
  tag_set = set(tags)
  for candidate in _WORKFLOW_TAG_PRIORITY:
    if candidate in tag_set:
      return candidate

  for tag in tags:
    if tag not in _GENERIC_TAGS:
      return tag

  return "general"


def compact_search_result_serializer(
    tools: Sequence[Any],
) -> list[dict[str, Any]]:
  """Serializes tool search results into a compact, low-token summary."""
  items = []
  for tool in tools:
    parameters = tool.parameters or {}
    properties = parameters.get("properties", {})
    required_args = list(parameters.get("required", []))
    optional_args = [
        arg_name for arg_name in properties if arg_name not in required_args
    ]
    required_args = [
        arg_name
        for arg_name in required_args
        if arg_name not in _COMMON_DISCOVERY_ARGS
    ]
    optional_args = [
        arg_name
        for arg_name in optional_args
        if arg_name not in _COMMON_DISCOVERY_ARGS
    ]

    tags = sorted(tool.tags or [])
    if MUTATE_TAG in tags:
      mode = "mutate"
    elif READ_TAG in tags:
      mode = "read"
    else:
      mode = "control"

    item = {
        "name": tool.name,
        "mode": mode,
        "workflow": _workflow_tag(tags),
        "summary": _first_sentence(tool.description or ""),
    }
    if required_args:
      item["required_args"] = required_args
    if optional_args:
      visible_optional_args = optional_args[:4]
      if (
          "page_token" in optional_args
          and "page_token" not in visible_optional_args
      ):
        visible_optional_args = [*visible_optional_args[:3], "page_token"]
      item["optional_args"] = visible_optional_args
    items.append(item)

  return items
