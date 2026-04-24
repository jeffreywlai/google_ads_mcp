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

from datetime import date
from datetime import timedelta
import functools
import json
import math
from pathlib import Path
import re
from typing import Any

from fastmcp.exceptions import ToolError
import yaml


NATIVE_DATE_RANGE_FUNCTIONS = {
    "LAST_14_DAYS",
    "LAST_30_DAYS",
    "LAST_7_DAYS",
    "LAST_BUSINESS_WEEK",
    "LAST_MONTH",
    "LAST_WEEK_MON_SUN",
    "LAST_WEEK_SUN_SAT",
    "THIS_MONTH",
    "THIS_WEEK_MON_TODAY",
    "THIS_WEEK_SUN_TODAY",
    "TODAY",
    "YESTERDAY",
}
EXTENDED_DATE_RANGE_FUNCTIONS = {
    "ALL_TIME",
    "LAST_12_MONTHS",
    "LAST_90_DAYS",
    "LAST_180_DAYS",
    "LAST_365_DAYS",
    "LAST_QUARTER",
    "YTD",
}
DATE_RANGE_FUNCTIONS = NATIVE_DATE_RANGE_FUNCTIONS
ACCEPTED_DATE_RANGE_FUNCTIONS = (
    NATIVE_DATE_RANGE_FUNCTIONS | EXTENDED_DATE_RANGE_FUNCTIONS
)
_MAX_EXTENDED_LAST_N_DAYS = 3650
_DATE_FIELD_PATTERN = re.compile(
    r"\b(?P<field>[a-z][a-z0-9_]*(?:\.[a-zA-Z0-9_]+)+)"
    r"\s+DURING\s+(?P<literal>"
    r"LAST[\s_-]+\d+[\s_-]+DAYS|"
    r"LAST[\s_-]+12[\s_-]+MONTHS|"
    r"LAST[\s_-]+QUARTER|"
    r"ALL[\s_-]+TIME|"
    r"[A-Za-z0-9_]+"
    r")\b",
    re.IGNORECASE,
)
_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_CORE_DATE_SEGMENTS = {
    "segments.date",
    "segments.week",
    "segments.month",
    "segments.quarter",
    "segments.year",
}
_AGGREGATE_PATTERN = re.compile(
    r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(|\bGROUP\s+BY\b",
    re.IGNORECASE,
)
_OR_PATTERN = re.compile(r"\bOR\b", re.IGNORECASE)
_SELECT_FROM_PATTERN = re.compile(
    r"\bSELECT\b(?P<select>.*?)\bFROM\b",
    re.IGNORECASE | re.DOTALL,
)
_FROM_PATTERN = re.compile(
    r"\bFROM\s+(?P<resource>[a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)
_WHERE_PATTERN = re.compile(
    r"\bWHERE\b(?P<body>.*?)(?=\bORDER\s+BY\b|\bLIMIT\b|\bPARAMETERS\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_ORDER_BY_PATTERN = re.compile(
    r"\bORDER\s+BY\b(?P<body>.*?)(?=\bLIMIT\b|\bPARAMETERS\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_FIELD_TOKEN_PATTERN = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-zA-Z0-9_]+)+\b")
_STRING_LITERAL_PATTERN = re.compile(
    r"'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\"",
    re.DOTALL,
)
_PARAMETERS_PATTERN = re.compile(r"\bPARAMETERS\b", re.IGNORECASE)
_OMIT_UNSELECTED_PATTERN = re.compile(
    r"\bomit_unselected_resource_names\b",
    re.IGNORECASE,
)
_ENUM_FILTER_PATTERN = re.compile(
    r"\b(?P<field>[a-z][a-z0-9_]*(?:\.[a-zA-Z0-9_]+)+)"
    r"\s*(?P<operator>"
    r"CONTAINS\s+ANY|CONTAINS\s+ALL|CONTAINS\s+NONE|"
    r"NOT\s+IN|IN|!=|<>|="
    r")\s*"
    r"(?P<value>"
    r"\([^)]*\)|"
    r"'(?:\\.|[^'])*'|"
    r"\"(?:\\.|[^\"])*\"|"
    r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)?"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_ENUM_LITERAL_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)?"
)
_FIELDS_METADATA_PATH = (
    Path(__file__).resolve().parents[1] / "context" / "fields.yaml"
)
_VIEWS_METADATA_DIR = Path(__file__).resolve().parents[1] / "context" / "views"
_UNIQUE_USER_METRICS = {
    "metrics.unique_users",
    "metrics.unique_users_two_plus",
    "metrics.unique_users_three_plus",
    "metrics.unique_users_four_plus",
    "metrics.unique_users_five_plus",
    "metrics.unique_users_ten_plus",
}
_ALLOWED_UNIQUE_USER_SEGMENTS = {
    "segments.adjusted_age_range",
    "segments.adjusted_gender",
    "segments.date",
    "segments.device",
}


def gaql_quote_string(value: str) -> str:
  """Escapes a string literal for use in a GAQL query."""
  escaped_value = value.replace("\\", "\\\\").replace("'", "\\'")
  return f"'{escaped_value}'"


def gaql_like_substring_pattern(value: str) -> str:
  """Escapes a literal substring for use inside a GAQL LIKE pattern."""
  escape_map = {
      "[": "[[]",
      "]": "[]]",
      "%": "[%]",
      "_": "[_]",
  }
  escaped_value = "".join(escape_map.get(char, char) for char in value)
  return f"%{escaped_value}%"


def validate_limit(limit: int) -> None:
  """Validates that a tool limit is positive."""
  if isinstance(limit, bool) or not isinstance(limit, int):
    raise ToolError("limit must be an integer.")
  if limit <= 0:
    raise ToolError("limit must be greater than 0.")


def validate_non_negative_number(value: int | float, field_name: str) -> None:
  """Validates non-negative numeric threshold inputs."""
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ToolError(f"{field_name} must be a number.")
  if not math.isfinite(value):
    raise ToolError(f"{field_name} must be finite.")
  if value < 0:
    raise ToolError(f"{field_name} must be non-negative.")


def require_unique_values(values: list[str], field_name: str) -> list[str]:
  """Rejects duplicate normalized values while preserving input order."""
  seen = set()
  duplicates = []
  for value in values:
    if value in seen and value not in duplicates:
      duplicates.append(value)
    seen.add(value)
  if duplicates:
    raise ToolError(
        f"{field_name} must not contain duplicates: " + ", ".join(duplicates)
    )
  return values


def validate_date_range(date_range: str) -> str:
  """Validates and normalizes a GAQL DURING date range function."""
  normalized_date_range = normalize_date_range_literal(date_range)
  if normalized_date_range not in NATIVE_DATE_RANGE_FUNCTIONS:
    allowed_values = ", ".join(sorted(NATIVE_DATE_RANGE_FUNCTIONS))
    raise ToolError(
        f"Invalid date_range: {date_range}. Use one of: {allowed_values}."
    )
  return normalized_date_range


def normalize_date_range_literal(date_range: str) -> str:
  """Normalizes user-facing date range strings to enum-like literals."""
  if not isinstance(date_range, str):
    raise ToolError("date_range must be a string.")
  normalized_date_range = re.sub(r"[\s-]+", "_", date_range.strip().upper())
  if not re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized_date_range):
    raise ToolError(
        f"Invalid date_range: {date_range}. Use a supported date range."
    )
  return normalized_date_range


