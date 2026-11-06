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
from pathlib import Path

import streamlit as st

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:            # 클라우드는 이 파일의 폴더만 sys.path에 넣는다
    sys.path.insert(0, str(SRC))

from rag.ui import service

st.set_page_config(page_title="학교 감염병 대응 길잡이", page_icon="🏫", layout="wide")

EXAMPLES = [
    ("학교 행동", "학년부장은 능동감시에서 무엇을 하나요?", "GYEONGGI"),
    ("질병 정보", "홍역의 잠복기는 며칠인가요?", "NATIONAL"),
    ("문서 비교 (그래프)", "보호자에게 연락해 진료를 요청하는 일을 교육부 매뉴얼과 경기도 가이드북은 각각 어떻게 설명하나요?",
     "GYEONGGI"),
    ("복합 (재검색)", "학생이 홍역 의심 증상을 보이면 학교는 무엇을 해야 하고, 홍역의 잠복기는 얼마인가요?", "GYEONGGI"),
]
REGIONS = {"GYEONGGI": "경기도 학교", "NATIONAL": "전국 공통"}
STEP_LABELS = {"normalize_input": "입력 정리", "classify_scope": "질문 범위 판단", "classify_intent": "질문 의도 분류",
               "select_tool": "검색 도구 선택", "execute_tool": "검색 도구 실행", "merge_and_validate": "근거 병합·DB 대조",
               "evaluate_evidence": "근거 충분성 판단", "compose_answer": "답변 작성"}
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
        elif step["step"] == "evaluate_evidence":
            detail = f"{info.get('decision')} (상태 {info.get('status')}, 미해결 {info.get('unresolved')})"
            if info.get("next_tool"):
                detail += f" → 다음 도구 {info['next_tool']}"
        elif step["step"] == "compose_answer":
            detail = COMPOSE_MODES.get(info.get("mode"), info.get("mode"))
            if info.get("seconds") is not None:
                detail += f" · {info['seconds']}초"
            if info.get("problems"):
                detail += " · 검사: " + " / ".join(info["problems"][:3])
        elif step["step"] == "classify_scope":
            detail = f"{info.get('response_type')} {('— ' + info['reason']) if info.get('reason') else ''}"
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


def show_result(result: dict, documents: dict) -> None:
    output = result["output"]
    icon, color = STATUS_STYLE.get(output["status"], ("•", "gray"))
    tools = ", ".join(service.TOOL_LABELS.get(t, t).split(" (")[0] for t in output["tools_used"]) or "도구 호출 없음"
    st.markdown(f"{icon} :{color}[**{service.STATUS_LABELS.get(output['status'], output['status'])}**] · {tools}")
    compose = next((t for t in result["trace"] if t["step"] == "compose_answer"), {})
    if compose.get("mode") == "llm":
        st.markdown(output["answer"])
        with st.expander("원문 정리형 답변 보기 (출처 쪽수 포함)"):
            st.markdown(answer_markdown(compose["template"]))
    else:
        st.markdown(answer_markdown(output["answer"]))
        if compose.get("mode") == "template_fallback":
            st.caption("다듬은 답변이 근거 검사를 통과하지 못해 원문 정리형으로 보여 드립니다.")
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


# ---------------------------------------------------------------- 화면

def main() -> None:
    if not gate():
        return
    try:
        res = resources()
    except Exception as exc:  # noqa: BLE001 - 접속 실패는 화면에 이유만 알린다(접속 정보 미출력)
        st.error(f"지식 그래프 연결에 실패했습니다: {type(exc).__name__}. 환경 변수(Secrets) 설정을 확인하세요.")
        st.stop()
    documents = res["documents"]

    with st.sidebar:
        st.header("검색 조건")
        region = st.radio("학교 지역", list(REGIONS), format_func=REGIONS.get, key="region")
        options = list(documents)
        default = [d for d in service.EVALUATED_DOCUMENTS if d in documents]
        allowed = st.multiselect("참고 문서(허용 목록)", options, default=default,
                                 format_func=lambda d: doc_label(documents, d), key="allowed")
        st.caption("선택한 문서 안에서만 근거를 찾습니다. 기본값은 골든셋으로 검증한 5개 문서입니다.")
        can_polish = "answer_writer" in res["deps"]
        polish = st.toggle("답변 다듬기 (LLM + 근거 검사)", value=can_polish, disabled=not can_polish, key="polish",
                           help="검증된 근거만으로 문장을 다시 쓰고, 번호·숫자·주체·강한 표현을 코드로 검사합니다. "
                                "검사에 걸리면 원문 정리형 답변을 보여 줍니다.")
        st.divider()
        st.markdown("**사용 범위**\n- 공식 지침의 원문 근거와 쪽수를 안내합니다.\n- 진단·법적 판단을 대신하지 않습니다.\n"
                    "- 역할은 원문의 예시일 수 있으며 학교 상황에 따라 조정됩니다.")
        for tool, note in service.TOOL_LIMITS.items():
            st.caption(f"⚠ {service.TOOL_LABELS[tool].split(' (')[0]}: {note}")

    st.title("🏫 학교 감염병 대응 길잡이")
    st.caption("교육부·경기도교육청·질병관리청 지침과 감염병예방법을 지식 그래프로 묶어, 근거 원문과 쪽수로 답합니다.")

    cols = st.columns(len(EXAMPLES))
    for col, (label, question, example_region) in zip(cols, EXAMPLES, strict=True):
        if col.button(label, help=question, width="stretch"):
            st.session_state.pending = (question, example_region)

    question = st.chat_input("질문을 입력하세요 (예: 결핵 확진 학생이 나오면 담임교사는 무엇을 하나요?)")
    if question:
        st.session_state.pending = (question, region)

    history = st.session_state.setdefault("history", [])
    if st.session_state.get("pending"):
        question, ask_region = st.session_state.pop("pending")
        if not allowed:
            st.warning("참고 문서를 하나 이상 선택하세요. 빈 목록은 '허용 문서 없음'으로 처리됩니다.")
        with st.spinner("근거를 찾는 중…"):
            try:
                result = service.ask(res, question, ask_region, allowed, polish=polish)
            except Exception as exc:  # noqa: BLE001
                result = {"error": f"{type(exc).__name__}: {exc}"}
        history.insert(0, {"question": question, "region": ask_region, **result})

    for index, item in enumerate(history[:10]):
        with st.chat_message("user"):
            st.markdown(f"{item['question']}  \n:gray[{REGIONS.get(item['region'], item['region'])}]")
        with st.chat_message("assistant"):
            if "error" in item:
                st.error(f"처리 중 오류가 났습니다: {item['error']}")
            else:
                show_result(item, documents)
        if index == 0 and len(history) > 1:
            st.divider()


main()
