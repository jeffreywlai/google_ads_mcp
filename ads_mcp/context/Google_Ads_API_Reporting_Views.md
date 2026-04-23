# Reports

## Overview

These pages serve as reference for the resources that can be queried in the Google Ads API using `GoogleAdsService.SearchStream` or `GoogleAdsService.Search`.

Each resource name has its own page. On each page, the following lists are shown:

* Artifacts (resources, segments, or metrics) that can be used in the same `SELECT` clause with the resource
* Attributed resources whose fields will not segment metrics, when included in the same `SELECT` and `WHERE` clauses of these resource fields

Presented after those lists are the tables consisting of names of resource fields, segments, and metrics that can be included in a search query, when this resource is specified in the `FROM` clause. Each name is linked to the table that contains its metadata:

* `category`
* `data type`
* `type URL`
* `filterable`
* `selectable`
* `sortable`
* `repeated`

## Metadata

**Filterable**

This row indicates whether the field can be used in the conditions of the `WHERE` clause of the GAQL query.

**Selectable**

This row indicates whether the field can be specified in the `SELECT` clause of the GAQL query.

**Sortable**

This row indicates whether the field can be sorted and used in the `ORDER BY` clause of the GAQL query.

**Repeated**

This row indicates whether the field is repeated, which should be handled as a list.

## List of all resources

Resource types

### accessible\_bidding\_strategy

Represents a view of BiddingStrategies owned by and shared with the customer. In contrast to `BiddingStrategy`, this resource includes strategies owned by managers of the customer and shared with this customer - in addition to strategies owned by this customer. This resource does not provide metrics and only exposes a limited subset of the `BiddingStrategy` attributes.

### account\_budget

An account-level budget. It contains information about the budget itself, as well as the most recently approved changes to the budget and proposed changes that are pending approval. The proposed changes that are pending approval, if any, are found in 'pending\_proposal'. Effective details about the budget are found in fields prefixed 'approved\_', 'adjusted\_' and those without a prefix. Since some effective details may differ from what the user had originally requested (for example, spending limit), these differences are juxtaposed through 'proposed\_', 'approved\_', and possibly 'adjusted\_' fields. This resource is mutated using `AccountBudgetProposal` and cannot be mutated directly. A budget may have at most one pending proposal at any given time. It is read through pending\_proposal. Once approved, a budget may be subject to adjustments, such as credit adjustments. Adjustments create differences between the 'approved' and 'adjusted' fields, which would otherwise be identical.

### account\_budget\_proposal

An account-level budget proposal. All fields prefixed with 'proposed' may not necessarily be applied directly. For example, proposed spending limits may be adjusted before their application. This is true if the 'proposed' field has an 'approved' counterpart, for example, spending limits. Note that the proposal type (proposal\_type) changes which fields are required and which must remain empty.

### account\_link

Represents the data sharing connection between a Google Ads account and another account

### ad

An ad.

### ad\_group

An ad group.

### ad\_group\_ad

An ad group ad.

### ad\_group\_ad\_asset\_combination\_view

A view on the usage of ad group ad asset combination. Now we only support AdGroupAdAssetCombinationView for Responsive Search Ads, with more ad types planned for the future.

### ad\_group\_ad\_asset\_view

A link between an AdGroupAd and an Asset. AdGroupAdAssetView supports AppAds, Demand Gen campaigns, and Responsive Search Ads.

### ad\_group\_ad\_label

A relationship between an ad group ad and a label.

### ad\_group\_asset

A link between an ad group and an asset.

### ad\_group\_asset\_set

AdGroupAssetSet is the linkage between an ad group and an asset set. Creating an AdGroupAssetSet links an asset set with an ad group.

### ad\_group\_audience\_view

An ad group audience view. Includes performance data from interests and remarketing lists for Display Network and YouTube Network ads, and remarketing lists for search ads (RLSA), aggregated at the audience level.

### ad\_group\_bid\_modifier

Represents an ad group bid modifier.

### ad\_group\_criterion

An ad group criterion. The ad\_group\_criterion report only returns criteria that were explicitly added to the ad group.

