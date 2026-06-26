# tools 目录说明

本目录用于放置本地外部工具，例如 xPack OpenOCD、Tectonic/TeX 等。

这些工具属于可重新下载的本地依赖，体积较大或带有平台相关二进制文件，因此默认不提交到 GitHub。新环境复现时按需准备：

| 工具 | 期望路径 | 用途 |
| --- | --- | --- |
| xPack OpenOCD 0.12.0-7 | `tools/xpack-openocd-0.12.0-7/` | DAPLink/CMSIS-DAP SWD 烧录和调试。 |
| TeX/Tectonic | `tools/tex/` 或系统 PATH | 重新生成 `output/pdf/` 下的 PDF 报告。 |

如果 OpenOCD 已安装在其他位置，可以不放进 `tools/`，直接把 README 和接手文档中的 OpenOCD 命令路径改成本机路径即可。
