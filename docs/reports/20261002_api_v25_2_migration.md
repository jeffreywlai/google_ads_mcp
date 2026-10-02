# Google Ads API v25.2 migration and release coverage

Date: October 2, 2026.

The server selects the **v25 RPC surface with v25.2 features**, using locked
`google-ads==33.0.0` (`>=33.0.0,<34.0.0`) and project version `0.8.0`.
Google's latest listed minor release is v25.2, dated September 23, 2026.
Minor releases share the `v25` namespace; `v25.2` is not a separate RPC version.
The generated context contains **188 declared reporting views and 3,011 field
metadata entries**, with version markers updated after complete regeneration.
The integrated inventory is **143 public tools**, up from 117.
[Official release notes](https://developers.google.com/google-ads/api/docs/release-notes#v25_2).

This report supplements the earlier v24 usage-remediation report. Coverage
means an implemented request/query path backed by native SDK types or fetched
GoogleAdsField metadata. It does not establish that a restricted service,
account, study, asset type, or feature is eligible or available. Google controls
those conditions. No account mutations were performed for this migration.

## Upgrade guidance

Use `get_campaign_settings` for current settings. Its lifecycle result changed
from the removed legacy customer/campaign lifecycle resources to top-level
`account_goals` and per-campaign `campaign_goal_configs`. Account goals are
read from the conversion-tracking owner; campaign configs remain in the serving
account. Standard/custom conversion goals keep their separate result sections.
These sequential reads do not constitute an atomic account snapshot.

Saved queries against `customer_lifecycle_goal` or `campaign_lifecycle_goal`
now receive explicit migration errors. Change the resource and selected nested
fields deliberately: `goal.new_customer_acquisition_goal_settings` and
`campaign_goal_config.campaign_new_customer_acquisition_settings` replace the
legacy NCA model. Retention and loyalty settings use their corresponding native
oneof members. Value adjustments choose either an additional value or a
multiplier in each native oneof; supplying both is invalid.

For custom reports, use `execute_gaql` and current field/view metadata.
Existing complete-source paging and exact CSV exports remain available. New
lift reporting uses `list_lift_measurements`; custom metric/segment combinations
still follow Google's compatibility rules. A report without available results
cannot establish a zero effect or statistical significance.

Fixed planning services accept a request object or JSON string. Obtain exact
nested fields, enums, and oneofs with `get_planning_request_schema(tool_name)`.
Native resource updates use `get_resource_mutation_schema(operation_type)` and
`mutate_ads_resources`. Unified goals have dedicated `mutate_goals` and
`mutate_campaign_goal_configs` tools because they are absent from the native
GoogleAdsService bulk MutateOperation. Schema output describes serialization;
Google additionally checks immutable fields, required business inputs, and
eligibility.

New resource, goal, asset, and Smart draft mutations default to
`validate_only=true`. Account changes require explicit `validate_only=false`.
Inspect native partial-failure details: an execution receipt does not prove
that every operation succeeded. Mutations are never automatically retried.
Smart conversion generates only a PAUSED, INCOMPLETE PMax draft; GBP/image
options set to true are rejected before client access. Video crawl reads and
merges the repeated automation list to preserve unrelated entries; it cannot
prevent concurrent account edits. Advertiser attestation updates preserve
Google's output-only system attestation.

`fetch_incentives`, `apply_incentive`, and `create_product_link_invitation` expose
fixed native account-service methods. Their schemas are available through
`get_account_service_request_schema`. **Incentive redemption and invitation
creation have no validate-only API**: those calls apply the requested operation.
All mutation tools retain the session visibility guard.

## Breaking changes in v25

The following field mappings were checked against installed SDK 33 descriptors
and regenerated reporting metadata. Retired inputs require explicit migration;
resource/request guards do not silently rewrite them.

| Area | Required migration and implemented path |
|---|---|
| Lifecycle resources/settings | Removed `CustomerLifecycleGoal`, `CampaignLifecycleGoal`, their services, and legacy acquisition settings/enums. Read unified `goal`/`campaign_goal_config`; use the settings snapshot and dedicated goal mutation tools. |
| Lifecycle value settings | `additional_value` and `additional_high_lifetime_value` now share oneofs with `value_multiplier` and `high_lifetime_value_multiplier`. Native parsing enforces these choices in goal/config mutations. |
| Incentive names | Replace `FetchIncentiveRequest.type` and `Incentive.type` with `incentive_type`; replace `IncentiveOffer.type` with `offer_type`. Fixed incentive tools preserve native responses. |
| Incentive redemption | `ApplyIncentiveRequest` requires `selected_incentive_id` and `customer_id`. `apply_incentive` validates the selected ID and pins account scope. |
| Local Services contact | `ContactDetails.email` was removed. Read remaining `local_services_lead.contact_details` through GAQL; no legacy email value is synthesized. |
| Product-link invitations | Advertising-partner creation requires `advertising_partner_properties.allowed_domain`; the dedicated invitation tool checks it. |
| Creator search | Removed `GenerateCreatorInsightsRequest.search_brand`; use `search_topics` in `generate_creator_insights`. |
| Reach requests | Replace singular `Targeting.plannable_location_id` with `plannable_location_ids` and `cookie_frequency_cap` with `cookie_frequency_cap_setting` in `generate_reach_forecast`. |
| Benchmark responses | `customer_metrics` now uses native `CustomerMetrics`, not generic `Metrics`. Planning responses preserve its aggregate/share/rate/percentile structure and omit unavailable fields. |

Native schema references: [creator request](https://developers.google.com/google-ads/api/reference/rpc/v25/GenerateCreatorInsightsRequest),
[reach request](https://developers.google.com/google-ads/api/reference/rpc/v25/GenerateReachForecastRequest),
[unified campaign goals](https://developers.google.com/google-ads/api/reference/rpc/v25/CampaignGoalConfig).

## Feature coverage by release

The matrices map all v25, v25.1, and v25.2 release-note feature groups to concrete
server paths. Field/enum names and operation availability were checked against
SDK 33 and regenerated metadata. A dedicated convenience tool is not required
for every selectable field or native resource setting. `mutate_ads_resources`
uses a fixed allowlist of resource families, not arbitrary RPC invocation.

### v25

| Feature group | Server path and limit |
|---|---|
| Demand Gen animated-image automation | Native `ad_operation`/`ad_group_ad_operation` carries `GENERATE_ANIMATED_IMAGES_FROM_OTHER_ASSETS`; inspect existing ad settings through GAQL. Google's new-ad default remains API controlled. |
| Mutable synthetic-content information | `update_ad_synthetic_content_info` and `update_asset_synthetic_content_info` update advertiser attestations only; native ad/asset operations also expose the schema. |
| Loyalty retention and new-customer acquisition | Dedicated goal/config tools cover all three goal types, native value/multiplier settings, loyalty bid adjustments, PLA member-benefit flags, and campaign overrides. GAQL/snapshot reads expose returned configuration. |
| YouTube third-party conversion attribution | Native customer/campaign operations include conversion-attribution integration partners; GAQL exposes their current settings. Account/partner authorization remains Google's responsibility. |
| Local Services phone extension | Native `ContactDetails.phone_number_extension` is returned within GAQL contact details when available. |
| Incentive eligibility errors | Fixed redemption retains Google's billing-country, manager/suspension, pending/redemption-limit, recent-spend, and country-mismatch errors through centralized handling. |
| Trending channel details and creator consent | `generate_trending_insights` returns native related-channel metadata; `generate_creator_insights` preserves consent and consent-dependent statistics when returned. Missing nonpublic data is unavailable. |
| Ad sub-format and Shorts social metrics | GAQL supports `segments.ad_sub_format_type` with required `segments.ad_format_type`, plus `metrics.youtube_comments`, `youtube_likes`, and `youtube_shares` on supported views. |

### v25.1

| Feature group | Server path and limit |
|---|---|
| Text disclaimer assets | Native asset/association operations support `TEXT_DISCLAIMER`; GAQL exposes configured/served field types where selectable. |
| AI Max migration dates | GAQL and the settings snapshot expose `campaign.aca_migration_date_time` and `campaign.broad_match_migration_date_time`. |
| Brand Lift | `list_lift_measurements` covers config, campaign, age, device, gender, video, and flight configuration plus supported brand metrics. GAQL exposes measurement, survey, flight, and response-mode fields/enums. |
| Conversion Lift | The lift tool exposes study-level measured windows, categories, conversion-action inclusion, statistical bounds, and winner scores. GAQL exposes the new config/flight resources and full metric set; native experiment operations support the study link. Study creation/availability is not established by reporting support. |
| Smart conversion errors and production authorization | Central error handling retains named Smart conversion errors and `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`. SDK/schema support cannot grant production access. |
| Creator brand sentiment | `generate_creator_insights` accepts native supplemental brand-sentiment data and preserves its returned fields. |
| Benchmark categories and competitive metrics | Benchmark source/date/location/product discovery plus `generate_benchmarks_metrics` expose category filters and native customer aggregate/share/rate metrics. |
| Parental-status reach targeting | `generate_reach_forecast` and `list_plannable_products` expose supported parental-status inputs/options. |
| Campaign-specific app-goal recommendations | Existing recommendation list/apply tools expose the native payload and its `EffectiveAutomaticGoal` entries. |
| Original conversion value | GAQL and `get_campaign_performance` expose unadjusted biddable value separately from adjusted value. DEVICE/LOYALTY_MEMBERSHIP combinations omit the incompatible original-value metric explicitly. |
| Loyalty reporting and value rules | GAQL exposes `segments.loyalty_membership`; `segments.conversion_value_rule_primary_dimension` accepts `LOYALTY_MEMBERSHIP`. This is a reporting dimension, not a new `ConversionValueRuleSet.dimensions` input. |
| Vertical ad item bids | **Upstream gap:** `vertical_ads_item_bid` is announced but absent from SDK 33, current RPC/proto, and regenerated reporting fields. Native mutations reject it locally; `vertical_ads_item_group_rule_list` is a different field and is not a substitute. |

The announced bid-field gap was checked against the
[release-note entry](https://developers.google.com/google-ads/api/docs/release-notes#v25_1),
[current AdGroupCriterion RPC reference](https://developers.google.com/google-ads/api/reference/rpc/v25/AdGroupCriterion),
and [Google's v25 proto](https://raw.githubusercontent.com/googleapis/googleapis/master/google/ads/googleads/v25/resources/ad_group_criterion.proto).
Locally, `AdGroupCriterion.pb().DESCRIPTOR.fields_by_name` in SDK 33.0.0 has no
`vertical_ads_item_bid`, and `ads_mcp/context/fields.yaml` has no
`ad_group_criterion.vertical_ads_item_bid` entry. The SDK contains the distinct
`vertical_ads_item_group_rule_list`; reporting metadata exposes its
`ad_group_criterion.vertical_ads_item_group_rule_list.shared_set` child.
Support for the announced bid field requires a published native schema; this
migration does not invent one.

### v25.2

| Feature group | Server path and limit |
|---|---|
| Asset-group URL options | `update_asset_group_url_options` exposes tracking templates, custom parameters, and final URL suffixes, with explicit clear/preserve behavior. |
| Automated video crawl | `update_campaign_video_crawl_settings` supports LANDING_PAGE/SOCIAL/YOUTUBE URLs and enabled flags while preserving other automation entries. |
| Lifecycle multiplier errors | Dedicated goal/config services preserve missing/lower high-lifetime-value multiplier errors and campaign-type restrictions. Native oneofs are validated locally; account/value eligibility is checked by Google. |
| Smart-to-PMax draft generation | `generate_pmax_draft_campaign` returns native validation information and actual generated campaign/budget/asset-group/asset names. Validation-only is the default; unsupported GBP/image flags are rejected. |
| Firebase ad-impression revenue conversions | Native conversion-action operations accept Android/iOS Firebase ad-impression types and `IN_APP_AD_REVENUE`; GAQL exposes supported action settings and reports. |
| Business Profile sync errors | Asset-set resource operations retain named `LOCATION_SYNC` validation errors for email, OAuth, account removal, and access; metadata support does not grant Business Profile permissions. |
| Benchmark percentiles/open quarters | Native planning supports percentile tiers, all-advertiser/category requests, and the distinct all-metrics date coverage. Share/rate values outside that coverage stay absent; `NO_METRICS_FOUND` remains a service error. |
| Creator channel handles | `generate_creator_insights` accepts native YouTube channel handles as an alternative channel-search input. |
| Search performance-bid recommendations | Existing list/apply tools include `RAISE_TARGET_CPA_PERFORMANCE_BID_TOO_LOW` and `LOWER_TARGET_ROAS_PERFORMANCE_BID_TOO_LOW`, with native multiplier parameters. |
| Hotel itinerary reporting | GAQL exposes advance-booking window, length of stay, booking start date/day, and user-set dates on supported customer/campaign/ad-group views. |
| Vertical ads reporting | GAQL exposes average booking value, potential impressions, price-difference percentage, price tier, rate-rule ID, and rate type on supported views. |

Benchmark request details remain native:
[GenerateBenchmarksMetricsRequest](https://developers.google.com/google-ads/api/reference/rpc/v25/GenerateBenchmarksMetricsRequest).

The fixed resource mutation path also covers carried-forward native features,
such as Demand Gen Maps channels, experiment types/arms, conversion value
rules, assets and associations. API eligibility and mutable-field rules still
apply; the upgrade does not create bespoke tools for every field.

## Live lift field workaround

On October 2, 2026, the full CONFIG read returned `500 Internal error encountered`.
Two minimal config/flight resource queries succeeded. Individual config-field
queries isolated the failure to:

```sql
SELECT lift_measurement_config.resource_name,
       lift_measurement_config.campaigns
FROM lift_measurement_config LIMIT 1
```

The other ten config fields succeeded separately, and the sorted resource-name
and config-ID query succeeded. After the omission, live CONFIG, FLIGHT, BRAND,
and CONVERSION reads succeeded with empty study results; CAMPAIGN returned
association rows. The cause inside Google's service is unknown.
The dedicated CONFIG report excludes this parent field and explicitly reports
its omission; use `dimension="CAMPAIGN"` for the separate campaign-association
view. The published field remains in metadata and accessible for custom GAQL;
no response, association list, or zero metric is invented to hide the failure.

## Public tool additions

| Family | Added tools |
|---|---|
| Planning: 12 | `get_planning_request_schema`, `list_benchmarks_available_dates`, `list_benchmarks_sources`, `list_benchmarks_locations`, `list_benchmarks_products`, `generate_benchmarks_metrics`, `generate_creator_insights`, `generate_trending_insights`, `list_audience_insights_attributes`, `generate_reach_forecast`, `list_plannable_products`, `list_plannable_locations` |
| Focused mutations: 7 | `generate_pmax_draft_campaign`, `update_asset_group_url_options`, `update_campaign_video_crawl_settings`, `update_ad_synthetic_content_info`, `update_asset_synthetic_content_info`, `mutate_goals`, `mutate_campaign_goal_configs` |
| Lift: 1 | `list_lift_measurements` |
| Native resource mutations: 2 | `mutate_ads_resources`, `get_resource_mutation_schema` |
| Account services: 4 | `fetch_incentives`, `apply_incentive`, `create_product_link_invitation`, `get_account_service_request_schema` |

## Historical query compatibility and validation status

The anonymized **122-query historical fixture remains unchanged**. Of the 106
queries recorded as successful under v24, 103 retain v25 preflight acceptance.
Three formerly successful lifecycle queries (`q018`, `q022`, `q027`) now fail
with explicit resource/field migration guidance. A fourth legacy lifecycle
query (`q019`) already failed under v24 and now receives the same migration
category. Captured account-local dates and expected historical failures remain
preserved. These are offline query-compatibility checks, not replay of historical
account results or proof that Google will return identical data.

SDK descriptors, generated metadata counts/version markers, and focused
native-message regression coverage have been inspected. Restricted planning,
incentive, invitation, and mutation workflows are
**not claimed to have been validated live**. Current read-only v25 smoke checks
are recorded below; an empty study response does not establish lift-result
eligibility or statistical validity. Earlier v24 evidence remains historical.

Final local integration gates on October 2, 2026:

- `uv sync --locked`: passed with SDK 33.0.0 and FastMCP 3.2.x.
- `uv run pyink --check .`: passed, 95 files unchanged.
- `uv run pylint ads_mcp tests --fail-under=9.5`: passed, 9.87/10.
- `uv run pytest -q`: **2,665 passed, 17 skipped**. The ordinary suite remains
  offline; opt-in live tests were not enabled for this command.
- Registered inventory: **143**; the guide has the same tool set. Startup,
  read-only routing visibility, schema parsing, migration hints, native zero
  presence, mask validation, pagination, and exact CSV paths passed.
- Independent Sol 6.1 / max review and complementary reporting review completed.
  Confirmed startup imports, public-schema credential independence,
  recommendation parameter normalization, raw goal-mask validation, and
  unavailable lift-value handling were fixed with regressions. No unresolved
  P1/P2 findings remain in the reviewed implementation.

An actual stdio MCP client using the locked v25 SDK completed **15 read-only
checks**: `list_recommendations`, `list_asset_group_assets`, five
`list_lift_measurements` variants (CONFIG, CAMPAIGN, FLIGHT, BRAND, CONVERSION),
`get_campaign_performance`, `summarize_customer_match_jobs`,
`get_campaign_settings`, `compare_performance_periods`,
`compare_performance_around_changes`, `list_geographic_performance`,
`list_campaign_simulations`, and `list_ad_group_simulations`. The connection
also remained usable after an intentional local GAQL validation error.
Identifiers, credentials, and account result contents are omitted here.

These are local CI-equivalent gates and targeted live acceptance, not a claim
that GitHub CI ran or that restricted services and actual lift statistics were
validated. No mutation RPC was executed.
