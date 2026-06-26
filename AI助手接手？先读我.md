# AI助手接手？先读我

更新时间：2026-06-24

这是给下一位 AI 助手接手项目的唯一入口文档。人类第一次通览项目可先读 `README.md`；无论使用什么 AI 助手接手本项目，都从本文开始，再按本文列出的顺序打开进度、房颤风险功能详细总结、压力情绪功能详细总结和数据集说明。若旧段落与 `2026-06-23 验收定稿声明`、`2026-06-24 数据目录中文整理` 或 `2026-06-24 Python 脚本目录整理` 冲突，以本文、当前 `Core/Src/main.c`、当前 Python 脚本和当前数据目录为准。

## 先读顺序

1. `AI助手接手？先读我.md`：当前项目地图、行动边界和后续阅读路线。
2. `Reports/总体完成进度（2026.6.23定稿）.md`：总进度，最前面的 `2026-06-23 验收定稿同步` 是最新状态。
3. `Reports/房颤风险功能详细总结.md`：房颤风险功能的设计、数据集、结果、限制和展示口径。
4. `Reports/压力情绪功能详细总结.md`：压力/情绪 HRV 功能的训练、移植、显示策略和验证结果。
5. `testing dataset/本地采集数据集说明.md`：本地 MAX30102 采集、CSV 字段阅读方法、每组数据用途和 HR/SpO2/OLED/AF/压力回放说明。
6. `training_dataset/公开训练集说明.md`：公开训练数据、模型文件和验证报告的中文说明。
7. `scripts/脚本使用说明.md`：Python 脚本集中目录、用途和常用命令。

## 2026-06-24 数据目录中文整理

本次只整理项目入口文档、数据说明文档、测试数据目录名和根目录散落 CSV 的归档位置，没有改动任何 CSV/JSON 数据内容、固件源码或模型参数。验证脚本只做了接手一致性同步：`scripts/validate_stress_hrv_live.py` 更新默认本地 PPG 分组路径以匹配中文目录名；`scripts/validate_mcu_af_live.py` 将默认 slow step 同步为 30s、HR jump gate 同步为 25 bpm，以匹配当前 `Core/Src/main.c`。这些同步不覆盖正式模型，也不改封版报告目录。

文件命名调整：

| 旧路径 | 新路径 |
| --- | --- |
| `README_接手指南.md` | `AI助手接手？先读我.md` |
| `testing dataset/README.md` | `testing dataset/本地采集数据集说明.md` |
| `training_dataset/README.md` | `training_dataset/公开训练集说明.md` |

本地采集目录已保留编号并统一匿名为 subject_A / subject_B，例如：

| 目录 | 含义 |
| --- | --- |
| `testing dataset/112_subject_A_pressure_baseline_light_stable_20260617_140429/` | subject_A 第一组 90s 稳定轻触压力基线。 |
| `testing dataset/113_subject_A_pressure_baseline_light_stable_20260617_141049/` | subject_A 第二组 90s 稳定轻触压力基线。 |
| `testing dataset/114_subject_B_af_high_review_20260617_144713/` | subject_B AF 偶发高值排查采集。 |

根目录原本散落的 `diag_30s.csv`、`diag_30s_repeat.csv`、`diag_30s_waveform.csv`、`diag_preflash_20260611.csv` 已归档到：

```text
testing dataset/00_串口烟测与原始数据检查/根目录诊断CSV归档/
```

`training_dataset/physionet/`、`training_dataset/wesad/`、`training_dataset/models/`、`training_dataset/reports/` 的英文目录名保留，因为训练和验证脚本默认使用这些路径。当前 Git 提交保留 `physionet/` 最小注释文件、模型和最终 current 报告；`wesad/`、`derived/`、历史报告、`build/` 和外部工具目录需要按说明下载或生成。

## 2026-06-24 Python 脚本目录整理

根目录原本散落的 Python 辅助脚本已经统一收进 `scripts/`，并新增 `scripts/脚本使用说明.md`。后续运行脚本时仍建议站在项目根目录执行命令，例如 `python scripts/validate_mcu_af_live.py`，不要先 `cd scripts`，因为脚本默认按项目根目录解析 `training_dataset/`、`testing dataset/`、`Core/Src/main.c` 等相对路径。

