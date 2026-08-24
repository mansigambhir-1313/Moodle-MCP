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

        scope = {"campus": "jaipur", "batch": "2024-26", "trimester": "5"}

        marks = await client.call_tool("marks_overview", {"params": scope})
        print("\nmarks_overview ->", marks.data.get("mean_mark_pct"), "% mean,",
              marks.data.get("students_with_zeros"), "with zeros")

        att = await client.call_tool("attendance_overview", {"params": scope})
        print("attendance_overview ->", att.data.get("mean_attendance_pct"), "% mean,",
              att.data.get("below_75_pct_count"), "below 75%")

        roster = await client.call_tool("list_students",
                                        {"params": {"campus": "jaipur", "batch": "2024-26", "limit": 1}})
        sid = roster.data["students"][0]["student_id"]
        stu = await client.call_tool("get_student", {"params": {"student_id": sid, "trimester": "5"}})
        print(f"get_student({sid}) ->", stu.data["student"]["name"], "|",
              stu.data["subjects_count"], "subjects |", stu.data["overall_attendance_pct"], "% attendance")


if __name__ == "__main__":
    asyncio.run(main())
