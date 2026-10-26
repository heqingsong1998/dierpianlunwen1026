
from pathlib import Path
from mcp.server import MCPServer

# 1. 创建 MCP 服务
mcp = MCPServer("SensorLab")

# 2. 注册一个 MCP 工具
@mcp.tool()
def get_sensor_info(sensor_type: str) -> str:
    """查询指定类型传感器的信息。"""

    if sensor_type == "force":
        return "force_channels = 3, sample_rate = 1000 Hz"

    elif sensor_type == "proximity":
        return "proximity_channels = 4, sample_rate = 1000 Hz"

    elif sensor_type == "all":
        return (
            "force_channels = 2, "
            "proximity_channels = 4, "
            "sample_rate = 1000 Hz"
        )

    else:
        return f"unknown sensor_type: {sensor_type}"

# 3. 启动 MCP 服务
if __name__ == "__main__":
    mcp.run()
