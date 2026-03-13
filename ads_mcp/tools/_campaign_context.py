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

from typing import Any

from ads_mcp.tools._gaql import quote_int_values
from ads_mcp.tools.api import run_gaql_query


def get_campaign_context(
    customer_id: str,
    campaign_ids: list[str],
    login_customer_id: str | None = None,
    spend_date_range: str = "LAST_30_DAYS",
) -> dict[str, dict[str, Any]]:
  """Returns campaign status and recent spend keyed by campaign ID."""
  unique_campaign_ids = sorted(set(campaign_ids), key=int)
  if not unique_campaign_ids:
    return {}

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
        AND segments.date DURING {spend_date_range}
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

  return context
