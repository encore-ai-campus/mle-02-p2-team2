"""ActionSearchTool (주승우) — 공통 계약 v2.0의 `action_search`.

ToolInput → ToolResult. 학교가 수행하는 행동·담당 역할·대응 단계와 원문 근거를 찾는다.

부품(11/05 지시서 B03~B06)
- B03 plan_action_task: 승인 사전으로 역할·질병·단계 힌트를 읽는다. 질문은 바꾸지 않는다.
  역할이 '…에게'처럼 수신자로만 나오면 수행자로 보지 않는다. 단계는 원문 검색 범위 힌트일 뿐 현재 상황 판정이 아니다.
- B04 retrieve_action_records: (1) 역할이 있으면 고정 Cypher로 SchoolAction-DONE_BY-SchoolRole을 직접 찾고
  (2) 질문 표현은 청크 검색(hybrid/fulltext) 뒤 SUPPORTED_BY로 연결된 행동을 찾는다. 후보 확보 방식(via)과
  관계를 통한 근거 확인(get_action_evidence)을 구분한다. 모든 조회에 allowed를 다시 적용하고,
  담당 문서의 선정쪽수 밖 청크(capabilities.active_pages)는 쓰지 않는다.
- B05 records_to_tool_result: Finding(원자적 주장)·Citation(실제 청크 원문)·GraphPath(실제 조회한 관계, 저장 방향).
- B06 verify_tool_result_db: 구조 검사(contract_v2_local) + DB 대조(기존 verify_against_db) + GraphPath 실재·방향·
  허용 범위 검사. 실패한 주장·경로는 제거하고 사유를 warnings에 남긴다(새 표현으로 고쳐 통과시키지 않음).

자원: deps의 driver는 메인(호출자)이 소유한다. 이 Tool은 driver를 닫지 않는다. 임베딩은 선택이다
(embedder가 없으면 fulltext + 고정 Cypher만 쓴다).
"""

from __future__ import annotations

import re

from index.school.dictionaries import compare_key, load_dictionaries
from rag.school.capabilities import active_pages, in_scope
from rag.school.contract_v2_local import (
    ContractError,
    ToolInput,
    ToolResult,
    split_ref,
    validate_tool_input,
    validate_tool_result,
)
from rag.school.graph import (
    GENERIC_BIGRAMS,
    MODALITY_LABELS,
    STAGE_HINTS,
    STAGE_LABELS,
    STAGE_PATTERNS,
    _is_scenario,
    bigrams,
    build_claim,
    build_quote_claim,
    excerpt_of,
    plan_question,
    quote_findings,
    strip_particle,
)
from rag.school.relevance import (
    action_disease_mismatch,
    disease_mentions,
    disease_mismatch,
    evidence_diseases,
    mask_disease_names,
    matches_operation,
    operation_terms,
)
from rag.school.search import (
    ACTION_FIELDS,
    SearchError,
    _rows,
    expand_school_actions,
    get_action_evidence,
    get_role_actions,
    search_school_chunks,
)
from rag.school.validation_local import verify_against_db

TOOL_NAME = "action_search"
DEFAULT_TOP_K = 8
UNRESOLVED = "[미해결]"          # 메인 그래프가 다음 Tool 판단에 쓰는 warnings 접두어
ID_PROPERTY = {"Document": "document_id", "Chunk": "chunk_id", "SchoolAction": "action_id",
               "SchoolRole": "role_id", "ActionConcept": "concept_id", "Disease": "disease_id"}
RECIPIENT = r"\s*(에게|께|한테|에게로)"
PAGE_IN_ID = re.compile(r":p(\d{4}):")
# 질문 말투 → 원문 표기(관련도 비교용 2글자 보강). 의미를 바꾸지 않고 원문이 쓰는 낱말만 더한다.
SYNONYMS = {"끝나": "종료", "끝난": "종료", "끝내": "종료", "언제": "시기 기간", "누가": "담당", "누구": "담당",
            "어디": "장소", "얼마나": "기간", "돌봐": "담당", "돌보": "담당", "맡": "담당", "써도": "사용",
            "쓰면": "사용", "연락": "연락 통보", "알려": "안내", "다시등교": "등교 재개", "준비": "준비 협조"}
# 상황 표현 → 원문 단계(검색 우선순위 힌트일 뿐 판정 아님). 복구 표현이 있으면 '유행'만으로 잡힌 제3단계를 뺀다.
RECOVERY_CUES = ("유행종료", "복구", "끝나면", "끝난", "끝나", "종료되", "종료후", "후속조치")


