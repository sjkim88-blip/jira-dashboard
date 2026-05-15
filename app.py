"""
Jira Cloud 티켓 대시보드 — API 토큰 기반 최종 완성 버전
"""

from __future__ import annotations
import os
from datetime import date, datetime
from typing import Any
import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# 1. 설정 불러오기
load_dotenv()

JIRA_SERVER = (os.getenv("JIRA_SERVER") or "").rstrip("/")
JIRA_EMAIL = (os.getenv("JIRA_EMAIL") or "").strip()
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN") or ""
JIRA_PROJECT_KEY = (os.getenv("JIRA_PROJECT_KEY") or "").strip()

_DONE_NAMES = frozenset({"완료", "Done", "DONE", "Closed", "closed", "Resolved", "resolved"})
_IN_PROGRESS_NAMES = frozenset({"진행중", "진행 중", "In Progress", "in progress"})

# 2. 에러 체크 로직
def require_config():
    missing = []
    if not JIRA_SERVER: missing.append("JIRA_SERVER")
    if not JIRA_EMAIL: missing.append("JIRA_EMAIL")
    if not JIRA_API_TOKEN: missing.append("JIRA_API_TOKEN")
    if not JIRA_PROJECT_KEY: missing.append("JIRA_PROJECT_KEY")
    
    if missing:
        st.error(f".env 파일에 다음 정보가 없습니다: {', '.join(missing)}")
        return False
    return True

# 3. 데이터 가져오기 로직
@st.cache_data(ttl=120, show_spinner=True)
def fetch_all_issues_raw(server, email, token, project_key, jql_extra):
    jql = f'project = "{project_key}"'
    if jql_extra.strip():
        jql = f"({jql}) AND ({jql_extra.strip()})"
    jql += " ORDER BY updated DESC"

    auth = (email, token) # API 토큰 인증
    
    params = {
        "jql": jql,
        "maxResults": 100,
        "fields": ["summary", "status", "assignee", "updated", "created", "priority", "duedate"]
    }
    
    # 여기서 최신 Jira API 주소(url)를 정확히 지정합니다!
    url = f"{server}/rest/api/3/search/jql"
    
    r = requests.get(url, auth=auth, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("issues", [])

# --- 화면 그리는 함수들 ---

def _fmt_jira_date(s):
    return s[:10] if s else "—"

def flatten_issue(server, raw):
    fields = raw.get("fields") or {}
    status = (fields.get("status") or {}).get("name") or ""
    assignee = fields.get("assignee")
    return {
        "키": raw.get("key"),
        "요약": fields.get("summary"),
        "상태": status,
        "담당자": (assignee or {}).get("displayName") if assignee else "미지정",
        "작성일": _fmt_jira_date(fields.get("created")),
        "만기일": _fmt_jira_date(fields.get("duedate")),
        "링크": f"{server}/browse/{raw.get('key')}"
    }

def tab_bucket(status):
    if status in _DONE_NAMES: return "완료"
    if status in _IN_PROGRESS_NAMES: return "진행 중"
    return "대기 중"

def glass_kpi(css_extra, icon, label, value):
    st.markdown(f'<div style="padding:20px; border:1px solid #ddd; border-radius:10px;"><h3>{icon} {label}</h3><h2>{value:,}</h2></div>', unsafe_allow_html=True)

# 4. 메인 실행 부분
def main():
    st.set_page_config(page_title="VNTG Jira 대시보드", layout="wide")
    st.title("📊 팀 Jira 티켓 대시보드")
    
    if not require_config():
        st.stop()

    jql_extra = st.text_input("필터링 (JQL)", placeholder="예: priority = High")
    
    try:
        raw_list = fetch_all_issues_raw(JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, jql_extra)
        rows = [flatten_issue(JIRA_SERVER, r) for r in raw_list]
        
        # KPI 계산
        total = len(rows)
        done = sum(1 for r in rows if tab_bucket(r["상태"]) == "완료")
        prog = sum(1 for r in rows if tab_bucket(r["상태"]) == "진행 중")
        
        c1, c2, c3 = st.columns(3)
        with c1: glass_kpi("", "◆", "전체", total)
        with c2: glass_kpi("", "◎", "진행중", prog)
        with c3: glass_kpi("", "✓", "완료", done)
        
        st.markdown("### 티켓 목록")
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        
    except Exception as e:
        st.error(f"데이터를 가져오는데 실패했습니다: {e}")

if __name__ == "__main__":
    main()