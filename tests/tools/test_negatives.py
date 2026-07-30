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

"""Tests for the negative keyword list management tools."""

from unittest import mock

from ads_mcp.tools import negatives
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
import pytest


CUSTOMER_ID = "1234567890"
SHARED_SET_ID = "111"
CAMPAIGN_ID = "222"


def _reference_count_response(reference_count):
  row = mock.Mock()
  row.shared_set.reference_count = reference_count
  return [mock.Mock(results=[row])]


def _google_ads_exception(message):
  error = mock.Mock()
  error.__str__ = lambda self: message
  return GoogleAdsException(
      error=mock.Mock(),
      failure=mock.Mock(errors=[error]),
      call=mock.Mock(),
      request_id="test",
  )


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.negatives.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


# ---------------------------------------------------------------------------
# Shared Sets
# ---------------------------------------------------------------------------


class TestListSharedSets:

  def test_returns_shared_sets(self, mock_ads_client):
    mock_row = mock.Mock()
    mock_row.shared_set.id = 111
    mock_row.shared_set.name = "My Negatives"
    mock_row.shared_set.member_count = 5

    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = [
        mock.Mock(results=[mock_row])
    ]

    result = negatives.list_shared_sets(CUSTOMER_ID)
    assert result == {
        "shared_sets": [
            {"id": "111", "name": "My Negatives", "member_count": 5}
        ]
    }

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = []

    negatives.list_shared_sets(CUSTOMER_ID, login_customer_id="999")
    mock_ads_client._mock_get.assert_any_call("999")

  def test_normalizes_enum_filters_before_search(self, mock_ads_client):
    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = []

    negatives.list_shared_sets(CUSTOMER_ID)

    sent_query = mock_ads_service.search_stream.call_args.kwargs["query"]
    assert "shared_set.type = NEGATIVE_KEYWORDS" in sent_query
    assert "shared_set.status = ENABLED" in sent_query

  def test_raises_tool_error_on_api_error(self, mock_ads_client):
    mock_ads_service = mock_ads_client.get_service.return_value
    error = mock.Mock()
    error.__str__ = lambda self: "API error"
    exc = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_ads_service.search_stream.side_effect = exc

    with pytest.raises(ToolError):
      negatives.list_shared_sets(CUSTOMER_ID)


class TestCreateSharedSet:

  def test_creates_shared_set(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_shared_sets.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/sharedSets/111")
    ]

    result = negatives.create_shared_set(CUSTOMER_ID, "Test List")
    assert result == {"resource_name": "customers/123/sharedSets/111"}
    assert mock_op.create.name == "Test List"