# ---------------------------------------------------------------- B03 계획

def _names(entry: dict) -> list[str]:
    return [entry["name"], *entry.get("aliases", [])]


def plan_action_task(tool_input: ToolInput, books: dict, capabilities: dict | None = None,
                     has_embedder: bool = False) -> dict:
    """역할·단계·질병 힌트, 검색 방식, 미해결 항목. 질문 원문은 그대로 둔다."""
    question = tool_input["question"]
    base = plan_question(question, books)
    unresolved: list[str] = []
    key = compare_key(question)

    # '교장·교감은', '담임/보건교사'처럼 기호로 이어 쓴 역할도 낱말로 나눠 다시 찾는다(2글자 별칭은 낱말 전체일 때만).
    excluded = [compare_key(m["mention"]) for m in books["role"].get("excluded_mentions", [])]
    pieces = [strip_particle(compare_key(p)) for p in re.split(r"[\s·ㆍ,/]+|및", question) if p.strip()]
    found = []
    for entry in books["role"]["entries"]:
        for name in map(compare_key, _names(entry)):
            hit = [i for i, p in enumerate(pieces)
                   if (p == name or (len(name) >= 3 and name in p)) and not any(x in p for x in excluded)]
            if hit:
                found.append((min(hit), entry["role_id"]))
    detected = list(dict.fromkeys([*base["roles"], *(r for _, r in sorted(found))]))

    # 단계: 질문에 단계가 직접 적혔으면 그대로, 아니면 상황 표현 힌트. 힌트로 잡힌 단계는 원문 인용을 거르지 않는다.
    named = [s for s, pattern in STAGE_PATTERNS.items() if re.search(pattern, key)]
    situation = mask_disease_names(question, books)
    stages = named or [s for s, cues in STAGE_HINTS.items() if any(c in situation for c in cues)]
    if not named and any(c in situation for c in RECOVERY_CUES):
        stages = [s for s in stages if s != "RESPONSE_3"] + ["RECOVERY"]
    stages = list(dict.fromkeys(stages)) or None
    focus = set(base["focus"])
    for cue, words in SYNONYMS.items():
        if cue in key:
            focus |= bigrams(words)

    # 역할이 '보건교사에게 연락'처럼 수신자로만 나오면 수행자 후보에서 뺀다.
    roles, recipients = [], []
    for role_id in detected:
        entry = next(e for e in books["role"]["entries"] if e["role_id"] == role_id)
        mentions, covered = [], set()
        for name in sorted(_names(entry), key=len, reverse=True):     # 긴 표기 우선, 그 안의 짧은 별칭은 건너뜀
            for m in re.finditer(re.escape(name), question):
                if not covered & set(range(m.start(), m.end())):
                    mentions.append(m)
                    covered |= set(range(m.start(), m.end()))
        as_recipient = [m for m in mentions if re.match(RECIPIENT, question[m.end():])]
        (recipients if mentions and len(as_recipient) == len(mentions) else roles).append(role_id)

    mentioned = disease_mentions(question, books)
    diseases = [e["disease_id"] for e in books["disease"]["entries"] if e["disease_id"] in mentioned]

    # context_entity_ids: 첫 ':'로 라벨/ID를 나누고 사전에 있는 것만 쓴다.
    known = {"SchoolRole": {e["role_id"] for e in books["role"]["entries"]},
             "Disease": {e["disease_id"] for e in books["disease"]["entries"]},
             "ActionConcept": {e["concept_id"] for e in books["concept"]["entries"]}}
    concepts = []
    for ref in tool_input.get("context_entity_ids", []):
        label, node_id = split_ref(ref)
        if label in known and node_id not in known[label]:
            unresolved.append(f"사전에 없는 context_entity_id: {ref}")
        elif label == "SchoolRole" and node_id not in roles:
            roles.append(node_id)
        elif label == "Disease" and node_id not in diseases:
            diseases.append(node_id)
        elif label == "ActionConcept":
            concepts.append(node_id)
        elif label not in known:
            unresolved.append(f"이 Tool이 쓰지 않는 context 라벨: {ref}")

    if base["out_of_scope"]:
        unresolved += base["out_of_scope"]
    if base["legal_duty"]:
        unresolved.append("법정 신고 의무 여부는 학교 문서만으로 확정하지 않는다 — legal_search 확인 필요")
    if base["compare"]:
        unresolved.append("문서별 비교는 crossdoc_graph_search 몫이다 — 이 Tool은 문서별 행동을 따로 반환")
    paths = (capabilities or {}).get("paths", {})
    mode = "hybrid" if has_embedder and paths.get("vector", True) else "fulltext"
    return {**base, "roles": roles, "recipients": recipients, "diseases": diseases, "concepts": concepts,
            "stages": stages, "stage_named": bool(named), "focus": sorted(focus),
            "broad": bool(roles) and base["broad"] or (bool(roles) and any(c in key for c in ("어떤일", "무슨일"))),
            "mode": mode, "unresolved": unresolved,
            "operation_terms": operation_terms(question, roles),
            "top_k": tool_input.get("top_k", DEFAULT_TOP_K)}


