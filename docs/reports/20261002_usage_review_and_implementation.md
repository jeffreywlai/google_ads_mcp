# Google Ads MCP usage review and implementation

Date: October 2, 2026.

The supplied usage report identifies real gaps in campaign investigations and
large-result delivery. This review checked those gaps against current main,
reused existing query-safety work, and implemented the supported improvements
across R1 through R5. The priority is complete, recoverable data first, then
fewer failed queries and fewer client-side joins.

R5 now supports explicit daily windows and periods around supported retained
campaign/budget changes. Conversion activity does not establish a historical
goal-switch date, and daily metrics cannot be allocated at an arbitrary change
timestamp. Change-derived comparisons exclude every boundary day and retain
the exact timestamps as evidence; they do not establish causal effects.

## Baseline and scope

- Current fetched main: `28a57cdc844cec8983cd5b7b0a542c407111d603`.
- Reused query-safety branch: `codex/batch-a-query-safety`,
  `048c34fbbe06916efd90187bfd032b10844586cb`. Its three existing commits are
  included in this checkout; they are not yet in the fetched main.
- Implementation branch: `codex/usage-report-remediation`, in
  `.worktrees/usage-report-remediation` under the primary repository.
- The primary checkout's existing edits and the other worktrees were preserved.
- Google Ads API remains v24; the lockfile and dependency versions are unchanged.
- The 15 reported main-thread transcripts were reparsed and 154 MCP calls/results
  paired by tool-use ID. Their 114 raw queries and eight query-based exports form
  the anonymized 122-query preflight fixture; a ninth token export has no SQL.
  Queries were checked at captured account-local dates. This validates query
  compatibility, not past account-data equivalence or historical output replay.
  Subsequent opt-in live reads are described separately below.

## Priorities and implemented behavior

These priorities reflect correctness and recurring workflow cost. The report's
time-saving estimates are not measured performance results.

| Priority | Recommendation | Implemented behavior |
|---|---|---|
| 1 | R4 and F08, preserve complete results | `execute_gaql` bounds inline rows to 32 KiB and the logical response to 48 KiB. MCP text/structured duplication and framing can produce a larger transport envelope. `max_rows` and its alias affect the preview; the exact CSV handoff preserves all retrieved rows without rerunning the query. Query LIMIT and API constraints still apply. GAQL and deferred report snapshots have a 15-minute expiry, with earlier eviction possible under documented bounded-cache pressure. |
| 2 | R3, reduce failed round trips | Known field, enum, and segment-metric problems are reported together in a bounded error. Verified suggestions cover removed campaign date fields, keyword text, simulation point lists, and lifecycle resource-name filters. Unknown future fields still reach Google for validation. Existing query-safety work supplies required SELECT additions and explicit retention policies. |
| 3 | R1, answer scoped history questions | All four history tools accept campaign/ad-group filters. Inline rows name the changed entity. Event previews optionally show only changed-field old/new values with explicit unavailable markers. Continuations and exports retain scope and value mode. Extended history keeps status-only resource coverage separate from granular events. |
| 4 | R2, assemble current settings | `get_campaign_settings` joins complete settings reads before presentation: bidding and portfolio targets, budget amount/period/type/sharing, v24 dates, AI Max, geographic settings, resolved location targets, shared lists, standard/custom conversion goals, and lifecycle goals. Its whole response stays within 32 KiB, with exact deferred export for omitted objects. |
| 5 | R5, compare supported periods | `compare_performance_periods` sums complete daily data across caller-supplied nonoverlapping inclusive windows, excludes gaps, and computes ratios from summed metrics. DEVICE and physical-user COUNTRY breakdowns include API-resolved country names. `compare_performance_around_changes` derives periods only from complete supported retained campaign/budget evidence, excludes all boundary days, and exposes their totals separately. Both provide exact source and assembled-result exports. |

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
| F07, connection closed | Historical cause remains unknown because no server log/version was recorded. Opt-in payload-free stderr diagnostics, offline concurrent-large-response stdio coverage, and a successful live read-only session improve evidence; they do not prove the old cause or guarantee long-running production reliability. |
| F08, expired export snapshot | The 113-second historical failure is consistent with the inspected code's former 90-second expiry; the unrecorded historical server version prevents proving the cause. Expiry is now 15 minutes, with early eviction described. |
| F09, API compatibility failures | Generated segment-metric checks were already present. Reused SELECT repair handles attributed segments; valid unknown fields are not rejected speculatively. |
| F10, OR in field search | Multiple-pattern field search was already merged. No duplicate tool was added. GAQL still does not accept OR. |
| F11, location mutations | Added `add_campaign_location_targets` and `remove_campaign_location_targets`. Additions require explicit target/exclusion choice; removals verify active LOCATION attachments in the requested campaign. Both support validation-only and optional partial failures without mutation retries. No account targeting was changed during validation. |
| F12, dedicated-tool coverage | Settings/lifecycle coverage is added. Campaign/ad-group simulations now include every supported point list when type is omitted. The preexisting acquisition tool covers compatible new-versus-returning split queries; discovery coverage identifies it. These capabilities have offline coverage, not historical-data acceptance. |

