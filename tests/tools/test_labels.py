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

"""Tests for the label management tools."""

from unittest import mock

from ads_mcp.tools import labels
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v25.errors.types.errors import GoogleAdsFailure
from google.api_core import exceptions as google_exceptions
import pytest

CUSTOMER_ID = "1234567890"
LABEL_ID = "111"
CAMPAIGN_ID = "222"
AD_GROUP_ID = "333"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.labels.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


class TestCreateLabel:

  def test_creates_label(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/labels/111")
    ]

    result = labels.create_label(CUSTOMER_ID, "Test Label")
    assert result == {"resource_name": "customers/123/labels/111"}
    assert mock_op.create.name == "Test Label"

  def test_creates_label_with_description(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/labels/111")
    ]

    labels.create_label(CUSTOMER_ID, "Test Label", description="A test label")
    assert mock_op.create.text_label.description == "A test label"

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    labels.create_label(CUSTOMER_ID, "Test", login_customer_id="999")
    mock_ads_client._mock_get.assert_any_call("999")

  def test_raises_tool_error_on_api_error(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    error = mock.Mock()
    error.__str__ = lambda self: "API error"
    exc = GoogleAdsException(
        error=mock.Mock(),
        failure=mock.Mock(errors=[error]),
        call=mock.Mock(),
        request_id="test",
    )
    mock_service.mutate_labels.side_effect = exc

    with pytest.raises(ToolError):
      labels.create_label(CUSTOMER_ID, "Test")


class TestDeleteLabel:

  def test_deletes_label(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.label_path.return_value = "customers/123/labels/111"
    mock_response = mock_service.mutate_labels.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/labels/111")
    ]

    result = labels.delete_label(CUSTOMER_ID, LABEL_ID)
    assert result == {"resource_name": "customers/123/labels/111"}


class TestManageCampaignLabels:

  def test_applies_to_campaigns(self, mock_ads_client):
    mock_campaign_label_service = mock.Mock()
    mock_campaign_service = mock.Mock()
    mock_campaign_service.campaign_path.return_value = (
        "customers/123/campaigns/222"
    )
    mock_label_service = mock.Mock()
    mock_label_service.label_path.return_value = "customers/123/labels/111"

    def get_service(name):
      if name == "CampaignLabelService":
        return mock_campaign_label_service
      if name == "CampaignService":
        return mock_campaign_service
      return mock_label_service

    mock_ads_client.get_service.side_effect = get_service
    mock_response = (
        mock_campaign_label_service.mutate_campaign_labels.return_value
    )
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaignLabels/222~111")
    ]

    result = labels.manage_campaign_labels(
        CUSTOMER_ID, LABEL_ID, [CAMPAIGN_ID], "APPLY"
    )
    assert result == {
        "resource_names": ["customers/123/campaignLabels/222~111"]
    }

  def test_removes_from_campaigns(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.campaign_label_path.return_value = (
        "customers/123/campaignLabels/222~111"
    )
    mock_response = mock_service.mutate_campaign_labels.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaignLabels/222~111")
    ]

    result = labels.manage_campaign_labels(
        CUSTOMER_ID, LABEL_ID, [CAMPAIGN_ID], "REMOVE"
    )
    assert result == {
        "resource_names": ["customers/123/campaignLabels/222~111"]
    }

  def test_invalid_action_raises_error(self, mock_ads_client):
    with pytest.raises(ToolError, match="Invalid action"):
      labels.manage_campaign_labels(
          CUSTOMER_ID, LABEL_ID, [CAMPAIGN_ID], "INVALID"
      )