# ---------------------------------------------------------------- B04 조회

def page_of(chunk_id: str) -> int | None:
    match = PAGE_IN_ID.search(chunk_id)
    return int(match.group(1)) if match else None


def scan_actions(driver, allowed: list[str], *, limit: int = 3000, database=None) -> list[dict]:
    """허용 문서의 학교 행동 전체(행동·근거 청크별 1행)와 근거 청크의 표 제목·절. 학교 문서 행동은 수백 개라
    전수 조회 후 행동 문장·표 제목으로 관련도를 매긴다(청크 검색이 놓친 행동을 구조로 찾는 경로)."""
    if not allowed:
        return []
    return _rows(driver, f"""
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction)
WHERE d.document_id IN $allowed
WITH DISTINCT a, c, d
RETURN {ACTION_FIELDS}, c.section AS section
ORDER BY document_id, chunk_id, a.step_order, action_id LIMIT $limit""", database, allowed=allowed, limit=limit)


def _score(action: dict, plan: dict, chunk_rank: dict[str, int], names: dict[str, str]) -> float:
    """후보 정렬 점수(선별용일 뿐 의미 판정이 아니다)."""
    focus = set(plan["focus"])
    text_pairs = bigrams(action["action_text"]) - GENERIC_BIGRAMS
    score = 1.5 * len(text_pairs & focus)
    score += 0.5 * len((bigrams(action.get("section") or "") - GENERIC_BIGRAMS) & focus)
    score += max((len(bigrams(names[c]) & focus) for c in action["concept_ids"] if c in names), default=0)
    performer = {r["role_id"] for r in action["roles"]}
    if plan["roles"] and performer & set(plan["roles"]):
        score += 4
    elif plan["roles"] and performer:
        score -= 4                     # 질문하지 않은 역할이 수행하는 행동
    if plan["stages"] and action["stage"] in plan["stages"]:
        score += 3
    if plan["diseases"] and set(action["disease_ids"]) & set(plan["diseases"]):
        score += 3
    if plan.get("concepts") and set(action["concept_ids"]) & set(plan["concepts"]):
        score += 3
    rank = chunk_rank.get(action["chunk_id"])
    if rank is not None:
        score += max(0.0, 6 - rank * 0.5)
    return score


def _off_stage(action: dict, plan: dict) -> bool:
    """질문 단계 힌트가 있는데 원문이 다른 단계(미지정 제외)에 배치한 행동."""
    stage = action.get("stage")
    return bool(plan["stages"] and stage and stage != "UNSPECIFIED" and stage not in plan["stages"])


def diversify(ranked: list[dict], plan: dict, k: int) -> list[dict]:
    """(질문한 역할, 문서) 묶음을 번갈아 뽑는다. '담임·보건교사 각각' 질문에서 한 역할·한 문서가 상위를 독차지하지 않게."""
    buckets: dict[tuple, list[dict]] = {}
    for item in ranked:
        asked = [r for r in plan["roles"] if r in {x["role_id"] for x in item["roles"]}]
        buckets.setdefault((asked[0] if asked else "-", item["document_id"]), []).append(item)
    queues = list(buckets.values())
    selected: list[dict] = []
    while len(selected) < k and any(queues):
        for queue in queues:
            if queue and len(selected) < k:
                selected.append(queue.pop(0))
    return selected


