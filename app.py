"""
사업기획팀 주간 현황 대시보드
- Jira를 열지 않아도 모든 업무 처리 가능
- 상태변경 / 담당자변경 / 우선순위변경 / 기한변경 / 댓글 / 새 티켓 생성
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

TEAM_MEMBERS = ["염필호", "황승빈", "선우예정", "최윤희", "김진우", "유대석", "김성직"]
PRIORITIES = ["Highest", "High", "Medium", "Low", "Lowest"]
ISSUE_TYPES = ["작업", "스토리", "버그"]


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
# Jira API - 읽기
# -----------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_issues(server, email, token, project_key):
    jql = f'project = "{project_key}" ORDER BY updated DESC'
    r = requests.get(
        f"{server}/rest/api/3/search/jql",
        auth=(email, token),
        params={"jql": jql, "maxResults": 200,
                "fields": ["summary","status","assignee","updated","created","duedate","priority","labels"]},
        timeout=30
    )
    r.raise_for_status()
    return r.json().get("issues", [])


@st.cache_data(ttl=30, show_spinner=False)
def fetch_detail(server, email, token, key):
    r = requests.get(
        f"{server}/rest/api/3/issue/{key}",
        auth=(email, token),
        params={"fields": "summary,status,assignee,comment,priority,duedate,reporter,labels"},
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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_project_members(server, email, token, project_key):
    """프로젝트 멤버 accountId 가져오기"""
    r = requests.get(
        f"{server}/rest/api/3/user/assignable/search",
        auth=(email, token),
        params={"project": project_key, "maxResults": 50},
        timeout=30
    )
    if r.status_code == 200:
        return {u.get("displayName"): u.get("accountId") for u in r.json()}
    return {}


# -----------------------------------------------------------------------------
# Jira API - 쓰기
# -----------------------------------------------------------------------------
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


def update_issue(server, email, token, key, fields: dict):
    """담당자, 우선순위, 만기일 등 업데이트"""
    r = requests.put(
        f"{server}/rest/api/3/issue/{key}",
        auth=(email, token),
        json={"fields": fields},
        timeout=30
    )
    return r.status_code in (200, 204)


def create_issue(server, email, token, project_key, summary, assignee_id,
                 priority, due_date, issue_type):
    """새 티켓 생성"""
    fields = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "priority": {"name": priority},
    }
    if assignee_id:
        fields["assignee"] = {"accountId": assignee_id}
    if due_date:
        fields["duedate"] = str(due_date)

    r = requests.post(
        f"{server}/rest/api/3/issue",
        auth=(email, token),
        json={"fields": fields},
        timeout=30
    )
    return r.status_code in (200, 201), r.json()


# -----------------------------------------------------------------------------
# 데이터 가공
# -----------------------------------------------------------------------------
def flatten(server, raw):
    f = raw.get("fields") or {}
    status = (f.get("status") or {}).get("name") or ""
    assignee = f.get("assignee")
    priority = (f.get("priority") or {}).get("name") or ""
    cdt = parse_dt(f.get("created"))
    udt = parse_dt(f.get("updated"))
    return {
        "키": raw.get("key") or "",
        "요약": f.get("summary") or "",
        "상태": status,
        "담당자": (assignee or {}).get("displayName") if assignee else "미지정",
        "우선순위": priority,
        "작성일_date": cdt.date() if cdt else None,
        "변경일_date": udt.date() if udt else None,
        "변경일": fmt_date(udt),
        "만기일": fmt_date(f.get("duedate")),
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
    .block-container { padding-top: 3.5rem; padding-bottom: 3rem; max-width: 1600px; }

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

    .detail-section { background: #F8FAFC; border-radius: 10px; padding: 12px; margin-bottom: 10px; border: 1px solid #E2E8F0; }
    .detail-label { font-size: 11px; font-weight: 700; color: #64748B; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.05em; }
    .detail-value { font-size: 13px; font-weight: 600; color: #0F172A; }
    </style>
    """, unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 사이드바 - 티켓 상세 + 전체 수정