def normalize_list_arg(value: Any, field_name: str) -> list[Any]:
  """Normalizes list args, accepting JSON-stringified arrays from LLMs."""
  if value is None:
    return []
  if isinstance(value, str):
    stripped_value = value.strip()
    if not stripped_value:
      return []
    if stripped_value.startswith("["):
      try:
        decoded_value = json.loads(stripped_value)
      except json.JSONDecodeError as exc:
        raise ToolError(
            f"{field_name} must be an array, for example ['123']; got "
            f"invalid JSON string {value!r}."
        ) from exc
      if not isinstance(decoded_value, list):
        raise ToolError(
            f"{field_name} must decode to an array; got {value!r}."
        )
      return decoded_value
    if "," in stripped_value:
      return [
          part.strip() for part in stripped_value.split(",") if part.strip()
      ]
    return [stripped_value]
  if isinstance(value, (list, tuple, set)):
    return list(value)
  raise ToolError(
      f"{field_name} must be an array of strings, for example ['123']; "
      f"got {type(value).__name__}."
  )


def merge_single_and_list_arg(
    single_value: str | None,
    list_value: list[str] | str | None,
    field_name: str,
) -> list[str] | None:
  """Merges a singular ID-style arg with its list/string-list companion."""
  value_list = normalize_list_arg(list_value, field_name)
  if single_value:
    return [single_value, *value_list]
  return value_list or None


