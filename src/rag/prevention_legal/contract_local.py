"""공통 계약 v1.2 의 임시 로컬 복사본 (유종혁 / prevention_legal).

팀 공통 파일 src/common/contracts.py 와 validation.py 가 병합되면 이 파일의 import 를 바꾼다.
정의와 검증 코드는 공통 계약 가이드 v1.2 의 8장(타입)·9장(최소 검증)을 그대로 옮겼다.
9장 검증은 '최소 출력 검사'이다. 팀 지시서 v1.1 §4-4 가 요구하는 추가 검증(중첩 필드 타입,
입력 검증, Neo4j 대조)은 공통 파일에서 구현한다.
"""
from typing import Literal, NotRequired, TypedDict

Domain = Literal["school", "respiratory", "prevention_legal"]
Status = Literal["ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ERROR"]
Region = Literal["NATIONAL", "GYEONGGI"]


class QueryInput(TypedDict):
    question: str
    region: Region
    allowed_document_ids: list[str]
    task: NotRequired[str]
    preferred_document_ids: NotRequired[list[str]]
    context_entity_ids: NotRequired[list[str]]


class Citation(TypedDict):
    document_id: str
    chunk_id: str
    pdf_page: int
    printed_page: int | None
    excerpt: str


class Finding(TypedDict):
    claim: str
    chunk_ids: list[str]
    action_ids: list[str]
    disease_ids: list[str]
    concept_ids: list[str]


class QueryOutput(TypedDict):
    domain: Domain
    status: Status
    findings: list[Finding]
    citations: list[Citation]
    action_ids: list[str]
    warnings: list[str]
    answer: str


def validate_query_output(result: dict, request: dict) -> None:
    """공통 계약 가이드 v1.2 9장의 최소 출력 검사. 문제가 있으면 ValueError."""
    required = {
        "domain", "status", "findings", "citations",
        "action_ids", "warnings", "answer",
    }
    if not required.issubset(result):
        raise ValueError(f"Missing keys: {required - set(result)}")

    if result["domain"] not in {"school", "respiratory", "prevention_legal"}:
        raise ValueError("Unknown domain")
    if result["status"] not in {
        "ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ERROR"
    }:
        raise ValueError("Unknown status")

    allowed = set(request["allowed_document_ids"])
    citations = result["citations"]
    chunks = {}
    for c in citations:
        if c["document_id"] not in allowed:
            raise ValueError("Citation outside allowed documents")
        if c["chunk_id"] in chunks:
            raise ValueError("Duplicate citation chunk_id")
        if not isinstance(c["pdf_page"], int) or isinstance(c["pdf_page"], bool) or c["pdf_page"] < 1:
            raise ValueError("Invalid PDF page")
        if not isinstance(c["excerpt"], str) or not c["excerpt"].strip():
            raise ValueError("Empty citation excerpt")
        chunks[c["chunk_id"]] = c

    used_actions = set()
    for finding in result["findings"]:
        if not finding["claim"].strip() or not finding["chunk_ids"]:
            raise ValueError("Finding missing claim or sources")
        if not set(finding["chunk_ids"]).issubset(chunks):
            raise ValueError("Finding cites a missing chunk")
        used_actions.update(finding["action_ids"])

    if set(result["action_ids"]) != used_actions:
        raise ValueError("Top-level action_ids must match findings")
    if result["status"] in {"ANSWERED", "PARTIAL"} and not result["findings"]:
        raise ValueError("Answered/partial result requires a finding")
    if result["status"] in {"INSUFFICIENT_EVIDENCE", "ERROR"} and result["findings"]:
        raise ValueError("No unsupported findings on no-evidence/error result")
