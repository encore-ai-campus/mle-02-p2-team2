"""학교 감염병 대응 Q&A — Streamlit 화면 (주승우, 11/07 M5).

실행
- 로컬:  uv run --with streamlit streamlit run src/rag/ui/app.py   (루트 .env 사용)
- Streamlit Community Cloud: Main file path = src/rag/ui/app.py, Python 3.12.
  의존성은 같은 폴더의 requirements.txt, 자격 증명은 앱 Settings → Secrets 최상위 키(secrets.toml.example 참고).

화면은 QueryOutput과 실행 기록만 보여준다. 답변 내용을 화면에서 새로 만들지 않는다.
"""

from __future__ import annotations

import html
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:            # 클라우드는 이 파일의 폴더만 sys.path에 넣는다
    sys.path.insert(0, str(SRC))

import pandas as pd

from rag.ui import history, service, sources

st.set_page_config(page_title="학교 감염병 대응 길잡이", page_icon="🏫", layout="wide")

# 화면에서 지역을 고르지 않는다(11/07). 참고 문서 범위(allowed_document_ids)가 근거 범위를 정하므로 지역은 한 값으로 고정한다.
DEFAULT_REGION = "GYEONGGI"
STEP_LABELS = {"normalize_input": "입력 정리", "classify_scope": "질문 범위 판단", "classify_intent": "질문 의도 분류",
               "select_tool": "검색 도구 선택", "execute_tool": "검색 도구 실행", "merge_and_validate": "근거 병합·DB 대조",
               "evaluate_evidence": "근거 충분성 판단", "check_facts": "판단 기준 확인(신고 대상·단계·조문)",
               "select_evidence": "필요한 근거 선별(재순위)",
               "compose_answer": "답변 작성"}
# 처리 중 진행 표시(11/07): 방금 끝난 단계 → 지금 하는 일.
PROGRESS = {"classify_scope": "질문을 이해했습니다. 검색 도구를 고르는 중…",
            "select_tool": "공식 자료에서 근거를 찾는 중…",
            "execute_tool": "찾은 근거를 확인하는 중…",
            "evaluate_evidence": "신고 대상·대응 단계 같은 판단 기준을 확인하는 중…",
            "check_facts": "질문에 꼭 필요한 근거를 고르는 중…",
            "select_evidence": "답변을 작성하는 중…"}
COMPOSE_MODES = {"llm": "다듬은 답변(검사 통과)", "template": "원문 정리형", "template_fallback": "원문 정리형(다듬은 답변이 검사에 걸림)"}
STATUS_STYLE = {"ANSWERED": ("✅", "green"), "PARTIAL": ("🟡", "orange"), "INSUFFICIENT_EVIDENCE": ("⚪", "gray"),
                "ERROR": ("⛔", "red")}


# ---------------------------------------------------------------- 자원·접근

@st.cache_resource(show_spinner="지식 그래프(Neo4j)에 연결하는 중…")
def resources() -> dict:
    return service.build_resources()


def gate() -> bool:
    """APP_PASSWORD(Secrets)가 설정돼 있으면 비밀번호를 받는다. 비어 있으면 열어 둔다."""
    password = os.getenv("APP_PASSWORD")
    if not password or st.session_state.get("authed"):
        return True
    with st.form("gate"):
        typed = st.text_input("접속 비밀번호", type="password")
        if st.form_submit_button("들어가기"):
            st.session_state.authed = typed == password
            if not st.session_state.authed:
                st.error("비밀번호가 맞지 않습니다.")
            st.rerun()
    return False


# ---------------------------------------------------------------- 표시 도우미

def answer_markdown(text: str) -> str:
    """compose_text의 구역 제목(■)을 굵은 제목으로 바꾸고 줄바꿈을 보존한다. 문구는 바꾸지 않는다."""
    lines = []
    for line in text.splitlines():
        if line.startswith("■ "):
            lines.append(f"\n**{line[2:]}**\n")
        elif line.startswith("- "):
            lines.append(line)
        else:
            lines.append(line + "  ")
    return "\n".join(lines)