### ad\_group\_criterion\_customizer

A customizer value for the associated CustomizerAttribute at the AdGroupCriterion level.

### ad\_group\_criterion\_label

A relationship between an ad group criterion and a label.

### ad\_group\_criterion\_simulation

An ad group criterion simulation. Supported combinations of advertising channel type, criterion type, simulation type, and simulation modification method are detailed below respectively. Hotel AdGroupCriterion simulation operations starting in V5. 1. DISPLAY - KEYWORD - CPC\_BID - UNIFORM 2. SEARCH - KEYWORD - CPC\_BID - UNIFORM 3. SHOPPING - LISTING\_GROUP - CPC\_BID - UNIFORM 4. HOTEL - LISTING\_GROUP - CPC\_BID - UNIFORM 5. HOTEL - LISTING\_GROUP - PERCENT\_CPC\_BID - UNIFORM

### ad\_group\_customizer

A customizer value for the associated CustomizerAttribute at the AdGroup level.

### ad\_group\_label

A relationship between an ad group and a label.

### ad\_group\_simulation

An ad group simulation. Supported combinations of advertising channel type, simulation type and simulation modification method is detailed below respectively. 1. SEARCH - CPC\_BID - DEFAULT 2. SEARCH - CPC\_BID - UNIFORM 3. SEARCH - TARGET\_CPA - UNIFORM 4. SEARCH - TARGET\_ROAS - UNIFORM 5. DISPLAY - CPC\_BID - DEFAULT 6. DISPLAY - CPC\_BID - UNIFORM 7. DISPLAY - TARGET\_CPA - UNIFORM

### ad\_parameter

An ad parameter that is used to update numeric values (such as prices or inventory levels) in any text line of an ad (including URLs). There can be a maximum of two AdParameters per ad group criterion. (One with parameter\_index = 1 and one with parameter\_index = 2.) In the ad the parameters are referenced by a placeholder of the form "{param#:value}". For example, "{param1:$17}"

### ad\_schedule\_view

An ad schedule view summarizes the performance of campaigns by AdSchedule criteria.

### age\_range\_view

An age range view.

### ai\_max\_search\_term\_ad\_combination\_view

Ai Max search term ad combination view.

### android\_privacy\_shared\_key\_google\_ad\_group

An Android privacy shared key view for Google ad group key.

### android\_privacy\_shared\_key\_google\_campaign

An Android privacy shared key view for Google campaign key.

### android\_privacy\_shared\_key\_google\_network\_type

An Android privacy shared key view for Google network type key.

### app\_top\_combination\_view

A view resource in the App Top Combination Report.

### applied\_incentive

Represents an applied incentive.

### asset

Asset is a part of an ad which can be shared across multiple ads. It can be an image (ImageAsset), a video (YoutubeVideoAsset), etc. Assets are immutable and cannot be removed. To stop an asset from serving, remove the asset from the entity that is using it.

### asset\_field\_type\_view

An asset field type view. This view reports non-overcounted metrics for each asset field type when the asset is used as extension.

### asset\_group

An asset group. AssetGroupAsset is used to link an asset to the asset group. AssetGroupSignal is used to associate a signal to an asset group.

### asset\_group\_asset

AssetGroupAsset is the link between an asset and an asset group. Adding an AssetGroupAsset links an asset with an asset group.

### asset\_group\_listing\_group\_filter

AssetGroupListingGroupFilter represents a listing group filter tree node in an asset group.

### asset\_group\_product\_group\_view

An asset group product group view.

### asset\_group\_signal

AssetGroupSignal represents a signal in an asset group. The existence of a signal tells the performance max campaign who's most likely to convert. Performance Max uses the signal to look for new people with similar or stronger intent to find conversions across Search, Display, Video, and more.

### asset\_group\_top\_combination\_view

A view on the usage of asset group asset top combinations.

### asset\_set

An asset set representing a collection of assets. Use AssetSetAsset to link an asset to the asset set.

### asset\_set\_asset

AssetSetAsset is the link between an asset and an asset set. Adding an AssetSetAsset links an asset with an asset set.

