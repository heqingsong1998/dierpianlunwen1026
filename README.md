# 动态黏附足实验：下位机、上位机与控制器

本仓库保存 STM32G474 下位机工程，以及黏附足与标准六轴力传感器的上位机同步采集、标定和阈值控制代码。完整克隆后即可在另一台电脑继续开发，不需要单独拷贝 `smzcode`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `Core/` | STM32G474 应用源码、FreeRTOS 任务和主机控制协议 |
| `Drivers/` | AD7142、CAN、STM32 HAL、CMSIS 等驱动与库 |
| `Middlewares/` | FreeRTOS 中间件 |
| `20x30_RTOS.ioc` | STM32CubeMX 工程配置 |
| `MDK-ARM/20x30_RTOS.uvprojx` | Keil MDK 工程入口 |
| `smzcode/apps/main_window.py` | 上位机 GUI 入口 |
| `smzcode/controller/` | 独立控制线程、标定参数与双阈值算法 |
| `smzcode/config/default.yaml` | 串口、六轴力设备网络等配置 |
| `tests/firmware/` | 下位机本机模拟测试 |
| `smzcode/tests/` | 上位机协议、控制器、同步记录与清零测试 |

## 在另一台电脑获取项目

先安装 Git，然后在准备保存项目的位置打开 PowerShell：

```powershell
git clone https://github.com/heqingsong1998/dierpianlunwen1026.git
cd dierpianlunwen1026
```

首次使用执行 `git clone`。以后在已有项目根目录执行：

```powershell
git pull --ff-only
```

如果仓库里还没有新提交，`pull` 会提示已经是最新版本。若本机有尚未提交的改动，应先保存成提交或使用 `git stash` 暂存后再更新。

## 上位机环境与启动

当前上位机使用 **Python 3.10、PyQt5、pyserial 和 PyYAML**。可安装 Python 3.10，或建立相应的 Conda 环境。下面使用标准 Python 虚拟环境，命令在仓库根目录运行，不需要修改 PowerShell 的脚本执行策略：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r smzcode\requirements.txt
cd smzcode
..\.venv\Scripts\python.exe apps\main_window.py
```

换电脑后，先检查 `smzcode/config/default.yaml` 中的黏附脚串口号、波特率，以及六轴力设备 IP、端口和通信方式。网口采集时还需让电脑网卡与传感器处于可通信的网段。使用 USB 串口或调试器时，需要安装对应硬件驱动。

当前配置文件里的端口号和 IP 来自原实验电脑，不能保证适用于新电脑。程序启动后由用户点击连接，控制器默认停止。

`smzcode/apps/testmcp/` 是独立的早期 MCP 调试示例，不参与主界面启动；其额外依赖和旧电脑路径未纳入主程序的运行要求。

## 下位机编译与烧录

使用 Keil µVision 5 打开 `MDK-ARM/20x30_RTOS.uvprojx`。现有工程配置为：

- 芯片：STM32G474CETx。
- 编译器：ARM Compiler 5.06 update 7（build 960）。
- 设备包：Keil.STM32G4xx_DFP 1.2.0。

另一台电脑需安装对应编译器与设备包，在工程设置中选择本机安装的编译器，并按实际 ST-Link/J-Link 硬件设置调试与下载选项。生成的 HEX 位于 `MDK-ARM/20x30_RTOS/`；该目录不上传，由本机重新编译生成。

仓库保留工程所需的驱动、CMSIS 库和 FreeRTOS 源码。编译输出、个人调试布局、Python 缓存、虚拟环境、本地备份和实验录制目录均由 `.gitignore` 排除，源码文件仍保留在原目录。

## 控制与标定

控制链路为：下位机采样、清零并上传 → 上位机独立控制线程标定 → 两组 Fz 阈值判断 → 下发命令 → 下位机更新 GPIO。

默认两组阈值均为 1.5 N，同时达到阈值时触发一次。界面可修改阈值、触发 PA4 电平，启停控制器或重新布防。参数和标定文件会随 Git 版本同步：

- `smzcode/controller/config/settings.json`：界面保存的阈值和触发电平。
- `smzcode/controller/config/calibration.json`：已有的多项式标定参数，修改后可在界面重载。

程序中修改这些参数后，必须提交并推送相应文件，另一台电脑才能通过 `git pull` 获得新参数。实验原始数据默认保存在运行目录下的 `data/sessions/`，该目录不会随源码上传，需要另行保存实验记录。

详见 [控制器说明](smzcode/controller/README.md)、[上位机协议](smzcode/docs/protocol_v1.md) 和 [下位机协议](docs/host_control_protocol.md)。

## 在两台电脑之间同步修改

每次开始工作，先在仓库根目录 `git pull --ff-only`。修改并验证完成后：

```powershell
git status
git add .
git commit -m "说明本次修改内容"
git push
```

然后另一台电脑执行 `git pull --ff-only` 即可。首次在新电脑提交时，需要设置作者信息：

```powershell
git config user.name "你的名字"
git config user.email "你的 GitHub 提交邮箱"
```

向 GitHub 推送需要登录具有该仓库写入权限的账号；Git for Windows 通常通过 Git Credential Manager 完成认证。尽量在一台电脑提交并推送后，再切换到另一台电脑拉取继续工作。

首次从完整工程根目录初始化 Git 前，应先把原 `smzcode/.git` 移到本机 `.codex_backups/` 下保留历史，避免被当作嵌套仓库、使另一台电脑无法获取其源码。这些初始化与上传步骤由用户在终端执行；旧的 `heqingsong1998/smzcode` 远程仓库不需要修改。

## 验证

上位机测试在 `smzcode` 目录执行，不连接实物设备：

```powershell
..\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
```

下位机模拟测试需要 MinGW GCC，从仓库根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\firmware\run_tests.ps1 -CompilerPath "C:\你的MinGW路径\bin\gcc.exe"
```

上述模拟测试不代替实物采集和电磁铁联调。

## 许可证

上位机原有许可证见 [smzcode/LICENSE](smzcode/LICENSE)。STM32、CMSIS 和 FreeRTOS 等第三方组件保留各自目录中的许可证与版权声明。