class TestDeleteSharedSet:

  def test_deletes_shared_set(self, mock_ads_client):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(0),
        [],
    ]
    shared_set_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]
    shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )
    mock_response = shared_set_service.mutate_shared_sets.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/sharedSets/111")
    ]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)
    assert result == {"resource_name": "customers/123/sharedSets/111"}

  def test_preflight_diagnostic_failure_does_not_block_delete(
      self, mock_ads_client
  ):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _google_ads_exception("diagnostic unavailable"),
        [],
    ]
    shared_set_service = mock.Mock()
    shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )
    shared_set_service.mutate_shared_sets.return_value.results = [
        mock.Mock(resource_name="customers/123/sharedSets/111")
    ]
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result == {"resource_name": "customers/123/sharedSets/111"}
    shared_set_service.mutate_shared_sets.assert_called_once()

  def test_returns_detach_instructions_for_attached_campaigns(
      self, mock_ads_client
  ):
    row = mock.Mock()
    row.campaign.id = 222
    row.campaign.name = "Brand Search"
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(1),
        [mock.Mock(results=[row])],
    ]
    shared_set_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result == {
        "deleted": False,
        "shared_set_id": SHARED_SET_ID,
        "reference_count": 1,
        "attached_campaigns": [
            {
                "customer_id": CUSTOMER_ID,
                "campaign_id": CAMPAIGN_ID,
                "name": "Brand Search",
            }
        ],
        "attachments_complete": True,
        "known_detach_calls": [
            {
                "tool": "detach_shared_set_from_campaign",
                "arguments": {
                    "customer_id": CUSTOMER_ID,
                    "campaign_id": CAMPAIGN_ID,
                    "shared_set_id": SHARED_SET_ID,
                    "login_customer_id": None,
                },
            }
        ],
        "next_step": (
            "Call each entry in known_detach_calls first, then retry "
            "delete_shared_set."
        ),
    }
    query = ads_service.search_stream.call_args.kwargs["query"]
    assert "FROM campaign_shared_set" in query
    assert (
        "campaign_shared_set.shared_set = "
        "'customers/1234567890/sharedSets/111'" in " ".join(query.split())
    )
    shared_set_service.mutate_shared_sets.assert_not_called()

  def test_recovers_if_shared_set_becomes_attached_during_delete(
      self, mock_ads_client
  ):
    row = mock.Mock()
    row.campaign.id = 222
    row.campaign.name = "Brand Search"
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(0),
        [],
        _reference_count_response(1),
        [mock.Mock(results=[row])],
    ]
    shared_set_service = mock.Mock()
    shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )
    error = mock.Mock()
    error.__str__ = lambda self: "SHARED_SET_IN_USE"
    shared_set_service.mutate_shared_sets.side_effect = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result["deleted"] is False
    assert result["attached_campaigns"] == [
        {
            "customer_id": CUSTOMER_ID,
            "campaign_id": CAMPAIGN_ID,
            "name": "Brand Search",
        }
    ]
    assert result["attachments_complete"] is True
    assert result["known_detach_calls"][0]["tool"] == (
        "detach_shared_set_from_campaign"
    )
    assert ads_service.search_stream.call_count == 4

  def test_reports_cross_account_references_without_empty_detach_loop(
      self, mock_ads_client
  ):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(2),
        [],
    ]
    shared_set_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result["deleted"] is False
    assert result["reference_count"] == 2
    assert result["attached_campaigns"] == []
    assert result["attachments_complete"] is False
    assert "managed client accounts" in result["warning"]
    assert "list_campaign_shared_sets" in result["next_step"]
    assert "managed_customer_discovery" in result["next_step"]
    assert result["managed_customer_discovery"] == {
        "tool": "execute_gaql",
        "arguments": {
            "customer_id": CUSTOMER_ID,
            "login_customer_id": None,
            "query": (
                "SELECT customer_client.id, "
                "customer_client.descriptive_name, customer_client.level "
                "FROM customer_client WHERE customer_client.level > 0"
            ),
        },
    }
    shared_set_service.mutate_shared_sets.assert_not_called()

  def test_incomplete_diagnostics_detach_known_campaigns_before_discovery(
      self, mock_ads_client
  ):
    row = mock.Mock()
    row.campaign.id = 222
    row.campaign.name = "Known Campaign"
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _google_ads_exception("reference lookup unavailable"),
        [mock.Mock(results=[row])],
    ]
    mock_ads_client.get_service.return_value = ads_service

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result["attachments_complete"] is False
    assert result["known_detach_calls"] == [
        {
            "tool": "detach_shared_set_from_campaign",
            "arguments": {
                "customer_id": CUSTOMER_ID,
                "campaign_id": CAMPAIGN_ID,
                "shared_set_id": SHARED_SET_ID,
                "login_customer_id": None,
            },
        }
    ]
    assert result["next_step"].startswith(
        "Call each entry in known_detach_calls first. "
    )
    assert "Run managed_customer_discovery next." in result["next_step"]

  def test_cross_account_guidance_preserves_login_context(
      self, mock_ads_client
  ):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(1),
        [],
    ]
    shared_set_service = mock.Mock()
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(
        CUSTOMER_ID,
        SHARED_SET_ID,
        login_customer_id="999",
    )

    discovery_arguments = result["managed_customer_discovery"]["arguments"]
    assert discovery_arguments["customer_id"] == CUSTOMER_ID
    assert discovery_arguments["login_customer_id"] == "999"
    assert "login_customer_id=999" in result["next_step"]

  def test_cross_account_guidance_reuses_default_login_context(
      self, mock_ads_client
  ):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(1),
        [],
    ]
    mock_ads_client.get_service.return_value = ads_service

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    discovery_arguments = result["managed_customer_discovery"]["arguments"]
    assert discovery_arguments["login_customer_id"] is None
    assert "configured default is reused" in result["next_step"]
    assert f"login_customer_id={CUSTOMER_ID}" not in result["next_step"]

  def test_reports_incomplete_cross_account_usage_after_delete_race(
      self, mock_ads_client
  ):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(0),
        [],
        _reference_count_response(2),
        [],
    ]
    shared_set_service = mock.Mock()
    shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )
    error = mock.Mock()
    error.__str__ = lambda self: "SHARED_SET_IN_USE"
    shared_set_service.mutate_shared_sets.side_effect = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result["reference_count"] == 2
    assert result["attached_campaigns"] == []
    assert result["attachments_complete"] is False
    assert "2 campaign references" in result["warning"]
    assert "managed client accounts" in result["warning"]
    assert "list_campaign_shared_sets" in result["next_step"]

  def test_preserves_in_use_response_when_reference_diagnostic_fails(
      self, mock_ads_client
  ):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(0),
        [],
        _google_ads_exception("reference lookup unavailable"),
        [],
    ]
    shared_set_service = mock.Mock()
    shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )
    shared_set_service.mutate_shared_sets.side_effect = _google_ads_exception(
        "SHARED_SET_IN_USE"
    )
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result["deleted"] is False
    assert result["attachments_complete"] is False
    assert result["diagnostic_errors"] == [
        {
            "check": "shared_set.reference_count",
            "error": "reference lookup unavailable",
        }
    ]
    assert "rather than retrying the identical delete" in result["warning"]
    assert "list_campaign_shared_sets" in result["next_step"]
    shared_set_service.mutate_shared_sets.assert_called_once()

  def test_preserves_in_use_response_when_attachment_diagnostic_fails(
      self, mock_ads_client
  ):
    ads_service = mock.Mock()
    ads_service.search_stream.side_effect = [
        _reference_count_response(0),
        [],
        _reference_count_response(2),
        _google_ads_exception("attachment lookup unavailable"),
    ]
    shared_set_service = mock.Mock()
    shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )
    shared_set_service.mutate_shared_sets.side_effect = _google_ads_exception(
        "SHARED_SET_IN_USE"
    )
    mock_ads_client.get_service.side_effect = lambda name: {
        "GoogleAdsService": ads_service,
        "SharedSetService": shared_set_service,
    }[name]

    result = negatives.delete_shared_set(CUSTOMER_ID, SHARED_SET_ID)

    assert result["deleted"] is False
    assert result["reference_count"] == 2
    assert result["attached_campaigns"] == []
    assert result["attachments_complete"] is False
    assert result["diagnostic_errors"] == [
        {
            "check": "campaign_shared_set attachments",
            "error": "attachment lookup unavailable",
        }
    ]
    assert "rather than retrying the identical delete" in result["warning"]
    assert "list_campaign_shared_sets" in result["next_step"]
    shared_set_service.mutate_shared_sets.assert_called_once()