### asset\_set\_type\_view

An asset set type view. This view reports non-overcounted metrics for each asset set type. Child asset set types are not included in this report. Their stats are aggregated under the parent asset set type.

### audience

Audience is an effective targeting option that lets you intersect different segment attributes, such as detailed demographics and affinities, to create audiences that represent sections of your target segments.

### batch\_job

A list of mutates being processed asynchronously. The mutates are uploaded by the user. The mutates themselves aren't readable and the results of the job can only be read using BatchJobService.ListBatchJobResults.

### bidding\_data\_exclusion

Represents a bidding data exclusion. Bidding data exclusions can be set in client accounts only, and cannot be used in manager accounts.

### bidding\_seasonality\_adjustment

Represents a bidding seasonality adjustment. Cannot be used in manager accounts.

### bidding\_strategy

A bidding strategy.

### bidding\_strategy\_simulation

A bidding strategy simulation. Supported combinations of simulation type and simulation modification method are detailed below respectively. 1. TARGET\_CPA - UNIFORM 2. TARGET\_ROAS - UNIFORM

### billing\_setup

A billing setup, which associates a payments account and an advertiser. A billing setup is specific to one advertiser.

### call\_view

A call view that includes data for call tracking of call-only ads or call extensions.

### campaign

A campaign.

### campaign\_aggregate\_asset\_view

A campaign-level aggregate asset view that shows where the asset is linked, performamce of the asset and stats.

### campaign\_asset

A link between a Campaign and an Asset.

### campaign\_asset\_set

CampaignAssetSet is the linkage between a campaign and an asset set. Adding a CampaignAssetSet links an asset set with a campaign.

### campaign\_audience\_view

A campaign audience view. Includes performance data from interests and remarketing lists for Display Network and YouTube Network ads, and remarketing lists for search ads (RLSA), aggregated by campaign and audience criterion. This view only includes audiences attached at the campaign level.

### campaign\_bid\_modifier

Represents a bid-modifiable only criterion at the campaign level.

### campaign\_budget

A campaign budget shared amongst various budget recommendation types.

### campaign\_conversion\_goal

The biddability setting for the specified campaign only for all conversion actions with a matching category and origin.

### campaign\_criterion

A campaign criterion.

### campaign\_customizer

A customizer value for the associated CustomizerAttribute at the Campaign level.

### campaign\_draft

A campaign draft.

### campaign\_group

A campaign group.

### campaign\_goal\_config

A link between a campaign and a goal enabling campaign-specific optimization.

### campaign\_label

Represents a relationship between a campaign and a label.

### campaign\_lifecycle\_goal

Campaign level customer lifecycle goal settings.

### campaign\_search\_term\_insight

A Campaign search term view. Historical data is available starting March 2023.

### campaign\_search\_term\_view

Granular campaign-level search term performance, including cost metrics for each individual search term that triggered ads.

### campaign\_shared\_set

CampaignSharedSets are used for managing the shared sets associated with a campaign.

### campaign\_simulation

A campaign simulation. Supported combinations of advertising channel type, simulation type and simulation modification method is detailed below respectively. \* SEARCH - CPC\_BID - UNIFORM \* SEARCH - CPC\_BID - SCALING \* SEARCH - TARGET\_CPA - UNIFORM \* SEARCH - TARGET\_CPA - SCALING \* SEARCH - TARGET\_ROAS - UNIFORM \* SEARCH - TARGET\_IMPRESSION\_SHARE - UNIFORM \* SEARCH - BUDGET - UNIFORM \* SHOPPING - BUDGET - UNIFORM \* SHOPPING - TARGET\_ROAS - UNIFORM \* MULTI\_CHANNEL - TARGET\_CPA - UNIFORM \* MULTI\_CHANNEL - TARGET\_ROAS - UNIFORM \* DEMAND\_GEN - TARGET\_CPA - DEFAULT \* DISPLAY - TARGET\_CPA - UNIFORM \* PERFORMANCE\_MAX - TARGET\_CPA - UNIFORM \* PERFORMANCE\_MAX - TARGET\_ROAS - UNIFORM \* PERFORMANCE\_MAX - BUDGET - UNIFORM

