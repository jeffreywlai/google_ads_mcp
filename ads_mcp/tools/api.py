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
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from concurrent import futures
import contextlib
import csv
from copy import deepcopy
import difflib
import functools
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import threading
import time
from typing import Any
import uuid

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.util import get_nested_attr
from google.ads.googleads.v24.services.services.customer_service import CustomerServiceClient
from google.ads.googleads.v24.services.services.google_ads_service import GoogleAdsServiceClient
from google.protobuf.field_mask_pb2 import FieldMask
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtobufMessage
from google.oauth2.credentials import Credentials
from google.api_core import exceptions as google_exceptions
import proto
import yaml

from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tooling import local_write_tool
from ads_mcp.tools._gaql import gaql_quote_string as _gaql_quote_string
from ads_mcp.tools._gaql import preprocess_gaql_query
from ads_mcp.utils import MODULE_DIR
from ads_mcp.utils import ROOT_DIR


_ADS_CLIENTS: OrderedDict[str | None, GoogleAdsClient] = OrderedDict()
_ADS_CLIENT_BUILDS: dict[str | None, futures.Future] = {}
_ADS_CLIENTS_LOCK = threading.Lock()
_ADS_CLIENTS_MAX_ENTRIES = 8
_ADS_CLIENTS_CREDENTIALS_MTIME: float | None = None
_ADS_CLIENTS_CREDENTIALS_PATH: str | None = None
_ADS_CONFIG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PAGED_QUERY_CACHE_TTL_SECONDS = 90.0
_PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE = 8
_PAGED_QUERY_CACHE_MAX_ENTRIES = 16
_PAGED_QUERY_CACHE_MAX_BYTES = 64 * 1024 * 1024
_MAX_INLINE_PAGE_SIZE = 100
INLINE_PAGE_BYTE_LIMIT = 32 * 1024
INLINE_SECTION_BYTE_LIMIT = 40 * 1024
INLINE_RESPONSE_BYTE_LIMIT = 48 * 1024
_MAX_INLINE_PAGE_BYTES = INLINE_PAGE_BYTE_LIMIT
_SNAPSHOT_TOKEN_PREFIX = "gaql-snapshot-v1:"
_MATERIALIZED_SNAPSHOT_TOKEN_PREFIX = "materialized-snapshot-v1:"
_MATERIALIZED_SNAPSHOT_CACHE_TTL_SECONDS = 90.0
_MATERIALIZED_SNAPSHOT_CACHE_MAX_ENTRIES_PER_SCOPE = 8
_MATERIALIZED_SNAPSHOT_CACHE_MAX_ENTRIES = 16
_MATERIALIZED_SNAPSHOT_CACHE_MAX_BYTES = 32 * 1024 * 1024
_MATERIALIZED_SNAPSHOT_CACHE: OrderedDict[
    tuple[str, str],
    tuple[float, list[dict[str, Any]], int],
] = OrderedDict()
_MATERIALIZED_SNAPSHOT_CACHE_LOCK = threading.Lock()
_INLINE_OMISSION_KEY = "_google_ads_mcp_inline_omission"
_ACCOUNT_SNAPSHOT_TOKEN_PREFIX = "accounts-snapshot-v1:"
_ACCOUNT_PAGE_TOKEN_PREFIX = "accounts-page-v1:"
_ACCOUNT_SNAPSHOT_CACHE_TTL_SECONDS = 90.0
_ACCOUNT_SNAPSHOT_CACHE_MAX_ENTRIES_PER_SCOPE = 4
_ACCOUNT_SNAPSHOT_CACHE_MAX_ENTRIES = 8
_ACCOUNT_SNAPSHOT_CACHE_MAX_BYTES = 16 * 1024 * 1024
_MANAGED_TEMP_ARTIFACT_TTL_SECONDS = 60 * 60
_MANAGED_TEMP_ARTIFACT_MAX_ENTRIES = 64
_MANAGED_TEMP_ARTIFACT_MAX_BYTES = 512 * 1024 * 1024
_PagedQueryCacheKey = tuple[
    str,
    str,
    str | None,
    str,
    tuple[str, ...],
]
_PagedSnapshotCacheKey = tuple[
    str,
    str,
    str | None,
    str,
    tuple[str, ...],
    str,
]
_PAGED_QUERY_CACHE: OrderedDict[
    _PagedSnapshotCacheKey,
    tuple[float, Any, int],
] = OrderedDict()
_PAGED_QUERY_LATEST: dict[_PagedQueryCacheKey, str] = {}
_PAGED_QUERY_BUILDS: dict[
    _PagedQueryCacheKey,
    futures.Future,
] = {}
_PAGED_QUERY_CACHE_LOCK = threading.Lock()
_ACCOUNT_SNAPSHOT_CACHE: OrderedDict[
    tuple[str, str],
    tuple[float, tuple[str, ...], int],
] = OrderedDict()
_ACCOUNT_SNAPSHOT_CACHE_LOCK = threading.Lock()
_MANAGED_TEMP_ARTIFACTS: OrderedDict[
    str,
    tuple[float, float, int, int, int],
] = OrderedDict()
_MANAGED_TEMP_ARTIFACT_CONDITION = threading.Condition()
_MANAGED_TEMP_ARTIFACT_REAPER: threading.Thread | None = None
_GAQL_FIELD_NAMES_CACHE: tuple[str, ...] | None = None
_DEFAULT_EXECUTE_GAQL_WARNING_ROW_THRESHOLD = 100
gaql_quote_string = _gaql_quote_string
_EXECUTE_GAQL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "data": {"type": "array", "items": {"type": "object"}},
        "returned_row_count": {"type": "integer"},
        "total_row_count": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "max_rows_applied": {"type": "integer"},
        "warning_row_threshold": {"type": "integer"},
        "token_efficiency_warning": {"type": "string"},
    },
    "required": ["data"],
}
_KNOWN_FIELD_CORRECTIONS = {
    "campaign_criterion.audience.audience": "campaign_criterion.audience",
    "recommendation.impact.base_campaign": "recommendation.campaign",
}
_TRANSIENT_GOOGLE_ADS_ERROR_MARKERS = (
    "DEADLINE_EXCEEDED",
    "INTERNAL_ERROR",
    "UNAVAILABLE",
)
_NON_RETRYABLE_GOOGLE_ADS_ERROR_MARKERS = (
    "QUOTA_ERROR",
    "RESOURCE_EXHAUSTED",
)
_GOOGLE_ADS_ERROR_HINTS = (
    (
        ("PROHIBITED_RESOURCE_TYPE_IN_SELECT",),
        True,
        "The selected field is not compatible with the FROM resource. Use "
        "get_resource_metadata for that resource or switch to a *_view that "
        "joins the resources you need.",
    ),
    (
        ("PROHIBITED_SEGMENT", "PROHIBITED_METRIC"),
        False,
        "At least one selected metric/segment is incompatible with this "
        "resource. Drop the incompatible segment or query a more specific "
        "reporting view.",
    ),
    (
        ("BAD_ENUM_CONSTANT", "DETAILED_DEMOGRAPHIC"),
        True,
        "DETAILED_DEMOGRAPHIC is not a CampaignCriterionType. Use "
        "AGE_RANGE, GENDER, INCOME_RANGE, PARENTAL_STATUS, USER_LIST, "
        "USER_INTEREST, CUSTOM_AUDIENCE, or COMBINED_AUDIENCE as "
        "applicable.",
    ),
    (
        ("Metrics cannot be requested for a manager account",),
        True,
        "Use the child customer_id for metric queries and pass the manager "
        "account as login_customer_id.",
    ),
    (
        ("USER_PERMISSION_DENIED", "CUSTOMER_NOT_FOUND"),
        False,
        "Call list_accessible_accounts to confirm valid customer IDs, then "
        "use login_customer_id for manager-account access when needed.",
    ),
)
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


@functools.lru_cache(maxsize=1)
def _package_ads_assistant() -> str:
  """Returns the process-wide default Google Ads assistant tag."""
  try:
    version = importlib.metadata.version("google-ads-mcp")
  except importlib.metadata.PackageNotFoundError:
    return "google-ads-mcp"

  return f"google-ads-mcp-{version}"


def _default_ads_assistant() -> str | None:
  """Returns the default Google Ads assistant request tag."""
  configured_tag = os.getenv("GOOGLE_ADS_ADS_ASSISTANT")
  if configured_tag is not None:
    configured_tag = configured_tag.strip()
    return configured_tag or None

  return _package_ads_assistant()


def _apply_ads_client_defaults(ads_config: dict[str, Any]) -> dict[str, Any]:
  """Applies compact default client settings for this MCP server."""
  normalized_config = dict(ads_config)
  normalized_config["use_proto_plus"] = True

  ads_assistant = _default_ads_assistant()
  if ads_assistant and not normalized_config.get("ads_assistant"):
    normalized_config["ads_assistant"] = ads_assistant

  return normalized_config


def _load_ads_config(
    credentials_path: str, cache_mtime: float | None = None
) -> dict[str, Any]:
  """Loads the Google Ads YAML config with mtime-based caching."""
  if cache_mtime is None:
    cache_mtime = os.path.getmtime(credentials_path)
  cache_entry = _ADS_CONFIG_CACHE.get(credentials_path)
  if cache_entry and cache_entry[0] == cache_mtime:
    return cache_entry[1]

  with open(credentials_path, "r", encoding="utf-8") as f:
    ads_config = yaml.safe_load(f.read())

  _ADS_CONFIG_CACHE[credentials_path] = (cache_mtime, ads_config)
  return ads_config


def _normalize_login_customer_id(login_customer_id: Any) -> str | None:
  """Normalizes a caller-provided manager account ID to digits only."""
  if login_customer_id in (None, ""):
    return None
  normalized_id = re.sub(r"[\s-]", "", str(login_customer_id))
  if not normalized_id.isdigit():
    raise ToolError(
        "login_customer_id must be a numeric Google Ads customer ID "
        "(dashes and spaces are allowed)."
    )
  return normalized_id


def _default_login_customer_id_key(default_value: Any) -> str | None:
  """Normalizes the YAML default login ID without rejecting it locally.

  Malformed YAML defaults are passed through so client construction reports
  them; only caller-supplied IDs get strict local validation.
  """
  if default_value in (None, ""):
    return None
  normalized_id = re.sub(r"[\s-]", "", str(default_value))
  if normalized_id.isdigit():
    return normalized_id
  return str(default_value)


def _build_ads_client(
    ads_config: dict[str, Any],
    key: str | None,
) -> GoogleAdsClient:
  """Builds an immutable GoogleAdsClient for one login_customer_id key."""
  build_config = dict(ads_config)
  if key is None:
    build_config.pop("login_customer_id", None)
  else:
    build_config["login_customer_id"] = key
  try:
    return GoogleAdsClient.load_from_dict(build_config)
  except ValueError as exc:
    raise ToolError(f"Invalid Google Ads client config: {exc}") from exc


