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

import json
import os
from typing import Any
import time

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
        credentials, developer_token=ads_config.get("developer_token")
    )
    if login_customer_id:
      client.login_customer_id = login_customer_id
    return client

  if not _ADS_CLIENT:
    _ADS_CLIENT = GoogleAdsClient.load_from_storage(credentials_path)
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
  rows = run_gaql_query(
      query=query,
      customer_id=customer_id,
      login_customer_id=login_customer_id,
  )
  next_offset = offset + page_size
  return {
      "rows": rows[offset:next_offset],
      "next_page_token": (
          str(next_offset) if next_offset < len(rows) else None
      ),
      "total_results_count": len(rows),
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