### carrier\_constant

A carrier criterion that can be used in campaign targeting.

### cart\_data\_sales\_view

Cart data sales view. Provides information about products purchased when
conversions with cart data are implemented, including revenue, gross profit,
lead and cross-sell metrics, and Merchant Center product dimensions.

### change\_event

Describes the granular change of returned resources of certain resource types. Changes made through the UI or API in the past 30 days are included. Previous and new values of the changed fields are shown. ChangeEvent could have up to 3 minutes delay to reflect a new change.

### change\_status

Describes the status of returned resource. ChangeStatus could have up to 3 minutes delay to reflect a new change.

### channel\_aggregate\_asset\_view

A channel-level aggregate asset view that shows where the asset is linked, performamce of the asset and stats.

### click\_view

A click view with metrics aggregated at each click level, including both valid and invalid clicks. For non-Search campaigns, metrics.clicks represents the number of valid and invalid interactions. Queries including ClickView must have a filter limiting the results to one day and can be requested for dates back to 90 days before the time of the request.

### combined\_audience

Describe a resource for combined audiences which includes different audiences.

### content\_criterion\_view

A content criterion view.

### conversion\_action

A conversion action.

### conversion\_custom\_variable

A conversion custom variable.

### conversion\_goal\_campaign\_config

Conversion goal settings for a Campaign.

### conversion\_value\_rule

A conversion value rule

### conversion\_value\_rule\_set

A conversion value rule set

### currency\_constant

A currency constant.

### custom\_audience

A custom audience. This is a list of users by interest.

### custom\_conversion\_goal

Custom conversion goal that can make arbitrary conversion actions biddable.

### custom\_interest

A custom interest. This is a list of users by interest.

### customer

A customer.

### customer\_asset

A link between a customer and an asset.

### customer\_asset\_set

CustomerAssetSet is the linkage between a customer and an asset set. Adding a CustomerAssetSet links an asset set with a customer.

### customer\_client

A link between the given customer and a client customer. CustomerClients only exist for manager customers. All direct and indirect client customers are included, as well as the manager itself.

### customer\_client\_link

Represents customer client link relationship.

### customer\_conversion\_goal

Biddability control for conversion actions with a matching category and origin.

### customer\_customizer

A customizer value for the associated CustomizerAttribute at the Customer level.

### customer\_label

Represents a relationship between a customer and a label. This customer may not have access to all the labels attached to it. Additional CustomerLabels may be returned by increasing permissions with login-customer-id.

### customer\_lifecycle\_goal

Account level customer lifecycle goal settings.

### customer\_manager\_link

Represents customer-manager link relationship.

### customer\_negative\_criterion

A negative criterion for exclusions at the customer level.

### customer\_search\_term\_insight

A Customer search term view. Historical data is available starting March 2023.

### customer\_user\_access

Represents the permission of a single user onto a single customer.

### customer\_user\_access\_invitation

Represent an invitation to a new user on this customer account.

### customizer\_attribute

A customizer attribute. Use CustomerCustomizer, CampaignCustomizer, AdGroupCustomizer, or AdGroupCriterionCustomizer to associate a customizer attribute and set its value at the customer, campaign, ad group, or ad group criterion level, respectively.

### data\_link

Represents the data sharing connection between a Google Ads customer and another product's data.

### detail\_content\_suitability\_placement\_view

A detail content suitability placement view.

### detail\_placement\_view

A view with metrics aggregated by ad group and URL or YouTube video.

### detailed\_demographic

A detailed demographic: a particular interest-based vertical to be targeted to reach users based on long-term life facts.

### display\_keyword\_view

A display keyword view.

### distance\_view

A distance view with metrics aggregated by the user's distance from an advertiser's location extensions. Each DistanceBucket includes all impressions that fall within its distance and a single impression will contribute to the metrics for all DistanceBuckets that include the user's distance.

### domain\_category

A category generated automatically by crawling a domain. If a campaign uses the DynamicSearchAdsSetting, then domain categories will be generated for the domain. The categories can be targeted using WebpageConditionInfo.