# ---------------------------------------------------------------------------
# Shared Set Keywords
# ---------------------------------------------------------------------------


class TestListSharedSetKeywords:

  def test_returns_keywords(self, mock_ads_client):
    mock_row = mock.Mock()
    mock_row.shared_criterion.criterion_id = 333
    mock_row.shared_criterion.keyword.text = "free stuff"
    mock_row.shared_criterion.keyword.match_type.name = "BROAD"

    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = [
        mock.Mock(results=[mock_row])
    ]

    result = negatives.list_shared_set_keywords(CUSTOMER_ID, SHARED_SET_ID)
    assert result == {
        "keywords": [
            {
                "criterion_id": "333",
                "text": "free stuff",
                "match_type": "BROAD",
            }
        ]
    }


class TestAddSharedSetKeywords:

  def test_adds_keywords(self, mock_ads_client):
    mock_service = mock.Mock()
    mock_shared_set_service = mock.Mock()
    mock_shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )

    def get_service(name):
      if name == "SharedCriterionService":
        return mock_service
      return mock_shared_set_service

    mock_ads_client.get_service.side_effect = get_service
    mock_enum_value = mock.Mock(value=1)
    mock_ads_client.enums.KeywordMatchTypeEnum.__getitem__ = mock.Mock(
        return_value=mock_enum_value
    )
    mock_response = mock_service.mutate_shared_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/sharedCriteria/111~1")
    ]

    keywords = [{"text": "cheap", "match_type": "EXACT"}]
    result = negatives.add_shared_set_keywords(
        CUSTOMER_ID, SHARED_SET_ID, keywords
    )
    assert result == {"resource_names": ["customers/123/sharedCriteria/111~1"]}


