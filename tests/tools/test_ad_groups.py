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

"""Tests for the ad group management tools."""

from unittest import mock

from ads_mcp.tools import ad_groups
from fastmcp.exceptions import ToolError
import pytest


CUSTOMER_ID = "1234567890"
AD_GROUP_ID = "111"
CRITERION_ID = "222"


def _criterion_operation():
  operation = mock.Mock()
  operation.update_mask.paths = []
  return operation


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.ad_groups.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


class TestSetAdGroupStatus:

  def test_pauses_ad_group(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_path.return_value = "customers/123/adGroups/111"
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroups/111")
    ]

    result = ad_groups.set_ad_group_status(CUSTOMER_ID, AD_GROUP_ID, "PAUSED")
    assert result == {"resource_name": "customers/123/adGroups/111"}

  def test_enables_ad_group(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_path.return_value = "customers/123/adGroups/111"
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroups/111")
    ]

    result = ad_groups.set_ad_group_status(CUSTOMER_ID, AD_GROUP_ID, "ENABLED")
    assert result == {"resource_name": "customers/123/adGroups/111"}

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.set_ad_group_status(
        CUSTOMER_ID, AD_GROUP_ID, "PAUSED", login_customer_id="999"
    )
    mock_ads_client._mock_get.assert_any_call("999")


class TestSetAdGroupCriterionStatus:

  def test_pauses_ad_group_criteria(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_criterion_path.side_effect = (
        lambda customer_id, ad_group_id, criterion_id: (
            f"customers/{customer_id}/adGroupCriteria/"
            f"{ad_group_id}~{criterion_id}"
        )
    )
    mock_ads_client.enums.AdGroupCriterionStatusEnum.PAUSED = "PAUSED"
    operations = [_criterion_operation(), _criterion_operation()]
    mock_ads_client.get_type.side_effect = operations
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupCriteria/111~222"),
        mock.Mock(resource_name="customers/123/adGroupCriteria/111~333"),
    ]

    result = ad_groups.set_ad_group_criterion_status(
        CUSTOMER_ID,
        AD_GROUP_ID,
        ["222", "333"],
        "PAUSED",
    )

    assert result == {
        "resource_names": [
            "customers/123/adGroupCriteria/111~222",
            "customers/123/adGroupCriteria/111~333",
        ],
        "criterion_ids": ["222", "333"],
        "status": "PAUSED",
        "updated_count": 2,
    }
    assert [op.update.resource_name for op in operations] == [
        f"customers/{CUSTOMER_ID}/adGroupCriteria/{AD_GROUP_ID}~222",
        f"customers/{CUSTOMER_ID}/adGroupCriteria/{AD_GROUP_ID}~333",
    ]
    assert [op.update.status for op in operations] == ["PAUSED", "PAUSED"]
    assert [op.update_mask.paths for op in operations] == [
        ["status"],
        ["status"],
    ]
    mock_service.mutate_ad_group_criteria.assert_called_once_with(
        customer_id=CUSTOMER_ID,
        operations=operations,
    )

  def test_accepts_single_criterion_id_string(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_criterion_path.return_value = (
        "customers/123/adGroupCriteria/111~222"
    )
    mock_ads_client.enums.AdGroupCriterionStatusEnum.ENABLED = "ENABLED"
    mock_ads_client.get_type.return_value = _criterion_operation()
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroupCriteria/111~222")
    ]

    result = ad_groups.set_ad_group_criterion_status(
        CUSTOMER_ID,
        AD_GROUP_ID,
        CRITERION_ID,
        "ENABLED",
    )

    assert result["criterion_ids"] == [CRITERION_ID]
    assert result["status"] == "ENABLED"

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_ads_client.get_type.return_value = _criterion_operation()
    mock_response = mock_service.mutate_ad_group_criteria.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.set_ad_group_criterion_status(
        CUSTOMER_ID,
        AD_GROUP_ID,
        CRITERION_ID,
        "PAUSED",
        login_customer_id="999",
    )
    mock_ads_client._mock_get.assert_any_call("999")

  def test_rejects_invalid_status(self, mock_ads_client):
    with pytest.raises(ToolError, match="Invalid status"):
      ad_groups.set_ad_group_criterion_status(
          CUSTOMER_ID,
          AD_GROUP_ID,
          CRITERION_ID,
          "REMOVED",
      )

    mock_ads_client.get_service.assert_not_called()

  @pytest.mark.parametrize(
      "criterion_ids,match",
      [
          ([], "criterion_ids must not be empty"),
          (["222", "222"], "must not contain duplicates"),
          (["not-a-number"], "must be an integer string"),
      ],
  )
  def test_rejects_bad_criterion_ids(
      self, mock_ads_client, criterion_ids, match
  ):
    with pytest.raises(ToolError, match=match):
      ad_groups.set_ad_group_criterion_status(
          CUSTOMER_ID,
          AD_GROUP_ID,
          criterion_ids,
          "PAUSED",
      )

    mock_ads_client.get_service.assert_not_called()


class TestUpdateAdGroupBid:

  def test_updates_bid(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_service.ad_group_path.return_value = "customers/123/adGroups/111"
    mock_op = mock_ads_client.get_type.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [
        mock.Mock(resource_name="customers/123/adGroups/111")
    ]

    result = ad_groups.update_ad_group_bid(CUSTOMER_ID, AD_GROUP_ID, 2_500_000)
    assert result == {"resource_name": "customers/123/adGroups/111"}
    assert mock_op.update.cpc_bid_micros == 2_500_000

  def test_sets_login_customer_id(self, mock_ads_client):
    mock_service = mock_ads_client.get_service.return_value
    mock_response = mock_service.mutate_ad_groups.return_value
    mock_response.results = [mock.Mock(resource_name="x")]

    ad_groups.update_ad_group_bid(
        CUSTOMER_ID,
        AD_GROUP_ID,
        1_000_000,
        login_customer_id="999",
    )
    mock_ads_client._mock_get.assert_any_call("999")