### dynamic\_search\_ads\_search\_term\_view

A dynamic search ads search term view.

### expanded\_landing\_page\_view

A landing page view with metrics aggregated at the expanded final URL level.

### experiment

A Google ads experiment for users to experiment changes on multiple campaigns, compare the performance, and apply the effective changes.

### experiment\_arm

A Google ads experiment for users to experiment changes on multiple campaigns, compare the performance, and apply the effective changes.

### final\_url\_expansion\_asset\_view

Final URL expansion asset view with landing-page performance metrics.

### gender\_view

A gender view. The gender\_view resource reflects the effective serving state, rather than what criteria were added. An ad group without gender criteria by default shows to all genders, so all genders appear in gender\_view with stats.

### geo\_target\_constant

A geo target constant.

### geographic\_view

A geographic view. Geographic View includes all metrics aggregated at the country level, one row per country. It reports metrics at either actual physical location of the user or an area of interest. If other segment fields are used, you may get more than one row per country.

### goal

Representation of goals.

### group\_content\_suitability\_placement\_view

A group content suitability placement view.

### group\_placement\_view

A group placement view.

### hotel\_group\_view

A hotel group view.

### hotel\_performance\_view

A hotel performance view.

### hotel\_reconciliation

A hotel reconciliation. It contains conversion information from Hotel bookings to reconcile with advertiser records. These rows may be updated or canceled before billing through Bulk Uploads.

### income\_range\_view

An income range view.

### keyword\_plan

A Keyword Planner plan. Max number of saved keyword plans: 10000. It's possible to remove plans if limit is reached.

### keyword\_plan\_ad\_group

A Keyword Planner ad group. Max number of keyword plan ad groups per plan: 200.

### keyword\_plan\_ad\_group\_keyword

A Keyword Plan ad group keyword. Max number of keyword plan keywords per plan: 10000.

### keyword\_plan\_campaign

A Keyword Plan campaign. Max number of keyword plan campaigns per plan allowed: 1.

### keyword\_plan\_campaign\_keyword

A Keyword Plan Campaign keyword. Only negative keywords are supported for Campaign Keyword.

### keyword\_theme\_constant

A Smart Campaign keyword theme constant.

### keyword\_view

A keyword view.

### label

A label.

### landing\_page\_view

A landing page view with metrics aggregated at the unexpanded final URL level.

### language\_constant

A language.

### lead\_form\_submission\_data

Data from lead form submissions.

### life\_event

A life event: a particular interest-based vertical to be targeted to reach users when they are in the midst of important life milestones.

### local\_services\_employee

A local services employee resource.

### local\_services\_lead

Data from Local Services Lead. Contains details of Lead which is generated when user calls, messages or books service from advertiser.

### local\_services\_lead\_conversation

Data from Local Services Lead Conversation. Contains details of Lead Conversation which is generated when user calls, messages or books service from advertiser. These are appended to a Lead.

### local\_services\_verification\_artifact

A local services verification resource.

### location\_interest\_view

A location interest view summarizes the performance of ad group location interest criteria.

### location\_view

A location view summarizes the performance of campaigns by a Location criterion. If no Location criterion is set, no results are returned; instead, use geographic\_view or user\_location\_view for visitor location data.

### managed\_placement\_view

A managed placement view.

### matched\_location\_interest\_view

Reports metrics for locations where users showed interest and matched the advertiser's location interest targeting. This view is currently available for AI Max campaigns.

### media\_file

A media file.

### mobile\_app\_category\_constant

A mobile application category constant.

### mobile\_device\_constant

A mobile device constant.

### offline\_conversion\_upload\_client\_summary

Offline conversion upload summary at customer level.

### offline\_conversion\_upload\_conversion\_action\_summary

Offline conversion upload summary at conversion action level.

### offline\_user\_data\_job

A job containing offline user data of store visitors, or user list members that will be processed asynchronously. The uploaded data isn't readable and the processing results of the job can only be read using GoogleAdsService.Search/SearchStream.

### operating\_system\_version\_constant

