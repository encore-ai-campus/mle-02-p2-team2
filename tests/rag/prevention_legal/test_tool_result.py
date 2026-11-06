"""LegalSearchTool 의 ToolResult 검사 (유종혁 / prevention_legal). DB·LLM 없이 실행한다.

설명
- validate_tool_result 가 잘못된 결과를 실제로 잡는지 확인한다 (키, 상태 규칙, Finding↔Citation, 허용 문서).
- legal_search 가 run() 실패를 ERROR 로 바꾸는지 확인한다. run() 은 가짜로 바꿔 DB 를 부르지 않는다.
- 한계: validate_tool_result 는 Citation 의 발췌가 DB 원문에 있는지는 보지 않는다.
  그 검사는 run.py 의 build_citations 가 맡으며, DB 가 필요하므로 이 파일에서는 다루지 않는다.
"""
import pytest

from rag.prevention_legal import tool
from rag.prevention_legal.tool import legal_search, validate_tool_result

DOC = "kdca_measles_2026"
CHUNK = "kdca_measles_2026:p0093:b06"
TOOL_INPUT = {
    "question": "홍역 검사 결과 환자가 아니면 언제 등교하나요?",
    "task": "legal_search",
    "region": "NATIONAL",
    "allowed_document_ids": [DOC],
}


def _citation(chunk_id=CHUNK, document_id=DOC):
    # 인용 한 건. 발췌 문구는 검사 대상이 아니므로 짧은 문자열을 쓴다.
    return {"document_id": document_id, "chunk_id": chunk_id, "pdf_page": 93,
            "printed_page": None, "excerpt": "익일 등교"}


def _finding(chunk_ids=(CHUNK,)):
    return {"claim": "익일 등교 안내", "chunk_ids": list(chunk_ids),
            "action_ids": [], "disease_ids": [], "concept_ids": []}


def _result(**overrides):
    # 정상 OK 결과를 기본으로 두고, 테스트마다 필요한 칸만 바꾼다.
    base = {
        "tool_name": "legal_search",
        "status": "OK",
        "findings": [_finding()],
        "citations": [_citation()],
        "graph_paths": [],
        "warnings": [],
    }
    base.update(overrides)
    return base


def test_normal_ok_result_passes():
    validate_tool_result(_result(), TOOL_INPUT)


def test_missing_key_is_rejected():
    result = _result()
    del result["warnings"]
    with pytest.raises(ValueError, match="키 불일치"):
        validate_tool_result(result, TOOL_INPUT)


def test_extra_key_is_rejected():
    with pytest.raises(ValueError, match="키 불일치"):
        validate_tool_result(_result(extra=1), TOOL_INPUT)


def test_wrong_tool_name_is_rejected():
    with pytest.raises(ValueError, match="tool_name"):
        validate_tool_result(_result(tool_name="action_search"), TOOL_INPUT)


def test_ok_without_findings_is_rejected():
    with pytest.raises(ValueError, match="OK"):
        validate_tool_result(_result(findings=[], citations=[]), TOOL_INPUT)


def test_no_evidence_with_citations_is_rejected():
    result = _result(status="NO_EVIDENCE", findings=[], warnings=["근거 없음"])
    with pytest.raises(ValueError, match="비어 있어야"):
        validate_tool_result(result, TOOL_INPUT)


def test_error_without_warning_is_rejected():
    result = _result(status="ERROR", findings=[], citations=[], warnings=[])
    with pytest.raises(ValueError, match="warnings"):
        validate_tool_result(result, TOOL_INPUT)


def test_finding_pointing_to_missing_citation_is_rejected():
    result = _result(findings=[_finding(chunk_ids=("kdca_measles_2026:p0094:b01",))])
    with pytest.raises(ValueError, match="없는 Citation"):
        validate_tool_result(result, TOOL_INPUT)


def test_duplicate_citation_is_rejected():
    result = _result(citations=[_citation(), _citation()])
    with pytest.raises(ValueError, match="중복"):
        validate_tool_result(result, TOOL_INPUT)


def test_citation_from_disallowed_document_is_rejected():
    result = _result(citations=[_citation(document_id="moleg_infectious_disease_act_21521")],
                     findings=[_finding()])
    with pytest.raises(ValueError, match="허용되지 않은"):
        validate_tool_result(result, TOOL_INPUT)


def test_run_failure_becomes_error_result(monkeypatch):
    # run() 이 예외를 내면 ERROR 로 돌려주고, 근거는 비운다 (근거 없음과 구분)
    def boom(_query):
        raise ConnectionError("DB 연결 실패 흉내")

    monkeypatch.setattr(tool, "run", boom)
    result = legal_search(TOOL_INPUT)
    assert result["status"] == "ERROR"
    assert result["findings"] == [] and result["citations"] == []
    assert result["warnings"] == ["Tool 실행 실패: ConnectionError"]
    validate_tool_result(result, TOOL_INPUT)


def test_status_mapping_from_run(monkeypatch):
    # run() 의 INSUFFICIENT_EVIDENCE 는 NO_EVIDENCE 로 바뀌어야 한다
    def fake_run(_query):
        return {"status": "INSUFFICIENT_EVIDENCE", "findings": [], "citations": [], "warnings": []}

    monkeypatch.setattr(tool, "run", fake_run)
    result = legal_search(TOOL_INPUT)
    assert result["status"] == "NO_EVIDENCE"
    assert result["graph_paths"] == []   # 법령 Tool 은 그래프 경로를 만들지 않는다
    validate_tool_result(result, TOOL_INPUT)
