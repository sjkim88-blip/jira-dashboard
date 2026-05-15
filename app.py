"""
Jira Cloud — 주간 업무 현황 대시보드
"""
from __future__ import annotations
import os
from datetime import date, datetime, timedelta
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

JIRA_SERVER = (os.getenv("JIRA_SERVER") or "").rstrip("/")
JIRA_EMAIL  = (os.getenv("JIRA_EMAIL")  or "").strip()
JIRA_TOKEN  = (os.getenv("JIRA_API_TOKEN") or "")
JIRA_KEY    = (os.getenv("JIRA_PROJECT_KEY") or "").strip()

_DONE     = frozenset({"완료", "Done", "DONE", "Closed", "closed", "Resolved", "resolved"})
_KOR_DAYS = ["월요일", "화요일", "수요일", "목요일", "금요일"]

# ─── helpers ──────────────────────────────────────────────────────────────

def week_bounds(ref: date) -> tuple[date, date]:
    mon = ref - timedelta(days=ref.weekday())
    return mon, mon + timedelta(days=4)

def parse_d(s) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:19]).date()
    except Exception:
        return None

# ─── Jira API ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner=False)
def fetch(server: str, email: str, token: str, key: str, extra_jql: str = "") -> list:
    jql = f'project = "{key}"'
    if extra_jql.strip():
        jql = f"({jql}) AND ({extra_jql.strip()})"
    jql += " ORDER BY updated DESC"
    resp = requests.get(
        f"{server}/rest/api/3/search/jql",
        auth=(email, token),
        params={
            "jql": jql,
            "maxResults": 500,
            "fields": ["summary", "status", "assignee", "updated", "created"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("issues", [])

def to_rows(server: str, raw_list: list) -> list[dict]:
    out = []
    for raw in raw_list:
        f   = raw.get("fields") or {}
        stn = (f.get("status") or {}).get("name") or ""
        asn = f.get("assignee")
        out.append(dict(
            key      = raw["key"],
            summary  = f.get("summary", ""),
            status   = stn,
            assignee = asn["displayName"] if asn else "미지정",
            created  = parse_d(f.get("created")),
            updated  = parse_d(f.get("updated")),
            link     = f"{server}/browse/{raw['key']}",
            done     = stn in _DONE,
        ))
    return out

# ─── CSS ──────────────────────────────────────────────────────────────────

def inject_css():
    st.markdown("""
<style>
/* global */
[data-testid="stAppViewContainer"] { background: #f0f2f6; }
[data-testid="stHeader"]           { display: none; }
.block-container { padding-top: 1.4rem !important; padding-bottom: 2rem !important; }
div[data-testid="column"] { padding: 0 6px; }

/* header */
.hdr-title {
    font-size: 1.35rem; font-weight: 800; color: #1a1a2e;
    display: flex; align-items: center; gap: 10px;
}
.hdr-badge {
    font-size: .72rem; background: #e3f2fd; color: #1565c0;
    border-radius: 5px; padding: 2px 9px; font-weight: 600;
}
.hdr-meta { font-size: .78rem; color: #888; text-align: right; margin-top: 6px; }

/* filter bar */
.filter-bar {
    background: #fff; border-radius: 10px; padding: 10px 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,.07); margin-bottom: 18px;
    display: flex; align-items: center; gap: 12px;
}

/* KPI */
.kpi-card {
    background: #fff; border-radius: 12px; padding: 18px 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,.07);
}
.kpi-icon  { font-size: 1.7rem; margin-bottom: 4px; }
.kpi-label { font-size: .72rem; color: #888; font-weight: 700;
             text-transform: uppercase; letter-spacing: .04em; }
.kpi-value { font-size: 2.3rem; font-weight: 900; color: #1a1a2e; line-height: 1.1; }
.kpi-delta { font-size: .78rem; margin-top: 4px; }

/* section title */
.sec-title {
    font-size: .95rem; font-weight: 800; color: #1a1a2e;
    margin: 0 0 10px; border-left: 3px solid #1976d2; padding-left: 8px;
}

/* weekly flow */
.day-col {
    background: #fff; border-radius: 10px; padding: 12px 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06); min-height: 220px;
}
.day-header { font-weight: 800; font-size: .85rem; color: #1a1a2e; }
.day-date   { font-size: .75rem; color: #888; margin-bottom: 6px; }
.day-badges { display: flex; gap: 6px; margin-bottom: 10px; }
.badge-new  {
    font-size: .68rem; background: #e3f2fd; color: #1565c0;
    border-radius: 4px; padding: 1px 7px; font-weight: 600;
}
.badge-chg  {
    font-size: .68rem; background: #e8f5e9; color: #2e7d32;
    border-radius: 4px; padding: 1px 7px; font-weight: 600;
}

/* ticket card */
.tcard {
    border-left: 3px solid #ccc; background: #fafafa;
    border-radius: 0 6px 6px 0; padding: 7px 9px;
    margin-bottom: 7px; font-size: .76rem; line-height: 1.4;
}
.tcard.is-new     { border-color: #1976d2; }
.tcard.is-changed { border-color: #388e3c; }
.tcard-key { font-weight: 700; color: #1a1a2e; font-size: .77rem; }
.tcard-sum { color: #555; margin: 2px 0;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tcard-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 3px; }
.st-badge   {
    font-size: .62rem; background: #ede7f6; color: #512da8;
    border-radius: 4px; padding: 1px 5px;
}
.st-badge.new-tag {
    background: #e3f2fd; color: #1565c0;
}
.assignee   { font-size: .65rem; color: #999; }
.more-link  { font-size: .73rem; color: #1976d2; margin-top: 5px; cursor: pointer; }

/* alert panel */
.alert-panel {
    background: #fff; border-radius: 10px; padding: 14px 14px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
}
.alert-group-title { font-size: .83rem; font-weight: 800; margin: 12px 0 8px; }
.alert-card {
    border-radius: 7px; padding: 9px 11px; margin-bottom: 7px;
    font-size: .77rem; line-height: 1.45;
    display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
}
.alert-card.red    { background: #fff5f5; border: 1px solid #ffcdd2; }
.alert-card.orange { background: #fff8f0; border: 1px solid #ffe0b2; }
.alert-key  { font-weight: 700; color: #1a1a2e; }
.alert-sub  { color: #777; font-size: .7rem; margin-top: 2px; }
.alert-days-red    { color: #c62828; font-weight: 800; font-size: .8rem;
                     text-align: right; white-space: nowrap; }
.alert-days-orange { color: #e65100; font-weight: 800; font-size: .8rem;
                     text-align: right; white-space: nowrap; }

/* full ticket table */
.table-wrap {
    background: #fff; border-radius: 10px; padding: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06); margin-top: 18px;
}
</style>
""", unsafe_allow_html=True)

# ─── render helpers ───────────────────────────────────────────────────────

def render_kpi(icon: str, label: str, value: int, delta: int, higher_is_bad: bool = False):
    if delta > 0:
        sign = "▲"
        color = "#f44336" if higher_is_bad else "#4caf50"
    elif delta < 0:
        sign = "▼"
        color = "#4caf50" if higher_is_bad else "#f44336"
    else:
        sign, color = "—", "#aaa"

    delta_html = (
        f'<div class="kpi-delta" style="color:{color}">'
        f'지난 주 대비 {sign} {abs(delta)}</div>'
        if delta != 0 else
        '<div class="kpi-delta" style="color:#aaa">지난 주 대비 동일</div>'
    )
    st.markdown(f"""
<div class="kpi-card">
  <div class="kpi-icon">{icon}</div>
  <div class="kpi-label">{label}</div>
  <div class="kpi-value">{value:,}</div>
  {delta_html}
</div>""", unsafe_allow_html=True)


def ticket_card_html(r: dict, card_type: str = "is-new") -> str:
    key  = r["key"]
    sm   = (r["summary"] or "")[:32]
    tag  = '<span class="st-badge new-tag">신규</span>' if card_type == "is-new" \
           else f'<span class="st-badge">{r["status"]}</span>'
    return f"""
<div class="tcard {card_type}">
  <div class="tcard-key">{key}</div>
  <div class="tcard-sum">{sm}</div>
  <div class="tcard-foot">{tag}<span class="assignee">{r['assignee']}</span></div>
</div>"""


def alert_card_html(r: dict, days: int, style: str = "red") -> str:
    sm = (r["summary"] or "")[:32]
    return f"""
<div class="alert-card {style}">
  <div>
    <div class="alert-key">{r['key']}</div>
    <div class="tcard-sum">{sm}</div>
    <div class="alert-sub">담당자 {r['assignee']} | 마지막 변경 {r['updated']}</div>
  </div>
  <div class="alert-days-{style}">{days}일<br>정체</div>
</div>"""

# ─── main ─────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="주간 업무 현황", layout="wide", page_icon="📊")
    inject_css()

    today    = date.today()
    mon, fri = week_bounds(today)
    prev_mon, prev_fri = week_bounds(mon - timedelta(days=1))

    # ── 헤더 ──────────────────────────────────────────────────────────────
    h_l, h_r = st.columns([5, 2])
    with h_l:
        st.markdown(
            '<div class="hdr-title">📊 [사업기획] 주간 업무 현황'
            '<span class="hdr-badge">Jira API 연동</span></div>',
            unsafe_allow_html=True,
        )
    with h_r:
        rc, bc = st.columns([4, 1])
        rc.markdown(
            f'<div class="hdr-meta">마지막 업데이트 : {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>',
            unsafe_allow_html=True,
        )
        if bc.button("↺", help="새로고침"):
            st.cache_data.clear()
            st.rerun()

    # ── 필터 ──────────────────────────────────────────────────────────────
    f1, f2, f3, _ = st.columns([2.5, 2, 1.3, 3])
    with f1:
        dr = st.date_input("기간", [mon, fri], label_visibility="collapsed")
    with f2:
        st.selectbox("팀", ["사업기획팀", "개발팀", "디자인팀", "전체"], label_visibility="collapsed")
    with f3:
        if st.button("필터 초기화", use_container_width=True):
            st.rerun()

    start = dr[0] if isinstance(dr, (list, tuple)) and len(dr) > 0 else mon
    end   = dr[1] if isinstance(dr, (list, tuple)) and len(dr) > 1 else fri

    # ── 설정 확인 ──────────────────────────────────────────────────────────
    missing = [k for k, v in [
        ("JIRA_SERVER", JIRA_SERVER), ("JIRA_EMAIL", JIRA_EMAIL),
        ("JIRA_API_TOKEN", JIRA_TOKEN), ("JIRA_PROJECT_KEY", JIRA_KEY),
    ] if not v]
    if missing:
        st.error(f".env 파일에 다음 정보가 없습니다: {', '.join(missing)}")
        st.stop()

    # ── 데이터 fetch ───────────────────────────────────────────────────────
    with st.spinner("Jira 데이터 불러오는 중..."):
        try:
            rows = to_rows(JIRA_SERVER, fetch(JIRA_SERVER, JIRA_EMAIL, JIRA_TOKEN, JIRA_KEY))
        except Exception as e:
            st.error(f"데이터 가져오기 실패: {e}")
            st.stop()

    # ── KPI 계산 ───────────────────────────────────────────────────────────
    def in_range(r, s, e, field): return r[field] and s <= r[field] <= e

    new_this  = [r for r in rows if in_range(r, start, end, "created")]
    chg_this  = [r for r in rows if in_range(r, start, end, "updated")
                 and not in_range(r, start, end, "created")]
    new_prev  = [r for r in rows if in_range(r, prev_mon, prev_fri, "created")]
    chg_prev  = [r for r in rows if in_range(r, prev_mon, prev_fri, "updated")
                 and not in_range(r, prev_mon, prev_fri, "created")]

    stag_all = sorted(
        [r for r in rows if not r["done"] and r["updated"]
         and (today - r["updated"]).days >= 7],
        key=lambda x: (today - x["updated"]).days, reverse=True,
    )
    stag2w      = [r for r in stag_all if (today - r["updated"]).days >= 14]
    stag1w_only = [r for r in stag_all if (today - r["updated"]).days < 14]

    # ── KPI 카드 ───────────────────────────────────────────────────────────
    kc = st.columns(4)
    for col, icon, label, val, delta, bad in [
        (kc[0], "📋", "신규 발생 (이번 주)", len(new_this),  len(new_this)  - len(new_prev),  False),
        (kc[1], "🔄", "상태 변경 (이번 주)", len(chg_this),  len(chg_this)  - len(chg_prev),  False),
        (kc[2], "⏳", "1주 이상 정체",       len(stag_all),  0,                               True),
        (kc[3], "⏰", "2주 이상 정체",       len(stag2w),    0,                               True),
    ]:
        with col:
            render_kpi(icon, label, val, delta, bad)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # ── 주간 흐름 + 알림 ───────────────────────────────────────────────────
    flow_col, alert_col = st.columns([7, 3])

    with flow_col:
        st.markdown('<p class="sec-title">주간 티켓 흐름 (신규 발생 &amp; 상태 변경)</p>',
                    unsafe_allow_html=True)
        day_cols = st.columns(5)
        for i in range(5):
            day     = start + timedelta(days=i)
            day_new = [r for r in rows if r["created"] == day]
            day_chg = [r for r in rows if r["updated"] == day and r["created"] != day]
            with day_cols[i]:
                html = (
                    f'<div class="day-col">'
                    f'<div class="day-header">{_KOR_DAYS[i]}</div>'
                    f'<div class="day-date">{day.strftime("%m-%d")}</div>'
                    f'<div class="day-badges">'
                    f'<span class="badge-new">신규 {len(day_new)}</span>'
                    f'<span class="badge-chg">변경 {len(day_chg)}</span>'
                    f'</div>'
                )
                for r in day_new[:4]:
                    html += ticket_card_html(r, "is-new")
                for r in day_chg[:4]:
                    html += ticket_card_html(r, "is-changed")
                total = len(day_new) + len(day_chg)
                if total > 8:
                    html += f'<div class="more-link">+ {total - 8}건 더 보기</div>'
                html += "</div>"
                st.markdown(html, unsafe_allow_html=True)

    with alert_col:
        st.markdown('<p class="sec-title">검토 지연 알림</p>', unsafe_allow_html=True)
        html_a = '<div class="alert-panel">'
        if stag2w:
            html_a += (
                f'<div class="alert-group-title" style="color:#c62828">'
                f'2주 이상 정체 ({len(stag2w)})</div>'
            )
            for r in stag2w[:3]:
                html_a += alert_card_html(r, (today - r["updated"]).days, "red")
        if stag1w_only:
            html_a += (
                f'<div class="alert-group-title" style="color:#e65100">'
                f'1주 이상 정체 ({len(stag1w_only)})</div>'
            )
            for r in stag1w_only[:5]:
                html_a += alert_card_html(r, (today - r["updated"]).days, "orange")
        if not stag2w and not stag1w_only:
            html_a += '<div style="color:#888;font-size:.82rem;padding:12px 0">정체 티켓이 없습니다 ✓</div>'
        rest = len(stag_all) - 8
        if rest > 0:
            html_a += f'<div class="more-link" style="text-align:right;margin-top:6px">+ {rest}건 더 보기 ›</div>'
        html_a += "</div>"
        st.markdown(html_a, unsafe_allow_html=True)

    # ── 전체 티켓 리스트 ───────────────────────────────────────────────────
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    st.markdown('<p class="sec-title">전체 티켓 리스트</p>', unsafe_allow_html=True)

    df = pd.DataFrame([{
        "키":           r["key"],
        "요약":         r["summary"],
        "상태":         r["status"],
        "담당자":       r["assignee"],
        "생성일":       str(r["created"])  if r["created"]  else "—",
        "마지막 변경일": str(r["updated"]) if r["updated"]  else "—",
        "링크":         r["link"],
    } for r in rows])

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")},
    )


if __name__ == "__main__":
    main()
