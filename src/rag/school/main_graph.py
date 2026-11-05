"""메인 LangGraph (주승우) — QueryInput → 1개 Tool 실행 → QueryOutput. 11/05 B07·B08.

노드(계약 v2.0 §10): normalize_input → classify_scope → classify_intent → select_tool → execute_tool →
merge_and_validate → evaluate_evidence → compose_answer | partial_or_insufficient → END.
GENERAL·OUT_OF_SCOPE는 Tool 없이 compose_answer로 간다.

- 오늘(11/05)은 Tool 1회 실행 후 종료한다. 재진입 루프·총 3회 한도·동일 입력 반복 방지는 11/06 작업이다.
  State에 tool_results·unresolved·search_rounds를 두어 내일 연결점을 만든다. recursion_limit은 Tool 3회 제한이 아니다.
- Tool registry: 승인된 이름 → 실제 callable(ToolInput → ToolResult). 이 모듈은 다른 담당자 폴더를 import하지 않는다
  (팀 규칙). 기본 registry는 action_search만 등록하고, 다른 Tool은 호출자가 `registry=`로 넣는다.
  등록되지 않은 Tool을 고르면 호출하지 않고(0회) 연결 오류로 남긴다. action_search로 대신 부르지 않는다.
- tools_used·search_rounds는 실제 Tool 호출만 센다(실패 호출 포함, 노드 수·내부 DB 조회 제외).
- 최종 QueryOutput에는 graph_paths가 없다. 경로는 ToolResult와 실행 기록(deps['main_trace'])에만 남긴다.
- driver는 이 모듈이 만들지 않는다. 호출자(서비스·노트북)가 deps로 넣고 닫는다.
"""

from __future__ import annotations

import re
import time
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from index.school.dictionaries import compare_key
from rag.school.action_tool import UNRESOLVED, action_search, check_graph_paths
from rag.school.contract_v2_local import (
    TOOL_NAMES,
    ContractError,
    Finding,
    Citation,
    QueryInput,
    QueryOutput,
    ToolInput,
    ToolResult,
    validate_query_input,
    validate_query_output,
    validate_tool_result,
)
from rag.school.graph import COMPARE_HINTS, OUT_OF_SCOPE
from rag.school.validation_local import verify_against_db

ToolCallable = Callable[[ToolInput], ToolResult]

INTENT_TOOL = {"ACTION": "action_search", "DISEASE": "disease_search", "LEGAL": "legal_search",
               "CROSSDOC": "crossdoc_graph_search"}
TOOL_TASKS = {
    "action_search": "학교가 수행하는 행동, 역할, 단계 및 근거 찾기",
    "disease_search": "질병별 사실(잠복기·전파·증상·관리 기준)과 원문 근거 찾기",
    "legal_search": "학교 보고, 학교장의 보건소 신고, 의료기관 신고 주체를 구분해 근거 찾기",
    "crossdoc_graph_search": "같은 개념에 속한 교육부/경기도 문서별 행동·담당·단계 비교",
}
GREETING = ("안녕", "반가", "고마", "감사합니다", "감사해", "무엇을할수있", "뭘할수있", "뭐할수있", "어떤기능",
            "사용법", "어떻게사용", "도와줄수있", "누구야", "소개해")
DOMAIN_WORDS = ("감염", "학교", "학생", "교사", "교직원", "등교", "보건", "질병", "격리", "신고", "증상", "유행", "역학",
                "관찰실", "방역", "소독", "나이스", "neis", "출결", "출석", "예방", "접종", "잠복", "전파", "환자", "발열",
                "마스크", "휴업", "교육청", "확진", "의심", "가정통신문", "접촉", "결석", "특수학교", "ecp", "연속성",
                "식중독", "급식", "보호자", "학부모", "수업결손", "능동감시", "대응", "단계", "관리조직")
