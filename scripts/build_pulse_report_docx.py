# 中文说明：
#   生成项目补充汇报用的 Word 文档（.docx）的辅助脚本。
#   它直接拼装 docx 内部 XML，用于快速输出“脉搏测试仪项目补充汇报”类文档。
#   这个脚本和 AF 模型训练/单片机运行无直接关系，主要是文档生成工具。

from __future__ import annotations

import html
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Reports" / "脉搏测试仪项目补充汇报.docx"


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def run_xml(text: str, bold: bool = False, italic: bool = False) -> str:
    props = []
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f"<w:r>{rpr}<w:t xml:space=\"preserve\">{esc(text)}</w:t></w:r>"


def para(text: str = "", style: str | None = None, num_id: int | None = None,
         ilvl: int = 0, keep_next: bool = False, code: bool = False) -> str:
    ppr = []
    if style:
        ppr.append(f"<w:pStyle w:val=\"{style}\"/>")
    if num_id is not None:
        ppr.append(
            f"<w:numPr><w:ilvl w:val=\"{ilvl}\"/><w:numId w:val=\"{num_id}\"/></w:numPr>"
        )
    if keep_next:
        ppr.append("<w:keepNext/>")
    if code:
        ppr.append("<w:shd w:fill=\"F4F6F8\"/>")
    ppr_xml = f"<w:pPr>{''.join(ppr)}</w:pPr>" if ppr else ""
    return f"<w:p>{ppr_xml}{run_xml(text)}</w:p>"


def heading(text: str, level: int = 1) -> str:
    return para(text, style=f"Heading{level}", keep_next=True)


def numbered(text: str) -> str:
    return para(text, num_id=1)


def bullet(text: str) -> str:
    return para(text, num_id=2)


def code_line(text: str) -> str:
    return para(text, style="Code", code=True)


