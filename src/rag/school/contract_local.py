"""공통 계약 v1.2의 school 임시 로컬 복사본 (주승우 / school).

src/common/contracts.py·validation.py가 병합되면 이 파일 대신 공통 모듈을 import한다.
- 타입: 계약 가이드 v1.2 8장 그대로.
- validate_query_output: 계약 가이드 v1.2 9장 최소 검사 + 팀 지시서 v1.2 4-4의 추가 검사
  (중첩 객체 필수 키·타입, 상태별 빈 목록, 근거 부족·오류 사유).
- validate_query_input: 팀 지시서 v1.2 4-2 run() 호출 전 입력 검사.
Neo4j 대조 검증(존재·HAS_CHUNK·쪽수·excerpt·SUPPORTED_BY)은 이 파일 범위 밖이다.
"""

from typing import Literal, NotRequired, TypedDict

Domain = Literal["school", "respiratory", "prevention_legal"]
Status = Literal["ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ERROR"]
Region = Literal["NATIONAL", "GYEONGGI"]

DOMAINS = {"school", "respiratory", "prevention_legal"}
STATUSES = {"ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ERROR"}
REGIONS = {"NATIONAL", "GYEONGGI"}


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


class ContractError(ValueError):
    """계약 위반."""


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_page(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def validate_query_input(query: dict) -> None:
    """run() 호출 전 QueryInput 검사. 잘못된 입력은 ContractError."""
    if not isinstance(query, dict):
        raise ContractError("QueryInput은 dict여야 함")
    for key in ("question", "region", "allowed_document_ids"):
        if key not in query:
            raise ContractError(f"QueryInput 필수 키 없음: {key}")
    allowed_keys = {"question", "region", "allowed_document_ids", "task",
                    "preferred_document_ids", "context_entity_ids"}
    extra = set(query) - allowed_keys
    if extra:
        raise ContractError(f"QueryInput에 없는 키: {sorted(extra)}")
    if not isinstance(query["question"], str) or not query["question"].strip():
        raise ContractError("question은 비어 있지 않은 문자열")
    if query["region"] not in REGIONS:
        raise ContractError(f"region 값 오류: {query['region']!r}")
    if not _is_str_list(query["allowed_document_ids"]):
        raise ContractError("allowed_document_ids는 문자열 목록 ([]는 '허용 문서 없음')")
    if "task" in query and not isinstance(query["task"], str):
        raise ContractError("task는 문자열")
    for key in ("preferred_document_ids", "context_entity_ids"):
        if key in query and not _is_str_list(query[key]):
            raise ContractError(f"{key}는 문자열 목록")
    preferred = set(query.get("preferred_document_ids", []))
    if not preferred <= set(query["allowed_document_ids"]):
        raise ContractError("preferred_document_ids는 allowed_document_ids의 부분집합이어야 함")
    for entity in query.get("context_entity_ids", []):
        label, sep, ident = entity.partition(":")
        if not sep or not label or not ident:
            raise ContractError(f"context_entity_ids는 '노드라벨:ID' 형식: {entity!r}")


def validate_query_output(result: dict, request: dict) -> None:
    """QueryOutput 구조 검사(계약 9장 + 팀 지시서 4-4 추가 검사). 위반 시 ContractError."""
    required = {"domain", "status", "findings", "citations", "action_ids", "warnings", "answer"}
    if not isinstance(result, dict) or not required.issubset(result):
        missing = required - set(result) if isinstance(result, dict) else required
        raise ContractError(f"Missing keys: {sorted(missing)}")
    extra = set(result) - required
    if extra:
        raise ContractError(f"QueryOutput에 계약 밖 키: {sorted(extra)}")
    if result["domain"] not in DOMAINS:
        raise ContractError("Unknown domain")
    if result["status"] not in STATUSES:
        raise ContractError("Unknown status")
    if not isinstance(result["findings"], list) or not isinstance(result["citations"], list):
        raise ContractError("findings·citations는 목록")
    if not _is_str_list(result["action_ids"]) or not _is_str_list(result["warnings"]):
        raise ContractError("action_ids·warnings는 문자열 목록")
    if not isinstance(result["answer"], str):
        raise ContractError("answer는 문자열")

    allowed = set(request["allowed_document_ids"])
    chunks: dict[str, dict] = {}
    for citation in result["citations"]:
        keys = {"document_id", "chunk_id", "pdf_page", "printed_page", "excerpt"}
        if not isinstance(citation, dict) or set(citation) != keys:
            raise ContractError("Citation 키 오류")
        if not isinstance(citation["document_id"], str) or not isinstance(citation["chunk_id"], str):
            raise ContractError("Citation document_id·chunk_id는 문자열")
        if citation["document_id"] not in allowed:
            raise ContractError("Citation outside allowed documents")
        if citation["chunk_id"] in chunks:
            raise ContractError("Duplicate citation chunk_id")
        if not _is_page(citation["pdf_page"]):
            raise ContractError("Invalid PDF page")
        if citation["printed_page"] is not None and not _is_page(citation["printed_page"]):
            raise ContractError("Invalid printed page")
        if not isinstance(citation["excerpt"], str) or not citation["excerpt"].strip():
            raise ContractError("Empty citation excerpt")
        chunks[citation["chunk_id"]] = citation

    used_actions: set[str] = set()
    for finding in result["findings"]:
        keys = {"claim", "chunk_ids", "action_ids", "disease_ids", "concept_ids"}
        if not isinstance(finding, dict) or set(finding) != keys:
            raise ContractError("Finding 키 오류")
        if not isinstance(finding["claim"], str) or not finding["claim"].strip():
            raise ContractError("Finding missing claim")
        for key in ("chunk_ids", "action_ids", "disease_ids", "concept_ids"):
            if not _is_str_list(finding[key]):
                raise ContractError(f"Finding.{key}는 문자열 목록")
        if not finding["chunk_ids"]:
            raise ContractError("Finding missing sources")
        if not set(finding["chunk_ids"]).issubset(chunks):
            raise ContractError("Finding cites a missing chunk")
        # 타입이 정해진 목록에는 라벨 접두어를 쓰지 않는다(온톨로지 v3.1 6절).
        for key in ("disease_ids", "concept_ids"):
            if any(":" in value for value in finding[key]):
                raise ContractError(f"Finding.{key}에 라벨 접두어 금지")
        used_actions.update(finding["action_ids"])

    if set(result["action_ids"]) != used_actions or len(result["action_ids"]) != len(used_actions):
        raise ContractError("Top-level action_ids must be the de-duplicated union of findings")
    if result["status"] in {"ANSWERED", "PARTIAL"} and not result["findings"]:
        raise ContractError("Answered/partial result requires a finding")
    if result["status"] in {"INSUFFICIENT_EVIDENCE", "ERROR"}:
        if result["findings"] or result["citations"] or result["action_ids"]:
            raise ContractError("No findings/citations/action_ids on no-evidence/error result")
        if not result["warnings"]:
            raise ContractError("근거 부족·오류 사유를 warnings에 기록해야 함")
    if result["status"] == "PARTIAL" and not result["warnings"]:
        raise ContractError("PARTIAL은 빠진 부분을 warnings에 기록해야 함")


def empty_output(domain: str, status: str, reason: str) -> QueryOutput:
    """근거 부족·오류용 완전한 QueryOutput (팀 지시서 4-3 분기 실패 형태)."""
    if status not in {"INSUFFICIENT_EVIDENCE", "ERROR"}:
        raise ContractError("empty_output은 INSUFFICIENT_EVIDENCE·ERROR 전용")
    return {"domain": domain, "status": status, "findings": [], "citations": [],
            "action_ids": [], "warnings": [reason], "answer": ""}
