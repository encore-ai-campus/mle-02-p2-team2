"""B09: SchoolGraph - 입력 검사 → 범위 확인 → 청크 검색 → 고정 그래프 확장 → Finding/Citation 조립 → DB 대조.

설계 근거(그래프분석과검색 수업 S05·S06·S07)
- 38-5 agent/ToolNode의 '도구를 감싸 흐름으로 묶기'를 쓰되, 자유 도구 반복 대신 **고정 순서**로 실행한다.
  '청크 검색을 먼저 한다'는 프로젝트 정책을 프롬프트가 아니라 그래프 구조로 보장하기 위해서다.
- 37-6 rows/@tool처럼 목적이 정해진 매개변수 Cypher(search.py)만 호출한다. allowed_document_ids는 요청
  State에서만 넣고 모델이 바꿀 수 없다(이 그래프는 LLM을 쓰지 않는다).
- 38-5 route_graph의 아이디어는 '그래프 내부' 분기로만 쓴다(범위 밖 질문 차단, 비교 질문 확장).
  Orchestrator의 도메인 선택과 섞지 않는다.
- 도구 오류는 근거가 아니다(ToolNode handle_tool_errors 주의): DB 오류는 ERROR로, 결과 없음은
  INSUFFICIENT_EVIDENCE로 구분한다.
- 실행 추적(호출한 단계·검색어·청크 ID·건수)은 deps['trace']에 남기고 QueryOutput에는 넣지 않는다.

주장(claim)은 DB에서 읽은 행동·역할·basis·단계·조건만으로 만든다(모델 상식으로 보충하지 않음).
EXAMPLE 역할은 '담당 예시'로, 모의훈련 시나리오는 '시나리오 예시'로 claim에 남긴다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from index.school.dictionaries import compare_key, load_dictionaries
from rag.school.contract_local import (
    QueryInput,
    QueryOutput,
    empty_output,
    validate_query_input,
    validate_query_output,
)
from rag.school.search import (
    SearchError,
    compare_action_sources,
    expand_school_actions,
    get_action_evidence,
    search_school_chunks,
)
from rag.school.validation_local import verify_against_db

ROOT = Path(__file__).resolve().parents[3]
DOMAIN = "school"
DOC_LABELS = {"moe_school_manual_v3": "교육부 매뉴얼", "gyeonggi_school_guide_2025": "경기도 가이드북"}
STAGE_LABELS = {"PREVENTION": "예방단계", "RESPONSE_1": "대응 제1단계", "RESPONSE_2": "대응 제2단계",
                "RESPONSE_3": "대응 제3단계", "RECOVERY": "복구단계", "ALL_STAGES": "모든 단계",
                "NATIONAL_CRISIS": "국가위기 단계", "UNSPECIFIED": "단계 미기재"}
MODALITY_LABELS = {"MANDATORY": "의무", "RECOMMENDED": "권장", "DISCRETIONARY": "선택"}
# 질문 표현 → 원문 단계 섹션. 사용자 상황의 단계를 '판정'하는 것이 아니라 찾을 원문 범위를 좁히는 용도.
STAGE_HINTS = {
    "RESPONSE_1": ("유증상", "의심증상", "증상이있", "열이나", "발견되", "발견하", "발견시"),
    "RESPONSE_2": ("확진", "환자발생", "환자가생기", "환자가나", "진단받"),
    "RESPONSE_3": ("유행", "확산", "집단발생"),
    "RECOVERY": ("유행종료", "복구"),
}
COMPARE_HINTS = ("비교", "차이", "각각어떻게설명", "두문서", "매뉴얼과", "가이드북과")
# '각각 무엇을 해야/확인' 같은 넓은 질문이면 요청한 역할의 행동을 모두 돌려준다.
BROAD_HINTS = ("무엇을", "무엇", "뭘", "어떤일", "할일", "어떻게대응")
PARTICLES = ("에게서", "에게", "께서", "에서", "으로", "와", "과", "은", "는", "이", "가", "을", "를", "의",
             "도", "만", "께", "로", "에")
# 관련성 비교에서 뺄 흔한 2글자(학교 문서 어디에나 나옴).
GENERIC_BIGRAMS = {"학교", "학생", "감염", "염병", "교사", "교육", "육부", "매뉴", "뉴얼", "경기", "기도",
                   "가이", "이드", "드북", "설명", "각각", "어떻", "떻게", "하나", "나요", "해야", "에서", "에게"}
OUT_OF_SCOPE = {
    "진단·질병 판정은 서비스 범위 밖이다. 증상으로 질병을 판정하지 않으며 의료기관 진료가 필요하다.":
        ("판단해", "판정해", "진단해", "인지알려", "병인가요", "걸렸나요"),
    "NEIS 입력·제출 등 시스템 대행과 개인정보 처리는 서비스 범위 밖이다.":
        ("입력해", "대신입력", "대신등록", "제출해줘", "등록해줘", "대신해줘"),
}


class SchoolState(TypedDict, total=False):
    question: str
    region: str
    allowed_document_ids: list[str]
    task: NotRequired[str]
    preferred_document_ids: NotRequired[list[str]]
    context_entity_ids: NotRequired[list[str]]
    plan: dict
    hits: list[dict]
    actions: list[dict]
    domain: str
    status: str
    findings: list[dict]
    citations: list[dict]
    action_ids: list[str]
    warnings: list[str]
    answer: str


def default_deps() -> dict:
    from index.school.embed import make_embedder
    from index.school.neo4j_local import get_driver

    driver, database = get_driver(ROOT / ".env")
    embedder = make_embedder() if os.getenv("OPENAI_API_KEY") else None
    return {"driver": driver, "database": database, "embedder": embedder, "owns_driver": True}


def _trace(deps: dict, step: str, **info) -> None:
    if deps.get("trace") is not None:
        deps["trace"].append({"step": step, **info})


def _output(status: str, warnings: list[str], findings=(), citations=(), answer="") -> dict:
    findings = list(findings)
    action_ids = list(dict.fromkeys(a for f in findings for a in f["action_ids"]))
    return {"domain": DOMAIN, "status": status, "findings": findings, "citations": list(citations),
            "action_ids": action_ids, "warnings": list(dict.fromkeys(warnings)), "answer": answer}


# ---------------------------------------------------------------- 계획

def plan_question(question: str, books: dict) -> dict:
    """질문에 나온 학교 역할, 원문 단계 범위, 비교 여부, 범위 밖 여부."""
    key = compare_key(question)
    out_of_scope = [reason for reason, cues in OUT_OF_SCOPE.items() if any(c in key for c in cues)]
    masked = key
    for item in sorted(books["role"].get("excluded_mentions", []), key=lambda m: -len(m["mention"])):
        masked = masked.replace(compare_key(item["mention"]), "#")      # '보건소'가 '보건'으로 잡히지 않게
    tokens = {strip_particle(compare_key(w)) for w in question.split()}
    names = sorted(((compare_key(n), e["role_id"]) for e in books["role"]["entries"]
                    for n in [e["name"], *e.get("aliases", [])] if len(compare_key(n)) >= 2),
                   key=lambda x: -len(x[0]))
    found: list[tuple[int, str]] = []
    for name, role_id in names:
        position = masked.find(name)
        # 2글자 별칭('보건', '담임')은 낱말 전체일 때만 역할로 본다('보건실'은 보건교사가 아님).
        if position >= 0 and (len(name) >= 3 or name in tokens):
            found.append((position, role_id))
            masked = masked.replace(name, "#")
    roles = list(dict.fromkeys(role_id for _, role_id in sorted(found)))
    # 질문에 단계가 직접 적혀 있으면('대응 제1단계는') 그것을 우선한다. 없을 때만 상황 표현으로 고른다.
    named = [stage for stage, pattern in STAGE_PATTERNS.items() if re.search(pattern, key)]
    stages = named or [stage for stage, cues in STAGE_HINTS.items() if any(c in key for c in cues)]
    role_bigrams = set()
    for entry in books["role"]["entries"]:
        if entry["role_id"] in roles:
            for name in [entry["name"], *entry.get("aliases", [])]:
                role_bigrams |= bigrams(name)
    return {"roles": roles, "stages": stages or None, "compare": any(c in key for c in COMPARE_HINTS),
            "broad": bool(roles) and any(c in key for c in BROAD_HINTS),
            # '신고해야 하나요', '신고 대상인가요' - 법정 신고 의무를 묻는 질문(절차를 묻는 '어떻게 보내나요'는 아님)
            "legal_duty": bool(re.search(r"신고(해야|하여야|의무|대상인|대상이)", key)),
            "focus": sorted(bigrams(question) - role_bigrams - GENERIC_BIGRAMS),
            "out_of_scope": out_of_scope}


def strip_particle(token: str) -> str:
    for particle in PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def bigrams(text: str) -> set[str]:
    """비교용 2글자 묶음(cjk 분석기와 같은 단위). 낱말 경계를 넘는 묶음('에게 연락'→'게연')은 만들지 않는다."""
    pairs = set()
    for word in text.split():
        key = "".join(ch for ch in compare_key(word) if ch.isalnum())
        pairs |= {key[i:i + 2] for i in range(len(key) - 1)}
    return pairs


def relevant_actions(actions: list[dict], plan: dict, books: dict) -> list[dict]:
    """질문이 특정 행동을 물을 때 관련 행동만 남긴다(후보 선별일 뿐 의미 판정이 아니다).

    관련 = 표준 개념 이름이 질문의 특징 2글자와 2개 이상 겹치거나, 행동 문장이 3개 이상 겹침.
    넓은 역할 질문('담임·보건교사는 각각 무엇을')이면 거르지 않는다.
    """
    if plan["broad"]:
        return actions
    names = {e["concept_id"]: e["name"] for e in books["concept"]["entries"]}
    focus = set(plan["focus"])
    kept = []
    for action in actions:
        concept_hits = max((len(bigrams(names[c]) & focus) for c in action["concept_ids"] if c in names),
                           default=0)
        if concept_hits >= 2 or len(bigrams(action["action_text"]) & focus) >= 3:
            kept.append(action)
    return kept


# ---------------------------------------------------------------- 조립

def excerpt_of(text: str) -> str:
    """표 행 청크는 머리글을 뺀 행 원문(계약 v1.2 4-1), 문단 청크는 원문 전체."""
    last = text.rstrip("\n").split("\n")[-1]
    if last.startswith("|"):
        return last.strip().strip("|").strip()
    return text.strip()


def _is_scenario(hit: dict) -> bool:
    return "시나리오" in (hit.get("section") or "")


def build_claim(action: dict, hit: dict) -> str:
    doc = DOC_LABELS.get(action["document_id"], hit.get("title") or action["document_id"])
    where = f"{doc} PDF {hit['pdf_page']}쪽" + (", 모의훈련 시나리오 예시" if _is_scenario(hit) else "")
    stage = STAGE_LABELS.get(action["stage"], action["stage"])
    example = [r["name"] for r in action["roles"] if r["basis"] == "EXAMPLE"]
    explicit = [r["name"] for r in action["roles"] if r["basis"] == "EXPLICIT"]
    who = []
    if example:
        who.append(f"원문이 제시한 담당 예시: {', '.join(example)}")
    if explicit:
        who.append(f"원문이 명시한 담당: {', '.join(explicit)}")
    claim = f"[{where} · {stage}] {' / '.join(who) or '원문에 담당 미기재'} — {action['action_text']}"
    extras = []
    if action.get("when_text"):
        extras.append(f"조건: {action['when_text']}")
    if action.get("timing_text"):
        extras.append(f"시점: {action['timing_text']}")
    if action.get("modality") in MODALITY_LABELS:
        extras.append(f"원문 강도: {MODALITY_LABELS[action['modality']]}")
    return claim + (f" ({'; '.join(extras)})" if extras else "")


# ---------------------------------------------------------------- 원문 인용 Finding (행동이 없는 청크)

STAGE_PATTERNS = {"RESPONSE_1": r"1단계|단계1(?!\d)", "RESPONSE_2": r"2단계|단계2(?!\d)",
                  "RESPONSE_3": r"3단계|단계3(?!\d)", "RECOVERY": r"복구단계|4단계|단계4(?!\d)",
                  "PREVENTION": r"예방단계"}
EXAMPLE_ROLE_DOCUMENTS = {"moe_school_manual_v3", "gyeonggi_school_guide_2025"}   # 역할 배정이 예시인 문서
MAX_QUOTE_FINDINGS = 8


def _cells(hit: dict) -> list[str]:
    last = hit["text"].rstrip("\n").split("\n")[-1]
    return [c.strip() for c in last.strip().strip("|").split("|")] if last.startswith("|") else []


def chunk_stages(hit: dict) -> set[str]:
    """청크가 속한 원문 단계: 절 제목(문단) 또는 표 제목·앞 두 칸(표 행)에서 읽는다."""
    head = (hit.get("section") or "") + " " + " ".join(_cells(hit)[:2])
    head = re.sub(r"<br\s*/?>|\s", "", head)
    return {stage for stage, pattern in STAGE_PATTERNS.items() if re.search(pattern, head)}


def row_role(hit: dict, books: dict) -> dict | None:
    """표 행 첫 칸이 학교 역할이면 그 역할(사전 표준명)."""
    cells = _cells(hit)
    if not cells:
        return None
    from index.school.normalize import resolve_mention
    result = resolve_mention(cells[0], books["role"], "role_id")
    if result["status"] != "RESOLVED":
        return None
    entry = next(e for e in books["role"]["entries"] if e["role_id"] == result["id"])
    return {"role_id": entry["role_id"], "name": entry["name"]}


def best_excerpt(hit: dict, focus: set[str], role_pairs: set[str] = frozenset(),
                 window: int = 3) -> tuple[str, int]:
    """(발췌, 겹침 수). 표 행은 행 원문, 문단은 질문 특징·질문한 역할명과 가장 많이 겹치는 연속 1~3줄.

    점수가 같으면 더 긴 범위를 고른다(제목 한 줄만 발췌되지 않게). 발췌는 청크 원문의 부분 문자열이다.
    """
    if _cells(hit):
        excerpt = excerpt_of(hit["text"])
        return excerpt, len(bigrams(excerpt) & focus)
    lines = hit["text"].split("\n")
    best = ("", -1, -1)
    for size in range(1, window + 1):
        for start in range(len(lines) - size + 1):
            text = "\n".join(lines[start:start + size])
            pairs = bigrams(text)
            score = len(pairs & focus) + len(pairs & role_pairs)
            if score >= best[1] and (score > best[1] or len(text) > best[2]):
                best = (text, score, len(text))
    return best[0].strip(), max(best[1], 0)


def quote_findings(state: SchoolState, hits: list[dict], books: dict, skip: set[str]) -> list[dict]:
    """학교 행동 노드가 없는 청크에서 원문을 그대로 인용한 Finding 후보.

    - 질문 단계와 다른 단계의 청크는 뺀다. 단, 질문의 영문 핵심어(예: NEIS)를 담은 청크는
      '원문 배치 단계'를 밝히고 넣는다(S-T2: 'NEIS 보고는 제2단계 활동'이라는 근거가 답의 핵심).
    - 역할을 물었으면 다른 역할의 표 행은 뺀다. 예시 역할표 문서의 역할 행은 '담당 예시'로 표시한다.
    - 넓은 역할 질문이 아니면 질문 특징 2글자가 2개 이상 겹치는 발췌만 쓴다.
    """
    plan = state["plan"]
    focus = set(plan["focus"])
    explicit = [w.casefold() for w in re.findall(r"[A-Za-z]{2,}", state["question"])]
    role_pairs = set()
    for entry in books["role"]["entries"]:
        if entry["role_id"] in plan["roles"]:
            for name in [entry["name"], *entry.get("aliases", [])]:
                role_pairs |= bigrams(name)
    rows = []
    for hit in hits:
        if hit["chunk_id"] in skip or len(rows) >= MAX_QUOTE_FINDINGS:
            continue
        stages = chunk_stages(hit)
        off_stage = bool(plan["stages"] and stages and not stages & set(plan["stages"]))
        names_term = any(w in hit["text"].casefold() for w in explicit)
        if off_stage and not names_term:
            continue
        role = row_role(hit, books)
        if plan["roles"] and role and role["role_id"] not in plan["roles"]:
            continue
        excerpt, score = best_excerpt(hit, focus, role_pairs)
        if not excerpt or (not (plan["broad"] and role) and score < 2 and not names_term):
            continue
        rows.append({"hit": hit, "excerpt": excerpt, "role": role, "stages": stages, "off_stage": off_stage})
    return rows


def build_quote_claim(row: dict) -> str:
    hit = row["hit"]
    doc = DOC_LABELS.get(hit["document_id"], hit.get("title") or hit["document_id"])
    where = f"{doc} PDF {hit['pdf_page']}쪽" + (", 모의훈련 시나리오 예시" if _is_scenario(hit) else "")
    tags = []
    if row["stages"]:
        placed = ", ".join(STAGE_LABELS[s] for s in sorted(row["stages"]))
        tags.append(f"원문 배치: {placed}" + (" — 질문 단계와 다름" if row["off_stage"] else ""))
    if row["role"]:
        label = "원문이 제시한 담당 예시" if hit["document_id"] in EXAMPLE_ROLE_DOCUMENTS else "원문의 담당"
        tags.append(f"{label}: {row['role']['name']}")
    quote = re.sub(r"<br\s*/?>", " ", row["excerpt"]).replace("\n", " ")
    quote = quote if len(quote) <= 220 else quote[:217] + "…"
    return f"[{where}{' · ' + ' · '.join(tags) if tags else ''}] 원문: “{quote}”"


def compose(state: SchoolState, hits_by_id: dict[str, dict], books: dict | None = None) -> dict:
    plan, actions = state["plan"], state["actions"]
    preferred = set(state.get("preferred_document_ids") or [])
    role_order = {r: i for i, r in enumerate(plan["roles"])}

    def sort_key(a):
        first_role = min((role_order.get(r["role_id"], 99) for r in a["roles"]), default=99)
        return (a["document_id"] not in preferred, a["document_id"], first_role,
                a["step_order"] or 99, a["action_id"])

    findings, used_chunks = [], {}
    quotes = quote_findings(state, state["hits"], books, {a["chunk_id"] for a in actions}) if books else []
    for row in quotes:            # 본문·표 원문 인용을 먼저(검색 순위 순), 그다음 구조화된 학교 행동
        hit = row["hit"]
        findings.append({"claim": build_quote_claim(row), "chunk_ids": [hit["chunk_id"]], "action_ids": [],
                         "disease_ids": [], "concept_ids": []})
        used_chunks[hit["chunk_id"]] = {**hit, "_excerpt": row["excerpt"]}
    for action in sorted(actions, key=sort_key):
        hit = hits_by_id[action["chunk_id"]]
        findings.append({"claim": build_claim(action, hit), "chunk_ids": [action["chunk_id"]],
                         "action_ids": [action["action_id"]], "disease_ids": action["disease_ids"],
                         "concept_ids": action["concept_ids"]})
        used_chunks[action["chunk_id"]] = hit
    citations = [{"document_id": h["document_id"], "chunk_id": h["chunk_id"], "pdf_page": h["pdf_page"],
                  "printed_page": h["printed_page"], "excerpt": h.get("_excerpt") or excerpt_of(h["text"])}
                 for h in used_chunks.values()]
    if not findings:
        return _output("INSUFFICIENT_EVIDENCE", [
            "검색된 원문에서 질문에 해당하는 근거를 찾지 못했다(관련 청크만 있음).",
            "검색된 청크: " + ", ".join(h["chunk_id"] for h in state["hits"][:5])])
    warnings = []
    if quotes:
        warnings.append("원문 인용 Finding은 검색된 원문을 그대로 옮긴 것이다. 질문에 대한 해석·적용 판단은 포함하지 않는다.")
    if any(r["basis"] == "EXAMPLE" for a in actions for r in a["roles"]) or any(q["role"] for q in quotes):
        warnings.append("역할은 원문이 제시한 예시이며 학교 상황에 따라 조정할 수 있다(의무 배정 아님).")
    if any(q["off_stage"] for q in quotes):
        warnings.append("질문 단계와 다른 단계에 배치된 원문이 포함됐다(각 Finding에 원문 배치 단계를 표시).")
    if plan["stages"]:
        labels = ", ".join(STAGE_LABELS[s] for s in plan["stages"])
        warnings.append(f"질문 표현에 맞춰 원문의 {labels} 근거를 골랐다. 단계는 원문이 내용을 배치한 단계이며 "
                        "현재 상황의 단계를 판정한 것이 아니다.")
    status = "ANSWERED"
    if used_chunks and all(_is_scenario(h) for h in used_chunks.values()):
        status = "PARTIAL"
        warnings.append("근거가 모의훈련 시나리오(진한이 사례) 표뿐이다. 본문 지침의 근거는 아직 확인하지 못했다.")
    # 질문의 영문 핵심어(NEIS 등)가 검색된 원문 어디에도 없으면, 주변 근거를 답처럼 보이게 하지 않는다.
    texts = " ".join(h["text"] for h in state["hits"]).casefold()
    absent = [w for w in re.findall(r"[A-Za-z]{2,}", state["question"]) if w.casefold() not in texts]
    if absent:
        status = "PARTIAL"
        warnings.append(f"질문의 핵심어 {absent}에 대한 원문을 찾지 못했다. "
                        "아래 행동은 주변 근거일 뿐 그 질문의 답이 아니다.")
    if plan.get("legal_duty"):
        # 학교 문서의 '신고가 필요한 법정감염병' 목록은 작성 시점 기준이다. 현행 의무 판단은 법령 쪽(prevention_legal) 몫.
        status = "PARTIAL"
        warnings.append("법정 신고 의무 여부는 학교 문서만으로 판단하지 않는다. 학교 문서의 목록·절차만 인용했으며 "
                        "현행 감염병예방법·신고기준 확인이 필요하다(목록에 없다고 '의무 없음'으로 단정하지 않음).")
    covered = {r["role_id"] for a in actions for r in a["roles"]} | {q["role"]["role_id"] for q in quotes
                                                                       if q["role"]}
    missing = [r for r in plan["roles"] if r not in covered]
    if any(q["off_stage"] for q in quotes) and status == "ANSWERED":
        status = "PARTIAL"
    if missing:
        status = "PARTIAL"
        warnings.append(f"질문의 역할 중 원문 근거를 찾지 못한 역할: {', '.join(missing)}")
    return _output(status, warnings, findings, citations, summarize(findings))


def summarize(findings: list[dict]) -> str:
    """단독 시연용 요약(계약상 answer). 최종 답변은 Orchestrator가 findings로 다시 구성한다."""
    return "\n".join(f"- {f['claim']}" for f in findings)


# ---------------------------------------------------------------- 그래프

def build_graph(deps: dict | None = None):
    """내부용 컴파일 그래프. input_schema=QueryInput, output_schema=QueryOutput로 계약 키만 드나든다."""
    deps = deps if deps is not None else default_deps()
    books = deps.get("books") or load_dictionaries()
    driver, database = deps["driver"], deps.get("database")

    def guard(state: SchoolState) -> dict:
        plan = plan_question(state["question"], books)
        _trace(deps, "plan", **{k: v for k, v in plan.items()})
        if not state["allowed_document_ids"]:
            return {"plan": plan, **_output("INSUFFICIENT_EVIDENCE", ["허용된 문서가 없다(allowed_document_ids=[])."])}
        if plan["out_of_scope"]:
            return {"plan": plan, **_output("INSUFFICIENT_EVIDENCE", plan["out_of_scope"])}
        return {"plan": plan}

    def search(state: SchoolState) -> dict:
        try:
            result = search_school_chunks(driver, state, books, embedder=deps.get("embedder"),
                                          mode=deps.get("search_mode", "hybrid"), k=deps.get("k", 8),
                                          database=database)
        except SearchError as exc:
            _trace(deps, "search", error=str(exc))
            return _output("ERROR", [f"청크 검색 실패(DB 조회 오류): {exc}"])
        _trace(deps, "search", mode=result["mode"], terms=result["terms"],
               chunk_ids=[h["chunk_id"] for h in result["hits"]])
        plan = state["plan"]
        if len(plan["roles"]) > 1:
            # 여러 역할을 '각각' 묻는 질문: 역할마다 보조 검색(질문 + 그 역할 표기)을 한 번 더 해서
            # 한 역할의 근거가 상위 결과를 독차지하지 않게 한다. 같은 허용 문서·같은 검색 함수.
            seen = {h["chunk_id"] for h in result["hits"]}
            entries = {e["role_id"]: e for e in books["role"]["entries"]}
            for role_id in plan["roles"]:
                question = state["question"]
                for other in plan["roles"]:
                    if other != role_id:
                        for name in sorted([entries[other]["name"], *entries[other].get("aliases", [])],
                                           key=len, reverse=True):
                            if len(name) >= 3:
                                question = question.replace(name, "")
                try:
                    extra = search_school_chunks(driver, {**state, "question": question}, books,
                                                 embedder=deps.get("embedder"),
                                                 mode=deps.get("search_mode", "hybrid"), k=6, database=database)["hits"]
                except SearchError as exc:
                    _trace(deps, "role_search", error=str(exc))
                    return _output("ERROR", [f"역할별 보조 검색 실패(DB 조회 오류): {exc}"])
                added = [h for h in extra if h["chunk_id"] not in seen]
                seen |= {h["chunk_id"] for h in added}
                result["hits"] += added
                _trace(deps, "role_search", role=role_id, added=[h["chunk_id"] for h in added])
        if not result["hits"]:
            return {"hits": [], **_output("INSUFFICIENT_EVIDENCE", ["허용된 문서에서 관련 원문 청크를 찾지 못했다."])}
        return {"hits": result["hits"]}

    def expand(state: SchoolState) -> dict:
        plan, allowed = state["plan"], state["allowed_document_ids"]
        hits = {h["chunk_id"]: h for h in state["hits"]}
        try:
            actions = expand_school_actions(driver, list(hits), allowed, stages=plan["stages"],
                                            role_ids=plan["roles"] or None, database=database)
            found = len(actions)
            actions = relevant_actions(actions, plan, books)
            _trace(deps, "relevance", found=found, kept=len(actions), focus=plan["focus"])
            if plan["compare"] and actions:
                concepts = sorted({c for a in actions for c in a["concept_ids"]})
                others = compare_action_sources(driver, concepts, allowed,
                                                exclude_action_ids=[a["action_id"] for a in actions],
                                                database=database)
                if plan["stages"]:
                    others = [a for a in others if a["stage"] in plan["stages"]]
                others = relevant_actions(others, plan, books)
                for row in get_action_evidence(driver, [a["action_id"] for a in others], allowed, database):
                    hits.setdefault(row["chunk_id"], {k: v for k, v in row.items() if k != "action_id"})
                actions += others
        except SearchError as exc:
            _trace(deps, "expand", error=str(exc))
            return _output("ERROR", [f"학교 행동 조회 실패(DB 조회 오류): {exc}"])
        _trace(deps, "expand", actions=[a["action_id"] for a in actions])
        result = compose({**state, "actions": actions, "hits": list(hits.values())}, hits, books)
        _trace(deps, "compose", findings=len(result["findings"]),
               quote_chunks=[f["chunk_ids"][0] for f in result["findings"] if not f["action_ids"]])
        return {"actions": actions, "hits": list(hits.values()), **result}

    def verify(state: SchoolState) -> dict:
        if not deps.get("verify_db", True):
            return {}
        try:
            report = verify_against_db(state, driver, database)
        except Exception as exc:  # noqa: BLE001 - 대조를 못 했으면 그 Finding은 쓰지 않는다
            _trace(deps, "verify", error=type(exc).__name__)
            return _output("ERROR", [f"Neo4j 대조 실패({type(exc).__name__}) - 대조하지 못한 Finding은 반환하지 않음"])
        _trace(deps, "verify", **{k: len(v) for k, v in report.items()})
        if not report["bad_findings"] and not report["bad_citations"]:
            return {}
        keep = [f for i, f in enumerate(state["findings"]) if i not in report["bad_findings"]]
        used = {c for f in keep for c in f["chunk_ids"]}
        warnings = state["warnings"] + [f"DB 대조 실패로 제외: {state['findings'][i]['action_ids']} {p}"
                                        for i, p in report["bad_findings"].items()]
        if not keep:
            return _output("INSUFFICIENT_EVIDENCE", warnings)
        return _output("PARTIAL", warnings, keep, [c for c in state["citations"] if c["chunk_id"] in used],
                       summarize(keep))

    def done(state: SchoolState) -> str:
        return "end" if state.get("status") else "next"

    graph = StateGraph(SchoolState, input_schema=QueryInput, output_schema=QueryOutput)
    graph.add_node("guard", guard)
    graph.add_node("search", search)
    graph.add_node("expand", expand)
    graph.add_node("verify", verify)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", done, {"end": END, "next": "search"})
    graph.add_conditional_edges("search", done, {"end": END, "next": "expand"})
    graph.add_conditional_edges("expand", lambda s: "end" if s.get("status") in ("ERROR", "INSUFFICIENT_EVIDENCE")
                                else "next", {"end": END, "next": "verify"})
    graph.add_edge("verify", END)
    return graph.compile()


def run(query: QueryInput, *, deps: dict | None = None) -> QueryOutput:
    """공개 호출(팀 지시서 4-2). 입력 검사 → 그래프 실행 → 출력 검사. 잘못된 입력은 ContractError."""
    validate_query_input(query)
    owns = deps is None
    deps = deps if deps is not None else default_deps()
    try:
        result = build_graph(deps).invoke(dict(query))
    except Exception as exc:  # noqa: BLE001 - 실행 실패는 완전한 ERROR 출력으로 바꾼다
        _trace(deps, "run", error=type(exc).__name__)
        result = empty_output(DOMAIN, "ERROR", f"SchoolGraph 실행 실패: {type(exc).__name__}")
    finally:
        if owns:
            deps["driver"].close()
    validate_query_output(result, query)
    return result