# -----------------------------------------------------------------------------
def render_sidebar(key: str):
    with st.sidebar:
        st.markdown(f"### 🎫 {key}")

        try:
            detail = fetch_detail(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key)
            f = detail.get("fields") or {}
            summary = f.get("summary") or ""
            status = (f.get("status") or {}).get("name") or ""
            assignee_info = f.get("assignee")
            current_assignee = (assignee_info or {}).get("displayName") if assignee_info else "미지정"
            current_priority = (f.get("priority") or {}).get("name") or "Medium"
            current_due = f.get("duedate") or ""

            # 티켓 기본 정보
            st.markdown(f"**{summary}**")
            st.markdown(
                f'<div class="detail-section">'
                f'<div class="detail-label">현재 상태</div>'
                f'<div class="detail-value"><span class="st-badge">{h(status)}</span></div>'
                f'</div>',
                unsafe_allow_html=True
            )
            st.markdown(f"🔗 [Jira에서 열기]({JIRA_SERVER}/browse/{key})")
            st.markdown("---")

            # ── 탭으로 기능 분리 ──
            tab1, tab2, tab3 = st.tabs(["✏️ 수정", "💬 댓글", "📋 활동"])

            with tab1:
                st.markdown("**상태 변경**")
                transitions = fetch_transitions(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key)
                if transitions:
                    t_map = {t["name"]: t["id"] for t in transitions}
                    sel_status = st.selectbox("변경할 상태", list(t_map.keys()), key=f"t_{key}")
                    if st.button("상태 적용", key=f"apply_status_{key}", use_container_width=True):
                        if do_transition(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key, t_map[sel_status]):
                            st.success(f"✅ '{sel_status}' 으로 변경!")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error("변경 실패")

                st.markdown("---")
                st.markdown("**담당자 변경**")
                members = fetch_project_members(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY)

                # 팀 멤버 목록과 API 멤버 매칭
                member_options = TEAM_MEMBERS.copy()
                default_idx = member_options.index(current_assignee) if current_assignee in member_options else 0
                sel_assignee = st.selectbox("담당자", member_options, index=default_idx, key=f"a_{key}")

                if st.button("담당자 적용", key=f"apply_assignee_{key}", use_container_width=True):
                    account_id = members.get(sel_assignee)
                    if account_id:
                        if update_issue(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key,
                                       {"assignee": {"accountId": account_id}}):
                            st.success(f"✅ {sel_assignee}으로 변경!")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error("변경 실패")
                    else:
                        st.warning("해당 멤버의 계정 정보를 찾을 수 없어요.")

                st.markdown("---")
                st.markdown("**우선순위 변경**")
                priority_idx = PRIORITIES.index(current_priority) if current_priority in PRIORITIES else 2
                sel_priority = st.selectbox("우선순위", PRIORITIES, index=priority_idx, key=f"p_{key}")
                if st.button("우선순위 적용", key=f"apply_priority_{key}", use_container_width=True):
                    if update_issue(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key,
                                   {"priority": {"name": sel_priority}}):
                        st.success(f"✅ {sel_priority}으로 변경!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("변경 실패")

                st.markdown("---")
                st.markdown("**기한(만기일) 변경**")
                current_due_date = None
                if current_due:
                    try:
                        current_due_date = datetime.strptime(current_due[:10], "%Y-%m-%d").date()
                    except Exception:
                        pass
                sel_due = st.date_input("만기일", value=current_due_date, key=f"d_{key}")
                if st.button("만기일 적용", key=f"apply_due_{key}", use_container_width=True):
                    if update_issue(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key,
                                   {"duedate": str(sel_due)}):
                        st.success(f"✅ {sel_due} 으로 변경!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("변경 실패")

            with tab2:
                st.markdown("**댓글 작성**")
                comment_text = st.text_area("내용", placeholder="댓글을 입력하세요...",
                                            key=f"c_{key}", height=100, label_visibility="collapsed")
                if st.button("💬 등록", key=f"post_{key}", use_container_width=True):
                    if comment_text.strip():
                        if post_comment(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, key, comment_text.strip()):
                            st.success("✅ 댓글 등록됐어요!")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error("등록 실패")
                    else:
                        st.warning("내용을 입력해주세요.")

            with tab3:
                st.markdown("**댓글 목록**")
                comments = (f.get("comment") or {}).get("comments") or []
                if comments:
                    for c in reversed(comments[-10:]):
                        author = (c.get("author") or {}).get("displayName") or "알 수 없음"
                        created = parse_dt(c.get("created"))
                        body_text = ""
                        for block in ((c.get("body") or {}).get("content") or []):
                            for inline in (block.get("content") or []):
                                if inline.get("type") == "text":
                                    body_text += inline.get("text", "")
                        st.markdown(
                            f'<div class="c-box">'
                            f'<span class="c-author">{h(author)}</span>'
                            f'<span class="c-date">{fmt_dt(created)}</span>'
                            f'<div class="c-text">{h(body_text)}</div>'
                            f'</div>',
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
# 새 티켓 생성 모달
# -----------------------------------------------------------------------------
def render_create_ticket():
    with st.expander("➕ 새 티켓 생성", expanded=st.session_state.get("create_open", False)):
        st.markdown("### 새 티켓 만들기")
        members = fetch_project_members(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY)

        c1, c2 = st.columns(2)
        with c1:
            new_summary = st.text_input("제목 *", placeholder="티켓 제목을 입력하세요", key="new_summary")
            new_assignee = st.selectbox("담당자", TEAM_MEMBERS, key="new_assignee")
            new_type = st.selectbox("이슈 타입", ISSUE_TYPES, key="new_type")
        with c2:
            new_priority = st.selectbox("우선순위", PRIORITIES, index=2, key="new_priority")
            new_due = st.date_input("기한(만기일)", value=None, key="new_due")

        if st.button("🚀 티켓 생성", use_container_width=True, key="create_btn"):
            if not new_summary.strip():
                st.warning("제목을 입력해주세요.")
            else:
                account_id = members.get(new_assignee)
                success, resp = create_issue(
                    JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY,
                    new_summary.strip(), account_id, new_priority, new_due, new_type
                )
                if success:
                    new_key = resp.get("key", "")
                    st.success(f"✅ 티켓 생성 완료! [{new_key}]({JIRA_SERVER}/browse/{new_key})")
                    st.cache_data.clear()
                else:
                    st.error(f"생성 실패: {resp}")


# -----------------------------------------------------------------------------
# 렌더링
# -----------------------------------------------------------------------------
def render_header(ws, we):
    col_logo, col_title, col_date = st.columns([0.06, 0.7, 0.24])
    with col_logo:
        st.image("assets/logo.jpeg", width=48)
    with col_title:
        st.markdown(
            f"""<div style="display:flex;align-items:center;height:56px;gap:12px;">
                <h1 style="font-size:22px;font-weight:800;color:#0F172A;letter-spacing:-0.03em;margin:0;">
                    사업기획팀 주간 현황
                </h1>
                <span style="font-size:12px;color:#0057FF;background:#EEF4FF;border:1px solid #BFDBFE;
                    border-radius:999px;padding:4px 10px;font-weight:700;">
                    Jira 연동
                </span>
            </div>
            <div style="font-size:13px;color:#64748B;">
                Jira를 열지 않아도 모든 업무를 처리할 수 있어요
            </div>""",
            unsafe_allow_html=True
        )
    with col_date:
        st.markdown(
            f"""<div style="height:56px;display:flex;align-items:center;justify-content:flex-end;">
                <div style="text-align:right;font-size:13px;color:#94A3B8;">
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
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
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
                </div>""",
                unsafe_allow_html=True
            )
        with col2:
            if st.button("열기", key=f"open_{row.get('키')}_{show_key}", use_container_width=True):
                st.session_state.selected_ticket = row.get("키")
                st.rerun()

    if cnt > limit:
        label = "접기 ▲" if show_all else f"+ {cnt - limit}건 더보기 ▼"
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

    # 티켓 직접 검색
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">🔍 티켓 검색 & 수정</div>', unsafe_allow_html=True)
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

    for k in ["selected_ticket", "show_14", "show_7", "create_open"]:
        if k not in st.session_state:
            st.session_state[k] = None if k == "selected_ticket" else False

    if not require_config():
        st.stop()

    if st.session_state.selected_ticket:
        render_sidebar(st.session_state.selected_ticket)

    ws, we = week_range(0)
    lws, lwe = week_range(-1)

    render_header(ws, we)

    # 상단 버튼
    b1, b2, b3 = st.columns([4, 1, 1])
    with b2:
        if st.button("➕ 새 티켓", use_container_width=True):
            st.session_state.create_open = not st.session_state.get("create_open", False)
            st.rerun()
    with b3:
        if st.button("🔄 새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # 새 티켓 생성
    if st.session_state.get("create_open", False):
        render_create_ticket()

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