def doc_label(documents: dict, document_id: str) -> str:
    meta = documents.get(document_id) or {}
    return meta.get("title") or document_id


def show_citations(output: dict, documents: dict) -> None:
    if not output["citations"]:
        st.caption("표시할 출처가 없습니다.")
        return
    for number, citation in enumerate(output["citations"], 1):
        meta = documents.get(citation["document_id"]) or {}
        page = f"PDF {citation['pdf_page']}쪽"
        if citation.get("printed_page"):
            page += f" (인쇄 {citation['printed_page']}쪽)"
        head = f"[{number}] {meta.get('title') or citation['document_id']} · {page}"
        with st.expander(head):
            st.markdown(f"**발행** {meta.get('issuer') or '확인 필요'} · **판본** {meta.get('version') or '확인 필요'}  \n"
                        f"`{citation['chunk_id']}`")
            st.text(citation["excerpt"])


def show_flow(result: dict) -> None:
    output = result["output"]
    cols = st.columns(4)
    cols[0].metric("응답 유형", service.RESPONSE_LABELS.get(output["response_type"], output["response_type"]))
    cols[1].metric("도구 호출", f"{output['search_rounds']}회")
    cols[2].metric("근거 Finding", len(output["findings"]))
    cols[3].metric("처리 시간", f"{result['seconds']}초")
    for step in result["trace"]:
        name = STEP_LABELS.get(step["step"], step["step"])
        info = {k: v for k, v in step.items() if k not in ("step", "request_id")}
        if step["step"] == "select_tool":
            tool = info.get("tool")
            detail = f"{service.TOOL_LABELS.get(tool, tool)} — {info.get('reason')} (라운드 {info.get('round')})"
            if not info.get("registered"):
                detail += " · 연결 안 됨"
        elif step["step"] == "execute_tool":
            detail = (f"{info.get('tool')} → {info.get('status')} · Finding {info.get('findings')} · "
                      f"인용 {info.get('citations')} · 경로 {info.get('graph_paths')} · {info.get('seconds')}초")
            if info.get("error"):
                detail += f" · 오류: {info['error']}"
            if info.get("sub_question"):
                detail += f" · 질문: {info['sub_question']}"
        elif step["step"] == "evaluate_evidence":
            detail = f"{info.get('decision')} (상태 {info.get('status')}, 미해결 {info.get('unresolved')})"
            if info.get("next_tool"):
                detail += f" → 다음 도구 {info['next_tool']}"
        elif step["step"] == "check_facts":
            if info.get("mode") == "db":
                found = info.get("found") or {}
                detail = "확인 항목 " + ", ".join(f"{k} {len(v)}건" for k, v in found.items()) + f" · {info.get('seconds')}초"
                for fact in info.get("facts") or []:
                    detail += f"\n  - {fact}"
            else:
                detail = f"확인 항목 없음 ({info.get('mode')}) {info.get('error') or ''}"
        elif step["step"] == "select_evidence":
            if info.get("mode") == "llm":
                detail = (f"근거 카드 {info.get('cards')}개 → 인용 {info.get('after', ('', ''))[1]}개 "
                          f"(상태 {info.get('status')}) · {info.get('seconds')}초")
                for cov in info.get("coverage") or []:
                    detail += f"\n  - 하위 질문 {cov['part']}: {cov['status']}" + (
                        f" — {cov['missing']}" if cov.get("missing") else "")
            else:
                detail = f"재순위 안 함 ({info.get('mode')}) {info.get('reason') or info.get('error') or ''}"
        elif step["step"] == "compose_answer":
            detail = COMPOSE_MODES.get(info.get("mode"), info.get("mode"))
            if info.get("seconds") is not None:
                detail += f" · {info['seconds']}초"
            if info.get("attempts") == 2:
                detail += " · 첫 줄 검사 실패로 1회 다시 씀"
            if info.get("dropped") and info.get("mode") == "llm":
                detail += f" · 검사에 걸려 뺀 줄 {len(info['dropped'])}개"
            if info.get("problems"):
                detail += " · 검사: " + " / ".join(info["problems"][:3])
        elif step["step"] == "classify_scope":
            mode = {"llm": "LLM 분류", "rule": "규칙 분류", "rule_fallback": "LLM 실패 → 규칙 분류"}.get(
                info.get("mode"), info.get("mode"))
            detail = f"{mode} · {info.get('response_type')} {('— ' + info['reason']) if info.get('reason') else ''}"
            if info.get("error"):
                detail += f" · 오류: {info['error']}"
            for tool, sub in (info.get("sub_questions") or {}).items():
                detail += f"\n  - 하위 질문({tool}): {sub}"
        elif step["step"] == "classify_intent":
            detail = f"주 의도 {info.get('intent')}" + (f", 보조 {info['secondary']}" if info.get("secondary") else "")
        else:
            detail = ", ".join(f"{k}={v}" for k, v in info.items())
        st.markdown(f"- **{name}** · {detail}")


