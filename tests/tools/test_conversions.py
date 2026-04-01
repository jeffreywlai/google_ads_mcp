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

"""Tests for conversions.py."""

from unittest import mock

from ads_mcp.tools import conversions
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
import pytest


CUSTOMER_ID = "1234567890"
CONVERSION_ACTION = "customers/1234567890/conversionActions/987654321"


@pytest.fixture(autouse=True)
def mock_ads_client():
  """Patches get_ads_client for all conversion upload tests."""
  with mock.patch("ads_mcp.tools.conversions.get_ads_client") as mock_get:
    client = mock.Mock()
    mock_get.return_value = client
    client._mock_get = mock_get
    yield client


def test_upload_click_conversions_builds_request(mock_ads_client):
  mock_service = mock_ads_client.get_service.return_value
  mock_response = mock_service.upload_click_conversions.return_value
  mock_response.results = [
      {
          "gclid": "test-gclid",
          "conversion_action": CONVERSION_ACTION,
          "conversion_date_time": "2026-03-20 12:34:56-04:00",
      }
  ]
  mock_response.partial_failure_error = None
  mock_response.job_id = 777

  result = conversions.upload_click_conversions(
      CUSTOMER_ID,
      conversions=[
          {
              "gclid": "test-gclid",
              "conversion_action": CONVERSION_ACTION,
              "conversion_date_time": "2026-03-20 12:34:56-04:00",
              "conversion_value": 10.5,
              "currency_code": "USD",
              "order_id": "order-1",
              "consent": {"ad_user_data": "GRANTED"},
          }
      ],
      validate_only=True,
      job_id=777,
  )

  assert result == {
      "results": [
          {
              "gclid": "test-gclid",
              "conversion_action": CONVERSION_ACTION,
              "conversion_date_time": "2026-03-20 12:34:56-04:00",
          }
      ],
      "result_count": 1,
      "job_id": 777,
  }
  request = mock_service.upload_click_conversions.call_args.kwargs["request"]
  assert request.customer_id == CUSTOMER_ID
  assert request.partial_failure is True
  assert request.validate_only is True
  assert request.job_id == 777
  assert len(request.conversions) == 1
  assert request.conversions[0].gclid == "test-gclid"
  assert request.conversions[0].order_id == "order-1"
  assert request.conversions[0].consent.ad_user_data.name == "GRANTED"


