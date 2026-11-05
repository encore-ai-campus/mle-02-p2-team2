"""공통 계약 v2.0 임시본 (school 개인 범위) — B02.

- 타입은 `지시서들/new/교육시설_감염병_LangGraph_공통계약_가이드_v2.0.md` §9를 **글자 그대로** 옮겼다.
  필드 이름·뜻을 바꾸지 않는다. 유종혁의 공통 타입·검증기가 병합되면 import만 바꾼다
  (교체 전 이 파일과 공통본의 차이를 먼저 비교한다).
- 검사 함수는 §11 `validate_tool_result` 원문을 기준으로 하고, B02 지시서의 추가 검사
  (호출한 Tool 이름과 반환 tool_name 비교, 정수/불리언 쪽수 구분, 중첩 타입, request_id 수용)와
  §12 최종 응답 규칙을 더했다.
- 구조 검사는 DB 진위를 판정하지 못한다. Neo4j 대조는 `action_tool.verify_tool_result_db`가 한다.
- v1.2 검사기(`contract_local.py`)는 회귀용으로 남기고 v2 값을 넣지 않는다.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

# ---------------------------------------------------------------- §9 타입 (원문 그대로)

Region = Literal["NATIONAL", "GYEONGGI"]
ToolName = Literal[
    "action_search", "disease_search",
    "legal_search", "crossdoc_graph_search"
]
ToolStatus = Literal["OK", "PARTIAL", "NO_EVIDENCE", "ERROR"]
AnswerStatus = Literal[
    "ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ERROR"
]
ResponseType = Literal["DOMAIN", "GENERAL", "OUT_OF_SCOPE"]


class QueryInput(TypedDict):
    question: str
    region: Region
    allowed_document_ids: list[str]
    request_id: NotRequired[str]
    task: NotRequired[str]
    preferred_document_ids: NotRequired[list[str]]
    context_entity_ids: NotRequired[list[str]]


class ToolInput(TypedDict):
    question: str
    task: str
    region: Region
    allowed_document_ids: list[str]
    preferred_document_ids: NotRequired[list[str]]
    context_entity_ids: NotRequired[list[str]]
    top_k: NotRequired[int]


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


class GraphEdge(TypedDict):
    source_ref: str
    relationship_type: str
    target_ref: str


class GraphPath(TypedDict):
    edges: list[GraphEdge]


class ToolResult(TypedDict):
    tool_name: ToolName
    status: ToolStatus
    findings: list[Finding]
    citations: list[Citation]
    graph_paths: list[GraphPath]
    warnings: list[str]


class QueryOutput(TypedDict):
    response_type: ResponseType
    status: AnswerStatus
    answer: str
    findings: list[Finding]
    citations: list[Citation]
    action_ids: list[str]
    warnings: list[str]
    tools_used: list[ToolName]
    search_rounds: int


# ---------------------------------------------------------------- 런타임 검사

REGIONS = {"NATIONAL", "GYEONGGI"}
TOOL_NAMES = {"action_search", "disease_search", "legal_search", "crossdoc_graph_search"}
TOOL_STATUSES = {"OK", "PARTIAL", "NO_EVIDENCE", "ERROR"}
ANSWER_STATUSES = {"ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ERROR"}
RESPONSE_TYPES = {"DOMAIN", "GENERAL", "OUT_OF_SCOPE"}

QUERY_KEYS = ({"question", "region", "allowed_document_ids"},
              {"request_id", "task", "preferred_document_ids", "context_entity_ids"})
TOOL_INPUT_KEYS = ({"question", "task", "region", "allowed_document_ids"},
                   {"preferred_document_ids", "context_entity_ids", "top_k"})
TOOL_RESULT_KEYS = {"tool_name", "status", "findings", "citations", "graph_paths", "warnings"}
QUERY_OUTPUT_KEYS = {"response_type", "status", "answer", "findings", "citations", "action_ids",
                     "warnings", "tools_used", "search_rounds"}
FINDING_KEYS = {"claim", "chunk_ids", "action_ids", "disease_ids", "concept_ids"}
CITATION_KEYS = {"document_id", "chunk_id", "pdf_page", "printed_page", "excerpt"}
EDGE_KEYS = {"source_ref", "relationship_type", "target_ref"}


class ContractError(ValueError):
    """계약 위반(구조). DB 진위 실패와 구분한다."""


def _is_int(value) -> bool:
    """bool은 int의 하위형이지만 쪽수·횟수로 받지 않는다."""
    return type(value) is int


def _str_list(value, name: str, *, nonempty_items: bool = True) -> None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ContractError(f"{name}은 문자열 목록이어야 함")
    if nonempty_items and any(not v.strip() for v in value):
        raise ContractError(f"{name}에 빈 문자열")


def _keys(obj, required: set[str], optional: set[str], name: str) -> None:
    if not isinstance(obj, dict):
        raise ContractError(f"{name}은 dict여야 함")
    missing = required - obj.keys()
    extra = obj.keys() - required - optional
    if missing:
        raise ContractError(f"{name} 필수 키 누락: {sorted(missing)}")
    if extra:
        raise ContractError(f"{name}에 계약 밖 키: {sorted(extra)}")


def _scope(obj: dict, name: str) -> None:
    if not isinstance(obj["question"], str) or not obj["question"].strip():
        raise ContractError(f"{name}.question이 비었음")
    if obj["region"] not in REGIONS:
        raise ContractError(f"{name}.region 허용값 아님: {obj['region']!r}")
    _str_list(obj["allowed_document_ids"], f"{name}.allowed_document_ids")
    allowed = set(obj["allowed_document_ids"])
    if "preferred_document_ids" in obj:
        _str_list(obj["preferred_document_ids"], f"{name}.preferred_document_ids")
        if not set(obj["preferred_document_ids"]) <= allowed:
            raise ContractError("preferred docs are outside allowed docs")
    if "context_entity_ids" in obj:
        _str_list(obj["context_entity_ids"], f"{name}.context_entity_ids")
        bad = [x for x in obj["context_entity_ids"] if ":" not in x or not x.split(":", 1)[1]]
        if bad:
            raise ContractError(f"context_entity_ids는 '라벨:ID' 형식이어야 함: {bad}")


def validate_query_input(query: dict) -> None:
    """메인 그래프 입구 검사. request_id는 선택 문자열로 받는다(v1.2 검사기와 다름)."""
    _keys(query, *QUERY_KEYS, "QueryInput")
    _scope(query, "QueryInput")
    for key in ("request_id", "task"):
        if key in query and (not isinstance(query[key], str) or not query[key].strip()):
            raise ContractError(f"QueryInput.{key}는 비어 있지 않은 문자열")


def validate_tool_input(tool_input: dict) -> None:
    _keys(tool_input, *TOOL_INPUT_KEYS, "ToolInput")
    _scope(tool_input, "ToolInput")
    if not isinstance(tool_input["task"], str) or not tool_input["task"].strip():
        raise ContractError("ToolInput.task는 필수(이번 Tool에서 확인할 세부 작업)")
    if "top_k" in tool_input and (not _is_int(tool_input["top_k"]) or tool_input["top_k"] < 1):
        raise ContractError("top_k는 1 이상의 정수")


def _citations(citations, allowed: set[str], name: str) -> dict[str, dict]:
    if not isinstance(citations, list):
        raise ContractError(f"{name}.citations는 목록")
    chunks: dict[str, dict] = {}
    for citation in citations:
        _keys(citation, CITATION_KEYS, set(), "Citation")
        if citation["document_id"] not in allowed:
            raise ContractError("Citation outside allowed documents")
        if not isinstance(citation["chunk_id"], str) or not citation["chunk_id"] or citation["chunk_id"] in chunks:
            raise ContractError("Missing or duplicate chunk_id")
        if not _is_int(citation["pdf_page"]) or citation["pdf_page"] < 1:
            raise ContractError("Invalid 1-based pdf_page")
        printed = citation["printed_page"]
        if printed is not None and (not _is_int(printed) or printed < 1):
            raise ContractError("printed_page는 1 이상 정수 또는 None")
        if not isinstance(citation["excerpt"], str) or not citation["excerpt"].strip():
            raise ContractError("Empty excerpt")
        chunks[citation["chunk_id"]] = citation
    return chunks


def _findings(findings, chunks: dict, name: str) -> None:
    if not isinstance(findings, list):
        raise ContractError(f"{name}.findings는 목록")
    for finding in findings:
        _keys(finding, FINDING_KEYS, set(), "Finding")
        if not isinstance(finding["claim"], str) or not finding["claim"].strip():
            raise ContractError("Empty claim")
        for key in ("chunk_ids", "action_ids", "disease_ids", "concept_ids"):
            _str_list(finding[key], f"Finding.{key}")
        ids = finding["chunk_ids"]
        if not ids or not set(ids) <= chunks.keys():
            raise ContractError("Finding has missing Citation")
        # 타입별 ID 목록에는 'SchoolAction:' 같은 접두어를 붙이지 않는다(§3)
        for key, label in (("action_ids", "SchoolAction:"), ("concept_ids", "ActionConcept:"),
                           ("disease_ids", "Disease:")):
            if any(v.startswith(label) for v in finding[key]):
                raise ContractError(f"Finding.{key}에 라벨 접두어를 붙이지 않음")


def validate_graph_paths(paths) -> None:
    if not isinstance(paths, list):
        raise ContractError("graph_paths는 목록")
    for path in paths:
        _keys(path, {"edges"}, set(), "GraphPath")
        if not isinstance(path["edges"], list) or not path["edges"]:
            raise ContractError("GraphPath.edges는 비어 있지 않은 목록")
        for edge in path["edges"]:
            _keys(edge, EDGE_KEYS, set(), "GraphEdge")
            for key in ("source_ref", "target_ref"):
                ref = edge[key]
                if not isinstance(ref, str) or ":" not in ref or not all(split_ref(ref)):
                    raise ContractError(f"GraphEdge.{key}는 '라벨:ID' 형식: {ref!r}")
            if not isinstance(edge["relationship_type"], str) or not edge["relationship_type"].isupper():
                raise ContractError(f"relationship_type 형식 오류: {edge['relationship_type']!r}")


def split_ref(ref: str) -> tuple[str, str]:
    """'SchoolAction:moe:p0036:a01' → ('SchoolAction', 'moe:p0036:a01'). **첫 ':'만** 타입 구분자다(M04)."""
    label, _, node_id = ref.partition(":")
    return label, node_id


def validate_tool_result(result: dict, request: dict, expected_tool: str | None = None) -> None:
    """§11 최소 런타임 검사 + B02 추가 검사. 실패하면 ContractError."""
    _keys(result, TOOL_RESULT_KEYS, set(), "ToolResult")
    if result["tool_name"] not in TOOL_NAMES:
        raise ContractError("Unknown tool_name")
    if expected_tool is not None and result["tool_name"] != expected_tool:
        raise ContractError(f"호출한 Tool({expected_tool})과 반환 tool_name({result['tool_name']}) 불일치")
    if result["status"] not in TOOL_STATUSES:
        raise ContractError("Unknown status")
    allowed = set(request["allowed_document_ids"])
    if not set(request.get("preferred_document_ids", [])) <= allowed:
        raise ContractError("preferred docs are outside allowed docs")
    chunks = _citations(result["citations"], allowed, "ToolResult")
    _findings(result["findings"], chunks, "ToolResult")
    validate_graph_paths(result["graph_paths"])
    _str_list(result["warnings"], "ToolResult.warnings")
    if result["status"] in {"OK", "PARTIAL"} and not result["findings"]:
        raise ContractError("Successful/partial result needs verified Finding")
    if result["status"] in {"NO_EVIDENCE", "ERROR"} and (result["findings"] or result["citations"]):
        raise ContractError("No unverified claims on missing/effectively failed result")
    if result["status"] in {"NO_EVIDENCE", "ERROR"} and not result["warnings"]:
        raise ContractError("근거 없음·오류는 warnings에 사유를 남겨야 함")


def validate_query_output(output: dict, request: dict) -> None:
    """§8·§12 최종 응답 검사."""
    _keys(output, QUERY_OUTPUT_KEYS, set(), "QueryOutput")
    if output["response_type"] not in RESPONSE_TYPES:
        raise ContractError("Unknown response_type")
    if output["status"] not in ANSWER_STATUSES:
        raise ContractError("Unknown answer status")
    if not isinstance(output["answer"], str):
        raise ContractError("answer는 문자열")
    chunks = _citations(output["citations"], set(request["allowed_document_ids"]), "QueryOutput")
    _findings(output["findings"], chunks, "QueryOutput")
    _str_list(output["action_ids"], "QueryOutput.action_ids")
    _str_list(output["warnings"], "QueryOutput.warnings")
    if not isinstance(output["tools_used"], list) or not set(output["tools_used"]) <= TOOL_NAMES:
        raise ContractError("tools_used는 승인된 Tool 이름 목록")
    if not _is_int(output["search_rounds"]) or output["search_rounds"] < 0:
        raise ContractError("search_rounds는 0 이상 정수")
    if output["search_rounds"] != len(output["tools_used"]):
        raise ContractError("search_rounds는 실제 Tool 호출 횟수(tools_used 길이)와 같아야 함")
    union = list(dict.fromkeys(a for f in output["findings"] for a in f["action_ids"]))
    if sorted(output["action_ids"]) != sorted(union) or len(output["action_ids"]) != len(set(output["action_ids"])):
        raise ContractError("action_ids는 Finding.action_ids의 중복 제거 합집합")
    kind, status = output["response_type"], output["status"]
    if kind == "DOMAIN" and status in {"ANSWERED", "PARTIAL"} and not (output["findings"] and output["citations"]):
        raise ContractError("DOMAIN ANSWERED/PARTIAL은 Finding과 Citation 필수")
    if kind in {"GENERAL", "OUT_OF_SCOPE"}:
        if output["findings"] or output["citations"] or output["action_ids"]:
            raise ContractError(f"{kind}에는 findings·citations·action_ids를 두지 않음")
        if output["tools_used"]:
            raise ContractError(f"{kind}는 Tool을 호출하지 않음")
    if kind == "GENERAL" and status != "ANSWERED":
        raise ContractError("GENERAL은 ANSWERED")
    if kind == "OUT_OF_SCOPE" and status != "INSUFFICIENT_EVIDENCE":
        raise ContractError("OUT_OF_SCOPE는 INSUFFICIENT_EVIDENCE")
    if status in {"INSUFFICIENT_EVIDENCE", "ERROR"} and (output["findings"] or output["citations"]):
        raise ContractError("근거 부족·오류 응답에 검증되지 않은 주장을 두지 않음")
