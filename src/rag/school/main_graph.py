"""메인 LangGraph (주승우) — QueryInput → Tool 호출(최대 3회) → QueryOutput. 11/05 B07·B08, 11/06 M4 재검색 루프.

노드(계약 v2.0 §10): normalize_input → classify_scope → classify_intent → select_tool → execute_tool →
merge_and_validate → evaluate_evidence → (select_tool 재진입 | select_evidence → compose_answer | partial_or_insufficient) → END.
select_evidence(11/07 ②)는 모든 Tool 결과를 모은 뒤 LLM 재순위로 필요한 근거만 남기고 하위 질문별 충분성을 status에 반영한다.
check_facts(11/07 답변 품질 P0-2)는 select_evidence 앞에서 라우터가 고른 확인 항목(신고 대상·급수·유행의심 기준·신고의무자·
벌칙·등교중지)의 판단 기준 원문을 DB에서 직접 찾아 근거에 더한다. 이 근거는 선별 뒤에도 남긴다(판단에 꼭 필요한 규칙).
GENERAL·OUT_OF_SCOPE는 Tool 없이 compose_answer로 간다.

- 재검색(11/06): 근거가 충분하면(ANSWERED) 추가 호출 없이 끝낸다. 부족할 때만, 명시적 신호가 가리키는 아직 부르지 않은
  Tool을 다음으로 부른다. 신호는 (1) 질문 분류의 보조 의도, (2) Tool 경고의 `[미해결] … <tool 이름> …` 두 가지뿐이다.
  신호 없이 '혹시 몰라' 다른 Tool을 부르지 않는다. 총 호출은 MAX_TOOL_CALLS(3)회, 같은 Tool은 한 번만 부른다.
  넘기기(11/08): 주 의도 Tool이 NO_EVIDENCE(거절 카드 제외)이고 남은 신호가 없으면 학교 행동 Tool(허용 문서 전체 검색)을
  한 번 부른다(FALLBACK_TOOL). 근거 부족 시 추가 호출이라 계약 v2.0 범위 안이고, 막다른 길에서만 시간이 든다.
  두 번째 호출부터는 앞선 Finding의 질병·개념 ID를 context_entity_ids로 넘긴다. recursion_limit은 이 한도가 아니다.
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
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from index.school.dictionaries import compare_key
from rag.school.action_tool import UNRESOLVED, action_search, check_graph_paths
from rag.school.answer_writer import user_note
from rag.school.checks import CHECK_LABELS, augment_checks, gather_checks
from rag.school.contract_v2_local import (
    TOOL_NAMES,
    Citation,
    ContractError,
    Finding,
    QueryInput,
    QueryOutput,
    ToolInput,
    ToolResult,
    validate_query_input,
    validate_query_output,
    validate_tool_result,
)
from rag.school.graph import COMPARE_HINTS, OUT_OF_SCOPE
from rag.school.rerank import MAX_CARDS, filter_evidence, key_terms
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
# 법 조항·처벌을 직접 말하는 질문만 법령(LEGAL)을 먼저 부른다.
LEGAL_RE = re.compile(r"법(령|률)?상|법률|법령|법적|감염병예방법|예방법|시행규칙|시행령|과태료|벌칙|처벌|의무가있")
# '신고해야 하나요?'처럼 학교의 신고 의무만 묻고 법 조항은 말하지 않는 질문. 학교 문서(신고 대상 목록·학교장 신고 행동)가
# 먼저 답하고, 법적 확정은 법령 확인이 필요하다(11/06 S-Q03: 법령만 부르면 카드가 없을 때 막다른 길이 된다).
SCHOOL_DUTY_RE = re.compile(r"신고(도|는)?(해야|하여야|의무|대상인|대상이|할의무)")
PROCEDURE_RE = re.compile(r"공문|어떻게보내|(?<!예)방법|절차|메뉴|입력하|기안")   # '감염병예방법'의 '방법'은 절차가 아님
DISEASE_FACT = ("잠복기", "전파경로", "감염경로", "전파", "증상은", "증상이뭐", "원인", "치료", "합병증", "특징", "전염력",
                "백신", "얼마나오래", "감염력")
SCHOOL_ACTION_CUES = ("학교는", "학교가", "담임", "보건교사", "교장", "교감", "등교", "해야하나요", "무엇을해야", "조치",
                      "역할", "협조", "준비", "격리하나요")
CROSS_CUES = (*COMPARE_HINTS, "같은가요", "다른가요", "두문서", "문서별")
# 법령·질병 질문 안에 '학교가 할 일'을 함께 묻는 표현(역할 이름만으로는 넣지 않는다: '학교장이 신고해야'의 학교장은 신고 주체).
ACTION_ASK = ("무엇을하", "무엇을해야", "뭘해야", "뭐해야", "어떻게해야", "어떤조치", "조치는", "조치를", "역할", "안내하나요",
              "안내해야", "할일")
DISEASE_ASK = ("잠복기", "전파경로", "감염경로", "증상은", "증상이뭐", "합병증", "전염력", "감염력", "전염기간")
MAX_TOOL_CALLS = 3
# 개념 수준 문서 용어 검색 문장을 task로 넘기는 Tool(11/07 ②). 다른 담당자 Tool은 task를 검색어로 쓸 수 있어
# (crossdoc은 질문+task 글자를 읽는다) 넘기지 않는다.
DOC_QUERY_TOOLS = frozenset({"action_search"})
# 주 의도 Tool이 근거 없음(NO_EVIDENCE)으로 끝났을 때 한 번 넘기는 Tool(11/08). 학교 행동 Tool은 허용 문서 전체를
# hybrid로 찾으므로, 답 카드가 없는 법령 질문 등이 막다른 길(근거 없음)로 끝나지 않게 한다. 근거: 법령 골든 Q21
# '수족구병 등교중지의 법적 근거' — 법령 Tool NO_EVIDENCE로 끝났지만 학교 행동 Tool 단독은 학교보건법 제8조 원문을 찾았다
# ([교차 측정](notebooks/school/평가결과/20261108/타담당Tool_교차측정_개선제안_20261108.md)).
FALLBACK_TOOL = "action_search"
# Tool이 일부러 답을 거절한 경우(근거가 없어서가 아니라 확정할 수 없어서)는 넘기지 않는다. 법령 Tool 거부 카드 문구.
DECLINE_MARKERS = ("확정하지 않았습니다",)
# 질병 동반 호출(11/08): 질문이 승인 질병을 말하면 첫 호출에 함께 부르는 Tool. 상태 판정에서는 보조 근거로 본다.
COMPANION_TOOL = "disease_search"
COMPANION_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="companion_tool")


def named_diseases(question: str, books: dict) -> list[str]:
    """질문에 나온 승인 질병 ID(사전의 이름·별칭, 2글자 이상, compare_key로 비교)."""
    key = compare_key(question)
    return [entry["disease_id"] for entry in books["disease"]["entries"]
            if any(len(compare_key(n)) >= 2 and compare_key(n) in key for n in (entry["name"], *entry.get("aliases", [])))]


# 확인 항목 조회를 Tool 실행과 동시에 돌리는 공용 실행기(요청마다 만들지 않는다).
CHECK_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="check_facts")


class MainState(TypedDict, total=False):
    request: QueryInput
    response_type: str            # DOMAIN / GENERAL / OUT_OF_SCOPE
    scope_reason: str
    intent: str                   # ACTION / DISEASE / LEGAL / CROSSDOC
    secondary_intents: list[str]
    sub_questions: dict[str, str]  # Tool 이름 → 하위 질문(LLM 라우터, 의도 2개 이상일 때만)
    search_queries: dict[str, str]  # Tool 이름 → 개념 수준 문서 용어 검색 문장(LLM 라우터, 11/07 ②)
    picks: dict[str, dict]        # 근거 재순위 결과: chunk_id → 역할·하위 질문 번호·원문 대조를 통과한 핵심 구절
    situation: dict               # 질문 속 사실(질병·확진/의심·인원·범위·질문자 역할, LLM 라우터, 11/07 P0-1)
    checks: list[str]             # 답하려면 확인할 항목(REPORTABLE·STAGE…)
    pinned: list[str]             # 확인 항목 근거 chunk_id(선별 뒤에도 남긴다)
    facts: list[dict]             # 확인 항목에서 코드가 원문과 글자 대조한 사실({chunk_id, text})
    pin_roles: dict[str, str]     # 확인 항목 근거의 쓰임: ROLE(역할별 할 일 표) / RULE(판단 규칙)
    selected_tool: str
    next_tool: str
    next_reason: str
    skipped_tools: list[str]
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


def scenario_chunks(deps: dict, chunk_ids: list[str]) -> set[str]:
    """인용 청크 중 모의훈련 시나리오 절(section에 '시나리오')에 속한 것(읽기 전용 조회 1회, 11/07 ③).
    다른 Tool의 Finding claim에는 시나리오 표기가 없어 DB section으로 확인한다. DB가 없거나 실패하면 빈 집합."""
    driver = deps.get("driver")
    if not driver or not chunk_ids:
        return set()
    try:
        records, _, _ = driver.execute_query(
            "MATCH (c:Chunk) WHERE c.chunk_id IN $ids AND coalesce(c.section, '') CONTAINS '시나리오' "
            "RETURN c.chunk_id AS chunk_id", ids=list(dict.fromkeys(chunk_ids)),
            database_=deps.get("database"), routing_="r")
    except Exception:  # noqa: BLE001 - 표시를 못 붙여도 답변은 낸다(claim 표기로만 판단)
        return set()
    return {r["chunk_id"] for r in records}


def chunk_texts(deps: dict, chunk_ids: list[str]) -> dict[str, str]:
    """청크 본문 전체(읽기 전용 조회 1회, 11/08 본문 일치 구간). DB가 없거나 실패하면 빈 사전(발췌만 쓴다)."""
    driver = deps.get("driver")
    if not driver or not chunk_ids:
        return {}
    try:
        records, _, _ = driver.execute_query(
            "MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN c.chunk_id AS chunk_id, c.text AS text",
            ids=list(dict.fromkeys(chunk_ids)), database_=deps.get("database"), routing_="r")
    except Exception:  # noqa: BLE001 - 본문 구간 없이도 선별은 한다
        return {}
    return {r["chunk_id"]: r["text"] or "" for r in records}


def disease_surfaces(books: dict) -> set[str]:
    """승인 사전의 질병 이름·별칭(핵심어에서 뺀다: 질병명은 청크 어디에나 있어 구간을 고르는 데 쓸모가 없다)."""
    return {name for entry in books["disease"]["entries"] for name in (entry["name"], *entry.get("aliases", []))}


def priority_evidence(deps: dict) -> tuple[dict, dict] | None:
    """교육부 매뉴얼 50쪽의 '방역당국 지침 우선 적용' 원문을 (Finding, Citation)으로. 원문을 DB에서 읽어 글자 대조한다."""
    text = chunk_texts(deps, [PRIORITY_CHUNK]).get(PRIORITY_CHUNK, "")
    match = re.search(r"\s*".join(map(re.escape, PRIORITY_PHRASE.replace(" ", ""))), text)
    if not match:
        return None
    excerpt = match.group(0)
    finding = {"claim": f"[교육부 매뉴얼 PDF 50쪽 · 판단 기준: 지침 우선 적용] 원문: “{excerpt}”",
               "chunk_ids": [PRIORITY_CHUNK], "action_ids": [], "disease_ids": [], "concept_ids": []}
    citation = {"document_id": PRIORITY_CHUNK.split(":", 1)[0], "chunk_id": PRIORITY_CHUNK, "pdf_page": 50,
                "printed_page": None, "excerpt": excerpt}
    return finding, citation


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
    procedure = bool(PROCEDURE_RE.search(key))
    legal = bool(LEGAL_RE.search(key)) and not procedure
    school_duty = bool(SCHOOL_DUTY_RE.search(key)) and not legal and not procedure
    if legal:
        intents.append("LEGAL")
    disease_fact = any(c in key for c in DISEASE_FACT) and not any(c in key for c in SCHOOL_ACTION_CUES)
    if disease_fact:
        intents.append("DISEASE")
    intents.append("ACTION")                         # 병명 없는 학교 업무도 Action(일반 대화로 보내지 않음)
    if school_duty:
        intents.append("LEGAL")                      # 학교 신고 의무 질문: 학교 문서 먼저, 법령은 보조(법적 확정 확인)
    primary = intents[0]
    # 보조 의도: 첫 Tool 외에 확인이 필요한 영역. 기본값으로 붙인 ACTION은 세지 않고, 학교 행동을 실제로 함께 물을 때만 넣는다.
    secondary = [i for i in dict.fromkeys(intents[1:]) if i != "ACTION"]
    if primary in ("LEGAL", "DISEASE") and any(c in key for c in ACTION_ASK):
        secondary.append("ACTION")
    # 학교 행동 질문 안에 병명과 질병 사실(잠복기 등)을 함께 물으면 질병 확인을 보조로 둔다.
    # '전파 차단을 위한 휴업'처럼 행동 문맥의 넓은 낱말로는 넣지 않는다(DISEASE_ASK는 좁은 목록).
    if (primary != "DISEASE" and "DISEASE" not in secondary and diseases
            and any(c in key for c in DISEASE_ASK)):
        secondary.append("DISEASE")
    return {"response_type": "DOMAIN", "intent": primary, "secondary": secondary, "diseases": diseases,
            "roles": roles, "confidence": "high" if primary != "ACTION" or roles or diseases else "medium"}


# ---------------------------------------------------------------- 노드

def build_main_graph(deps: dict, registry: dict[str, ToolCallable]):
    books = deps["books"]
    trace = deps.setdefault("main_trace", [])
    # 확인 항목 조회는 라우터 결과만 있으면 시작할 수 있어 Tool 실행과 동시에 돌린다(11/07 지연 개선). 한 번 실행에 하나.
    background: dict[str, Future] = {}
    pool = CHECK_POOL

    def log(step: str, **info):
        trace.append({"step": step, **info})
        listener = deps.get("on_event")             # 화면 진행 표시(11/07). 실패해도 그래프는 계속한다.
        if listener:
            try:
                listener(step, info)
            except Exception:  # noqa: BLE001, S110 - 화면 콜백 오류가 답변을 막지 않게 한다
                pass

    def normalize_input(state: MainState) -> dict:
        request = state["request"]
        log("normalize_input", request_id=request.get("request_id"), region=request["region"],
            allowed=len(request["allowed_document_ids"]))
        return {"tool_results": [], "tool_calls": [], "findings": [], "citations": [], "warnings": [],
                "tools_used": [], "search_rounds": 0, "unresolved": [], "skipped_tools": [], "next_tool": "",
                "next_reason": ""}

    def classify_scope(state: MainState) -> dict:
        """deps['router'](LLM)가 있으면 그 결정을 쓰고, 없거나 실패하면 규칙 분류기 결과를 쓴다. 둘 다 기록한다."""
        question = state["request"]["question"]
        rule = classify_question(question, books)
        result, mode, error, started = rule, "rule", None, time.perf_counter()
        router = deps.get("router")
        if router:
            try:
                result, mode = router(question, rule), "llm"
                if result["response_type"] == "DOMAIN":      # 질문 낱말로 분명한 확인 항목은 코드가 보탠다(11/07)
                    result["checks"] = augment_checks(question, result.get("situation", {}), result.get("checks", []))
            except Exception as exc:  # noqa: BLE001 - LLM 실패·형식 위반은 규칙 분류로 되돌린다
                mode, error = "rule_fallback", f"{type(exc).__name__}: {str(exc)[:120]}"
        brief = ("response_type", "intent", "secondary", "reason", "confidence")
        log("classify_scope", mode=mode, **{k: result[k] for k in brief if k in result},
            sub_questions=result.get("sub_questions", {}), search_queries=result.get("search_queries", {}),
            situation=result.get("situation", {}), checks=result.get("checks", []),
            rule={k: rule[k] for k in brief if k in rule},
            error=error, seconds=round(time.perf_counter() - started, 2))
        update = {"response_type": result["response_type"], "scope_reason": result.get("reason", "")}
        if result["response_type"] == "DOMAIN" and result.get("checks") and deps.get("driver"):
            situation = {**result.get("situation", {}), "_question": question}
            background["checks"] = pool.submit(gather_checks, list(result["checks"]), situation,
                                               state["request"]["allowed_document_ids"], deps)
        if result["response_type"] == "DOMAIN":
            update |= {"intent": result["intent"], "secondary_intents": result["secondary"],
                       "sub_questions": result.get("sub_questions", {}),
                       "search_queries": result.get("search_queries", {}),
                       "situation": result.get("situation", {}), "checks": result.get("checks", [])}
        return update

    def classify_intent(state: MainState) -> dict:
        log("classify_intent", intent=state["intent"], secondary=state.get("secondary_intents", []))
        return {}

    def select_tool(state: MainState) -> dict:
        first = not state["tool_results"] and not state["skipped_tools"]
        tool = INTENT_TOOL[state["intent"]] if first else state["next_tool"]
        reason = "질문의 주 의도" if first else state["next_reason"]
        registered = tool in registry
        log("select_tool", tool=tool, registered=registered, round=state["search_rounds"] + 1, reason=reason)
        if registered:
            return {"selected_tool": tool, "next_tool": ""}
        return {"selected_tool": tool, "next_tool": "", "skipped_tools": state["skipped_tools"] + [tool],
                "warnings": state["warnings"] + [
                    (f"선택한 Tool '{tool}'이 메인 그래프에 연결되지 않아 호출하지 않았다(연결 오류, 호출 0회). "
                     "다른 Tool로 대신 답하지 않는다.")]}

    def execute_tool(state: MainState) -> dict:
        request, tool = state["request"], state["selected_tool"]
        task = TOOL_TASKS[tool] + (f" (요청 힌트: {request['task']})" if request.get("task") else "")
        if state["tool_results"]:
            task += f" (앞선 호출 {state['tools_used']}에서 남은 부분: {state['next_reason'][:120]})"
        # 하위 질문은 복합 질문(의도 2개 이상)일 때만 Tool에 넘긴다(원 질문은 task에 남긴다). 의도가 하나면 원 질문 그대로.
        # 질병 Tool은 어미·일상어를 직접 처리한다(송수림 a72aef8, 11/06 확인).
        subs = state.get("sub_questions", {})
        question = (subs.get(tool) if len(subs) > 1 else None) or request["question"]
        if question != request["question"]:
            task += f" (원 질문: {request['question']})"
        doc_query = state.get("search_queries", {}).get(tool) if tool in DOC_QUERY_TOOLS else None
        if doc_query:                                             # 11/07 ②: 학교 행동 Tool의 multi-query
            task += f" (문서 용어 질문: {doc_query.replace(')', ' ')})"
        tool_input: ToolInput = {"question": question, "task": task, "region": request["region"],
                                 "allowed_document_ids": list(request["allowed_document_ids"])}
        if request.get("preferred_document_ids"):
            tool_input["preferred_document_ids"] = list(request["preferred_document_ids"])
        context = list(request.get("context_entity_ids", []))
        for finding in state["findings"]:                         # 앞선 Tool이 확인한 질병·개념을 다음 Tool에 넘긴다
            context += [f"Disease:{d}" for d in finding["disease_ids"]]
            context += [f"ActionConcept:{c}" for c in finding["concept_ids"]]
        if context:
            tool_input["context_entity_ids"] = list(dict.fromkeys(context))

        def invoke(name: str, payload: ToolInput, asked: str, companion: bool = False) -> tuple[ToolResult, dict]:
            started = time.perf_counter()
            error = None
            try:
                result = registry[name](payload)
                validate_tool_result(result, payload, expected_tool=name)
            except ContractError as exc:
                error = f"ToolResult 계약 위반: {exc}"
            except Exception as exc:  # noqa: BLE001 - Tool 내부 예외도 호출 실패로 기록한다
                error = f"Tool 실행 실패: {type(exc).__name__}: {exc}"
            if error:
                result = {"tool_name": name, "status": "ERROR", "findings": [], "citations": [], "graph_paths": [],
                          "warnings": [error]}
            call = {"request_id": request.get("request_id"), "tool": name, "status": result["status"],
                    "findings": len(result["findings"]), "citations": len(result["citations"]),
                    "graph_paths": len(result["graph_paths"]), "seconds": round(time.perf_counter() - started, 2),
                    "error": error, "sub_question": asked if asked != request["question"] else None,
                    "companion": companion}
            return result, call

        # 질병 동반 호출(11/08): 첫 호출에서 질문이 승인 질병(11개)을 말하면, 주 의도 Tool이 질병 Tool이 아니어도 질병 Tool을
        # 동시에 부른다. 학교 행동 Tool은 학교 문서(2023·2024) 행동 그래프 중심이라 2026 질병별 지침 근거(전염기간·복귀 기준 등)가
        # 후보에 들지 않았다(지침 전용 질문 측정, notebooks/school/평가결과/20261108). 동시 실행이라 대기 시간은 거의 늘지 않고,
        # 실제 호출이므로 tools_used·search_rounds에 센다. 결과는 같은 DB 대조·근거 선별을 거친다.
        companion = None
        # 공문·절차·메뉴 입력처럼 절차만 묻는 질문은 질병 정보가 필요 없다(라우팅 규칙과 같다). 골든 S-A04 '홍역 … 신고 공문을
        # 어떻게 보내나요?'에서 동반 근거가 섞여 정답(경기도 211·212쪽)이 빠졌다(11/08 골든 비교).
        if (not state["tool_results"] and tool != COMPANION_TOOL and COMPANION_TOOL in registry
                and named_diseases(request["question"], books)
                and not PROCEDURE_RE.search(compare_key(request["question"]))):
            companion = COMPANION_TOOL
        if companion:
            companion_question = (subs.get(companion) if len(subs) > 1 else None) or request["question"]
            companion_input: ToolInput = {**tool_input, "question": companion_question,
                                          "task": TOOL_TASKS[companion] + " (질문 속 질병의 최신 지침 근거, 동반 호출)"
                                          + (f" (원 질문: {request['question']})"
                                             if companion_question != request["question"] else "")}
            companion_input.pop("context_entity_ids", None)
            if context:
                companion_input["context_entity_ids"] = list(dict.fromkeys(context))
            side = COMPANION_POOL.submit(invoke, companion, companion_input, companion_question, True)
        result, call = invoke(tool, tool_input, question)
        log("execute_tool", **call)
        results, calls, used = [result], [call], [tool]
        if companion:
            side_result, side_call = side.result()
            log("execute_tool", **side_call)
            results, calls, used = results + [side_result], calls + [side_call], used + [companion]
        return {"tool_input": tool_input, "tool_results": state["tool_results"] + results,
                "tool_calls": state["tool_calls"] + calls, "tools_used": state["tools_used"] + used,
                "search_rounds": state["search_rounds"] + len(used)}

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
        """근거 충분성 판정 + 다음 Tool 결정. ANSWERED면 끝, 아니면 신호가 가리키는 부르지 않은 Tool을 한도 안에서 고른다."""
        results, rounds = state["tool_results"], state["search_rounds"]
        status_of = {r["tool_name"]: r["status"] for r in results}
        tried = set(status_of) | set(state["skipped_tools"])
        primary = INTENT_TOOL[state["intent"]]
        # 신호 1: 보조 의도, 신호 2: '[미해결]' 경고가 이름으로 가리키는 Tool(첫 Tool 자신은 제외)
        needs: list[tuple[str, str]] = [(INTENT_TOOL[i], f"질문의 일부({i})") for i in state.get("secondary_intents", [])]
        for warning in state["unresolved"]:
            needs += [(tool, warning.removeprefix(UNRESOLVED).strip()) for tool in sorted(TOOL_NAMES) if tool in warning]
        needs = [(tool, why) for tool, why in dict.fromkeys(needs) if tool != primary]
        needs = list({tool: (tool, why) for tool, why in reversed(needs)}.values())[::-1]   # Tool별 첫 사유만
        # 어떤 Tool이 OK로 답했으면 그 Tool을 가리키던 [미해결]은 해소된 것으로 본다.
        # 이미 부른 Tool을 가리키는 [미해결]은 여기서 정리한다: OK면 해소, 실패면 아래 '호출했지만…' 한 줄로 대신한다.
        # 자기 자신을 가리키는 경고는 정리하지 않는다(같은 Tool은 다시 부르지 않으므로 미해결로 남는다).
        issuer = {w: r["tool_name"] for r in results for w in r["warnings"]}
        open_unresolved = [w for w in state["unresolved"]
                           if not any(tool in w and tool in status_of and tool != issuer.get(w) for tool in TOOL_NAMES)]
        pending = [(tool, why) for tool, why in needs if tool not in tried]
        # 넘기기(11/08): 주 의도 Tool이 근거 없음이고 거절이 아니면, 아직 안 부른 학교 행동 Tool을 한 번 부른다.
        primary_result = next((r for r in results if r["tool_name"] == primary), None)
        declined = bool(primary_result) and any(m in w for w in primary_result["warnings"] for m in DECLINE_MARKERS)
        fallback = (primary != FALLBACK_TOOL and status_of.get(primary) == "NO_EVIDENCE" and not declined
                    and FALLBACK_TOOL in registry and FALLBACK_TOOL not in tried)
        # 주 의도 Tool이 근거를 못 냈거나 연결되지 않았는데 다른 Tool만 답했다면 질문의 핵심이 빠진 것이다.
        # 단, 넘기기로 부른 학교 행동 Tool이 근거를 냈으면 그 근거가 주 의도 질문(원 질문)에 대한 것이므로 빠진 것으로 보지 않는다.
        fell_back = (primary != FALLBACK_TOOL and FALLBACK_TOOL not in {tool for tool, _ in needs}
                     and status_of.get(FALLBACK_TOOL) in ("OK", "PARTIAL"))
        primary_unmet = status_of.get(primary) in ("NO_EVIDENCE", "ERROR", None) and not fell_back

        def partial_result(result: ToolResult) -> bool:
            if result["status"] != "PARTIAL":
                return False
            own = [w for w in result["warnings"] if w.startswith(UNRESOLVED)]
            return not own or any(w in open_unresolved for w in own)   # 표시 없는 PARTIAL은 Tool 판단을 존중

        # 동반 호출(질병 Tool) 결과는 보조 근거다: 그 Tool의 PARTIAL·ERROR가 질문 전체의 상태를 낮추지 않는다(근거 선별이 다시 본다).
        companions = {c["tool"] for c in state["tool_calls"] if c.get("companion")}
        own_results = [r for r in results if r["tool_name"] not in companions]
        if not results:
            status = "INSUFFICIENT_EVIDENCE" if pending else "ERROR"
        elif state["findings"]:
            unmet = [tool for tool, _ in needs if status_of.get(tool) != "OK"]
            partial = any(partial_result(r) for r in own_results) or open_unresolved or unmet or primary_unmet
            status = "PARTIAL" if partial else "ANSWERED"
        elif any(r["status"] == "ERROR" for r in own_results):
            status = "ERROR"
        else:
            status = "INSUFFICIENT_EVIDENCE"

        next_tool, next_reason = "", ""
        if status != "ANSWERED" and rounds < MAX_TOOL_CALLS and pending:
            next_tool, next_reason = pending[0]
        elif status != "ANSWERED" and rounds < MAX_TOOL_CALLS and fallback:
            next_tool = FALLBACK_TOOL
            next_reason = f"주 의도 Tool({primary})이 근거를 찾지 못해 허용 문서 전체에서 다시 찾는다"
        unresolved = list(open_unresolved)
        if not next_tool:
            if state["findings"] and primary_unmet and primary not in state["skipped_tools"]:
                unresolved.append(f"{UNRESOLVED} 질문의 주 의도({primary})에서 근거를 찾지 못했다. "
                                  "아래 내용은 질문의 다른 부분에 대한 근거다.")
            for tool, why in needs:
                if status_of.get(tool) in ("NO_EVIDENCE", "ERROR"):
                    unresolved.append(f"{UNRESOLVED} {tool}를 호출했지만 근거를 찾지 못했다: {why}")
                elif tool in state["skipped_tools"]:
                    unresolved.append(f"{UNRESOLVED} {tool}가 연결되지 않아 확인하지 못했다: {why}")
                elif tool not in status_of:
                    unresolved.append(f"{UNRESOLVED} 호출 한도({MAX_TOOL_CALLS}회)로 {tool} 확인을 하지 못했다: {why}")
            if not results and status == "INSUFFICIENT_EVIDENCE":
                status = "ERROR"                                   # 연결된 Tool이 하나도 없었다
        log("evaluate_evidence", status=status, rounds=rounds, unresolved=len(unresolved),
            resolved=len(state["unresolved"]) - len(open_unresolved), next_tool=next_tool or None,
            decision="재검색" if next_tool else "종료")
        return {"status": status, "unresolved": list(dict.fromkeys(unresolved)), "next_tool": next_tool,
                "next_reason": next_reason}

    def check_facts(state: MainState) -> dict:
        """확인 항목의 판단 기준 원문을 찾아 근거 앞에 더한다(읽기 전용). 항목이 없거나 실패하면 그대로 둔다."""
        checks = state.get("checks") or []
        if not checks or not deps.get("driver") or state["status"] == "ERROR" and not state["tool_results"]:
            log("check_facts", mode="skip", checks=checks)
            return {"pinned": [], "facts": []}
        try:
            future = background.pop("checks", None)
            got = future.result(timeout=20) if future else gather_checks(
                checks, {**(state.get("situation") or {}), "_question": state["request"]["question"]},
                state["request"]["allowed_document_ids"], deps)
        except Exception as exc:  # noqa: BLE001 - 확인 항목 조회 실패는 답변을 막지 않는다
            log("check_facts", mode="error", checks=checks, error=f"{type(exc).__name__}: {str(exc)[:120]}")
            return {"pinned": [], "facts": []}
        have = {c["chunk_id"] for c in state["citations"]}
        new_cits = [c for c in got["citations"] if c["chunk_id"] not in have]
        pinned = got["pinned"]
        status = state["status"]
        if got["findings"] and status in ("INSUFFICIENT_EVIDENCE", "ERROR"):
            status = "PARTIAL"                 # Tool은 못 찾았지만 판단 기준 원문은 있다(선별 단계가 충분성을 다시 본다)
        log("check_facts", mode="db", checks=checks, found={CHECK_LABELS.get(k, k): v for k, v in got["found"].items()},
            facts=[f["text"] for f in got["facts"]], added=len(new_cits), seconds=got["seconds"])
        return {"findings": got["findings"] + state["findings"], "citations": new_cits + state["citations"],
                "pinned": pinned, "facts": got["facts"], "status": status,
                "pin_roles": {c: "ROLE" for c in got["found"].get("ROLE_TASKS", [])}}

    def select_evidence(state: MainState) -> dict:
        """근거 재순위·충분성 판정(11/07 ②). deps['reranker']가 없거나 실패하면 근거를 그대로 둔다."""
        reranker = deps.get("reranker")
        pinned = [c for c in state.get("pinned", []) if c in {x["chunk_id"] for x in state["citations"]}]
        if not reranker or state["status"] not in ("ANSWERED", "PARTIAL") or not state["findings"]:
            log("select_evidence", mode="skip", reason="재순위기 없음" if not reranker else "근거 없음")
            return {"picks": {}}
        request = state["request"]
        subs = state.get("sub_questions", {})
        parts = list(subs.values()) if len(subs) > 1 else [request["question"]]
        titles = {doc: doc_label(doc) for doc in DOC_TITLES}
        situation = state.get("situation") or {}
        started = time.perf_counter()
        # 본문 일치 구간(11/08): 카드 청크의 본문 전체를 읽어 질문 핵심어가 든 구간을 카드에 더한다(읽기 전용, 쿼리 1회).
        texts = chunk_texts(deps, [c["chunk_id"] for c in state["citations"][:MAX_CARDS]])
        terms = key_terms([request["question"], *parts, *(situation.get("asks") or [])], weak=disease_surfaces(books))
        try:
            result = reranker(request["question"], parts, state["findings"], state["citations"], titles,
                              situation=situation, pinned=pinned, facts=state.get("facts") or [],
                              texts=texts, terms=terms)
        except Exception as exc:  # noqa: BLE001 - LLM 실패는 재순위 없이 진행한다
            log("select_evidence", mode="fallback", error=f"{type(exc).__name__}: {str(exc)[:120]}",
                seconds=round(time.perf_counter() - started, 2))
            return {"picks": {}}
        before = (len(state["findings"]), len(state["citations"]))
        unresolved = list(state["unresolved"])
        warnings = state["warnings"]
        order = list(dict.fromkeys(result["order"] + pinned))       # 판단 기준 근거는 선별 뒤에도 남긴다
        if not result["order"]:
            # Tool 경고는 버려진 Finding에 대한 안내라 남기지 않는다(답변에 내부 문구가 노출되던 문제, 11/07 A03).
            status, findings, citations, warnings = "INSUFFICIENT_EVIDENCE", [], [], []
            unresolved.insert(0, f"{UNRESOLVED} 근거 선별: 검색한 자료 중 이 질문에 직접 답하는 근거를 찾지 못했다.")
        else:
            findings, citations = filter_evidence(state["findings"], state["citations"], order)
            status = state["status"]
            gaps = [c for c in result["coverage"] if c["status"] != "ANSWERED"]
            if gaps:
                status = "PARTIAL"
                unresolved += [f"{UNRESOLVED} 근거에서 확인하지 못한 부분: {c['missing']}" for c in gaps]
            elif pinned:
                # 판단 기준 원문을 더한 뒤 선별 단계가 하위 질문마다 '답함'이라고 판정했으면, Tool 단계의 미해결(다른 Tool이
                # 못 찾음 등)은 그 원문으로 채워진 것이다(11/07 P0: 기준 원문으로 답할 수 있는데 '일부 확인'으로 끝나던 문제).
                status, unresolved = "ANSWERED", []
        log("select_evidence", mode="llm", cards=result["cards"], before=before,
            candidates=[c["chunk_id"] for c in state["citations"]][:result["cards"]],
            after=(len(findings), len(citations)), status=status, coverage=result["coverage"],
            picks=[{"chunk_id": c, **{k: v for k, v in result["picks"][c].items() if k != "card"}}
                   for c in result["order"]],
            invalid=result["invalid"], seconds=round(time.perf_counter() - started, 2))
        picks = dict(result["picks"])
        for chunk_id in pinned:
            picks.setdefault(chunk_id, {"role": (state.get("pin_roles") or {}).get(chunk_id, "RULE"), "part": 1,
                                        "quotes": []})
        # 판본 우선 규칙(11/08): 고른 근거에 질병관리청 지침과 학교 문서가 함께 있으면, 학교 문서가 방역당국 지침을 우선하라고
        # 적은 원문(교육부 매뉴얼 50쪽)을 판단 기준으로 붙인다. 답변은 두 기준이 다를 때 2026 지침을 결론에 둔다.
        docs = {c["document_id"] for c in citations}
        if (any(d.startswith("kdca_") for d in docs) and docs & SCHOOL_DOCS
                and PRIORITY_CHUNK.split(":", 1)[0] in request["allowed_document_ids"]
                and PRIORITY_CHUNK not in {c["chunk_id"] for c in citations}):
            rule = priority_evidence(deps)
            if rule:
                findings, citations = findings + [rule[0]], citations + [rule[1]]
                picks[PRIORITY_CHUNK] = {"role": "RULE", "part": 1, "quotes": [rule[1]["excerpt"]]}
        return {"findings": findings, "citations": citations, "status": status, "picks": picks,
                "warnings": warnings, "unresolved": list(dict.fromkeys(unresolved))}

    def finish(state: MainState) -> dict:
        """템플릿 답변을 만들고, deps['answer_writer']가 있으면 검사를 통과한 다듬은 답변으로 바꾼다(11/07)."""
        output = finalize(state)
        writer = deps.get("answer_writer")
        if not writer or output["response_type"] != "DOMAIN" or output["status"] not in ("ANSWERED", "PARTIAL"):
            log("compose_answer", mode="template", reason="다듬기 대상 아님" if writer else "작성기 없음")
            return {"final_result": output}
        started = time.perf_counter()
        scenario = scenario_chunks(deps, [c["chunk_id"] for c in output["citations"]])
        try:
            written = writer(state["request"]["question"], output, books, picks=state.get("picks") or {},
                             on_line=deps.get("on_answer_line"), scenario=scenario,
                             context={"situation": state.get("situation") or {}, "facts": state.get("facts") or []})
        except Exception as exc:  # noqa: BLE001 - LLM 실패는 템플릿으로 되돌린다
            written = {"answer": None, "problems": [f"작성기 실패: {type(exc).__name__}"], "draft": ""}
        polished = written["answer"]
        log("compose_answer", mode="llm" if polished else "template_fallback", problems=written["problems"],
            dropped=written.get("dropped", []), moved=written.get("moved", []), marked=written.get("marked", []),
            attempts=written.get("attempts"), added=written.get("added", []),
            draft=written["draft"], template=output["answer"], seconds=round(time.perf_counter() - started, 2))
        return {"final_result": {**output, "answer": polished} if polished else output}

    def compose_answer(state: MainState) -> dict:
        return finish(state)

    def partial_or_insufficient(state: MainState) -> dict:
        return finish(state)

    graph = StateGraph(MainState)
    for name, fn in (("normalize_input", normalize_input), ("classify_scope", classify_scope),
                     ("classify_intent", classify_intent), ("select_tool", select_tool),
                     ("execute_tool", execute_tool), ("merge_and_validate", merge_and_validate),
                     ("evaluate_evidence", evaluate_evidence), ("check_facts", check_facts),
                     ("select_evidence", select_evidence),
                     ("compose_answer", compose_answer),
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
    graph.add_conditional_edges("evaluate_evidence", lambda s: "again" if s.get("next_tool") else "select",
                                {"again": "select_tool", "select": "check_facts"})
    graph.add_edge("check_facts", "select_evidence")
    graph.add_conditional_edges("select_evidence", lambda s: "full" if s["status"] == "ANSWERED" else "partial",
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
              "moleg_infectious_disease_act_21521": ("감염병예방법", "감염병예방법"),
              "kdca_covid19_2026": ("질병관리청 코로나19 관리지침", "질병청 코로나19"),
              "kdca_respiratory_2026": ("질병관리청 호흡기감염병 관리지침", "질병청 호흡기"),
              "kdca_flu_facility_2026": ("질병관리청 인플루엔자 관리지침(학교용)", "질병청 인플루엔자"),
              "kdca_hfmd_2026": ("질병관리청 수족구병 관리지침", "질병청 수족구병"),
              "kdca_vaccine_preventable_2026": ("질병관리청 예방접종 대상 감염병 지침", "질병청 예방접종")}
# 판본(11/08). 질병관리청 질병별 지침은 매년 개정되는 2026년판, 학교 문서는 그보다 오래된 판본이다(승우 님 확인:
# 교육부 매뉴얼 2023·경기도 가이드북 2024). 근거 선별·답변 작성에 판본을 보여 주고, 같은 항목을 다르게 말하면 2026 지침을
# 결론에 둔다. 교육부 매뉴얼 50쪽도 "방역당국에서 별도의 관리지침을 제시할 경우에는 그 지침을 우선 적용함"이라고 적는다.
DOC_EDITIONS = {"moe_school_manual_v3": "제3차 개정판, 2023", "gyeonggi_school_guide_2025": "2024",
                "kdca_measles_2026": "2026", "kdca_diagnosis_reporting_2026": "2026", "kdca_covid19_2026": "2026",
                "kdca_respiratory_2026": "2026", "kdca_flu_facility_2026": "2026", "kdca_hfmd_2026": "2026",
                "kdca_vaccine_preventable_2026": "2026", "moleg_infectious_disease_act_21521": "2026.10.8 시행"}
SCHOOL_DOCS = frozenset({"moe_school_manual_v3", "gyeonggi_school_guide_2025"})
# 학교 문서가 질병관리청 지침을 우선하라고 적은 원문(교육부 매뉴얼 PDF 50쪽). 근거에 두 출처가 함께 있으면 판단 기준으로 붙인다.
PRIORITY_CHUNK = "moe_school_manual_v3:p0050:b01"
PRIORITY_PHRASE = "방역당국에서 별도의 관리지침을 제시할 경우에는 그 지침을 우선 적용함"


def doc_label(document_id: str) -> str:
    """'질병관리청 수족구병 관리지침(2026)'처럼 문서 이름에 판본을 붙인다."""
    name = DOC_TITLES.get(document_id, (document_id,))[0]
    edition = DOC_EDITIONS.get(document_id)
    return f"{name}({edition})" if edition else name
OFF_STAGE_TAIL = " — 원문 배치 단계가 질문 단계와 다름"
HEAD_RE = re.compile(r"^\[(?P<head>[^\]]*)\]\s*(?P<body>.*)$", re.DOTALL)
ROLE_RE = re.compile(r"^(?:원문이 제시한 담당 예시|원문이 명시한 담당):\s*(?P<role>.+?)\s*—\s*(?P<text>.+)$", re.DOTALL)
NO_ROLE_RE = re.compile(r"^원문에 담당 미기재\s*—\s*(?P<text>.+)$", re.DOTALL)
QUOTE_RE = re.compile(r"^원문:\s*“(?P<text>.*)”$", re.DOTALL)
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
    # 사용자 화면에는 확인하지 못한 부분만 쉬운 말로 보인다. Tool 내부 경고(원문 인용 Finding·답 카드·원문 강도 안내 등)는
    # warnings·실행 기록에만 남긴다(11/07 P0-5: 템플릿 답변에 내부 문구가 노출되던 문제).
    notes = list(dict.fromkeys(user_note(w) for w in warnings if w.startswith(UNRESOLVED)))[:3]
    if notes:
        lines += ["", "※ 확인하지 못한 부분:", *(f"- {n}" for n in notes)]
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