LEGAL_RE = re.compile(r"신고(해야|하여야|의무|대상인|대상이|할의무)|법(령|률)?상|법률|법령|법적|감염병예방법|예방법|시행규칙|시행령|과태료|벌칙|의무가있")
PROCEDURE_RE = re.compile(r"공문|어떻게보내|(?<!예)방법|절차|메뉴|입력하|기안")   # '감염병예방법'의 '방법'은 절차가 아님
DISEASE_FACT = ("잠복기", "전파경로", "감염경로", "전파", "증상은", "증상이뭐", "원인", "치료", "합병증", "특징", "전염력",
                "백신", "얼마나오래", "감염력")
SCHOOL_ACTION_CUES = ("학교는", "학교가", "담임", "보건교사", "교장", "교감", "등교", "해야하나요", "무엇을해야", "조치",
                      "역할", "협조", "준비", "격리하나요")
CROSS_CUES = (*COMPARE_HINTS, "같은가요", "다른가요", "두문서", "문서별")


class MainState(TypedDict, total=False):
    request: QueryInput
    response_type: str            # DOMAIN / GENERAL / OUT_OF_SCOPE
    scope_reason: str
    intent: str                   # ACTION / DISEASE / LEGAL / CROSSDOC
    secondary_intents: list[str]
    selected_tool: str
    tool_input: ToolInput
    tool_results: list[ToolResult]
    tool_calls: list[dict]
    findings: list[Finding]
    citations: list[Citation]
    warnings: list[str]
    tools_used: list[str]
    search_rounds: int
    unresolved: list[str]
    status: str
    final_result: QueryOutput


# ---------------------------------------------------------------- 분류

def classify_question(question: str, books: dict) -> dict:
    """규칙 기반 범위·의도 분류(사전·단서 특징). 근거가 약한 판단은 confidence='low'로 표시한다."""
    key = compare_key(question).casefold()
    diseases = [e["disease_id"] for e in books["disease"]["entries"]
                if any(len(compare_key(n)) >= 2 and compare_key(n) in key for n in [e["name"], *e.get("aliases", [])])]
    roles = [e["role_id"] for e in books["role"]["entries"]
             if any(len(compare_key(n)) >= 3 and compare_key(n) in key for n in [e["name"], *e.get("aliases", [])])]
    domain = bool(diseases or roles or any(w in key for w in DOMAIN_WORDS))
    out = [reason for reason, cues in OUT_OF_SCOPE.items() if any(c in key for c in cues)]
    if out:
        return {"response_type": "OUT_OF_SCOPE", "reason": out[0], "confidence": "high"}
    if not domain:
        if any(c in key for c in GREETING):
            return {"response_type": "GENERAL", "reason": "인사·서비스 사용법", "confidence": "high"}
        return {"response_type": "OUT_OF_SCOPE", "confidence": "low",
                "reason": "학교 감염병 대응 자료와 관련된 질문으로 보이지 않는다."}

    intents = []
    if any(c in key for c in CROSS_CUES):
        intents.append("CROSSDOC")
    legal = bool(LEGAL_RE.search(key)) and not PROCEDURE_RE.search(key)
    if legal:
        intents.append("LEGAL")
    disease_fact = any(c in key for c in DISEASE_FACT) and not any(c in key for c in SCHOOL_ACTION_CUES)
    if disease_fact:
        intents.append("DISEASE")
    intents.append("ACTION")                         # 병명 없는 학교 업무도 Action(일반 대화로 보내지 않음)
    primary = intents[0]
    # 보조 의도: 첫 Tool 외에 확인이 필요한 영역(기본값 ACTION은 보조 의도로 세지 않는다).
    secondary = [i for i in dict.fromkeys(intents[1:]) if i != "ACTION"]
    return {"response_type": "DOMAIN", "intent": primary, "secondary": secondary, "diseases": diseases,
            "roles": roles, "confidence": "high" if primary != "ACTION" or roles or diseases else "medium"}


# ---------------------------------------------------------------- 노드

