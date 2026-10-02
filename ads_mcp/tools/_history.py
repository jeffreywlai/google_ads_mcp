"""Pure, conservative change-event interval planning (no API calls)."""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
import re
from typing import Any
from typing import Literal

from fastmcp.exceptions import ToolError

from ads_mcp.tools import _gaql


RetentionPolicy = Literal["error", "clamp"]
CHANGE_EVENT_LOOKBACK_DAYS = 30
CHANGE_EVENT_RESULT_CAP = 10_000
_FIELD = "change_event.change_date_time"
_FIELD_PATTERN = r"change_event\.change_date_time"
_QUOTED_DATE = r"(?:'[^']*'|\"[^\"]*\")"
_COMPARISON = re.compile(
    rf"{_FIELD_PATTERN}\s*(>=|>|<=|<)\s*({_QUOTED_DATE})", re.I
)
_BETWEEN = re.compile(
    rf"{_FIELD_PATTERN}\s+BETWEEN\s+({_QUOTED_DATE})\s+AND\s+({_QUOTED_DATE})",
    re.I,
)
_TIMESTAMP = re.compile(
    r"(?:\d{4}-\d{2}-\d{2}|\d{8})(?: \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)?"
)


def validate_retention_policy(policy: str) -> None:
  if policy not in ("error", "clamp"):
    raise ToolError("retention_policy must be 'error' or 'clamp'.")


def oldest_supported_start(lookback_days: int, today: date) -> str:
  """First date in an inclusive account-calendar lookback."""
  return (today - timedelta(days=lookback_days - 1)).isoformat()


def _timestamp(value: str) -> datetime:
  text = value[1:-1]
  if not _TIMESTAMP.fullmatch(text):
    raise ToolError(
        "change_event dates must be YYYY-MM-DD or YYYYMMDD, optionally "
        "with HH:MM:SS and up to six fractional digits."
    )
  try:
    return datetime.fromisoformat(text)
  except ValueError as exc:
    raise ToolError(f"Invalid change_event date: {text}.") from exc


def _format_timestamp(value: datetime) -> str:
  return value.isoformat(
      sep=" ", timespec="microseconds" if value.microsecond else "seconds"
  )


@dataclass(frozen=True)
class HistoryInterval:
  """A local wall-clock interval retaining both endpoint operators."""

  start: datetime
  end: datetime
  start_inclusive: bool = True
  end_inclusive: bool = False

  @property
  def empty(self) -> bool:
    return self.start > self.end or (
        self.start == self.end
        and not (self.start_inclusive and self.end_inclusive)
    )

  def metadata(self) -> dict[str, Any]:
    return {
        "start": _format_timestamp(self.start),
        "end": _format_timestamp(self.end),
        "start_inclusive": self.start_inclusive,
        "end_inclusive": self.end_inclusive,
    }

  def condition(self) -> str:
    lower_op = ">=" if self.start_inclusive else ">"
    upper_op = "<=" if self.end_inclusive else "<"
    return (
        f"{_FIELD} {lower_op} '{_format_timestamp(self.start)}' AND "
        f"{_FIELD} {upper_op} '{_format_timestamp(self.end)}'"
    )


def _intersect(
    left: HistoryInterval, right: HistoryInterval
) -> HistoryInterval | None:
  start = max(left.start, right.start)
  end = min(left.end, right.end)
  interval = HistoryInterval(
      start,
      end,
      (left.start < start or left.start_inclusive)
      and (right.start < start or right.start_inclusive),
      (left.end > end or left.end_inclusive)
      and (right.end > end or right.end_inclusive),
  )
  return None if interval.empty else interval


