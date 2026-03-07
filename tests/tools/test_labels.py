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
    assert mock_ads_client.login_customer_id == "999"

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


class TestApplyLabelToCampaigns:

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

    result = labels.apply_label_to_campaigns(
        CUSTOMER_ID, LABEL_ID, [CAMPAIGN_ID]
    )
    assert result == {
        "resource_names": ["customers/123/campaignLabels/222~111"]
    }


class TestRemoveLabelFromCampaigns:

  def test_removes_from_campaigns(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.campaign_label_path.return_value = (
        "customers/123/campaignLabels/222~111"
    )
    mock_response = mock_service.mutate_campaign_labels.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/campaignLabels/222~111")
    ]

    result = labels.remove_label_from_campaigns(
        CUSTOMER_ID, LABEL_ID, [CAMPAIGN_ID]
    )
    assert result == {
        "resource_names": ["customers/123/campaignLabels/222~111"]
    }


class TestApplyLabelToAdGroups:

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

    result = labels.apply_label_to_ad_groups(
        CUSTOMER_ID, LABEL_ID, [AD_GROUP_ID]
    )
    assert result == {
        "resource_names": ["customers/123/adGroupLabels/333~111"]
    }


class TestRemoveLabelFromAdGroups:

  def test_removes_from_ad_groups(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_label_path.return_value = (
        "customers/123/adGroupLabels/333~111"
    )
    mock_response = mock_service.mutate_ad_group_labels.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupLabels/333~111")
    ]

    result = labels.remove_label_from_ad_groups(
        CUSTOMER_ID, LABEL_ID, [AD_GROUP_ID]
    )
    assert result == {
        "resource_names": ["customers/123/adGroupLabels/333~111"]
    }