class TestRemoveSharedSetKeywords:

  def test_removes_keywords(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_shared_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/sharedCriteria/111~333")
    ]

    result = negatives.remove_shared_set_keywords(
        CUSTOMER_ID, SHARED_SET_ID, ["333"]
    )
    assert result == {
        "resource_names": ["customers/123/sharedCriteria/111~333"]
    }

  def test_accepts_single_criterion_id_string(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_shared_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/sharedCriteria/111~333")
    ]
    operation = mock.Mock()
    mock_ads_client.get_type.return_value = operation

    negatives.remove_shared_set_keywords(CUSTOMER_ID, SHARED_SET_ID, "333")

    assert operation.remove == "customers/1234567890/sharedCriteria/111~333"
    request_operations = mock_service.mutate_shared_criteria.call_args.kwargs[
        "operations"
    ]
    assert request_operations == [operation]

  def test_rejects_malformed_criterion_ids(self, mock_ads_client):
    with pytest.raises(ToolError, match="criterion_ids must be an integer"):
      negatives.remove_shared_set_keywords(
          CUSTOMER_ID,
          SHARED_SET_ID,
          "333 OR metrics.clicks > 0",
      )

    mock_ads_client.get_service.assert_not_called()

  def test_rejects_duplicate_criterion_ids(self, mock_ads_client):
    with pytest.raises(ToolError, match="must not contain duplicates"):
      negatives.remove_shared_set_keywords(
          CUSTOMER_ID,
          SHARED_SET_ID,
          "333,333",
      )

    mock_ads_client.get_service.assert_not_called()

  def test_rejects_empty_criterion_ids(self, mock_ads_client):
    with pytest.raises(ToolError, match="criterion_ids must not be empty"):
      negatives.remove_shared_set_keywords(
          CUSTOMER_ID,
          SHARED_SET_ID,
          "[]",
      )

    mock_ads_client.get_service.assert_not_called()


# ---------------------------------------------------------------------------
# Campaign Shared Sets
# ---------------------------------------------------------------------------


class TestListCampaignSharedSets:

  def test_returns_links(self, mock_ads_client):
    mock_row = mock.Mock()
    mock_row.campaign.id = 222
    mock_row.campaign.name = "Campaign A"
    mock_row.shared_set.id = 111
    mock_row.shared_set.name = "My Negatives"

    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = [
        mock.Mock(results=[mock_row])
    ]

    result = negatives.list_campaign_shared_sets(CUSTOMER_ID)
    assert result == {
        "campaign_shared_sets": [
            {
                "campaign_id": "222",
                "campaign_name": "Campaign A",
                "shared_set_id": "111",
                "shared_set_name": "My Negatives",
            }
        ]
    }

  def test_filters_by_campaign_id(self, mock_ads_client):
    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = []

    negatives.list_campaign_shared_sets(CUSTOMER_ID, campaign_id=CAMPAIGN_ID)
    call_args = mock_ads_service.search_stream.call_args
    assert f"campaign.id = {CAMPAIGN_ID}" in call_args.kwargs["query"]


class TestAttachSharedSetToCampaign:

  def test_attaches(self, mock_ads_client):
    mock_service = mock.Mock()
    mock_campaign_service = mock.Mock()
    mock_campaign_service.campaign_path.return_value = (
        "customers/123/campaigns/222"
    )
    mock_shared_set_service = mock.Mock()
    mock_shared_set_service.shared_set_path.return_value = (
        "customers/123/sharedSets/111"
    )

    def get_service(name):
      if name == "CampaignSharedSetService":
        return mock_service
      if name == "CampaignService":
        return mock_campaign_service
      return mock_shared_set_service

    mock_ads_client.get_service.side_effect = get_service
    mock_response = mock_service.mutate_campaign_shared_sets.return_value
    mock_response.results = [
        mock.Mock(resource_name=("customers/123/campaignSharedSets/222~111"))
    ]

    result = negatives.attach_shared_set_to_campaign(
        CUSTOMER_ID, CAMPAIGN_ID, SHARED_SET_ID
    )
    assert result == {
        "resource_name": ("customers/123/campaignSharedSets/222~111")
    }