def _node(ref: str) -> str:
    label, _, key = ref.partition(":")
    return f"{label}\\n{key[-28:]}" if key else label


def show_paths(result: dict) -> None:
    paths = [(r["tool_name"], p) for r in result["tool_results"] for p in r["graph_paths"]]
    if not paths:
        st.caption("이번 답변에는 그래프 관계 경로가 없습니다.")
        return
    st.caption(f"도구가 Neo4j에서 실제로 조회한 관계 {len(paths)}개(저장 방향 그대로). 처음 12개를 그립니다.")
    edges = []
    for _, path in paths[:12]:
        for edge in path["edges"]:
            edges.append((edge["source_ref"], edge["relationship_type"], edge["target_ref"]))
    edges = list(dict.fromkeys(edges))
    dot = ["digraph G {", 'rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef3fb", fontsize=10];',
           "edge [fontsize=9];"]
    for source, rel, target in edges:
        dot.append(f'"{html.escape(_node(source))}" -> "{html.escape(_node(target))}" [label="{rel}"];')
    dot.append("}")
    st.graphviz_chart("\n".join(dot), width="stretch")
    with st.expander("경로 원본"):
        for tool, path in paths:
            st.text(f"[{tool}] " + "  ".join(f"({e['source_ref']})-[{e['relationship_type']}]->({e['target_ref']})"
                                             for e in path["edges"]))


NO_EVIDENCE_NOTICE = "제공된 공식 자료에서 확인하지 못했습니다. 공식 문서 원문을 직접 확인해 주세요."
PARTIAL_NOTICE = "일부만 확인한 답변입니다. 근거 문서와 '확인이 필요한 부분'을 함께 확인하세요."


@st.cache_data
def source_urls() -> dict:
    """manifest 의 공식 URL(http 주소가 있는 문서만). 한 번 읽어 둔다."""
    return sources.load_urls()


def show_user_notes(output: dict, documents: dict) -> None:
    """모든 사용자에게 보이는 근거 문서(문서명·판본·PDF 쪽수)와 확인이 필요한 부분. 발췌문은 보이지 않는다."""
    if output.get("response_type") != "DOMAIN":
        return
    lines = sources.source_lines(output.get("citations", []), documents, source_urls())
    if lines:
        st.markdown("**근거 문서**")
        for line in lines:
            st.markdown("- " + line)
    else:
        st.caption("표시할 출처가 없습니다.")
    if output["status"] == "INSUFFICIENT_EVIDENCE":
        st.info(NO_EVIDENCE_NOTICE)
    elif output["status"] == "PARTIAL":
        st.caption(PARTIAL_NOTICE)
    notes = sources.user_warnings(output.get("warnings", []))
    if notes:
        with st.expander(f"확인이 필요한 부분 ({len(notes)})"):
            for note in notes:
                st.markdown("- " + note)


