"""11/05 B07·B08 메인 그래프 검사(가짜 Tool·DB 없이). 실DB 1-Tool 실행은 notebooks/school/school_main_tool_call.ipynb."""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school.contract_v2_local import ContractError
from rag.school.main_graph import classify_question, run_main

ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]


@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


def query(question, **extra):
    return {"question": question, "region": "GYEONGGI", "allowed_document_ids": list(ALLOWED), **extra}


def ok_result(tool_input, chunk="moe_school_manual_v3:p0036:t01r01", action="moe_school_manual_v3:p0036:a01"):
    return {"tool_name": "action_search", "status": "OK",
            "findings": [{"claim": "[교육부 PDF 36쪽] 담임교사 — 보호자에게 연락한다.", "chunk_ids": [chunk],
                          "action_ids": [action], "disease_ids": [], "concept_ids": []},
                         {"claim": "[교육부 PDF 36쪽] 담임교사 — 보건교사에게 연락한다.", "chunk_ids": [chunk],
                          "action_ids": [action.replace("a01", "a02")], "disease_ids": [], "concept_ids": []}],
            "citations": [{"document_id": "moe_school_manual_v3", "chunk_id": chunk, "pdf_page": 36,
                           "printed_page": 22, "excerpt": "원문"}],
            "graph_paths": [], "warnings": []}


class Recorder:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, tool_input):
        self.calls.append(tool_input)
        return self.fn(tool_input)


def deps(books):
    return {"driver": None, "database": None, "books": books}


# ---------------------------------------------------------------- 분류 (B07)

@pytest.mark.parametrize("question, kind, intent", [
    ("학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요?", "DOMAIN", "ACTION"),   # T02
    ("학생이 열이 나서 보건실에 왔어요. 어떻게 해야 하나요?", "DOMAIN", "ACTION"),                  # 병명 없음 ≠ 일반
    ("홍역의 잠복기는 며칠인가요?", "DOMAIN", "DISEASE"),                                          # T01 유형
    ("성홍열도 학교장이 보건소에 신고해야 하나요?", "DOMAIN", "LEGAL"),                              # T03 유형
    ("홍역 학생이 확인되면 학교는 보건소 신고 공문을 어떻게 보내나요?", "DOMAIN", "ACTION"),          # 절차는 Action
    ("보호자 연락을 교육부 매뉴얼과 경기도 가이드북은 각각 어떻게 설명하나요?", "DOMAIN", "CROSSDOC"),  # T04 유형
    ("수두 환자는 언제까지 격리하나요?", "DOMAIN", "ACTION"),
    ("감염병예방법상 학교장의 신고 의무는 무엇인가요?", "DOMAIN", "LEGAL"),                       # '예방법'≠'방법'
    ("대응 제2단계의 주요 활동은 무엇인가요?", "DOMAIN", "ACTION"),
    ("안녕하세요, 이 서비스로 무엇을 할 수 있나요?", "GENERAL", None),
    ("오늘 점심 메뉴 추천해 줘.", "OUT_OF_SCOPE", None),
    ("열이 나고 발진이 있어요. 성홍열인지 판단해 주세요.", "OUT_OF_SCOPE", None),                   # T10 진단
    ("학생 등교중지 정보를 NEIS에 대신 입력해 주세요.", "OUT_OF_SCOPE", None),
])
def test_classify(books, question, kind, intent):
    result = classify_question(question, books)
    assert result["response_type"] == kind
    if intent:
        assert result["intent"] == intent


# ---------------------------------------------------------------- 실행·호출 기록 (B08)

def test_general_and_out_of_scope_make_zero_calls(books):
    tool = Recorder(ok_result)
    for question, kind in (("안녕하세요?", "GENERAL"), ("오늘 점심 메뉴 추천해 줘.", "OUT_OF_SCOPE")):
        out = run_main(query(question), deps(books), {"action_search": tool})
        assert out["response_type"] == kind and out["tools_used"] == [] and out["search_rounds"] == 0
    assert tool.calls == []


