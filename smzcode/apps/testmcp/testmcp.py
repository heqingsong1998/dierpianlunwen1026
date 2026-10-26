
from pathlib import Path

# 第一步：找到文件
file_path = Path(r"D:\北工大_何清松\论文写作\第二篇论文\足端程序修改\修改版本2\smzcode\testmcp.txt")

# 第二步：读取文件内容
content = file_path.read_text(encoding="utf-8")

# 第三步：打印内容
print(content)
