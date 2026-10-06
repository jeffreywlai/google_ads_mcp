# Copyright 2026 Google LLC
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

"""Validation and aggregation for explicit daily performance comparisons."""

from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
import math
import re
from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp.tools._gaql import date_range_bounds
from ads_mcp.tools._gaql import normalize_list_arg

PERFORMANCE_FIELDS = {
    "cost_micros": "metrics.cost_micros",
    "impressions": "metrics.impressions",
    "clicks": "metrics.clicks",
    "conversions": "metrics.conversions",
    "conversions_value": "metrics.conversions_value",
}
_INTEGER_METRICS = {"cost_micros", "impressions", "clicks"}


def normalize_periods(
    periods: list[dict[str, str]] | str,
) -> list[dict[str, str]]:
  """Validates named inclusive date windows and rejects overlap."""
  normalized = []
  labels = set()
  for index, period in enumerate(normalize_list_arg(periods, "periods")):
    if not isinstance(period, dict):
      raise ToolError("Each period must have start_date and end_date.")
    unknown_keys = set(period) - {"label", "start_date", "end_date"}
    if unknown_keys:
      raise ToolError(
          "Period objects only support label, start_date, and end_date."
      )
    bounds = date_range_bounds(
        {key: period.get(key) for key in ("start_date", "end_date")}
    )
    label = period.get("label", f"period_{index + 1}")
    if not isinstance(label, str) or not label.strip():
      raise ToolError("Period labels must be non-empty strings.")
    label = label.strip()
    if label in labels:
      raise ToolError("Period labels must be unique.")
    labels.add(label)
    normalized.append(
        {"label": label, "start_date": bounds[0], "end_date": bounds[1]}
    )
  if not normalized:
    raise ToolError("At least one explicit period is required.")
  normalized.sort(key=lambda period: period["start_date"])
  for previous, current in zip(normalized, normalized[1:]):
    if current["start_date"] <= previous["end_date"]:
      raise ToolError("Periods must not overlap; dates are inclusive.")
  return normalized


def _metric_value(row: dict[str, Any], name: str) -> Decimal:
  """Reads finite metric values without inventing zeros for missing fields."""
  field = PERFORMANCE_FIELDS[name]
  value = row.get(field)
  if isinstance(value, bool) or value is None:
    raise ToolError(f"Missing or invalid performance value for {field}.")
  try:
    number = Decimal(str(value))
  except InvalidOperation as exc:
    raise ToolError(f"Invalid performance value for {field}.") from exc
  if not number.is_finite() or not math.isfinite(float(number)):
    raise ToolError(f"Non-finite performance value for {field}.")
  if name in _INTEGER_METRICS and (
      number < 0 or number != number.to_integral_value()
  ):
    raise ToolError(f"{field} must be a non-negative integer.")
  return number


def _empty_totals() -> dict[str, Decimal]:
  return {name: Decimal(0) for name in PERFORMANCE_FIELDS}


def _present_totals(totals: dict[str, Decimal]) -> dict[str, Any]:
  cost = totals["cost_micros"] / Decimal(1_000_000)
  return {
      name: int(value) if name in _INTEGER_METRICS else float(value)
      for name, value in totals.items()
  } | {
      "cost": float(cost),
      "roas": (float(totals["conversions_value"] / cost) if cost else None),
      "cost_per_conversion": (
          float(cost / totals["conversions"])
          if totals["conversions"]
          else None
      ),
  }


