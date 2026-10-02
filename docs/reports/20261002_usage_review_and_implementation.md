# Google Ads MCP usage review and implementation

Date: October 2, 2026.

The supplied usage report identifies real gaps in campaign investigations and
large-result delivery. This review checked those gaps against current main,
reused existing query-safety work, and implemented the supported improvements
across R1 through R5. The priority is complete, recoverable data first, then
fewer failed queries and fewer client-side joins.

The automatic period detection proposed in R5 needs different evidence.
Conversion activity does not establish a historical goal-switch date, and
reported daily or hourly metrics cannot be allocated precisely at an arbitrary
change timestamp. The implementation compares explicit daily date windows.

## Baseline and scope

- Current fetched main: `28a57cdc844cec8983cd5b7b0a542c407111d603`.
- Reused query-safety branch: `codex/batch-a-query-safety`,
  `048c34fbbe06916efd90187bfd032b10844586cb`. Its three existing commits are
  included in this checkout; they are not yet in the fetched main.
- Implementation branch: `codex/usage-report-remediation`, in
  `.worktrees/usage-report-remediation` under the primary repository.
- The primary checkout's existing edits and the other worktrees were preserved.
- Google Ads API remains v24; the lockfile and dependency versions are unchanged.
- Historical counts are taken from the supplied report, which records 154 calls
  across 15 sessions. This work did not independently reparse the transcripts,
  replay every historical query, or retrieve live account data.

## Priorities and implemented behavior

These priorities reflect correctness and recurring workflow cost. The report's
time-saving estimates are not measured performance results.

| Priority | Recommendation | Implemented behavior |
|---|---|---|
| 1 | R4 and F08, preserve complete results | `execute_gaql` bounds inline rows to 32 KiB and the complete response to 48 KiB. `max_rows` and its alias affect the preview; the exact CSV handoff preserves all retrieved rows without rerunning the query. Query LIMIT and API constraints still apply. GAQL and deferred report snapshots have a 15-minute expiry, with earlier eviction possible under documented bounded-cache pressure. |
| 2 | R3, reduce failed round trips | Known field, enum, and segment-metric problems are reported together in a bounded error. Verified suggestions cover removed campaign date fields, keyword text, simulation point lists, and lifecycle resource-name filters. Unknown future fields still reach Google for validation. Existing query-safety work supplies required SELECT additions and explicit retention policies. |
| 3 | R1, answer scoped history questions | All four history tools accept campaign/ad-group filters. Inline rows name the changed entity. Event previews optionally show only changed-field old/new values with explicit unavailable markers. Continuations and exports retain scope and value mode. Extended history keeps status-only resource coverage separate from granular events. |
| 4 | R2, assemble current settings | `get_campaign_settings` joins complete settings reads before presentation: bidding and portfolio targets, budget amount/period/type/sharing, v24 dates, AI Max, geographic settings, resolved location targets, shared lists, standard/custom conversion goals, and lifecycle goals. Its whole response stays within 32 KiB, with exact deferred export for omitted objects. |
| 5 | R5, compare complete explicit periods | `compare_performance_periods` sums complete daily data across validated nonoverlapping inclusive windows. It excludes gap days, computes ratios from summed metrics, supports DEVICE breakdowns, and exposes exact source and assembled-result exports. Period boundaries are explicitly caller supplied. |

Small raw responses retain their prior result shape. Incomplete raw previews
identify API-order subsets when the query has no ORDER BY. Export tokens are
credential scoped and preserve the captured rows, including oversized rows that
cannot appear inline. Reads do not automatically write exported CSV files.

The new settings snapshot contains sequential API observations rather than an
atomic Google snapshot. Missing source rows remain unavailable or empty; scalar
API defaults do not establish that an override was explicitly configured.
Custom goals and customer lifecycle settings are read from their owning
conversion accounts. Campaign configuration stays in the serving account.

## Findings checked against current code

