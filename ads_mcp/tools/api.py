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

"""This module contains tools for interacting with the Google Ads API."""

from collections import OrderedDict
import csv
from copy import deepcopy
import json
import math
import os
import tempfile
import time
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.util import get_nested_attr
from google.ads.googleads.v23.services.services.customer_service import CustomerServiceClient
from google.ads.googleads.v23.services.services.google_ads_service import GoogleAdsServiceClient
from google.protobuf.field_mask_pb2 import FieldMask
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtobufMessage
from google.oauth2.credentials import Credentials
import proto
import yaml

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.utils import ROOT_DIR


_ADS_CLIENT: GoogleAdsClient | None = None
_ADS_CONFIG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DEFAULT_LOGIN_CUSTOMER_ID: str | None = None
_PAGED_QUERY_CACHE_TTL_SECONDS = 90.0
_PAGED_QUERY_CACHE_MAX_ENTRIES = 2
_PAGED_QUERY_CACHE: OrderedDict[
    tuple[str, str | None, str], tuple[float, list[dict[str, Any]]]
] = OrderedDict()
_EXECUTE_GAQL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "data": {"type": "array", "items": {"type": "object"}},
        "returned_row_count": {"type": "integer"},
        "total_row_count": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "max_rows_applied": {"type": "integer"},
    },
    "required": ["data"],
}
_EXPORT_GAQL_CSV_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "row_count": {"type": "integer"},
        "total_row_count": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "max_rows_applied": {"type": "integer"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "bytes_written": {"type": "integer"},
    },
    "required": [
        "file_path",
        "row_count",
        "total_row_count",
        "truncated",
        "columns",
        "bytes_written",
    ],
}


def _load_ads_config(credentials_path: str) -> dict[str, Any]:
  """Loads the Google Ads YAML config with mtime-based caching."""
  cache_mtime = os.path.getmtime(credentials_path)
  cache_entry = _ADS_CONFIG_CACHE.get(credentials_path)
  if cache_entry and cache_entry[0] == cache_mtime:
    return cache_entry[1]

  with open(credentials_path, "r", encoding="utf-8") as f:
    ads_config = yaml.safe_load(f.read())

  _ADS_CONFIG_CACHE[credentials_path] = (cache_mtime, ads_config)
  return ads_config


def get_ads_client(
    login_customer_id: str | None = None,
) -> GoogleAdsClient:
  """Gets a GoogleAdsClient instance.

  Looks for an access token from the environment or loads credentials from
  a YAML file. Resets login_customer_id to the YAML-configured default
  before each call to prevent state pollution between tool invocations.

  Args:
      login_customer_id: Optional manager account ID to use for this
          request. Resets to the YAML default when not provided.

  Returns:
      A GoogleAdsClient instance.

  Raises:
      FileNotFoundError: If the credentials YAML file is not found.
  """
  global _ADS_CLIENT, _DEFAULT_LOGIN_CUSTOMER_ID

  access_token = get_access_token()
  if access_token:
    access_token = access_token.token

  default_path = f"{ROOT_DIR}/google-ads.yaml"
  credentials_path = os.environ.get("GOOGLE_ADS_CREDENTIALS", default_path)
  if not os.path.isfile(credentials_path):
    raise FileNotFoundError(
        "Google Ads credentials YAML file is not found. "
        "Check [GOOGLE_ADS_CREDENTIALS] config."
    )

  if access_token:
    credentials = Credentials(access_token)
    ads_config = _load_ads_config(credentials_path)
    client = GoogleAdsClient(
        credentials,
        developer_token=ads_config.get("developer_token"),
        use_proto_plus=True,
    )
    if login_customer_id:
      client.login_customer_id = login_customer_id
    return client

  if not _ADS_CLIENT:
    ads_config = dict(_load_ads_config(credentials_path))
    ads_config["use_proto_plus"] = True
    _ADS_CLIENT = GoogleAdsClient.load_from_dict(ads_config)
    _DEFAULT_LOGIN_CUSTOMER_ID = getattr(
        _ADS_CLIENT, "login_customer_id", None
    )

  # Always reset to prevent state pollution from previous calls.
  _ADS_CLIENT.login_customer_id = (
      login_customer_id or _DEFAULT_LOGIN_CUSTOMER_ID
  )

  return _ADS_CLIENT


@ads_read_tool(mcp, tags={"accounts", "discovery"})
def list_accessible_accounts() -> list[str]:
  """Lists Google Ads customers id directly accessible by the user.

  The accounts can be used as `login_customer_id`.
  """
  ads_client = get_ads_client()
  customer_service: CustomerServiceClient = ads_client.get_service(
      "CustomerService"
  )
  accounts = customer_service.list_accessible_customers().resource_names
  return [account.split("/")[-1] for account in accounts]


