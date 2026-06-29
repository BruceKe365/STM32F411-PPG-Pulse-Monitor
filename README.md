# STM32F411 智能健康监测系统

本项目是基于 STM32F411 和 MAX30102 的便携式智能健康监测系统设计，实现了心率、血氧、OLED 显示、PPG 波形显示、房颤风险提示和 HRV 压力指数原型。

作者：Bruce Ke

所属单位：华中科技大学 电子信息与通信学院

如果是后续维护者继续开发，请先认真阅读本文。

如果希望使用 AI Agent 辅助继续开发，请让 AI 助手统一从 `AI助手接手？先读我.md` 开始阅读，里面已经详细整理了当前状态、关键文件、封版规则和后续开发边界等，随后再做进一步修改。

## 核心技术亮点

这个项目不只是把传感器读数显示到屏幕上，而是在 STM32F411 上实现了一套完整的 PPG 信号采集、质量控制、特征提取和端侧轻量推理流程：

- 心率计算：基于 MAX30102 红外 PPG 信号，结合自相关候选、连续确认、中位数平滑和跳变抑制，提高静息测量稳定性。
- 血氧计算：使用红光/红外双通道 PPG 的 AC/DC 比值思路，完成 SpO2 估计和屏幕实时显示。
- 波形显示：在 0.91 寸 OLED 上实时绘制处理后的 PPG 波形，并结合峰值检测做可视化反馈。
- 房颤风险提示：从 PPG 峰间期 PPI 中提取 HRV/节律不规则特征，使用离散朴素贝叶斯模型输出 0-100% AF risk。
- 压力指数原型：从 PPI 序列提取 HRV 特征，使用标准化 Logistic Regression 模型输出 1-99 压力指数。
- 端侧工程化：在 MCU 上完成窗口缓存、质量门控、异常兜底、OLED 多模式显示和 USB CDC 诊断输出，Python 端提供采集、训练、回放和验证脚本。

## 项目目标

本项目目标是做出一套可实物运行、可串口调试、可屏幕展示的 STM32 脉搏测试仪：

- 使用 MAX30102 采集红光/红外 PPG 信号。
- 计算并显示心率 HR 和血氧 SpO2。
- 使用 0.96 寸 OLED 作为主显示屏。
- 使用 0.91 寸 OLED 显示 PPG 波形。
- 基于 PPG 峰间期 PPI 做疑似房颤风险提示。
- 基于 HRV 特征做 1-99 压力指数原型。
- 通过 USB CDC 虚拟串口输出 raw 数据和处理后的诊断数据。

注意：房颤和压力相关功能是课程设计中的风险提示/指数原型，不是医学诊断工具。

## 硬件平台

当前固件面向 STM32F411CEUx/STM32F411CEU6 平台，主板和供电板可结合根目录下的 PCB 工程文件复现：

- `PCB板文件(epro2文件)/脉搏测试仪主板（不带电源）.epro2`
- `PCB板文件(epro2文件)/锂电池供电电源板 30mm×40mm.epro2`

主要硬件如下：

| 模块 | 型号/说明 | 用途 |
| --- | --- | --- |
| MCU | STM32F411CEU6 | 传感器读取、算法计算、OLED 显示、USB CDC 串口 |
| PPG 传感器 | MAX30102 | 红光/红外 PPG 采样，用于 HR、SpO2、PPI |
| 主显示屏 | 0.96 寸 SSD1306 OLED | 显示 HR/SpO2、AF risk、Stress 等主信息 |
| 波形屏 | 0.91 寸 SSD1306 OLED | 显示 PPG 滚动波形 |
| 调试/烧录 | DAPLink / CMSIS-DAP + SWD | OpenOCD 烧录和调试 |
| 通信 | USB CDC FS | Windows 下枚举为虚拟串口，用于采集和诊断 |

复现时建议准备的主要元件/模块：

| 元件/模块 | 建议规格 | 连接或注意事项 |
| --- | --- | --- |
| STM32F411CEU6 最小系统或自制主板 | STM32F411CEUx，3.3 V 逻辑，25 MHz HSE | 与 `STM32_F411_Test.ioc` 的时钟和引脚配置保持一致。 |
| MAX30102 PPG 传感器模块 | I2C，7-bit 地址 `0x57` | 接 I2C3：PA8=SCL、PB4=SDA；INT 接 PB5，下降沿触发并上拉。 |
| 0.96 寸 SSD1306 OLED | I2C，128x64，常见 7-bit 地址 `0x3C` | 主显示屏，接 I2C1：PB6=SCL、PB7=SDA。 |
| 0.91 寸 SSD1306 OLED | I2C，128x32，常见 7-bit 地址 `0x3C` | 波形屏，接 I2C2：PB10=SCL、PB3=SDA。 |
| 用户按键 | 普通轻触按键 | 接 PA0，固件内使用上拉，低电平按下，用于切换显示模式。 |
| DAPLink/CMSIS-DAP 或 ST-Link | SWD 调试器 | 至少连接 SWDIO、SWCLK、GND；主板已供电时不要再并接调试器 3V3 供电。 |
| USB Type-C 数据线 | 支持数据传输 | 用于主板供电、USB CDC 虚拟串口和串口采集。 |
| 锂电池供电板和电池 | 可选，见 PCB 工程 | 使用电池供电时参考 `PCB板文件(epro2文件)/锂电池供电电源板 30mm×40mm.epro2`。 |

