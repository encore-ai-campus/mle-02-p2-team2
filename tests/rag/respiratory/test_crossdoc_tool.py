from __future__ import annotations

from index.school.dictionaries import load_dictionaries
from rag.respiratory.crossdoc_tool import (
    _select_concepts,
    crossdoc_graph_search,
)
from rag.respiratory.disease_tool import ToolInput
from rag.school.search import SearchError


def validate_tool_result(result: dict, request: dict) -> None:
    required = {
        "tool_name", "status", "findings", "citations",
        "graph_paths", "warnings"
    }
    if not required.issubset(result):
        raise ValueError(f"Missing ToolResult keys: {required - result.keys()}")

    tool_names = {
        "action_search", "disease_search", "legal_search",
        "crossdoc_graph_search"
    }
    if result["tool_name"] not in tool_names:
        raise ValueError("Unknown tool_name")
    if result["status"] not in {"OK", "PARTIAL", "NO_EVIDENCE", "ERROR"}:
        raise ValueError("Unknown status")

    allowed = set(request["allowed_document_ids"])
    if not set(request.get("preferred_document_ids", [])).issubset(allowed):
        raise ValueError("preferred docs are outside allowed docs")

    chunks = {}
    for citation in result["citations"]:
        if citation["document_id"] not in allowed:
            raise ValueError("Citation outside allowed documents")
        if not citation["chunk_id"] or citation["chunk_id"] in chunks:
            raise ValueError("Missing or duplicate chunk_id")
        p = citation["pdf_page"]
        if type(p) is not int or p < 1:
            raise ValueError("Invalid 1-based pdf_page")
        if not isinstance(citation["excerpt"], str) or not citation["excerpt"].strip():
            raise ValueError("Empty excerpt")
        chunks[citation["chunk_id"]] = citation

    for finding in result["findings"]:
        if not finding["claim"].strip():
            raise ValueError("Empty claim")
        ids = finding["chunk_ids"]
        if not ids or not set(ids).issubset(chunks):
            raise ValueError("Finding has missing Citation")

    if result["status"] in {"OK", "PARTIAL"} and not result["findings"]:
        raise ValueError("Successful/partial result needs verified Finding")
    if result["status"] in {"NO_EVIDENCE", "ERROR"} and (
        result["findings"] or result["citations"]
    ):
        raise ValueError("No unverified claims on missing/effectively failed result")


class FakeDriver:
    def close(self):
        pass


def make_tool_input(
    question: str = "두 문서에서 보호자 연락은 누가 하나요?",
    *,
    allowed: list[str] | None = None,
    context_entity_ids: list[str] | None = None,
) -> ToolInput:
    request: ToolInput = {
        "question": question,
        "task": "문서별 학교 행동과 담당 비교",
        "region": "NATIONAL",
        "allowed_document_ids": allowed or ["doc_a", "doc_b"],
    }
    if context_entity_ids is not None:
        request["context_entity_ids"] = context_entity_ids
    return request


def make_action(
    document_id: str,
    action_id: str,
    chunk_id: str,
    *,
    text: str = "담임교사는 보호자에게 연락한다.",
    concept_ids: list[str] | None = None,
    disease_ids: list[str] | None = None,
    roles: list[dict] | None = None,
) -> dict:
    return {
        "document_id": document_id,
        "action_id": action_id,
        "action_text": text,
        "chunk_id": chunk_id,
        "concept_ids": concept_ids or ["CONTACT_GUARDIAN"],
        "disease_ids": disease_ids or [],
        "roles": roles or [
            {"role_id": "HOMEROOM_TEACHER", "name": "담임교사", "basis": "EXPLICIT"}
        ],
    }


def make_evidence(action: dict) -> dict:
    return {
        "action_id": action["action_id"],
        "document_id": action["document_id"],
        "chunk_id": action["chunk_id"],
        "pdf_page": 12,
        "printed_page": 8,
        "text": f"원문 앞부분. {action['action_text']} 원문 뒷부분.",
    }