class TestManageAdGroupLabels:

  def test_applies_to_ad_groups(self, mock_ads_client):
    mock_ad_group_label_service = mock.Mock()
    mock_ad_group_service = mock.Mock()
    mock_ad_group_service.ad_group_path.return_value = (
        "customers/123/adGroups/333"
    )
    mock_label_service = mock.Mock()
    mock_label_service.label_path.return_value = "customers/123/labels/111"

    def get_service(name):
      if name == "AdGroupLabelService":
        return mock_ad_group_label_service
      if name == "AdGroupService":
        return mock_ad_group_service
      return mock_label_service

    mock_ads_client.get_service.side_effect = get_service
    mock_response = (
        mock_ad_group_label_service.mutate_ad_group_labels.return_value
    )
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupLabels/333~111")
    ]

    result = labels.manage_ad_group_labels(
        CUSTOMER_ID, LABEL_ID, [AD_GROUP_ID], "APPLY"
    )
    assert result == {
        "resource_names": ["customers/123/adGroupLabels/333~111"]
    }

  def test_removes_from_ad_groups(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_label_path.return_value = (
        "customers/123/adGroupLabels/333~111"
    )
    mock_response = mock_service.mutate_ad_group_labels.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupLabels/333~111")
    ]

    result = labels.manage_ad_group_labels(
        CUSTOMER_ID, LABEL_ID, [AD_GROUP_ID], "REMOVE"
    )
    assert result == {
        "resource_names": ["customers/123/adGroupLabels/333~111"]
    }


# Pytest injects fixtures using their declared names.
# pylint: disable=redefined-outer-name


@pytest.fixture(params=["transport", "google_ads"])
def api_failure(request):
  """Provides transport errors and native Google Ads hint-bearing failures."""
  if request.param == "transport":
    return google_exceptions.ServiceUnavailable("transport unavailable")
  return GoogleAdsException(
      error=mock.Mock(),
      failure=GoogleAdsFailure(errors=[{"message": "USER_PERMISSION_DENIED"}]),
      call=mock.Mock(),
      request_id="test",
  )


def _assert_api_failure(raised, original):
  assert raised.__cause__ is original
  if isinstance(original, GoogleAdsException):
    assert "USER_PERMISSION_DENIED" in str(raised)
    assert "Hints:\n- Call list_accessible_accounts" in str(raised)
  else:
    assert str(raised) == str(original)


@pytest.mark.parametrize(
    ("tool", "args", "method"),
    [
        pytest.param(
            labels.create_label,
            (CUSTOMER_ID, "Test Label"),
            "mutate_labels",
            id="create",
        ),
        pytest.param(
            labels.delete_label,
            (CUSTOMER_ID, LABEL_ID),
            "mutate_labels",
            id="delete",
        ),
        pytest.param(
            labels.manage_campaign_labels,
            (CUSTOMER_ID, LABEL_ID, [CAMPAIGN_ID], "APPLY"),
            "mutate_campaign_labels",
            id="apply_campaign",
        ),
        pytest.param(
            labels.manage_campaign_labels,
            (CUSTOMER_ID, LABEL_ID, [CAMPAIGN_ID], "REMOVE"),
            "mutate_campaign_labels",
            id="remove_campaign",
        ),
        pytest.param(
            labels.manage_ad_group_labels,
            (CUSTOMER_ID, LABEL_ID, [AD_GROUP_ID], "APPLY"),
            "mutate_ad_group_labels",
            id="apply_ad_group",
        ),
        pytest.param(
            labels.manage_ad_group_labels,
            (CUSTOMER_ID, LABEL_ID, [AD_GROUP_ID], "REMOVE"),
            "mutate_ad_group_labels",
            id="remove_ad_group",
        ),
    ],
)
def test_label_mutation_handles_api_failure_once(
    mock_ads_client, api_failure, tool, args, method
):
  mutation = getattr(mock_ads_client.get_service.return_value, method)
  mutation.side_effect = api_failure

  with pytest.raises(ToolError) as caught:
    tool(*args)

  _assert_api_failure(caught.value, api_failure)
  mutation.assert_called_once()
  assert len(mutation.call_args.kwargs["operations"]) == 1