def build_main_graph(deps: dict, registry: dict[str, ToolCallable]):
    books = deps["books"]
    trace = deps.setdefault("main_trace", [])

    def log(step: str, **info):
        trace.append({"step": step, **info})

    def normalize_input(state: MainState) -> dict:
        request = state["request"]
        log("normalize_input", request_id=request.get("request_id"), region=request["region"],
            allowed=len(request["allowed_document_ids"]))
        return {"tool_results": [], "tool_calls": [], "findings": [], "citations": [], "warnings": [],
                "tools_used": [], "search_rounds": 0, "unresolved": []}

    def classify_scope(state: MainState) -> dict:
        result = classify_question(state["request"]["question"], books)
        log("classify_scope", **{k: result[k] for k in ("response_type", "reason", "confidence") if k in result})
        update = {"response_type": result["response_type"], "scope_reason": result.get("reason", "")}
        if result["response_type"] == "DOMAIN":
            update |= {"intent": result["intent"], "secondary_intents": result["secondary"]}
        return update

    def classify_intent(state: MainState) -> dict:
        log("classify_intent", intent=state["intent"], secondary=state.get("secondary_intents", []))
        return {}

    def select_tool(state: MainState) -> dict:
        tool = INTENT_TOOL[state["intent"]]
        registered = tool in registry
        log("select_tool", tool=tool, registered=registered)
        if registered:
            return {"selected_tool": tool}
        return {"selected_tool": tool, "warnings": [
            f"선택한 Tool '{tool}'이 메인 그래프에 연결되지 않아 호출하지 않았다(연결 오류, 호출 0회). "
            "다른 Tool로 대신 답하지 않는다."]}

    def execute_tool(state: MainState) -> dict:
        request, tool = state["request"], state["selected_tool"]
        task = TOOL_TASKS[tool] + (f" (요청 힌트: {request['task']})" if request.get("task") else "")
        tool_input: ToolInput = {"question": request["question"], "task": task, "region": request["region"],
                                 "allowed_document_ids": list(request["allowed_document_ids"])}
        for key in ("preferred_document_ids", "context_entity_ids"):
            if request.get(key):
                tool_input[key] = list(request[key])
        started = time.perf_counter()
        error = None
        try:
            result = registry[tool](tool_input)
            validate_tool_result(result, tool_input, expected_tool=tool)
        except ContractError as exc:
            error = f"ToolResult 계약 위반: {exc}"
        except Exception as exc:  # Tool 내부 예외도 호출 실패로 기록한다
            error = f"Tool 실행 실패: {type(exc).__name__}: {exc}"
        if error:
            result = {"tool_name": tool, "status": "ERROR", "findings": [], "citations": [], "graph_paths": [],
                      "warnings": [error]}
        call = {"request_id": request.get("request_id"), "tool": tool, "status": result["status"],
                "findings": len(result["findings"]), "citations": len(result["citations"]),
                "graph_paths": len(result["graph_paths"]), "seconds": round(time.perf_counter() - started, 2),
                "error": error}
        log("execute_tool", **call)
        return {"tool_input": tool_input, "tool_results": state["tool_results"] + [result],
                "tool_calls": state["tool_calls"] + [call], "tools_used": state["tools_used"] + [tool],
                "search_rounds": state["search_rounds"] + 1}

    def merge_and_validate(state: MainState) -> dict:
        """Citation은 chunk_id로 한 번만, Finding은 근거별로 보존. action_search 외 Tool 결과는 여기서 DB 대조한다."""
        request = state["request"]
        findings, citations, warnings, unresolved = [], {}, list(state.get("warnings", [])), []
        driver, database = deps["driver"], deps.get("database")
        for result in state["tool_results"]:
            if result["status"] in ("OK", "PARTIAL") and result["tool_name"] != "action_search":
                report = verify_against_db(result, driver, database)
                bad_paths = check_graph_paths(result["graph_paths"], request["allowed_document_ids"], driver, database)
                kept = [f for i, f in enumerate(result["findings"]) if i not in report["bad_findings"]]
                if len(kept) != len(result["findings"]) or bad_paths:
                    warnings.append(f"{result['tool_name']} 결과 중 DB 대조 실패 Finding "
                                    f"{len(result['findings']) - len(kept)}개, 경로 {len(bad_paths)}개를 제외했다.")
                result = {**result, "findings": kept}
            used = {c for f in result["findings"] for c in f["chunk_ids"]}
            for citation in result["citations"]:
                if citation["chunk_id"] in used:
                    citations.setdefault(citation["chunk_id"], citation)
            findings += result["findings"]
            for warning in result["warnings"]:
                (unresolved if warning.startswith(UNRESOLVED) else warnings).append(warning)
        log("merge_and_validate", findings=len(findings), citations=len(citations), unresolved=len(unresolved))
        return {"findings": findings, "citations": list(citations.values()),
                "warnings": list(dict.fromkeys(warnings)), "unresolved": list(dict.fromkeys(unresolved))}

    def evaluate_evidence(state: MainState) -> dict:
        """오늘은 1회 실행 후 충분성만 판정한다(추가 Tool 재진입은 11/06)."""
        results = state["tool_results"]
        if not results:                                          # 미등록 Tool: 호출 0회
            status = "ERROR"
        elif state["findings"]:
            partial = (any(r["status"] == "PARTIAL" for r in results) or state["unresolved"]
                       or state.get("secondary_intents"))
            status = "PARTIAL" if partial else "ANSWERED"
        elif any(r["status"] == "ERROR" for r in results):
            status = "ERROR"
        else:
            status = "INSUFFICIENT_EVIDENCE"
        unresolved = list(state["unresolved"])
        for intent in state.get("secondary_intents", []):
            unresolved.append(f"{UNRESOLVED} 질문의 일부는 {INTENT_TOOL[intent]} 확인이 필요하다(추가 호출은 11/06 루프에서).")
        log("evaluate_evidence", status=status, unresolved=len(unresolved))
        return {"status": status, "unresolved": unresolved}

    def compose_answer(state: MainState) -> dict:
        return {"final_result": finalize(state)}

    def partial_or_insufficient(state: MainState) -> dict:
        return {"final_result": finalize(state)}

    graph = StateGraph(MainState)
    for name, fn in (("normalize_input", normalize_input), ("classify_scope", classify_scope),
                     ("classify_intent", classify_intent), ("select_tool", select_tool),
                     ("execute_tool", execute_tool), ("merge_and_validate", merge_and_validate),
                     ("evaluate_evidence", evaluate_evidence), ("compose_answer", compose_answer),
                     ("partial_or_insufficient", partial_or_insufficient)):
        graph.add_node(name, fn)
    graph.add_edge(START, "normalize_input")
    graph.add_edge("normalize_input", "classify_scope")
    graph.add_conditional_edges("classify_scope", lambda s: "domain" if s["response_type"] == "DOMAIN" else "direct",
                                {"domain": "classify_intent", "direct": "compose_answer"})
    graph.add_edge("classify_intent", "select_tool")
    graph.add_conditional_edges("select_tool", lambda s: "run" if s["selected_tool"] in registry else "skip",
                                {"run": "execute_tool", "skip": "evaluate_evidence"})
    graph.add_edge("execute_tool", "merge_and_validate")
    graph.add_edge("merge_and_validate", "evaluate_evidence")
    graph.add_conditional_edges("evaluate_evidence", lambda s: "full" if s["status"] == "ANSWERED" else "partial",
                                {"full": "compose_answer", "partial": "partial_or_insufficient"})
    graph.add_edge("compose_answer", END)
    graph.add_edge("partial_or_insufficient", END)
    return graph.compile()


