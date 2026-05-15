"""
사업기획팀 주간 현황 대시보드
- Jira에서 볼 수 없는 것만 보여준다
- 감시툴 X → 팀 함께 보는 현황판
"""

from __future__ import annotations

import html
import os
from datetime import date, datetime, timedelta
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

_DONE_NAMES = frozenset({"완료", "Done", "DONE", "Closed", "closed", "Resolved", "resolved", "drop", "Drop", "DROP"})
_HOLD_NAMES = frozenset({"홀딩", "Hold", "HOLD", "On Hold", "on hold", "보류"})


# -----------------------------------------------------------------------------
# 2. 유틸
# -----------------------------------------------------------------------------
def require_config() -> bool:
    missing = []
    if not JIRA_SERVER: missing.append("JIRA_SERVER")
    if not JIRA_EMAIL: missing.append("JIRA_EMAIL")
    if not JIRA_API_TOKEN: missing.append("JIRA_API_TOKEN")
    if not JIRA_PROJECT_KEY: missing.append("JIRA_PROJECT_KEY")
    if missing:
        st.error(f".env 파일에 다음 정보가 없습니다: {', '.join(missing)}")
        return False
    return True


def h(value: Any) -> str:
    return html.escape(str(value or ""))


def parse_jira_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None


def fmt_date(value: Any) -> str:
    if value is None: return "—"
    if isinstance(value, datetime): return value.strftime("%Y-%m-%d")
    if isinstance(value, date): return value.strftime("%Y-%m-%d")
    return str(value)[:10] if value else "—"


def fmt_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def get_week_range(offset_weeks: int = 0) -> tuple[date, date]:
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)
    friday = monday + timedelta(days=4)
    return monday, friday


def is_done(status: str) -> bool:
    return status in _DONE_NAMES

def is_hold(status: str) -> bool:
    return status in _HOLD_NAMES

def days_stale(updated_date: date | None) -> int:
    if not updated_date:
        return 0
    return max((date.today() - updated_date).days, 0)


# -----------------------------------------------------------------------------
# 3. Jira API
# -----------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_issues(server: str, email: str, token: str, project_key: str, jql_extra: str = "") -> list[dict]:
    jql = f'project = "{project_key}"'
    if jql_extra.strip():
        jql = f"({jql}) AND ({jql_extra.strip()})"
    jql += " ORDER BY updated DESC"

    auth = (email, token)
    params = {
        "jql": jql,
        "maxResults": 200,
        "fields": ["summary", "status", "assignee", "updated", "created", "priority", "duedate", "labels"],
    }
    url = f"{server}/rest/api/3/search/jql"
    r = requests.get(url, auth=auth, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("issues", [])


@st.cache_data(ttl=60, show_spinner=False)
def fetch_issue_detail(server: str, email: str, token: str, issue_key: str) -> dict:
    auth = (email, token)
    url = f"{server}/rest/api/3/issue/{issue_key}"
    r = requests.get(url, auth=auth, params={"fields": "summary,status,assignee,comment,priority,duedate"}, timeout=30)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_transitions(server: str, email: str, token: str, issue_key: str) -> list[dict]:
    auth = (email, token)
    url = f"{server}/rest/api/3/issue/{issue_key}/transitions"
    r = requests.get(url, auth=auth, timeout=30)
    r.raise_for_status()
    return r.json().get("transitions", [])


def add_comment(server: str, email: str, token: str, issue_key: str, text: str) -> bool:
    auth = (email, token)
    url = f"{server}/rest/api/3/issue/{issue_key}/comment"
    payload = {
        "body": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]
        }
    }
    r = requests.post(url, auth=auth, json=payload, timeout=30)
    return r.status_code in (200, 201)


def change_status(server: str, email: str, token: str, issue_key: str, transition_id: str) -> bool:
    auth = (email, token)
    url = f"{server}/rest/api/3/issue/{issue_key}/transitions"
    r = requests.post(url, auth=auth, json={"transition": {"id": transition_id}}, timeout=30)
    return r.status_code in (200, 204)


# -----------------------------------------------------------------------------
# 4. 데이터 가공
# -----------------------------------------------------------------------------
def flatten(server: str, raw: dict) -> dict:
    fields = raw.get("fields") or {}
    status = (fields.get("status") or {}).get("name") or ""
    assignee = fields.get("assignee")
    created_dt = parse_jira_datetime(fields.get("created"))
    updated_dt = parse_jira_datetime(fields.get("updated"))

    return {
        "키": raw.get("key") or "",
        "요약": fields.get("summary") or "",
        "상태": status,
        "담당자": (assignee or {}).get("displayName") if assignee else "미지정",
        "작성일_date": created_dt.date() if created_dt else None,
        "변경일_date": updated_dt.date() if updated_dt else None,
        "변경일": fmt_date(updated_dt),
        "만기일": fmt_date(fields.get("duedate")),
        "링크": f"{server}/browse/{raw.get('key')}",
        "라벨": fields.get("labels") or [],
    }