def test_one_tool_call_answers_with_union_action_ids_and_request_id(books):   # T05
    tool = Recorder(ok_result)
    out = run_main(query("담임교사는 유증상 학생 발견 시 무엇을 하나요?", request_id="t05"), deps(books),
                   {"action_search": tool})
    assert out["status"] == "ANSWERED" and out["tools_used"] == ["action_search"] and out["search_rounds"] == 1
    assert len(tool.calls) == 1 and "request_id" not in tool.calls[0] and tool.calls[0]["task"]
    assert out["action_ids"] == ["moe_school_manual_v3:p0036:a01", "moe_school_manual_v3:p0036:a02"]
    assert len(out["citations"]) == 1 and len(out["findings"]) == 2          # Citation 1번, Finding 보존
    assert "graph_paths" not in out


def test_unregistered_tool_is_not_substituted(books):
    tool = Recorder(ok_result)
    out = run_main(query("홍역의 잠복기는 며칠인가요?"), deps(books), {"action_search": tool})
    assert tool.calls == [] and out["tools_used"] == [] and out["search_rounds"] == 0
    assert out["status"] == "ERROR" and any("disease_search" in w for w in out["warnings"])


def test_tool_exception_is_recorded_as_call_and_error(books):   # T11
    def boom(tool_input):
        raise RuntimeError("DB down")
    out = run_main(query("담임교사는 무엇을 하나요?"), deps(books), {"action_search": boom})
    assert out["status"] == "ERROR" and out["tools_used"] == ["action_search"] and out["search_rounds"] == 1
    assert out["findings"] == [] and any("DB down" in w for w in out["warnings"])


def test_no_evidence_is_insufficient_not_error(books):   # T11 구분
    def empty(tool_input):
        return {"tool_name": "action_search", "status": "NO_EVIDENCE", "findings": [], "citations": [],
                "graph_paths": [], "warnings": ["근거 없음"]}
    out = run_main(query("담임교사는 무엇을 하나요?"), deps(books), {"action_search": empty})
    assert out["status"] == "INSUFFICIENT_EVIDENCE" and out["search_rounds"] == 1


def test_tool_returning_wrong_name_or_fake_citation_is_rejected(books):
    def wrong(tool_input):
        return {**ok_result(tool_input), "tool_name": "legal_search"}

    def outside(tool_input):
        result = ok_result(tool_input)
        result["citations"][0]["document_id"] = "kdca_measles_2026"
        return result
    for fn in (wrong, outside):
        out = run_main(query("담임교사는 무엇을 하나요?"), deps(books), {"action_search": fn})
        assert out["status"] == "ERROR" and out["findings"] == [] and out["search_rounds"] == 1


def test_partial_tool_and_secondary_intent_keep_partial(books):
    def partial(tool_input):
        return {**ok_result(tool_input), "status": "PARTIAL", "warnings": ["[미해결] 보건교사 근거 없음"]}
    out = run_main(query("담임교사는 무엇을 하나요?"), deps(books), {"action_search": partial})
    assert out["status"] == "PARTIAL" and any("보건교사" in w for w in out["warnings"])


def test_invalid_query_and_unknown_registry_name_raise(books):
    with pytest.raises(ContractError):
        run_main({"question": "q", "region": "GYEONGGI"}, deps(books))
    with pytest.raises(ContractError):
        run_main(query("담임교사는 무엇을 하나요?"), deps(books), {"fake_search": ok_result})


def test_allowed_empty_passes_through_to_tool(books):
    tool = Recorder(lambda ti: {"tool_name": "action_search", "status": "NO_EVIDENCE", "findings": [],
                                "citations": [], "graph_paths": [], "warnings": ["허용 문서 없음"]})
    out = run_main({"question": "담임교사는 무엇을 하나요?", "region": "GYEONGGI", "allowed_document_ids": []},
                   deps(books), {"action_search": tool})
    assert tool.calls[0]["allowed_document_ids"] == [] and out["status"] == "INSUFFICIENT_EVIDENCE"