| Finding | Disposition |
|---|---|
| F01, campaign association and values | Implemented scope, entity fields, and compact diffs. Scope follows Google's reported associations; shared account resources may have no campaign association. |
| F02, oversized results | Raw-query delivery now uses a bounded preview and exact export. Existing list-tool budgets remain in place. |
| F03, one-field errors and stale names | Aggregated errors and verified suggestions implemented. `campaign.start_date_time` and `campaign.end_date_time` are the v24 paths; obsolete aliases remain invalid. |
| F04, unsupported change types | Existing extended-history routing already partitions status/event coverage. Granular event tools retain their narrower types and now give status-only recovery guidance. Unsupported types are not fabricated. |
| F05, history date rules | Reused strict/clamp retention policies from the existing query-safety branch. Relative lookbacks use the account calendar; unavailable explicit history is not silently replaced. |
| F06, incomplete subsets used as answers | Preview completeness and export recovery are clearer. Server metadata cannot guarantee that a client will follow it. |
| F07, connection closed | Historical cause remains unknown because no server log/version was recorded. No causal claim or speculative connection fix is made. |
| F08, expired export snapshot | The 113-second historical failure is consistent with the inspected code's former 90-second expiry; the unrecorded historical server version prevents proving the cause. Expiry is now 15 minutes, with early eviction described. |
| F09, API compatibility failures | Generated segment-metric checks were already present. Reused SELECT repair handles attributed segments; valid unknown fields are not rejected speculatively. |
| F10, OR in field search | Multiple-pattern field search was already merged. No duplicate tool was added. GAQL still does not accept OR. |
| F11, location mutations | The missing write capability remains a separate follow-up. The settings snapshot resolves location names, but it does not change targeting. |
| F12, dedicated-tool coverage | Settings/lifecycle coverage is added. Simulation points already work when `simulation_type="TARGET_ROAS"` or `"TARGET_CPA"` is supplied. The existing acquisition tool already handles its supported split queries. |

## Decisions that differ from the proposals

R4 uses the existing 32 KiB row budget and 48 KiB whole-response contract,
instead of imposing a new 32 KiB whole-response limit on every raw query. Both
new workflow reports use a 32 KiB whole-response limit. No new public budget
parameter, snapshot store, or query language was introduced.

R1 keeps `list_change_events` event-only. Requests mixing events and status-only
resources use `get_change_history_extended`, which reports both coverage sources
without describing status summaries as old/new-value events. A scoped request
cannot reconstruct unassociated shared-resource history.

R5 does not infer goal changes from the first/last nonzero conversion dates or
claim timestamp-perfect allocation. COUNTRY breakdowns and other reporting
grains are not part of the new comparator. Existing geographic tools and raw
exports remain available. The exact daily source export includes the enclosing
date range; its scope is labeled, and gap days are excluded from period totals.

## Validation

All final gates ran successfully in the isolated implementation checkout:

```text
uv sync --locked
Resolved 113 packages; audited 108 packages.

uv run pyink --check .
70 files would be left unchanged.

uv run pylint ads_mcp tests --fail-under=9.5
9.85/10; exit 0. Existing test warnings remain.

uv run pytest -q
1921 passed, 17 skipped in 19.02s.

git diff --check
Exit 0.
```

Coverage includes real installed v24 protobuf field traversal, every emitted
workflow query's local preflight, actual FastMCP tool/schema delivery, whole
response byte limits, exact CSV values without query reruns, snapshot expiry and
credential isolation, two intervening exports after more than ten minutes,
history cursor binding, unavailable/zero/false diff values, and decimal sums
across device and disjoint date windows. Search registration now includes 113
public tools and preserves mutation visibility and routing regression coverage.

Independent reviews checked the export path and the new workflows. They found
and resolved response-budget and discovery-routing errors before the final
gates. Live tests remained opt-in: all 17 were skipped. No ad-account mutation,
merge, deployment, live-account acceptance, or measured time saving is claimed.

## Primary references

- [v24 campaign fields](https://developers.google.com/google-ads/api/fields/v24/campaign)
  and [campaign schema](https://raw.githubusercontent.com/googleapis/googleapis/master/google/ads/googleads/v24/resources/campaign.proto)
  establish the selected date and strategy paths.
- [v24 change event](https://developers.google.com/google-ads/api/fields/v24/change_event)
  and [change status](https://developers.google.com/google-ads/api/fields/v24/change_status)
  define entity associations, values, filters, and distinct resource coverage.
- [v24 campaign lifecycle goals](https://developers.google.com/google-ads/api/fields/v24/campaign_lifecycle_goal)
  and [customer lifecycle goals](https://developers.google.com/google-ads/api/fields/v24/customer_lifecycle_goal)
  define the implemented fields. The [lifecycle guide](https://developers.google.com/google-ads/api/docs/conversions/goals/lifecycle-goals)
  explains conversion-owner handling; implementation follows the v24 schemas
  where the rolling guide describes newer resources.
- [Conversion goals](https://developers.google.com/google-ads/api/docs/conversions/goals/overview),
  [conversion reporting](https://developers.google.com/google-ads/api/docs/conversions/reporting),
  and [v24 time segments](https://developers.google.com/google-ads/api/fields/v24/segments#segments.hour)
  support the evidence limits used in the comparison design. The conclusion
  that conversion activity cannot establish exact historical goal switches is
  an inference from those limits, not a promised Google history feature.