def build_data(rows: list[dict], week_start: date, week_end: date) -> dict:
    today = date.today()

    def in_week(d): return d is not None and week_start <= d <= week_end

    new_this_week = [r for r in rows if in_week(r.get("작성일_date"))]
    done_this_week = [r for r in rows if in_week(r.get("변경일_date")) and is_done(r.get("상태", ""))]

    # Drop 제외한 진짜 정체
    active = [r for r in rows if not is_done(r.get("상태", "")) and not is_hold(r.get("상태", ""))]
    need_attention = sorted(
        [r for r in active if days_stale(r.get("변경일_date")) >= 7],
        key=lambda r: days_stale(r.get("변경일_date")),
        reverse=True
    )

    # 요일별 신규
    by_day = {}
    for i in range(5):
        d = week_start + timedelta(days=i)
        by_day[d] = [r for r in rows if r.get("작성일_date") == d]

    return {
        "new_this_week": new_this_week,
        "done_this_week": done_this_week,
        "need_attention": need_attention,
        "by_day": by_day,
        "total_active": len(active),
    }


# -----------------------------------------------------------------------------
# 5. CSS
# -----------------------------------------------------------------------------
def inject_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap');

    html, body, [class*="css"] {
        font-family: 'Noto Sans KR', system-ui, sans-serif;
    }
    .stApp { background: #F0F4F8; }
    .block-container { padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1600px; }

    .dash-header {
        background: white;
        border-radius: 16px;
        padding: 20px 24px;
        margin-bottom: 20px;
        border: 1px solid #E2E8F0;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .dash-title { font-size: 22px; font-weight: 800; color: #0F172A; letter-spacing: -0.03em; }
    .dash-sub { font-size: 13px; color: #64748B; margin-top: 3px; }
    .dash-week { font-size: 13px; color: #94A3B8; }

    .summary-bar {
        display: flex;
        gap: 12px;
        margin-bottom: 20px;
    }
    .summary-chip {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 14px 20px;
        flex: 1;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .summary-chip-label { font-size: 12px; color: #64748B; font-weight: 600; margin-bottom: 4px; }
    .summary-chip-value { font-size: 28px; font-weight: 800; color: #0F172A; letter-spacing: -0.04em; }
    .summary-chip-diff { font-size: 12px; margin-top: 4px; }
    .diff-up { color: #059669; }
    .diff-down { color: #DC2626; }
    .diff-same { color: #94A3B8; }

    .section-wrap {
        background: white;
        border-radius: 16px;
        border: 1px solid #E2E8F0;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .section-label {
        font-size: 15px;
        font-weight: 800;
        color: #0F172A;
        margin-bottom: 14px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .section-count {
        background: #F1F5F9;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 700;
        color: #475569;
        padding: 2px 8px;
    }

    .attention-item {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 14px;
        border-radius: 12px;
        border: 1px solid #E2E8F0;
        margin-bottom: 10px;
        background: #FAFAFA;
        transition: border-color 0.15s;
    }
    .attention-item:hover { border-color: #94A3B8; background: white; }
    .attention-days {
        min-width: 52px;
        text-align: center;
        border-radius: 10px;
        padding: 6px 4px;
        font-size: 13px;
        font-weight: 800;
    }
    .days-red { background: #FEE2E2; color: #DC2626; }
    .days-orange { background: #FFEDD5; color: #EA580C; }
    .attention-key { font-size: 12px; font-weight: 700; color: #2563EB; margin-bottom: 3px; }
    .attention-summary { font-size: 13px; color: #1E293B; line-height: 1.4; margin-bottom: 6px; }
    .attention-meta { font-size: 12px; color: #94A3B8; display: flex; gap: 10px; flex-wrap: wrap; }
    .status-badge {
        display: inline-block;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
        padding: 2px 8px;
        background: #EFF6FF;
        color: #2563EB;
        border: 1px solid #BFDBFE;
    }

    .new-item {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 12px;
        border-radius: 10px;
        border: 1px solid #E2E8F0;
        margin-bottom: 8px;
        background: white;
    }
    .new-dot {
        width: 8px; height: 8px;
        border-radius: 50%;
        background: #2563EB;
        flex-shrink: 0;
    }
    .new-key { font-size: 12px; font-weight: 700; color: #2563EB; white-space: nowrap; }
    .new-summary { font-size: 13px; color: #334155; flex: 1; }
    .new-assignee { font-size: 12px; color: #94A3B8; white-space: nowrap; }

    .day-grid {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 10px;
        margin-top: 4px;
    }
    .day-col {
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 12px;
        background: #F8FAFC;
        min-height: 80px;
    }
    .day-col-head {
        font-size: 13px;
        font-weight: 800;
        color: #0F172A;
        margin-bottom: 4px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .day-col-date { font-size: 11px; color: #94A3B8; }
    .day-count { font-size: 20px; font-weight: 800; color: #2563EB; margin: 6px 0; }
    .day-empty { font-size: 12px; color: #CBD5E1; }
    .day-items { margin-top: 8px; }
    .day-ticket-key { font-size: 11px; font-weight: 700; color: #2563EB; }
    .day-ticket-summary { font-size: 11px; color: #475569; line-height: 1.3; margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    .empty-msg {
        text-align: center;
        color: #CBD5E1;
        font-size: 13px;
        padding: 20px;
        border: 1px dashed #E2E8F0;
        border-radius: 10px;
    }

    .comment-box {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 12px;
        margin-bottom: 8px;
    }
    .comment-author { font-size: 13px; font-weight: 700; color: #0F172A; }
    .comment-date { font-size: 11px; color: #94A3B8; margin-left: 6px; }
    .comment-text { font-size: 13px; color: #334155; margin-top: 5px; line-height: 1.5; }
    </style>
    """, unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 6. 티켓 상세 사이드바
# -----------------------------------------------------------------------------
def render_sidebar(issue_key: str):
    with st.sidebar:
        st.markdown(f"### 🎫 {issue_key}")
        st.markdown("---")

        try:
            detail = fetch_issue_detail(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, issue_key)
            fields = detail.get("fields") or {}
            summary = fields.get("summary") or ""
            status = (fields.get("status") or {}).get("name") or ""
            assignee_info = fields.get("assignee")
            assignee = (assignee_info or {}).get("displayName") if assignee_info else "미지정"
            due_date = fmt_date(fields.get("duedate"))

            st.markdown(f"**{summary}**")
            st.markdown(f"📌 상태: `{status}`")
            st.markdown(f"👤 담당자: {assignee}")
            st.markdown(f"📅 만기일: {due_date}")
            st.markdown(f"🔗 [Jira에서 열기]({JIRA_SERVER}/browse/{issue_key})")
            st.markdown("---")

            # 상태 변경
            st.markdown("**🔄 상태 변경**")
            transitions = fetch_transitions(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, issue_key)
            if transitions:
                t_map = {t["name"]: t["id"] for t in transitions}
                selected = st.selectbox("변경할 상태", list(t_map.keys()), key=f"t_{issue_key}")
                if st.button("적용", key=f"apply_{issue_key}", use_container_width=True):
                    if change_status(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, issue_key, t_map[selected]):
                        st.success(f"✅ '{selected}' 으로 변경됐어요!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("변경 실패. 권한을 확인해주세요.")
            else:
                st.info("가능한 상태 전환 없음")

            st.markdown("---")

            # 댓글
            st.markdown("**💬 댓글 달기**")
            comment_text = st.text_area("내용", placeholder="댓글을 입력하세요...", key=f"c_{issue_key}", height=80, label_visibility="collapsed")
            if st.button("등록", key=f"post_{issue_key}", use_container_width=True):
                if comment_text.strip():
                    if add_comment(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, issue_key, comment_text.strip()):
                        st.success("✅ 댓글이 등록됐어요!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("등록 실패")
                else:
                    st.warning("내용을 입력해주세요.")

            st.markdown("---")

            # 댓글 목록
            st.markdown("**📋 댓글 목록**")
            comments = (fields.get("comment") or {}).get("comments") or []
            if comments:
                for c in reversed(comments[-8:]):
                    author = (c.get("author") or {}).get("displayName") or "알 수 없음"
                    created = parse_jira_datetime(c.get("created"))
                    body_text = ""
                    for block in ((c.get("body") or {}).get("content") or []):
                        for inline in (block.get("content") or []):
                            if inline.get("type") == "text":
                                body_text += inline.get("text", "")
                    st.markdown(
                        f'<div class="comment-box"><span class="comment-author">{h(author)}</span>'
                        f'<span class="comment-date">{fmt_datetime(created)}</span>'
                        f'<div class="comment-text">{h(body_text)}</div></div>',
                        unsafe_allow_html=True
                    )
            else:
                st.markdown('<div class="empty-msg">아직 댓글이 없습니다.</div>', unsafe_allow_html=True)

        except Exception as e:
            st.error(f"불러오기 실패: {e}")

        st.markdown("---")
        if st.button("✖ 닫기", use_container_width=True):
            st.session_state.selected_ticket = None
            st.rerun()


# -----------------------------------------------------------------------------
# 7. 렌더링
# -----------------------------------------------------------------------------
def render_header(week_start: date, week_end: date, last_updated: datetime):
    st.markdown(
        f"""
        <div class="dash-header">
            <div>
                <div class="dash-title">🗂 사업기획팀 주간 현황</div>
                <div class="dash-sub">Jira에서 한눈에 보기 어려운 것들을 모았어요</div>
            </div>
            <div class="dash-week">
                {week_start:%Y.%m.%d}(월) ~ {week_end:%Y.%m.%d}(금)<br>
                <span style="font-size:11px;">업데이트 {fmt_datetime(last_updated)}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


def render_summary_bar(this_week: dict, last_week: dict):
    new_this = len(this_week["new_this_week"])
    new_last = len(last_week["new_this_week"])
    done_this = len(this_week["done_this_week"])
    done_last = len(last_week["done_this_week"])
    attention_this = len(this_week["need_attention"])
    attention_last = len(last_week["need_attention"])

    def diff_html(a, b, reverse=False):
        diff = a - b
        if diff == 0:
            return '<span class="diff-same">지난주와 동일</span>'
        if (diff > 0 and not reverse) or (diff < 0 and reverse):
            return f'<span class="diff-up">▲ {abs(diff)} 지난주 대비</span>'
        return f'<span class="diff-down">▼ {abs(diff)} 지난주 대비</span>'

    st.markdown(
        f"""
        <div class="summary-bar">
            <div class="summary-chip">
                <div class="summary-chip-label">📝 이번주 새로 시작한 것</div>
                <div class="summary-chip-value">{new_this}</div>
                <div class="summary-chip-diff">{diff_html(new_this, new_last)}</div>
            </div>
            <div class="summary-chip">
                <div class="summary-chip-label">✅ 이번주 완료한 것</div>
                <div class="summary-chip-value">{done_this}</div>
                <div class="summary-chip-diff">{diff_html(done_this, done_last)}</div>
            </div>
            <div class="summary-chip">
                <div class="summary-chip-label">💬 같이 확인이 필요한 것</div>
                <div class="summary-chip-value">{attention_this}</div>
                <div class="summary-chip-diff">{diff_html(attention_this, attention_last, reverse=True)}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


def render_attention(rows: list[dict]):
    st.markdown(
        f'<div class="section-label">💬 같이 확인이 필요한 것들 <span class="section-count">{len(rows)}건 · Drop 제외</span></div>',
        unsafe_allow_html=True
    )

    if not rows:
        st.markdown('<div class="empty-msg">🎉 현재 정체된 티켓이 없어요!</div>', unsafe_allow_html=True)
        return

    for row in rows[:10]:
        stale = days_stale(row.get("변경일_date"))
        days_class = "days-red" if stale >= 14 else "days-orange"
        st.markdown(
            f"""
            <div class="attention-item">
                <div class="attention-days {days_class}">{stale}일</div>
                <div style="flex:1;min-width:0;">
                    <div class="attention-key">{h(row.get('키'))}</div>
                    <div class="attention-summary">{h(row.get('요약'))}</div>
                    <div class="attention-meta">
                        <span>{h(row.get('담당자'))}</span>
                        <span>마지막 변경 {h(row.get('변경일'))}</span>
                        <span class="status-badge">{h(row.get('상태'))}</span>
                    </div>
                </div>
                <a href="{h(row.get('링크'))}" target="_blank" style="font-size:12px;color:#2563EB;font-weight:700;white-space:nowrap;">열기 →</a>
            </div>
            """,
            unsafe_allow_html=True
        )

    if len(rows) > 10:
        st.markdown(f'<div style="text-align:center;font-size:12px;color:#94A3B8;padding:8px;">+ {len(rows)-10}건 더 있음</div>', unsafe_allow_html=True)


def render_new_tickets(rows: list[dict]):
    st.markdown(
        f'<div class="section-label">📝 이번주 새로 추가된 것들 <span class="section-count">{len(rows)}건</span></div>',
        unsafe_allow_html=True
    )

    if not rows:
        st.markdown('<div class="empty-msg">이번주 신규 티켓이 없어요</div>', unsafe_allow_html=True)
        return

    for row in rows:
        st.markdown(
            f"""
            <div class="new-item">
                <div class="new-dot"></div>
                <span class="new-key">{h(row.get('키'))}</span>
                <span class="new-summary">{h(row.get('요약'))}</span>
                <span class="new-assignee">{h(row.get('담당자'))}</span>
                <a href="{h(row.get('링크'))}" target="_blank" style="font-size:11px;color:#2563EB;white-space:nowrap;">열기</a>
            </div>
            """,
            unsafe_allow_html=True
        )


def render_weekly_flow(by_day: dict[date, list[dict]]):
    day_names = ["월", "화", "수", "목", "금"]
    cols_html = ""

    for i, (d, items) in enumerate(sorted(by_day.items())):
        count = len(items)
        if count == 0:
            inner = '<div class="day-empty">없음</div>'
        else:
            # 최대 3개만 미리보기
            preview = ""
            for r in items[:3]:
                preview += f'<div class="day-ticket-key">{h(r.get("키"))}</div><div class="day-ticket-summary">{h(r.get("요약"))}</div>'
            if count > 3:
                preview += f'<div style="font-size:11px;color:#2563EB;font-weight:700;">+ {count-3}건 더</div>'
            inner = f'<div class="day-items">{preview}</div>'

        cols_html += f"""
        <div class="day-col">
            <div class="day-col-head">
                <span>{day_names[i]}</span>
                <span class="day-col-date">{d:%m/%d}</span>
            </div>
            <div class="day-count">{count}</div>
            {inner}
        </div>
        """

    st.markdown(
        f'<div class="section-label">📅 요일별 신규 티켓</div>'
        f'<div class="day-grid">{cols_html}</div>',
        unsafe_allow_html=True
    )


# -----------------------------------------------------------------------------
# 8. 메인
# -----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="사업기획팀 주간 현황", layout="wide")
    inject_css()

    if "selected_ticket" not in st.session_state:
        st.session_state.selected_ticket = None

    if not require_config():
        st.stop()

    if st.session_state.selected_ticket:
        render_sidebar(st.session_state.selected_ticket)

    week_start, week_end = get_week_range(0)
    last_week_start, last_week_end = get_week_range(-1)

    render_header(week_start, week_end, datetime.now())

    # 새로고침 버튼
    col_refresh, col_ticket = st.columns([5, 1])
    with col_refresh:
        pass
    with col_ticket:
        if st.button("🔄 새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    try:
        with st.spinner("데이터 불러오는 중..."):
            raw_this = fetch_issues(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY)
            rows_this = [flatten(JIRA_SERVER, r) for r in raw_this]
            this_week = build_data(rows_this, week_start, week_end)

            # 지난주 데이터 (같은 전체 데이터에서 필터)
            last_week = build_data(rows_this, last_week_start, last_week_end)

        # 요약 바
        render_summary_bar(this_week, last_week)

        # 메인 레이아웃: 왼쪽(확인 필요) / 오른쪽(신규)
        left, right = st.columns([0.55, 0.45], gap="medium")

        with left:
            st.markdown('<div class="section-wrap">', unsafe_allow_html=True)
            render_attention(this_week["need_attention"])
            st.markdown('</div>', unsafe_allow_html=True)

            # 티켓 상세 보기 입력
            st.markdown('<div class="section-wrap">', unsafe_allow_html=True)
            st.markdown('<div class="section-label">🎫 티켓 상태변경 / 댓글</div>', unsafe_allow_html=True)
            c1, c2 = st.columns([3, 1])
            with c1:
                ticket_input = st.text_input("티켓 키", placeholder="예: BP-123", label_visibility="collapsed", key="ticket_input")
            with c2:
                if st.button("열기", use_container_width=True):
                    if ticket_input.strip():
                        st.session_state.selected_ticket = ticket_input.strip().upper()
                        st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        with right:
            st.markdown('<div class="section-wrap">', unsafe_allow_html=True)
            render_new_tickets(this_week["new_this_week"])
            st.markdown('</div>', unsafe_allow_html=True)

        # 요일별 흐름
        st.markdown('<div class="section-wrap">', unsafe_allow_html=True)
        render_weekly_flow(this_week["by_day"])
        st.markdown('</div>', unsafe_allow_html=True)

    except requests.HTTPError as e:
        st.error(f"Jira API 오류: {e}")
    except Exception as e:
        st.error(f"오류가 발생했습니다: {e}")


if __name__ == "__main__":
    main()