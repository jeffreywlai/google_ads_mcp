# Google Ads usage report resolution

The October 22 report concerns `google_ads_mcp`. Its cached commit,
`6c7c83e6b6f0a8d7941496634e8be40fddc5b310`, matched GitHub `main` when this
work began. Changes use that clean base and preserve the unrelated dirty
primary checkout. The current base uses SDK 33 and API v25.2; the old primary
checkout's v24 instructions do not describe the deployed code.

The design reuses exact snapshots, bounded response helpers, native protobuf
schemas and the existing mutation gate. It adds four tools: snapshot JSON
continuation, asset-group performance, video upload and upload status. It
extends the native mutation allowlist instead of adding a separate write
framework or duplicating campaign, asset and ad services.

## Finding dispositions

| Report item | Disposition and evidence |
| --- | --- |
| G01 singular campaign ID | Fixed. `get_campaign_settings(campaign_id="123")` accepts one ID; missing inputs or both aliases produce a concise `ToolError`. Existing positional calls remain valid. MCP and direct-call regressions cover the contract. |
| G02 five campaigns truncated | Fixed. Oversized results prioritize compact `campaigns` cores, then full `campaign_details`. Core settings preserve status, bidding targets, budget/period, geo types and unified goal values. Independent core/full completeness flags prevent treating summaries as complete detail. Tests cover five realistic goal configurations and large location lists; exact exports retain every full record. |
| G03 incomplete GAQL previews | Fixed. `next_inline_call` invokes `get_gaql_snapshot_page` against the original snapshot. Offsets derive from rows actually delivered after whole-response bounding. Tests cover 19-of-20, multiple pages, credential isolation, expiration, empty results and oversized rows. `complete_inline` on a continuation describes its offset onward; earlier pages and omission markers still matter. |
| G04 removed lifecycle/date fields | Valid upstream changes. Existing v25 diagnostics and explicit field alternatives remain; queries are not silently rewritten. G01/G02 fix the failed `get_campaign_settings` recovery path. |
| G05 removed URL expansion field | Existing local diagnostic retained and tested with the other obsolete fields. |
| G05 empty audience resource predicate | Existing local rejection retained. It suggests correcting the resource predicate without silently broadening the query. |
| G05 empty interest searches | Improved. Zero results explain literal taxonomy-name search and suggest a broader category, browsing, or removing the taxonomy filter. No speculative synonyms or automatic extra Google calls. |
| G05 mutation discovery | Fixed with the locked discovery and native operation coverage below. |
| Known item 1 large location lists | Fixed with G02. Tests cover more than 100 criteria while retaining campaign cores and exact full details. |
| Known item 2 shared-set event type validation | Fixed. The schema still advertises supported enum values, while runtime validation supplies the concise server hint. `SHARED_SET` points to retained change-status history; unsupported `SHARED_CRITERION` is rejected without promising unsupported coverage. Both direct MCP and proxy calls are covered. |
| Known item 3 bad event type and open date range | Existing finite-window guard remains local and actionable. Regressions verify both query/export reject the open interval before creating an API client. Bad enum values in bounded queries remain rejected locally. The caller must correct both constraints. |
| Known item 4 metadata query OR | Fixed. Query and export reject unsupported `OR` before an RPC, reusing the shared parser. Quoted `OR` text stays valid. Existing `patterns=[...]` mode handles multiple name searches. |
| Gap A campaign changes | Covered by native mutations. Regression requests include geo type, target ROAS, status and budget operations with `validate_only=true`. Google's PMax shared-budget restriction remains. |
| Gap B asset-group move | Native operation coverage added. `asset_group_listing_group_filter_operation` completes the allowlist for a 98-operation clone: one group, three tree nodes, 59 asset links and 35 signals. The caller still reads the source, remaps temporary IDs and builds the operations; there is no automatic source-to-clone planner. Pausing the source is a separate explicit update. The server does not change immutable `asset_group.campaign`. |
| Gap C video launch | Added local-file `upload_youtube_video` and `get_youtube_video_upload`. Native workflow fixtures cover three YouTube assets and two PAUSED Demand Gen video ads. URL download is intentionally outside this local-file workflow. Live processing and account eligibility remain to be verified with a real authorized upload. |
| Gap D calibration | Added `get_asset_group_performance` with explicit inclusive dates, optional campaign/group IDs, stable paging and exact export. Snowflake margin joins and business assumptions remain outside the Ads server, as the report specifies. |
| Gap E historical PMax creation | Added `campaign_budget_operation` and listing-filter support to the existing native write path. A positive fixture creates a private budget and PAUSED PMax campaign with temporary references; the clone fixture covers listing filters, asset links and signals. The historical private scripts were not supplied for byte-for-byte replay. |

## Write discovery and workflow verification

`search_tools("copy asset group to another campaign")` returns
`mutate_ads_resources` and `get_resource_mutation_schema` while locked.
Mutation entries include `locked=true` and an unlock instruction. Direct and
proxy execution remain locked. Component authorization and explicit server or
session disables remain effective; independent clients keep independent locks.
Negated, advisory and historical queries retain their read guards.

