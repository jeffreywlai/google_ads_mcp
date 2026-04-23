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

"""Internal helpers for building GAQL-based tools."""

import re

from fastmcp.exceptions import ToolError

from ads_mcp.tools.api import gaql_quote_string


def validate_limit(limit: int) -> None:
  """Validates that a tool limit is positive."""
  if limit <= 0:
    raise ToolError("limit must be greater than 0.")


def quote_int_values(values: list[str]) -> str:
  """Formats integer-like values for an IN clause."""
  return ", ".join(str(int(value)) for value in values)


def quote_string_values(values: list[str]) -> str:
  """Formats string values for an IN clause."""
  return ", ".join(gaql_quote_string(value) for value in values)


def quote_enum_values(values: list[str]) -> str:
  """Formats enum names for an IN clause."""
  quoted_values = []
  for value in values:
    if not isinstance(value, str):
      raise ToolError("enum values must be strings.")
    normalized_value = value.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized_value):
      raise ToolError(
          f"Invalid enum value: {value}. Use Google Ads enum names."
      )
    quoted_values.append(normalized_value)
  return ", ".join(quoted_values)


def build_where_clause(conditions: list[str]) -> str:
  """Builds a WHERE clause from already-sanitized conditions."""
  if not conditions:
    return ""
  return " WHERE " + " AND ".join(conditions)