# ---------------------------------------------------------------- 최종 응답

GENERAL_ANSWER = ("학교 감염병 대응 지침(교육부 매뉴얼·경기도 가이드북 등)을 근거로 학교의 대응 단계별 행동, 담당 역할, "
                  "등교중지·보고 절차, 질병별 관리 기준을 찾아 원문 쪽수와 함께 안내합니다. 진단이나 법적 판단을 대신하지는 않습니다.")
STATUS_HEAD = {"ANSWERED": "원문에서 확인한 내용입니다.",
               "PARTIAL": "원문에서 일부만 확인했습니다. 확인한 내용과 남은 확인 사항을 함께 안내합니다.",
               "INSUFFICIENT_EVIDENCE": "허용된 자료에서 질문에 답할 근거를 찾지 못했습니다.",
               "ERROR": "검색 도구를 실행하지 못해 답하지 못했습니다."}
MAX_PER_SECTION = 3       # 화면 답변에서 역할(구역)마다 보이는 줄 수. Tool 근거는 줄이지 않고 findings·citations에 모두 남긴다.
MAX_QUOTE_LINES = 2
QUOTE_CHARS = 130
NO_ROLE_TITLE = "담당이 원문에 적혀 있지 않은 행동"
QUOTE_TITLE = "원문 그대로 (행동으로 정리되지 않은 문단·표)"
DOC_SHORT = {"교육부 매뉴얼": "교육부", "경기도 가이드북": "경기도"}
# 다른 Tool(질병·법령·문서 비교)의 claim은 형식이 달라 문서별로 묶는다(계약: 문서마다 조건이 다르면 분리해 설명).
DOC_TITLES = {"moe_school_manual_v3": ("교육부 매뉴얼", "교육부"),
              "gyeonggi_school_guide_2025": ("경기도 가이드북", "경기도"),
              "kdca_measles_2026": ("질병관리청 홍역 관리지침", "질병청 홍역"),
              "kdca_diagnosis_reporting_2026": ("질병관리청 진단·신고 기준", "질병청 신고"),
              "moleg_infectious_disease_act_21521": ("감염병예방법", "감염병예방법")}