def get_ads_client(
    login_customer_id: str | None = None,
) -> GoogleAdsClient:
  """Gets a GoogleAdsClient instance.

  Looks for an access token from the environment or loads credentials from
  a YAML file. YAML-backed clients are cached immutably per manager account
  so concurrent calls cannot change another request's login_customer_id.
  Clients are built outside the cache lock because construction refreshes
  OAuth tokens over the network.

  Args:
      login_customer_id: Optional manager account ID to use for this
          request. Uses the YAML default when not provided.

  Returns:
      A GoogleAdsClient instance.

  Raises:
      FileNotFoundError: If the credentials YAML file is not found.
      ToolError: If login_customer_id or the client config is invalid.
  """
  global _ADS_CLIENTS_CREDENTIALS_MTIME, _ADS_CLIENTS_CREDENTIALS_PATH

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
    ads_config = _apply_ads_client_defaults(_load_ads_config(credentials_path))
    client = GoogleAdsClient(
        credentials,
        developer_token=ads_config.get("developer_token"),
        use_proto_plus=True,
        ads_assistant=ads_config.get("ads_assistant"),
    )
    if login_customer_id:
      client.login_customer_id = login_customer_id
    return client

  credentials_mtime = os.path.getmtime(credentials_path)
  ads_config = _apply_ads_client_defaults(
      _load_ads_config(credentials_path, credentials_mtime)
  )
  key = _normalize_login_customer_id(login_customer_id)
  if key is None:
    key = _default_login_customer_id_key(ads_config.get("login_customer_id"))

  with _ADS_CLIENTS_LOCK:
    if (
        credentials_path != _ADS_CLIENTS_CREDENTIALS_PATH
        or credentials_mtime != _ADS_CLIENTS_CREDENTIALS_MTIME
    ):
      _ADS_CLIENTS.clear()
      _ADS_CLIENT_BUILDS.clear()
      _ADS_CLIENTS_CREDENTIALS_PATH = credentials_path
      _ADS_CLIENTS_CREDENTIALS_MTIME = credentials_mtime

    cached_client = _ADS_CLIENTS.get(key)
    if cached_client is not None:
      _ADS_CLIENTS.move_to_end(key)
      return cached_client

    build = _ADS_CLIENT_BUILDS.get(key)
    owns_build = build is None
    if owns_build:
      build = futures.Future()
      _ADS_CLIENT_BUILDS[key] = build

  if not owns_build:
    return build.result()

  try:
    client = _build_ads_client(ads_config, key)
  except BaseException as exc:
    with _ADS_CLIENTS_LOCK:
      _ADS_CLIENT_BUILDS.pop(key, None)
    build.set_exception(exc)
    raise

  with _ADS_CLIENTS_LOCK:
    _ADS_CLIENT_BUILDS.pop(key, None)
    if (
        credentials_path == _ADS_CLIENTS_CREDENTIALS_PATH
        and credentials_mtime == _ADS_CLIENTS_CREDENTIALS_MTIME
    ):
      _ADS_CLIENTS[key] = client
      while len(_ADS_CLIENTS) > _ADS_CLIENTS_MAX_ENTRIES:
        _ADS_CLIENTS.popitem(last=False)
  build.set_result(client)
  return client


@ads_read_tool(mcp, tags={"accounts", "discovery"})
def list_accessible_accounts(
    page_size: int = 100,
    page_token: str | None = None,
) -> dict[str, Any]:
  """Lists every directly accessible Google Ads customer ID.

  This read remains side-effect free: it keeps a short-lived,
  credential-scoped snapshot in memory and never writes a file implicitly.
  Follow `next_page_token` for every ID, or explicitly call the returned
  `bulk_export_call` to write the exact snapshot to CSV. The IDs can be used
  as `login_customer_id`.

  Args:
      page_size: Requested inline page size. Values above the shared token-safe
          ceiling are clamped; later pages remain available.
      page_token: Stable continuation token from the previous response.

  Returns:
      A bounded page of account IDs, complete counts, and exact export guidance.
  """
  requested_page_size = page_size
  page_size = applied_inline_page_size(page_size)
  if page_token:
    snapshot_id, offset, token_page_size = _decode_account_page_token(
        page_token
    )
    if token_page_size != page_size:
      raise ToolError(
          f"page_token is bound to page_size={token_page_size}. Continue with "
          "the same page_size or restart without page_token."
      )
    account_ids = _get_account_snapshot(snapshot_id)
  else:
    ads_client = get_ads_client()
    customer_service: CustomerServiceClient = ads_client.get_service(
        "CustomerService"
    )
    with handle_google_ads_errors():
      response = customer_service.list_accessible_customers()
      resource_names = list(response.resource_names)
    account_ids = tuple(account.split("/")[-1] for account in resource_names)
    snapshot_id = _store_account_snapshot(account_ids)
    offset = 0

  if (
      offset < 0
      or (account_ids and offset >= len(account_ids))
      or offset % page_size
  ):
    raise ToolError(
        "page_token does not identify a valid page boundary. Restart without "
        "page_token."
    )
  page_account_ids = list(account_ids[offset : offset + page_size])
  next_offset = offset + len(page_account_ids)
  next_page_token = (
      _encode_account_page_token(snapshot_id, next_offset, page_size)
      if next_offset < len(account_ids)
      else None
  )
  snapshot_token = _encode_account_snapshot_token(snapshot_id)
  return {
      "accounts": page_account_ids,
      "returned_count": len(page_account_ids),
      "total_count": len(account_ids),
      "total_page_count": math.ceil(len(account_ids) / page_size),
      "truncated": next_page_token is not None,
      "has_more": next_page_token is not None,
      "complete_inline": next_page_token is None and offset == 0,
      "next_page_token": next_page_token,
      "page_size": page_size,
      "requested_page_size": requested_page_size,
      "page_size_clamped": requested_page_size != page_size,
      "bulk_export_call": {
          "tool": "export_accessible_accounts_csv",
          "arguments": {"snapshot_token": snapshot_token},
      },
  }


