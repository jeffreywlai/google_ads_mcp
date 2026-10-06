"""Single registry of public tool modules for both server transports."""

from ads_mcp.tools import account_services
from ads_mcp.tools import ad_groups
from ads_mcp.tools import ads
from ads_mcp.tools import api
from ads_mcp.tools import assets
from ads_mcp.tools import audiences
from ads_mcp.tools import campaigns
from ads_mcp.tools import changes
from ads_mcp.tools import conversions
from ads_mcp.tools import docs
from ads_mcp.tools import goals
from ads_mcp.tools import keyword_planner
from ads_mcp.tools import keywords
from ads_mcp.tools import labels
from ads_mcp.tools import negatives
from ads_mcp.tools import performance_max
from ads_mcp.tools import planning
from ads_mcp.tools import recommendations
from ads_mcp.tools import reporting
from ads_mcp.tools import resources
from ads_mcp.tools import search_terms
from ads_mcp.tools import simulations
from ads_mcp.tools import smart_campaigns

TOOL_MODULES = [
    account_services,
    ad_groups,
    ads,
    api,
    assets,
    audiences,
    campaigns,
    changes,
    conversions,
    docs,
    goals,
    keyword_planner,
    keywords,
    labels,
    negatives,
    performance_max,
    planning,
    recommendations,
    reporting,
    resources,
    search_terms,
    simulations,
    smart_campaigns,
]
