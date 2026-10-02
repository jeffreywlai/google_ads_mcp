# Batch A implementation and verification

Date: 2026-09-08.
Status: implementation and offline verification record; not live validated
or deployed. The GitHub PR records subsequent publication and review status.

## Scope and baseline

- Branch: `codex/batch-a-query-safety`.
- Worktree: `/Users/jefflai/Documents/GitHub/google_ads_mcp/.worktrees/batch-a`.
- Base: current fetched `origin/main`,
  `28a57cdc844cec8983cd5b7b0a542c407111d603` (merged P1/P2).
- Contract: R1 and R2, with related R9 guidance, from the
  `20260908_usage_findings_remediation_spec.md` draft in the primary checkout.
- The primary checkout's unrelated edits remain untouched. Its new
  `.worktrees/` entry is this isolated implementation, not content to stage
  in the primary branch.
- Google Ads API remains v24. Installed locked versions: `google-ads 30.1.0`
  and `fastmcp 3.2.4`. No dependency, lockfile, version-marker, generated-view,
  public-tool registration, or transport-entry-point changes.

## Implemented behavior

| Finding | Implementation | Offline evidence |
|---|---|---|
| F1 / R1 | Default `retention_policy="error"`; explicit `"clamp"` on raw execution, raw CSV export, and `list_change_events`. Reports requested, available, applied, and unavailable intervals. | Date-only/timestamp bounds, BETWEEN, DURING, 29/30/31/90-day windows, future endpoints, no overlap, midnight, DST, explicit limits, and unsupported syntax. |
| F2 / R2 | Required SELECT additions use the actual FROM resource's segment metadata, including attributed resources. | Both shopping-report windows gain exactly `campaign.id`; ordinary attributes and core-date exceptions are preserved; preparation is idempotent. |
| F3 / R2 | Bounded compatible-field alternatives and verified alternate FROM hints. Removed fields remain invalid; alternatives are not described as equivalent replacements. | Reported removed fields receive inline guidance; genuinely unknown fields pass local validation; known-incompatible fields and pairs remain rejected. |
| F5 / R2 | Verified RESOURCE_NAME empty comparisons fail before reporting I/O with an explicitly broader candidate query where safely constructible. | Both quote forms and equality/inequality operators; ordinary empty text and query-like quoted strings remain valid. No candidate is executed automatically. |

The existing account-timezone helper is shared by execution and history.
Interval interpretation/intersection is isolated in a pure internal helper;
there is no new public tool, general GAQL parser, workflow engine, dependency,
or repair-and-resend loop.

Existing response finalization and credential-scoped snapshots preserve SQL
adjustments, dates, and unavailable coverage across continuations and exact
exports. Large raw results and metadata respect the 48 KiB response budget
and retain an explicit exact materialized-response export. An explicit
`max_rows` still intentionally limits the requested inline result.

`LAST_30_DAYS` continues to exclude today. It is not interchangeable with
the curated tool's inclusive-today `lookback_days=30`. Wholly unavailable
ranges never become a recent replacement period. Raw queries reaching their
LIMIT are marked potentially incomplete; the 10,000-result API cap remains
visible. The existing cap-aware change-history exporter is retained.

Installed v24 message descriptors confirm that the suggested alternatives
exist and the four removed aliases do not. FROM-specific selectability is
checked against the repository's generated metadata. This is not a live
account query or an equivalence claim.

## Regression and adversarial checks

Initial red runs, before the corresponding implementation:

```text
.venv/bin/pytest -q tests/tools/test_batch_a_gaql.py
14 failed, 2 passed in 9.06s

.venv/bin/pytest -q tests/tools/test_batch_a_retention.py
16 failed in 3.80s
```

The final manual adversarial pass additionally reproduced and fixed loss of
unavailable-range metadata when the oldest retained day aged out during the
existing account-midnight refresh. The regression failed with a missing
retention-refresh key before the fix and now passes.

Additional coverage includes:

- Changed policy, dates, resource filters, customer, manager, and page size
  cannot reuse a continuation for a different request.
- Credential changes and expired tokens cannot access or silently rebuild
  a history snapshot. Snapshot exports cannot accept retention overrides.
- Repeating original clamped inputs and using explicit continuation arguments
  both preserve the first request's resolved calendar and coverage.
- Oversized query metadata survives actual FastMCP calls and output-schema
  validation, with exact deferred CSV recovery.
- Huge LIMIT strings and an unrepresentable inclusive end date fail safely.
- Existing stdio/HTTP registration, visibility, enum/string contracts, routing,
  pagination, exports, and P1/P2 regressions remain covered by the full suite.

Final focused regression output:

```text
.venv/bin/pytest -q tests/tools/test_batch_a_gaql.py tests/tools/test_batch_a_retention.py
68 passed in 5.89s
```

## Final CI-equivalent gates

All commands ran from the isolated implementation worktree and exited 0.
Output below records their actual final summaries.

