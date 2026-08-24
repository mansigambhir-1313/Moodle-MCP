"""Smoke-test the Moodle MCP over HTTP: handshake, list tools, call a few.

  # against a locally-running server:
  MCP_URL=http://localhost:8899/mcp MCP_TOKEN=<token> python test_client.py
  # against the deployed server:
  MCP_URL=https://<render-url>/mcp MCP_TOKEN=<token> python test_client.py
"""
import asyncio
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = os.environ.get("MCP_URL", "http://localhost:8899/mcp")
TOKEN = os.environ.get("MCP_TOKEN", "")


async def main():
    transport = StreamableHttpTransport(url=URL, headers={"Authorization": f"Bearer {TOKEN}"})
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"connected to {URL}")
        print(f"tools ({len(tools)}):", ", ".join(t.name for t in tools))

        who = await client.call_tool("whoami", {})
        print("\nwhoami ->", who.data)

        acc = await client.call_tool("accuracy_overview",
                                     {"params": {"campus": "jaipur", "batch": "2024-26", "trimester": "5"}})
        print("\naccuracy_overview(jaipur 2024-26 T5) ->", acc.data)

        risk = await client.call_tool("at_risk_students",
                                      {"params": {"campus": "jaipur", "batch": "2024-26", "trimester": "5", "limit": 3}})
        print("\nat_risk_students -> count:", risk.data.get("at_risk_count"))


if __name__ == "__main__":
    asyncio.run(main())
