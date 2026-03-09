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


def compact_search_result_serializer(tools: Sequence[Any]) -> dict[str, Any]:
  """Serializes tool search results into a compact, low-token summary."""
  items = []
  for tool in tools:
    parameters = tool.parameters or {}
    properties = parameters.get("properties", {})
    required_args = list(parameters.get("required", []))
    optional_args = [
        arg_name for arg_name in properties if arg_name not in required_args
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
        "summary": _first_sentence(tool.description or ""),
    }
    if tags:
      item["tags"] = tags
    if required_args:
      item["required_args"] = required_args
    if optional_args:
      item["optional_args"] = optional_args[:5]
    items.append(item)

  return {"tools": items}