def make_deps() -> dict:
    return {
        "driver": FakeDriver(),
        "database": "fake",
        "books": load_dictionaries(),
    }


def patch_search(monkeypatch, actions: list[dict], evidence: list[dict] | None = None,
                 *, error: Exception | None = None, captured: dict | None = None) -> None:
    def compare_action_sources(driver, concept_ids, allowed, *, limit, database):
        if captured is not None:
            captured.update({
                "concept_ids": concept_ids,
                "allowed": allowed,
                "limit": limit,
                "database": database,
            })
        if error is not None:
            raise error
        return [
            action
            for action in actions
            if action["document_id"] in allowed
        ]

    evidence_rows = evidence if evidence is not None else [make_evidence(action) for action in actions]

    def get_action_evidence(driver, action_ids, allowed, *, database):
        return [
            row for row in evidence_rows
            if row["action_id"] in action_ids and row["document_id"] in allowed
        ]

    monkeypatch.setattr("rag.respiratory.crossdoc_tool.compare_action_sources", compare_action_sources)
    monkeypatch.setattr("rag.respiratory.crossdoc_tool.get_action_evidence", get_action_evidence)


def test_context_concept_returns_separate_findings_and_valid_result(monkeypatch):
    actions = [
        make_action("doc_a", "doc_a:p0012:a01", "doc_a:p0012:b01"),
        make_action(
            "doc_b",
            "doc_b:p0040:a02",
            "doc_b:p0040:b03",
            text="보건교사는 보호자에게 연락한다.",
            roles=[{"role_id": "HEALTH_TEACHER", "name": "보건교사", "basis": "EXAMPLE"}],
        ),
    ]
    patch_search(monkeypatch, actions)
    request = make_tool_input(
        context_entity_ids=["ActionConcept:CONTACT_GUARDIAN"],
    )

    result = crossdoc_graph_search(request, deps=make_deps())

    validate_tool_result(result, request)
    assert result["status"] == "OK"
    assert result["tool_name"] == "crossdoc_graph_search"
    assert len(result["findings"]) == 2
    assert [finding["action_ids"] for finding in result["findings"]] == [
        ["doc_a:p0012:a01"],
        ["doc_b:p0040:a02"],
    ]
    assert [citation["document_id"] for citation in result["citations"]] == ["doc_a", "doc_b"]
    assert result["findings"][0]["claim"] == actions[0]["action_text"]
    assert result["findings"][1]["claim"] == actions[1]["action_text"]


def test_name_fragment_scoring_prefers_contact_guardian():
    request = make_tool_input("보호자 연락은 문서마다 누가 하나요?")
    concept_ids = _select_concepts(request, load_dictionaries())

    assert concept_ids == ["CONTACT_GUARDIAN"]


def test_single_document_returns_partial(monkeypatch):
    action = make_action("doc_a", "doc_a:p0001:a01", "doc_a:p0001:b01")
    patch_search(monkeypatch, [action])
    request = make_tool_input(allowed=["doc_a"])

    result = crossdoc_graph_search(request, deps=make_deps())

    assert result["status"] == "PARTIAL"
    assert "비교할 다른 문서 근거 없음" in result["warnings"]
    assert result["warnings"][-1].startswith("doc_a:담당 ")


def test_missing_concept_returns_no_evidence_without_search(monkeypatch):
    def unexpected_search(*args, **kwargs):
        raise AssertionError("검색하면 안 됨")

    monkeypatch.setattr("rag.respiratory.crossdoc_tool.compare_action_sources", unexpected_search)
    request = make_tool_input("오늘 날씨 알려줘")

    result = crossdoc_graph_search(request, deps=make_deps())

    assert result["status"] == "NO_EVIDENCE"
    assert result["warnings"] == ["질문에서 비교할 행동 개념을 찾지 못함"]
    assert result["findings"] == []
    assert result["citations"] == []


def test_search_error_returns_empty_error_result(monkeypatch):
    patch_search(monkeypatch, [], error=SearchError("private details"))
    request = make_tool_input(context_entity_ids=["ActionConcept:CONTACT_GUARDIAN"])

    result = crossdoc_graph_search(request, deps=make_deps())

    assert result["status"] == "ERROR"
    assert result["warnings"] == ["SearchError"]
    assert result["findings"] == []
    assert result["citations"] == []


