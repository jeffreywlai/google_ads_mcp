"""Large source rows and ordinary errors preserve a real stdio connection."""

import asyncio
import csv
import json
from pathlib import Path
import sys
import textwrap

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest


def test_stdio_survives_large_queries_exports_and_validation_error(tmp_path):
  root = Path(__file__).resolve().parents[1]
  fixture = tmp_path / "offline_server.py"
  fixture.write_text(
      textwrap.dedent("""
          from ads_mcp import stdio
          from ads_mcp.tools import api

          stdio.refresh_view_docs_for_startup = lambda: None
          api.get_ads_client = lambda *args, **kwargs: None
          api.get_ads_credential_cache_scope = lambda: "offline-stdio"
          source_calls = 0

          def rows(query, **kwargs):
            global source_calls
            source_calls += 1
            if "campaign.status" in query:
              return [{"campaign.status": "ENABLED"}]
            return [{"campaign.id": index + 1,
                     "campaign.name": "x" * 480}
                    for index in range(12000)]

          api.run_gaql_query = rows

          @stdio.mcp_server.tool(tags={"read"})
          def offline_source_call_count():
            return {"source_calls": source_calls}

          stdio.main()
          """),
      encoding="utf-8",
  )
  config = {
      "mcpServers": {
          "OfflineAds": {
              "command": sys.executable,
              "args": [str(fixture)],
              "env": {
                  "PYTHONPATH": str(root),
                  "GOOGLE_ADS_MCP_EXPORT_DIR": str(tmp_path),
                  "GOOGLE_ADS_MCP_DIAGNOSTICS": "1",
              },
          }
      }
  }

  async def exercise():
    async with Client(config) as client:
      results = await asyncio.gather(
          *(
              client.call_tool(
                  "execute_gaql",
                  {
                      "customer_id": "123",
                      "query": "SELECT campaign.id, campaign.name "
                      f"FROM campaign WHERE campaign.id > {index}",
                  },
              )
              for index in range(3)
          )
      )
      for index, result in enumerate(results):
        payload = result.structured_content
        assert payload["total_row_count"] == 12000
        assert payload["complete_inline"] is False
        assert len(json.dumps(payload).encode()) <= 48 * 1024
        export = payload["bulk_export_call"]
        destination = str(tmp_path / f"rows_{index}.csv")
        exported = await client.call_tool(
            export["tool"],
            {**export["arguments"], "output_path": destination},
        )
        assert exported.structured_content["row_count"] == 12000
        with open(destination, encoding="utf-8", newline="") as stream:
          data = list(csv.DictReader(stream))
        assert len(data) == 12000
        assert data[-1]["campaign.name"] == "x" * 480

      with pytest.raises(ToolError, match="campaign.start_date_time"):
        await client.call_tool(
            "execute_gaql",
            {
                "customer_id": "123",
                "query": "SELECT campaign.start_date FROM campaign",
            },
        )
      subsequent = await client.call_tool(
          "execute_gaql",
          {
              "customer_id": "123",
              "query": "SELECT campaign.status FROM campaign LIMIT 1",
          },
      )
      assert subsequent.structured_content["data"] == [
          {"campaign.status": "ENABLED"}
      ]
      count = await client.call_tool("offline_source_call_count", {})
      assert count.data["source_calls"] == 4

  asyncio.run(exercise())