@local_write_tool(mcp, tags={"accounts", "discovery", "export"})
def export_accessible_accounts_csv(
    snapshot_token: str,
    output_path: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
  """Exports an exact accessible-account snapshot to CSV.

  Args:
      snapshot_token: Token from `list_accessible_accounts.bulk_export_call`.
      output_path: Optional destination inside GOOGLE_ADS_MCP_EXPORT_DIR.
          Defaults to a uniquely named file in the system temp directory.
      overwrite: Whether to replace an existing explicit output path.

  Returns:
      CSV path, row count, columns, and bytes written.
  """
  snapshot_id = _decode_account_snapshot_token(snapshot_token)
  account_ids = _get_account_snapshot(snapshot_id)
  resolved_output_path = _resolve_export_path(output_path, overwrite)
  rows = [{"customer_id": account_id} for account_id in account_ids]
  file_path, columns, bytes_written = _write_csv_rows(
      rows,
      resolved_output_path,
      overwrite,
      columns=["customer_id"],
  )
  return {
      "file_path": file_path,
      "row_count": len(rows),
      "total_row_count": len(rows),
      "columns": columns,
      "bytes_written": bytes_written,
      "complete": True,
      "snapshot_token": snapshot_token,
  }


def _account_snapshot_size(account_ids: tuple[str, ...]) -> int:
  """Returns the retained payload size for one account snapshot."""
  return sum(len(account_id.encode("utf-8")) for account_id in account_ids)


def _prune_account_snapshots_unlocked(now: float) -> None:
  """Removes expired accessible-account snapshots under the cache lock."""
  expired_keys = [
      cache_key
      for cache_key, cache_entry in _ACCOUNT_SNAPSHOT_CACHE.items()
      if now - cache_entry[0] > _ACCOUNT_SNAPSHOT_CACHE_TTL_SECONDS
  ]
  for cache_key in expired_keys:
    _ACCOUNT_SNAPSHOT_CACHE.pop(cache_key, None)


def _enforce_account_snapshot_bounds_unlocked(credential_scope: str) -> None:
  """Applies per-credential, process-count, and process-byte cache bounds."""
  scoped_keys = [
      cache_key
      for cache_key in _ACCOUNT_SNAPSHOT_CACHE
      if cache_key[0] == credential_scope
  ]
  while len(scoped_keys) > _ACCOUNT_SNAPSHOT_CACHE_MAX_ENTRIES_PER_SCOPE:
    _ACCOUNT_SNAPSHOT_CACHE.pop(scoped_keys.pop(0), None)

  while len(_ACCOUNT_SNAPSHOT_CACHE) > _ACCOUNT_SNAPSHOT_CACHE_MAX_ENTRIES:
    _ACCOUNT_SNAPSHOT_CACHE.popitem(last=False)

  retained_bytes = sum(
      cache_entry[2] for cache_entry in _ACCOUNT_SNAPSHOT_CACHE.values()
  )
  # Always retain the newest snapshot even if that one result exceeds the
  # ordinary process budget; otherwise a successful read would return dead
  # continuation/export tokens immediately.
  while (
      retained_bytes > _ACCOUNT_SNAPSHOT_CACHE_MAX_BYTES
      and len(_ACCOUNT_SNAPSHOT_CACHE) > 1
  ):
    _, removed_entry = _ACCOUNT_SNAPSHOT_CACHE.popitem(last=False)
    retained_bytes -= removed_entry[2]


def _store_account_snapshot(account_ids: tuple[str, ...]) -> str:
  """Stores one credential-scoped account snapshot and returns its ID."""
  credential_scope = _page_cache_scope()
  snapshot_id = uuid.uuid4().hex
  with _ACCOUNT_SNAPSHOT_CACHE_LOCK:
    now = time.monotonic()
    _prune_account_snapshots_unlocked(now)
    cache_key = (credential_scope, snapshot_id)
    _ACCOUNT_SNAPSHOT_CACHE[cache_key] = (
        now,
        account_ids,
        _account_snapshot_size(account_ids),
    )
    _ACCOUNT_SNAPSHOT_CACHE.move_to_end(cache_key)
    _enforce_account_snapshot_bounds_unlocked(credential_scope)
  return snapshot_id


def _get_account_snapshot(snapshot_id: str) -> tuple[str, ...]:
  """Returns an unexpired account snapshot for the active credentials."""
  credential_scope = _page_cache_scope()
  cache_key = (credential_scope, snapshot_id)
  with _ACCOUNT_SNAPSHOT_CACHE_LOCK:
    _prune_account_snapshots_unlocked(time.monotonic())
    cache_entry = _ACCOUNT_SNAPSHOT_CACHE.get(cache_key)
    if cache_entry is not None:
      _ACCOUNT_SNAPSHOT_CACHE.move_to_end(cache_key)
      return cache_entry[1]
  raise ToolError(
      "Accessible-account snapshot expired, was evicted, or belongs to "
      "different Google Ads credentials. Call list_accessible_accounts again "
      "without page_token, then promptly use its continuation or export call."
  )


def _encode_account_page_token(
    snapshot_id: str,
    offset: int,
    page_size: int,
) -> str:
  """Encodes an account snapshot continuation token."""
  return f"{_ACCOUNT_PAGE_TOKEN_PREFIX}{snapshot_id}:{offset}:{page_size}"


def _decode_account_page_token(page_token: str) -> tuple[str, int, int]:
  """Validates and decodes an account snapshot continuation token."""
  if not isinstance(page_token, str):
    raise ToolError("Invalid page_token.")
  match = re.fullmatch(
      rf"{re.escape(_ACCOUNT_PAGE_TOKEN_PREFIX)}"
      r"([0-9a-f]{32}):([0-9]{1,18}):([0-9]{1,18})",
      page_token,
  )
  if not match:
    raise ToolError("Invalid page_token.")
  offset, page_size = int(match.group(2)), int(match.group(3))
  if offset <= 0 or page_size <= 0:
    raise ToolError("Invalid page_token.")
  return match.group(1), offset, page_size


def _encode_account_snapshot_token(snapshot_id: str) -> str:
  """Encodes an exact accessible-account export token."""
  return f"{_ACCOUNT_SNAPSHOT_TOKEN_PREFIX}{snapshot_id}"


def _decode_account_snapshot_token(snapshot_token: str) -> str:
  """Validates an exact accessible-account export token."""
  if not isinstance(snapshot_token, str):
    raise ToolError(
        "Invalid snapshot_token. Use the exact bulk_export_call returned by "
        "list_accessible_accounts."
    )
  match = re.fullmatch(
      rf"{re.escape(_ACCOUNT_SNAPSHOT_TOKEN_PREFIX)}([0-9a-f]{{32}})",
      snapshot_token,
  )
  if not match:
    raise ToolError(
        "Invalid snapshot_token. Use the exact bulk_export_call returned by "
        "list_accessible_accounts."
    )
  return match.group(1)


def preprocess_gaql(query: str) -> str:
  """Preprocesses GAQL for safer, lower-retry execution."""
  return preprocess_gaql_query(query)


def _load_known_gaql_field_names() -> tuple[str, ...]:
  """Loads local field metadata for lightweight error suggestions."""
  global _GAQL_FIELD_NAMES_CACHE
  if _GAQL_FIELD_NAMES_CACHE is not None:
    return _GAQL_FIELD_NAMES_CACHE

  fields_path = os.path.join(MODULE_DIR, "context", "fields.yaml")
  try:
    with open(fields_path, "r", encoding="utf-8") as f:
      fields = yaml.safe_load(f) or {}
  except FileNotFoundError:
    _GAQL_FIELD_NAMES_CACHE = ()
    return _GAQL_FIELD_NAMES_CACHE

  _GAQL_FIELD_NAMES_CACHE = tuple(sorted(fields))
  return _GAQL_FIELD_NAMES_CACHE


def _field_suggestions(field_name: str) -> list[str]:
  if field_name in _KNOWN_FIELD_CORRECTIONS:
    return [_KNOWN_FIELD_CORRECTIONS[field_name]]

  field_names = _load_known_gaql_field_names()
  if not field_names:
    return []
  return difflib.get_close_matches(
      field_name,
      field_names,
      n=3,
      cutoff=0.72,
  )


def _format_google_ads_error(error: GoogleAdsException) -> str:
  """Formats Google Ads errors with common self-recovery hints."""
  message = "\n".join(str(i) for i in error.failure.errors)
  hints = []

  unrecognized_field_match = re.search(
      r"Unrecognized field in the query: '([^']+)'",
      message,
  )
  if unrecognized_field_match:
    field_name = unrecognized_field_match.group(1)
    suggestions = _field_suggestions(field_name)
    if suggestions:
      hints.append("Did you mean: " + ", ".join(suggestions) + "?")
    else:
      hints.append(
          "Use get_resource_metadata or search_google_ads_fields to find "
          "valid selectable fields for this FROM resource."
      )

  referenced_field_match = re.search(
      r"must be present in SELECT clause: '([^']+)'",
      message,
  )
  if referenced_field_match:
    hints.append(
        "Add "
        f"{referenced_field_match.group(1)} to SELECT; GAQL requires "
        "filtered or sorted fields to be selected, except core date "
        "segments."
    )

  for markers, require_all, hint in _GOOGLE_ADS_ERROR_HINTS:
    marker_matcher = all if require_all else any
    if marker_matcher(marker in message for marker in markers):
      hints.append(hint)

  if not hints:
    return message
  return message + "\n\nHints:\n- " + "\n- ".join(hints)


@contextlib.contextmanager
def handle_google_ads_errors():
  """Converts Google Ads API errors into hint-formatted ToolErrors."""
  try:
    yield
  except GoogleAdsException as exc:
    raise ToolError(_format_google_ads_error(exc)) from exc
  except google_exceptions.GoogleAPICallError as exc:
    raise ToolError(str(exc)) from exc


def _google_ads_error_text(error: GoogleAdsException) -> str:
  """Returns searchable text for a GoogleAdsException."""
  parts = [str(error)]
  failure = getattr(error, "failure", None)
  for failure_error in getattr(failure, "errors", ()) or ():
    parts.append(str(failure_error))

  for attr_name in ("error", "call"):
    attr_value = getattr(error, attr_name, None)
    if attr_value:
      parts.append(str(attr_value))

  return "\n".join(part for part in parts if part)


def _is_retryable_google_ads_error(error: GoogleAdsException) -> bool:
  message = _google_ads_error_text(error)
  if any(
      marker in message for marker in _NON_RETRYABLE_GOOGLE_ADS_ERROR_MARKERS
  ):
    return False
  return any(
      marker in message for marker in _TRANSIENT_GOOGLE_ADS_ERROR_MARKERS
  )


def _validate_optional_positive_int(
    value: int | None,
    field_name: str,
) -> None:
  """Validates optional positive integer tool arguments."""
  if value is None:
    return
  if isinstance(value, bool) or not isinstance(value, int):
    raise ToolError(f"{field_name} must be an integer.")
  if value <= 0:
    raise ToolError(f"{field_name} must be greater than 0.")


def applied_inline_page_size(requested_page_size: int | None) -> int:
  """Returns the presentation size without limiting full-data access."""
  _validate_optional_positive_int(requested_page_size, "page_size")
  if requested_page_size is None:
    return _MAX_INLINE_PAGE_SIZE
  return min(requested_page_size, _MAX_INLINE_PAGE_SIZE)


def _serialized_json_bytes(value: Any) -> int:
  """Returns a conservative UTF-8 size for an inline JSON value."""
  return len(
      json.dumps(
          value,
          ensure_ascii=False,
          separators=(",", ":"),
          default=str,
      ).encode("utf-8")
  )


def _inline_page_plan(
    rows: list[dict[str, Any]],
    page_size: int,
) -> list[dict[str, Any]]:
  """Partitions rows by count and bytes without restricting snapshot export."""
  pages = []
  cursor = 0
  while cursor < len(rows):
    start_offset = cursor
    display_rows = []
    page_bytes = 2
    omitted_row_count = 0
    byte_limited = False
    while cursor < len(rows) and len(display_rows) < page_size:
      row = rows[cursor]
      row_bytes = _serialized_json_bytes(row)
      separator_bytes = 1 if display_rows else 0
      if page_bytes + row_bytes > _MAX_INLINE_PAGE_BYTES and not display_rows:
        display_rows.append(
            {
                _INLINE_OMISSION_KEY: {
                    "row_index": cursor,
                    "serialized_bytes": row_bytes,
                    "reason": "single_row_exceeds_inline_byte_budget",
                    "full_row_available_via": "bulk_export_call",
                }
            }
        )
        omitted_row_count = 1
        byte_limited = True
        cursor += 1
        break
      if (
          display_rows
          and page_bytes + separator_bytes + row_bytes > _MAX_INLINE_PAGE_BYTES
      ):
        byte_limited = True
        break
      display_rows.append(row)
      page_bytes += separator_bytes + row_bytes
      cursor += 1
    pages.append(
        {
            "start_offset": start_offset,
            "end_offset": cursor,
            "rows": display_rows,
            "inline_bytes": _serialized_json_bytes(display_rows),
            "inline_omitted_row_count": omitted_row_count,
            "byte_limited": byte_limited,
        }
    )
  return pages


def is_inline_omission(row: dict[str, Any]) -> bool:
  """Returns whether a row is an explicit oversized-row placeholder."""
  return _INLINE_OMISSION_KEY in row


def project_inline_rows(
    rows: list[dict[str, Any]],
    projector: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
  """Projects data rows while preserving inline-omission placeholders."""
  return [row if is_inline_omission(row) else projector(row) for row in rows]


def bound_inline_sections(
    sections: dict[str, list[dict[str, Any]]],
    *,
    max_rows: int = _MAX_INLINE_PAGE_SIZE,
    max_bytes: int = INLINE_SECTION_BYTE_LIMIT,
) -> dict[str, Any]:
  """Shares one count/byte presentation budget across fan-out sections.

  This helper only bounds the inline representation. Callers retain their
  source totals, continuation tokens, and exact snapshot exports so no source
  data becomes inaccessible when a later section is omitted from the preview.
  """
  bounded_sections = {section_name: [] for section_name in sections}
  omitted_counts = {section_name: 0 for section_name in sections}
  represented_count = 0
  limited_by_rows = False
  limited_by_bytes = False

  for section_name, rows in sections.items():
    for row_index, row in enumerate(rows):
      if represented_count >= max_rows:
        omitted_counts[section_name] += len(rows) - row_index
        limited_by_rows = True
        break
      bounded_sections[section_name].append(row)
      if _serialized_json_bytes(bounded_sections) > max_bytes:
        bounded_sections[section_name].pop()
        omitted_counts[section_name] += len(rows) - row_index
        limited_by_bytes = True
        break
      represented_count += 1

  returned_counts = {
      section_name: sum(not is_inline_omission(row) for row in section_rows)
      for section_name, section_rows in bounded_sections.items()
  }
  represented_counts = {
      section_name: len(section_rows)
      for section_name, section_rows in bounded_sections.items()
  }
  return {
      "sections": bounded_sections,
      "returned_counts": returned_counts,
      "represented_counts": represented_counts,
      "omitted_counts": omitted_counts,
      "limited": limited_by_rows or limited_by_bytes,
      "limited_by_rows": limited_by_rows,
      "limited_by_bytes": limited_by_bytes,
      "inline_bytes": _serialized_json_bytes(bounded_sections),
      "inline_byte_limit": max_bytes,
      "inline_row_limit": max_rows,
  }


def build_bounded_materialized_response(
    materialized_result: dict[str, Any],
    section_keys: tuple[str, ...],
    *,
    artifact_key: str,
    truncation_note: str,
    artifact_failure_is_error: bool = True,
    defer_artifact_write: bool = False,
) -> dict[str, Any]:
  """Bounds materialized arrays and preserves omitted values losslessly.

  Small responses are returned unchanged. When aggregate result arrays exceed
  the shared inline count or byte budget, they are shortened together. Normal
  local/mutation callers write a temporary CSV; read finalizers defer that
  write and return an exact in-memory snapshot export call instead.
  """
  section_values = {}
  section_kinds = {}
  for section_key in section_keys:
    values = materialized_result.get(section_key)
    if isinstance(values, list):
      section_kinds[section_key] = "list"
      section_values[section_key] = [{"value": value} for value in values]
    elif isinstance(values, dict):
      section_kinds[section_key] = "dict"
      section_values[section_key] = [
          {"entry_key": entry_key, "value": value}
          for entry_key, value in values.items()
      ]
    else:
      raise ValueError(
          f"Materialized result section {section_key!r} must be a list or "
          "dict."
      )

  delivery = bound_inline_sections(section_values)
  bounded_sections = delivery.pop("sections")
  whole_response_too_large = (
      _serialized_json_bytes(materialized_result) > INLINE_RESPONSE_BYTE_LIMIT
  )
  if not delivery["limited"] and not whole_response_too_large:
    return materialized_result

  base_result = {
      key: value
      for key, value in materialized_result.items()
      if key not in section_keys
  }
  base_needs_artifact = _serialized_json_bytes(base_result) > 4 * 1024
  artifact_rows = [
      {
          "result_type": section_key,
          "result_index": result_index,
          "result": json.dumps(
              (
                  row["value"]
                  if section_kinds[section_key] == "list"
                  else {
                      "entry_key": row["entry_key"],
                      "value": row["value"],
                  }
              ),
              ensure_ascii=False,
              separators=(",", ":"),
              default=str,
          ),
      }
      for section_key in section_keys
      for result_index, row in enumerate(section_values[section_key])
  ]
  if base_needs_artifact:
    artifact_rows.append(
        {
            "result_type": "response_metadata",
            "result_index": 0,
            "result": json.dumps(
                base_result,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
        }
    )
  artifact_columns = ["result_type", "result_index", "result"]
  if defer_artifact_write:
    snapshot_token = _store_materialized_snapshot(artifact_rows)
    artifact = {
        "available": True,
        "row_count": len(artifact_rows),
        "columns": artifact_columns,
        "export_call": {
            "tool": "export_materialized_response_csv",
            "arguments": {"snapshot_token": snapshot_token},
        },
        "expires_after_seconds": _MATERIALIZED_SNAPSHOT_CACHE_TTL_SECONDS,
    }
  else:
    try:
      file_path, columns, bytes_written = write_rows_to_temp_csv(
          artifact_rows,
          columns=artifact_columns,
      )
      artifact = {
          "available": True,
          "file_path": file_path,
          "row_count": len(artifact_rows),
          "bytes_written": bytes_written,
          "columns": columns,
          **managed_temp_artifact_metadata(file_path),
      }
    except (OSError, ToolError) as exc:
      if artifact_failure_is_error:
        raise
      artifact = {
          "available": False,
          "row_count": len(artifact_rows),
          "error": str(exc),
          "mutation_completed": True,
          "do_not_retry_mutation": True,
          "recovery": (
              "The Google Ads mutation already completed. Do not repeat it. "
              "Use the corresponding list/read tool to verify final state."
          ),
      }
  effective_truncation_note = (
      truncation_note
      if artifact["available"]
      else (
          "Inline result arrays were bounded, but the artifact could not be "
          f"written. Inspect {artifact_key}. If this was a mutation, it "
          "already completed; do not retry it."
      )
  )

  metadata_omitted_fields = []
  if base_needs_artifact:
    compact_base_result = {}
    for field_name, value in base_result.items():
      candidate = {**compact_base_result, field_name: value}
      if _serialized_json_bytes(candidate) <= 4 * 1024:
        compact_base_result[field_name] = value
      else:
        metadata_omitted_fields.append(field_name)
        placeholder = {
            "inline_omitted": True,
            "reason": "whole_response_byte_budget",
            "full_value_available_in": artifact_key,
        }
        candidate = {**compact_base_result, field_name: placeholder}
        if _serialized_json_bytes(candidate) <= 4 * 1024:
          compact_base_result[field_name] = placeholder
    base_result = compact_base_result

  def _assemble_result(
      current_delivery: dict[str, Any],
      current_sections: dict[str, list[dict[str, Any]]],
  ) -> dict[str, Any]:
    delivery_metadata = {
        key: value
        for key, value in current_delivery.items()
        if key != "sections"
    }
    if metadata_omitted_fields:
      omitted_field_preview = []
      for field_name in metadata_omitted_fields:
        candidate = [*omitted_field_preview, field_name]
        if len(candidate) > 25 or _serialized_json_bytes(candidate) > 1024:
          break
        omitted_field_preview = candidate
      delivery_metadata.update(
          {
              "metadata_omitted_field_count": len(metadata_omitted_fields),
              "metadata_omitted_fields": omitted_field_preview,
              "metadata_omitted_fields_truncated": (
                  len(omitted_field_preview) < len(metadata_omitted_fields)
              ),
          }
      )

    def _restore_section(
        section_key: str,
        rows: list[dict[str, Any]],
    ) -> list[Any] | dict[Any, Any]:
      if section_kinds[section_key] == "list":
        return [row["value"] for row in rows]
      return {row["entry_key"]: row["value"] for row in rows}

    return {
        **base_result,
        **{
            section_key: _restore_section(
                section_key,
                current_sections[section_key],
            )
            for section_key in section_keys
        },
        "complete_counts": {
            section_key: len(section_values[section_key])
            for section_key in section_keys
        },
        "returned_counts": delivery_metadata["returned_counts"],
        "shared_inline_delivery": delivery_metadata,
        "truncated": True,
        artifact_key: artifact,
        "truncation_note": effective_truncation_note,
    }

  result = _assemble_result(delivery, bounded_sections)
  section_byte_limit = INLINE_SECTION_BYTE_LIMIT
  while _serialized_json_bytes(result) > INLINE_RESPONSE_BYTE_LIMIT and any(
      bounded_sections.values()
  ):
    overflow_bytes = (
        _serialized_json_bytes(result) - INLINE_RESPONSE_BYTE_LIMIT
    )
    section_byte_limit = max(
        0,
        section_byte_limit - overflow_bytes - 256,
    )
    delivery_with_sections = bound_inline_sections(
        section_values,
        max_bytes=section_byte_limit,
    )
    bounded_sections = delivery_with_sections["sections"]
    result = _assemble_result(delivery_with_sections, bounded_sections)
  return result


def finalize_bounded_response(
    response: dict[str, Any],
    section_keys: tuple[str, ...],
) -> dict[str, Any]:
  """Finalizes a variable-cardinality read under one whole-response budget.

  Every requested source row is still fetched before this presentation step.
  Small responses retain their existing shape. If sections or enrichment make
  the serialized response too large, the inline representation is bounded
  across all named list/dict sections. The read stores an in-memory exact
  snapshot and returns an explicit local-write export call; it never writes a
  file implicitly. Callers should also retain exact source snapshot exports.
  """
  return build_bounded_materialized_response(
      response,
      section_keys,
      artifact_key="full_materialized_response_export",
      truncation_note=(
          "Inline read sections were bounded across the whole response. "
          "Call full_materialized_response_export.export_call to write every "
          "already-materialized section item and any omitted response "
          "metadata to CSV. Exact source export calls remain valid."
      ),
      defer_artifact_write=True,
  )


def build_bounded_mutation_response(
    mutation_result: dict[str, Any],
    section_keys: tuple[str, ...],
) -> dict[str, Any]:
  """Bounds mutation arrays without restricting applied operations."""
  return build_bounded_materialized_response(
      mutation_result,
      section_keys,
      artifact_key="full_mutation_result_artifact",
      truncation_note=(
          "The mutation used every input. Inline result arrays were bounded; "
          "full_mutation_result_artifact contains every already-fetched result "
          "item, including all success and failure details."
      ),
      artifact_failure_is_error=False,
  )


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


def _decode_page_token(
    page_token: str | None,
) -> tuple[str | None, int, int | None]:
  """Decodes a snapshot-bound page token."""
  if not page_token:
    return None, 0, None
  if not isinstance(page_token, str):
    raise ToolError("Invalid page_token.")
  match = re.fullmatch(
      r"([0-9a-f]{32}):([0-9]+)(?::([0-9]+))?",
      page_token,
  )
  if not match:
    raise ToolError("Invalid page_token.")
  offset_text = match.group(2)
  page_size_text = match.group(3)
  if len(offset_text) > 18 or (
      page_size_text is not None and len(page_size_text) > 18
  ):
    raise ToolError("Invalid page_token.")
  try:
    offset = int(offset_text)
    bound_page_size = (
        int(page_size_text) if page_size_text is not None else None
    )
  except ValueError as exc:
    raise ToolError("Invalid page_token.") from exc
  if bound_page_size is not None and bound_page_size <= 0:
    raise ToolError("Invalid page_token.")
  return match.group(1), offset, bound_page_size


def _encode_snapshot_token(
    snapshot_id: str,
    *,
    page_size: int | None = None,
    total_page_count: int | None = None,
    byte_limited: bool = False,
) -> str:
  """Builds an opaque exact-snapshot token with optional delivery metadata."""
  token = f"{_SNAPSHOT_TOKEN_PREFIX}{snapshot_id}"
  if page_size is None or total_page_count is None:
    return token
  return f"{token}:{page_size}:{total_page_count}:{int(byte_limited)}"


def _snapshot_delivery_metadata(
    snapshot_token: str,
) -> tuple[int | None, int | None, bool]:
  """Returns page-size metadata carried by a snapshot token."""
  match = re.fullmatch(
      rf"{re.escape(_SNAPSHOT_TOKEN_PREFIX)}([0-9a-f]{{32}})"
      r"(?::([0-9]+):([0-9]+):([01]))?",
      snapshot_token,
  )
  if not match:
    raise ToolError(
        "Invalid snapshot_token. Use the exact bulk_export_call returned by "
        "the original list or report response."
    )
  if match.group(2) is None:
    return None, None, False
  return int(match.group(2)), int(match.group(3)), match.group(4) == "1"


def _decode_snapshot_token(snapshot_token: str) -> str:
  """Validates an exact-snapshot export token."""
  if not isinstance(snapshot_token, str):
    raise ToolError(
        "Invalid snapshot_token. Use the exact bulk_export_call returned by "
        "the original list or report response."
    )
  match = re.fullmatch(
      rf"{re.escape(_SNAPSHOT_TOKEN_PREFIX)}([0-9a-f]{{32}})"
      r"(?::[0-9]+:[0-9]+:[01])?",
      snapshot_token,
  )
  if not match:
    raise ToolError(
        "Invalid snapshot_token. Use the exact bulk_export_call returned by "
        "the original list or report response."
    )
  return match.group(1)


def _page_cache_key(
    query: str,
    customer_id: str,
    login_customer_id: str | None,
    row_sort_fields: tuple[str, ...] | None = None,
) -> _PagedQueryCacheKey:
  """Builds a stable cache key for paged GAQL queries."""
  return (
      _page_cache_scope(),
      customer_id,
      login_customer_id,
      query,
      tuple(row_sort_fields or ()),
  )


def _page_cache_scope() -> str:
  """Returns the credential scope used by paged-query snapshots."""
  return get_ads_credential_cache_scope()


def get_ads_credential_cache_scope() -> str:
  """Returns a non-secret identity for the active Google Ads credentials."""
  access_token = get_access_token()
  if access_token and access_token.token:
    token_digest = hashlib.sha256(
        str(access_token.token).encode("utf-8")
    ).hexdigest()
    return f"oauth:{token_digest}"

  default_path = f"{ROOT_DIR}/google-ads.yaml"
  credentials_path = os.path.realpath(
      os.environ.get("GOOGLE_ADS_CREDENTIALS", default_path)
  )
  try:
    credentials_stat = os.stat(credentials_path)
  except OSError as exc:
    raise FileNotFoundError(
        "Google Ads credentials YAML file is not found. "
        "Check [GOOGLE_ADS_CREDENTIALS] config."
    ) from exc
  return (
      f"yaml:{credentials_path}:{credentials_stat.st_dev}:"
      f"{credentials_stat.st_ino}:{credentials_stat.st_ctime_ns}:"
      f"{credentials_stat.st_mtime_ns}:{credentials_stat.st_size}"
  )


def _prune_materialized_snapshot_cache_unlocked(now: float) -> None:
  """Removes expired deferred read exports while holding the cache lock."""
  expired_keys = [
      cache_key
      for cache_key, (cached_at, _, _) in _MATERIALIZED_SNAPSHOT_CACHE.items()
      if now - cached_at > _MATERIALIZED_SNAPSHOT_CACHE_TTL_SECONDS
  ]
  for cache_key in expired_keys:
    _MATERIALIZED_SNAPSHOT_CACHE.pop(cache_key, None)


def _store_materialized_snapshot(rows: list[dict[str, Any]]) -> str:
  """Stores one exact read result for a later explicit local-write export."""
  credential_scope = get_ads_credential_cache_scope()
  snapshot_id = uuid.uuid4().hex
  cache_key = (credential_scope, snapshot_id)
  with _MATERIALIZED_SNAPSHOT_CACHE_LOCK:
    _prune_materialized_snapshot_cache_unlocked(time.monotonic())
    _MATERIALIZED_SNAPSHOT_CACHE[cache_key] = (
        time.monotonic(),
        rows,
        _serialized_json_bytes(rows),
    )
    _MATERIALIZED_SNAPSHOT_CACHE.move_to_end(cache_key)
    scoped_keys = [
        current_key
        for current_key in _MATERIALIZED_SNAPSHOT_CACHE
        if current_key[0] == credential_scope
    ]
    while (
        len(scoped_keys) > _MATERIALIZED_SNAPSHOT_CACHE_MAX_ENTRIES_PER_SCOPE
    ):
      _MATERIALIZED_SNAPSHOT_CACHE.pop(scoped_keys.pop(0), None)
    while (
        len(_MATERIALIZED_SNAPSHOT_CACHE)
        > _MATERIALIZED_SNAPSHOT_CACHE_MAX_ENTRIES
    ):
      _MATERIALIZED_SNAPSHOT_CACHE.popitem(last=False)
    retained_bytes = sum(
        cache_entry[2] for cache_entry in _MATERIALIZED_SNAPSHOT_CACHE.values()
    )
    while (
        retained_bytes > _MATERIALIZED_SNAPSHOT_CACHE_MAX_BYTES
        and len(_MATERIALIZED_SNAPSHOT_CACHE) > 1
    ):
      _, removed_entry = _MATERIALIZED_SNAPSHOT_CACHE.popitem(last=False)
      retained_bytes -= removed_entry[2]
  return f"{_MATERIALIZED_SNAPSHOT_TOKEN_PREFIX}{snapshot_id}"


def _get_materialized_snapshot_rows(
    snapshot_token: str,
) -> list[dict[str, Any]]:
  """Returns a credential-scoped deferred read export snapshot."""
  if not isinstance(snapshot_token, str):
    raise ToolError(
        "Invalid materialized snapshot_token. Use the exact export_call "
        "returned by the original read response."
    )
  match = re.fullmatch(
      rf"{re.escape(_MATERIALIZED_SNAPSHOT_TOKEN_PREFIX)}([0-9a-f]{{32}})",
      snapshot_token,
  )
  if not match:
    raise ToolError(
        "Invalid materialized snapshot_token. Use the exact export_call "
        "returned by the original read response."
    )
  cache_key = (get_ads_credential_cache_scope(), match.group(1))
  with _MATERIALIZED_SNAPSHOT_CACHE_LOCK:
    now = time.monotonic()
    _prune_materialized_snapshot_cache_unlocked(now)
    cache_entry = _MATERIALIZED_SNAPSHOT_CACHE.get(cache_key)
    if cache_entry is None:
      raise ToolError(
          "materialized snapshot_token expired, was evicted, or belongs to "
          "different Google Ads credentials. Rerun the original read to "
          "create a fresh export_call."
      )
    _MATERIALIZED_SNAPSHOT_CACHE.move_to_end(cache_key)
    return deepcopy(cache_entry[1])


class _SpooledGaqlSnapshot:
  """Immutable disk-backed GAQL rows with bounded page reads."""

  def __init__(
      self,
      file_path: str,
      row_count: int,
      columns: tuple[str, ...],
  ):
    self.file_path = file_path
    self.row_count = row_count
    self.columns = columns
    self.serialized_bytes = os.path.getsize(file_path)
    self._plan_lock = threading.Lock()

  def __del__(self):
    with contextlib.suppress(OSError):
      os.remove(self.file_path)

  def iter_rows(self) -> Iterator[dict[str, Any]]:
    """Streams ordered rows without retaining the full result in memory."""
    with sqlite3.connect(self.file_path) as connection:
      cursor = connection.execute(
          "SELECT payload FROM ordered_rows ORDER BY position"
      )
      for (payload,) in cursor:
        yield json.loads(payload)

  def row_at(self, position: int) -> dict[str, Any]:
    """Returns one zero-based ordered row."""
    with sqlite3.connect(self.file_path) as connection:
      result = connection.execute(
          "SELECT payload FROM ordered_rows WHERE position = ?",
          (position + 1,),
      ).fetchone()
    if result is None:
      raise IndexError(position)
    return json.loads(result[0])

  def rows_between(self, start: int, end: int) -> list[dict[str, Any]]:
    """Returns one bounded half-open row interval."""
    with sqlite3.connect(self.file_path) as connection:
      cursor = connection.execute(
          "SELECT payload FROM ordered_rows "
          "WHERE position > ? AND position <= ? ORDER BY position",
          (start, end),
      )
      return [json.loads(payload) for (payload,) in cursor]

  def ensure_page_plan(self, page_size: int) -> None:
    """Materializes compact page boundaries in SQLite, not Python memory."""
    with self._plan_lock, sqlite3.connect(self.file_path) as connection:
      existing = connection.execute(
          "SELECT 1 FROM page_plans WHERE page_size = ? LIMIT 1",
          (page_size,),
      ).fetchone()
      if existing is not None or self.row_count == 0:
        return

      cursor = connection.execute(
          "SELECT position, row_bytes FROM ordered_rows ORDER BY position"
      )
      start_offset = 0
      page_bytes = 2
      displayed_count = 0
      plans = []
      for position, row_bytes in cursor:
        row_offset = position - 1
        oversized_row = row_bytes > _MAX_INLINE_PAGE_BYTES - 2
        if oversized_row and displayed_count:
          plans.append(
              (
                  page_size,
                  start_offset,
                  row_offset,
                  page_bytes,
                  0,
                  1,
              )
          )
          start_offset = row_offset
          page_bytes = 2
          displayed_count = 0
        if oversized_row:
          placeholder = {
              _INLINE_OMISSION_KEY: {
                  "row_index": row_offset,
                  "serialized_bytes": row_bytes,
                  "reason": "single_row_exceeds_inline_byte_budget",
                  "full_row_available_via": "bulk_export_call",
              }
          }
          plans.append(
              (
                  page_size,
                  start_offset,
                  row_offset + 1,
                  _serialized_json_bytes([placeholder]),
                  1,
                  1,
              )
          )
          start_offset = row_offset + 1
          page_bytes = 2
          displayed_count = 0
          continue
        separator_bytes = 1 if displayed_count else 0
        if displayed_count and (
            displayed_count >= page_size
            or page_bytes + separator_bytes + row_bytes
            > _MAX_INLINE_PAGE_BYTES
        ):
          plans.append(
              (
                  page_size,
                  start_offset,
                  row_offset,
                  page_bytes,
                  0,
                  int(displayed_count < page_size),
              )
          )
          start_offset = row_offset
          page_bytes = 2
          displayed_count = 0
          separator_bytes = 0
        page_bytes += separator_bytes + row_bytes
        displayed_count += 1
      if displayed_count:
        plans.append(
            (
                page_size,
                start_offset,
                self.row_count,
                page_bytes,
                0,
                0,
            )
        )
      connection.executemany(
          "INSERT OR IGNORE INTO page_plans "
          "(page_size, start_offset, end_offset, inline_bytes, "
          "omitted_count, byte_limited) VALUES (?, ?, ?, ?, ?, ?)",
          plans,
      )

  def page(self, page_size: int, offset: int) -> dict[str, Any] | None:
    """Reads one validated page and compact plan metadata."""
    self.ensure_page_plan(page_size)
    if self.row_count == 0 and offset == 0:
      return {
          "start_offset": 0,
          "end_offset": 0,
          "rows": [],
          "inline_bytes": 2,
          "inline_omitted_row_count": 0,
          "byte_limited": False,
          "total_page_count": 0,
          "byte_limited_pagination": False,
      }
    with sqlite3.connect(self.file_path) as connection:
      plan = connection.execute(
          "SELECT end_offset, inline_bytes, omitted_count, byte_limited "
          "FROM page_plans WHERE page_size = ? AND start_offset = ?",
          (page_size, offset),
      ).fetchone()
      totals = connection.execute(
          "SELECT COUNT(*), COALESCE(MAX(byte_limited), 0) "
          "FROM page_plans WHERE page_size = ?",
          (page_size,),
      ).fetchone()
    if plan is None:
      return None
    end_offset, inline_bytes, omitted_count, byte_limited = plan
    if omitted_count:
      row = self.row_at(offset)
      row_bytes = _serialized_json_bytes(row)
      page_rows = [
          {
              _INLINE_OMISSION_KEY: {
                  "row_index": offset,
                  "serialized_bytes": row_bytes,
                  "reason": "single_row_exceeds_inline_byte_budget",
                  "full_row_available_via": "bulk_export_call",
              }
          }
      ]
    else:
      page_rows = self.rows_between(offset, end_offset)
    return {
        "start_offset": offset,
        "end_offset": end_offset,
        "rows": page_rows,
        "inline_bytes": inline_bytes,
        "inline_omitted_row_count": omitted_count,
        "byte_limited": bool(byte_limited),
        "total_page_count": totals[0],
        "byte_limited_pagination": bool(totals[1]),
    }


class _SpooledRows(Sequence[dict[str, Any]]):
  """Repeatable sequence view over a live immutable spool snapshot."""

  def __init__(self, snapshot: _SpooledGaqlSnapshot):
    self._snapshot = snapshot

  def __len__(self) -> int:
    return self._snapshot.row_count

  def __iter__(self) -> Iterator[dict[str, Any]]:
    return self._snapshot.iter_rows()

  def __getitem__(self, index):
    if isinstance(index, slice):
      start, stop, step = index.indices(len(self))
      if step == 1:
        return self._snapshot.rows_between(start, stop)
      return [
          self._snapshot.row_at(position)
          for position in range(start, stop, step)
      ]
    if index < 0:
      index += len(self)
    return self._snapshot.row_at(index)

  def __eq__(self, other: Any) -> bool:
    if isinstance(other, Sequence):
      return list(self) == list(other)
    return False


def _snapshot_cache_key(
    query_key: _PagedQueryCacheKey,
    snapshot_id: str,
) -> _PagedSnapshotCacheKey:
  """Builds the cache key for one exact query-result snapshot."""
  return (*query_key, snapshot_id)


def _snapshot_query_key(
    snapshot_key: _PagedSnapshotCacheKey,
) -> _PagedQueryCacheKey:
  """Returns a snapshot cache key's query-identity portion."""
  return (
      snapshot_key[0],
      snapshot_key[1],
      snapshot_key[2],
      snapshot_key[3],
      snapshot_key[4],
  )


def _remove_page_snapshot_unlocked(
    snapshot_key: _PagedSnapshotCacheKey,
) -> None:
  """Removes a snapshot and any latest-query pointer to it."""
  _PAGED_QUERY_CACHE.pop(snapshot_key, None)
  query_key = _snapshot_query_key(snapshot_key)
  if _PAGED_QUERY_LATEST.get(query_key) == snapshot_key[5]:
    _PAGED_QUERY_LATEST.pop(query_key, None)


def _get_page_snapshot_unlocked(
    query_key: _PagedQueryCacheKey,
    snapshot_id: str,
) -> _SpooledGaqlSnapshot | None:
  """Returns one exact cached snapshot while the cache lock is held."""
  snapshot_key = _snapshot_cache_key(query_key, snapshot_id)
  cache_entry = _PAGED_QUERY_CACHE.get(snapshot_key)
  if not cache_entry:
    return None

  cached_at, cached_snapshot, _ = cache_entry
  if (time.monotonic() - cached_at) > _PAGED_QUERY_CACHE_TTL_SECONDS:
    _remove_page_snapshot_unlocked(snapshot_key)
    return None

  _PAGED_QUERY_CACHE.move_to_end(snapshot_key)
  return cached_snapshot


def _get_export_snapshot_rows(snapshot_token: str) -> _SpooledRows:
  """Returns the exact cached rows authorized for the active credentials."""
  snapshot_id = _decode_snapshot_token(snapshot_token)
  credential_scope = _page_cache_scope()
  with _PAGED_QUERY_CACHE_LOCK:
    snapshot_key = next(
        (
            cache_key
            for cache_key in _PAGED_QUERY_CACHE
            if cache_key[0] == credential_scope and cache_key[5] == snapshot_id
        ),
        None,
    )
    if snapshot_key is not None:
      snapshot = _get_page_snapshot_unlocked(
          _snapshot_query_key(snapshot_key),
          snapshot_id,
      )
      if snapshot is not None:
        return _SpooledRows(snapshot)

  raise ToolError(
      "snapshot_token expired, was evicted, or belongs to different Google "
      "Ads credentials. Call the original list or report tool again without "
      "page_token, then promptly use the new bulk_export_call it returns."
  )


def _prune_page_cache_unlocked(now: float) -> None:
  """Removes expired snapshots and dangling latest-query pointers."""
  expired_snapshot_keys = [
      snapshot_key
      for snapshot_key, cache_entry in _PAGED_QUERY_CACHE.items()
      if (now - cache_entry[0]) > _PAGED_QUERY_CACHE_TTL_SECONDS
  ]
  for snapshot_key in expired_snapshot_keys:
    _remove_page_snapshot_unlocked(snapshot_key)

  dangling_query_keys = [
      query_key
      for query_key, snapshot_id in _PAGED_QUERY_LATEST.items()
      if _snapshot_cache_key(query_key, snapshot_id) not in _PAGED_QUERY_CACHE
  ]
  for query_key in dangling_query_keys:
    _PAGED_QUERY_LATEST.pop(query_key, None)


def _enforce_page_cache_bounds_unlocked(credential_scope: str) -> None:
  """Applies per-principal and process count/byte snapshot bounds."""
  scoped_snapshot_keys = [
      snapshot_key
      for snapshot_key in _PAGED_QUERY_CACHE
      if snapshot_key[0] == credential_scope
  ]
  while len(scoped_snapshot_keys) > _PAGED_QUERY_CACHE_MAX_ENTRIES_PER_SCOPE:
    _remove_page_snapshot_unlocked(scoped_snapshot_keys.pop(0))

  while len(_PAGED_QUERY_CACHE) > _PAGED_QUERY_CACHE_MAX_ENTRIES:
    oldest_snapshot_key = next(iter(_PAGED_QUERY_CACHE))
    _remove_page_snapshot_unlocked(oldest_snapshot_key)

  retained_bytes = sum(
      cache_entry[2] for cache_entry in _PAGED_QUERY_CACHE.values()
  )
  # Preserve the newest completed snapshot even when one on-disk spool exceeds
  # the ordinary process budget so freshly returned tokens do not start dead.
  while (
      retained_bytes > _PAGED_QUERY_CACHE_MAX_BYTES
      and len(_PAGED_QUERY_CACHE) > 1
  ):
    oldest_snapshot_key = next(iter(_PAGED_QUERY_CACHE))
    removed_entry = _PAGED_QUERY_CACHE[oldest_snapshot_key]
    _remove_page_snapshot_unlocked(oldest_snapshot_key)
    retained_bytes -= removed_entry[2]


def _publish_page_snapshot_unlocked(
    query_key: _PagedQueryCacheKey,
    snapshot_id: str,
    snapshot: _SpooledGaqlSnapshot,
    *,
    make_latest: bool = False,
    make_latest_if_missing: bool = False,
) -> None:
  """Publishes one exact snapshot and applies expiry and LRU bounds."""
  cached_at = time.monotonic()
  _prune_page_cache_unlocked(cached_at)
  snapshot_key = _snapshot_cache_key(query_key, snapshot_id)
  _PAGED_QUERY_CACHE[snapshot_key] = (
      cached_at,
      snapshot,
      snapshot.serialized_bytes,
  )
  _PAGED_QUERY_CACHE.move_to_end(snapshot_key)
  _enforce_page_cache_bounds_unlocked(query_key[0])
  if make_latest or (
      make_latest_if_missing and query_key not in _PAGED_QUERY_LATEST
  ):
    _PAGED_QUERY_LATEST[query_key] = snapshot_id


def _get_or_build_page_snapshot(
    query_key: _PagedQueryCacheKey,
    query: str,
    customer_id: str,
    login_customer_id: str | None,
    row_sort_fields: tuple[str, ...] | None,
) -> tuple[
    str,
    _SpooledGaqlSnapshot,
    futures.Future | None,
]:
  """Starts a fresh snapshot, sharing only concurrent identical query work.

  Completed snapshots remain addressable by their opaque continuation and
  export tokens. A new tokenless request intentionally does not reuse one:
  Google Ads state may have changed between independent calls.
  """
  with _PAGED_QUERY_CACHE_LOCK:
    build = _PAGED_QUERY_BUILDS.get(query_key)
    owns_build = build is None
    if owns_build:
      build = futures.Future()
      _PAGED_QUERY_BUILDS[query_key] = build

  if not owns_build:
    snapshot_id, snapshot = build.result()
    return snapshot_id, snapshot, None

  try:
    snapshot = _build_spooled_gaql_snapshot(
        query=query,
        customer_id=customer_id,
        login_customer_id=login_customer_id,
        row_sort_fields=row_sort_fields,
    )
    snapshot_id = uuid.uuid4().hex
  except BaseException as exc:
    with _PAGED_QUERY_CACHE_LOCK:
      if _PAGED_QUERY_BUILDS.get(query_key) is build:
        _PAGED_QUERY_BUILDS.pop(query_key, None)
    build.set_exception(exc)
    raise

  with _PAGED_QUERY_CACHE_LOCK:
    _publish_page_snapshot_unlocked(
        query_key,
        snapshot_id,
        snapshot,
        make_latest=True,
    )
  build.set_result((snapshot_id, snapshot))
  return snapshot_id, snapshot, build


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


def _allowed_export_bases() -> list[str]:
  """Returns real paths explicit export paths must stay within."""
  configured_base = os.environ.get("GOOGLE_ADS_MCP_EXPORT_DIR")
  if configured_base:
    return [os.path.realpath(configured_base)]

  allowed_bases = [os.path.realpath(tempfile.gettempdir())]
  # macOS gettempdir() is the per-user $TMPDIR; users reasonably expect
  # /tmp to count as the system temp directory too.
  if os.path.isdir("/tmp"):
    posix_tmp = os.path.realpath("/tmp")
    if posix_tmp not in allowed_bases:
      allowed_bases.append(posix_tmp)
  return allowed_bases


def _resolve_export_path(
    output_path: str | None,
    overwrite: bool,
) -> str | None:
  """Validates an explicit export path before any query work runs."""
  if not output_path:
    return None

  allowed_bases = _allowed_export_bases()
  resolved_path = os.path.realpath(output_path)
  for allowed_base in allowed_bases:
    try:
      if os.path.commonpath([allowed_base, resolved_path]) == allowed_base:
        break
    except ValueError:
      continue
  else:
    raise ToolError(
        "output_path must be inside GOOGLE_ADS_MCP_EXPORT_DIR "
        f"(currently {allowed_bases[0]}). Omit output_path to write a "
        "uniquely named temp file instead."
    )
  if os.path.isdir(resolved_path):
    raise ToolError("output_path must be a file path, not a directory.")
  if os.path.exists(resolved_path) and not overwrite:
    raise ToolError(
        "output_path already exists; pass overwrite=True to replace it."
    )
  return resolved_path


def _open_export_file(resolved_path: str, overwrite: bool) -> Any:
  """Opens an export target without following final-component symlinks."""
  open_flags = os.O_WRONLY | os.O_CREAT
  open_flags |= os.O_TRUNC if overwrite else os.O_EXCL
  open_flags |= getattr(os, "O_NOFOLLOW", 0)
  try:
    file_descriptor = os.open(resolved_path, open_flags, 0o644)
  except FileExistsError as exc:
    raise ToolError(
        "output_path already exists; pass overwrite=True to replace it."
    ) from exc
  except IsADirectoryError as exc:
    raise ToolError(
        "output_path must be a file path, not a directory."
    ) from exc
  except OSError as exc:
    raise ToolError(f"Unable to write output_path: {exc}") from exc
  return os.fdopen(file_descriptor, "w", newline="", encoding="utf-8")


def _write_csv_rows(
    rows: list[dict[str, Any]],
    resolved_output_path: str | None = None,
    overwrite: bool = False,
    columns: list[str] | None = None,
) -> tuple[str, list[str], int]:
  """Writes GAQL rows to CSV and returns the path, columns, and size."""
  with contextlib.ExitStack() as temp_cleanup:
    existing_mode = None
    if resolved_output_path:
      final_path = resolved_output_path
      parent_dir = os.path.dirname(final_path)
      if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
      if overwrite:
        try:
          existing_mode = stat.S_IMODE(os.stat(final_path).st_mode)
        except FileNotFoundError:
          pass
      file_descriptor, working_path = tempfile.mkstemp(
          prefix=".google_ads_mcp_",
          suffix=".tmp",
          dir=parent_dir or ".",
      )
      temp_cleanup.callback(_remove_export_file, working_path)
    else:
      final_path = None
      file_descriptor, working_path = tempfile.mkstemp(
          prefix="google_ads_mcp_",
          suffix=".csv",
      )
      temp_cleanup.callback(_remove_export_file, working_path)
    try:
      csv_file = os.fdopen(
          file_descriptor,
          "w",
          newline="",
          encoding="utf-8",
      )
    except OSError:
      os.close(file_descriptor)
      raise

    columns = list(columns) if columns is not None else _csv_columns(rows)
    with csv_file:
      writer = csv.writer(csv_file)
      if columns:
        writer.writerow(columns)
        for row in rows:
          writer.writerow(
              [_csv_cell_value(row.get(column)) for column in columns]
          )

    bytes_written = os.path.getsize(working_path)
    if final_path:
      try:
        if existing_mode is not None:
          os.chmod(working_path, existing_mode)
        if overwrite:
          with _MANAGED_TEMP_ARTIFACT_CONDITION:
            os.replace(working_path, final_path)
            _MANAGED_TEMP_ARTIFACTS.pop(final_path, None)
            _MANAGED_TEMP_ARTIFACT_CONDITION.notify_all()
        else:
          os.link(working_path, final_path)
          _remove_export_file(working_path)
      except FileExistsError as exc:
        raise ToolError(
            "output_path already exists; pass overwrite=True to replace it."
        ) from exc
      except OSError as exc:
        raise ToolError(f"Unable to write output_path: {exc}") from exc
      output_path = final_path
    else:
      output_path = working_path
      temp_cleanup.pop_all()
    return output_path, columns, bytes_written


def write_rows_to_temp_csv(
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> tuple[str, list[str], int]:
  """Writes an internally managed temporary CSV with bounded retention."""
  file_path, output_columns, bytes_written = _write_csv_rows(
      rows,
      columns=columns,
  )
  _register_managed_temp_artifact(file_path, bytes_written)
  return file_path, output_columns, bytes_written


def write_rows_to_explicit_csv(
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> tuple[str, list[str], int]:
  """Writes a user-requested temp CSV that is not auto-expired."""
  return _write_csv_rows(rows, columns=columns)


def write_rows_to_intermediate_csv(
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> tuple[str, list[str], int]:
  """Writes an internally scoped CSV whose caller owns immediate cleanup."""
  return _write_csv_rows(rows, columns=columns)


def _unlink_temp_artifacts(
    artifacts: list[tuple[str, int, int]],
) -> None:
  """Unlinks only the exact managed artifact inode selected for cleanup."""
  for file_path, expected_device, expected_inode in artifacts:
    with _MANAGED_TEMP_ARTIFACT_CONDITION:
      try:
        current_stat = os.stat(file_path, follow_symlinks=False)
      except OSError:
        continue
      if (current_stat.st_dev, current_stat.st_ino) != (
          expected_device,
          expected_inode,
      ):
        continue
      with contextlib.suppress(OSError):
        os.remove(file_path)


def _collect_expired_managed_temp_artifacts_unlocked(
    now: float,
) -> list[tuple[str, int, int]]:
  """Drops expired or externally removed files while the lock is held."""
  removed_artifacts = []
  for file_path, artifact in list(_MANAGED_TEMP_ARTIFACTS.items()):
    expires_at, _, _, expected_device, expected_inode = artifact
    try:
      current_stat = os.stat(file_path, follow_symlinks=False)
    except OSError:
      _MANAGED_TEMP_ARTIFACTS.pop(file_path, None)
      continue
    if (current_stat.st_dev, current_stat.st_ino) != (
        expected_device,
        expected_inode,
    ):
      _MANAGED_TEMP_ARTIFACTS.pop(file_path, None)
      continue
    if expires_at <= now:
      _MANAGED_TEMP_ARTIFACTS.pop(file_path, None)
      removed_artifacts.append((file_path, expected_device, expected_inode))
  return removed_artifacts


def _enforce_managed_temp_artifact_bounds_unlocked() -> (
    list[tuple[str, int, int]]
):
  """Evicts oldest files until count and byte limits are satisfied."""
  removed_paths = []
  retained_bytes = sum(
      artifact[2] for artifact in _MANAGED_TEMP_ARTIFACTS.values()
  )
  while _MANAGED_TEMP_ARTIFACTS and (
      len(_MANAGED_TEMP_ARTIFACTS) > _MANAGED_TEMP_ARTIFACT_MAX_ENTRIES
      or retained_bytes > _MANAGED_TEMP_ARTIFACT_MAX_BYTES
  ):
    file_path, artifact = _MANAGED_TEMP_ARTIFACTS.popitem(last=False)
    _, _, bytes_written, expected_device, expected_inode = artifact
    retained_bytes -= bytes_written
    removed_paths.append((file_path, expected_device, expected_inode))
  return removed_paths


def _managed_temp_artifact_reaper() -> None:
  """Runs one daemon reaper for all registered automatic artifacts."""
  global _MANAGED_TEMP_ARTIFACT_REAPER
  while True:
    removed_paths = []
    should_exit = False
    with _MANAGED_TEMP_ARTIFACT_CONDITION:
      removed_paths.extend(
          _collect_expired_managed_temp_artifacts_unlocked(time.monotonic())
      )
      if not _MANAGED_TEMP_ARTIFACTS:
        _MANAGED_TEMP_ARTIFACT_REAPER = None
        should_exit = True
      else:
        next_expiry = min(
            entry[0] for entry in _MANAGED_TEMP_ARTIFACTS.values()
        )
        wait_seconds = max(0.0, next_expiry - time.monotonic())
        if not removed_paths:
          _MANAGED_TEMP_ARTIFACT_CONDITION.wait(timeout=wait_seconds)
    _unlink_temp_artifacts(removed_paths)
    if should_exit:
      return


def _register_managed_temp_artifact(
    file_path: str,
    bytes_written: int,
) -> None:
  """Registers one automatic artifact and starts bounded cleanup."""
  global _MANAGED_TEMP_ARTIFACT_REAPER
  created_at = time.monotonic()
  expires_at = created_at + _MANAGED_TEMP_ARTIFACT_TTL_SECONDS
  expires_at_epoch = time.time() + _MANAGED_TEMP_ARTIFACT_TTL_SECONDS
  try:
    artifact_stat = os.stat(file_path, follow_symlinks=False)
  except OSError:
    _remove_export_file(file_path)
    raise
  removed_paths = []
  with _MANAGED_TEMP_ARTIFACT_CONDITION:
    removed_paths.extend(
        _collect_expired_managed_temp_artifacts_unlocked(created_at)
    )
    _MANAGED_TEMP_ARTIFACTS[file_path] = (
        expires_at,
        expires_at_epoch,
        bytes_written,
        artifact_stat.st_dev,
        artifact_stat.st_ino,
    )
    _MANAGED_TEMP_ARTIFACTS.move_to_end(file_path)
    removed_paths.extend(_enforce_managed_temp_artifact_bounds_unlocked())
    retained = file_path in _MANAGED_TEMP_ARTIFACTS
    if retained and _MANAGED_TEMP_ARTIFACT_REAPER is None:
      _MANAGED_TEMP_ARTIFACT_REAPER = threading.Thread(
          target=_managed_temp_artifact_reaper,
          name="google-ads-mcp-artifact-reaper",
          daemon=True,
      )
      _MANAGED_TEMP_ARTIFACT_REAPER.start()
    _MANAGED_TEMP_ARTIFACT_CONDITION.notify_all()
  _unlink_temp_artifacts(removed_paths)
  if not retained:
    raise ToolError(
        "Automatic result artifact exceeded the managed temporary-file byte "
        "budget and was removed. The Google Ads operation may already have "
        "completed; follow the caller's do-not-retry guidance."
    )


def managed_temp_artifact_metadata(file_path: str) -> dict[str, Any]:
  """Returns explicit expiry metadata for an automatic result artifact."""
  removed_paths = []
  with _MANAGED_TEMP_ARTIFACT_CONDITION:
    removed_paths.extend(
        _collect_expired_managed_temp_artifacts_unlocked(time.monotonic())
    )
    cache_entry = _MANAGED_TEMP_ARTIFACTS.get(file_path)
    if cache_entry is None:
      metadata = {
          "automatic_cleanup": True,
          "available": False,
          "expires_after_seconds": _MANAGED_TEMP_ARTIFACT_TTL_SECONDS,
      }
    else:
      metadata = {
          "automatic_cleanup": True,
          "expires_after_seconds": _MANAGED_TEMP_ARTIFACT_TTL_SECONDS,
          "expires_at_epoch_seconds": cache_entry[1],
          "may_be_evicted_earlier": True,
      }
  _unlink_temp_artifacts(removed_paths)
  return metadata


def remove_temp_csv_file(file_path: str) -> None:
  """Removes and unregisters a managed or unmanaged temporary CSV."""
  _remove_export_file(file_path)


def _remove_export_file(file_path: str) -> None:
  """Removes an export file if it still exists."""
  with _MANAGED_TEMP_ARTIFACT_CONDITION:
    _MANAGED_TEMP_ARTIFACTS.pop(file_path, None)
    _MANAGED_TEMP_ARTIFACT_CONDITION.notify_all()
  with contextlib.suppress(OSError):
    os.remove(file_path)


def merge_temp_csv_files(
    fragment_paths: list[str],
    columns: list[str],
) -> tuple[str, list[str], int]:
  """Merges temporary CSV fragments and removes them after the attempt."""
  with contextlib.ExitStack() as fragment_cleanup:
    for fragment_path in fragment_paths:
      fragment_cleanup.callback(_remove_export_file, fragment_path)

    output_path, output_columns, _ = _write_csv_rows([], columns=columns)
    with contextlib.ExitStack() as output_cleanup:
      output_cleanup.callback(_remove_export_file, output_path)
      with open(output_path, "a", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        for fragment_path in fragment_paths:
          with open(
              fragment_path,
              "r",
              newline="",
              encoding="utf-8",
          ) as fragment_file:
            reader = csv.reader(fragment_file)
            next(reader, None)
            writer.writerows(reader)
      output_cleanup.pop_all()
    return output_path, output_columns, os.path.getsize(output_path)


def _iter_gaql_query_attempt(
    query: str,
    customer_id: str,
    login_customer_id: str | None = None,
) -> Iterator[dict[str, Any]]:
  """Yields plain rows from one Google Ads streaming-query attempt."""
  query = preprocess_gaql(query)
  ads_client = get_ads_client(login_customer_id)
  ads_service: GoogleAdsServiceClient = ads_client.get_service(
      "GoogleAdsService"
  )
  query_res = ads_service.search_stream(
      query=query,
      customer_id=customer_id,
  )
  for batch in query_res:
    for row in batch.results:
      yield {
          field_name: format_value(get_nested_attr(row, field_name))
          for field_name in batch.field_mask.paths
      }


def _build_spooled_gaql_snapshot(
    query: str,
    customer_id: str,
    login_customer_id: str | None,
    row_sort_fields: tuple[str, ...] | None,
) -> _SpooledGaqlSnapshot:
  """Streams one retry-safe exact GAQL snapshot into an internal SQLite DB."""
  descriptor, file_path = tempfile.mkstemp(
      prefix="google_ads_mcp_snapshot_",
      suffix=".sqlite3",
  )
  os.close(descriptor)
  sort_fields = tuple(row_sort_fields or ())
  sort_definitions = ", ".join(
      f"sort_{index} TEXT" for index in range(len(sort_fields))
  )
  source_columns = "seq INTEGER PRIMARY KEY, payload TEXT, row_bytes INTEGER"
  if sort_definitions:
    source_columns = f"{source_columns}, {sort_definitions}"
  insert_columns = "payload, row_bytes"
  value_placeholders = "?, ?"
  if sort_fields:
    insert_columns += ", " + ", ".join(
        f"sort_{index}" for index in range(len(sort_fields))
    )
    value_placeholders += ", " + ", ".join("?" for _ in sort_fields)

  columns = []
  seen_columns = set()
  try:
    with sqlite3.connect(file_path) as connection:
      connection.execute("PRAGMA journal_mode = OFF")
      connection.execute("PRAGMA synchronous = OFF")
      connection.execute(f"CREATE TABLE source_rows ({source_columns})")
      connection.execute(
          "CREATE TABLE ordered_rows ("
          "position INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT, "
          "row_bytes INTEGER)"
      )
      connection.execute(
          "CREATE TABLE page_plans ("
          "page_size INTEGER, start_offset INTEGER, end_offset INTEGER, "
          "inline_bytes INTEGER, omitted_count INTEGER, "
          "byte_limited INTEGER, PRIMARY KEY (page_size, start_offset))"
      )
      for attempt in range(3):
        connection.execute("DELETE FROM source_rows")
        columns.clear()
        seen_columns.clear()
        try:
          for row in _iter_gaql_query_attempt(
              query=query,
              customer_id=customer_id,
              login_customer_id=login_customer_id,
          ):
            for column in row:
              if column not in seen_columns:
                seen_columns.add(column)
                columns.append(column)
            payload = json.dumps(
                row,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            values = [payload, len(payload.encode("utf-8"))]
            values.extend(str(row.get(field, "")) for field in sort_fields)
            connection.execute(
                f"INSERT INTO source_rows ({insert_columns}) "
                f"VALUES ({value_placeholders})",
                values,
            )
          break
        except GoogleAdsException as exc:
          if attempt == 2 or not _is_retryable_google_ads_error(exc):
            raise ToolError(_format_google_ads_error(exc)) from exc
          connection.rollback()
          time.sleep(2**attempt)

      order_clause = "seq"
      if sort_fields:
        order_clause = ", ".join(
            f"sort_{index} COLLATE BINARY" for index in range(len(sort_fields))
        )
        order_clause += ", seq"
      connection.execute(
          "INSERT INTO ordered_rows (payload, row_bytes) "
          f"SELECT payload, row_bytes FROM source_rows ORDER BY {order_clause}"
      )
      row_count = connection.execute(
          "SELECT COUNT(*) FROM ordered_rows"
      ).fetchone()[0]
      connection.execute("DROP TABLE source_rows")
      connection.commit()
    return _SpooledGaqlSnapshot(file_path, row_count, tuple(columns))
  except BaseException:
    with contextlib.suppress(OSError):
      os.remove(file_path)
    raise


def run_gaql_query(
    query: str,
    customer_id: str,
    login_customer_id: str | None = None,
) -> list[dict[str, Any]]:
  """Executes a GAQL query and returns formatted rows."""
  for attempt in range(3):
    try:
      return list(
          _iter_gaql_query_attempt(
              query=query,
              customer_id=customer_id,
              login_customer_id=login_customer_id,
          )
      )
    except GoogleAdsException as e:
      if attempt == 2 or not _is_retryable_google_ads_error(e):
        raise ToolError(_format_google_ads_error(e)) from e
      time.sleep(2**attempt)

  return []


def run_gaql_query_snapshot(
    query: str,
    customer_id: str,
    login_customer_id: str | None = None,
    row_sort_fields: tuple[str, ...] | None = None,
) -> dict[str, Any]:
  """Materializes one immutable GAQL snapshot for complete local analysis."""
  query_key = _page_cache_key(
      query,
      customer_id,
      login_customer_id,
      row_sort_fields,
  )
  snapshot_id, snapshot, owned_build = _get_or_build_page_snapshot(
      query_key,
      query,
      customer_id,
      login_customer_id,
      row_sort_fields,
  )
  try:
    snapshot_rows = _SpooledRows(snapshot)
  finally:
    if owned_build is not None:
      with _PAGED_QUERY_CACHE_LOCK:
        if _PAGED_QUERY_BUILDS.get(query_key) is owned_build:
          _PAGED_QUERY_BUILDS.pop(query_key, None)
  return {
      "rows": snapshot_rows,
      "total_results_count": snapshot.row_count,
      "snapshot_token": _encode_snapshot_token(snapshot_id),
  }


def run_gaql_query_page(
    query: str,
    customer_id: str,
    page_size: int | None,
    page_token: str | None = None,
    login_customer_id: str | None = None,
    row_sort_fields: tuple[str, ...] | None = None,
) -> dict[str, Any]:
  """Executes a GAQL query and slices the results into stable pages.

  Google Ads Search uses a fixed page size for some resources, so this helper
  provides a consistent cursor contract for MCP tools by applying client-side
  paging over the full result set. Inline pages are capped independently from
  full-data access. Continuation and export tokens refer to the same immutable
  result snapshot. Tokens can expire after the cache TTL or normal bounded-LRU
  eviction; callers receive a restart hint rather than rows from a different
  result snapshot.
  """
  requested_page_size = page_size
  page_size = applied_inline_page_size(requested_page_size)

  requested_snapshot_id, offset, bound_page_size = _decode_page_token(
      page_token
  )
  if bound_page_size is not None and bound_page_size != page_size:
    raise ToolError(
        f"page_token is bound to page_size={bound_page_size}. Continue with "
        "the same limit/page_size used for the first page, or restart without "
        "page_token."
    )
  query_key = _page_cache_key(
      query,
      customer_id,
      login_customer_id,
      row_sort_fields,
  )
  owned_build = None
  if requested_snapshot_id:
    with _PAGED_QUERY_CACHE_LOCK:
      snapshot = _get_page_snapshot_unlocked(
          query_key,
          requested_snapshot_id,
      )
      query_has_other_snapshots = query_key in _PAGED_QUERY_LATEST or any(
          _snapshot_query_key(snapshot_key) == query_key
          for snapshot_key in _PAGED_QUERY_CACHE
      )
    if snapshot is None and query_has_other_snapshots:
      raise ToolError(
          "page_token belongs to an expired result snapshot. Restart "
          "pagination by calling the same tool without page_token."
      )
    if snapshot is None:
      raise ToolError(
          "page_token expired or its result snapshot was evicted. Restart "
          "pagination by calling the same tool without page_token."
      )
    snapshot_id = requested_snapshot_id
    if offset <= 0 or offset >= snapshot.row_count:
      raise ToolError("Invalid page_token.")
  else:
    snapshot_id, snapshot, owned_build = _get_or_build_page_snapshot(
        query_key,
        query,
        customer_id,
        login_customer_id,
        row_sort_fields,
    )
  try:
    planned_page = snapshot.page(page_size, offset)
    if planned_page is None:
      raise ToolError(
          "page_token does not identify a valid page boundary for this "
          "snapshot. Restart without page_token."
      )
    next_offset = planned_page["end_offset"]
    page_rows = deepcopy(planned_page["rows"])

    next_page_token = None
    if next_offset < snapshot.row_count:
      next_page_token = f"{snapshot_id}:{next_offset}:{page_size}"
    byte_limited = planned_page["byte_limited_pagination"]
    snapshot_token = _encode_snapshot_token(
        snapshot_id,
        page_size=page_size,
        total_page_count=planned_page["total_page_count"],
        byte_limited=byte_limited,
    )
    with _PAGED_QUERY_CACHE_LOCK:
      if next_page_token is not None:
        _publish_page_snapshot_unlocked(
            query_key,
            snapshot_id,
            snapshot,
            make_latest_if_missing=True,
        )
    return {
        "rows": page_rows,
        "next_page_token": next_page_token,
        "total_results_count": snapshot.row_count,
        "snapshot_token": snapshot_token,
        "requested_page_size": requested_page_size,
        "page_size": page_size,
        "page_size_clamped": requested_page_size != page_size,
        "total_page_count": planned_page["total_page_count"],
        "inline_bytes": planned_page["inline_bytes"],
        "inline_byte_limit": _MAX_INLINE_PAGE_BYTES,
        "inline_omitted_row_count": planned_page["inline_omitted_row_count"],
        "page_size_reduced_by_bytes": planned_page["byte_limited"],
        "byte_limited_pagination": byte_limited,
    }
  finally:
    if owned_build is not None:
      with _PAGED_QUERY_CACHE_LOCK:
        if _PAGED_QUERY_BUILDS.get(query_key) is owned_build:
          _PAGED_QUERY_BUILDS.pop(query_key, None)


def build_paginated_list_response(
    item_key: str,
    rows: list[dict[str, Any]],
    total_count: int,
    page_size: int | None,
    next_page_token: str | None,
    snapshot_token: str | None = None,
) -> dict[str, Any]:
  """Builds a consistent paginated list response envelope."""
  requested_page_size = page_size
  page_size = applied_inline_page_size(requested_page_size)
  inline_omitted_row_count = sum(is_inline_omission(row) for row in rows)
  returned_count = len(rows) - inline_omitted_row_count
  complete_inline = (
      inline_omitted_row_count == 0 and total_count == returned_count
  )
  planned_page_size = None
  planned_total_page_count = None
  byte_limited_pagination = False
  if snapshot_token is not None:
    (
        planned_page_size,
        planned_total_page_count,
        byte_limited_pagination,
    ) = _snapshot_delivery_metadata(snapshot_token)
  if planned_page_size is not None and planned_page_size != page_size:
    raise ToolError(
        "snapshot_token delivery metadata does not match page_size. Restart "
        "pagination without page_token."
    )
  result = {
      item_key: rows,
      "returned_count": returned_count,
      "total_count": total_count,
      "total_page_count": (
          planned_total_page_count
          if planned_total_page_count is not None
          else math.ceil(total_count / page_size)
          if total_count
          else 0
      ),
      "truncated": (
          next_page_token is not None or inline_omitted_row_count > 0
      ),
      "has_more": next_page_token is not None,
      "complete_inline": complete_inline,
      "next_page_token": next_page_token,
      "page_size": page_size,
      "requested_page_size": requested_page_size,
      "page_size_clamped": (
          requested_page_size is None or requested_page_size != page_size
      ),
  }
  if byte_limited_pagination or inline_omitted_row_count:
    result.update(
        {
            "inline_bytes": _serialized_json_bytes(rows),
            "inline_byte_limit": _MAX_INLINE_PAGE_BYTES,
            "byte_limited_pagination": byte_limited_pagination,
            "page_size_reduced_by_bytes": (
                inline_omitted_row_count > 0
                or (next_page_token is not None and len(rows) < page_size)
            ),
        }
    )
  if inline_omitted_row_count:
    result["inline_omitted_row_count"] = inline_omitted_row_count
    result["represented_row_count"] = len(rows)
    result["inline_omission_note"] = (
        "One or more individual rows exceeded the inline byte budget and are "
        "represented by explicit placeholders. Use bulk_export_call for the "
        "complete original rows."
    )
  if snapshot_token is None and next_page_token is not None:
    page_token_match = re.fullmatch(
        r"([0-9a-f]{32}):[0-9]+(?::[0-9]+)?",
        next_page_token,
    )
    if page_token_match:
      snapshot_token = _encode_snapshot_token(page_token_match.group(1))
  if snapshot_token is not None:
    result["bulk_export_call"] = {
        "tool": "export_gaql_csv",
        "arguments": {"snapshot_token": snapshot_token},
    }
  return result


def _unbounded_gaql_response(
    rows: list[dict[str, Any]],
    warning_row_threshold: int | None,
) -> dict[str, Any]:
  """Builds an untruncated execute_gaql response with optional warning."""
  result: dict[str, Any] = {"data": rows}
  if warning_row_threshold is None or len(rows) <= warning_row_threshold:
    return result

  result.update(
      {
          "returned_row_count": len(rows),
          "total_row_count": len(rows),
          "truncated": False,
          "warning_row_threshold": warning_row_threshold,
          "token_efficiency_warning": (
              "Unbounded execute_gaql returned "
              f"{len(rows)} rows. For token efficiency, rerun with max_rows, "
              "use a paginated dedicated tool, or call export_gaql_csv for "
              "bulk extracts."
          ),
      }
  )
  return result


@ads_read_tool(
    mcp,
    tags={"gaql", "reporting"},
    output_schema=_EXECUTE_GAQL_OUTPUT_SCHEMA,
)
def execute_gaql(
    query: str,
    customer_id: str,
    max_rows: int | None = None,
    max_results: int | None = None,
    warning_row_threshold: int | None = (
        _DEFAULT_EXECUTE_GAQL_WARNING_ROW_THRESHOLD
    ),
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Executes a GAQL query to get reporting data.

  Prefer dedicated visible tools first. Use search_tools only when the right
  tool is unclear, then use get_tool_guide, get_gaql_doc, and
  get_reporting_view_doc when a custom GAQL query is needed. Set max_rows to
  cap large result sets without changing the underlying GAQL query.
  max_results is accepted as an alias for max_rows. Unbounded calls are not
  truncated, but responses above warning_row_threshold include token-efficiency
  metadata that recommends max_rows, dedicated paginated tools, or
  export_gaql_csv.
  """
  _validate_optional_positive_int(max_rows, "max_rows")
  _validate_optional_positive_int(max_results, "max_results")
  _validate_optional_positive_int(
      warning_row_threshold,
      "warning_row_threshold",
  )
  if (
      max_rows is not None
      and max_results is not None
      and max_rows != max_results
  ):
    raise ToolError("Use only one of max_rows or max_results.")
  if max_rows is None:
    max_rows = max_results

  rows = run_gaql_query(
      query=query,
      customer_id=customer_id,
      login_customer_id=login_customer_id,
  )
  if max_rows is None:
    return _unbounded_gaql_response(rows, warning_row_threshold)

  returned_rows = rows[:max_rows]
  return {
      "data": returned_rows,
      "returned_row_count": len(returned_rows),
      "total_row_count": len(rows),
      "truncated": len(rows) > max_rows,
      "max_rows_applied": max_rows,
  }


@local_write_tool(
    mcp,
    tags={"gaql", "reporting", "export"},
    output_schema=_EXPORT_GAQL_CSV_OUTPUT_SCHEMA,
)
def export_materialized_response_csv(
    snapshot_token: str,
    output_path: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
  """Writes an oversized read response captured by a bounded read tool.

  This explicit local-write step preserves the no-side-effects contract of
  read-only Google Ads tools. Pass the opaque snapshot_token exactly as shown
  in a read response's full_materialized_response_export.export_call.

  Args:
      snapshot_token: Credential-scoped token from a bounded read response.
      output_path: Optional destination inside GOOGLE_ADS_MCP_EXPORT_DIR.
      overwrite: Whether to replace an existing explicit output path.

  Returns:
      The CSV path, row count, columns, and bytes written.
  """
  rows = _get_materialized_snapshot_rows(snapshot_token)
  resolved_output_path = _resolve_export_path(output_path, overwrite)
  file_path, columns, bytes_written = _write_csv_rows(
      rows,
      resolved_output_path,
      overwrite,
      columns=["result_type", "result_index", "result"],
  )
  return {
      "file_path": file_path,
      "row_count": len(rows),
      "columns": columns,
      "bytes_written": bytes_written,
      "truncated": False,
  }


@local_write_tool(
    mcp,
    tags={"gaql", "reporting", "export"},
    output_schema=_EXPORT_GAQL_CSV_OUTPUT_SCHEMA,
)
def export_gaql_csv(
    query: str | None = None,
    customer_id: str | None = None,
    output_path: str | None = None,
    overwrite: bool = False,
    max_rows: int | None = None,
    login_customer_id: str | None = None,
    snapshot_token: str | None = None,
) -> dict[str, Any]:
  """Exports GAQL query results to a CSV file for bulk extraction.

  Prefer this over execute_gaql when the goal is loading a large result set
  into another system or reading the data outside the model context. Pass the
  opaque snapshot_token from a paginated tool's bulk_export_call for an exact,
  full export of the same result snapshot without reconstructing or rerunning
  its GAQL.

  Args:
      query: GAQL query to export. Required unless snapshot_token is provided.
      customer_id: Google Ads customer ID. Required unless snapshot_token is
          provided.
      output_path: Optional destination path inside GOOGLE_ADS_MCP_EXPORT_DIR.
        Defaults to a uniquely named CSV file in the system temp directory.
      overwrite: Whether to replace an existing explicit output path.
      max_rows: Optional row cap for partial query exports. Not accepted with
          snapshot_token because snapshot exports are always complete.
      login_customer_id: Optional manager account ID.
      snapshot_token: Opaque credential-scoped token from bulk_export_call.

  Returns:
      A dict with the CSV path and export metadata.
  """
  _validate_optional_positive_int(max_rows, "max_rows")
  resolved_output_path = _resolve_export_path(output_path, overwrite)

  if snapshot_token is not None:
    if any(
        value is not None
        for value in (query, customer_id, max_rows, login_customer_id)
    ):
      raise ToolError(
          "Use snapshot_token by itself; query, customer_id, max_rows, and "
          "login_customer_id are not accepted for exact snapshot exports."
      )
    rows = _get_export_snapshot_rows(snapshot_token)
  else:
    if query is None or customer_id is None:
      raise ToolError(
          "query and customer_id are required unless snapshot_token is "
          "provided."
      )
    rows = run_gaql_query(
        query=query,
        customer_id=customer_id,
        login_customer_id=login_customer_id,
    )
  exported_rows = rows if max_rows is None else rows[:max_rows]
  file_path, columns, bytes_written = _write_csv_rows(
      exported_rows, resolved_output_path, overwrite
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