def _parse_iso_date(value: Any, field_name: str) -> date:
  if not isinstance(value, str) or not _ISO_DATE_PATTERN.fullmatch(value):
    raise ToolError(f"{field_name} must be a YYYY-MM-DD date string.")
  try:
    return date.fromisoformat(value)
  except ValueError as exc:
    raise ToolError(f"{field_name} must be a valid YYYY-MM-DD date.") from exc


def _add_years(day: date, years: int) -> date:
  try:
    return day.replace(year=day.year + years)
  except ValueError:
    return day.replace(month=2, day=28, year=day.year + years)


def _last_quarter_bounds(today: date) -> tuple[date, date]:
  current_quarter = (today.month - 1) // 3
  last_quarter = current_quarter - 1
  year = today.year
  if last_quarter < 0:
    last_quarter = 3
    year -= 1
  start_month = last_quarter * 3 + 1
  start_date = date(year, start_month, 1)
  if start_month == 10:
    next_quarter_start = date(year + 1, 1, 1)
  else:
    next_quarter_start = date(year, start_month + 3, 1)
  return start_date, next_quarter_start - timedelta(days=1)


def _month_start(day: date) -> date:
  return date(day.year, day.month, 1)


def _last_month_bounds(today: date) -> tuple[date, date]:
  this_month_start = _month_start(today)
  last_month_end = this_month_start - timedelta(days=1)
  return _month_start(last_month_end), last_month_end


def _this_week_bounds(today: date, week_start: int) -> tuple[date, date]:
  days_since_start = (today.weekday() - week_start) % 7
  return today - timedelta(days=days_since_start), today


def _last_week_bounds(today: date, week_start: int) -> tuple[date, date]:
  this_week_start, _ = _this_week_bounds(today, week_start)
  last_week_end = this_week_start - timedelta(days=1)
  return last_week_end - timedelta(days=6), last_week_end


def _literal_date_bounds(
    date_range: str,
    today: date | None = None,
) -> tuple[date, date] | None:
  today = today or date.today()
  yesterday = today - timedelta(days=1)
  if date_range == "TODAY":
    return today, today
  if date_range == "YESTERDAY":
    return yesterday, yesterday
  match = re.fullmatch(r"LAST_(\d+)_DAYS", date_range)
  if match:
    days = int(match.group(1))
    if days <= 0:
      raise ToolError("LAST_N_DAYS date ranges must be greater than 0.")
    if days > _MAX_EXTENDED_LAST_N_DAYS:
      raise ToolError(
          "LAST_N_DAYS date ranges must be "
          f"{_MAX_EXTENDED_LAST_N_DAYS} days or fewer."
      )
    return today - timedelta(days=days), yesterday
  if date_range == "THIS_MONTH":
    return _month_start(today), today
  if date_range == "LAST_MONTH":
    return _last_month_bounds(today)
  if date_range == "LAST_12_MONTHS":
    return _add_years(today, -1), yesterday
  if date_range == "THIS_WEEK_MON_TODAY":
    return _this_week_bounds(today, 0)
  if date_range == "THIS_WEEK_SUN_TODAY":
    return _this_week_bounds(today, 6)
  if date_range == "LAST_WEEK_MON_SUN":
    return _last_week_bounds(today, 0)
  if date_range == "LAST_WEEK_SUN_SAT":
    return _last_week_bounds(today, 6)
  if date_range == "LAST_BUSINESS_WEEK":
    last_week_start, _ = _last_week_bounds(today, 0)
    return last_week_start, last_week_start + timedelta(days=4)
  if date_range == "LAST_QUARTER":
    return _last_quarter_bounds(today)
  if date_range == "YTD":
    return date(today.year, 1, 1), today
  if date_range == "ALL_TIME":
    return None
  raise ToolError(
      f"Invalid date_range: {date_range}. Use one of: "
      + ", ".join(sorted(ACCEPTED_DATE_RANGE_FUNCTIONS))
      + " or {'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD'}."
  )