OFF_STAGE_TAIL = " — 원문 배치 단계가 질문 단계와 다름"
HEAD_RE = re.compile(r"^\[(?P<head>[^\]]*)\]\s*(?P<body>.*)$", re.S)
ROLE_RE = re.compile(r"^(?:원문이 제시한 담당 예시|원문이 명시한 담당):\s*(?P<role>.+?)\s*—\s*(?P<text>.+)$", re.S)
NO_ROLE_RE = re.compile(r"^원문에 담당 미기재\s*—\s*(?P<text>.+)$", re.S)
QUOTE_RE = re.compile(r"^원문:\s*“(?P<text>.*)”$", re.S)
PAGE_RE = re.compile(r"^(?P<doc>.*?)\s*PDF\s*(?P<page>\d+)쪽(?P<extra>.*)$")


def _short_source(first: str) -> str:
    match = PAGE_RE.match(first.strip())
    if not match:
        return first.strip()
    doc = DOC_SHORT.get(match["doc"].strip(), match["doc"].strip())
    extra = "(시나리오 예시)" if "모의훈련 시나리오 예시" in match["extra"] else ""
    return f"{doc} p.{match['page']}{extra}"


def parse_claim(claim: str) -> dict | None:
    """Action Tool이 만든 Finding.claim 형식(`[출처 · 단계] 담당 — 행동` / `[출처 · 태그] 원문: “…”`)을 읽는다.
    형식이 다른 Tool의 claim이면 None을 돌려주고 호출자가 원문 그대로 쓴다."""
    match = HEAD_RE.match(claim)
    if not match:
        return None
    parts = [p.strip() for p in match["head"].split(" · ")]
    body, off_stage = match["body"].strip(), False
    if body.endswith(OFF_STAGE_TAIL):
        body, off_stage = body[: -len(OFF_STAGE_TAIL)], True
    stage = None
    role = None
    for tag in parts[1:]:
        if tag.startswith("원문 배치:"):
            label = tag.removeprefix("원문 배치:").strip()
            off_stage = off_stage or "질문 단계와 다름" in label
            stage = label.split("—")[0].strip()
        elif tag.startswith(("원문이 제시한 담당 예시:", "원문의 담당:")):
            role = tag.split(":", 1)[1].strip()
        elif tag != "단계 미기재":
            stage = tag
    out = {"source": _short_source(parts[0]), "stage": re.sub(r"제(\d)단계", r"\1단계", stage) if stage else None,
           "off_stage": off_stage}
    quote = QUOTE_RE.match(body)
    if quote:
        # PDF 글머리 기호(사용자 정의 문자 영역 글리프·¢·∙ 등)는 공백으로 바꾸고 앞쪽 기호를 지운다. 낱말은 건드리지 않는다.
        text = re.sub(r"[\ue000-\uf8ff]", " ", quote["text"])
        text = re.sub(r"^[^\w(\[「『<]+", "", re.sub(r"\s+", " ", text).strip())
        return {**out, "kind": "quote", "role": role,
                "text": text if len(text) <= QUOTE_CHARS else text[: QUOTE_CHARS - 1].rstrip() + "…"}
    with_role = ROLE_RE.match(body)
    if with_role:
        return {**out, "kind": "action", "role": with_role["role"], "text": with_role["text"]}
    no_role = NO_ROLE_RE.match(body)
    if no_role:
        return {**out, "kind": "action", "role": None, "text": no_role["text"]}
    return None


