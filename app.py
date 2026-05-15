"""
사업기획팀 주간 현황 대시보드
- 왼쪽: 같이 확인이 필요한 것들 (1주/2주 이상 경과, 더보기)
- 오른쪽: 요일별 신규 티켓 카드 (더보기)
"""

from __future__ import annotations

import html
import os
from datetime import date, datetime, timedelta
from typing import Any

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

JIRA_SERVER = (os.getenv("JIRA_SERVER") or "").rstrip("/")
JIRA_EMAIL = (os.getenv("JIRA_EMAIL") or "").strip()
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN") or ""
JIRA_PROJECT_KEY = (os.getenv("JIRA_PROJECT_KEY") or "").strip()

_DONE_NAMES = frozenset({
    "완료", "Done", "DONE", "Closed", "closed", "Resolved", "resolved",
    "drop", "Drop", "DROP"
})
_HOLD_NAMES = frozenset({"홀딩", "Hold", "HOLD", "On Hold", "on hold", "보류"})


# -----------------------------------------------------------------------------
# 유틸
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


def h(v: Any) -> str:
    return html.escape(str(v or ""))


def parse_dt(v: str | None) -> datetime | None:
    if not v: return None
    try: return datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try: return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError: return None


def fmt_date(v: Any) -> str:
    if v is None: return "—"
    if isinstance(v, datetime): return v.strftime("%Y-%m-%d")
    if isinstance(v, date): return v.strftime("%Y-%m-%d")
    return str(v)[:10]


def fmt_dt(v: datetime | None) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if v else "—"


def week_range(offset: int = 0) -> tuple[date, date]:
    today = date.today()
    mon = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return mon, mon + timedelta(days=4)


def is_done(s: str) -> bool: return s in _DONE_NAMES
def is_hold(s: str) -> bool: return s in _HOLD_NAMES

def stale_days(d: date | None) -> int:
    if not d: return 0
    return max((date.today() - d).days, 0)


# -----------------------------------------------------------------------------
# Jira API
# -----------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_issues(server, email, token, project_key):
    jql = f'project = "{project_key}" ORDER BY updated DESC'
    r = requests.get(
        f"{server}/rest/api/3/search/jql",
        auth=(email, token),
        params={"jql": jql, "maxResults": 200,
                "fields": ["summary","status","assignee","updated","created","duedate","labels"]},
        timeout=30
    )
    r.raise_for_status()
    return r.json().get("issues", [])