def _explicit_date_bounds(date_range: dict[str, str]) -> tuple[date, date]:
  invalid_keys = sorted(set(date_range) - {"start_date", "end_date"})
  if invalid_keys:
    raise ToolError(
        "date_range object only supports start_date and end_date; got "
        + ", ".join(invalid_keys)
        + "."
    )
  start_date = _parse_iso_date(date_range.get("start_date"), "start_date")
  end_date = _parse_iso_date(date_range.get("end_date"), "end_date")
  if start_date > end_date:
    raise ToolError("start_date must be on or before end_date.")
  return start_date, end_date


def date_range_label(
    date_range: str | dict[str, str],
) -> str | dict[str, str]:
  """Returns a normalized user-facing date range label."""
  if isinstance(date_range, dict):
    return dict(date_range)
  return normalize_date_range_literal(date_range)


def date_range_bounds(
    date_range: str | dict[str, str],
) -> tuple[str, str] | None:
  """Returns explicit YYYY-MM-DD bounds when a date range can be resolved."""
  if isinstance(date_range, dict):
    start_date, end_date = _explicit_date_bounds(date_range)
    return start_date.isoformat(), end_date.isoformat()

  date_bounds = _literal_date_bounds(normalize_date_range_literal(date_range))
  if date_bounds is None:
    return None
  start_date, end_date = date_bounds
  return start_date.isoformat(), end_date.isoformat()


def _between_date_condition(
    field_name: str,
    start_date: date,
    end_date: date,
) -> str:
  return (
      f"{field_name} BETWEEN {gaql_quote_string(start_date.isoformat())} "
      f"AND {gaql_quote_string(end_date.isoformat())}"
  )


def build_date_range_condition(
    field_name: str,
    date_range: str | dict[str, str],
) -> str:
  """Builds a GAQL date filter, rewriting unsupported ranges to BETWEEN."""
  if isinstance(date_range, dict):
    start_date, end_date = _explicit_date_bounds(date_range)
    return _between_date_condition(field_name, start_date, end_date)

  normalized_date_range = normalize_date_range_literal(date_range)
  if normalized_date_range in NATIVE_DATE_RANGE_FUNCTIONS:
    return f"{field_name} DURING {normalized_date_range}"

  date_bounds = _literal_date_bounds(normalized_date_range)
  if date_bounds is None:
    return ""
  start_date, end_date = date_bounds
  return _between_date_condition(field_name, start_date, end_date)


def segments_date_condition(date_range: str | dict[str, str]) -> str:
  """Builds the standard segments.date condition used by reporting tools."""
  return build_date_range_condition("segments.date", date_range)


def rewrite_gaql_date_ranges(query: str) -> str:
  """Rewrites unsupported GAQL DURING literals to explicit BETWEEN dates."""

  def _replace(match: re.Match[str]) -> str:
    field_name = match.group("field")
    literal = normalize_date_range_literal(match.group("literal"))
    if literal in NATIVE_DATE_RANGE_FUNCTIONS:
      return f"{field_name} DURING {literal}"
    condition = build_date_range_condition(field_name, literal)
    if not condition:
      raise ToolError(
          f"{literal} cannot be used with DURING in GAQL. Remove the date "
          "predicate when you need all-time results."
      )
    return condition

  return _sub_outside_string_literals(query, _DATE_FIELD_PATTERN, _replace)


def _query_without_string_literals(query: str) -> str:
  """Returns query text with quoted string literals blanked out."""
  return _STRING_LITERAL_PATTERN.sub("''", query)


def _blank_string_literals(query: str) -> str:
  """Blanks string literals while preserving query character offsets."""
  return _STRING_LITERAL_PATTERN.sub(
      lambda match: " " * (match.end() - match.start()),
      query,
  )


def _outside_string_literal_parts(query: str) -> list[str]:
  """Returns query chunks that are outside quoted string literals."""
  parts = []
  start = 0
  for match in _STRING_LITERAL_PATTERN.finditer(query):
    parts.append(query[start : match.start()])
    start = match.end()
  parts.append(query[start:])
  return parts


