"""문서별 학교 행동과 담당을 ActionConcept 기준으로 비교하는 그래프 Tool."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from index.school.dictionaries import compare_key, load_dictionaries
from index.school.neo4j_local import get_driver
from rag.respiratory.disease_tool import (
    Citation,
    Finding,
    GraphEdge,
    GraphPath,
    ToolInput,
    ToolResult,
    _all_document_ids,
)
from rag.respiratory.graph import PROJECT_ROOT
from rag.school.search import compare_action_sources, get_action_evidence

FIXTURE_DIR = PROJECT_ROOT / "tests/fixtures/respiratory"
MAX_TOP_K = 100
CONCEPT_SPLIT_RE = re.compile(r"[\s·()（）]+")


def _result(status: str, warnings: list[str] | None = None) -> ToolResult:
    return {
        "tool_name": "crossdoc_graph_search",
        "status": status,  # type: ignore[typeddict-item]
        "findings": [],
        "citations": [],
        "graph_paths": [],
        "warnings": list(warnings or []),
    }


def _default_deps() -> dict[str, Any]:
    driver, database = get_driver(PROJECT_ROOT / ".env")
    try:
        books = load_dictionaries()
    except Exception:
        driver.close()
        raise
    return {"driver": driver, "database": database, "books": books}


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result


def _concept_entries(books: dict) -> list[dict]:
    return books["concept"]["entries"]


def _select_concepts(tool_input: ToolInput, books: dict) -> list[str]:
    entries = _concept_entries(books)
    context_ids = []
    for entity_id in tool_input.get("context_entity_ids", []):
        if isinstance(entity_id, str) and entity_id.startswith("ActionConcept:"):
            concept_id = entity_id.removeprefix("ActionConcept:")
            if concept_id and concept_id not in context_ids:
                context_ids.append(concept_id)
    if context_ids:
        return context_ids

    literal_text = f"{tool_input['question']} {tool_input.get('task', '')}"
    literal_ids = [
        entry["concept_id"]
        for entry in entries
        if isinstance(entry.get("concept_id"), str)
        and entry["concept_id"] in literal_text
    ]
    if literal_ids:
        return list(dict.fromkeys(literal_ids))

    question = tool_input["question"]
    scores = {}
    for entry in entries:
        concept_id = entry.get("concept_id")
        name = entry.get("name")
        if not isinstance(concept_id, str) or not isinstance(name, str):
            continue
        fragments = {
            fragment
            for fragment in CONCEPT_SPLIT_RE.split(name)
            if len(fragment) >= 2
        }
        scores[concept_id] = sum(fragment in question for fragment in fragments)
    highest = max(scores.values(), default=0)
    if highest <= 0:
        return []
    return [concept_id for concept_id, score in scores.items() if score == highest]


def _named_diseases(question: str, books: dict) -> set[str]:
    """질문에 나온 승인 질병 ID(사전 이름·별칭 2글자 이상)."""
    key = compare_key(question)
    return {
        entry["disease_id"]
        for entry in books.get("disease", {}).get("entries", [])
        if any(len(compare_key(n)) >= 2 and compare_key(n) in key
               for n in (entry.get("name", ""), *entry.get("aliases", [])))
    }


def _disease_first(rows: list[dict], disease_ids: set[str]) -> list[dict]:
    """질문 질병에 FOR_DISEASE로 연결된 행동을 먼저 둔다(나머지 순서는 그대로). 질병이 없으면 그대로."""
    if not disease_ids:
        return rows
    return sorted(rows, key=lambda row: not (disease_ids & set(_unique_strings(row.get("disease_ids")))))


def _select_actions(rows: list[dict], allowed: set[str], top_k: int) -> list[dict]:
    per_document: dict[str, list[dict]] = {}
    seen_action_ids = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        document_id = row.get("document_id")
        action_id = row.get("action_id")
        if (
            not isinstance(document_id, str)
            or document_id not in allowed
            or not isinstance(action_id, str)
            or not action_id
            or action_id in seen_action_ids
        ):
            continue
        selected = per_document.setdefault(document_id, [])
        if len(selected) >= top_k:
            continue
        selected.append(row)
        seen_action_ids.add(action_id)
    return [action for actions in per_document.values() for action in actions]


def _excerpt_around(text: str, action_text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    position = text.find(action_text) if action_text else -1
    center = max(position, 0)
    start = max(0, center - limit // 2)
    end = min(len(text), start + limit)
    if end - start < limit:
        start = max(0, end - limit)
    return text[start:end]


def _make_citation(evidence: dict, action_text: str, allowed: set[str]) -> Citation | None:
    document_id = evidence.get("document_id")
    chunk_id = evidence.get("chunk_id")
    pdf_page = evidence.get("pdf_page")
    text = evidence.get("text")
    if (
        not isinstance(document_id, str)
        or document_id not in allowed
        or not isinstance(chunk_id, str)
        or not chunk_id
        or type(pdf_page) is not int
        or pdf_page < 1
        or not isinstance(text, str)
        or not text.strip()
    ):
        return None
    printed_page = evidence.get("printed_page")
    if type(printed_page) is not int:
        printed_page = None
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "excerpt": _excerpt_around(text, action_text),
    }


def _role_list(action: dict) -> list[dict]:
    roles = action.get("roles", [])
    if not isinstance(roles, list):
        return []
    return [role for role in roles if isinstance(role, dict)]


def _role_warning(document_id: str, roles: list[dict]) -> str:
    summaries = []
    for role in roles:
        name = role.get("name")
        role_id = role.get("role_id")
        if not isinstance(name, str) or not name:
            name = role_id if isinstance(role_id, str) else None
        if not name:
            continue
        basis = role.get("basis")
        summary = f"{name}({basis})" if isinstance(basis, str) and basis else f"{name}(basis 미상)"
        if summary not in summaries:
            summaries.append(summary)
    responsible = ", ".join(summaries) if summaries else "명시 없음"
    return f"{document_id}:담당 {responsible}"


def _graph_path(action: dict, evidences: list[dict], concept_ids: list[str]) -> GraphPath:
    action_id = action["action_id"]
    action_ref = f"SchoolAction:{action_id}"
    edges: list[GraphEdge] = []
    seen_edges = set()

    def add_edge(source: str, relationship: str, target: str) -> None:
        key = (source, relationship, target)
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append({
                "source_ref": source,
                "relationship_type": relationship,
                "target_ref": target,
            })

    for concept_id in concept_ids:
        add_edge(action_ref, "INSTANCE_OF", f"ActionConcept:{concept_id}")
    for evidence in evidences:
        chunk_id = evidence["chunk_id"]
        document_id = evidence["document_id"]
        chunk_ref = f"Chunk:{chunk_id}"
        add_edge(action_ref, "SUPPORTED_BY", chunk_ref)
        add_edge(f"Document:{document_id}", "HAS_CHUNK", chunk_ref)
    for role in _role_list(action):
        role_id = role.get("role_id")
        if isinstance(role_id, str) and role_id:
            add_edge(action_ref, "DONE_BY", f"SchoolRole:{role_id}")
    # DB에서 읽은 실제 FOR_DISEASE 관계만 경로에 남긴다.
    for disease_id in _unique_strings(action.get("disease_ids")):
        add_edge(action_ref, "FOR_DISEASE", f"Disease:{disease_id}")
    return {"edges": edges}


def _search(tool_input: ToolInput, deps: dict) -> ToolResult:
    driver = deps["driver"]
    database = deps.get("database")
    books = deps["books"]
    concept_ids = _select_concepts(tool_input, books)
    if not concept_ids:
        return _result("NO_EVIDENCE", ["질문에서 비교할 행동 개념을 찾지 못함"])

    allowed_document_ids = _unique_strings(tool_input["allowed_document_ids"])
    allowed = set(allowed_document_ids)
    top_k = tool_input.get("top_k", 5)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        top_k = 5
    top_k = min(top_k, MAX_TOP_K)

    rows = compare_action_sources(
        driver,
        concept_ids,
        allowed_document_ids,
        limit=100,
        database=database,
    )
    if not isinstance(rows, list):
        raise TypeError("비교 검색 결과가 목록이 아닙니다.")
    actions = _select_actions(_disease_first(rows, _named_diseases(tool_input["question"], books)), allowed, top_k)
    if not actions:
        return _result("NO_EVIDENCE", ["허용 문서에서 비교 근거를 찾지 못함"])

    action_ids = [action["action_id"] for action in actions]
    evidence_rows = get_action_evidence(
        driver,
        action_ids,
        allowed_document_ids,
        database=database,
    )
    if not isinstance(evidence_rows, list):
        raise TypeError("근거 검색 결과가 목록이 아닙니다.")
    selected_action_ids = set(action_ids)
    evidence_by_action: dict[str, list[dict]] = {}
    for evidence in evidence_rows:
        if not isinstance(evidence, dict):
            continue
        action_id = evidence.get("action_id")
        if action_id in selected_action_ids:
            evidence_by_action.setdefault(action_id, []).append(evidence)

    findings: list[Finding] = []
    citations: list[Citation] = []
    graph_paths: list[GraphPath] = []
    cited_chunk_ids = set()
    document_order = []
    roles_by_document: dict[str, list[dict]] = {}
    for action in actions:
        action_id = action["action_id"]
        document_id = action["document_id"]
        action_text = action.get("action_text")
        if not isinstance(action_text, str) or not action_text.strip():
            continue

        valid_evidence = []
        chunk_ids = []
        for evidence in evidence_by_action.get(action_id, []):
            if evidence.get("document_id") != document_id:
                continue
            citation = _make_citation(evidence, action_text, allowed)
            if citation is None:
                continue
            valid_evidence.append(evidence)
            chunk_id = citation["chunk_id"]
            chunk_ids.append(chunk_id)
            if chunk_id not in cited_chunk_ids:
                citations.append(citation)
                cited_chunk_ids.add(chunk_id)
        if not chunk_ids:
            continue

        concept_refs = _unique_strings(action.get("concept_ids"))
        disease_refs = _unique_strings(action.get("disease_ids"))
        findings.append({
            "claim": action_text,
            "chunk_ids": list(dict.fromkeys(chunk_ids)),
            "action_ids": [action_id],
            "disease_ids": disease_refs,
            "concept_ids": concept_refs,
        })
        graph_paths.append(_graph_path(action, valid_evidence, concept_refs))
        if document_id not in roles_by_document:
            roles_by_document[document_id] = []
            document_order.append(document_id)
        roles_by_document[document_id].extend(_role_list(action))

    if not findings:
        return _result("NO_EVIDENCE", ["허용 문서에서 비교 근거를 찾지 못함"])

    warnings = [
        _role_warning(document_id, roles_by_document[document_id])
        for document_id in document_order
    ]
    finding_documents = {
        citation["document_id"]
        for citation in citations
    }
    if len(finding_documents) >= 2:
        status = "OK"
    else:
        status = "PARTIAL"
        warnings.insert(0, "비교할 다른 문서 근거 없음")
    return {
        "tool_name": "crossdoc_graph_search",
        "status": status,  # type: ignore[typeddict-item]
        "findings": findings,
        "citations": citations,
        "graph_paths": graph_paths,
        "warnings": warnings,
    }


def crossdoc_graph_search(tool_input: ToolInput, *, deps=None) -> ToolResult:
    """ToolInput을 받아 문서별 행동·담당 근거를 반환한다."""
    owned_driver = None
    result = _result("ERROR")
    try:
        if deps is None:
            deps = _default_deps()
            owned_driver = deps["driver"]
        result = _search(tool_input, deps)
    except Exception as error:  # noqa: BLE001
        result = _result("ERROR", [type(error).__name__])
    finally:
        if owned_driver is not None:
            try:
                owned_driver.close()
            except Exception as error:  # noqa: BLE001
                result["warnings"].append(type(error).__name__)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="ActionConcept 기준 문서별 행동 비교 Tool")
    parser.add_argument("question", help="문서별 비교 질문")
    parser.add_argument("--concept", action="append", help="비교할 ActionConcept ID (반복 가능)")
    parser.add_argument("--save", help="입출력 fixture 이름")
    args = parser.parse_args()
    if args.save and not re.fullmatch(r"[A-Za-z0-9_-]+", args.save):
        parser.error("--save 이름에는 영문·숫자·하이픈·밑줄만 사용할 수 있습니다.")

    request: ToolInput = {
        "question": args.question,
        "task": "문서별 학교 행동과 담당 비교",
        "region": "NATIONAL",
        "allowed_document_ids": [],
    }
    if args.concept:
        request["context_entity_ids"] = [f"ActionConcept:{concept_id}" for concept_id in args.concept]

    deps = None
    result = _result("ERROR")
    try:
        deps = _default_deps()
        request["allowed_document_ids"] = _all_document_ids(deps["driver"], deps.get("database"))
        result = crossdoc_graph_search(request, deps=deps)
    except Exception as error:  # noqa: BLE001
        result = _result("ERROR", [type(error).__name__])
    finally:
        if deps is not None:
            try:
                deps["driver"].close()
            except Exception as error:  # noqa: BLE001
                result["warnings"].append(type(error).__name__)

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.save:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        request_path = FIXTURE_DIR / f"{args.save}_tool_input.json"
        result_path = FIXTURE_DIR / f"{args.save}_tool_result.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"저장: {request_path.name}, {result_path.name}")


if __name__ == "__main__":
    main()