Native workflow tests use the installed SDK's messages and a mocked service.
They prove request parsing, operation ordering, temporary references, update
masks, validation flags and one-attempt dispatch. Google still validates
required assets, campaign compatibility, ownership of referenced resources and
account eligibility. The report does not contain the complete private payloads
or live entity IDs, so these are structural fixtures rather than a live replay.
The clone fixture assumes brand guidelines are disabled and includes the
required `BUSINESS_NAME` and `LOGO` group links. Brand-guidelines-enabled campaigns
instead supply those through campaign assets; use the destination campaign's
actual configuration. See Google's [asset requirements](https://developers.google.com/google-ads/api/performance-max/asset-requirements).
Google's [Performance Max request structure guide](https://developers.google.com/google-ads/api/performance-max/structure-requests)
explains the resource dependencies and atomic-create constraints.

The video wrapper uses [typed v25 metadata](https://developers.google.com/google-ads/api/reference/rpc/v25/YouTubeVideoUpload)
and the [documented resumable protocol](https://developers.google.com/google-ads/api/docs/assets/upload-videos).
[SDK 33's upload helper](https://github.com/googleads/google-ads-python/blob/33.0.0/google/ads/googleads/v25/services/services/you_tube_video_upload_service/transports/resumable_upload.py)
retries chunks internally even when initiation is called with `retry=None`.
A small explicit upload loop avoids global SDK patches and
automatic write retries, including authentication replay. It validates Google's
session URL before sending credentials, respects chunk granularity, bounds and
redacts HTTP errors, and reports confirmed bytes and uncertain outcomes.
Successful uploads retain a status call even when subsequent processing reads
fail or the optional wait expires.

The redaction guarantee covers tool results and surfaced errors. Independent
review reproduced sensitive headers and resumable URLs in opt-in dependency
DEBUG logging. Leave DEBUG logging disabled for Google Auth and HTTP libraries
during uploads; this change does not alter global logging configuration.

## Review and local validation

GPT-6.1-Sol agents with maximum reasoning implemented and independently reviewed
the changes. Review regressions cover authorization and scoped visibility rules,
ambiguous upload/read routing, strict MCP snapshot offsets, refreshed-token
redaction, processing deadlines and PMax asset requirements. The full suite
also exercises direct MCP calls and the `call_tool` proxy with mutations locked.

PR review identified discovery errors involving asset-level reads, dedicated
creation tools, upload-record exports, asset-group clone/move requests and
explicit history. Regression coverage checks the selected tool, exclusions,
mutation visibility and actual MCP results in both lock states. It also covers
neighboring requests involving asset-group audience signals, individual asset
metrics, campaign names, budget amounts and mixed configuration/performance
history. Direct/proxy locks, component authorization, client isolation and
advisory guards remain part of the verification; discovery never creates an
Ads client.

The follow-up regression file checks 113 requests in both lock states, including
inferred history without the word "history", reordered date context and genuine
current instructions that refer to past performance. It also covers date-free
record exports, planned changes, recommendation subjects versus attribution,
and current write instructions combined with exports, including ordinary search
fallbacks. Field lists containing words such as "bid" and "label" remain reads;
explicit instructions to bid or label retain their mutation tools. Existing
order-independent CPC/CPA history tests remain unchanged and pass alongside
target-ROAS history and separately requested performance metrics.

Repeated fixes and independent reviews ended with two GPT-6.1-Sol maximum
reasoning reviews finding no remaining actionable issues in the reviewed scope
on the same frozen source. The semantic review passed 155 independent decisions
and 318 MCP searches; the integration review passed 254 MCP searches and nine
access-focused tests, plus scoped authorization, direct/proxy execution guards,
client isolation and relocking checks. All checks were offline.

| Check | Result |
| --- | --- |
| `uv sync --locked` | Passed using the unchanged lockfile. |
| `uv run --locked pyink --check .` | Passed; 108 files unchanged. |
| `uv run --locked pylint ads_mcp tests --fail-under=9.5` | Passed; 9.88/10. Existing warnings remain. |
| `uv run --locked pytest -q --tb=short` | 3,245 passed, 17 skipped; one upstream Authlib deprecation warning. |
| `git diff --check` | Passed. |

These are local offline results. GitHub CI remains the deployment gate, and
mocked request tests do not establish Google acceptance for live resources.

## Client rollout and remaining live checks

Nick must reconnect to the fixed revision before testing these tools. `pipx run`
can reuse a cached environment even after a remote branch changes. Pin the
reviewed Git commit in the configured spec, or rebuild with `--no-cache` (newer
pipx also offers `--refresh`), then reconnect. See the
[pipx cache documentation](https://pipx.pypa.io/stable/tutorial/run-applications.html#run-cache).
The presence of `get_gaql_snapshot_page` confirms the new surface; the unchanged
package version alone cannot identify the installed commit.

No live account changes or video uploads were made by this implementation.
Google's validate-only acceptance of the exact private campaigns, a real video
reaching `PROCESSED`, and Nick's restarted installation require live checks.
Uploads have no validate-only API. A live check needs an authorized customer,
video file and title; after `PROCESSED`, its `video_id` can be used in a
validate-only YouTube asset request before any asset/ad creation.

API version, dependencies, package version and generated field/view metadata
are unchanged. The handwritten tool guide, registration map and tool count are
updated to 147.
