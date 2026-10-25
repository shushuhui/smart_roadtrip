"""行程导出：Markdown、独立 HTML 与 GPX。

HTML 使用 markdown-it 渲染，并禁用原始 HTML，避免把 LLM/用户文本当作脚本执行。
GPX 优先使用高德路线返回的折线；没有折线时退化为充电站/医疗维修等航点。
"""

from __future__ import annotations

import html
import io
import re
import xml.etree.ElementTree as ET
from typing import Any

from markdown_it import MarkdownIt

from smart_roadtrip.schemas.trip import AgentName, GeoPoint, Itinerary

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"

_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")

# ReportLab 内置 CID 字体对部分符号覆盖有限，做保守替换。
_PDF_REPLACEMENTS = {
    "→": "至",
    "–": "-",
    "—": "-",
    "℃": "摄氏度",
    "•": "·",
}
_EMOJI_PATTERN = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")
_PDF_FONT_REGISTERED = False

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{
    margin: 0; padding: 32px 20px 48px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC",
      "Microsoft YaHei", sans-serif;
    color: #1f2529; background: #fff; line-height: 1.75;
  }}
  article {{ max-width: 920px; margin: 0 auto; }}
  h1, h2, h3 {{ color: #17405f; line-height: 1.3; }}
  h1 {{ border-bottom: 2px solid #d8e4ee; padding-bottom: 10px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 1em 0; }}
  th, td {{ border: 1px solid #d8dee4; padding: 7px 9px; text-align: left; }}
  th {{ background: #f3f7fa; }}
  blockquote {{
    margin: 1em 0; padding: 8px 16px; color: #4b5b66;
    border-left: 4px solid #9fc2da; background: #f7fafc;
  }}
  a {{ color: #125c8c; }}
  hr {{ border: 0; border-top: 1px solid #d8dee4; margin: 2em 0; }}
  footer {{
    max-width: 920px; margin: 32px auto 0; color: #7a8a94;
    font-size: 12px; text-align: center;
  }}
  @media print {{
    body {{ padding: 0; }}
    a {{ color: inherit; text-decoration: none; }}
    footer {{ margin-top: 24px; }}
  }}
</style>
</head>
<body>
<article>
{content}
</article>
<footer>由基于多智能体协作的旅行规划系统（Smart Roadtrip）生成</footer>
</body>
</html>
"""


class ExportError(ValueError):
    """当前行程不足以生成指定格式的导出文件。"""


def render_markdown_export(
    state: dict[str, Any],
    journal_markdown: str | None = None,
) -> str:
    roadbook = _require_roadbook(state)
    if journal_markdown:
        return f"{roadbook.rstrip()}\n\n---\n\n{journal_markdown.strip()}\n"
    return roadbook


def render_html(
    state: dict[str, Any],
    journal_markdown: str | None = None,
) -> str:
    roadbook = _require_roadbook(state)
    content = _MARKDOWN.render(roadbook)
    if journal_markdown:
        content += (
            '<section class="journal">\n'
            f"{_MARKDOWN.render(journal_markdown)}\n"
            "</section>\n"
        )
    return _HTML_TEMPLATE.format(
        title=html.escape(_title(state)),
        content=content,
    )


def render_pdf(
    state: dict[str, Any],
    journal_markdown: str | None = None,
) -> bytes:
    """生成可打印的中文 PDF 路书（ReportLab 内置 STSong-Light 字体）。"""

    roadbook = _require_roadbook(state)

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import (
            HRFlowable,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise ExportError("未安装 reportlab，无法生成 PDF。") from exc

    global _PDF_FONT_REGISTERED
    if not _PDF_FONT_REGISTERED:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _PDF_FONT_REGISTERED = True

    font = "STSong-Light"
    base_color = colors.HexColor("#1f2529")
    heading_color = colors.HexColor("#17405f")
    accent_color = colors.HexColor("#245a7d")
    muted_color = colors.HexColor("#4b5b66")
    line_color = colors.HexColor("#d8dee4")
    soft_bg = colors.HexColor("#f3f7fa")

    body_style = ParagraphStyle(
        "body",
        fontName=font,
        fontSize=10.5,
        leading=17,
        textColor=base_color,
        spaceAfter=4,
    )
    h1_style = ParagraphStyle(
        "h1",
        fontName=font,
        fontSize=20,
        leading=26,
        textColor=heading_color,
        spaceBefore=4,
        spaceAfter=10,
    )
    h2_style = ParagraphStyle(
        "h2",
        fontName=font,
        fontSize=15,
        leading=21,
        textColor=heading_color,
        spaceBefore=12,
        spaceAfter=6,
    )
    h3_style = ParagraphStyle(
        "h3",
        fontName=font,
        fontSize=12.5,
        leading=18,
        textColor=accent_color,
        spaceBefore=8,
        spaceAfter=4,
    )
    quote_style = ParagraphStyle(
        "quote",
        fontName=font,
        fontSize=10,
        leading=16,
        textColor=muted_color,
        leftIndent=10,
        rightIndent=6,
        backColor=colors.HexColor("#f7fafc"),
        borderColor=line_color,
        borderWidth=0.5,
        borderPadding=6,
        spaceBefore=4,
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        "bullet",
        fontName=font,
        fontSize=10.5,
        leading=17,
        textColor=base_color,
        leftIndent=16,
        bulletIndent=4,
        spaceAfter=2,
    )
    cell_style = ParagraphStyle(
        "cell",
        fontName=font,
        fontSize=8.8,
        leading=12.5,
        textColor=base_color,
    )
    head_cell_style = ParagraphStyle(
        "headcell",
        fontName=font,
        fontSize=8.8,
        leading=12.5,
        textColor=heading_color,
    )

    def pdf_text(value: str) -> str:
        for source, target in _PDF_REPLACEMENTS.items():
            value = value.replace(source, target)
        return _EMOJI_PATTERN.sub("", value)

    def inline(value: str) -> str:
        value = html.escape(pdf_text(value), quote=False)
        value = re.sub(
            r"\*\*(.+?)\*\*", r'<font color="#17405f">\1</font>', value
        )
        value = re.sub(r"`(.+?)`", r'<font color="#7a3e00">\1</font>', value)
        value = re.sub(
            r"\[([^\]]+)\]\(([^)]+)\)", r'<link href="\2">\1</link>', value
        )
        value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
        return value

    def split_table_row(line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    def is_table_separator(cells: list[str]) -> bool:
        return bool(cells) and all(
            re.fullmatch(r":?-{3,}:?", cell.replace(" ", ""))
            for cell in cells
        )

    def make_table(rows: list[list[str]], width: float) -> Table:
        column_count = max(len(row) for row in rows)
        normalized = [
            row + [""] * (column_count - len(row)) for row in rows
        ]
        has_header = len(normalized) > 1
        data = []
        for row_index, row in enumerate(normalized):
            style = (
                head_cell_style
                if has_header and row_index == 0
                else cell_style
            )
            data.append(
                [Paragraph(inline(cell) or " ", style) for cell in row]
            )

        table = Table(
            data,
            colWidths=[width / column_count] * column_count,
            repeatRows=1 if has_header else 0,
            hAlign="LEFT",
        )
        commands = [
            ("GRID", (0, 0), (-1, -1), 0.4, line_color),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        if has_header:
            commands.append(("BACKGROUND", (0, 0), (-1, 0), soft_bg))
        table.setStyle(TableStyle(commands))
        return table

    def flowables(markdown: str, width: float) -> list[Any]:
        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        story: list[Any] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()

            if not stripped:
                story.append(Spacer(1, 4))
                index += 1
                continue

            if stripped.startswith("# "):
                story.append(Paragraph(inline(stripped[2:].strip()), h1_style))
                index += 1
                continue
            if stripped.startswith("## "):
                story.append(Paragraph(inline(stripped[3:].strip()), h2_style))
                index += 1
                continue
            if stripped.startswith("### "):
                story.append(Paragraph(inline(stripped[4:].strip()), h3_style))
                index += 1
                continue

            if stripped.startswith("|") and index + 1 < len(lines):
                next_cells = split_table_row(lines[index + 1])
                if is_table_separator(next_cells):
                    table_lines: list[str] = []
                    while (
                        index < len(lines)
                        and lines[index].strip().startswith("|")
                    ):
                        table_lines.append(lines[index])
                        index += 1
                    rows = [
                        split_table_row(row) for row in table_lines
                    ]
                    rows = [
                        row for row in rows if not is_table_separator(row)
                    ]
                    if rows:
                        story.append(make_table(rows, width))
                        story.append(Spacer(1, 6))
                    continue

            if stripped.startswith(">"):
                quote_lines: list[str] = []
                while (
                    index < len(lines)
                    and lines[index].strip().startswith(">")
                ):
                    quote_lines.append(
                        lines[index].strip().lstrip(">").strip()
                    )
                    index += 1
                story.append(
                    Paragraph(
                        "<br/>".join(
                            inline(value)
                            for value in quote_lines
                            if value
                        ),
                        quote_style,
                    )
                )
                story.append(Spacer(1, 4))
                continue

            if stripped in ("---", "***", "___"):
                story.append(
                    HRFlowable(
                        width="100%",
                        thickness=0.6,
                        color=line_color,
                        spaceBefore=6,
                        spaceAfter=6,
                    )
                )
                index += 1
                continue

            bullet_match = re.match(r"^[-*]\s+(.*)$", stripped)
            number_match = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
            if bullet_match or number_match:
                if bullet_match:
                    content = bullet_match.group(1)
                    bullet = "•"
                else:
                    content = number_match.group(2)
                    bullet = f"{number_match.group(1)}."
                story.append(
                    Paragraph(
                        inline(content),
                        bullet_style,
                        bulletText=bullet,
                    )
                )
                index += 1
                continue

            paragraph_lines = [stripped]
            index += 1
            while index < len(lines):
                next_line = lines[index].strip()
                if (
                    not next_line
                    or next_line.startswith("#")
                    or next_line.startswith("|")
                    or next_line.startswith(">")
                    or next_line in ("---", "***", "___")
                    or re.match(r"^[-*]\s+", next_line)
                    or re.match(r"^(\d+)[.)]\s+", next_line)
                ):
                    break
                paragraph_lines.append(next_line)
                index += 1
            story.append(
                Paragraph(
                    inline(" ".join(paragraph_lines)),
                    body_style,
                )
            )

        return story

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#7a8a94"))
        canvas.drawString(
            18 * mm, 10 * mm, "基于多智能体协作的旅行规划系统（Smart Roadtrip）"
        )
        canvas.drawRightString(
            A4[0] - 18 * mm, 10 * mm, f"第 {document.page} 页"
        )
        canvas.restoreState()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=pdf_text(_title(state)),
        author="基于多智能体协作的旅行规划系统（Smart Roadtrip）",
        subject="自驾路书",
    )
    story = flowables(roadbook, document.width)
    if journal_markdown:
        story.append(PageBreak())
        story.append(Paragraph("游记草稿", h1_style))
        story.extend(flowables(journal_markdown, document.width))
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def render_gpx(state: dict[str, Any]) -> str:
    route_option = _first_route_option(state)
    polyline = _polyline(route_option)
    waypoints = _waypoints(state)

    if not polyline and not waypoints:
        raise ExportError(
            "当前行程没有可用的路线坐标；请配置高德地图并重新规划后再导出 GPX。"
        )

    root = ET.Element(
        "gpx",
        {
            "version": "1.1",
            "creator": "基于多智能体协作的旅行规划系统（Smart Roadtrip）",
            "xmlns": GPX_NAMESPACE,
        },
    )
    metadata = ET.SubElement(root, "metadata")
    ET.SubElement(metadata, "name").text = _title(state)

    for category, name, point, description in waypoints:
        wpt = ET.SubElement(
            root, "wpt", {"lat": str(point.lat), "lon": str(point.lng)}
        )
        ET.SubElement(wpt, "name").text = f"{category}：{name}"
        if description:
            ET.SubElement(wpt, "desc").text = description

    if polyline:
        track = ET.SubElement(root, "trk")
        ET.SubElement(track, "name").text = _title(state)
        segment = ET.SubElement(track, "trkseg")
        for point in polyline:
            ET.SubElement(
                segment, "trkpt", {"lat": str(point.lat), "lon": str(point.lng)}
            )

    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


def _require_roadbook(state: dict[str, Any]) -> str:
    roadbook = state.get("report_markdown")
    if not roadbook:
        raise ExportError("路书尚未生成，请先确认行程。")
    return str(roadbook)


def _title(state: dict[str, Any]) -> str:
    itinerary = state.get("itinerary")
    if isinstance(itinerary, Itinerary):
        return itinerary.title
    if isinstance(itinerary, dict):
        title = itinerary.get("title")
        if title:
            return str(title)
    return "自驾路书"


def _first_route_option(state: dict[str, Any]) -> Any:
    results = state.get("agent_results") or {}
    route_result = results.get(AgentName.ROUTE.value)
    options = _get(route_result, "route_options", []) or []
    return options[0] if options else None


def _polyline(route_option: Any) -> list[GeoPoint]:
    points = _get(route_option, "polyline", []) or []
    return [point for point in (_point(item) for item in points) if point]


def _waypoints(
    state: dict[str, Any],
) -> list[tuple[str, str, GeoPoint, str]]:
    waypoints: list[tuple[str, str, GeoPoint, str]] = []
    results = state.get("agent_results") or {}

    energy = results.get(AgentName.ENERGY.value)
    for stop in _get(energy, "charging_stops", []) or []:
        point = _point(_get(stop, "location"))
        if point:
            waypoints.append(
                (
                    "充电站",
                    str(_get(stop, "name", "充电站")),
                    point,
                    str(_get(stop, "notes", "") or ""),
                )
            )

    safety = results.get(AgentName.SAFETY.value)
    for resource in _get(safety, "rescue_resources", []) or []:
        point = _point(_get(resource, "location"))
        if point:
            address = str(_get(resource, "address", "") or "")
            phone = str(_get(resource, "phone", "") or "")
            description = "；".join(part for part in (address, phone) if part)
            waypoints.append(
                (
                    str(_get(resource, "category", "救援资源")),
                    str(_get(resource, "name", "救援资源")),
                    point,
                    description,
                )
            )

    return waypoints


def _point(value: Any) -> GeoPoint | None:
    if isinstance(value, GeoPoint):
        return value
    if isinstance(value, dict):
        try:
            return GeoPoint.model_validate(value)
        except Exception:  # noqa: BLE001 - 坐标不合法时跳过
            return None
    return None


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
