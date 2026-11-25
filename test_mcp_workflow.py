import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matching_agent import run_matching_agent


class StubAgentLLM:
    def __init__(self) -> None:
        self.turn = 0

    def bind_tools(self, _tools: list[Any]) -> "StubAgentLLM":
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_watch_1",
                        "name": "watch_directory",
                        "args": {"since_timestamp": "2020-01-01T00:00:00+00:00"},
                    }
                ],
            )
        tool_outputs = [m.content for m in messages if isinstance(m, ToolMessage)]
        summary = tool_outputs[-1][:250] if tool_outputs else "no tool output"
        return AIMessage(content=f"Ranked Match Report based on MCP tools: {summary}")


async def verify_mcp_server() -> None:
    base_dir = Path(__file__).resolve().parent
    resumes_dir = base_dir / "resumes"
    resumes_dir.mkdir(exist_ok=True)
    sample_file = resumes_dir / "test_candidate.md"
    sample_file.write_text("# Alex Rivera\nBackend Python engineer with FastAPI and PostgreSQL.", encoding="utf-8")

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(base_dir / "filesystem_mcp_server.py")],
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            resources = await session.list_resources()
            uris = [str(r.uri) for r in resources.resources]
            assert "resumes://catalog" in uris
            assert "resumes://config/summary" in uris

            catalog_res = await session.read_resource("resumes://catalog")
            catalog_data = json.loads(catalog_res.contents[0].text)
            assert catalog_data["count"] >= 1

            watch_res = await session.call_tool(
                "watch_directory",
                arguments={"since_timestamp": "2020-01-01T00:00:00+00:00"},
            )
            assert "test_candidate.md" in watch_res.content[0].text

            batch_res = await session.call_tool(
                "batch_process",
                arguments={"filenames": ["test_candidate.md", "../escape.txt", "missing.txt"]},
            )
            batch_text = batch_res.content[0].text
            assert '"status": "ok"' in batch_text
            assert '"status": "error"' in batch_text

    if sample_file.exists():
        sample_file.unlink()


async def verify_agent_workflow() -> None:
    result = await run_matching_agent(
        job_requirements="Backend Python engineer with FastAPI and PostgreSQL.",
        since_timestamp="2020-01-01T00:00:00+00:00",
        llm_override=StubAgentLLM(),
    )
    assert "resumes://catalog" in result["discovered_resources"]
    assert "Ranked Match Report" in result["report"]


async def main() -> None:
    await verify_mcp_server()
    await verify_agent_workflow()
    print("MCP server and LangGraph agent verification passed.")

    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key and not api_key.startswith("your-"):
        live = await run_matching_agent("Backend Python engineer with FastAPI and PostgreSQL.")
        print("\nLive Agent Report:\n", live["report"])


if __name__ == "__main__":
    asyncio.run(main())