```text
uv sync --locked
Resolved 113 packages in 4ms
Audited 108 packages in 13ms

uv run pyink --check .
64 files would be left unchanged.

uv run pylint ads_mcp tests --fail-under=9.5
Your code has been rated at 9.83/10

uv run pytest -q
1774 passed, 17 skipped in 15.36s

git diff --check
[no output; exit 0]
```

The full lint gate retains warnings in existing tests; it is not warning-free.
Changed production files and focused tests also passed the stricter gate:

```text
uv run pylint ads_mcp/tools/_gaql.py ads_mcp/tools/_history.py ads_mcp/tools/api.py ads_mcp/tools/changes.py tests/tools/test_changes.py tests/tools/test_batch_a_gaql.py tests/tools/test_batch_a_retention.py --fail-under=10
Your code has been rated at 10.00/10
```

## Explicitly not done

- No live Google Ads checks: `GOOGLE_ADS_RUN_LIVE_TESTS` was not enabled;
  the 17 opt-in integration tests were skipped. Live API acceptance and
  account-specific results are not claimed.
- No ad-account mutations, production activation, or measured time savings.
- No merge, deployment, or API-version upgrade as part of implementation.
- Batches B onward remain separate work. Batch A does not complete the entire
  report or add conversion-goal resolution, field diffs, comparison reports,
  location topology, or creative/PMax mutations.

## PR #18 review follow-up — 2026-09-08

- Review comment `3961494777`: confirmed that accepted date-range aliases
  such as `LAST-7-DAYS` and `LAST 7 DAYS` failed in the new history parser.
  Reused the existing GAQL date-clause grammar and literal normalization,
  while continuing to resolve bounds against the captured account-local
  date. No host-time date rewriting was introduced. Regression coverage
  includes both execution/export, extended ranges, unavailable intersections,
  quoted query-like text, and the default error policy.
- Review comment `3961485610`: the claimed loss of preprocessing was not
  reproduced at the service boundary. Both tools reach
  `_iter_gaql_query_attempt`, which preprocesses the query immediately before
  `GoogleAdsService.search_stream`. Two new service-boundary tests pass on the
  reviewed implementation and verify date rewrites, enum normalization, and
  the PARAMETERS addition without mocking `run_gaql_query`. No unnecessary
  production change was made for this finding.

The new review cases initially produced `21 failed, 2 passed, 68 deselected`;
the two passing cases are the service-boundary checks above. After the fix:

```text
.venv/bin/pytest -q tests/tools/test_batch_a_gaql.py tests/tools/test_batch_a_retention.py
91 passed in 14.88s

uv sync --locked
Resolved 113 packages in 28ms
Audited 108 packages in 265ms

uv run pyink --check .
64 files would be left unchanged.

uv run pylint ads_mcp tests --fail-under=9.5
Your code has been rated at 9.84/10

uv run pylint ads_mcp/tools/_history.py tests/tools/test_batch_a_gaql.py tests/tools/test_batch_a_retention.py --fail-under=10
Your code has been rated at 10.00/10

uv run pytest -q
1797 passed, 17 skipped in 30.63s

git diff --check
[no output; exit 0]
```

Live tests remain opt-in and were not run. This follow-up does not authorize
or perform a merge, API upgrade, or ad-account mutation.

### Follow-up: unknown fields and pairwise validation

Review comment `3962096208` was confirmed: a future segment with unique-user
metrics was rejected by the local allowlist after passing the unknown-field
check. The generated compatibility graph likewise rejected a future metric
paired with a known segment. Pairwise validation now receives only locally
known, FROM-compatible fields; per-field rejection of known-incompatible or
removed fields still runs first. Unknown fields stay in the actual query for
Google to validate. They do not suppress validation of other known pairs.

New service-boundary cases cover both execution and CSV export, unknown
metrics/segments, and WHERE/ORDER references. Guard cases preserve known
invalid pairs and removed-field rejection. The isolated schema test now
imports all three tools it checks instead of relying on another test module
to register `list_change_events`.

```text
.venv/bin/pytest -q tests/tools/test_batch_a_gaql.py -k 'unknown_pairwise or unknown_fields_do_not' --tb=short
[before fix] 6 failed, 5 passed, 20 deselected in 4.52s

.venv/bin/pytest -q tests/tools/test_batch_a_gaql.py tests/tools/test_gaql.py tests/tools/test_api.py
204 passed in 4.70s

uv sync --locked
Resolved 113 packages in 4ms
Audited 108 packages in 14ms

uv run pyink --check .
64 files would be left unchanged.

uv run pylint ads_mcp tests --fail-under=9.5
Your code has been rated at 9.84/10

uv run pylint ads_mcp/tools/_gaql.py tests/tools/test_batch_a_gaql.py --fail-under=10
Your code has been rated at 10.00/10

uv run pytest -q
1808 passed, 17 skipped in 11.95s
```

The 17 opt-in live tests remain skipped; no live account validation is claimed.