当前脚本目录包括串口采集、数据下载、AF/Stress 训练、MCU 等价验证和补充汇报 Word 生成脚本。根目录不再直接放置 `.py` 文件。

## 2026-06-24 报告目录整理

根目录下原本散落的进度归档、功能详细总结和补充汇报已经统一收进 `Reports/`。根目录只保留工程入口、硬件/固件配置、构建配置和顶层目录，Python 辅助脚本统一收进 `scripts/`，避免下一位 AI 在根目录里混淆“入口文档”“历史报告”和“脚本工具”。

| 文件 | 当前路径 |
| --- | --- |
| 总进度归档 | `Reports/总体完成进度（2026.6.23定稿）.md` |
| 房颤风险功能详细总结 | `Reports/房颤风险功能详细总结.md` |
| 压力情绪功能详细总结 | `Reports/压力情绪功能详细总结.md` |
| 项目补充汇报 Word 文档 | `Reports/脉搏测试仪项目补充汇报.docx` |

## 2026-06-23 验收定稿声明

本项目当前版本正式作为验收定稿版本。2026-06-17 完成最后一轮固件、模型、数据和验证工作；截至 2026-06-23，没有再修改 `Core/Src/main.c`、模型参数、验证脚本、公开数据集或本地采集数据。当前 ELF、模型和报告与 2026-06-17 技术快照一致；2026-06-24 之后的文档、路径和验证脚本默认参数同步只为接手一致性服务，不改变封版结论。

验收定稿范围：

```text
STM32F411 + MAX30102
心率 HR
血氧 SpO2
0.96 寸主显示屏
0.91 寸 PPG 波形屏
AF risk / 不规则心律风险提示
1-99 HRV 压力指数
AFDB 内置 test 回放
USB CDC raw + PPG_PROC 诊断输出
DAPLink + OpenOCD SWD 烧录
```

最终显示模式：

```text
HR/SpO2 -> AF live -> STRESS -> AF test -> HR/SpO2
```

验收定稿产物：

| 产物 | 路径 |
| --- | --- |
| 最终固件源码 | `Core/Src/main.c` |
| 最终构建产物 | `build/Debug/STM32_F411_Test.elf`，本地生成，不随 Git 提交。 |
| AF 模型 | `training_dataset/models/af_nb_model.json`、`af_nb_model.h` |
| Stress 模型 | `training_dataset/models/stress_hrv_model.json`、`stress_hrv_model.h` |
| AF 最终验证 | `training_dataset/reports/full_af_validation_20260617_current/` |
| Stress live 最终验证 | `training_dataset/reports/full_stress_live_validation_20260617_current/` |
| Stress 训练验证 | `training_dataset/reports/full_stress_train_validation_20260617_current/` |
| 最终关键本地采集 | `testing dataset/112_subject_A_pressure_baseline_light_stable_20260617_140429/`、`testing dataset/113_subject_A_pressure_baseline_light_stable_20260617_141049/`、`testing dataset/114_subject_B_af_high_review_20260617_144713/` |

验收前封版规则：

- 不再调整 HR/SpO2、PPI 过滤、AF、Stress、OLED 波形或显示参数。
- 不再重新训练 AF/Stress 模型，不覆盖 `training_dataset/models/`。
- 不再为了“看起来更好”修改风险概率或压力指数。
- 只允许做编译、烧录、接线检查、COM7 恢复和验收演示操作。
- 如果现场出现硬件连接问题，先检查 Type-C、DAPLink、I2C 和串口，不要临时改算法。

验收口径：

- AF 功能称为“基于 PPI 不规则性的 AF risk / 不规则心律风险提示”，不是医学诊断。
- Stress 功能称为“基于 PPG HRV 的压力指数原型”，不是心理疾病诊断，也不识别开心/难过。
- 公开 AF 数据来自 ECG RR 标注，但模型只使用 RR/PPI 间期统计特征；真实 AF 患者 PPG 临床验证不在本项目范围内。
- 高心率时 Stress 显示 `-- high HR`，避免把运动/非静息状态解释成心理压力。

## 2026-06-17 定稿技术快照

本节是验收定稿所采用的最终技术参数快照：STM32F411 + MAX30102 已经实现心率、血氧、0.96 寸主屏、0.91 寸波形屏、AF 风险、压力/情绪指数、AF test 回放。最后一次烧录已成功，OpenOCD 返回 `Programming Finished`、`Verified OK`、`Resetting Target`；烧录后 COM7 可能不自动枚举，重插 Black Pill Type-C 是已知恢复方法。