def plan_retention(
    requested: HistoryInterval,
    today: date,
    time_zone: str,
    policy: RetentionPolicy,
) -> tuple[HistoryInterval | None, dict[str, Any]]:
  """Intersects a request with retained dates without inventing older data."""
  validate_retention_policy(policy)
  if requested.start > requested.end:
    raise ToolError("change_event start date must be on or before end date.")
  available = HistoryInterval(
      datetime.fromisoformat(
          oldest_supported_start(CHANGE_EVENT_LOOKBACK_DAYS, today)
      ),
      datetime.combine(today + timedelta(days=1), datetime.min.time()),
      # Google accepts a finite <= next-midnight endpoint. Keep that single
      # boundary instant without rewriting the caller's comparison operator;
      # later future timestamps and older history still require explicit clamp.
      end_inclusive=True,
  )
  applied = _intersect(requested, available)
  unavailable = []
  if not requested.empty:
    if applied is None:
      unavailable = [requested.metadata()]
    else:
      before = HistoryInterval(
          requested.start,
          applied.start,
          requested.start_inclusive,
          not applied.start_inclusive,
      )
      after = HistoryInterval(
          applied.end,
          requested.end,
          not applied.end_inclusive,
          requested.end_inclusive,
      )
      unavailable = [
          part.metadata() for part in (before, after) if not part.empty
      ]
  if unavailable and policy == "error":
    raise ToolError(
        "change_event only supports the last 30 days, with dates through today "
        f"in the account timezone ({time_zone}). Available interval: "
        f"{available.condition()}. "
        'Repeat the same call with retention_policy="clamp" to explicitly '
        "intersect the requested dates with that interval, or use "
        f"list_change_events(start_date='{available.start.date()}', "
        f"end_date='{today}'). "
        "Older granular events cannot be recovered; change_status summaries "
        "have separate 90-day coverage."
    )
  metadata = {
      "retention": {
          "policy": policy,
          "requested_range": requested.metadata(),
          "available_range": available.metadata(),
          "applied_range": applied.metadata() if applied else None,
          "unavailable_ranges": unavailable,
          "clamped": bool(unavailable),
      },
      "account_today": today.isoformat(),
      "account_time_zone": time_zone,
  }
  if unavailable:
    metadata["warnings"] = [
        "Requested change_event dates include unavailable history or future "
        "dates. Only the explicit applied_range was queried; "
        "unavailable_ranges were not retrieved."
        if applied
        else "The requested dates do not intersect retained change_event "
        "history. No reporting query was run; no recent period was substituted."
    ]
  return applied, metadata


def date_interval(start: str, end: str) -> HistoryInterval:
  """Converts the curated tool's inclusive dates to a half-open interval."""
  try:
    exclusive_end = date.fromisoformat(end) + timedelta(days=1)
  except (ValueError, OverflowError) as exc:
    raise ToolError(
        "change_event end_date must be a valid date before 9999-12-31."
    ) from exc
  return HistoryInterval(
      datetime.fromisoformat(start),
      datetime.combine(exclusive_end, datetime.min.time()),
  )


def _conditions(body: str) -> list[str]:
  """Splits top-level ANDs, preserving literals and BETWEEN's internal AND."""
  masked = _gaql._blank_string_literals(body)  # pylint: disable=protected-access
  parts = []
  start = 0
  between = False
  depth = 0
  for match in re.finditer(r"\(|\)|\bBETWEEN\b|\bAND\b", masked, re.I):
    token = match.group().upper()
    if token == "(":
      depth += 1
    elif token == ")":
      depth -= 1
    elif depth == 0 and token == "BETWEEN":
      between = True
    elif depth == 0 and token == "AND":
      if between:
        between = False
      else:
        parts.append(body[start : match.start()].strip())
        start = match.end()
  parts.append(body[start:].strip())
  return parts


def _unsupported_interval() -> ToolError:
  return ToolError(
      "Cannot safely resolve this change_event interval. Use paired "
      ">=/> and <=/< bounds, "
      "BETWEEN, or one DURING named range on change_event.change_date_time. "
      "No date filter was rewritten. Alternatively call list_change_events "
      "with explicit start_date/end_date or lookback_days."
  )


def _parse_interval(
    parts: list[str],
) -> tuple[HistoryInterval | str, list[int], bool]:
  indices = []
  lower = upper = None
  complete = None
  named = False
  for index, part in enumerate(parts):
    masked = _gaql._blank_string_literals(part)  # pylint: disable=protected-access
    if not re.search(rf"\b{_FIELD_PATTERN}\b", masked, re.I):
      continue
    indices.append(index)
    comparison = _COMPARISON.fullmatch(part)
    between = _BETWEEN.fullmatch(part)
    during = _gaql._DATE_FIELD_PATTERN.fullmatch(part)  # pylint: disable=protected-access
    if comparison:
      operator, value = comparison.groups()
      bound = (_timestamp(value), "=" in operator)
      if operator.startswith(">") and lower is None:
        lower = bound
      elif operator.startswith("<") and upper is None:
        upper = bound
      else:
        raise _unsupported_interval()
    elif between and complete is None:
      complete = HistoryInterval(
          _timestamp(between[1]), _timestamp(between[2]), True, True
      )
    elif during and complete is None:
      # Keep named bounds symbolic until the account calendar is available.
      literal = _gaql.validate_date_range_literal(during["literal"])
      if literal == "ALL_TIME":
        raise _unsupported_interval()
      complete = literal
      named = True
    else:
      raise _unsupported_interval()
  if complete is not None and lower is None and upper is None:
    return complete, indices, named
  if complete is None and lower is not None and upper is not None:
    return (
        HistoryInterval(lower[0], upper[0], lower[1], upper[1]),
        indices,
        named,
    )
  raise _unsupported_interval()