def _contains_outside_string_literals(
    query: str,
    pattern: re.Pattern[str],
) -> bool:
  """Returns whether pattern appears outside quoted string literals."""
  return any(
      pattern.search(part) for part in _outside_string_literal_parts(query)
  )


def _sub_outside_string_literals(
    query: str,
    pattern: re.Pattern[str],
    replacement: Any,
) -> str:
  """Applies a regex substitution only outside quoted string literals."""
  parts = []
  start = 0
  for match in _STRING_LITERAL_PATTERN.finditer(query):
    parts.append(pattern.sub(replacement, query[start : match.start()]))
    parts.append(query[match.start() : match.end()])
    start = match.end()
  parts.append(pattern.sub(replacement, query[start:]))
  return "".join(parts)


def _clause_body_span(
    query: str,
    clause_keyword: str,
    end_pattern: re.Pattern[str],
) -> tuple[int, int] | None:
  """Returns an original-query clause body span, ignoring quoted keywords."""
  blanked_query = _blank_string_literals(query)
  clause_match = re.search(rf"\b{clause_keyword}\b", blanked_query, re.I)
  if not clause_match:
    return None
  body_start = clause_match.end()
  end_match = end_pattern.search(blanked_query, body_start)
  body_end = end_match.start() if end_match else len(query)
  return body_start, body_end


def _where_body_span(query: str) -> tuple[int, int] | None:
  return _clause_body_span(
      query,
      "WHERE",
      re.compile(r"\bORDER\s+BY\b|\bLIMIT\b|\bPARAMETERS\b", re.I),
  )


def _order_by_body_span(query: str) -> tuple[int, int] | None:
  return _clause_body_span(
      query,
      "ORDER\\s+BY",
      re.compile(r"\bLIMIT\b|\bPARAMETERS\b", re.I),
  )


@functools.lru_cache(maxsize=1)
def _load_enum_field_values() -> dict[str, tuple[str, ...]]:
  """Loads field-level enum values from generated v24 field metadata."""
  try:
    with open(_FIELDS_METADATA_PATH, "r", encoding="utf-8") as fields_file:
      fields = yaml.safe_load(fields_file) or {}
  except FileNotFoundError:
    return {}

  enum_fields = {}
  for field_name, metadata in fields.items():
    if not isinstance(metadata, dict) or metadata.get("data_type") != "ENUM":
      continue
    enum_values = metadata.get("enum_values")
    if not isinstance(enum_values, str) or not enum_values:
      continue
    enum_fields[field_name.lower()] = tuple(
        value.strip() for value in enum_values.split(",") if value.strip()
    )
  return enum_fields


def _split_gaql_list_items(list_body: str) -> list[str]:
  """Splits a GAQL parenthesized list body into top-level item strings."""
  items = []
  start = 0
  quote_char = ""
  escaped = False
  for index, char in enumerate(list_body):
    if quote_char:
      if escaped:
        escaped = False
      elif char == "\\":
        escaped = True
      elif char == quote_char:
        quote_char = ""
      continue
    if char in ("'", '"'):
      quote_char = char
      continue
    if char == ",":
      items.append(list_body[start:index].strip())
      start = index + 1
  items.append(list_body[start:].strip())
  return [item for item in items if item]


def _unquote_gaql_string(value: str) -> str:
  """Returns an unquoted GAQL string literal or the original value."""
  value = value.strip()
  if len(value) < 2 or value[0] not in ("'", '"') or value[-1] != value[0]:
    return value
  quote_char = value[0]
  unescaped = value[1:-1].replace(f"\\{quote_char}", quote_char)
  return unescaped.replace("\\\\", "\\")


def _enum_allowed_values_text(allowed_values: tuple[str, ...]) -> str:
  """Formats allowed enum values without flooding ToolError responses."""
  preview_values = allowed_values[:40]
  allowed_text = ", ".join(preview_values)
  if len(allowed_values) > len(preview_values):
    allowed_text += f", ... ({len(allowed_values)} total)"
  return allowed_text


def _canonical_enum_literal(
    field_name: str,
    value: str,
    allowed_values: tuple[str, ...],
) -> str:
  """Validates and canonicalizes one enum literal for a GAQL field."""
  literal = _unquote_gaql_string(value).strip()
  enum_name = literal.split(".")[-1].upper()
  if (
      not _ENUM_LITERAL_PATTERN.fullmatch(literal)
      or enum_name not in allowed_values
  ):
    raise ToolError(
        f"Invalid enum literal {value!r} for {field_name}. Use one of: "
        f"{_enum_allowed_values_text(allowed_values)}."
    )
  return enum_name