## Remaining gaps and evidence limits

| Gap | Implemented, preexisting, or still limited |
|---|---|
| G01, change/performance windows | Scoped history plus the new retained-change comparator remove client-side joins for supported campaign/budget boundaries. Exact changed-day allocation and causal attribution remain unsupported. Current-only budget links are evidence, not proven past associations; edits inside portfolio strategies and conversion-goal switches are not inferred. |
| G02, older settings/goal history | Granular events beyond 30 days and status data beyond 90 days cannot be recovered from these APIs. Explicit performance windows can extend further, but conversion activity does not prove historical goal switches or keyword enablement dates. External historical evidence is still required. |
| G03, many windows/countries | Explicit multi-window COUNTRY sums and API-resolved names are added; the paginated geographic tool also resolves page country names. COUNTRY uses physical-user location and can differ from campaign totals. Reference-country screening, thresholds, and recommendations still require caller analysis and assumptions. |
| G04, Customer Match/upload health | `summarize_customer_match_jobs` joins attributed list names and counts every available matching job by status, match-rate bucket, and failure reason before previewing highest numeric IDs. v24 has no Customer Match creation/upload timestamp, so time recency remains unavailable. Buckets are not exact or volume-weighted rates. The separate offline conversion upload-health tools predate this work. |
| G05, new-versus-returning | The preexisting acquisition workflow uses compatible split queries; discovery and the historical invalid-query regression identify it. Warehouse new-customer definitions and conversion-lag measurements remain external. |
| G06, location removal | Dedicated validated addition/removal tools are now available. Geo IDs identify places; removal requires campaign criterion IDs. The historical unsaved browser edits were not resumed. |
| G07, warehouse search-term audit | Existing exact exports cover Google Ads data. N-gram judgment and joins to warehouse new-customer data remain outside this server; the original report indicated no separate server defect. |
| G08, simulations/calibration | Missing default point lists are fixed. Simulation calibration and the interpretation of forecasts remain analyst judgment; no calibration factor is generated or validated. |

## Decisions that differ from the proposals

R4 uses the existing 32 KiB row budget and 48 KiB whole-response contract,
instead of imposing a new 32 KiB whole-response limit on every raw query. The
settings, period, change-period, and Customer Match reports use a 32 KiB
logical-response limit. No new public budget parameter, snapshot store, or
query language was introduced.

R1 keeps `list_change_events` event-only. Requests mixing events and status-only
resources use `get_change_history_extended`, which reports both coverage sources
without describing status summaries as old/new-value events. A scoped request
cannot reconstruct unassociated shared-resource history.

R5 does not infer goal changes from first/last nonzero conversion dates or
claim timestamp-perfect allocation. COUNTRY is supported through physical-user
location rows; other grains remain outside these comparators. The exact daily
source export includes the enclosing date range; its scope is labeled, and gap
or boundary days are excluded from period totals. A capped change read disables
change-derived comparisons instead of treating incomplete evidence as complete.
Portfolio-strategy edits, ad-group/criterion bids, goal switches, and unreported
changes remain outside this evidence scope. Absence of events does not prove
unchanged settings; reporting delay and conversion lag still affect interpretation.

## Validation