def test_list_offline_conversion_upload_client_summaries_builds_query():
  with mock.patch("ads_mcp.tools.conversions.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }

    result = conversions.list_offline_conversion_upload_client_summaries(
        CUSTOMER_ID,
        clients=["google_ads_api"],
        statuses=["needs_attention"],
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM offline_conversion_upload_client_summary" in query
  assert "offline_conversion_upload_client_summary.alerts" in query
  assert "offline_conversion_upload_client_summary.daily_summaries" in query
  assert "offline_conversion_upload_client_summary.job_summaries" in query
  assert "offline_conversion_upload_client_summary.client IN" in query
  assert "GOOGLE_ADS_API" in query
  assert "offline_conversion_upload_client_summary.status IN" in query
  assert "NEEDS_ATTENTION" in query
  assert result["returned_count"] == 0
  assert result["total_count"] == 0


def test_list_offline_conversion_upload_client_summaries_passes_paging():
  with mock.patch("ads_mcp.tools.conversions.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [
            {
                "customer.id": CUSTOMER_ID,
                "offline_conversion_upload_client_summary.client": (
                    "GOOGLE_ADS_API"
                ),
            }
        ],
        "next_page_token": "next-page",
        "total_results_count": 101,
    }

    result = conversions.list_offline_conversion_upload_client_summaries(
        CUSTOMER_ID,
        limit=25,
        page_token="current-page",
        login_customer_id="999",
    )

  assert mock_run.call_args.kwargs["page_size"] == 25
  assert mock_run.call_args.kwargs["page_token"] == "current-page"
  assert mock_run.call_args.kwargs["login_customer_id"] == "999"
  assert result == {
      "offline_conversion_upload_client_summaries": [
          {
              "customer.id": CUSTOMER_ID,
              "offline_conversion_upload_client_summary.client": (
                  "GOOGLE_ADS_API"
              ),
          }
      ],
      "returned_count": 1,
      "total_count": 101,
      "total_page_count": 5,
      "truncated": True,
      "next_page_token": "next-page",
      "page_size": 25,
  }


def test_list_offline_conversion_upload_conversion_action_summaries_builds_query():
  with mock.patch("ads_mcp.tools.conversions.run_gaql_query_page") as mock_run:
    mock_run.return_value = {
        "rows": [],
        "next_page_token": None,
        "total_results_count": 0,
    }

    result = (
        conversions.list_offline_conversion_upload_conversion_action_summaries(
            CUSTOMER_ID,
            conversion_action_ids=["111", "222"],
            clients=["google_ads_api"],
            statuses=["excellent"],
        )
    )

  query = mock_run.call_args.kwargs["query"]
  assert "FROM offline_conversion_upload_conversion_action_summary" in query
  assert (
      "offline_conversion_upload_conversion_action_summary."
      "conversion_action_id"
  ) in query
  assert (
      "offline_conversion_upload_conversion_action_summary.daily_summaries"
      in query
  )
  assert (
      "offline_conversion_upload_conversion_action_summary.job_summaries"
      in query
  )
  assert "conversion_action_id IN (111, 222)" in query
  assert "GOOGLE_ADS_API" in query
  assert "EXCELLENT" in query
  assert result["returned_count"] == 0


def test_list_offline_conversion_upload_conversion_action_summaries_validates_limit():
  with pytest.raises(ToolError, match="limit must be greater than 0"):
    conversions.list_offline_conversion_upload_conversion_action_summaries(
        CUSTOMER_ID,
        limit=0,
    )


def test_upload_call_conversions_returns_partial_failure(mock_ads_client):
  mock_service = mock_ads_client.get_service.return_value
  mock_response = mock_service.upload_call_conversions.return_value
  mock_response.results = [
      {
          "caller_id": "+15551234567",
          "call_start_date_time": "2026-03-20 09:00:00-04:00",
          "conversion_action": CONVERSION_ACTION,
          "conversion_date_time": "2026-03-20 10:00:00-04:00",
      }
  ]
  mock_response.partial_failure_error = {
      "code": 3,
      "message": "One row failed.",
  }

  result = conversions.upload_call_conversions(
      CUSTOMER_ID,
      conversions=[
          {
              "caller_id": "+15551234567",
              "call_start_date_time": "2026-03-20 09:00:00-04:00",
              "conversion_action": CONVERSION_ACTION,
              "conversion_date_time": "2026-03-20 10:00:00-04:00",
              "conversion_value": 25.0,
              "currency_code": "USD",
              "consent": {"ad_user_data": "GRANTED"},
          }
      ],
  )

  assert result == {
      "results": [
          {
              "caller_id": "+15551234567",
              "call_start_date_time": "2026-03-20 09:00:00-04:00",
              "conversion_action": CONVERSION_ACTION,
              "conversion_date_time": "2026-03-20 10:00:00-04:00",
          }
      ],
      "result_count": 1,
      "partial_failure_error": {
          "code": 3,
          "message": "One row failed.",
      },
  }
  request = mock_service.upload_call_conversions.call_args.kwargs["request"]
  assert request.customer_id == CUSTOMER_ID
  assert request.partial_failure is True
  assert request.validate_only is False
  assert request.conversions[0].caller_id == "+15551234567"
  assert request.conversions[0].consent.ad_user_data.name == "GRANTED"


def test_upload_click_conversions_sets_login_customer_id(mock_ads_client):
  mock_service = mock_ads_client.get_service.return_value
  mock_service.upload_click_conversions.return_value.results = []
  mock_service.upload_click_conversions.return_value.partial_failure_error = (
      None
  )
  mock_service.upload_click_conversions.return_value.job_id = 0

  conversions.upload_click_conversions(
      CUSTOMER_ID,
      conversions=[
          {
              "gclid": "test-gclid",
              "conversion_action": CONVERSION_ACTION,
              "conversion_date_time": "2026-03-20 12:34:56-04:00",
          }
      ],
      login_customer_id="999",
  )

  mock_ads_client._mock_get.assert_any_call("999")


def test_upload_call_conversions_rejects_empty_conversions():
  with pytest.raises(ToolError, match="must not be empty"):
    conversions.upload_call_conversions(CUSTOMER_ID, conversions=[])


def test_upload_click_conversions_rejects_non_object_row():
  with pytest.raises(ToolError, match=r"conversions\[0\] must be an object"):
    conversions.upload_click_conversions(
        CUSTOMER_ID,
        conversions=["not-a-dict"],
    )


def test_upload_call_conversions_raises_tool_error_on_api_error(
    mock_ads_client,
):
  mock_service = mock_ads_client.get_service.return_value
  error = mock.Mock()
  error.__str__ = lambda self: "Upload failed"
  exc = GoogleAdsException(
      error=mock.Mock(),
      failure=mock.Mock(errors=[error]),
      call=mock.Mock(),
      request_id="test",
  )
  mock_service.upload_call_conversions.side_effect = exc

  with pytest.raises(ToolError, match="Upload failed"):
    conversions.upload_call_conversions(
        CUSTOMER_ID,
        conversions=[
            {
                "caller_id": "+15551234567",
                "call_start_date_time": "2026-03-20 09:00:00-04:00",
                "conversion_action": CONVERSION_ACTION,
                "conversion_date_time": "2026-03-20 10:00:00-04:00",
            }
        ],
    )
