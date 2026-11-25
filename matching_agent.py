import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, create_model

load_dotenv()


class MatchingState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    job_requirements: str
    discovered_resources: list[str]
    match_report: str


def _schema_to_pydantic(tool_name: str, input_schema: dict[str, Any]) -> type[BaseModel]:
    props: dict[str, Any] = input_schema.get("properties", {})
    required: set[str] = set(input_schema.get("required", []))
    fields: dict[str, Any] = {}

    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list[str],
        "object": dict[str, Any],
    }

    for field_name, spec in props.items():
        py_type = type_map.get(spec.get("type", "string"), Any)
        default = ... if field_name in required else None
        fields[field_name] = (py_type, default)

    safe_name = "".join(ch for ch in tool_name.title() if ch.isalnum()) or "Tool"
    return create_model(f"{safe_name}Args", **fields)


async def build_mcp_tools(session: ClientSession) -> list[StructuredTool]:
    mcp_tools_resp = await session.list_tools()
    langchain_tools: list[StructuredTool] = []

    for tool in mcp_tools_resp.tools:
        raw_schema = (
            getattr(tool, "input_schema", None)
            or getattr(tool, "inputSchema", None)
            or {}
        )
        args_schema = _schema_to_pydantic(tool.name, raw_schema)

        async def _call_mcp(_tool_name: str = tool.name, **kwargs: Any) -> str:
            clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
            result = await session.call_tool(_tool_name, arguments=clean_kwargs)
            parts: list[str] = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts)

        langchain_tools.append(
            StructuredTool.from_function(
                coroutine=_call_mcp,
                name=tool.name,
                description=tool.description or f"MCP tool {tool.name}",
                args_schema=args_schema,
            )
        )

    async def read_mcp_resource(uri: str) -> str:
        """Read an MCP resource by URI (for example, resumes://catalog or resumes://file/alice_backend.md)."""
        res = await session.read_resource(uri)
        return "\n".join(item.text for item in res.contents if hasattr(item, "text"))

    langchain_tools.append(
        StructuredTool.from_function(
            coroutine=read_mcp_resource,
            name="read_mcp_resource",
            description="Read an MCP resource URI such as resumes://catalog or resumes://file/<name>.",
        )
    )
    return langchain_tools


async def run_matching_agent(
    job_requirements: str,
    since_timestamp: str | None = None,
    llm_override: Any | None = None,
) -> dict[str, Any]:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("filesystem_mcp_server.py"))],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            resources_resp = await session.list_resources()
            resource_uris = [str(r.uri) for r in resources_resp.resources]
            catalog_text = ""
            if any(str(r.uri) == "resumes://catalog" for r in resources_resp.resources):
                catalog_res = await session.read_resource("resumes://catalog")
                catalog_text = "\n".join(
                    c.text for c in catalog_res.contents if hasattr(c, "text")
                )

            tools = await build_mcp_tools(session)
            if llm_override is not None:
                llm = (
                    llm_override.bind_tools(tools)
                    if hasattr(llm_override, "bind_tools")
                    else llm_override
                )
            else:
                llm = ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(tools)

            system_prompt = SystemMessage(
                content=(
                    "You are a technical resume matching agent. Use MCP tools to check for new resumes "
                    "(watch_directory), read or batch-process candidate files (batch_process or read_mcp_resource), "
                    "and rank candidates against the job requirements with clear evidence."
                )
            )

            async def agent_node(state: MatchingState) -> dict[str, Any]:
                response = await llm.ainvoke([system_prompt, *state["messages"]])
                content_str = str(getattr(response, "content", response))
                return {"messages": [response], "match_report": content_str}

            graph_builder = StateGraph(MatchingState)
            graph_builder.add_node("agent", agent_node)
            graph_builder.add_node("tools", ToolNode(tools))
            graph_builder.add_edge(START, "agent")
            graph_builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
            graph_builder.add_edge("tools", "agent")

            graph = graph_builder.compile()

            user_prompt = (
                f"Job Requirements:\n{job_requirements}\n\n"
                f"Initial Catalog Snapshot:\n{catalog_text}\n\n"
                f"Check for new/updated resumes since '{since_timestamp or 'beginning'}', "
                "batch process relevant resumes, and produce a ranked matching summary."
            )

            final_state = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content=user_prompt)],
                    "job_requirements": job_requirements,
                    "discovered_resources": resource_uris,
                    "match_report": "",
                }
            )

            final_text = final_state.get("match_report") or str(final_state["messages"][-1].content)
            return {
                "discovered_resources": resource_uris,
                "report": final_text,
            }


if __name__ == "__main__":
    sample_jd = "Looking for a backend Python engineer with FastAPI, PostgreSQL, and Docker experience."
    output = asyncio.run(run_matching_agent(sample_jd))
    print("Resources:", output["discovered_resources"])
    print("\nMatch Report:\n", output["report"])