def retrieve_action_records(tool_input: ToolInput, plan: dict, deps: dict) -> dict:
    """행동별 레코드(근거 청크 여러 개 보존)·인용 후보 청크·조회 기록. DB 오류는 SearchError로 올린다."""
    driver, database, books = deps["driver"], deps.get("database"), deps["books"]
    allowed = list(tool_input["allowed_document_ids"])
    scope = deps.get("active_pages")
    if scope is None:
        scope = deps["active_pages"] = active_pages()
    log: list[dict] = []
    if not allowed:
        return {"actions": [], "quotes": [], "hits": [], "log": [{"step": "allowed=[]", "rows": 0}]}

    candidates: dict[str, dict] = {}
    rejected_actions: dict[str, str] = {}

    def add(rows: list[dict], via: str) -> int:
        kept = 0
        for row in rows:
            if not in_scope(row["document_id"], page_of(row["chunk_id"]), scope):
                continue
            if action_disease_mismatch(row, plan["diseases"], books):
                rejected_actions[row["action_id"]] = "disease_mismatch"
                continue
            action_text = " ".join(row.get(k) or "" for k in ("action_text", "when_text", "section"))
            if not matches_operation(action_text, plan["operation_terms"]):
                rejected_actions[row["action_id"]] = "operation_mismatch"
                continue
            item = candidates.setdefault(row["action_id"], {**row, "via": set()})
            item["via"].add(via)
            if row.get("section") and not item.get("section"):
                item["section"] = row["section"]
            kept += 1
        return kept

    if plan["roles"]:
        rows = get_role_actions(driver, plan["roles"], allowed, stages=plan["stages"], limit=300,
                                database=database)
        log.append({"step": "role_cypher", "roles": plan["roles"], "stages": plan["stages"],
                    "rows": len(rows), "kept": add(rows, "role")})
        if plan["stages"] and not rows:
            rows = get_role_actions(driver, plan["roles"], allowed, limit=300, database=database)
            log.append({"step": "role_cypher_any_stage", "rows": len(rows), "kept": add(rows, "role")})

    request = {"question": tool_input["question"], "allowed_document_ids": allowed,
               "preferred_document_ids": tool_input.get("preferred_document_ids", [])}
    candidate_k = max(12, plan["top_k"] + 4)
    if plan["operation_terms"]:
        # 질병명만 겹치는 상위 청크를 제외한 뒤에도 조치 근거가 남도록 후보를 넓게 확보한다.
        # 출력 top_k와 검색 후보 수는 다르며, Tool 추가 호출/재진입은 아니다.
        candidate_k = min(64, max(32, plan["top_k"] * 4))
    found = search_school_chunks(driver, request, books, embedder=deps.get("embedder"), mode=plan["mode"],
                                 k=candidate_k, database=database)
    scoped_hits = [h for h in found["hits"] if in_scope(h["document_id"], h["pdf_page"], scope)]
    rejected_hits, hits = [], []
    for hit in scoped_hits:
        reason = None
        if disease_mismatch(evidence_diseases(hit, books), plan["diseases"]):
            reason = "disease_mismatch"
        elif not matches_operation(hit["text"] + " " + (hit.get("section") or ""), plan["operation_terms"]):
            reason = "operation_mismatch"
        if reason:
            rejected_hits.append({"chunk_id": hit["chunk_id"], "reason": reason})
        else:
            hits.append(hit)
    chunk_rank = {h["chunk_id"]: h["rank"] for h in hits}
    rows = expand_school_actions(driver, list(chunk_rank), allowed, limit=200, database=database)
    log.append({"step": f"chunk_{found['mode']}", "candidate_k": candidate_k,
                "hits": len(found["hits"]), "in_scope": len(scoped_hits), "relevant_hits": len(hits),
                "linked_actions": len(rows), "kept": add(rows, "chunk")})
    rows = scan_actions(driver, allowed, database=database)
    log.append({"step": "action_scan", "rows": len(rows), "kept": add(rows, "scan")})
    log.append({"step": "relevance_filter", "rejected_hits": rejected_hits,
                "rejected_actions": [{"action_id": a, "reason": why} for a, why in rejected_actions.items()]})

    names = {e["concept_id"]: e["name"] for e in books["concept"]["entries"]}
    for item in candidates.values():
        item["score"] = _score(item, plan, chunk_rank, names)
    ranked = sorted(candidates.values(), key=lambda a: (-a["score"], a["action_id"]))
    # 관련성 문턱: 역할로 찾은 넓은 역할 질문이 아니면, 질문 특징과 겹치거나 상위 청크에 붙은 행동만.
    keep = [item for item in ranked if item["score"] >= 4 or (plan["broad"] and "role" in item["via"])]
    selected = diversify(keep, plan, plan["top_k"])

    evidence = get_action_evidence(driver, [a["action_id"] for a in selected], allowed, database=database)
    by_action: dict[str, list[dict]] = {}
    for row in evidence:
        if (in_scope(row["document_id"], row["pdf_page"], scope)
                and not disease_mismatch(evidence_diseases(row, books), plan["diseases"])
                and matches_operation(row["text"] + " " + (row.get("section") or ""), plan["operation_terms"])):
            by_action.setdefault(row["action_id"], []).append(row)
    actions = []
    for item in selected:
        chunks = by_action.get(item["action_id"])
        if not chunks:               # 관계로 근거를 다시 확인하지 못한 행동은 쓰지 않는다
            continue
        actions.append({**item, "via": sorted(item["via"]), "evidence": chunks})
    log.append({"step": "evidence", "candidates": len(candidates), "selected": len(selected),
                "with_evidence": len(actions)})

    # 단계 힌트와 다른 단계에 배치된 원문은 인용하지 않는다(질문 핵심어가 있으면 '질문 단계와 다름'을 밝히고 넣음).
    # 다른 단계 행동을 '지금 할 일'로 섞지 않는 것이 함정 질문의 핵심 기준이다.
    state = {"question": tool_input["question"], "plan": plan}
    used = {c["chunk_id"] for a in actions for c in a["evidence"]}
    quotes = [q for q in quote_findings(state, hits, books, used)
              if matches_operation(q["excerpt"] + " " + (q["hit"].get("section") or ""), plan["operation_terms"])]
    if (plan["diseases"] and plan["operation_terms"]
            and not any(word in tool_input["question"] for word in ("시나리오", "모의훈련", "모의 훈련"))):
        wanted = set(plan["diseases"])
        direct_quotes = [q for q in quotes if not _is_scenario(q["hit"])
                         and wanted & (evidence_diseases(q["hit"], books, excerpt=q["excerpt"]) or set())]
        direct_actions = [a for a in actions if any(not _is_scenario(c) for c in a["evidence"])
                          and (wanted & set(a["disease_ids"]) or any(
                              wanted & (evidence_diseases(c, books) or set()) for c in a["evidence"]))]
        if direct_quotes or direct_actions:
            # 특정 질병·조치의 본문 근거가 있으면 일반 지침/시나리오로 남은 자리를 채우지 않는다.
            log.append({"step": "prefer_direct_evidence", "background_actions": len(actions) - len(direct_actions),
                        "background_quotes": len(quotes) - len(direct_quotes)})
            actions, quotes = direct_actions, direct_quotes
    # 행동(top_k까지)과 원문 인용(top_k//2까지)은 따로 상한을 둔다. 둘을 한 상한으로 묶으면 인용이 먼저 자리를
    # 차지해 이 Tool의 핵심인 역할 행동이 잘린다(11/05 3차 평가 S-Q07 2/2→0/2, 같은 8개 중 5·8번째가 절단).
    # 화면에 몇 개를 보일지는 메인 그래프 답변 구성이 정한다. 순서: 질문 단계와 같은(또는 단계 미지정) 행동 → 점수.
    quotes = quotes[: max(1, plan["top_k"] // 2)]
    actions.sort(key=lambda a: (_off_stage(a, plan), -a["score"]))
    log.append({"step": "quotes", "rows": len(quotes)})
    return {"actions": actions, "quotes": quotes, "hits": hits, "log": log}


# ---------------------------------------------------------------- B05 직렬화

def _citation(chunk: dict, excerpt: str | None = None) -> dict:
    return {"document_id": chunk["document_id"], "chunk_id": chunk["chunk_id"], "pdf_page": chunk["pdf_page"],
            "printed_page": chunk["printed_page"], "excerpt": excerpt or excerpt_of(chunk["text"])}


def action_path(action: dict) -> dict:
    """실제 조회한 관계만 저장 방향 그대로. basis가 없는 DONE_BY는 넣지 않는다."""
    ref = f"SchoolAction:{action['action_id']}"
    edges = []
    for chunk in action["evidence"]:
        edges.append({"source_ref": ref, "relationship_type": "SUPPORTED_BY",
                      "target_ref": f"Chunk:{chunk['chunk_id']}"})
        edges.append({"source_ref": f"Document:{chunk['document_id']}", "relationship_type": "HAS_CHUNK",
                      "target_ref": f"Chunk:{chunk['chunk_id']}"})
    edges += [{"source_ref": ref, "relationship_type": "DONE_BY", "target_ref": f"SchoolRole:{r['role_id']}"}
              for r in action["roles"] if r.get("basis")]
    edges += [{"source_ref": ref, "relationship_type": "INSTANCE_OF", "target_ref": f"ActionConcept:{c}"}
              for c in action["concept_ids"]]
    edges += [{"source_ref": ref, "relationship_type": "FOR_DISEASE", "target_ref": f"Disease:{d}"}
              for d in action["disease_ids"]]
    return {"edges": edges}


def records_to_tool_result(records: dict, tool_input: ToolInput, plan: dict, books: dict | None = None) -> ToolResult:
    # 구조화된 행동 Finding을 먼저, 원문 그대로 인용은 뒤에 둔다(Citation 번호도 이 순서를 따른다).
    findings, citations, paths = [], {}, []
    for action in records["actions"]:
        first = action["evidence"][0]
        claim = build_claim(action, first)
        if plan["stages"] and action["stage"] in STAGE_LABELS and action["stage"] != "UNSPECIFIED" \
                and action["stage"] not in plan["stages"]:
            claim += " — 원문 배치 단계가 질문 단계와 다름"
        findings.append({"claim": claim,
                         "chunk_ids": [c["chunk_id"] for c in action["evidence"]],
                         "action_ids": [action["action_id"]], "disease_ids": list(action["disease_ids"]),
                         "concept_ids": list(action["concept_ids"])})
        for chunk in action["evidence"]:
            # 같은 청크가 원문 인용과 행동을 함께 지지하면 행·문단 전체를 발췌로 써서 두 주장을 모두 덮는다.
            citations[chunk["chunk_id"]] = _citation(chunk)
        paths.append(action_path(action))
    for row in records["quotes"]:
        hit = row["hit"]
        findings.append({"claim": build_quote_claim(row), "chunk_ids": [hit["chunk_id"]], "action_ids": [],
                         "disease_ids": [], "concept_ids": []})
        citations.setdefault(hit["chunk_id"], _citation(hit, row["excerpt"]))

    warnings: list[str] = []
    unresolved = [f"{UNRESOLVED} {u}" for u in plan["unresolved"]]
    if not findings:
        reason = ("허용 문서 목록이 비어 있어 검색하지 않았다." if not tool_input["allowed_document_ids"]
                  else "허용 문서 안에서 질문에 해당하는 학교 행동·원문 근거를 찾지 못했다.")
        return {"tool_name": TOOL_NAME, "status": "NO_EVIDENCE", "findings": [], "citations": [],
                "graph_paths": [], "warnings": [reason, *unresolved]}

    actions = records["actions"]
    if records["quotes"]:
        warnings.append("원문 인용 Finding은 검색된 원문을 그대로 옮긴 것이다. 해석·적용 판단은 포함하지 않는다.")
    if any(r["basis"] == "EXAMPLE" for a in actions for r in a["roles"]) or any(q["role"] for q in records["quotes"]):
        warnings.append("역할은 원문이 제시한 예시이며 학교 상황에 따라 조정할 수 있다(의무 배정 아님).")
    if plan["stages"]:
        labels = ", ".join(STAGE_LABELS[s] for s in plan["stages"])
        warnings.append(f"질문 표현에 맞춰 원문의 {labels} 근거를 우선했다. 원문이 배치한 단계이며 현재 상황의 단계를 "
                        "판정한 것이 아니다.")
    if any(a.get("modality") in MODALITY_LABELS for a in actions):
        warnings.append("'원문 강도'는 원문 표현의 강도이며 법적 의무 여부를 확정하지 않는다.")

    status = "OK"
    chunks = [c for a in actions for c in a["evidence"]] + [q["hit"] for q in records["quotes"]]
    if chunks and all(_is_scenario(c) for c in chunks):
        status = "PARTIAL"
        unresolved.append(f"{UNRESOLVED} 근거가 모의훈련 시나리오 표뿐이다. 본문 지침 근거는 확인하지 못했다.")
    missing_roles = [r for r in plan["roles"]
                     if not any(r in {x["role_id"] for x in a["roles"]} for a in actions)
                     and not any(q["role"] and q["role"]["role_id"] == r for q in records["quotes"])]
    if missing_roles:
        status = "PARTIAL"
        unresolved.append(f"{UNRESOLVED} 질문한 역할의 근거를 찾지 못함: {missing_roles}")
    texts = " ".join(c["text"] for c in chunks).casefold()
    absent = [w for w in re.findall(r"[A-Za-z]{2,}", tool_input["question"]) if w.casefold() not in texts]
    if absent:
        status = "PARTIAL"
        unresolved.append(f"{UNRESOLVED} 질문의 핵심어 {absent}에 대한 원문을 찾지 못했다.")
    if plan["unresolved"]:
        status = "PARTIAL"
    if plan["diseases"]:
        if books is None:
            books = load_dictionaries()
        covered = set()
        for action in actions:
            covered.update(action["disease_ids"])
            for chunk in action["evidence"]:
                covered.update(evidence_diseases(chunk, books) or set())
        for quote in records["quotes"]:
            # 전체 청크의 다른 문장을 발췌의 적용 근거로 삼지 않는다.
            covered.update(evidence_diseases(quote["hit"], books, excerpt=quote["excerpt"]) or set())
        missing = set(plan["diseases"]) - covered
        if missing:
            status = "PARTIAL"
            unresolved.append(f"{UNRESOLVED} 질문한 질병의 적용 근거를 확인하지 못함: {sorted(missing)}. "
                              "일반 학교 지침을 해당 질병의 기준으로 확정하지 않는다.")
    return {"tool_name": TOOL_NAME, "status": status, "findings": findings, "citations": list(citations.values()),
            "graph_paths": paths, "warnings": list(dict.fromkeys(warnings + unresolved))}


# ---------------------------------------------------------------- B06 DB 검증

def check_graph_paths(paths: list[dict], allowed: list[str], driver, database=None) -> dict[int, list[str]]:
    """{경로 번호: [문제]}. 관계의 실재·타입·방향·양 끝 ID와 허용 문서 범위를 실제 MATCH로 판정한다."""
    problems: dict[int, list[str]] = {}
    rows, owners = [], []
    for p_index, path in enumerate(paths):
        for edge in path["edges"]:
            (s_label, s_id), (t_label, t_id) = split_ref(edge["source_ref"]), split_ref(edge["target_ref"])
            if s_label not in ID_PROPERTY or t_label not in ID_PROPERTY:
                problems.setdefault(p_index, []).append(f"알 수 없는 라벨: {edge}")
                continue
            rows.append({"sl": s_label, "sp": ID_PROPERTY[s_label], "sid": s_id, "rel": edge["relationship_type"],
                         "tl": t_label, "tp": ID_PROPERTY[t_label], "tid": t_id})
            owners.append((p_index, edge))
    if rows:
        records, _, _ = driver.execute_query("""
UNWIND $rows AS e
OPTIONAL MATCH (s) WHERE e.sl IN labels(s) AND s[e.sp] = e.sid
OPTIONAL MATCH (t) WHERE e.tl IN labels(t) AND t[e.tp] = e.tid
RETURN s IS NOT NULL AS has_s, t IS NOT NULL AS has_t,
       CASE WHEN s IS NULL OR t IS NULL THEN 0 ELSE COUNT { (s)-[r]->(t) WHERE type(r) = e.rel } END AS forward,
       CASE WHEN s IS NULL OR t IS NULL THEN 0 ELSE COUNT { (t)-[r]->(s) WHERE type(r) = e.rel } END AS backward
""", database_=database, routing_="r", rows=rows)
        for (p_index, edge), rec in zip(owners, records, strict=True):
            if not rec["has_s"] or not rec["has_t"]:
                problems.setdefault(p_index, []).append(f"DB에 없는 끝점: {edge}")
            elif rec["forward"] == 0:
                why = "방향이 반대인 관계" if rec["backward"] else "DB에 없는 관계"
                problems.setdefault(p_index, []).append(f"{why}: {edge}")
    # 허용 범위: 경로의 Document·Chunk·SchoolAction이 모두 allowed 문서에 속해야 한다.
    refs = {split_ref(r) for p in paths for e in p["edges"] for r in (e["source_ref"], e["target_ref"])}
    chunk_ids = [i for label, i in refs if label == "Chunk"]
    action_ids = [i for label, i in refs if label == "SchoolAction"]
    owner_docs: dict[str, set[str]] = {}
    if chunk_ids:
        records, _, _ = driver.execute_query("""
UNWIND $ids AS cid
OPTIONAL MATCH (d:Document)-[:HAS_CHUNK]->(:Chunk {chunk_id: cid})
RETURN cid, collect(DISTINCT d.document_id) AS docs""", database_=database, routing_="r", ids=chunk_ids)
        owner_docs |= {f"Chunk:{r['cid']}": set(r["docs"]) for r in records}
    if action_ids:
        records, _, _ = driver.execute_query("""
UNWIND $ids AS aid
OPTIONAL MATCH (:SchoolAction {action_id: aid})-[:SUPPORTED_BY]->(:Chunk)<-[:HAS_CHUNK]-(d:Document)
RETURN aid, collect(DISTINCT d.document_id) AS docs""", database_=database, routing_="r", ids=action_ids)
        owner_docs |= {f"SchoolAction:{r['aid']}": set(r["docs"]) for r in records}
    allowed_set = set(allowed)
    for p_index, path in enumerate(paths):
        for edge in path["edges"]:
            for ref in (edge["source_ref"], edge["target_ref"]):
                label, node_id = split_ref(ref)
                docs = {node_id} if label == "Document" else owner_docs.get(ref)
                if label in ("Document", "Chunk", "SchoolAction") and docs is not None and not docs <= allowed_set:
                    problems.setdefault(p_index, []).append(f"허용 문서 밖 경로: {ref}")
    return {k: list(dict.fromkeys(v)) for k, v in problems.items()}


def verify_tool_result_db(result: ToolResult, tool_input: ToolInput, driver, database=None) -> tuple[ToolResult, dict]:
    """DB 대조 후 실패한 Finding·Citation·GraphPath를 제거한 결과와 보고서를 돌려준다."""
    report = verify_against_db(result, driver, database)
    path_problems = check_graph_paths(result["graph_paths"], tool_input["allowed_document_ids"], driver, database)
    report["bad_paths"] = path_problems
    findings = [f for i, f in enumerate(result["findings"]) if i not in report["bad_findings"]]
    used = {c for f in findings for c in f["chunk_ids"]}
    citations = [c for c in result["citations"] if c["chunk_id"] in used and c["chunk_id"] not in report["bad_citations"]]
    kept_actions = {a for f in findings for a in f["action_ids"]}
    paths = [p for i, p in enumerate(result["graph_paths"])
             if i not in path_problems and any(split_ref(e["source_ref"])[1] in kept_actions for e in p["edges"])]
    warnings = list(result["warnings"])
    removed = len(result["findings"]) - len(findings)
    if removed:
        warnings.append(f"DB 대조에 실패한 Finding {removed}개를 제외했다.")
    if path_problems:
        warnings.append(f"DB에서 확인되지 않은 GraphPath {len(path_problems)}개를 제외했다.")
    status = result["status"]
    if not findings and status in ("OK", "PARTIAL"):
        status, citations, paths = "NO_EVIDENCE", [], []
        warnings.append("DB 대조를 통과한 주장이 남지 않았다.")
    elif removed and status == "OK":
        status = "PARTIAL"
    return ({**result, "status": status, "findings": findings, "citations": citations, "graph_paths": paths,
             "warnings": list(dict.fromkeys(warnings))}, report)


# ---------------------------------------------------------------- 진입점

def _error(message: str) -> ToolResult:
    return {"tool_name": TOOL_NAME, "status": "ERROR", "findings": [], "citations": [], "graph_paths": [],
            "warnings": [message]}


def action_search(tool_input: ToolInput, deps: dict) -> ToolResult:
    """메인 그래프가 부르는 callable. 예외를 밖으로 던지지 않고 ERROR ToolResult로 돌려준다.

    deps: driver(필수, 호출자 소유), database, books(없으면 로드), embedder(선택), active_pages(선택),
          capabilities(선택), trace(선택: 실행 기록 목록).
    """
    try:
        validate_tool_input(tool_input)
    except ContractError as exc:
        return _error(f"ToolInput 계약 위반: {exc}")
    if "books" not in deps:
        from index.school.dictionaries import load_dictionaries
        deps["books"] = load_dictionaries()
    trace = deps.get("trace")
    try:
        plan = plan_action_task(tool_input, deps["books"], deps.get("capabilities"),
                                has_embedder=deps.get("embedder") is not None)
        records = retrieve_action_records(tool_input, plan, deps)
        raw = records_to_tool_result(records, tool_input, plan, deps["books"])
        result, report = verify_tool_result_db(raw, tool_input, deps["driver"], deps.get("database"))
    except SearchError as exc:
        return _error(f"DB 검색 실패: {exc}")
    except Exception as exc:  # DB 대조 실패 등: 검증하지 못한 주장은 내보내지 않는다
        return _error(f"Tool 실행 실패: {type(exc).__name__}: {exc}")
    if trace is not None:
        trace.append({"tool": TOOL_NAME, "plan": {k: plan[k] for k in ("roles", "recipients", "stages", "diseases",
                                                                         "operation_terms", "mode", "broad",
                                                                         "unresolved")},
                      "log": records["log"], "raw_findings": len(raw["findings"]),
                      "db_report": {"bad_citations": report["bad_citations"], "bad_findings": report["bad_findings"],
                                    "bad_paths": report["bad_paths"]},
                      "selected": [{"action_id": a["action_id"], "score": round(a["score"], 1), "via": a["via"]}
                                   for a in records["actions"]]})
    try:
        validate_tool_result(result, tool_input, expected_tool=TOOL_NAME)
    except ContractError as exc:
        return _error(f"ToolResult 계약 검사 실패: {exc}")
    return result