def _canonical_enum_filter_value(
    field_name: str,
    operator: str,
    value: str,
    allowed_values: tuple[str, ...],
) -> str:
  """Canonicalizes one scalar enum filter or IN-list value."""
  normalized_operator = " ".join(operator.upper().split())
  if normalized_operator.endswith("IN") or normalized_operator.startswith(
      "CONTAINS "
  ):
    stripped_value = value.strip()
    if not stripped_value.startswith("(") or not stripped_value.endswith(")"):
      raise ToolError(
          f"{field_name} {operator} must use a parenthesized enum list."
      )
    items = _split_gaql_list_items(stripped_value[1:-1])
    if not items:
      raise ToolError(f"{field_name} {operator} enum list cannot be empty.")
    canonical_items = [
        _canonical_enum_literal(field_name, item, allowed_values)
        for item in items
    ]
    canonical_list = ", ".join(canonical_items)
    return f"({canonical_list})"
  return _canonical_enum_literal(field_name, value, allowed_values)


def normalize_gaql_enum_literals(query: str) -> str:
  """Validates and canonicalizes enum filters using field metadata.

  Unknown fields and non-enum fields are left untouched so custom valid GAQL is
  not blocked by incomplete local parsing.
  """
  where_span = _where_body_span(query)
  if where_span is None:
    return query

  enum_fields = _load_enum_field_values()
  if not enum_fields:
    return query

  replacements = []
  where_offset, where_end = where_span
  where_body = query[where_offset:where_end]
  string_spans = [
      match.span() for match in _STRING_LITERAL_PATTERN.finditer(where_body)
  ]
  for enum_filter_match in _ENUM_FILTER_PATTERN.finditer(where_body):
    field_start = enum_filter_match.start("field")
    if any(start <= field_start < end for start, end in string_spans):
      continue
    field_name = enum_filter_match.group("field").lower()
    allowed_values = enum_fields.get(field_name)
    if not allowed_values:
      continue

    value = enum_filter_match.group("value")
    canonical_value = _canonical_enum_filter_value(
        field_name,
        enum_filter_match.group("operator"),
        value,
        allowed_values,
    )
    value_start = where_offset + enum_filter_match.start("value")
    value_end = where_offset + enum_filter_match.end("value")
    if canonical_value != value:
      replacements.append((value_start, value_end, canonical_value))

  for start, end, replacement in sorted(replacements, reverse=True):
    query = query[:start] + replacement + query[end:]
  return query


@functools.lru_cache(maxsize=None)
def _load_resource_field_sets(
    resource_name: str,
) -> dict[str, frozenset[str]] | None:
  """Loads selectable field buckets for one FROM resource."""
  resource_path = _VIEWS_METADATA_DIR / f"{resource_name}.yaml"
  try:
    with open(resource_path, "r", encoding="utf-8") as view_file:
      view_metadata = yaml.safe_load(view_file) or {}
  except FileNotFoundError:
    return None

  field_sets = {}
  for category in ("attributes", "segments", "metrics"):
    values = view_metadata.get(category) or []
    field_sets[category] = frozenset(
        value.lower() for value in values if isinstance(value, str)
    )
  return field_sets


def _split_select_fields(select_body: str) -> list[str]:
  """Splits a simple GAQL SELECT field list."""
  return [
      field.strip().lower()
      for field in select_body.split(",")
      if field.strip()
  ]


def _query_from_resource(query: str) -> str | None:
  """Returns the FROM resource name for a simple GAQL query."""
  from_match = _FROM_PATTERN.search(_query_without_string_literals(query))
  if not from_match:
    return None
  return from_match.group("resource").lower()


def _selected_fields(query: str) -> list[str]:
  """Returns dotted field references from the SELECT clause."""
  select_match = _SELECT_FROM_PATTERN.search(query)
  if not select_match:
    return []
  return [
      field
      for field in _split_select_fields(select_match.group("select"))
      if _FIELD_TOKEN_PATTERN.fullmatch(field)
  ]