def show_result(result: dict, documents: dict, developer: bool = False) -> None:
    """답변을 보여 준다. 일반 사용자에게는 상태와 최종 답변만, 개발자에게는 점검용 정보(출처·처리 흐름·경로·주의)도 보여 준다."""
    output = result["output"]
    icon, color = STATUS_STYLE.get(output["status"], ("•", "gray"))
    status_label = service.STATUS_LABELS.get(output["status"], output["status"])
    if developer:
        tools = ", ".join(service.TOOL_LABELS.get(t, t).split(" (")[0] for t in output["tools_used"]) or "도구 호출 없음"
        st.markdown(f"{icon} :{color}[**{status_label}**] · {tools}")
    else:
        st.markdown(f"{icon} :{color}[**{status_label}**]")
    compose = next((t for t in result["trace"] if t["step"] == "compose_answer"), {})
    if compose.get("mode") == "llm":
        st.markdown(output["answer"])
        if developer:
            with st.expander("원문 정리형 답변 보기 (출처 쪽수 포함)"):
                st.markdown(answer_markdown(compose["template"]))
    else:
        st.markdown(answer_markdown(output["answer"]))
        if developer and compose.get("mode") == "template_fallback":
            st.caption("다듬은 답변이 근거 검사를 통과하지 못해 원문 정리형으로 보여 드립니다.")
    show_user_notes(output, documents)
    if not developer:
        return
    limits = [service.TOOL_LIMITS[t] for t in output["tools_used"] if t in service.TOOL_LIMITS]
    for note in limits:
        st.info(note)
    tab_cite, tab_flow, tab_path, tab_warn = st.tabs(
        [f"출처 {len(output['citations'])}", "처리 흐름", "그래프 경로", f"주의·한계 {len(output['warnings'])}"])
    with tab_cite:
        show_citations(output, documents)
    with tab_flow:
        show_flow(result)
    with tab_path:
        show_paths(result)
    with tab_warn:
        if output["warnings"]:
            for warning in output["warnings"]:
                st.markdown("- " + re.sub(r"\s+", " ", warning))
        else:
            st.caption("경고 없음")


# ---------------------------------------------------------------- 개발자 화면

def is_developer() -> bool:
    """주소에 ?dev=1 이 있고 DEV_PASSWORD(Secrets·.env)를 맞게 입력했을 때만 개발자 화면을 연다.

    DEV_PASSWORD 가 비어 있으면 열지 않는다(공개 URL 에서 질문 이력이 노출되지 않게 하는 기본값).
    비밀번호 없이 로컬에서만 쓰려면 환경 변수 DEV_ALLOW_OPEN=1 을 명시한다.
    """
    if st.query_params.get("dev") != "1":
        return False
    password = os.getenv("DEV_PASSWORD")
    if not password:
        return os.getenv("DEV_ALLOW_OPEN") == "1"
    typed = st.sidebar.text_input("개발자 비밀번호", type="password", key="dev_pw")
    return typed == password


def _bar(title: str, counts: dict, labels: dict | None = None) -> None:
    """개수 딕셔너리를 막대그래프로 그린다. labels 로 보기 좋은 이름으로 바꾼다."""
    st.markdown(f"**{title}**")
    if not counts:
        st.caption("데이터 없음")
        return
    frame = pd.DataFrame({"이름": [(labels or {}).get(k, k) for k in counts], "횟수": list(counts.values())})
    st.bar_chart(frame.set_index("이름"))