def build_document_xml() -> str:
    body: list[str] = []

    body.append(para("脉搏测试仪项目补充汇报", style="Title"))
    body.append(para("关于 MAX30102 传感器与 STM32F411CEU6 算法分工的说明", style="Subtitle"))

    body.append(para(
        "陈老师您好，关于我们的脉搏测试仪项目，我想向您做个补充汇报。今天中期验收的时候，"
        "我以为是简单展示一下进度就行了，所以在技术细节，特别是传感器和单片机算法上的准备"
        "不够充分，没能向您解释清楚。为了让您更准确地评估我们的工作量，我想详细梳理一下"
        "我们使用的 MAX30102 传感器与单片机 STM32F411CEU6 在系统中的真实分工，特别是传感器"
        "原始信号的局限性，以及我们在 MCU 端手写实现的核心数据处理算法。"
    ))

    body.append(heading("MAX30102 在系统中的作用", 1))
    body.append(para(
        "在整个系统架构中，MAX30102 仅作为一个底层的光电模拟前端。它在系统中主要承担"
        "“发光、接收、量化”的物理采样工作，其内部并没有直接输出心率 bpm 或血氧 SpO2 的"
        "生理特征解算功能。"
    ))
    body.append(para("传感器承担的物理工作主要是："))
    body.append(numbered("时序驱动发光：驱动红光 LED，约 660nm，和红外 LED，约 880nm，以配置好的电流、采样率和脉冲宽度照射手指。"))
    body.append(numbered("光电接收：接收经过手指组织吸收、散射、反射后返回的微弱光信号。"))
    body.append(numbered("ADC 量化：通过内部 18 位 ADC，把微弱光电流转换成 Red ADC Count 和 IR ADC Count。"))
    body.append(numbered("FIFO 缓存与 I2C 输出：把这些原始计数值放入 FIFO，再由 STM32 通过 I2C 读取。"))
    body.append(para(
        "也就是说，MAX30102 输出的不是“已经处理好的心率/血氧”，而是一串 Red/IR 原始光强"
        "时间序列。这些原始数据里确实包含心跳造成的血液容积周期变化，但这部分信号很小，"
        "同时混杂了大量非生理干扰，不能直接用于显示。"
    ))

    body.append(heading("原始信号的主要问题", 1))
    body.append(numbered(
        "巨大的直流背景：大部分光强变化来自皮肤、骨骼、肌肉、非搏动性血液、手指厚度、"
        "接触距离等静态因素。真正与心跳相关的交流分量只占很小一部分。"
    ))
    body.append(numbered(
        "低频基线漂移：呼吸、手指接触压力、手指位置微小变化，会让整体光强缓慢上下漂移。"
    ))
    body.append(numbered(
        "高频噪声和毛刺：环境光残留、电源噪声、肌肉微震颤、手指轻微移动，会在波形上叠加短时尖峰。"
    ))
    body.append(numbered(
        "接触状态不稳定：轻放、强按、歪放、拿开手指时，Red/IR 比值和波形质量都会明显变化。"
    ))
    body.append(numbered(
        "强按失真：用力按压时局部血流被压迫，心率有时还能提取，但 SpO2 的 Red/IR ratio 会失真，所以不能强行显示血氧。"
    ))

    body.append(heading("MCU 端核心数据处理算法", 1))
    body.append(para("针对这些问题，我们在 STM32 端主要实现了以下核心数据处理算法："))

    body.append(heading("1. DC/AC 分离和软件滤波", 2))
    body.append(para(
        "MAX30102 输出的 Red/IR 原始值中，绝大部分是直流背景，真正由心跳引起的脉搏波动只占很小一部分。"
        "因此我们首先对 Red 和 IR 分别做 DC 基线跟踪："
    ))
    body.append(code_line("dc = dc + 0.02 * (raw - dc)"))
    body.append(para("然后提取交流分量："))
    body.append(code_line("ac = raw - dc"))
    body.append(para(
        "这样可以把手指厚度、皮肤反射、环境背景、接触距离等慢变化直流成分去掉，得到与血液容积周期变化相关的 PPG 波形。"
    ))
    body.append(para("在此基础上，我们又对 AC 分量做一阶 IIR 低通滤波："))
    body.append(code_line("filt = filt + 0.20 * (ac - filt)"))
    body.append(para(
        "这个滤波用于抑制高频毛刺、单点噪声和接触抖动，使后续心率和血氧计算不直接受原始噪声影响。"
    ))

    body.append(heading("2. 滑动窗口 RMS 质量评估", 2))
    body.append(para(
        "我们维护了 1000 点滑动窗口。当前采样率是 100Hz，因此窗口对应最近约 10 秒的 PPG 波形。"
        "算法不是根据某一个瞬时 ADC 值判断，而是基于这一段时间内的波形质量进行计算。"
    ))
    body.append(para("在窗口中，我们计算 Red/IR 滤波波形的 RMS："))
    body.append(code_line("RMS = sqrt(sum(x^2) / N)"))
    body.append(para(
        "RMS 用于衡量交流脉搏波是否足够明显。只有 IR 直流强度足够，并且 IR AC RMS 达到门限，"
        "才认为手指有效放置。这样可以避免无手指、弱接触、环境光残留或底噪导致误显示。"
    ))

    body.append(heading("3. 自相关心率估计", 2))
    body.append(para(
        "心率计算不是简单数 MAX30102 的中断次数，也不是数 FIFO 采样点。我们使用 IR 滤波后的 PPG 波形进行自相关分析。"
    ))
    body.append(para(
        "自相关的核心思想是：取一段 PPG 波形，寻找它和自身延迟多少个采样点后最相似。"
        "这个最佳延迟对应一个心跳周期，再换算为 bpm。"
    ))
    body.append(para(
        "这一步能利用整段波形的周期性，相比只找单个峰值，对局部噪声、峰形变化和偶发毛刺更稳定。"
        "为了避免误判，我们还限制心率有效范围为 45 到 190 bpm，并要求相关性达到一定门限后才接受结果。"
    ))

    body.append(heading("4. 心率稳定确认与中位数平滑", 2))
    body.append(para(
        "单次自相关结果仍然可能受到接触抖动或短时噪声影响，所以我们没有直接显示第一个心率候选值，"
        "而是要求连续 3 个 HR 候选结果彼此接近，容差约 12 bpm，才正式确认心率。"
    ))
    body.append(para(
        "确认后，我们保存最近 5 个 HR 结果，并取中位数作为显示值。中位数相比普通平均更能抵抗偶发异常值，"
        "可以减少心率显示突然跳变。"
    ))

    body.append(heading("5. SpO2 的 Red/IR AC/DC Ratio 计算", 2))
    body.append(para(
        "血氧计算不是直接看 Red 或 IR 的绝对光强，而是看两种波长下脉搏波动相对于直流背景的比例关系。我们计算："
    ))
    body.append(code_line("ratio = (red_rms / red_dc) / (ir_rms / ir_dc)"))
    body.append(para("然后根据经验公式估算："))
    body.append(code_line("SpO2 = 101.0 - 7.0 * ratio"))
    body.append(para(
        "这一步是从 Red/IR 原始采样中提取血氧信息的核心。它需要前面的 DC/AC 分离、滤波、RMS 计算都比较稳定，"
        "否则 ratio 会明显失真。"
    ))

    body.append(heading("6. SpO2 质量门限与异常拒绝", 2))
    body.append(para(
        "SpO2 对接触状态非常敏感。强按、低灌注、手指偏移时，Red/IR ratio 很容易失真。"
        "为此我们设置了质量门限和 ratio 合理范围。"
    ))
    body.append(para(
        "如果信号质量不足，算法会让 SpO2 显示为空白，而不是强行输出一个看似正常但实际不可靠的数字。"
        "这个设计是为了提高结果可信度，而不是追求任何情况下都显示一个数值。"
    ))

    body.append(heading("其他实现与测试验证", 1))
    body.append(para(
        "此外，我们还实现了手指检测、手指放上后的 warmup 稳定等待、手指拿开后的状态清空、低质量信号拒绝显示、"
        "小屏 PPG 波形显示、大屏 HR/SpO2 显示，以及串口诊断输出。"
    ))
    body.append(para(
        "我们也采集并回放了多组测试数据，包括无手指基线、稳定轻放、强按、手指延迟放上、手指拿开、"
        "手表血氧参考和连续串口读取等场景，用来验证算法在不同接触状态下的表现。"
    ))

    body.append(heading("总结", 1))
    body.append(para(
        "综上，MAX30102 负责的是光电采样前端，把光学变化转换成 Red/IR 原始数字波形；"
        "而 STM32F411CEU6 负责从这两路原始 PPG 波形中完成滤波、去基线、质量判断、心率周期估计、"
        "血氧 ratio 计算、异常拒绝显示和状态控制。单片机并不是简单计数，而是在完成脉搏测试仪中最核心的"
        "数据处理和生理参数提取工作。"
    ))

    sect = (
        "<w:sectPr>"
        "<w:pgSz w:w=\"12240\" w:h=\"15840\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\" "
        "w:header=\"720\" w:footer=\"720\" w:gutter=\"0\"/>"
        "</w:sectPr>"
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:wpc=\"http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas\" "
        "xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\" "
        "xmlns:o=\"urn:schemas-microsoft-com:office:office\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
        "xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\" "
        "xmlns:v=\"urn:schemas-microsoft-com:vml\" "
        "xmlns:wp14=\"http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing\" "
        "xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" "
        "xmlns:w10=\"urn:schemas-microsoft-com:office:word\" "
        "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
        "xmlns:w14=\"http://schemas.microsoft.com/office/word/2010/wordml\" "
        "xmlns:wpg=\"http://schemas.microsoft.com/office/word/2010/wordprocessingGroup\" "
        "xmlns:wpi=\"http://schemas.microsoft.com/office/word/2010/wordprocessingInk\" "
        "xmlns:wne=\"http://schemas.microsoft.com/office/word/2006/wordml\" "
        "xmlns:wps=\"http://schemas.microsoft.com/office/word/2010/wordprocessingShape\" "
        "mc:Ignorable=\"w14 wp14\"><w:body>"
        + "".join(body)
        + sect
        + "</w:body></w:document>"
    )


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/><w:next w:val="Subtitle"/><w:qFormat/>
    <w:pPr><w:spacing w:before="0" w:after="160"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:b/><w:sz w:val="36"/><w:color w:val="1F4E79"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:after="280"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/><w:color w:val="666666"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="260" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:b/><w:sz w:val="32"/><w:color w:val="1F4E79"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:b/><w:sz w:val="28"/><w:color w:val="2F5597"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Code">
    <w:name w:val="Code"/>
    <w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:spacing w:before="80" w:after="120"/><w:ind w:left="360"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr>
  </w:style>