def _query_parts(query: str) -> tuple[int, tuple[int, int], list[str]]:
  """Validates the result cap and extracts the local interval conditions."""
  masked = _gaql._blank_string_literals(query)  # pylint: disable=protected-access
  limit = re.search(
      r"\bLIMIT\s+(\d+)\s*(?:PARAMETERS\b.*)?;?\s*$", masked, re.I | re.S
  )
  limit_digits = (limit[1].lstrip("0") or "0") if limit else "0"
  if (
      not limit
      or len(limit_digits) > 5
      or not 1 <= int(limit_digits) <= CHANGE_EVENT_RESULT_CAP
  ):
    raise ToolError(
        "change_event requires an explicit LIMIT from 1 to 10,000; "
        "the API cap is not a complete-history guarantee."
    )
  _gaql._validate_or_compatibility(query)  # pylint: disable=protected-access
  span = _gaql._where_body_span(query)  # pylint: disable=protected-access
  if span is None:
    raise _unsupported_interval()
  parts = _conditions(query[span[0] : span[1]])
  return int(limit_digits), span, parts


def validate_change_event_query(query: str) -> None:
  """Rejects malformed bounds and limits before any account lookup."""
  _, _, parts = _query_parts(query)
  requested, _, _ = _parse_interval(parts)
  if (
      isinstance(requested, HistoryInterval)
      and requested.start > requested.end
  ):
    raise ToolError("change_event start date must be on or before end date.")


def prepare_change_event_query(
    query: str, today: date, time_zone: str, policy: RetentionPolicy
) -> tuple[str | None, dict[str, Any]]:
  """Prepares only safely understood history bounds, never general GAQL."""
  result_limit, span, parts = _query_parts(query)
  requested, indices, named = _parse_interval(parts)
  if isinstance(requested, str):
    bounds = _gaql._literal_date_bounds(requested, today)  # pylint: disable=protected-access
    requested = date_interval(bounds[0].isoformat(), bounds[1].isoformat())
  applied, metadata = plan_retention(requested, today, time_zone, policy)
  executed = query
  adjustments = []
  if applied is None:
    executed = None
  elif named or applied != requested:
    parts[indices[0]] = applied.condition()
    parts = [
        part for index, part in enumerate(parts) if index not in indices[1:]
    ]
    executed = (
        query[: span[0]] + " " + " AND ".join(parts) + " " + query[span[1] :]
    )
  if named or metadata["retention"]["clamped"]:
    adjustments.append(
        {
            "rule": "retention_clamp"
            if metadata["retention"]["clamped"]
            else "resolve_history_dates",
            "field": _FIELD,
            "reason": (
                "Resolved once in account-local time; see retention for "
                "original and applied interval semantics."
            ),
        }
    )
  metadata.update({"original_query": query, "executed_query": executed})
  metadata["query_result_limit"] = result_limit
  if adjustments:
    metadata["query_adjustments"] = adjustments
  return executed, metadata


def add_result_coverage(metadata: dict[str, Any], row_count: int) -> None:
  """Distinguishes source caps from retention gaps and inline presentation."""
  if "retention" not in metadata:
    return
  reached = row_count >= metadata.get(
      "query_result_limit", CHANGE_EVENT_RESULT_CAP
  )
  queried = metadata.get("executed_query") is not None
  metadata["source_queried"] = queried
  metadata["source_complete"] = not reached if queried else None
  metadata["requested_range_complete"] = (
      not reached and not metadata["retention"]["unavailable_ranges"]
  )
  if reached:
    metadata["result_limit_reached"] = True
    metadata.setdefault("warnings", []).append(
        "The query reached its result limit; more matching events may exist. "
        "Subdivide the dates while preserving all filters. "
        "export_change_history_csv supports cap-aware subdivision for its "
        "documented resource/operation filters."
    )
    if metadata.get("query_result_limit") == CHANGE_EVENT_RESULT_CAP:
      metadata["api_result_cap"] = CHANGE_EVENT_RESULT_CAP