显示模式顺序：

```text
HR/SpO2 -> AF live -> STRESS -> AF test -> HR/SpO2
```

当前 `Core/Src/main.c` 关键状态：

```text
RAW_STREAM_USB_ENABLE = 1
DIAG_STREAM_USB_ENABLE = 0
PPG_PROC_STREAM_USB_ENABLE = 1

PPG_HR_WARMUP_SAMPLES = 1500       # 15s
PPG_SPO2_WARMUP_SAMPLES = 2000     # 20s
PPG_AUTOCORR_INTERVAL_SAMPLES = 100 # 约 1s
PPG_HR_CONFIRM_SAMPLES = 3
PPG_HR_CONFIRM_TOLERANCE_BPM = 12
PPG_HR_MAX_DISPLAY_JUMP_BPM = 25
PPG_HR_JUMP_ACCEPT_TIMEOUT_MS = 10000

AF_MIN_PPI_COUNT = 20
AF_WINDOW_TARGET_MS = 30000
AF_RISK_FAST_STEP_MS = 10000
AF_RISK_SLOW_STEP_MS = 30000
AF_STABLE_RISK_THRESHOLD_PERCENT = 20
AF_MAX_UP_JUMP_PERCENT = 20
PPG_PPI_QUALITY_MAX_REJECT_PCT = 20

STRESS_HRV_MIN_INTERVAL_COUNT = 28
STRESS_HRV_WINDOW_TARGET_MS = 40000
STRESS_HRV_FIRST_STEP_MS = 10000
STRESS_HRV_REFRESH_STEP_MS = 30000
STRESS_HRV_HIGH_HR_BPM = 120
```

心率显示策略：手指稳定后 15s warmup，约每 1s 出一次自相关候选；首次需要 3 个候选一致，显示值用最近 5 个心率中位数。单次显示跳变超过 25 bpm 会保持旧值；如果新趋势持续 10s 才接受。血氧 20s warmup 后按 PPG 滚动窗口计算。

AF 方法：PPG robust peak -> PPI -> HRV/不规则节律特征 -> 离散朴素贝叶斯 -> 0-100% AF risk。训练来源主要是 AFDB/LTAFDB/NSR2DB 的 RR/PPI 间期标注，MCU 端只做特征计算和模型推理，不在 MCU 上训练。输出必须表述为“AF risk / 不规则心律风险提示”，不是诊断。

AF live 显示策略：高风险或未出值阶段每 10s 尝试更新；当前显示风险已经低于 20% 后每 30s 尝试更新。PPI 异常比例超过 20%、窗口不足或 HR 差异过大时，已有值会保持；若新值比旧值上跳超过 20%，保持旧值，避免正常人瞬间跳到 90-100%。AF test 不走这套稳定策略，它只播放内置 AFDB `06995` PPI 段。

压力/情绪方法：PPG robust peak -> PPI -> HRV 特征 -> Logistic Regression -> 1-99 stress index。训练来源是 WESAD，默认用 wrist BVP；本地 MAX30102 数据只作为正常烟测/回放验证。显示区间：`1-29 relax`、`30-59 normal`、`60-79 medium`、`80-99 high`。首次未出值时每 10s 尝试更新，出值后每 30s 尝试更新；当 HR > 120 bpm 时 STRESS 第二行显示 `-- high HR`，后台仍计算，但显示层当作“暂无可参考压力值”，刷新节奏走 10s。

0.91 寸波形：只在心率确认、PPG valid 且 HR>0 后滚动显示，列推进约 40ms，峰值标记使用 robust peak。不要为了 AF/压力去改 HR/SpO2 主计算或小屏波形归一化。

## 新增/关键文件速览

