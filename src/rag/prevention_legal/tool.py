"""LegalSearchTool — 예방·법령 검색을 공통 Tool 계약(v2.0)으로 감싸는 어댑터 (유종혁 / prevention_legal).

- 입력: ToolInput (메인 LangGraph → Tool)
- 출력: ToolResult (Tool → 메인 LangGraph)
- 내부 검색·답변은 run.py 의 run() 을 그대로 쓴다. 이 파일은 모양만 바꾼다.

주의: ToolInput·ToolResult·Finding·Citation 타입은 공통계약 v2.0 의 임시 복사본이다.
      팀 공통 파일이 병합되면 그 파일에서 import 하도록 바꾼다 (차이가 없는지 먼저 확인).
"""
from typing import Literal, NotRequired, TypedDict

from rag.prevention_legal.contract_local import Citation, Finding, QueryInput
from rag.prevention_legal.run import run

ToolName = Literal["action_search", "disease_search", "legal_search", "crossdoc_graph_search"]
ToolStatus = Literal["OK", "PARTIAL", "NO_EVIDENCE", "ERROR"]
Region = Literal["NATIONAL", "GYEONGGI"]


class ToolInput(TypedDict):
    question: str
    task: str
    region: Region
    allowed_document_ids: list[str]
    preferred_document_ids: NotRequired[list[str]]
    context_entity_ids: NotRequired[list[str]]
    top_k: NotRequired[int]


class GraphEdge(TypedDict):
    # 계약 v2.0 §9 와 같은 모양. 예: SchoolAction:<ID> --SUPPORTED_BY--> Chunk:<ID>
    source_ref: str
    relationship_type: str
    target_ref: str


class GraphPath(TypedDict):
    # 법령 Tool 은 지금 그래프 경로를 만들지 않는다 (graph_paths 는 빈 목록). 모양만 계약과 맞춘다.
    edges: list[GraphEdge]


class ToolResult(TypedDict):
    tool_name: ToolName
    status: ToolStatus
    findings: list[Finding]
    citations: list[Citation]
    graph_paths: list[GraphPath]
    warnings: list[str]


# run() 의 답 상태(QueryOutput) -> Tool 상태(ToolResult) 대응표
_STATUS_MAP = {
    "ANSWERED": "OK",
    "PARTIAL": "PARTIAL",
    "INSUFFICIENT_EVIDENCE": "NO_EVIDENCE",
    "ERROR": "ERROR",
}


def legal_search(tool_input: ToolInput) -> ToolResult:
    """질문 하나를 법령·신고 검색으로 처리하고 ToolResult 를 돌려준다. 실패하면 ERROR 로 돌려준다."""
    try:
        query: QueryInput = {
            "question": tool_input["question"],
            "region": tool_input["region"],
            "allowed_document_ids": tool_input["allowed_document_ids"],
        }
        out = run(query)
    except Exception as e:  # noqa: BLE001 - 모든 실행 실패를 ERROR 로 바꿔 근거 없음과 구분한다
        return {
            "tool_name": "legal_search",
            "status": "ERROR",
            "findings": [],
            "citations": [],
            "graph_paths": [],
            "warnings": [f"Tool 실행 실패: {type(e).__name__}"],
        }

    return {
        "tool_name": "legal_search",
        "status": _STATUS_MAP[out["status"]],
        "findings": out["findings"],
        "citations": out["citations"],
        "graph_paths": [],
        "warnings": out["warnings"],
    }


def validate_tool_result(result: dict, tool_input: ToolInput) -> None:
    """ToolResult 가 계약 v2.0 규칙을 지키는지 검사한다. 어기면 ValueError."""
    required = {"tool_name", "status", "findings", "citations", "graph_paths", "warnings"}
    if set(result) != required:   # 키가 많거나 적으면 모두 오류 (계약 v2.0 키 목록과 정확히 같아야 함)
        raise ValueError(f"ToolResult 키 불일치: 누락 {required - set(result)}, 추가 {set(result) - required}")
    if result["tool_name"] not in {"action_search", "disease_search", "legal_search", "crossdoc_graph_search"}:
        raise ValueError("알 수 없는 tool_name")
    if result["tool_name"] != "legal_search":   # 이 어댑터가 반환하는 Tool 이름은 legal_search 뿐이다
        raise ValueError("호출한 Tool(legal_search)과 반환 tool_name 이 다릅니다")
    if result["status"] not in {"OK", "PARTIAL", "NO_EVIDENCE", "ERROR"}:
        raise ValueError("알 수 없는 status")

    findings, citations = result["findings"], result["citations"]
    allowed = set(tool_input["allowed_document_ids"])
    chunk_ids = {c["chunk_id"] for c in citations}
    if len(chunk_ids) != len(citations):
        raise ValueError("Citation chunk_id 가 중복됩니다")
    for c in citations:
        if c["document_id"] not in allowed:
            raise ValueError("허용되지 않은 문서의 Citation 입니다")

    # 상태별 규칙 (계약 v2.0 표)
    st = result["status"]
    if st == "OK" and not (findings and citations):
        raise ValueError("OK 는 Finding 1개 이상과 Citation 이 필요합니다")
    if st in {"NO_EVIDENCE", "ERROR"} and (findings or citations):
        raise ValueError(f"{st} 는 findings·citations 가 비어 있어야 합니다")
    if st == "ERROR" and not result["warnings"]:
        raise ValueError("ERROR 는 오류 이유를 warnings 에 적어야 합니다")

    # 각 Finding 은 실제 Citation 을 가리켜야 한다
    for f in findings:
        if not f["chunk_ids"]:
            raise ValueError("Finding 에 chunk_ids 가 없습니다")
        missing = [cid for cid in f["chunk_ids"] if cid not in chunk_ids]
        if missing:
            raise ValueError(f"Finding 이 없는 Citation 을 가리킵니다: {missing}")
