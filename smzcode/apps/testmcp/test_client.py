import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


# 找到 mcp_server.py
server_path = Path(__file__).parent / "mcp_proxy.py"


# 告诉 MCP Client：
# 用哪个 Python 启动 MCP Server
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(server_path)],
)


async def main():

    # 启动 MCP Server，并通过 stdin/stdout 与它通信
    async with Client(server_params) as client:

        print("=== MCP Server 已连接 ===")

        # 查看协议版本
        print("协议版本：", client.protocol_version)

        # 查看服务器信息
        print("服务器信息：", client.server_info)

        # 获取服务器提供的工具
        tools_result = await client.list_tools()

        print("\n=== 服务器提供的工具 ===")

        for tool in tools_result.tools:
            print("工具名称：", tool.name)
            print("工具描述：", tool.description)

        # 调用工具
        print("\n=== 调用 read_sensor_file ===")

        result = await client.call_tool(
            "get_sensor_info",
            {
                "sensor_type": "force"
            }
        )

        print("\n=== 工具返回结果 ===")

        for content in result.content:
            if hasattr(content, "text"):
                print(content.text)


asyncio.run(main())