def preprocess_gaql(query: str) -> str:
  """Preprocesses a GAQL query to add omit_unselected_resource_names=true."""
  if "omit_unselected_resource_names" not in query:
    if "PARAMETERS" in query and "include_drafts" in query:
      return query + ", omit_unselected_resource_names=true"
    return query + " PARAMETERS omit_unselected_resource_names=true"
  return query


def format_value(value: Any) -> Any:
  """Formats a value from a Google Ads API response."""
  if isinstance(value, proto.marshal.collections.repeated.Repeated):
    return_value = [format_value(i) for i in value]
  elif isinstance(value, proto.Message):
    # covert to json first to avoid serialization issues
    return_value = proto.Message.to_json(
        value,
        use_integers_for_enums=False,
    )
    return_value = json.loads(return_value)
  elif isinstance(value, FieldMask):
    return_value = {"paths": list(value.paths)}
  elif isinstance(value, ProtobufMessage):
    return_value = MessageToDict(
        value,
        preserving_proto_field_name=True,
    )
  elif isinstance(value, proto.Enum):
    return_value = value.name
  else:
    return_value = value

  return return_value


def gaql_quote_string(value: str) -> str:
  """Escapes a string literal for use in a GAQL query."""
  escaped_value = value.replace("\\", "\\\\").replace("'", "\\'")
  return f"'{escaped_value}'"


def gaql_results_to_dicts(query_res: Any) -> list[dict[str, Any]]:
  """Converts a Google Ads search stream response into plain dict rows."""
  output = []
  for batch in query_res:
    for row in batch.results:
      output.append(
          {
              field_name: format_value(get_nested_attr(row, field_name))
              for field_name in batch.field_mask.paths
          }
      )
  return output


def _decode_page_token(page_token: str | None) -> int:
  """Decodes a simple offset-style page token."""
  if not page_token:
    return 0
  try:
    offset = int(page_token)
  except ValueError as exc:
    raise ToolError("Invalid page_token.") from exc
  if offset < 0:
    raise ToolError("Invalid page_token.")
  return offset


def _page_cache_key(
    query: str,
    customer_id: str,
    login_customer_id: str | None,
) -> tuple[str, str | None, str]:
  """Builds a stable cache key for paged GAQL queries."""
  return (customer_id, login_customer_id, query)


def _get_cached_page_rows(
    query: str,
    customer_id: str,
    login_customer_id: str | None,
) -> list[dict[str, Any]] | None:
  """Returns cached paged query rows when the entry is still fresh."""
  cache_key = _page_cache_key(query, customer_id, login_customer_id)
  cache_entry = _PAGED_QUERY_CACHE.get(cache_key)
  if not cache_entry:
    return None

  cached_at, cached_rows = cache_entry
  if (time.monotonic() - cached_at) > _PAGED_QUERY_CACHE_TTL_SECONDS:
    _PAGED_QUERY_CACHE.pop(cache_key, None)
    return None

  _PAGED_QUERY_CACHE.move_to_end(cache_key)
  return deepcopy(cached_rows)


def _set_cached_page_rows(
    query: str,
    customer_id: str,
    login_customer_id: str | None,
    rows: list[dict[str, Any]],
) -> None:
  """Stores paged query rows in a bounded TTL cache."""
  cache_key = _page_cache_key(query, customer_id, login_customer_id)
  _PAGED_QUERY_CACHE[cache_key] = (time.monotonic(), deepcopy(rows))
  _PAGED_QUERY_CACHE.move_to_end(cache_key)

  while len(_PAGED_QUERY_CACHE) > _PAGED_QUERY_CACHE_MAX_ENTRIES:
    _PAGED_QUERY_CACHE.popitem(last=False)


def _csv_columns(rows: list[dict[str, Any]]) -> list[str]:
  """Returns CSV columns in first-seen row/key order."""
  columns = []
  seen_columns = set()
  for row in rows:
    for column in row:
      if column in seen_columns:
        continue
      seen_columns.add(column)
      columns.append(column)
  return columns


def _csv_cell_value(value: Any) -> Any:
  """Serializes a row value into a CSV-safe scalar."""
  if value is None:
    return ""
  if isinstance(value, (str, int, float, bool)):
    return value
  return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _write_csv_rows(
    rows: list[dict[str, Any]],
    output_path: str | None = None,
) -> tuple[str, list[str], int]:
  """Writes GAQL rows to CSV and returns the path, columns, and size."""
  if output_path:
    resolved_path = os.path.abspath(output_path)
    parent_dir = os.path.dirname(resolved_path)
    if parent_dir:
      os.makedirs(parent_dir, exist_ok=True)
  else:
    file_descriptor, resolved_path = tempfile.mkstemp(
        prefix="google_ads_mcp_",
        suffix=".csv",
    )
    os.close(file_descriptor)

  columns = _csv_columns(rows)
  with open(resolved_path, "w", newline="", encoding="utf-8") as csv_file:
    writer = csv.writer(csv_file)
    if columns:
      writer.writerow(columns)
      for row in rows:
        writer.writerow(
            [_csv_cell_value(row.get(column)) for column in columns]
        )

  return resolved_path, columns, os.path.getsize(resolved_path)