def aggregate_periods(
    rows: list[dict[str, Any]],
    periods: list[dict[str, str]],
    segment_field: str | None,
    source_bounds: tuple[str, str] | None = None,
) -> dict[str, Any]:
  """Aggregates complete rows once, using ratios of summed metrics."""
  totals_by_label = {period["label"]: _empty_totals() for period in periods}
  segments_by_label = {period["label"]: {} for period in periods}
  counts_by_label = {period["label"]: 0 for period in periods}
  requested_totals = _empty_totals()
  source_totals = _empty_totals()
  excluded_totals = _empty_totals()
  excluded_gap_row_count = 0
  if source_bounds is None:
    source_bounds = (periods[0]["start_date"], periods[-1]["end_date"])
  for row in rows:
    row_date = row.get("segments.date")
    if not isinstance(row_date, str):
      raise ToolError("Performance source rows must include segments.date.")
    try:
      valid_date = date.fromisoformat(row_date).isoformat() == row_date
    except ValueError as exc:
      raise ToolError("Invalid performance source date.") from exc
    if not valid_date:
      raise ToolError("Invalid performance source date.")
    if not source_bounds[0] <= row_date <= source_bounds[1]:
      raise ToolError("Performance source returned a date outside periods.")
    values = {name: _metric_value(row, name) for name in PERFORMANCE_FIELDS}
    for name, value in values.items():
      source_totals[name] += value
    period = next(
        (
            period
            for period in periods
            if period["start_date"] <= row_date <= period["end_date"]
        ),
        None,
    )
    if period is None:
      excluded_gap_row_count += 1
      for name, value in values.items():
        excluded_totals[name] += value
      continue
    label = period["label"]
    counts_by_label[label] += 1
    for name, value in values.items():
      totals_by_label[label][name] += value
      requested_totals[name] += value
    if segment_field:
      segment = row.get(segment_field)
      if segment is None:
        raise ToolError(f"Performance source is missing {segment_field}.")
      segment_totals = segments_by_label[label].setdefault(
          str(segment), _empty_totals()
      )
      for name, value in values.items():
        segment_totals[name] += value
  results = []
  for period in periods:
    label = period["label"]
    result = {
        **period,
        "boundary_evidence": "caller_supplied_dates",
        "source_row_count": counts_by_label[label],
        **_present_totals(totals_by_label[label]),
    }
    if segment_field:
      result["segments"] = [
          {"segment": segment, **_present_totals(totals)}
          for segment, totals in sorted(segments_by_label[label].items())
      ]
    results.append(result)
  return {
      "periods": results,
      "requested_periods_total": _present_totals(requested_totals),
      "excluded_gap_row_count": excluded_gap_row_count,
      "excluded_gap_total": _present_totals(excluded_totals),
      "captured_source_total": _present_totals(source_totals),
  }


def periods_around_changes(
    start_date: str,
    end_date: str,
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
  """Derives daily windows, conservatively excluding every boundary day.

  Timestamps remain evidence only. Excluding even a midnight boundary avoids
  assuming the API timestamp is an exact performance allocation boundary.
  """
  change_days = set()
  for event in evidence:
    value = event.get("change_event.change_date_time")
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value
    ):
      raise ToolError("Invalid account-local change-event timestamp.")
    try:
      timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
      raise ToolError("Invalid change-event timestamp.") from exc
    if timestamp.tzinfo is not None:
      raise ToolError("Expected an account-local change-event timestamp.")
    change_date = timestamp.date().isoformat()
    if not start_date <= change_date <= end_date:
      raise ToolError("Change evidence returned a timestamp outside dates.")
    change_days.add(change_date)
  periods = []
  cursor = date.fromisoformat(start_date)
  last_date = date.fromisoformat(end_date)
  for change_date in sorted(change_days):
    boundary = date.fromisoformat(change_date)
    if cursor < boundary:
      periods.append(
          {
              "label": f"period_{len(periods) + 1}",
              "start_date": cursor.isoformat(),
              "end_date": (boundary - timedelta(days=1)).isoformat(),
          }
      )
    cursor = boundary + timedelta(days=1)
  if cursor <= last_date:
    periods.append(
        {
            "label": f"period_{len(periods) + 1}",
            "start_date": cursor.isoformat(),
            "end_date": last_date.isoformat(),
        }
    )
  return periods, sorted(change_days)