如果使用面包板或飞线复现，所有模块必须共地，并确认 OLED 与 MAX30102 模块均工作在 3.3 V 逻辑电平。更完整的封装、布局和供电连接以 `PCB板文件(epro2文件)/` 中的 PCB 工程为准。

## 引脚连接

当前引脚分配来自 `STM32_F411_Test.ioc`、`Core/Src/i2c.c`、`Core/Src/gpio.c` 和 `Core/Src/main.c`：

| 引脚 | CubeMX 功能 | 连接对象 | 说明 |
| --- | --- | --- | --- |
| PB6 | I2C1_SCL | 0.96 寸 SSD1306 OLED | 主显示屏 SCL，I2C1 400 kHz |
| PB7 | I2C1_SDA | 0.96 寸 SSD1306 OLED | 主显示屏 SDA |
| PB10 | I2C2_SCL | 0.91 寸 SSD1306 OLED | 波形屏 SCL，I2C2 400 kHz |
| PB3 | I2C2_SDA | 0.91 寸 SSD1306 OLED | 波形屏 SDA |
| PA8 | I2C3_SCL | MAX30102 | 传感器 SCL，I2C3 100 kHz |
| PB4 | I2C3_SDA | MAX30102 | 传感器 SDA |
| PB5 | GPIO EXTI falling | MAX30102 INT | 下降沿外部中断，内部上拉 |
| PA0 | GPIO input | 用户按键 | 固件内配置上拉，低电平按下，用于切换显示模式 |
| PA11 | USB_OTG_FS_DM | USB D- | USB CDC 虚拟串口 |
| PA12 | USB_OTG_FS_DP | USB D+ | USB CDC 虚拟串口 |
| PA13 | SWDIO | DAPLink / ST-Link | SWD 下载调试 |
| PA14 | SWCLK | DAPLink / ST-Link | SWD 下载调试 |
| PH0/PH1 | HSE OSC IN/OUT | 外部晶振 | 当前配置使用 25 MHz HSE |

## STM32CubeMX 配置要点

CubeMX 工程文件为 `STM32_F411_Test.ioc`，当前关键配置如下：

- MCU：STM32F411CEUx，封装 UFQFPN48。
- CubeMX：6.14.1。
- 固件包：STM32Cube FW_F4 V1.28.3。
- Toolchain：CMake。
- 时钟：25 MHz HSE，经 PLL 配置到 96 MHz SYSCLK/HCLK，USB 48 MHz。
- 外设：I2C1、I2C2、I2C3、USB_OTG_FS Device Only、USB_DEVICE CDC FS、SYS Serial Wire。
- GPIO：PB5 配置为下降沿外部中断并上拉；PA0 在 `Core/Src/main.c` 的用户代码中初始化为按键输入。

如果用 CubeMX 重新生成代码，必须复查 I2C 引脚、I2C 速率、USB CDC、PB5 中断和 PA0 按键初始化，避免覆盖用户代码段外的手写逻辑。

## 当前固件功能

PA0 按键循环切换 4 个显示模式：

```text
HR/SpO2 -> AF live -> STRESS -> AF test -> HR/SpO2
```

当前串口输出配置在 `Core/Src/main.c`：

```text
RAW_STREAM_USB_ENABLE = 1
DIAG_STREAM_USB_ENABLE = 0
PPG_PROC_STREAM_USB_ENABLE = 1
```

串口会输出 MAX30102 原始采样和 `PPG_PROC` 处理结果，便于使用 Python 脚本采集、回放和验证。

当前算法参数以 `Core/Src/main.c` 和对应 ELF 为权威基线。2026-06-29 已据此完成模型与报告一致性同步：AF 使用 30 秒窗口、至少 20 个 PPI、10 秒/30 秒快慢刷新、18 bpm PPI-HR 容差和 20% 风险上跳保持；Stress 使用 40 秒窗口、至少 28 个 PPI、10 秒/30 秒刷新和 HR>120 隐藏门限；HR 显示使用 25 bpm 跳变门限和 10s 超时接受策略。正式 Stress JSON/头文件与 MCU 内嵌模型逐项一致，当前报告统一位于 `training_dataset/reports/full_*_20260629_current/`。

## 项目目录

