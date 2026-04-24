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

"""Helpers for lightweight campaign status/spend context."""

from collections import OrderedDict
from copy import deepcopy
import time
from typing import Any

from ads_mcp.tools._gaql import date_range_label
from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools._gaql import segments_date_condition
from ads_mcp.tools.api import run_gaql_query


_CAMPAIGN_CONTEXT_CACHE_TTL_SECONDS = 15.0
_CAMPAIGN_CONTEXT_CACHE_MAX_ENTRIES = 128
_CAMPAIGN_CONTEXT_CACHE: OrderedDict[
    tuple[str, str | None, str, tuple[str, ...]],
    tuple[float, dict[str, dict[str, Any]]],
] = OrderedDict()


def _campaign_context_cache_key(
    customer_id: str,
    campaign_ids: list[str],
    login_customer_id: str | None,
    spend_date_range: str | dict[str, str],
) -> tuple[str, str | None, str, tuple[str, ...]]:
  """Builds a cache key for campaign context reads."""
  return (
      customer_id,
      login_customer_id,
      (
          str(dict(spend_date_range))
          if isinstance(spend_date_range, dict)
          else spend_date_range
      ),
      tuple(sorted(set(campaign_ids), key=int)),
  )


def _cache_get(
    key: tuple[str, str | None, str, tuple[str, ...]],
) -> dict[str, dict[str, Any]] | None:
  """Returns a cached campaign context when still fresh."""
  cache_entry = _CAMPAIGN_CONTEXT_CACHE.get(key)
  if not cache_entry:
    return None

  cached_at, context = cache_entry
  if time.monotonic() - cached_at > _CAMPAIGN_CONTEXT_CACHE_TTL_SECONDS:
    _CAMPAIGN_CONTEXT_CACHE.pop(key, None)
    return None

  _CAMPAIGN_CONTEXT_CACHE.move_to_end(key)
  return deepcopy(context)


def _cache_set(
    key: tuple[str, str | None, str, tuple[str, ...]],
    context: dict[str, dict[str, Any]],
) -> None:
  """Stores campaign context in the bounded in-process cache."""
  _CAMPAIGN_CONTEXT_CACHE[key] = (time.monotonic(), deepcopy(context))
  _CAMPAIGN_CONTEXT_CACHE.move_to_end(key)
  while len(_CAMPAIGN_CONTEXT_CACHE) > _CAMPAIGN_CONTEXT_CACHE_MAX_ENTRIES:
    _CAMPAIGN_CONTEXT_CACHE.popitem(last=False)


def get_campaign_context(
    customer_id: str,
    campaign_ids: list[str],
    login_customer_id: str | None = None,
    spend_date_range: str | dict[str, str] = "LAST_30_DAYS",
) -> dict[str, dict[str, Any]]:
  """Returns campaign status and recent spend keyed by campaign ID."""
  unique_campaign_ids = sorted(set(campaign_ids), key=int)
  if not unique_campaign_ids:
    return {}
  spend_date_range = date_range_label(spend_date_range)
  spend_date_condition = segments_date_condition(spend_date_range)

  cache_key = _campaign_context_cache_key(
      customer_id,
      unique_campaign_ids,
      login_customer_id,
      spend_date_range,
  )
  cached_context = _cache_get(cache_key)
  if cached_context is not None:
    return cached_context

  campaign_id_filter = quote_int_values(unique_campaign_ids)
  status_rows = run_gaql_query(
      f"""
      SELECT
        campaign.id,
        campaign.name,
        campaign.status
      FROM campaign
      WHERE campaign.id IN ({campaign_id_filter})
      ORDER BY campaign.id
      """,
      customer_id,
      login_customer_id,
  )
  spend_rows = run_gaql_query(
      f"""
      SELECT
        campaign.id,
        metrics.cost_micros
      FROM campaign
      WHERE campaign.id IN ({campaign_id_filter})
        {f"AND {spend_date_condition}" if spend_date_condition else ""}
      ORDER BY campaign.id
      """,
      customer_id,
      login_customer_id,
  )

  context = {
      row["campaign.id"]: {
          "campaign.name": row.get("campaign.name"),
          "campaign.status": row.get("campaign.status"),
          "recent_30_day_cost_micros": 0,
      }
      for row in status_rows
  }
  for row in spend_rows:
    campaign_id = row["campaign.id"]
    context.setdefault(
        campaign_id,
        {
            "campaign.name": None,
            "campaign.status": None,
            "recent_30_day_cost_micros": 0,
        },
    )
    context[campaign_id]["recent_30_day_cost_micros"] = row.get(
        "metrics.cost_micros", 0
    )

  _cache_set(cache_key, context)
  return deepcopy(context)