</w:styles>
"""

NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0">
    <w:multiLevelType w:val="singleLevel"/>
    <w:lvl w:ilvl="0">
      <w:start w:val="1"/>
      <w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1."/>
      <w:lvlJc w:val="left"/>
      <w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:ind w:left="720" w:hanging="360"/></w:pPr>
    </w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
  <w:abstractNum w:abstractNumId="1">
    <w:multiLevelType w:val="singleLevel"/>
    <w:lvl w:ilvl="0">
      <w:start w:val="1"/>
      <w:numFmt w:val="bullet"/>
      <w:lvlText w:val="•"/>
      <w:lvlJc w:val="left"/>
      <w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:ind w:left="720" w:hanging="360"/></w:pPr>
      <w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>
    </w:lvl>
  </w:abstractNum>
  <w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>
"""


def core_props() -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>脉搏测试仪项目补充汇报</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>
"""


APP_PROPS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": RELS,
        "word/_rels/document.xml.rels": DOC_RELS,
        "word/document.xml": build_document_xml(),
        "word/styles.xml": STYLES,
        "word/numbering.xml": NUMBERING,
        "docProps/core.xml": core_props(),
        "docProps/app.xml": APP_PROPS,
    }
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data.encode("utf-8"))
    print(OUT)


if __name__ == "__main__":
    main()
