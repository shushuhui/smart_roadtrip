"""Streamlit 对话界面原型：需求输入 → 人在回路确认 → 路书展示。

直接调用编排层（进程内 MemorySaver），不经过 FastAPI，便于本地演示。
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import streamlit as st

from smart_roadtrip.orchestrator.export import (
    ExportError,
    render_gpx,
    render_html,
    render_pdf,
)
from smart_roadtrip.orchestrator.journal import generate_travelogue
from smart_roadtrip.orchestrator.main_agent import (
    build_graph,
    resume_planning,
    start_planning,
)
from smart_roadtrip.orchestrator.request_parser import (
    RequestParseError,
    is_llm_configured,
    parse_request_text,
)
from smart_roadtrip.schemas.trip import (
    EnergyType,
    PendingConfirmation,
    TravelPreferences,
    Traveler,
    TripRequest,
    TripStatus,
    TripVersion,
    VehicleInfo,
)

_ENERGY_OPTIONS = {
    "纯电": EnergyType.EV,
    "插电混动": EnergyType.PHEV,
    "燃油": EnergyType.ICE,
}

_STATUS_LABELS = {
    TripStatus.PLANNING: "规划中",
    TripStatus.AWAITING_CONFIRMATION: "等待确认",
    TripStatus.REVISING: "调整中",
    TripStatus.APPROVED: "已批准",
    TripStatus.COMPLETED: "已完成",
    TripStatus.ROLLED_BACK: "已回退",
    TripStatus.FAILED: "失败",
}


def _split(text: str) -> list[str]:
    return [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]


def _as_pending(value) -> PendingConfirmation | None:
    if value is None:
        return None
    return value if isinstance(value, PendingConfirmation) else PendingConfirmation.model_validate(value)


def _as_version(value) -> TripVersion:
    return value if isinstance(value, TripVersion) else TripVersion.model_validate(value)


def _reset_session() -> None:
    st.session_state.state = None
    st.session_state.thread_id = None
    st.session_state.graph = build_graph()
    st.session_state.journal = None
    st.session_state.journal_source = None


def _apply_decision(decision: dict) -> None:
    try:
        new_state = resume_planning(
            decision,
            st.session_state.thread_id,
            graph=st.session_state.graph,
        )
    except Exception as exc:  # noqa: BLE001 - UI 边界统一提示
        st.error(f"确认失败：{exc}")
        return
    st.session_state.state = new_state
    st.rerun()


def _start_trip(request: TripRequest) -> None:
    thread_id = f"ui-{uuid4().hex}"
    # 新一轮规划使用全新的图，释放上一轮的进程内状态。
    st.session_state.graph = build_graph()
    try:
        state = start_planning(request, thread_id, graph=st.session_state.graph)
    except Exception as exc:  # noqa: BLE001 - UI 边界统一提示
        st.error(f"规划失败：{exc}")
        return
    st.session_state.thread_id = thread_id
    st.session_state.state = state
    st.session_state.journal = None
    st.session_state.journal_source = None
    st.rerun()


st.set_page_config(
    page_title="基于多智能体协作的旅行规划系统",
    page_icon="🚗",
    layout="wide",
)

if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "state" not in st.session_state:
    st.session_state.state = None
if "journal" not in st.session_state:
    st.session_state.journal = None
if "journal_source" not in st.session_state:
    st.session_state.journal_source = None

st.title("🚗 基于多智能体协作的旅行规划系统")
st.caption("多 Agent 自驾行程规划原型 · 未配置外部 API 时自动使用占位数据")

with st.sidebar:
    st.header("行程需求")
    nl_tab, form_tab = st.tabs(["自然语言", "表单"])

    with nl_tab:
        st.caption(
            "已启用 LLM 解析" if is_llm_configured() else "未配置 LLM，使用规则解析"
        )
        nl_text = st.text_area(
            "用一句话描述行程",
            value="从成都到稻城亚丁玩4天，两个人，开电车，喜欢雪山和摄影，预算8000元",
            height=120,
        )
        if st.button("解析并生成行程", use_container_width=True):
            with st.spinner("正在解析需求..."):
                try:
                    request = parse_request_text(nl_text)
                except RequestParseError as exc:
                    st.error(str(exc))
                else:
                    _start_trip(request)

    with form_tab:
        with st.form("trip_form"):
            origin = st.text_input("出发地", value="成都")
            destination = st.text_input("目的地", value="稻城亚丁")
            days = st.number_input("行程天数", min_value=1, max_value=30, value=4, step=1)
            start_date = st.date_input("出发日期", value=date.today() + timedelta(days=7))
            travelers_text = st.text_input("同行人（逗号分隔）", value="小张,小李")
            energy_label = st.selectbox("车型", options=list(_ENERGY_OPTIONS), index=0)
            interests_text = st.text_input("兴趣偏好（逗号分隔）", value="雪山,摄影")
            budget = st.number_input(
                "总预算（元，0 表示未填写）",
                min_value=0,
                max_value=1_000_000,
                value=8000,
                step=500,
            )
            submitted = st.form_submit_button("生成行程", use_container_width=True)

        if submitted:
            names = _split(travelers_text) or ["同行人"]
            request = TripRequest(
                origin=origin.strip() or "出发地",
                destination=destination.strip() or "目的地",
                days=int(days),
                start_date=start_date,
                travelers=[
                    Traveler(name=name, is_driver=(index == 0), preferences=_split(interests_text))
                    for index, name in enumerate(names)
                ],
                vehicle=VehicleInfo(energy_type=_ENERGY_OPTIONS[energy_label]),
                preferences=TravelPreferences(interests=_split(interests_text)),
                budget_cny=float(budget) if budget else None,
            )
            _start_trip(request)

state = st.session_state.state
if not state:
    st.info("在左侧选择「自然语言」或「表单」并生成行程。")
    st.stop()

status = TripStatus(state.get("status", TripStatus.PLANNING))
pending = _as_pending(state.get("pending_confirmation"))

left, right = st.columns([2, 1])
with left:
    st.caption(
        f"线程：`{st.session_state.thread_id}` · "
        f"状态：{_STATUS_LABELS.get(status, status.value)} · "
        f"轮次：{state.get('round', 0)}"
    )
with right:
    if st.button("重新开始", use_container_width=True):
        _reset_session()
        st.rerun()

if status == TripStatus.AWAITING_CONFIRMATION and pending is not None:
    st.subheader("待确认行程")
    st.markdown(pending.summary_markdown)

    acknowledged: list[str] = []
    all_required_checked = True
    if pending.safety_alerts:
        st.markdown("#### 安全提示确认")
        for alert in pending.safety_alerts:
            checked = st.checkbox(
                f"[{alert.severity.value}] {alert.message}",
                key=f"ack_{pending.confirmation_id}_{alert.alert_id}",
                help=alert.recommendation,
            )
            if checked:
                acknowledged.append(alert.alert_id)
            elif alert.requires_user_confirmation:
                all_required_checked = False

    feedback = st.text_input(
        "退回原因（点击「退回调整」时填写）",
        key=f"feedback_{pending.confirmation_id}",
    )

    approve_col, reject_col = st.columns(2)
    with approve_col:
        if st.button(
            "确认并生成路书",
            type="primary",
            use_container_width=True,
            disabled=not all_required_checked,
        ):
            _apply_decision(
                {"decision": "approve", "acknowledged_alert_ids": acknowledged}
            )
    with reject_col:
        if st.button("退回调整", use_container_width=True):
            _apply_decision({"decision": "reject", "feedback": feedback})

    if not all_required_checked:
        st.caption("请先逐项勾选需要确认的安全提示，才能批准行程。")

elif status == TripStatus.COMPLETED:
    st.success("行程已确认，路书已生成。")
    markdown = state.get("report_markdown") or ""
    st.markdown(markdown)

    journal_markdown = st.session_state.get("journal")
    md_col, html_col = st.columns(2)
    with md_col:
        st.download_button(
            "下载 Markdown 路书",
            data=markdown.encode("utf-8"),
            file_name=f"roadtrip-{st.session_state.thread_id}.md",
            mime="text/markdown",
            key="download-roadbook",
        )
    with html_col:
        try:
            html_content = render_html(state, journal_markdown)
        except ExportError as exc:
            st.caption(f"HTML 导出不可用：{exc}")
        else:
            st.download_button(
                "下载 HTML 路书",
                data=html_content.encode("utf-8"),
                file_name=f"roadtrip-{st.session_state.thread_id}.html",
                mime="text/html",
                key="download-html",
            )

    pdf_col, gpx_col = st.columns(2)
    with pdf_col:
        try:
            pdf_content = render_pdf(state, journal_markdown)
        except ExportError as exc:
            st.caption(f"PDF 导出不可用：{exc}")
        else:
            st.download_button(
                "下载 PDF 路书",
                data=pdf_content,
                file_name=f"roadtrip-{st.session_state.thread_id}.pdf",
                mime="application/pdf",
                key="download-pdf",
            )
    with gpx_col:
        try:
            gpx_content = render_gpx(state)
        except ExportError:
            st.caption(
                "GPX 导出不可用：当前路线没有坐标（配置高德地图后重新规划）。"
            )
        else:
            st.download_button(
                "下载 GPX 轨迹",
                data=gpx_content.encode("utf-8"),
                file_name=f"roadtrip-{st.session_state.thread_id}.gpx",
                mime="application/gpx+xml",
                key="download-gpx",
            )

    st.divider()
    st.subheader("游记草稿")
    if st.button("生成游记草稿", key="generate-journal"):
        with st.spinner("正在生成游记..."):
            try:
                journal_markdown, generated_by = generate_travelogue(
                    st.session_state.state
                )
            except Exception as exc:  # noqa: BLE001 - UI 边界统一提示
                st.error(f"游记生成失败：{exc}")
            else:
                st.session_state.journal = journal_markdown
                st.session_state.journal_source = generated_by
                st.rerun()

    journal = st.session_state.get("journal")
    if journal:
        source_label = (
            "LLM 生成"
            if st.session_state.get("journal_source") == "llm"
            else "模板生成"
        )
        st.caption(f"游记来源：{source_label}")
        st.markdown(journal)
        st.download_button(
            "下载游记草稿",
            data=journal.encode("utf-8"),
            file_name=f"journal-{st.session_state.thread_id}.md",
            mime="text/markdown",
            key="download-journal",
        )
    else:
        st.caption("可基于已确认行程生成一份中文游记草稿（不包含真实照片与个人经历）。")

elif status == TripStatus.FAILED:
    st.error("本次规划失败。")
    for error in state.get("errors") or []:
        st.write(f"- {error}")

else:
    st.info(f"当前状态：{_STATUS_LABELS.get(status, status.value)}")

with st.expander("版本与诊断信息"):
    versions = [_as_version(item) for item in state.get("versions") or []]
    if versions:
        st.table(
            [
                {
                    "版本": f"v{version.number}",
                    "原因": version.reason,
                    "总里程(km)": version.itinerary.total_distance_km,
                    "创建时间": version.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
                for version in versions
            ]
        )
    else:
        st.write("暂无版本快照。")

    current = state.get("current_version_id")
    confirmed = state.get("confirmed_version_id")
    st.write(f"当前候选版本：`{current or '无'}`")
    st.write(f"最后确认版本：`{confirmed or '无'}`")

    errors = state.get("errors") or []
    if errors:
        st.write("错误/降级记录：")
        for error in errors:
            st.write(f"- {error}")
    else:
        st.write("暂无错误记录。")
