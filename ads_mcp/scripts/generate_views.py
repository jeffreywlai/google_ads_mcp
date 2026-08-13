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
"""Generates YAML files for Google Ads API reporting views."""

import asyncio
from importlib import metadata
import logging
import os
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml

logging.getLogger("httpx").setLevel(logging.WARNING)

ADS_API_VERSION = "v24"
CONTEXT_SCHEMA_VERSION = "2"
try:
  MCP_SERVER_VERSION = f"v{metadata.version("google-ads-mcp")}"
except metadata.PackageNotFoundError:
  MCP_SERVER_VERSION = "v0.6.3"
VIEW_JSON_URL_PATH = (
    f"https://gaql-query-builder.uc.r.appspot.com/schemas/{ADS_API_VERSION}/"
)
MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT_PATH = f"{MODULE_ROOT}/context"


def get_view_json_url(view: str) -> str:
  return f"{VIEW_JSON_URL_PATH}{view}.json"


async def get_view_json(
    view: str, http_client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
  """Fetches the JSON definition for a given reporting view."""
  if http_client is None:
    async with httpx.AsyncClient(http2=True) as client:
      return await get_view_json(view, client)

  http_res = await http_client.get(get_view_json_url(view), timeout=15.0)
  http_res.raise_for_status()
  view_json = http_res.json()
  return view_json


def get_fields_obj(
    view_json: dict[str, Any],
    category: Literal["attributes", "segments", "metrics"],
) -> dict[str, Any]:
  """Extracts field metadata details for a given category from the view JSON."""
  selected_info = [
      # "name",
      "description",
      # "category",
      "data_type",
      "is_repeated",
      "enum_values",
      "filterable",
      "sortable",
  ]

  def details(field):
    raw_data = view_json["fields"][field]["field_details"]
    info = {i: raw_data[i] for i in selected_info}
    if raw_data.get("data_type") == "ENUM":
      info["enum_values"] = ", ".join(raw_data["enum_values"])
    else:
      del info["enum_values"]

    if category == "segments" and field.startswith("segments."):
      info["compatible_metrics"] = sorted(
          selectable_field
          for selectable_field in raw_data.get("selectable_with", [])
          if selectable_field.startswith("metrics.")
      )

    return info

  return {field: details(field) for field in view_json[category]}


async def save_view_yaml(
    view: str,
    path: str = ".",
    http_client: httpx.AsyncClient | None = None,
):
  """Saves the reporting view metadata as a YAML file."""
  view_json = await get_view_json(view, http_client)

  attributed_views = set(
      v.split(".")[0]
      for v in view_json["attributes"]
      if not v.startswith(f"{view}.")
  )

  view_data = {
      "display_name": view_json["display_name"],
      "name": view_json["name"],
      "description": view_json["description"],
      "attributed_views": list(attributed_views),
      "attributes": get_fields_obj(view_json, "attributes"),
      "segments": get_fields_obj(view_json, "segments"),
      "metrics": get_fields_obj(view_json, "metrics"),
  }

  view_output = {
      "display_name": view_json["display_name"],
      "name": view_json["name"],
      "description": view_json["description"],
      "attributed_views": list(attributed_views),
      "attributes": list(view_data["attributes"].keys()),
      "segments": list(view_data["segments"].keys()),
      "metrics": list(view_data["metrics"].keys()),
  }

  with open(os.path.join(path, f"{view}.yaml"), "w", encoding="utf-8") as f:
    yaml.safe_dump(view_output, f, sort_keys=False)

  return view_data


def check_context_version() -> bool:
  """Checks if the current API and MCP server versions match context files.

  Returns:
      bool: True if context files exist and versions match, False otherwise.
  """
  if not os.path.isfile(f"{CONTEXT_PATH}/fields.yaml") or not os.path.isfile(
      f"{CONTEXT_PATH}/segment_metric_compatibility.yaml"
  ):
    return False

  if os.path.isfile(f"{CONTEXT_PATH}/.api-version"):
    with open(f"{CONTEXT_PATH}/.api-version", "r", encoding="utf-8") as f:
      if f.read().strip() != ADS_API_VERSION:
        return False
  else:
    return False

  if os.path.isfile(f"{CONTEXT_PATH}/.mcp-server-version"):
    with open(
        f"{CONTEXT_PATH}/.mcp-server-version", "r", encoding="utf-8"
    ) as f:
      if f.read().strip() != MCP_SERVER_VERSION:
        return False
  else:
    return False

  if os.path.isfile(f"{CONTEXT_PATH}/.context-schema-version"):
    with open(
        f"{CONTEXT_PATH}/.context-schema-version", "r", encoding="utf-8"
    ) as f:
      if f.read().strip() != CONTEXT_SCHEMA_VERSION:
        return False
  else:
    return False

  return True


def _read_views_manifest() -> set[str] | None:
  """Reads the set of views covered by the last fully successful run."""
  manifest_path = f"{CONTEXT_PATH}/.views-manifest"
  if not os.path.isfile(manifest_path):
    return None
  with open(manifest_path, "r", encoding="utf-8") as f:
    return {line.strip() for line in f if line.strip()}


def _write_views_manifest(declared_views: list[str]) -> None:
  """Records a fully successful run so partial failures keep retrying."""
  with open(f"{CONTEXT_PATH}/.views-manifest", "w", encoding="utf-8") as f:
    f.write("\n".join(sorted(declared_views)))


def should_regenerate_views(
    declared_views: list[str], present_views: set[str]
) -> bool:
  """Returns whether reporting view context needs regeneration.

  The manifest comparison keeps retrying after partial failures: view files
  written by a failed batch satisfy the declared-vs-present check, but the
  manifest is only rewritten when every view (and fields.yaml) succeeded.
  """
  return (
      not check_context_version()
      or bool(set(declared_views) - present_views)
      or _read_views_manifest() != set(declared_views)
  )


async def update_views_yaml():
  """Updates the YAML files for all reporting views."""
  with open(f"{CONTEXT_PATH}/views.yaml", "r", encoding="utf-8") as f:
    views = yaml.safe_load(f)

  views_path = Path(CONTEXT_PATH) / "views"
  present_views = {path.stem for path in views_path.glob("*.yaml")}
  if not should_regenerate_views(views, present_views):
    return

  semaphore = asyncio.Semaphore(10)

  async with httpx.AsyncClient(http2=True) as http_client:

    async def save_with_limit(view: str):
      async with semaphore:
        return await save_view_yaml(view, str(views_path), http_client)

    tasks = [save_with_limit(view) for view in views]
    results = await asyncio.gather(*tasks, return_exceptions=True)

  failed_views = [
      view
      for view, result in zip(views, results)
      if isinstance(result, BaseException)
  ]
  if failed_views:
    logging.warning(
        "Failed to refresh reporting views: %s", ", ".join(failed_views)
    )
    return

  all_fields = {}
  segment_metric_compatibility = {}
  for view in results:
    for category in ("attributes", "segments", "metrics"):
      for field_name, field_details in view[category].items():
        field_metadata = dict(field_details)
        compatible_metrics = field_metadata.pop("compatible_metrics", None)
        if field_name.startswith("segments.") and isinstance(
            compatible_metrics, list
        ):
          segment_metric_compatibility[field_name] = compatible_metrics
        all_fields[field_name] = field_metadata

  with open(f"{CONTEXT_PATH}/fields.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(all_fields, f, sort_keys=True)
  with open(
      f"{CONTEXT_PATH}/segment_metric_compatibility.yaml",
      "w",
      encoding="utf-8",
  ) as f:
    yaml.safe_dump(segment_metric_compatibility, f, sort_keys=True)

  with open(f"{CONTEXT_PATH}/.api-version", "w", encoding="utf-8") as f:
    f.write(ADS_API_VERSION)
  with open(f"{CONTEXT_PATH}/.mcp-server-version", "w", encoding="utf-8") as f:
    f.write(MCP_SERVER_VERSION)
  with open(
      f"{CONTEXT_PATH}/.context-schema-version", "w", encoding="utf-8"
  ) as f:
    f.write(CONTEXT_SCHEMA_VERSION)
  _write_views_manifest(views)


def refresh_view_docs_for_startup(timeout_seconds: float = 30.0) -> None:
  """Refreshes view docs with a boot deadline; never blocks serving."""

  async def _bounded_update() -> None:
    await asyncio.wait_for(update_views_yaml(), timeout=timeout_seconds)

  try:
    asyncio.run(_bounded_update())
  except Exception as error:  # pylint: disable=broad-exception-caught
    logging.warning(
        "Unable to refresh reporting view documentation: %s", error
    )


if __name__ == "__main__":
  asyncio.run(update_views_yaml())