| 路径 | 当前用途 |
| --- | --- |
| `Core/Src/main.c` | 定稿固件主文件，集中包含 HR/SpO2、OLED、AF、Stress、串口输出、按键模式。 |
| `scripts/download_stress_training_data.py` | WESAD 下载/整理入口。 |
| `scripts/train_stress_hrv_model.py` | 训练压力 HRV Logistic Regression，输出 JSON/C 头文件和验证报告。 |
| `scripts/validate_stress_hrv_live.py` | 复刻 MCU stress live 显示/质量门控逻辑，回放本地 processed PPG。 |
| `scripts/validate_mcu_af_live.py` | 复刻 MCU AF live/test 逻辑，含 PPI 质量门控、跳变保持、快慢刷新策略。 |
| `scripts/read_max30102_raw.py` | 串口采集/离线处理 MAX30102 raw 数据，也用于生成本地 processed/replay 文件。 |
| `training_dataset/models/stress_hrv_model.json` | 压力模型参数，Python/验证脚本默认读取。 |
| `training_dataset/models/stress_hrv_model.h` | 压力模型 C 头文件参数副本。 |
| `training_dataset/models/af_nb_model.json` | AF 朴素贝叶斯模型参数。 |
| `training_dataset/models/af_nb_model.h` | AF 模型 C 头文件参数副本。 |
| `training_dataset/reports/full_af_validation_20260617_current/` | 2026-06-17 当前 AF 全量验证报告。 |
| `training_dataset/reports/full_stress_live_validation_20260617_current/` | 2026-06-17 当前压力 MCU live 等价回放报告。 |
| `training_dataset/reports/full_stress_train_validation_20260617_current/` | 2026-06-17 WESAD 压力训练/验证隔离报告。 |
| `testing dataset/112_subject_A_pressure_baseline_light_stable_20260617_140429/` | 本地 90s 稳定轻触压力基线采集。 |
| `testing dataset/113_subject_A_pressure_baseline_light_stable_20260617_141049/` | 第二组本地 90s 稳定轻触压力基线采集。 |
| `testing dataset/114_subject_B_af_high_review_20260617_144713/` | subject_B AF 偶发高值排查采集，后续用于验证 PPI 假峰/门控策略。 |
| `README.md` | 面向人类读者的项目总览，说明硬件平台、引脚、CubeMX 配置、目录结构和复现路线。 |

## 当前一句话状态

项目已经实现 STM32F411 + MAX30102 的心率、血氧、OLED 显示、基于 PPG 峰间期 PPI 的疑似房颤风险原型，以及基于 HRV 的压力/情绪指数。HR/SpO2 和小屏波形是稳定基础功能；AF 与压力都是旁路模块，已经完成 PC 端训练/验证、MCU 移植、显示策略和烧录验证。

## 项目结构地图

| 路径 | 作用 |
| --- | --- |
| `Core/Src/main.c` | 主固件。包含 MAX30102 驱动、HR/SpO2、OLED 显示、按键轮询、AF live/test、Stress live 计算。 |
| `Core/Src/i2c.c` | I2C 速率配置。当前大屏/小屏 400 kHz，MAX30102 100 kHz。CubeMX 生成后必须复查。 |
| `Core/Inc/` | STM32 用户头文件。 |
| `USB_DEVICE/` | USB CDC 虚拟串口代码。 |
| `Drivers/`、`Middlewares/` | STM32 HAL/CMSIS/USB 库，通常不要改。 |
| `training_dataset/` | 公开 PhysioNet RR/PPI、WESAD 数据、训练出的 AF/Stress 模型和验证报告。 |
| `testing dataset/` | 本地 MAX30102 采样数据、HR/SpO2/OLED/AF/Stress 回放测试数据。没有真实 AF 患者 PPG。 |
| `Reports/` | 进度归档、AF/压力情绪功能详细总结和补充汇报文档。 |
| `scripts/` | Python 辅助脚本目录，包含串口采集、数据下载、模型训练、验证回放和文档生成脚本；先读 `scripts/脚本使用说明.md`。 |
| `tools/` | 外部工具占位说明；OpenOCD 和 TeX 工具本体不随 Git 提交。 |
| `build/Debug/` | CMake 本地构建产物目录，不随 Git 提交；clone 后需重新构建生成 `STM32_F411_Test.elf`。 |
| `README.md` | 面向人类读者的项目总览。 |
| `AI助手接手？先读我.md` | 本文，下一位 AI 助手的唯一入口。 |
| `Reports/总体完成进度（2026.6.23定稿）.md` | 总进度存档。 |
| `Reports/房颤风险功能详细总结.md` | 房颤风险功能详细总结。 |
| `Reports/压力情绪功能详细总结.md` | 压力情绪功能详细总结。 |

## 当前固件功能

