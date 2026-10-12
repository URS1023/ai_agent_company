# -*- coding: utf-8 -*-
"""Minimal MCP server (mcp 2.0 lowlevel, stdio) for provider verification."""
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ListToolsResult, CallToolResult

_TOOLS = [
    Tool(name="add", description="计算两个整数之和",
         inputSchema={"type": "object",
                      "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                      "required": ["a", "b"]}),
    Tool(name="greet", description="向用户问好",
         inputSchema={"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}),
]


async def _list_tools(ctx, params):
    return ListToolsResult(tools=_TOOLS)


async def _call_tool(ctx, params):
    name = params.name
    args = params.arguments or {}
    if name == "add":
        text = str(int(args.get("a", 0)) + int(args.get("b", 0)))
    elif name == "greet":
        text = "你好，%s！" % args.get("name", "")
    else:
        text = "unknown tool"
    return CallToolResult(content=[TextContent(type="text", text=text)])


async def main():
    server = Server("demo-math", on_list_tools=_list_tools, on_call_tool=_call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