def _unique_field_order(fields: list[str]) -> list[str]:
  """Deduplicates field names while preserving first-seen order."""
  seen: set[str] = set()
  deduped_fields = []
  for field_name in fields:
    normalized_field = field_name.lower()
    if normalized_field in seen:
      continue
    seen.add(normalized_field)
    deduped_fields.append(normalized_field)
  return deduped_fields


def _compatible_fields_text(
    resource_name: str,
    field_sets: dict[str, frozenset[str]],
) -> str:
  """Builds a compact compatibility hint for a FROM resource."""
  attribute_count = len(field_sets["attributes"])
  segment_count = len(field_sets["segments"])
  metric_count = len(field_sets["metrics"])
  return (
      f"Use get_reporting_view_doc({resource_name!r}) for compatible fields. "
      f"Local v24 metadata has {attribute_count} attributes, "
      f"{segment_count} segments, and {metric_count} metrics for this "
      "resource."
  )


def validate_gaql_field_compatibility(query: str) -> None:
  """Validates selected, filtered, and sorted fields against FROM metadata.

  The checked-in v24 view metadata is generated from Google Ads field metadata
  and encodes the attribute resources, segments, and metrics selectable with
  each FROM resource. Unknown FROM resources are left to Google Ads so custom
  future-valid GAQL is not blocked by local metadata gaps.
  """
  resource_name = _query_from_resource(query)
  if not resource_name:
    return

  field_sets = _load_resource_field_sets(resource_name)
  if field_sets is None:
    return

  compatible_fields = frozenset().union(*field_sets.values())
  referenced_fields = _unique_field_order(
      _selected_fields(query) + _referenced_filter_and_order_fields(query)
  )
  for field_name in referenced_fields:
    if field_name in compatible_fields:
      continue
    raise ToolError(
        f"{field_name} is not compatible with FROM {resource_name}. "
        + _compatible_fields_text(resource_name, field_sets)
    )
  _validate_pairwise_field_compatibility(resource_name, referenced_fields)


def _validate_pairwise_field_compatibility(
    resource_name: str,
    referenced_fields: list[str],
) -> None:
  """Validates compact local pairwise compatibility overrides."""
  del resource_name
  metrics = {
      field for field in referenced_fields if field.startswith("metrics.")
  }
  segments = {
      field for field in referenced_fields if field.startswith("segments.")
  }
  unique_user_metrics = sorted(metrics & _UNIQUE_USER_METRICS)
  if not unique_user_metrics:
    return

  incompatible_segments = sorted(segments - _ALLOWED_UNIQUE_USER_SEGMENTS)
  if incompatible_segments:
    raise ToolError(
        f"{unique_user_metrics[0]} is not selectable with "
        + ", ".join(incompatible_segments)
        + ". Drop the incompatible segment or use get_reporting_view_doc for "
        "compatible metric/segment combinations."
    )


def _referenced_filter_and_order_fields(query: str) -> list[str]:
  """Returns WHERE/ORDER BY field references in first-seen order."""
  fields: list[str] = []
  seen: set[str] = set()
  for span_getter in (_where_body_span, _order_by_body_span):
    body_span = span_getter(query)
    if body_span is None:
      continue
    body_start, body_end = body_span
    clause_body = _query_without_string_literals(query[body_start:body_end])
    for field_name in _FIELD_TOKEN_PATTERN.findall(clause_body):
      normalized_field = field_name.lower()
      if normalized_field in seen:
        continue
      seen.add(normalized_field)
      fields.append(normalized_field)
  return fields


def _validate_or_compatibility(query: str) -> None:
  """Rejects OR in WHERE clauses before Google returns a parse error."""
  where_span = _where_body_span(query)
  if where_span is None:
    return

  body_start, body_end = where_span
  where_body = _query_without_string_literals(query[body_start:body_end])
  if not _contains_or_condition_separator(where_body):
    return

  raise ToolError(
      "GAQL WHERE clauses support AND between conditions, not OR. Split "
      "OR logic into multiple queries, or use IN/REGEXP_MATCH when one "
      "field can express the filter."
  )


def _contains_or_condition_separator(where_body: str) -> bool:
  """Returns whether OR is used as a condition separator, not enum value."""
  for match in _OR_PATTERN.finditer(where_body):
    if _or_token_is_enum_value(where_body, match):
      continue
    return True
  return False