| 路径 | 内容 |
| --- | --- |
| `README.md` | 面向人类读者的项目总览，也就是本文。 |
| `AI助手接手？先读我.md` | AI Agent 接手统一入口，包含当前状态、关键文件、开发边界和验证命令。 |
| `STM32_F411_Test.ioc` | STM32CubeMX 工程配置。 |
| `Core/Src/main.c` | 主固件文件，包含传感器读取、HR/SpO2、OLED、AF、Stress、按键和串口输出。 |
| `Core/Src/i2c.c` | 三路 I2C 初始化和引脚复用配置。 |
| `Core/Src/gpio.c` | GPIO 和 PB5 外部中断初始化。 |
| `Core/Inc/` | 用户头文件。 |
| `USB_DEVICE/` | USB CDC 虚拟串口相关代码。 |
| `Drivers/`、`Middlewares/` | 通常是 CubeMX 生成或库文件。 |
| `Reports/` | 总体完成进度、房颤风险功能详细总结、压力情绪功能详细总结和补充汇报。 |
| `scripts/` | Python 辅助脚本目录，包含串口采集、数据下载、模型训练、验证回放和文档生成脚本；详见 `scripts/脚本使用说明.md`。 |
| `testing dataset/` | 本地 MAX30102 采集数据、回放数据和诊断 CSV。 |
| `training_dataset/` | 公开训练数据、AF/Stress 模型参数、验证报告和数据集说明。 |
| `PCB板文件(epro2文件)/` | 主板和锂电池供电板 PCB 工程文件。 |
| `tools/` | 外部工具占位说明；OpenOCD、TeX 等工具本体不提交，按 `tools/README.md` 重新下载。 |
| `output/` | 本地生成的报告/PDF 输出目录，不随 GitHub 提交。 |
| `build/Debug/` | CMake Debug 本地构建产物目录，不随 Git 提交；clone 后需要重新构建生成 `STM32_F411_Test.elf`。 |
| `cmake/`、`CMakeLists.txt`、`CMakePresets.json` | CMake 构建配置。 |

未在本文逐项解释的 `.settings/`、`.vscode/`、`.mxproject`、启动文件、链接脚本以及 CubeMX 生成的基础文件，多数属于 IDE/CubeMX/CMake 配置或 STM32 标准工程支撑文件。

## Clone 后需要自行准备的内容

本仓库保留的是可复现项目所需的源码、配置、PCB 工程、文档、模型参数、最终验证报告和匿名化本地采集数据；大体积公开数据、构建产物和本机工具链不直接提交。下载项目后不需要一次性补齐所有外部内容，按复现目标准备即可：

| 目标 | 需要自行准备 | 说明 |
| --- | --- | --- |
| 阅读代码、文档、PCB、模型参数和最终验证结果 | 无额外下载 | 当前提交已经包含 README、AI 接手文档、Reports、PCB 工程、`training_dataset/models/` 和最终定稿验证报告。 |
| 编译固件 | ARM GNU Toolchain、CMake/Ninja，或 VS Code STM32/CubeMX 插件环境 | `build/` 不随 Git 提交，需要本机重新生成 `build/Debug/STM32_F411_Test.elf`。 |
| 重新打开或生成 CubeMX 工程 | STM32CubeMX 6.14.1 附近版本、STM32Cube FW_F4 V1.28.3 附近版本 | 当前仓库包含 `.ioc` 和 HAL/CMSIS 工程文件；只有重新生成工程时才需要重点核对 CubeMX 版本和用户代码段。 |
| 烧录调试 | DAPLink/CMSIS-DAP 或 ST-Link、OpenOCD | `tools/xpack-openocd-0.12.0-7/` 不提交。可按 `tools/README.md` 放到该路径，也可以使用本机已有 OpenOCD 并修改命令路径。 |
| 串口采集和实时诊断 | Python 3、`pyserial` | 使用 `python -m pip install pyserial` 安装后运行 `scripts/read_serial_diagnostics.py` 或 `scripts/read_max30102_raw.py`。 |
| 重新训练 AF 模型或刷新公开 AF 数据 | 可选下载 PhysioNet AF/NSR 注释文件 | 当前提交已保留 `training_dataset/physionet/` 最小注释文件；缺失或想刷新时运行 `scripts/download_af_training_data.py`。 |
| 重新训练 Stress 模型 | WESAD、`numpy`、`scipy`、`scikit-learn` | `training_dataset/wesad/` 体积较大，不提交。只有重新训练压力模型时才需要下载。 |
| 重新生成 PDF 报告 | TeX/Tectonic 工具链 | `output/` 是本地输出目录，不在 GitHub 展示；需要 PDF 时在本机重新生成。 |

更详细的数据集和脚本说明分别见 `training_dataset/公开训练集说明.md`、`testing dataset/本地采集数据集说明.md` 和 `scripts/脚本使用说明.md`。