def test_graph_edges_keep_contract_directions(monkeypatch):
    action = make_action("doc_a", "doc_a:p0001:a01", "doc_a:p0001:b01")
    patch_search(monkeypatch, [action])
    request = make_tool_input(allowed=["doc_a"], context_entity_ids=["ActionConcept:CONTACT_GUARDIAN"])

    result = crossdoc_graph_search(request, deps=make_deps())

    edges = result["graph_paths"][0]["edges"]
    edge_tuples = {
        (edge["source_ref"], edge["relationship_type"], edge["target_ref"])
        for edge in edges
    }
    assert ("SchoolAction:doc_a:p0001:a01", "INSTANCE_OF", "ActionConcept:CONTACT_GUARDIAN") in edge_tuples
    assert (
        "SchoolAction:doc_a:p0001:a01",
        "SUPPORTED_BY",
        "Chunk:doc_a:p0001:b01",
    ) in edge_tuples
    assert (
        "Document:doc_a",
        "HAS_CHUNK",
        "Chunk:doc_a:p0001:b01",
    ) in edge_tuples
    assert (
        "SchoolAction:doc_a:p0001:a01",
        "DONE_BY",
        "SchoolRole:HOMEROOM_TEACHER",
    ) in edge_tuples


def test_named_disease_actions_are_selected_first(monkeypatch):
    # 같은 문서에 질병 무관 행동이 앞 번호로 많아도, 질문 질병(수족구병)에 연결된 행동이 상한 안에 든다.
    filler = [
        make_action("doc_a", f"doc_a:p0010:a{i:02d}", f"doc_a:p0010:b{i:02d}", text=f"일반 등교중지 안내 {i}",
                    concept_ids=["EXCLUDE_FROM_SCHOOL"])
        for i in range(1, 6)
    ]
    target = make_action("doc_a", "doc_a:p0049:a01", "doc_a:p0049:t01r03", text="수족구병 환자는 수포 발생 후 6일간 등교 중지",
                         concept_ids=["EXCLUDE_FROM_SCHOOL"], disease_ids=["HFMD"])
    other = make_action("doc_b", "doc_b:p0030:a01", "doc_b:p0030:b01", text="발열이 없고 수포가 마른 후 복귀",
                        concept_ids=["EXCLUDE_FROM_SCHOOL"], disease_ids=["HFMD"])
    patch_search(monkeypatch, [*filler, target, other])
    request = make_tool_input("수족구병 등교중지 기간은 문서마다 어떻게 다른가요?",
                              context_entity_ids=["ActionConcept:EXCLUDE_FROM_SCHOOL"])
    request["top_k"] = 1

    result = crossdoc_graph_search(request, deps=make_deps())

    action_ids = [a for f in result["findings"] for a in f["action_ids"]]
    assert action_ids == ["doc_a:p0049:a01", "doc_b:p0030:a01"]
    edges = {(e["source_ref"], e["relationship_type"], e["target_ref"])
             for p in result["graph_paths"] for e in p["edges"]}
    assert ("SchoolAction:doc_a:p0049:a01", "FOR_DISEASE", "Disease:HFMD") in edges


def test_without_named_disease_order_is_unchanged(monkeypatch):
    actions = [
        make_action("doc_a", "doc_a:p0001:a01", "doc_a:p0001:b01"),
        make_action("doc_a", "doc_a:p0002:a01", "doc_a:p0002:b01", disease_ids=["MEASLES"]),
    ]
    patch_search(monkeypatch, actions)
    request = make_tool_input(allowed=["doc_a"], context_entity_ids=["ActionConcept:CONTACT_GUARDIAN"])
    request["top_k"] = 1

    result = crossdoc_graph_search(request, deps=make_deps())

    assert [a for f in result["findings"] for a in f["action_ids"]] == ["doc_a:p0001:a01"]