def show_dashboard() -> None:
    """질문 이력(질문 빈도, 상태·의도·도구 분포, 일별 추이)을 보여 준다. 개발자 화면 전용."""
    records = history.load_records()
    st.caption("질문 원문이 로컬 파일(data/interim/ui_history/queries.jsonl, Git 제외)에 저장됩니다. "
               "QUERY_LOG=0 이면 저장하지 않습니다. 클라우드에서는 앱을 다시 시작하면 지워질 수 있습니다.")
    if not records:
        st.info("아직 저장된 질문이 없습니다.")
        return
    summary = history.summarize(records)
    answered = summary["by_status"].get("ANSWERED", 0)
    insufficient = summary["by_status"].get("INSUFFICIENT_EVIDENCE", 0)
    cols = st.columns(4)
    cols[0].metric("전체 질문", summary["total"])
    cols[1].metric("평균 처리 시간", f"{summary['avg_seconds']}초" if summary["avg_seconds"] is not None else "-")
    cols[2].metric("답변 완료 비율", f"{answered / summary['total']:.0%}")
    cols[3].metric("근거 부족 비율", f"{insufficient / summary['total']:.0%}")

    st.markdown("**질문 빈도 TOP 10**")
    top = pd.DataFrame(summary["top_questions"], columns=["질문", "횟수"])
    st.bar_chart(top.set_index("질문"))
    left, right = st.columns(2)
    with left:
        _bar("일별 질문 수", summary["by_day"])
        _bar("답변 상태", summary["by_status"], service.STATUS_LABELS)
    with right:
        _bar("질문 의도 (주 의도)", summary["by_intent"])
        _bar("사용한 검색 도구", summary["by_tool"], {k: v.split(" (")[0] for k, v in service.TOOL_LABELS.items()})

    st.markdown("**최근 질문 30건**")
    recent = pd.DataFrame(records[-30:][::-1])
    shown = [c for c in ("ts", "question", "status", "intent", "tools", "citations", "seconds") if c in recent]
    st.dataframe(recent[shown], width="stretch", hide_index=True)
    st.download_button("이력 CSV 내려받기", history.csv_safe(pd.DataFrame(records)).to_csv(index=False).encode("utf-8-sig"),
                       file_name="query_history.csv", mime="text/csv")


# ---------------------------------------------------------------- 화면

DISCLAIMER = "이 답변은 공식 지침과 법령을 근거로 한 안내이며, 진단·법적 판단을 대신하지 않습니다."


def displayed_answer(result: dict) -> str:
    """화면에 보인 최종 답변 글. 다듬은 답변이면 그대로, 원문 정리형이면 화면과 같은 서식으로 돌려준다."""
    output = result["output"]
    compose = next((t for t in result["trace"] if t["step"] == "compose_answer"), {})
    return output["answer"] if compose.get("mode") == "llm" else answer_markdown(output["answer"])


def save_answer(item: dict, documents: dict) -> None:
    """답변을 이 접속(세션) 안의 '저장한 답변'에 담는다. 서버나 이력 파일에는 저장하지 않는다. 같은 답변은 한 번만 담는다."""
    saved = st.session_state.setdefault("saved", [])
    if any(s["id"] == item["request_id"] for s in saved):
        return
    saved.insert(0, {"id": item["request_id"], "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                     "question": item["question"], "status": item["output"]["status"],
                     "answer": displayed_answer(item),
                     "sources": sources.source_lines(item["output"].get("citations", []), documents, source_urls()),
                     "notice": {"PARTIAL": PARTIAL_NOTICE, "INSUFFICIENT_EVIDENCE": NO_EVIDENCE_NOTICE}.get(
                         item["output"]["status"]),
                     "notes": sources.user_warnings(item["output"].get("warnings", []))})


def saved_as_markdown(saved: list[dict]) -> str:
    """저장한 답변을 내려받기용 마크다운 글로 만든다. 답변마다 안내 문구를 붙인다."""
    parts = ["# 저장한 답변 — 학교 감염병 대응 길잡이", ""]
    for number, s in enumerate(saved, 1):
        status = service.STATUS_LABELS.get(s["status"], s["status"])
        parts += [f"## {number}. {s['question']}", f"- 저장 시각: {s['ts']} · 상태: {status}", "", s["answer"], ""]
        if s.get("sources"):
            parts += ["**근거 문서**", *[f"- {line}" for line in s["sources"]], ""]
        if s.get("notice"):
            parts += [f"**{s['notice']}**", ""]
        if s.get("notes"):
            parts += ["**확인이 필요한 부분**", *[f"- {note}" for note in s["notes"]], ""]
        parts += [f"> {DISCLAIMER}", ""]
    return "\n".join(parts)