def run_gaql_query(
    query: str,
    customer_id: str,
    login_customer_id: str | None = None,
) -> list[dict[str, Any]]:
  """Executes a GAQL query and returns formatted rows."""
  query = preprocess_gaql(query)
  ads_client = get_ads_client(login_customer_id)
  ads_service: GoogleAdsServiceClient = ads_client.get_service(
      "GoogleAdsService"
  )
  try:
    query_res = ads_service.search_stream(query=query, customer_id=customer_id)
    return gaql_results_to_dicts(query_res)
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e


def run_gaql_query_page(
    query: str,
    customer_id: str,
    page_size: int,
    page_token: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Executes a GAQL query and slices the results into stable pages.

  Google Ads Search uses a fixed page size for some resources, so this helper
  provides a consistent cursor contract for MCP tools by applying client-side
  paging over the full result set.
  """
  if page_size <= 0:
    raise ToolError("page_size must be greater than 0.")

  offset = _decode_page_token(page_token)
  rows = _get_cached_page_rows(query, customer_id, login_customer_id)
  if rows is None:
    rows = run_gaql_query(
        query=query,
        customer_id=customer_id,
        login_customer_id=login_customer_id,
    )
    _set_cached_page_rows(query, customer_id, login_customer_id, rows)
  next_offset = offset + page_size
  return {
      "rows": rows[offset:next_offset],
      "next_page_token": (
          str(next_offset) if next_offset < len(rows) else None
      ),
      "total_results_count": len(rows),
  }


def build_paginated_list_response(
    item_key: str,
    rows: list[dict[str, Any]],
    total_count: int,
    page_size: int,
    next_page_token: str | None,
) -> dict[str, Any]:
  """Builds a consistent paginated list response envelope."""
  return {
      item_key: rows,
      "returned_count": len(rows),
      "total_count": total_count,
      "total_page_count": (
          math.ceil(total_count / page_size) if total_count else 0
      ),
      "truncated": next_page_token is not None,
      "next_page_token": next_page_token,
      "page_size": page_size,
  }


@ads_read_tool(
    mcp,
    tags={"gaql", "reporting"},
    output_schema=_EXECUTE_GAQL_OUTPUT_SCHEMA,
)
def execute_gaql(
    query: str,
    customer_id: str,
    max_rows: int | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Executes a GAQL query to get reporting data.

  Prefer dedicated visible tools first. Use search_tools only when the right
  tool is unclear, then use get_tool_guide, get_gaql_doc, and
  get_reporting_view_doc when a custom GAQL query is needed. Set max_rows to
  cap large result sets without changing the underlying GAQL query.
  """
  if max_rows is not None and max_rows <= 0:
    raise ToolError("max_rows must be greater than 0.")

  rows = run_gaql_query(
      query=query,
      customer_id=customer_id,
      login_customer_id=login_customer_id,
  )
  if max_rows is None:
    return {"data": rows}

  returned_rows = rows[:max_rows]
  return {
      "data": returned_rows,
      "returned_row_count": len(returned_rows),
      "total_row_count": len(rows),
      "truncated": len(rows) > max_rows,
      "max_rows_applied": max_rows,
  }


@ads_read_tool(
    mcp,
    tags={"gaql", "reporting", "export"},
    output_schema=_EXPORT_GAQL_CSV_OUTPUT_SCHEMA,
)
def export_gaql_csv(
    query: str,
    customer_id: str,
    output_path: str | None = None,
    max_rows: int | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Exports GAQL query results to a CSV file for bulk extraction.

  Prefer this over execute_gaql when the goal is loading a large result set
  into another system or reading the data outside the model context.

  Args:
      query: GAQL query to export.
      customer_id: Google Ads customer ID.
      output_path: Optional destination path. Defaults to a temp CSV file.
      max_rows: Optional row cap for partial exports.
      login_customer_id: Optional manager account ID.

  Returns:
      A dict with the CSV path and export metadata.
  """
  if max_rows is not None and max_rows <= 0:
    raise ToolError("max_rows must be greater than 0.")

  rows = run_gaql_query(
      query=query,
      customer_id=customer_id,
      login_customer_id=login_customer_id,
  )
  exported_rows = rows if max_rows is None else rows[:max_rows]
  file_path, columns, bytes_written = _write_csv_rows(
      exported_rows, output_path
  )

  result = {
      "file_path": file_path,
      "row_count": len(exported_rows),
      "total_row_count": len(rows),
      "truncated": len(exported_rows) < len(rows),
      "columns": columns,
      "bytes_written": bytes_written,
  }
  if max_rows is not None:
    result["max_rows_applied"] = max_rows
  return result
