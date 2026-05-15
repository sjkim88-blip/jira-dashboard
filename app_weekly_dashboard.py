"""
Jira Cloud 주간 업무 현황 대시보드
- Jira API 토큰 기반
- Due date가 아니라 created / updated / status / labels 기준으로 주간회의용 현황 구성
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# 1. 환경설정
# -----------------------------------------------------------------------------
load_dotenv()

JIRA_SERVER = (os.getenv("JIRA_SERVER") or "").rstrip("/")
JIRA_EMAIL = (os.getenv("JIRA_EMAIL") or "").strip()
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN") or ""
JIRA_PROJECT_KEY = (os.getenv("JIRA_PROJECT_KEY") or "").strip()

# 회사 로고 경로: 프로젝트 실행 폴더 기준 assets/logo.png 권장
LOGO_CANDIDATES = [
    Path("assets/logo.png"),
    Path("logo.png"),
    Path("/mnt/data/image(65).png"),
]

_DONE_NAMES = frozenset({"완료", "Done", "DONE", "Closed", "closed", "Resolved", "resolved"})
_HOLD_NAMES = frozenset({"홀딩", "Hold", "HOLD", "On Hold", "on hold", "보류"})
_WEEKLY_MEETING_LABEL = "weekly-meeting"


# -----------------------------------------------------------------------------
# 2. 공통 유틸
# -----------------------------------------------------------------------------
def require_config() -> bool:
    missing = []
    if not JIRA_SERVER:
        missing.append("JIRA_SERVER")
    if not JIRA_EMAIL:
        missing.append("JIRA_EMAIL")
    if not JIRA_API_TOKEN:
        missing.append("JIRA_API_TOKEN")
    if not JIRA_PROJECT_KEY:
        missing.append("JIRA_PROJECT_KEY")

    if missing:
        st.error(f".env 파일에 다음 정보가 없습니다: {', '.join(missing)}")
        return False
    return True


def h(value: Any) -> str:
    """HTML escape."""
    return html.escape(str(value or ""))


def parse_jira_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    # Jira 예: 2026-05-13T09:30:00.000+0900
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None


def parse_date(value: str | None) -> date | None:
    if not value or value == "—":
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def fmt_date(value: date | datetime | str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value[:10] if value else "—"


def fmt_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def get_week_range(base: date | None = None) -> tuple[date, date]:
    """이번 주 월요일~금요일 반환."""
    base = base or date.today()
    monday = base - timedelta(days=base.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def is_done_status(status: str) -> bool:
    return status in _DONE_NAMES


def is_hold_status(status: str) -> bool:
    return status in _HOLD_NAMES


def status_color_class(status: str, stale_days: int = 0) -> str:
    if stale_days >= 14:
        return "red"
    if stale_days >= 7:
        return "orange"
    if is_done_status(status):
        return "purple"
    if is_hold_status(status):
        return "gray"
    return "blue"


def find_logo_path() -> Path | None:
    for path in LOGO_CANDIDATES:
        if path.exists():
            return path
    return None


# -----------------------------------------------------------------------------
# 3. Jira API
# -----------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=True)
def fetch_all_issues_raw(server: str, email: str, token: str, project_key: str, jql_extra: str) -> list[dict[str, Any]]:
    jql = f'project = "{project_key}"'
    if jql_extra.strip():
        jql = f"({jql}) AND ({jql_extra.strip()})"
    jql += " ORDER BY updated DESC"

    auth = (email, token)
    params = {
        "jql": jql,
        "maxResults": 100,
        "fields": [
            "summary",
            "status",
            "assignee",
            "updated",
            "created",
            "priority",
            "duedate",
            "labels",
        ],
    }

    # 기존 코드에서 사용하던 최신 Jira 검색 API 경로 유지
    url = f"{server}/rest/api/3/search/jql"
    r = requests.get(url, auth=auth, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("issues", [])


def flatten_issue(server: str, raw: dict[str, Any]) -> dict[str, Any]:
    fields = raw.get("fields") or {}
    status = (fields.get("status") or {}).get("name") or ""
    assignee = fields.get("assignee")
    created_dt = parse_jira_datetime(fields.get("created"))
    updated_dt = parse_jira_datetime(fields.get("updated"))
    labels = fields.get("labels") or []

    return {
        "키": raw.get("key") or "",
        "요약": fields.get("summary") or "",
        "상태": status,
        "담당자": (assignee or {}).get("displayName") if assignee else "미지정",
        "작성일": fmt_date(created_dt),
        "작성일_dt": created_dt,
        "작성일_date": created_dt.date() if created_dt else None,
        "마지막변경일": fmt_date(updated_dt),
        "마지막변경일_dt": updated_dt,
        "마지막변경일_date": updated_dt.date() if updated_dt else None,
        "만기일": fmt_date(fields.get("duedate")),
        "라벨": labels,
        "라벨문자열": ", ".join(labels),
        "링크": f"{server}/browse/{raw.get('key')}",
    }


# -----------------------------------------------------------------------------
# 4. 데이터 계산
# -----------------------------------------------------------------------------
@dataclass
class DashboardData:
    rows: list[dict[str, Any]]
    week_start: date
    week_end: date
    new_this_week: list[dict[str, Any]]
    changed_this_week: list[dict[str, Any]]
    stale_7: list[dict[str, Any]]
    stale_14: list[dict[str, Any]]
    meeting_unfinished: list[dict[str, Any]]
    by_day: dict[date, list[dict[str, Any]]]


def days_since_updated(row: dict[str, Any], today: date | None = None) -> int:
    today = today or date.today()
    updated = row.get("마지막변경일_date")
    if not updated:
        return 0
    return max((today - updated).days, 0)


def calculate_dashboard_data(rows: list[dict[str, Any]], week_start: date, week_end: date) -> DashboardData:
    today = date.today()

    def in_this_week(d: date | None) -> bool:
        return d is not None and week_start <= d <= week_end

    new_this_week = [r for r in rows if in_this_week(r.get("작성일_date"))]
    changed_this_week = [r for r in rows if in_this_week(r.get("마지막변경일_date"))]

    not_done = [r for r in rows if not is_done_status(r.get("상태", ""))]
    stale_7_all = [r for r in not_done if days_since_updated(r, today) >= 7]
    stale_14 = [r for r in not_done if days_since_updated(r, today) >= 14]
    stale_7 = [r for r in stale_7_all if days_since_updated(r, today) < 14]

    meeting_unfinished = [
        r for r in not_done
        if _WEEKLY_MEETING_LABEL in (r.get("라벨") or [])
    ]

    by_day: dict[date, list[dict[str, Any]]] = {}
    for offset in range(5):
        current = week_start + timedelta(days=offset)
        day_items: list[dict[str, Any]] = []
        seen_keys = set()
        for row in rows:
            if row.get("작성일_date") == current or row.get("마지막변경일_date") == current:
                key = row.get("키")
                if key not in seen_keys:
                    day_items.append(row)
                    seen_keys.add(key)
        by_day[current] = day_items

    return DashboardData(
        rows=rows,
        week_start=week_start,
        week_end=week_end,
        new_this_week=new_this_week,
        changed_this_week=changed_this_week,
        stale_7=stale_7,
        stale_14=stale_14,
        meeting_unfinished=meeting_unfinished,
        by_day=by_day,
    )


# -----------------------------------------------------------------------------
# 5. CSS
# -----------------------------------------------------------------------------
def inject_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"] {
            font-family: 'Pretendard', 'Noto Sans KR', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
        }
        .stApp {
            background: #F8FAFC;
        }
        .block-container {
            padding-top: 1.3rem;
            padding-bottom: 3rem;
            max-width: 1640px;
        }
        div[data-testid="stTextInput"] label,
        div[data-testid="stSelectbox"] label,
        div[data-testid="stDateInput"] label {
            font-weight: 700;
            color: #334155;
        }
        .top-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 18px;
        }
        .title-left {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .logo-fallback {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            background: linear-gradient(135deg, #2563EB, #1D4ED8);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 900;
            font-size: 22px;
        }
        .page-title {
            font-size: 30px;
            font-weight: 850;
            color: #0F172A;
            letter-spacing: -0.04em;
            margin: 0;
            line-height: 1.15;
        }
        .api-badge {
            font-size: 13px;
            color: #64748B;
            background: #F1F5F9;
            border: 1px solid #E2E8F0;
            border-radius: 999px;
            padding: 6px 10px;
        }
        .last-updated {
            font-size: 13px;
            color: #64748B;
            white-space: nowrap;
        }
        .section-card {
            background: white;
            border: 1px solid #E5E7EB;
            border-radius: 18px;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
            padding: 18px;
            margin-bottom: 18px;
        }
        .section-title {
            font-size: 18px;
            font-weight: 800;
            color: #0F172A;
            margin-bottom: 14px;
            letter-spacing: -0.02em;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 16px;
            margin: 18px 0;
        }
        .kpi-card {
            background: white;
            border: 1px solid #E5E7EB;
            border-radius: 18px;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
            padding: 20px;
            min-height: 124px;
        }
        .kpi-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        .kpi-icon {
            width: 42px;
            height: 42px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 14px;
            font-size: 22px;
        }
        .kpi-label {
            font-size: 15px;
            font-weight: 800;
        }
        .kpi-number {
            font-size: 32px;
            line-height: 1.1;
            color: #0F172A;
            font-weight: 850;
            letter-spacing: -0.04em;
            margin: 6px 0;
        }
        .kpi-desc {
            font-size: 13px;
            color: #64748B;
        }
        .blue { color: #2563EB; } .blue-bg { background: #DBEAFE; }
        .green { color: #059669; } .green-bg { background: #D1FAE5; }
        .orange { color: #EA580C; } .orange-bg { background: #FFEDD5; }
        .red { color: #DC2626; } .red-bg { background: #FEE2E2; }
        .purple { color: #7C3AED; } .purple-bg { background: #EDE9FE; }
        .gray { color: #64748B; } .gray-bg { background: #F1F5F9; }

        .board-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(190px, 1fr));
            gap: 10px;
            overflow-x: auto;
            padding-bottom: 4px;
        }
        .day-column {
            border: 1px solid #E5E7EB;
            border-radius: 14px;
            background: #F8FAFC;
            padding: 12px;
            min-height: 380px;
        }
        .day-head {
            text-align: center;
            margin-bottom: 12px;
        }
        .day-name {
            font-size: 15px;
            font-weight: 850;
            color: #0F172A;
        }
        .day-date {
            font-size: 12px;
            color: #64748B;
            margin-left: 4px;
        }
        .day-counts {
            display: flex;
            justify-content: center;
            gap: 10px;
            font-size: 12px;
            margin-top: 6px;
        }
        .ticket-card {
            background: white;
            border: 1px solid #E5E7EB;
            border-radius: 13px;
            padding: 12px;
            margin-bottom: 10px;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
            border-left: 4px solid #2563EB;
        }
        .ticket-card.red-line { border-left-color: #DC2626; }
        .ticket-card.orange-line { border-left-color: #EA580C; }
        .ticket-card.green-line { border-left-color: #059669; }
        .ticket-card.purple-line { border-left-color: #7C3AED; }
        .ticket-card.gray-line { border-left-color: #94A3B8; }
        .ticket-key {
            font-weight: 850;
            color: #0F172A;
            font-size: 13px;
            margin-bottom: 5px;
        }
        .ticket-summary {
            color: #1E293B;
            font-size: 13px;
            line-height: 1.45;
            min-height: 34px;
            margin-bottom: 10px;
        }
        .ticket-foot {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            font-size: 12px;
            color: #475569;
        }
        .badge {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 8px;
            border: 1px solid transparent;
            white-space: nowrap;
        }
        .badge.blue { background: #EFF6FF; border-color: #BFDBFE; color: #2563EB; }
        .badge.green { background: #ECFDF5; border-color: #A7F3D0; color: #059669; }
        .badge.orange { background: #FFF7ED; border-color: #FED7AA; color: #EA580C; }
        .badge.red { background: #FEF2F2; border-color: #FECACA; color: #DC2626; }
        .badge.purple { background: #F5F3FF; border-color: #DDD6FE; color: #7C3AED; }
        .badge.gray { background: #F8FAFC; border-color: #E2E8F0; color: #64748B; }
        .delay-box {
            border-radius: 14px;
            border: 1px solid #E5E7EB;
            margin-bottom: 14px;
            overflow: hidden;
        }
        .delay-head {
            padding: 12px 14px;
            font-weight: 850;
            font-size: 14px;
            border-bottom: 1px solid #E5E7EB;
        }
        .delay-item {
            padding: 12px 14px;
            border-bottom: 1px solid #E5E7EB;
            background: white;
        }
        .delay-item:last-child { border-bottom: none; }
        .delay-meta {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            color: #64748B;
            font-size: 12px;
            margin-top: 7px;
        }
        .empty-state {
            color: #94A3B8;
            font-size: 13px;
            text-align: center;
            padding: 24px 10px;
            border: 1px dashed #CBD5E1;
            border-radius: 14px;
            background: #F8FAFC;
        }
        .small-link a {
            color: #2563EB;
            text-decoration: none;
            font-weight: 700;
        }
        @media (max-width: 1200px) {
            .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (max-width: 720px) {
            .kpi-grid { grid-template-columns: 1fr; }
            .top-header { align-items: flex-start; flex-direction: column; }
            .page-title { font-size: 24px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------------------------------------------------------
# 6. 렌더링 함수
# -----------------------------------------------------------------------------
def render_header(last_updated: datetime) -> None:
    logo_path = find_logo_path()
    left_logo_html = '<div class="logo-fallback">V</div>'
    if logo_path:
        # st.image를 쓰면 header 내부 레이아웃 제어가 어려워 base64 없이 Streamlit 컬럼으로 처리
        cols = st.columns([0.035, 0.56, 0.405])
        with cols[0]:
            st.image(str(logo_path), width=42)
        with cols[1]:
            st.markdown(
                '<div style="display:flex;align-items:center;gap:12px;height:46px;">'
                '<h1 class="page-title">[사업기획] 주간 업무 현황</h1>'
                '<span class="api-badge">Jira API 연동</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        with cols[2]:
            st.markdown(
                f'<div style="height:46px;display:flex;align-items:center;justify-content:flex-end;" class="last-updated">마지막 업데이트 : {fmt_datetime(last_updated)}</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            f"""
            <div class="top-header">
                <div class="title-left">
                    {left_logo_html}
                    <h1 class="page-title">[사업기획] 주간 업무 현황</h1>
                    <span class="api-badge">Jira API 연동</span>
                </div>
                <div class="last-updated">마지막 업데이트 : {fmt_datetime(last_updated)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_filter_bar(week_start: date, week_end: date) -> tuple[str, str]:
    c1, c2, c3, c4 = st.columns([1.8, 1.4, 3.2, 1.1])
    with c1:
        st.text_input(
            "기간",
            value=f"{week_start:%Y-%m-%d} (월) ~ {week_end:%Y-%m-%d} (금)",
            disabled=True,
        )
    with c2:
        team = st.selectbox("팀", ["사업기획팀"], index=0)
    with c3:
        jql_extra = st.text_input("필터링 (JQL)", placeholder="예: priority = High")
    with c4:
        st.write("")
        if st.button("새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    return team, jql_extra


def render_kpi_cards(data: DashboardData) -> None:
    items = [
        ("blue", "📝", "신규 발행", len(data.new_this_week), "이번 주 생성된 티켓"),
        ("green", "🔄", "상태 변경", len(data.changed_this_week), "이번 주 업데이트된 티켓"),
        ("orange", "⏳", "1주 이상 정체", len(data.stale_7), "7일 이상 미변경"),
        ("red", "🚨", "2주 이상 정체", len(data.stale_14), "14일 이상 미변경"),
        ("purple", "👥", "지난 회의 미완료", len(data.meeting_unfinished), "weekly-meeting 라벨"),
    ]
    cards = []
    for color, icon, label, value, desc in items:
        cards.append(
            f"""
            <div class="kpi-card">
                <div class="kpi-top">
                    <div>
                        <div class="kpi-label {color}">{h(label)}</div>
                    </div>
                    <div class="kpi-icon {color}-bg">{icon}</div>
                </div>
                <div class="kpi-number">{value:,}</div>
                <div class="kpi-desc">{h(desc)}</div>
            </div>
            """
        )
    st.markdown(f'<div class="kpi-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def ticket_badge(row: dict[str, Any], current_day: date | None = None) -> tuple[str, str]:
    created = row.get("작성일_date")
    updated = row.get("마지막변경일_date")
    status = row.get("상태", "")
    if current_day and created == current_day:
        return "신규", "blue"
    if current_day and updated == current_day:
        return f"변경 · {status}", "green"
    if is_done_status(status):
        return status, "purple"
    if is_hold_status(status):
        return status, "gray"
    return status or "상태 없음", "blue"


def render_ticket_card(row: dict[str, Any], current_day: date | None = None) -> str:
    stale = days_since_updated(row)
    line = status_color_class(row.get("상태", ""), stale)
    badge_text, badge_color = ticket_badge(row, current_day)
    return f"""
        <div class="ticket-card {line}-line">
            <div class="ticket-key">{h(row.get('키'))}</div>
            <div class="ticket-summary">{h(row.get('요약'))}</div>
            <div class="ticket-foot">
                <span class="badge {badge_color}">{h(badge_text)}</span>
                <span>{h(row.get('담당자'))}</span>
            </div>
        </div>
    """


def render_weekly_board(data: DashboardData) -> None:
    day_names = ["월요일", "화요일", "수요일", "목요일", "금요일"]
    columns = []
    for idx, day in enumerate([data.week_start + timedelta(days=i) for i in range(5)]):
        items = data.by_day.get(day, [])
        new_count = sum(1 for r in data.rows if r.get("작성일_date") == day)
        changed_count = sum(1 for r in data.rows if r.get("마지막변경일_date") == day)
        cards_html = "".join(render_ticket_card(r, day) for r in items[:5])
        if len(items) > 5:
            cards_html += f'<div class="small-link" style="text-align:center;font-size:13px;">+ {len(items)-5}건 더 보기</div>'
        if not items:
            cards_html = '<div class="empty-state">해당 요일 티켓 없음</div>'

        columns.append(
            f"""
            <div class="day-column">
                <div class="day-head">
                    <span class="day-name">{day_names[idx]}</span>
                    <span class="day-date">{day:%m-%d}</span>
                    <div class="day-counts">
                        <span class="blue">신규 {new_count}</span>
                        <span class="green">변경 {changed_count}</span>
                    </div>
                </div>
                {cards_html}
            </div>
            """
        )

    st.markdown(
        f"""
        <div class="section-card">
            <div class="section-title">주간 티켓 흐름 <span style="font-size:13px;color:#64748B;">신규 발생 & 상태 변경</span></div>
            <div class="board-grid">{''.join(columns)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_delay_group(title: str, rows: list[dict[str, Any]], color: str) -> str:
    if not rows:
        return f"""
        <div class="delay-box">
            <div class="delay-head {color}-bg {color}">{h(title)} (0)</div>
            <div class="empty-state" style="margin:12px;">해당 티켓 없음</div>
        </div>
        """

    items = []
    sorted_rows = sorted(rows, key=lambda r: days_since_updated(r), reverse=True)
    for row in sorted_rows[:5]:
        stale = days_since_updated(row)
        items.append(
            f"""
            <div class="delay-item">
                <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
                    <div>
                        <div class="ticket-key">{h(row.get('키'))}</div>
                        <div class="ticket-summary" style="min-height:auto;margin-bottom:0;">{h(row.get('요약'))}</div>
                    </div>
                    <span class="badge {color}">{stale}일 정체</span>
                </div>
                <div class="delay-meta">
                    <span>담당자 {h(row.get('담당자'))}</span>
                    <span>|</span>
                    <span>마지막 변경 {h(row.get('마지막변경일'))}</span>
                </div>
            </div>
            """
        )
    more = ""
    if len(rows) > 5:
        more = f'<div style="text-align:center;padding:10px;color:#2563EB;font-weight:700;font-size:13px;">+ {len(rows)-5}건 더 있음</div>'

    return f"""
    <div class="delay-box">
        <div class="delay-head {color}-bg {color}">{h(title)} ({len(rows)})</div>
        {''.join(items)}
        {more}
    </div>
    """


def render_delay_panel(data: DashboardData) -> None:
    html_block = f"""
    <div class="section-card">
        <div class="section-title">검토 지연 알림 <span style="font-size:13px;color:#64748B;">updated 기준</span></div>
        {render_delay_group('2주 이상 정체', data.stale_14, 'red')}
        {render_delay_group('1주 이상 정체', data.stale_7, 'orange')}
    </div>
    """
    st.markdown(html_block, unsafe_allow_html=True)


def linkify_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "링크" in out.columns:
        out["링크"] = out["링크"].apply(lambda x: f'<a href="{h(x)}" target="_blank">열기</a>' if x else "")
    return out


def render_followup_table(data: DashboardData) -> None:
    cols = ["키", "요약", "담당자", "상태", "마지막변경일", "링크"]
    df = pd.DataFrame(data.meeting_unfinished)
    st.markdown('<div class="section-card"><div class="section-title">지난주 회의 안건 Follow-up</div>', unsafe_allow_html=True)
    if df.empty:
        st.markdown('<div class="empty-state">weekly-meeting 라벨의 미완료 티켓이 없습니다.</div>', unsafe_allow_html=True)
    else:
        df = df[cols]
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_ticket_table(data: DashboardData) -> None:
    cols = ["키", "요약", "상태", "담당자", "작성일", "마지막변경일", "라벨문자열", "링크"]
    df = pd.DataFrame(data.rows)
    st.markdown('<div class="section-card"><div class="section-title">전체 티켓 리스트</div>', unsafe_allow_html=True)
    if df.empty:
        st.markdown('<div class="empty-state">표시할 티켓이 없습니다.</div>', unsafe_allow_html=True)
    else:
        df = df[cols].rename(columns={"라벨문자열": "라벨"})
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "링크": st.column_config.LinkColumn("링크", display_text="열기"),
            },
        )
    st.markdown('</div>', unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 7. 메인
# -----------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="[사업기획] 주간 업무 현황", layout="wide")
    inject_css()

    if not require_config():
        st.stop()

    week_start, week_end = get_week_range()
    render_header(datetime.now())
    _, jql_extra = render_filter_bar(week_start, week_end)

    try:
        raw_list = fetch_all_issues_raw(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, jql_extra)
        rows = [flatten_issue(JIRA_SERVER, issue) for issue in raw_list]
        dashboard = calculate_dashboard_data(rows, week_start, week_end)

        render_kpi_cards(dashboard)

        left, right = st.columns([0.70, 0.30], gap="medium")
        with left:
            render_weekly_board(dashboard)
        with right:
            render_delay_panel(dashboard)

        bottom_left, bottom_right = st.columns([0.48, 0.52], gap="medium")
        with bottom_left:
            render_followup_table(dashboard)
        with bottom_right:
            render_ticket_table(dashboard)

    except requests.HTTPError as e:
        response_text = e.response.text if getattr(e, "response", None) is not None else ""
        st.error(f"Jira API 호출에 실패했습니다: {e}\n\n{response_text}")
    except Exception as e:
        st.error(f"데이터를 가져오거나 화면을 구성하는 중 오류가 발생했습니다: {e}")


if __name__ == "__main__":
    main()