def show_saved() -> None:
    """'저장한 답변' 탭. 이 접속 안에서만 유지되며, 창을 닫거나 새로고침하면 사라진다."""
    saved = st.session_state.get("saved", [])
    if not saved:
        st.info("저장한 답변이 없습니다. 답변 아래의 '답변 저장' 버튼을 누르면 여기에 모입니다.")
        return
    st.caption("이 화면을 닫거나 새로고침하면 사라집니다. 필요하면 내려받아 두세요. "
               "내려받는 파일에는 입력한 질문 원문이 들어 있으니, 공유하기 전에 개인정보가 없는지 확인하세요.")
    st.download_button("저장한 답변 내려받기 (.md)", saved_as_markdown(saved).encode("utf-8"),
                       file_name="saved_answers.md", mime="text/markdown")
    for s in list(saved):
        icon, _ = STATUS_STYLE.get(s["status"], ("•", "gray"))
        with st.expander(f"{icon} {s['question']}  · {s['ts']}"):
            st.markdown(s["answer"])
            if s.get("sources"):
                st.markdown("**근거 문서**")
                for line in s["sources"]:
                    st.markdown("- " + line)
            if s.get("notice"):
                st.caption(s["notice"])
            for note in s.get("notes", []):
                st.markdown("- " + note)
            st.caption(DISCLAIMER)
            if st.button("삭제", key=f"del-{s['id']}"):
                saved.remove(s)
                st.rerun()


def show_footer() -> None:
    """화면 맨 아래의 사용 범위 안내."""
    st.divider()
    st.markdown("**사용 범위**")
    st.caption("공식 지침과 법령을 근거로 답합니다. 진단·법적 판단을 대신하지 않습니다. "
               "역할은 원문의 예시일 수 있으며 학교 상황에 따라 조정됩니다.")
    if history.logging_enabled():
        st.caption("입력한 질문은 서비스 점검을 위해 저장될 수 있습니다. 학생 이름·연락처 같은 개인정보는 입력하지 마세요.")


def render_history(items: list[dict], documents: dict, developer: bool = False) -> None:
    """최근 대화 10건을 새것부터 보여 준다. developer 가 참이면 점검용 정보도 함께 보여 준다."""
    for index, item in enumerate(items[:10]):
        with st.chat_message("user"):
            st.markdown(item["question"])
        with st.chat_message("assistant"):
            if "error" in item:
                st.error(f"처리 중 오류가 났습니다: {item['error']}")
            else:
                show_result(item, documents, developer)
                if st.button("📌 답변 저장", key=f"save-{item['request_id']}"):
                    save_answer(item, documents)
                    st.rerun()
        if index == 0 and len(items) > 1:
            st.divider()