def _group_lines(entries: list[dict], limit: int) -> tuple[list[str], int]:
    """같은 문구(두 문서에 같은 말)는 한 줄로 묶고 출처를 모은다. (줄 목록, 보이지 않은 줄 수)."""
    groups: dict[str, dict] = {}
    for entry in entries:
        key = re.sub(r"\s+", "", entry["text"])
        group = groups.setdefault(key, {"text": entry["text"], "sources": [], "refs": set(),
                                        "role": entry.get("role"), "quote": entry.get("kind") == "quote"})
        label = (entry["source"] + (f" · {entry['stage']}" if entry.get("stage") else "")
                 + (" · 질문 단계와 다름" if entry.get("off_stage") else ""))
        if label not in group["sources"]:
            group["sources"].append(label)
        group["refs"].update(entry["refs"])
    lines = []
    for group in list(groups.values())[:limit]:
        who = f"{group['role']}: " if group["role"] and group["quote"] else ""   # 행동은 구역 제목이 역할
        refs = "".join(f"[{n}]" for n in sorted(group["refs"]))
        text = f"“{group['text']}”" if group["quote"] else group["text"]
        sources = "; ".join(s for s in group["sources"] if s)
        lines.append(f"- {who}{text}" + (f"  ({sources})" if sources else "") + f" {refs}".rstrip())
    return lines, max(0, len(groups) - limit)


def compose_text(status: str, findings: list[Finding], citations: list[Citation], warnings: list[str]) -> str:
    """검증된 Finding만으로 답변을 쓴다. 역할별로 묶고 출처는 줄 끝에 짧게 둔다. 새 사실을 더하지 않으며
    Finding은 그대로 둔다(같은 문구 합치기·재배열·출처 표기 축약만 한다)."""
    lines = [STATUS_HEAD[status]]
    index = {c["chunk_id"]: i for i, c in enumerate(citations, 1)}
    by_chunk = {c["chunk_id"]: c for c in citations}
    by_role: dict[str, list[dict]] = {}
    quotes = []
    by_doc: dict[str, list[dict]] = {}
    for finding in findings:
        refs = {index[c] for c in finding["chunk_ids"] if c in index}
        parsed = parse_claim(finding["claim"])
        if parsed is None:
            cited = [by_chunk[c] for c in finding["chunk_ids"] if c in by_chunk]
            doc = cited[0]["document_id"] if cited else ""
            short = DOC_TITLES.get(doc, (doc, doc))[1]
            pages = ", ".join(dict.fromkeys(f"p.{c['pdf_page']}" for c in cited if c["document_id"] == doc))
            text = re.sub(r"\s+", " ", finding["claim"]).strip()
            by_doc.setdefault(doc, []).append({"text": text, "source": f"{short} {pages}".strip(), "refs": refs})
        elif parsed["kind"] == "quote":
            quotes.append({**parsed, "refs": refs})
        else:
            by_role.setdefault(parsed["role"] or NO_ROLE_TITLE, []).append({**parsed, "refs": refs})
    sections: list[tuple[str, list[str], int]] = []
    ordered = [r for r in by_role if r != NO_ROLE_TITLE] + ([NO_ROLE_TITLE] if NO_ROLE_TITLE in by_role else [])
    for role in ordered:
        body, hidden = _group_lines(by_role[role], MAX_PER_SECTION)
        sections.append((role, body, hidden))
    if quotes:
        grouped, hidden = _group_lines(quotes, MAX_QUOTE_LINES)
        sections.append((QUOTE_TITLE, grouped, hidden))
    for doc, entries in by_doc.items():
        body, hidden = _group_lines(entries, MAX_PER_SECTION)
        sections.append((DOC_TITLES.get(doc, (doc or "출처 미상", ""))[0], body, hidden))
    hidden_total = 0
    for title, body, hidden in sections:
        lines += ["", f"■ {title}", *body]
        hidden_total += hidden
    if hidden_total:
        lines += ["", f"그 밖의 근거 {hidden_total}건은 findings·citations에 있습니다."]
    notes = [w.removeprefix(UNRESOLVED).strip() for w in warnings][:4]
    if notes:
        lines += ["", "참고: " + " / ".join(notes)]
    return "\n".join(lines)