def _or_token_is_enum_value(
    where_body: str,
    match: re.Match[str],
) -> bool:
  """Returns whether an OR token is positioned like a GAQL enum literal."""
  prefix = where_body[: match.start()].rstrip()
  if re.search(r"(?:=|!=|<>)\s*$", prefix):
    return True

  last_open = prefix.rfind("(")
  last_close = prefix.rfind(")")
  if last_open <= last_close:
    return False

  before_open = prefix[:last_open].rstrip()
  return bool(re.search(r"\b(?:NOT\s+)?IN\s*$", before_open, re.I))


def _add_missing_referenced_select_fields(query: str) -> str:
  """Adds WHERE/ORDER fields to SELECT when GAQL would require them."""
  select_match = _SELECT_FROM_PATTERN.search(query)
  if not select_match:
    return query

  selected_fields = set(_split_select_fields(select_match.group("select")))
  missing_fields = [
      field
      for field in _referenced_filter_and_order_fields(query)
      if field.startswith("segments.")
      and field not in selected_fields
      and field not in _CORE_DATE_SEGMENTS
  ]
  if not missing_fields:
    return query

  select_start = select_match.start("select")
  select_end = select_match.end("select")
  select_body = select_match.group("select").rstrip()
  missing_fields_text = ", ".join(missing_fields)
  updated_select_body = f"{select_body}, {missing_fields_text} "
  return query[:select_start] + updated_select_body + query[select_end:]


def _preflight_gaql(query: str) -> str:
  """Applies low-risk GAQL rewrites and rejects known-invalid syntax."""
  if not isinstance(query, str) or not query.strip():
    raise ToolError("query must be a non-empty GAQL string.")

  query_without_literals = _query_without_string_literals(query)
  if _AGGREGATE_PATTERN.search(query_without_literals):
    raise ToolError(
        "GAQL does not support aggregate functions or GROUP BY. Select raw "
        "rows and aggregate the result client-side."
    )
  _validate_or_compatibility(query_without_literals)

  query = rewrite_gaql_date_ranges(query)
  query = normalize_gaql_enum_literals(query)
  validate_gaql_field_compatibility(query)
  return _add_missing_referenced_select_fields(query)


def _append_omit_unselected_resource_names_parameter(query: str) -> str:
  """Adds omit_unselected_resource_names=true if the query omits it."""
  query = query.rstrip()
  if query.endswith(";"):
    query = query[:-1].rstrip()

  if _contains_outside_string_literals(query, _OMIT_UNSELECTED_PATTERN):
    return query

  if _contains_outside_string_literals(query, _PARAMETERS_PATTERN):
    return query + ", omit_unselected_resource_names=true"
  return query + " PARAMETERS omit_unselected_resource_names=true"


def preprocess_gaql_query(query: str) -> str:
  """Preprocesses GAQL for safer, lower-retry execution."""
  query = _preflight_gaql(query)
  return _append_omit_unselected_resource_names_parameter(query)


def quote_int_values(values: list[str]) -> str:
  """Formats integer-like values for an IN clause."""
  quoted_values = []
  for value in normalize_list_arg(values, "values"):
    quoted_values.append(quote_int_value(value, "values"))
  return ", ".join(quoted_values)


def quote_int_value(value: Any, field_name: str) -> str:
  """Formats one integer-like value for GAQL interpolation."""
  if isinstance(value, bool) or not isinstance(value, (int, str)):
    raise ToolError(f"{field_name} must be an integer.")
  try:
    return str(int(value))
  except (TypeError, ValueError) as exc:
    raise ToolError(f"{field_name} must be an integer.") from exc


def quote_string_values(values: list[str]) -> str:
  """Formats string values for an IN clause."""
  return ", ".join(
      gaql_quote_string(str(value))
      for value in normalize_list_arg(values, "values")
  )


def quote_enum_values(values: list[str]) -> str:
  """Formats enum names for an IN clause."""
  quoted_values = []
  for value in normalize_list_arg(values, "values"):
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
  conditions = [condition for condition in conditions if condition]
  if not conditions:
    return ""
  return " WHERE " + " AND ".join(conditions)