- 默认大屏显示心率/血氧，小屏显示 Pause/Loading/等效 PPG 波形。
- PA0 按键轮询切换显示：
  1. vitals：心率/血氧；
  2. live AF：实时传感器来源的 `AF %`；
  3. stress：实时传感器来源的压力指数和英文档位；
  4. test AF：内置 AFDB PPI 段来源的 `[test]` + `AF %`。
- 当前串口配置：
  - `RAW_STREAM_USB_ENABLE=1`：逐样本输出 `t_ms,red,ir,irq_count,sample_count`
  - `DIAG_STREAM_USB_ENABLE=0`
  - `PPG_PROC_STREAM_USB_ENABLE=1`：每秒输出一行 `PPG_PROC`，包含处理后 HR/SpO2/AF/Stress/PPI 诊断字段。
- 烧录或 OpenOCD reset 后，Windows 可能需要重新插拔黑丸 Type-C 才能稳定打开 COM 口。

## AF 风险功能现状

当前 AF 不是医学诊断，而是“基于 PPI 不规则性的疑似房颤风险提示”。

MCU live AF 计算条件：

```text
finger_present != 0
ppg_signal_valid != 0
heart_rate_bpm > 0
spo2_percent > 0
```

MCU 当前窗口策略：

```text
窗口长度约 30s
至少 20 个 PPI
未出值或高风险阶段每 10s 尝试刷新
低风险稳定后每 30s 尝试刷新
最多保留 100 个 PPI
PPI 推算 HR 与当前 HR 差异 > 18 bpm 时不更新/保持旧值
PPI 异常比例 > 20% 时不更新/保持旧值
新风险比旧风险上跳 > 20% 时不更新/保持旧值
```

test AF 内置的是 AFDB `06995` 的一段 AF rhythm PPI/RR 间期，不是硬编码概率。进入 test 后约 30.244 秒窗口满足，等价验证风险约 85%。

## 压力/情绪功能现状

当前 Stress 不是“开心/难过”识别，而是基于 HRV 的压力指数。它使用 PPG PPI 特征和 WESAD 训练出的 Logistic Regression 模型，输出 1-99 的压力数值：

```text
1-29   relax
30-59  normal
60-79  medium
80-99  high
```

MCU 当前窗口策略：

```text
窗口长度约 40s
至少 28 个 PPI
首次未出值时每 10s 尝试刷新
出值后每 30s 尝试刷新
HR > 120 bpm 时 STRESS 第二行显示 -- high HR，后台仍计算
```

压力值对高心率和 PPI 异常比较敏感。当前产品策略不是硬改模型，而是在高心率时隐藏数值，避免把运动/兴奋/手指不稳误解成心理压力。

## 当前重要结果

2026-06-17 当前验证结果：

```text
AF local_ppg normal: valid=28/287, median=0%, mean=3.786%, ge80=3.571%
mcu_test_afdb_06995: first_valid=30.244s, risk=85%
public_afdb normal: ge80=0.55%
public_ltafdb normal: ge80=1.348%
public_nsr2db normal: ge80=0.031%
public_afdb af: median=57%, mean=59.425%
public_ltafdb af: median=100%, mean=68.269%

Stress WESAD all: AUC=0.9412, nonstress_median=50, stress_median=87
Stress WESAD test_by_subject: AUC=0.9293, nonstress_median=52, stress_median=87
Stress local live: files=130, displayed=12/25, display_median=47.5, display_max=63
```

解释：AF 当前策略明显压住了正常人偶发 90-100% 跳变，同时保留 AF 数据响应；Stress 在 WESAD 上有较好区分度，本地静息显示多在 40-60 附近。两者都依赖 PPI 质量，不应宣传为医学诊断。

## 不要轻易改的东西

- 不要重写 HR/SpO2 主算法。
- 不要重写小屏 0.91 寸波形算法。
- 不要让 AF 功能反向影响 HR/SpO2 的有效性判断。
- 不要删除英文模型/报告文件名，脚本默认读取英文路径；中文文件多为人工查看副本。
- 不要把 `testing dataset/` 当 AF 阳性训练集，它没有真实 AF 患者 PPG。
- 不要把质量门控理解成“强行降低概率”。后续优化应是质量不足显示 `--`。

## Python 脚本入口