class TestDetachSharedSetFromCampaign:

  def test_detaches(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_campaign_shared_sets.return_value
    mock_response.results = [
        mock.Mock(resource_name=("customers/123/campaignSharedSets/222~111"))
    ]

    result = negatives.detach_shared_set_from_campaign(
        CUSTOMER_ID, CAMPAIGN_ID, SHARED_SET_ID
    )
    assert result == {
        "resource_name": ("customers/123/campaignSharedSets/222~111")
    }


# ---------------------------------------------------------------------------
# Campaign Negative Keywords
# ---------------------------------------------------------------------------


class TestListCampaignNegativeKeywords:

  def test_returns_keywords(self, mock_ads_client):
    mock_row = mock.Mock()
    mock_row.campaign_criterion.criterion_id = 444
    mock_row.campaign_criterion.keyword.text = "free"
    mock_row.campaign_criterion.keyword.match_type.name = "EXACT"

    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = [
        mock.Mock(results=[mock_row])
    ]

    result = negatives.list_campaign_negative_keywords(
        CUSTOMER_ID, CAMPAIGN_ID
    )
    assert result == {
        "keywords": [
            {
                "criterion_id": "444",
                "text": "free",
                "match_type": "EXACT",
            }
        ]
    }

  def test_normalizes_enum_filters_before_search(self, mock_ads_client):
    mock_ads_service = mock_ads_client.get_service.return_value
    mock_ads_service.search_stream.return_value = []

    negatives.list_campaign_negative_keywords(CUSTOMER_ID, CAMPAIGN_ID)

    sent_query = mock_ads_service.search_stream.call_args.kwargs["query"]
    assert "campaign_criterion.type = KEYWORD" in sent_query


class TestAddCampaignNegativeKeywords:

  def test_adds_keywords(self, mock_ads_client):
    mock_service = mock.Mock()
    mock_campaign_service = mock.Mock()
    mock_campaign_service.campaign_path.return_value = (
        "customers/123/campaigns/222"
    )

    def get_service(name):
      if name == "CampaignCriterionService":
        return mock_service
      return mock_campaign_service

    mock_ads_client.get_service.side_effect = get_service
    mock_enum_value = mock.Mock(value=1)
    mock_ads_client.enums.KeywordMatchTypeEnum.__getitem__ = mock.Mock(
        return_value=mock_enum_value
    )
    mock_response = mock_service.mutate_campaign_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name=("customers/123/campaignCriteria/222~444"))
    ]

    keywords = [{"text": "free", "match_type": "EXACT"}]
    result = negatives.add_campaign_negative_keywords(
        CUSTOMER_ID, CAMPAIGN_ID, keywords
    )
    assert result == {
        "resource_names": ["customers/123/campaignCriteria/222~444"]
    }


class TestRemoveCampaignNegativeKeywords:

  def test_removes_keywords(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_campaign_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name=("customers/123/campaignCriteria/222~444"))
    ]

    result = negatives.remove_campaign_negative_keywords(
        CUSTOMER_ID, CAMPAIGN_ID, ["444"]
    )
    assert result == {
        "resource_names": ["customers/123/campaignCriteria/222~444"]
    }

  def test_accepts_comma_separated_criterion_ids(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_campaign_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaignCriteria/222~333"),
        mock.Mock(resource_name="customers/123/campaignCriteria/222~444"),
    ]
    operations = [mock.Mock(), mock.Mock()]
    mock_ads_client.get_type.side_effect = operations

    negatives.remove_campaign_negative_keywords(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        "333,444",
    )

    assert operations[0].remove == (
        "customers/1234567890/campaignCriteria/222~333"
    )
    assert operations[1].remove == (
        "customers/1234567890/campaignCriteria/222~444"
    )
    request_operations = (
        mock_service.mutate_campaign_criteria.call_args.kwargs["operations"]
    )
    assert request_operations == operations

  def test_rejects_duplicate_criterion_ids(self, mock_ads_client):
    with pytest.raises(ToolError, match="must not contain duplicates"):
      negatives.remove_campaign_negative_keywords(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          ["333", "333"],
      )

    mock_ads_client.get_service.assert_not_called()

  def test_rejects_empty_criterion_ids(self, mock_ads_client):
    with pytest.raises(ToolError, match="criterion_ids must not be empty"):
      negatives.remove_campaign_negative_keywords(
          CUSTOMER_ID,
          CAMPAIGN_ID,
          "[]",
      )

    mock_ads_client.get_service.assert_not_called()