def finalize(state: MainState) -> QueryOutput:
    kind = state["response_type"]
    if kind == "GENERAL":
        return {"response_type": "GENERAL", "status": "ANSWERED", "answer": GENERAL_ANSWER, "findings": [],
                "citations": [], "action_ids": [], "warnings": [], "tools_used": [], "search_rounds": 0}
    if kind == "OUT_OF_SCOPE":
        reason = state.get("scope_reason") or "지원 범위 밖 질문이다."
        return {"response_type": "OUT_OF_SCOPE", "status": "INSUFFICIENT_EVIDENCE",
                "answer": f"이 서비스가 답할 수 있는 범위가 아닙니다. {reason}", "findings": [], "citations": [],
                "action_ids": [], "warnings": [reason], "tools_used": [], "search_rounds": 0}
    status = state["status"]
    findings = state["findings"] if status in ("ANSWERED", "PARTIAL") else []
    citations = state["citations"] if findings else []
    warnings = list(dict.fromkeys(state.get("warnings", []) + state.get("unresolved", [])))
    return {"response_type": "DOMAIN", "status": status,
            "answer": compose_text(status, findings, citations, warnings), "findings": findings,
            "citations": citations, "action_ids": list(dict.fromkeys(a for f in findings for a in f["action_ids"])),
            "warnings": warnings, "tools_used": list(state.get("tools_used", [])),
            "search_rounds": state.get("search_rounds", 0)}


# ---------------------------------------------------------------- 공개 진입점

def default_registry(deps: dict) -> dict[str, ToolCallable]:
    """이 모듈이 아는 실제 Tool. 다른 담당자 Tool은 호출자가 registry에 추가한다."""
    return {"action_search": lambda tool_input: action_search(tool_input, deps)}


def run_main(query: QueryInput, deps: dict, registry: dict[str, ToolCallable] | None = None) -> QueryOutput:
    """입력 검사 → compiled main graph.invoke → 최종 결과 투영 → v2 출력 검사. 내부 State는 밖으로 내보내지 않는다.

    deps: driver(호출자 소유), database, books(없으면 로드), embedder(선택). 실행 기록은 deps['main_trace']에 남는다.
    """
    validate_query_input(query)
    if "books" not in deps:
        from index.school.dictionaries import load_dictionaries
        deps["books"] = load_dictionaries()
    registry = default_registry(deps) if registry is None else registry
    unknown = set(registry) - TOOL_NAMES
    if unknown:
        raise ContractError(f"승인되지 않은 Tool 이름: {sorted(unknown)}")
    deps["main_trace"] = []
    state = build_main_graph(deps, registry).invoke({"request": query})
    output = state["final_result"]
    deps["main_trace"].append({"step": "tool_results", "results": state.get("tool_results", [])})
    validate_query_output(output, query)
    return output
