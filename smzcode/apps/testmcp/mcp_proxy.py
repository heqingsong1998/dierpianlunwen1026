import subprocess
import sys
import threading
from pathlib import Path
from datetime import datetime


# 找到真正的 MCP Server
server_path = Path(__file__).parent / "mcp_server.py"

# 通信记录文件
log_path = Path(__file__).parent / "mcp_trace.log"


def log_message(direction, data):
    """把通信内容写入日志文件"""

    with open(log_path, "a", encoding="utf-8") as f:

        time = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        f.write("\n")
        f.write("=" * 80 + "\n")
        f.write(f"[{time}] {direction}\n")
        f.write("=" * 80 + "\n")

        try:
            text = data.decode("utf-8")

            f.write(text)

        except UnicodeDecodeError:

            f.write(repr(data))

        f.flush()


# 启动真正的 MCP Server
server = subprocess.Popen(
    [
        sys.executable,
        str(server_path)
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)


def client_to_server():

    for line in sys.stdin.buffer:

        # 记录 Client → Server
        log_message(
            "Client → Server",
            line
        )

        # 原样转发
        server.stdin.write(line)
        server.stdin.flush()


def server_to_client():

    for line in server.stdout:

        # 记录 Server → Client
        log_message(
            "Server → Client",
            line
        )

        # 原样转发
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()


# 两个方向同时监听
thread1 = threading.Thread(
    target=client_to_server
)

thread2 = threading.Thread(
    target=server_to_client
)


thread1.start()
thread2.start()


thread1.join()
thread2.join()