@st.cache_data(ttl=60, show_spinner=False)
def fetch_detail(server, email, token, key):
    r = requests.get(
        f"{server}/rest/api/3/issue/{key}",
        auth=(email, token),
        params={"fields": "summary,status,assignee,comment,priority,duedate"},
        timeout=30
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_transitions(server, email, token, key):
    r = requests.get(f"{server}/rest/api/3/issue/{key}/transitions",
                     auth=(email, token), timeout=30)
    r.raise_for_status()
    return r.json().get("transitions", [])


def post_comment(server, email, token, key, text):
    r = requests.post(
        f"{server}/rest/api/3/issue/{key}/comment",
        auth=(email, token),
        json={"body": {"type":"doc","version":1,"content":[
            {"type":"paragraph","content":[{"type":"text","text":text}]}
        ]}},
        timeout=30
    )
    return r.status_code in (200, 201)


def do_transition(server, email, token, key, tid):
    r = requests.post(
        f"{server}/rest/api/3/issue/{key}/transitions",
        auth=(email, token),
        json={"transition": {"id": tid}},
        timeout=30
    )
    return r.status_code in (200, 204)


# -----------------------------------------------------------------------------
# 데이터
# -----------------------------------------------------------------------------
def flatten(server, raw):
    f = raw.get("fields") or {}
    status = (f.get("status") or {}).get("name") or ""
    assignee = f.get("assignee")
    cdt = parse_dt(f.get("created"))
    udt = parse_dt(f.get("updated"))
    return {
        "키": raw.get("key") or "",
        "요약": f.get("summary") or "",
        "상태": status,
        "담당자": (assignee or {}).get("displayName") if assignee else "미지정",
        "작성일_date": cdt.date() if cdt else None,
        "변경일_date": udt.date() if udt else None,
        "변경일": fmt_date(udt),
        "링크": f"{server}/browse/{raw.get('key')}",
    }


def build(rows, ws, we):
    def in_week(d): return d and ws <= d <= we

    new_week = [r for r in rows if in_week(r.get("작성일_date"))]
    done_week = [r for r in rows if in_week(r.get("변경일_date")) and is_done(r.get("상태",""))]
    active = [r for r in rows if not is_done(r.get("상태","")) and not is_hold(r.get("상태",""))]

    stale_14 = sorted([r for r in active if stale_days(r.get("변경일_date")) >= 14],
                      key=lambda r: stale_days(r.get("변경일_date")), reverse=True)
    stale_7 = sorted([r for r in active if 7 <= stale_days(r.get("변경일_date")) < 14],
                     key=lambda r: stale_days(r.get("변경일_date")), reverse=True)

    by_day = {ws + timedelta(days=i): [r for r in rows if r.get("작성일_date") == ws + timedelta(days=i)]
              for i in range(5)}

    return {"new_week": new_week, "done_week": done_week,
            "stale_14": stale_14, "stale_7": stale_7, "by_day": by_day}


# -----------------------------------------------------------------------------
# CSS
# -----------------------------------------------------------------------------
def inject_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap');
    html, body, [class*="css"] { font-family: 'Noto Sans KR', system-ui, sans-serif; }
    .stApp { background: #F0F4F8; }
    .block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1600px; }

    .dash-header {
        background: white; border-radius: 16px; padding: 18px 24px; margin-bottom: 16px;
        border: 1px solid #E2E8F0; display: flex; align-items: center;
        justify-content: space-between; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .dash-title { font-size: 22px; font-weight: 800; color: #0F172A; letter-spacing: -0.03em; }
    .dash-sub { font-size: 13px; color: #64748B; margin-top: 3px; }
    .dash-week { font-size: 13px; color: #94A3B8; text-align: right; }

    .summary-bar { display: flex; gap: 12px; margin-bottom: 16px; }
    .s-chip {
        background: white; border: 1px solid #E2E8F0; border-radius: 12px;
        padding: 14px 20px; flex: 1; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .s-label { font-size: 12px; color: #64748B; font-weight: 600; margin-bottom: 4px; }
    .s-value { font-size: 28px; font-weight: 800; color: #0F172A; letter-spacing: -0.04em; }
    .s-diff { font-size: 12px; margin-top: 4px; }
    .up { color: #059669; } .dn { color: #DC2626; } .eq { color: #94A3B8; }

    .card {
        background: white; border-radius: 16px; border: 1px solid #E2E8F0;
        padding: 18px; margin-bottom: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .card-title {
        font-size: 15px; font-weight: 800; color: #0F172A;
        margin-bottom: 12px; display: flex; align-items: center; gap: 8px;
    }
    .badge {
        background: #F1F5F9; border-radius: 999px;
        font-size: 12px; font-weight: 700; color: #475569; padding: 2px 8px;
    }

    .group-head {
        font-size: 13px; font-weight: 800; padding: 8px 12px;
        border-radius: 8px; margin-bottom: 8px;
        display: flex; align-items: center; justify-content: space-between;
    }
    .g-red { background: #FEE2E2; color: #DC2626; }
    .g-orange { background: #FFEDD5; color: #EA580C; }

    .stale-row {
        display: flex; align-items: flex-start; gap: 10px;
        padding: 12px; border-radius: 10px; border: 1px solid #E2E8F0;
        margin-bottom: 8px; background: #FAFAFA;
    }
    .stale-row:hover { border-color: #94A3B8; background: white; }
    .sd-pill {
        min-width: 44px; text-align: center; border-radius: 8px;
        padding: 5px 4px; font-size: 12px; font-weight: 800; flex-shrink: 0;
    }
    .p-red { background: #FEE2E2; color: #DC2626; }
    .p-orange { background: #FFEDD5; color: #EA580C; }
    .sr-key { font-size: 12px; font-weight: 700; color: #2563EB; margin-bottom: 2px; }
    .sr-summary { font-size: 13px; color: #1E293B; line-height: 1.4; margin-bottom: 5px; }
    .sr-meta { font-size: 11px; color: #94A3B8; display: flex; gap: 8px; flex-wrap: wrap; }
    .st-badge {
        display: inline-block; border-radius: 999px; font-size: 11px; font-weight: 700;
        padding: 2px 7px; background: #EFF6FF; color: #2563EB; border: 1px solid #BFDBFE;
    }

    .day-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
    .day-col {
        border: 1px solid #E2E8F0; border-radius: 14px;
        background: #F8FAFC; padding: 12px; min-height: 120px;
    }
    .day-head { text-align: center; margin-bottom: 6px; }
    .day-name { font-size: 14px; font-weight: 800; color: #0F172A; }
    .day-date { font-size: 11px; color: #94A3B8; margin-left: 4px; }
    .day-cnt { font-size: 24px; font-weight: 800; color: #2563EB; text-align: center; margin: 4px 0 8px; }
    .day-none { font-size: 12px; color: #CBD5E1; text-align: center; padding: 8px 0; }
    .t-card {
        background: white; border: 1px solid #E2E8F0; border-radius: 10px;
        padding: 9px; margin-bottom: 7px; border-left: 3px solid #2563EB;
    }
    .t-key { font-size: 11px; font-weight: 800; color: #0F172A; margin-bottom: 3px; }
    .t-sum { font-size: 12px; color: #334155; line-height: 1.4; }
    .t-who { font-size: 11px; color: #94A3B8; margin-top: 4px; }

    .c-box { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 10px; margin-bottom: 8px; }
    .c-author { font-size: 13px; font-weight: 700; color: #0F172A; }
    .c-date { font-size: 11px; color: #94A3B8; margin-left: 6px; }
    .c-text { font-size: 13px; color: #334155; margin-top: 4px; line-height: 1.5; }
    .empty { text-align: center; color: #CBD5E1; font-size: 13px; padding: 20px; border: 1px dashed #E2E8F0; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 사이드바
# -----------------------------------------------------------------------------
def render_sidebar(key: str):
    with st.sidebar:
        st.markdown(f"### 🎫 {key}")
        st.markdown("---")
        try:
            detail = fetch_detail(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key)
            f = detail.get("fields") or {}
            status = (f.get("status") or {}).get("name") or ""
            assignee_info = f.get("assignee")
            assignee = (assignee_info or {}).get("displayName") if assignee_info else "미지정"

            st.markdown(f"**{f.get('summary','')}**")
            st.markdown(f"📌 상태: `{status}`")
            st.markdown(f"👤 담당자: {assignee}")
            st.markdown(f"📅 만기일: {fmt_date(f.get('duedate'))}")
            st.markdown(f"🔗 [Jira에서 열기]({JIRA_SERVER}/browse/{key})")
            st.markdown("---")

            st.markdown("**🔄 상태 변경**")
            transitions = fetch_transitions(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key)
            if transitions:
                t_map = {t["name"]: t["id"] for t in transitions}
                sel = st.selectbox("변경할 상태", list(t_map.keys()), key=f"t_{key}")
                if st.button("적용", key=f"apply_{key}", use_container_width=True):
                    if do_transition(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key, t_map[sel]):
                        st.success(f"✅ '{sel}' 으로 변경됐어요!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("변경 실패")
            else:
                st.info("가능한 상태 전환 없음")

            st.markdown("---")
            st.markdown("**💬 댓글 달기**")
            comment_text = st.text_area("내용", placeholder="댓글을 입력하세요...",
                                        key=f"c_{key}", height=80, label_visibility="collapsed")
            if st.button("등록", key=f"post_{key}", use_container_width=True):
                if comment_text.strip():
                    if post_comment(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key, comment_text.strip()):
                        st.success("✅ 댓글 등록됐어요!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("등록 실패")
                else:
                    st.warning("내용을 입력해주세요.")

            st.markdown("---")
            st.markdown("**📋 댓글 목록**")
            comments = (f.get("comment") or {}).get("comments") or []
            if comments:
                for c in reversed(comments[-8:]):
                    author = (c.get("author") or {}).get("displayName") or "알 수 없음"
                    created = parse_dt(c.get("created"))
                    body_text = ""
                    for block in ((c.get("body") or {}).get("content") or []):
                        for inline in (block.get("content") or []):
                            if inline.get("type") == "text":
                                body_text += inline.get("text", "")
                    st.markdown(
                        f'<div class="c-box"><span class="c-author">{h(author)}</span>'
                        f'<span class="c-date">{fmt_dt(created)}</span>'
                        f'<div class="c-text">{h(body_text)}</div></div>',
                        unsafe_allow_html=True
                    )
            else:
                st.markdown('<div class="empty">아직 댓글이 없습니다.</div>', unsafe_allow_html=True)

        except Exception as e:
            st.error(f"불러오기 실패: {e}")

        st.markdown("---")
        if st.button("✖ 닫기", use_container_width=True):
            st.session_state.selected_ticket = None
            st.rerun()


# -----------------------------------------------------------------------------
# 렌더링
# -----------------------------------------------------------------------------
def render_header(ws, we):
    st.markdown(
        f"""<div class="dash-header">
            <div>
                <div class="dash-title">🗂 사업기획팀 주간 현황</div>
                <div class="dash-sub">Jira에서 한눈에 보기 어려운 것들을 모았어요</div>
            </div>
            <div class="dash-week">
                {ws:%Y.%m.%d}(월) ~ {we:%Y.%m.%d}(금)<br>
                <span style="font-size:11px;">업데이트 {fmt_dt(datetime.now())}</span>
            </div>
        </div>""",
        unsafe_allow_html=True
    )


def render_summary(this, last):
    def diff(a, b, rev=False):
        d = a - b
        if d == 0: return '<span class="eq">지난주와 동일</span>'
        up = (d > 0 and not rev) or (d < 0 and rev)
        return f'<span class="{"up" if up else "dn"}">{"▲" if d>0 else "▼"} {abs(d)} 지난주 대비</span>'

    nt, nl = len(this["new_week"]), len(last["new_week"])
    dt_, dl = len(this["done_week"]), len(last["done_week"])
    at = len(this["stale_14"]) + len(this["stale_7"])
    al = len(last["stale_14"]) + len(last["stale_7"])

    st.markdown(
        f"""<div class="summary-bar">
            <div class="s-chip">
                <div class="s-label">📝 이번주 새로 시작한 것</div>
                <div class="s-value">{nt}</div>
                <div class="s-diff">{diff(nt, nl)}</div>
            </div>
            <div class="s-chip">
                <div class="s-label">✅ 이번주 완료한 것</div>
                <div class="s-value">{dt_}</div>
                <div class="s-diff">{diff(dt_, dl)}</div>
            </div>
            <div class="s-chip">
                <div class="s-label">💬 같이 확인이 필요한 것</div>
                <div class="s-value">{at}</div>
                <div class="s-diff">{diff(at, al, rev=True)}</div>
            </div>
        </div>""",
        unsafe_allow_html=True
    )


def render_stale_group(rows, title, head_cls, pill_cls, show_key, limit=5):
    cnt = len(rows)
    st.markdown(
        f'<div class="group-head {head_cls}">{title} <span class="badge">{cnt}건</span></div>',
        unsafe_allow_html=True
    )
    if cnt == 0:
        st.markdown('<div class="empty" style="margin-bottom:10px;">해당 티켓 없어요 🎉</div>', unsafe_allow_html=True)
        return

    show_all = st.session_state.get(show_key, False)
    display = rows if show_all else rows[:limit]

    for row in display:
        sd = stale_days(row.get("변경일_date"))
        st.markdown(
            f"""<div class="stale-row">
                <div class="sd-pill {pill_cls}">{sd}일</div>
                <div style="flex:1;min-width:0;">
                    <div class="sr-key">{h(row.get('키'))}</div>
                    <div class="sr-summary">{h(row.get('요약'))}</div>
                    <div class="sr-meta">
                        <span>{h(row.get('담당자'))}</span>
                        <span>변경 {h(row.get('변경일'))}</span>
                        <span class="st-badge">{h(row.get('상태'))}</span>
                    </div>
                </div>
                <a href="{h(row.get('링크'))}" target="_blank"
                   style="font-size:12px;color:#2563EB;font-weight:700;white-space:nowrap;flex-shrink:0;">
                   열기 →
                </a>
            </div>""",
            unsafe_allow_html=True
        )

    if cnt > limit:
        remaining = cnt - limit
        label = "접기 ▲" if show_all else f"+ {remaining}건 더보기 ▼"
        if st.button(label, key=f"btn_{show_key}", use_container_width=True):
            st.session_state[show_key] = not show_all
            st.rerun()


def render_left(data):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="card-title">💬 같이 확인이 필요한 것들 <span class="badge">Drop 제외</span></div>',
        unsafe_allow_html=True
    )
    render_stale_group(data["stale_14"], "🔴 2주 이상 경과", "g-red", "p-red", "show_14")
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    render_stale_group(data["stale_7"], "🟠 1주 이상 경과", "g-orange", "p-orange", "show_7")
    st.markdown("</div>", unsafe_allow_html=True)

    # 티켓 상태변경/댓글
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">🎫 티켓 상태변경 / 댓글</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([3, 1])
    with c1:
        ticket_input = st.text_input("", placeholder="예: BP-123", key="ticket_input", label_visibility="collapsed")
    with c2:
        if st.button("열기", use_container_width=True):
            if ticket_input.strip():
                st.session_state.selected_ticket = ticket_input.strip().upper()
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def render_right(by_day):
    day_names = ["월", "화", "수", "목", "금"]
    total = sum(len(v) for v in by_day.values())

    st.markdown(
        f'<div class="card"><div class="card-title">📝 이번주 새로 추가된 것들 '
        f'<span class="badge">{total}건</span></div>',
        unsafe_allow_html=True
    )

    # 요일별 카드 그리드
    cols = st.columns(5)
    for i, (d, items) in enumerate(sorted(by_day.items())):
        show_key = f"day_{d.strftime('%m%d')}"
        show_all = st.session_state.get(show_key, False)
        display = items if show_all else items[:3]

        with cols[i]:
            st.markdown(
                f'<div class="day-col">'
                f'<div class="day-head">'
                f'<span class="day-name">{day_names[i]}</span>'
                f'<span class="day-date">{d:%m/%d}</span>'
                f'</div>'
                f'<div class="day-cnt">{len(items)}</div>',
                unsafe_allow_html=True
            )

            if not items:
                st.markdown('<div class="day-none">신규 없음</div>', unsafe_allow_html=True)
            else:
                for r in display:
                    st.markdown(
                        f'<div class="t-card">'
                        f'<div class="t-key">{h(r.get("키"))}</div>'
                        f'<div class="t-sum">{h(r.get("요약"))}</div>'
                        f'<div class="t-who">{h(r.get("담당자"))}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

            st.markdown("</div>", unsafe_allow_html=True)

            # 더보기 버튼
            if len(items) > 3:
                remaining = len(items) - 3
                label = "접기" if show_all else f"+{remaining}건"
                if st.button(label, key=f"daybtn_{d}", use_container_width=True):
                    st.session_state[show_key] = not show_all
                    st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 메인
# -----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="사업기획팀 주간 현황", layout="wide")
    inject_css()

    for k in ["selected_ticket", "show_14", "show_7"]:
        if k not in st.session_state:
            st.session_state[k] = None if k == "selected_ticket" else False

    if not require_config():
        st.stop()

    if st.session_state.selected_ticket:
        render_sidebar(st.session_state.selected_ticket)

    ws, we = week_range(0)
    lws, lwe = week_range(-1)

    render_header(ws, we)

    _, col_btn = st.columns([6, 1])
    with col_btn:
        if st.button("🔄 새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    try:
        with st.spinner("데이터 불러오는 중..."):
            raw = fetch_issues(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY)
            rows = [flatten(JIRA_SERVER, r) for r in raw]
            this = build(rows, ws, we)
            last = build(rows, lws, lwe)

        render_summary(this, last)

        left, right = st.columns([0.42, 0.58], gap="medium")
        with left:
            render_left(this)
        with right:
            render_right(this["by_day"])

    except requests.HTTPError as e:
        st.error(f"Jira API 오류: {e}")
    except Exception as e:
        st.error(f"오류 발생: {e}")


if __name__ == "__main__":
    main()