The expanded implementation passed these gates in the isolated checkout:

```text
uv sync --locked
Resolved 113 packages; audited 108 packages.

uv run pyink --check .
79 files would be left unchanged.

uv run pylint ads_mcp tests --fail-under=9.5
9.86/10; exit 0. Existing test warnings remain.

.venv/bin/pytest -q
2273 passed, 17 skipped in 25.59s.

git diff --check
Exit 0.
```

The full suite above was run by the final reviewer in the locked environment.
The skipped tests are opt-in live tests. The public inventory is now 117 tools.
The prior reviewed commit `7df9f7b` had 1921 passing tests and 17 live skips.

Coverage includes real installed v24 protobuf field traversal, every emitted
workflow query's local preflight, actual FastMCP tool/schema delivery, whole
response byte limits, exact CSV values without query reruns, snapshot expiry and
credential isolation, two intervening exports after more than ten minutes,
history cursor binding, unavailable/zero/false diff values, and decimal sums
across device and disjoint date windows. New focused checks cover Customer Match
all-job aggregation, exact source/derived exports, mutation preflight/receipts,
default simulation points, country names, and change-derived period evidence.

The sanitized historical fixture contains 106 previously successful queries
and 16 historical errors. All 106 pass current local preflight. Thirteen invalid
queries fail locally with actionable diagnostics; three historically failed but
valid queries pass: two old transport failures and one geographic required-SELECT
case repaired by existing query-safety behavior. Replay locks that repair rather
than introducing another query rewrite. Captured account timezone matters at
midnight; this replay uses account-local dates and preserves accepted comparison
operators. It does not rerun the old account reads or establish data equivalence.

An actual offline stdio subprocess passed three concurrent 12,000-row read
fixtures, a normal tool error, and a successful subsequent call. Opt-in
`GOOGLE_ADS_MCP_DIAGNOSTICS=1` records tool timings, exception class, result
envelope bytes, package versions, and a build fingerprint on stderr without
arguments, payloads, credentials, or error text. Observed successful ToolResult
envelopes were about 67 KiB before transport framing because text and structured
content duplicate the bounded logical result. Neither this measurement nor the
48 KiB logical-response contract promises a 48 KiB wire message.

The GPT-6.1 Sol max review found malformed country-lookup IN syntax and
location discovery colliding with negative-keyword requests. Both were fixed
and covered by regressions; the final integrated review found no remaining
actionable issues. A separate GPT-6.1 Sol max reviewer independently checked
the new location tools and passed all 85 focused regressions. Subsequent
opt-in read-only acceptance
used the actual stdio transport for seven dedicated tools: Customer Match jobs,
campaign settings, explicit COUNTRY periods with an actual API name lookup,
retained-change periods, geographic reporting, and default campaign/ad-group
simulations. The complete aggregations reported analysis_complete=true. A
normal aggregated invalid-query error was followed by another successful read
on the same connection. The simulation queries were accepted, but no available
live simulation points were verified. Exact live snapshot exports and the old
historical answers remain untested. No ad-account mutation, merge, deployment,
or measured time saving is claimed.

## Primary references

- [v24 campaign fields](https://developers.google.com/google-ads/api/fields/v24/campaign)
  and [campaign schema](https://raw.githubusercontent.com/googleapis/googleapis/master/google/ads/googleads/v24/resources/campaign.proto)
  establish the selected date and strategy paths.
- [v24 change event](https://developers.google.com/google-ads/api/fields/v24/change_event)
  and [change status](https://developers.google.com/google-ads/api/fields/v24/change_status)
  define entity associations, values, filters, and distinct resource coverage.
- [v24 offline user data jobs](https://developers.google.com/google-ads/api/fields/v24/offline_user_data_job)
  define attributed user lists, job status, match-rate ranges, and failure reason.
  Their job IDs are identifiers; the schema provides no Customer Match job
  creation/upload timestamp to establish chronological recency.
- [v24 user locations](https://developers.google.com/google-ads/api/fields/v24/user_location_view)
  and [geo target constants](https://developers.google.com/google-ads/api/fields/v24/geo_target_constant)
  define the physical-country metrics source and country-name lookup.
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