A mobile operating system version or a range of versions, depending on `operator_type`.

### paid\_organic\_search\_term\_view

A paid organic search term view providing a view of search stats across ads and organic listings aggregated by search term at the ad group level.

### parental\_status\_view

A parental status view.

### per\_store\_view

A per store view. This view provides per store impression reach and local action conversion stats for advertisers.

### performance\_max\_placement\_view

A view with impression metrics for Performance Max campaign placements.

### product\_category\_constant

A Product Category.

### product\_group\_view

A product group view.

### product\_link

Represents the data sharing connection between a Google Ads customer and another product.

### product\_link\_invitation

Represents an invitation for data sharing connection between a Google Ads account and another account.

### qualifying\_question

Qualifying Questions for Lead Form.

### recommendation

A recommendation.

### recommendation\_subscription

Recommendation Subscription resource

### remarketing\_action

A remarketing action. A snippet of JavaScript code that will collect the product id and the type of page people visited (product page, shopping cart page, purchase page, general site visit) on an advertiser's website.

### search\_term\_view

A search term view with metrics aggregated by search term at the ad group level.

### shared\_criterion

A criterion belonging to a shared set.

### shared\_set

SharedSets are used for sharing criterion exclusions across multiple campaigns.

### shopping\_performance\_view

Shopping performance view. Provides Shopping campaign statistics aggregated at several product dimension levels. Product dimension values from Merchant Center such as brand, category, custom attributes, product condition and product type will reflect the state of each dimension as of the date and time when the corresponding event was recorded.

### shopping\_product

A shopping product from Google Merchant Center that can be advertised by campaigns. The resource returns currently existing products from Google Merchant Center accounts linked with the customer. A campaign includes a product by specifying its merchant id (or, if available, the Multi Client Account id of the merchant) in the `ShoppingSetting`, and can limit the inclusion to products having a specified feed label. Standard Shopping campaigns can also limit the inclusion through a `campaign_criterion.listing_scope`. Queries to this resource specify a scope: Account: - Filters on campaigns or ad groups are not specified. - All products from the linked Google Merchant Center accounts are returned. - Metrics and some fields (see the per-field documentation) are aggregated across all Shopping and Performance Max campaigns that include a product. Campaign: - An equality filter on `campaign` is specified. Supported campaign types are Shopping, Performance Max, Demand Gen, Video. - Only products that are included by the specified campaign are returned. - Metrics and some fields (see the per-field documentation) are restricted to the specified campaign. Ad group: - An equality filter on `ad group` and `campaign` is specified. Supported campaign types are Shopping, Demand Gen, Video. - Only products that are included by the specified campaign are returned. - Metrics and some fields (see the per-field documentation) are restricted to the specified ad group. Note that segmentation by date segments is not permitted and will return UNSUPPORTED\_DATE\_SEGMENTATION error. On the other hand, filtering on date segments is allowed.

### smart\_campaign\_search\_term\_view

A Smart campaign search term view.

### smart\_campaign\_setting

Settings for configuring Smart campaigns.

### targeting\_expansion\_view

A targeting expansion view with metrics for automated expansion over manual targeting.

### third\_party\_app\_analytics\_link

A data sharing connection, allowing the import of third party app analytics into a Google Ads Customer.

### topic\_constant

Use topics to target or exclude placements in the Google Display Network based on the category into which the placement falls (for example, "Pets & Animals/Pets/Dogs").

### topic\_view

A topic view.

### travel\_activity\_group\_view

A travel activity group view.

### travel\_activity\_performance\_view

A travel activity performance view.

### user\_interest

A user interest: a particular interest-based vertical to be targeted.

### user\_list

A user list. This is a list of users a customer may target.

### user\_list\_customer\_type

A user list customer type

### user\_location\_view

A user location view. User Location View includes all metrics aggregated at the country level, one row per country. It reports metrics at the actual physical location of the user by targeted or not targeted location. If other segment fields are used, you may get more than one row per country.

### video

A video.

### video\_enhancement

A video enhancement.

### webpage\_view

A webpage view.

### you\_tube\_video\_upload

A YouTube video upload resource.
