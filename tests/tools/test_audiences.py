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

"""Tests for audience creation tools."""

from unittest import mock

from fastmcp.exceptions import ToolError
from google.ads.googleads.v24.enums.types.audience_scope import (
    AudienceScopeEnum,
)
from google.ads.googleads.v24.resources.types.audience import Audience
from ads_mcp.tools import audiences
import pytest


CUSTOMER_ID = "1234567890"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all tests."""
  with mock.patch("ads_mcp.tools.audiences.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client.get_ads_client_mock = mock_get
    yield client


def test_create_audience_builds_include_and_exclude_dimensions(
    mock_ads_client,
):
  audience_service = mock.Mock()
  mock_ads_client.get_service.return_value = audience_service

  operation = mock.Mock()
  operation.create = Audience()
  mock_ads_client.get_type.return_value = operation

  response = audience_service.mutate_audiences.return_value
  response.results = [
      mock.Mock(resource_name="customers/1234567890/audiences/777")
  ]
  audience_service.parse_audience_path.return_value = {
      "customer_id": CUSTOMER_ID,
      "audience_id": "777",
  }

  result = audiences.create_audience(
      customer_id=CUSTOMER_ID,
      name="Active Customers Exclude NTP-TC",
      description="Audience created in tests",
      include_dimensions=[
          {
              "segments": [
                  {
                      "type": "USER_LIST",
                      "resource_name": "customers/1234567890/userLists/1",
                  },
                  {
                      "type": "CUSTOM_AUDIENCE",
                      "resource_name": (
                          "customers/1234567890/customAudiences/2"
                      ),
                  },
              ]
          },
          {
              "segments": [
                  {
                      "type": "LIFE_EVENT",
                      "resource_name": "customers/1234567890/lifeEvents/3",
                  }
              ]
          },
      ],
      exclude_segments=[
          {
              "type": "USER_LIST",
              "resource_name": "customers/1234567890/userLists/9",
          }
      ],
  )

  assert result == {
      "audience_resource_name": "customers/1234567890/audiences/777",
      "audience_id": "777",
  }

  audience = operation.create
  dimensions = list(audience.dimensions)
  first_dimension_segments = list(dimensions[0].audience_segments.segments)
  second_dimension_segments = list(dimensions[1].audience_segments.segments)
  assert audience.name == "Active Customers Exclude NTP-TC"
  assert audience.description == "Audience created in tests"
  assert audience.scope == AudienceScopeEnum.AudienceScope.CUSTOMER
  assert len(dimensions) == 2
  assert len(first_dimension_segments) == 2
  assert first_dimension_segments[0].user_list.user_list == (
      "customers/1234567890/userLists/1"
  )
  assert (
      first_dimension_segments[1].custom_audience.custom_audience
      == "customers/1234567890/customAudiences/2"
  )
  assert (
      second_dimension_segments[0].life_event.life_event
      == "customers/1234567890/lifeEvents/3"
  )
  assert len(audience.exclusion_dimension.exclusions) == 1
  assert (
      audience.exclusion_dimension.exclusions[0].user_list.user_list
      == "customers/1234567890/userLists/9"
  )


def test_search_user_interests_builds_filtered_paginated_query():
  with mock.patch(
      "ads_mcp.tools.audiences.run_gaql_query_page",
      return_value={
          "rows": [
              {
                  "user_interest.user_interest_id": "90206",
                  "user_interest.name": "Dress shirts",
              }
          ],
          "next_page_token": "25",
          "total_results_count": 51,
      },
  ) as mock_query:
    result = audiences.search_user_interests(
        customer_id=CUSTOMER_ID,
        query="dress shirts",
        taxonomy_types='["IN_MARKET", "AFFINITY"]',
        limit=25,
        page_token="0",
        login_customer_id="999",
    )

  sent_query = mock_query.call_args.kwargs["query"]
  assert "FROM user_interest" in sent_query
  assert "user_interest.name LIKE '%dress shirts%'" in sent_query
  assert "user_interest.taxonomy_type IN (IN_MARKET, AFFINITY)" in sent_query
  assert "user_interest.launched_to_all = TRUE" in sent_query
  assert mock_query.call_args.kwargs["customer_id"] == CUSTOMER_ID
  assert mock_query.call_args.kwargs["page_size"] == 25
  assert mock_query.call_args.kwargs["page_token"] == "0"
  assert mock_query.call_args.kwargs["login_customer_id"] == "999"
  assert result["user_interests"] == [
      {
          "user_interest.user_interest_id": "90206",
          "user_interest.name": "Dress shirts",
      }
  ]
  assert result["truncated"] is True
  assert result["next_page_token"] == "25"


def test_search_user_interests_can_include_not_launched():
  with mock.patch(
      "ads_mcp.tools.audiences.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    audiences.search_user_interests(
        customer_id=CUSTOMER_ID,
        include_not_launched=True,
    )

  assert "WHERE" not in mock_query.call_args.kwargs["query"]


def test_search_user_interests_escapes_like_wildcards():
  with mock.patch(
      "ads_mcp.tools.audiences.run_gaql_query_page",
      return_value={
          "rows": [],
          "next_page_token": None,
          "total_results_count": 0,
      },
  ) as mock_query:
    audiences.search_user_interests(
        customer_id=CUSTOMER_ID,
        query="50%_off",
    )

  sent_query = mock_query.call_args.kwargs["query"]
  assert "user_interest.name LIKE '%50[%][_]off%'" in sent_query
  assert "LIKE '%50%_off%'" not in sent_query


def test_create_audience_rejects_non_user_list_exclusions(mock_ads_client):
  audience_service = mock.Mock()
  mock_ads_client.get_service.return_value = audience_service

  with pytest.raises(ToolError, match="Invalid exclude_segments\\[0\\].type"):
    audiences.create_audience(
        customer_id=CUSTOMER_ID,
        name="Bad exclusion",
        include_dimensions=[
            {
                "segments": [
                    {
                        "type": "USER_LIST",
                        "resource_name": "customers/1234567890/userLists/1",
                    }
                ]
            }
        ],
        exclude_segments=[
            {
                "type": "USER_INTEREST",
                "resource_name": ("customers/1234567890/userInterests/2"),
            }
        ],
    )

  audience_service.mutate_audiences.assert_not_called()


def test_create_audience_sets_login_customer_id(mock_ads_client):
  audience_service = mock.Mock()
  mock_ads_client.get_service.return_value = audience_service

  operation = mock.Mock()
  operation.create = Audience()
  mock_ads_client.get_type.return_value = operation

  response = audience_service.mutate_audiences.return_value
  response.results = [
      mock.Mock(resource_name="customers/1234567890/audiences/8")
  ]
  audience_service.parse_audience_path.return_value = {
      "customer_id": CUSTOMER_ID,
      "audience_id": "8",
  }

  audiences.create_audience(
      customer_id=CUSTOMER_ID,
      name="Login test audience",
      include_dimensions=[
          {
              "segments": [
                  {
                      "type": "USER_LIST",
                      "resource_name": "customers/1234567890/userLists/1",
                  }
              ]
          }
      ],
      login_customer_id="999",
  )

  mock_ads_client.get_ads_client_mock.assert_called_with("999")