| 脚本 | 用途 |
| --- | --- |
| `scripts/download_af_training_data.py` | 下载 PhysioNet 最小注释文件。 |
| `scripts/train_af_naive_bayes.py` | 训练离散朴素贝叶斯 AF 风险模型。 |
| `scripts/simulate_af_ppi_risk.py` | 用模拟 PPI 做风险行为检查。 |
| `scripts/validate_mcu_af_live.py` | 复刻 MCU live/test AF 逻辑，是最重要的验证脚本。 |
| `scripts/download_stress_training_data.py` | 下载/整理 WESAD 压力训练数据。 |
| `scripts/train_stress_hrv_model.py` | 训练 HRV 压力 Logistic Regression 模型。 |
| `scripts/validate_stress_hrv_live.py` | 复刻 MCU stress live 显示和质量门控逻辑。 |
| `scripts/read_max30102_raw.py` | raw 模式下采集 MAX30102 red/ir 串口数据。 |
| `scripts/read_serial_diagnostics.py` | 当前诊断 CSV 读取和性能检查。 |
| `scripts/build_pulse_report_docx.py` | 生成项目补充汇报 Word 文档的辅助脚本。 |

脚本运行前先读 `scripts/脚本使用说明.md`。所有命令建议在项目根目录执行，不要先 `cd scripts`。

## 常用命令

编译：

```powershell
cmake --preset Debug
cmake --build --preset Debug
```

如果使用 STM32CubeMX/STM32CubeIDE 插件自带 CMake，可把 `cmake` 替换为 `%LOCALAPPDATA%\stm32cube\bundles\cmake\4.3.1+st.1\bin\cmake.exe`。

烧录：

```powershell
& 'tools\xpack-openocd-0.12.0-7\bin\openocd.exe' -s 'tools\xpack-openocd-0.12.0-7\openocd\scripts' -f interface\cmsis-dap.cfg -f target\stm32f4x.cfg -c 'adapter speed 1000; program build/Debug/STM32_F411_Test.elf verify reset exit'
```

`tools/xpack-openocd-0.12.0-7/` 是本地下载工具目录，不随 Git 提交。clone 后先按 `tools/README.md` 准备 OpenOCD，或把命令路径改成本机 OpenOCD。

重新训练 AF 模型：

```powershell
python scripts/train_af_naive_bayes.py
```

快速跑 MCU 等价 AF 验证：

```powershell
python scripts/validate_mcu_af_live.py
```

跑当前定稿 AF 全量验证：

```powershell
python scripts/validate_mcu_af_live.py --out-dir training_dataset/reports/full_af_validation_YYYYMMDD_candidate --ppg-glob "testing dataset/**/*processed*.csv" --sim-runs 120 --sim-duration-s 180 --include-public --public-datasets afdb,ltafdb,nsr2db --public-record-limit 0 --quality-gate-experiment
```

额外跑 MITDB/NSRDB 泛化验证时再指定：

```powershell
python scripts/validate_mcu_af_live.py --out-dir training_dataset/reports/full_af_extra_validation_YYYYMMDD_candidate --ppg-glob "testing dataset/**/*processed*.csv" --sim-runs 0 --include-public --public-datasets mitdb,nsrdb --public-record-limit 0
```

跑当前定稿 Stress live 验证：

```powershell
python scripts/validate_stress_hrv_live.py --out-dir training_dataset/reports/full_stress_live_validation_YYYYMMDD_candidate --ppg-glob "testing dataset/**/*processed*.csv" --ppg-group all_local_processed --step-s 60
```

隔离训练/验证压力模型，不覆盖正式模型：

```powershell
python scripts/train_stress_hrv_model.py --base-dir training_dataset/reports/full_stress_train_validation_YYYYMMDD_candidate --wesad-dir training_dataset/wesad --local-ppg-glob "testing dataset/**/*processed*.csv"
```

## 后续优先级

1. 继续维护 HR/SpO2 和 OLED 波形稳定性，不要为 AF 牺牲基础功能。
2. AF/Stress 后续优化先在电脑端脚本验证，再移植到 MCU。
3. 如果拿到真实 AF 患者 PPG，先离线验证，再考虑重训或调参。
4. 压力功能如果继续优化，优先处理高心率/运动状态提示，不要把高 HR 直接解释成心理压力。
5. 对外展示时使用“疑似房颤风险 / 不规则心律提示 / 端侧轻量推理 / HRV 压力指数原型”这类保守表述。