def answer_live(res: dict, question: str, allowed: list[str], polish: bool) -> dict:
    """질문을 처리하면서 단계 진행을 보여 주고, 근거 검사를 통과한 답변 줄을 바로 출력한다(11/07 스트리밍).
    다듬은 답변이 최종적으로 검사에 걸려 원문 정리형으로 되돌아가면, 다시 그릴 때 최종 답변으로 바뀐다."""
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        progress = st.status("질문을 이해하는 중…", expanded=False)
        board = st.empty()
        lines: list[str] = []

        def on_event(step: str, info: dict) -> None:
            if step in PROGRESS:
                progress.update(label=PROGRESS[step])
            if step == "execute_tool":
                progress.write(f"✓ {service.TOOL_LABELS.get(info.get('tool'), info.get('tool')).split(' (')[0]} "
                               f"— 근거 {info.get('citations', 0)}개")
            elif step == "select_evidence" and info.get("mode") == "llm":
                progress.write(f"✓ 근거 {info.get('cards')}개 중 {info.get('after', ('', ''))[1]}개 선별")

        def on_line(text: str) -> None:
            lines.append(text)
            board.markdown("\n".join(lines) + " ▌")

        try:
            result = service.ask(res, question, DEFAULT_REGION, allowed, polish=polish, on_event=on_event,
                                 on_line=on_line)
            progress.update(label="완료", state="complete")
        except Exception as exc:  # noqa: BLE001
            progress.update(label="오류", state="error")
            result = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def main() -> None:
    if not gate():
        return
    try:
        res = resources()
    except Exception as exc:  # noqa: BLE001 - 접속 실패는 화면에 이유만 알린다(접속 정보 미출력)
        st.error(f"지식 그래프 연결에 실패했습니다: {type(exc).__name__}. 환경 변수(Secrets) 설정을 확인하세요.")
        st.stop()
    documents = res["documents"]

    # 일반 사용자에게는 사이드바가 없다. 답변 다듬기(LLM + 근거 검사)는 가능하면 항상 켠다(키가 없으면 원문 정리형).
    can_polish = "answer_writer" in res["deps"]
    polish = can_polish
    developer = is_developer()
    allowed = service.default_documents(documents)    # 11/07 P0-3: DB에 적재된 모든 문서(판단 기준 원문 포함)
    if developer:
        # 참고 문서 허용 목록·답변 다듬기 켜고 끄기·도구별 한계는 개발자 점검용이다.
        with st.sidebar:
            st.header("개발자 설정")
            polish = st.toggle("답변 다듬기 (LLM + 근거 검사)", value=can_polish, disabled=not can_polish, key="polish",
                               help="검증된 근거만으로 문장을 다시 쓰고, 번호·숫자·주체·강한 표현을 코드로 검사합니다. "
                                    "검사에 걸리면 원문 정리형 답변을 보여 줍니다.")
            for tool, note in service.TOOL_LIMITS.items():
                st.caption(f"⚠ {service.TOOL_LABELS[tool].split(' (')[0]}: {note}")
            allowed = st.multiselect("참고 문서(허용 목록)", list(documents), default=allowed,
                                     format_func=lambda d: doc_label(documents, d), key="allowed")
            st.caption("선택한 문서 안에서만 근거를 찾습니다. 기본값은 DB에 적재된 모든 문서입니다.")

    st.title("🏫 학교 감염병 대응 길잡이")
    st.caption("교육부·경기도교육청·질병관리청 지침과 감염병예방법을 지식 그래프로 묶어, 근거 원문과 쪽수로 답합니다.")

    question = st.chat_input("질문을 입력하세요 (예: 결핵 확진 학생이 나오면 담임교사는 무엇을 하나요?)")
    history_items = st.session_state.setdefault("history", [])

    saved = st.session_state.setdefault("saved", [])
    labels = ["질문하기", f"저장한 답변 ({len(saved)})"] + (["대시보드"] if developer else [])
    tabs = st.tabs(labels)
    with tabs[0]:
        if question:
            if not allowed:
                st.warning("참고 문서를 하나 이상 선택하세요. 빈 목록은 '허용 문서 없음'으로 처리됩니다.")
            result = answer_live(res, question, allowed, polish)
            history_items.insert(0, {"question": question, **result})
            if history.logging_enabled():
                history.append_record(history.make_record(question, result, polish=polish))
            st.rerun()      # 스트리밍 화면을 지우고 최종 결과(출처·처리 흐름 포함)로 다시 그린다
        render_history(history_items, documents, developer=developer)
    with tabs[1]:
        show_saved()
    if developer:
        with tabs[2]:
            show_dashboard()
    show_footer()


main()