## 复现流程建议

1. 打开 `PCB板文件(epro2文件)/` 中的两份 PCB 工程，结合上面的引脚表完成主板、供电板和外设连接。
2. 用 STM32CubeMX 打开 `STM32_F411_Test.ioc`，核对 MCU、时钟、I2C、USB CDC、PB5 EXTI 和 SWD 配置。
3. 安装 ARM GNU Toolchain、CMake/Ninja，并用 CMake 构建工程生成 `build/Debug/STM32_F411_Test.elf`。
4. 使用 DAPLink/CMSIS-DAP 和 OpenOCD 通过 SWD 烧录。
5. 烧录后连接 Type-C，打开 USB CDC 虚拟串口，使用 `scripts/read_serial_diagnostics.py` 或 `scripts/read_max30102_raw.py` 检查数据输出。
6. 若 Windows 下 COM 口枚举但打不开，优先重新插拔主板 Type-C，再检查供电、I2C 接线和 DAPLink 连接。

构建命令示例：

```powershell
cmake --preset Debug
cmake --build --preset Debug
```

如果使用 STM32CubeMX/STM32CubeIDE 插件自带 CMake，也可以把 `cmake` 替换为 `%LOCALAPPDATA%\stm32cube\bundles\cmake\4.3.1+st.1\bin\cmake.exe`。

OpenOCD 烧录命令可参考：

```powershell
& 'tools\xpack-openocd-0.12.0-7\bin\openocd.exe' -s 'tools\xpack-openocd-0.12.0-7\openocd\scripts' -f interface\cmsis-dap.cfg -f target\stm32f4x.cfg -c 'adapter speed 1000; program build/Debug/STM32_F411_Test.elf verify reset exit'
```

`tools/xpack-openocd-0.12.0-7/` 不随 Git 提交。新机器上需要下载 xPack OpenOCD 后放到这个路径，或把命令中的 OpenOCD 路径替换为本机已安装路径。

如果主板已经由 Type-C 供电，DAPLink 的 3V3 供电脚不要再并接供电，只接 SWDIO、SWCLK、GND 等必要调试线；单片机供电与电源板供电不可同时使用，以免烧毁硬件。

## Git 提交和外部数据边界

当前 Git 提交包含固件源码、CubeMX/CMake 配置、PCB 工程、文档、脚本、模型参数、最终定稿验证报告、`testing dataset/` 本地匿名采集数据，以及 `training_dataset/physionet/` 下用于 AF 训练/验证的 PhysioNet 最小注释文件。

以下内容不随 Git 提交，需要本地生成或下载：

| 路径 | 原因 | 复现方式 |
| --- | --- | --- |
| `build/` | CMake 构建产物 | 运行 `cmake --preset Debug` 和 `cmake --build --preset Debug`。 |
| `tools/xpack-openocd-0.12.0-7/` | 外部调试工具，平台相关且体积较大 | 下载 xPack OpenOCD，放到该路径，或改用本机 OpenOCD 路径。 |
| `tools/tex/` | LaTeX/PDF 工具链 | 只有重新生成报告 PDF 时需要，也可使用系统 PATH 中已有的 TeX/Tectonic。 |
| `output/` | 本地报告/PDF 输出目录 | 不在 GitHub 展示；需要时在本机生成。 |
| `training_dataset/wesad/` | WESAD 约 2.5 GB | 运行 `python scripts/download_stress_training_data.py`，只在重新训练压力模型时需要。 |
| `training_dataset/derived/` | 训练派生窗口，可重新生成 | 运行对应训练脚本重新生成。 |
| `training_dataset/reports/` 下的历史候选报告 | 中间实验产物较多 | 当前只保留 `*_current` 定稿报告；需要新实验时用脚本重新生成候选目录。 |

如果 `training_dataset/physionet/` 缺失或需要刷新公开 AF 数据，可运行：

```powershell
python scripts/download_af_training_data.py --datasets afdb ltafdb nsr2db mitdb nsrdb
```

如果需要重新训练压力模型，先准备 Python 依赖和 WESAD：

```powershell
python -m pip install numpy scipy scikit-learn
python scripts/download_stress_training_data.py
```

## 数据和算法说明

数据集、训练过程和验证结果不在本文展开，查看以下文件即可：

- `testing dataset/本地采集数据集说明.md`
- `training_dataset/公开训练集说明.md`
- `scripts/脚本使用说明.md`
- `Reports/房颤风险功能详细总结.md`
- `Reports/压力情绪功能详细总结.md`
- `Reports/总体完成进度（2026.6.23定稿）.md`

再次强调，AF risk 和 Stress index 都是课程设计中的端侧轻量推理原型。对外展示时建议使用“疑似房颤风险提示”“不规则心律风险提示”“HRV 压力指数原型”等保守表述。
