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
    (("홍역 의심환자가 생겼을 때 학교 담당교사와 의료기관은 각각 보건소에 무엇을 신고하거나 안내하나요? "
      "법률과 지침의 조건을 구분해 주세요."), "DOMAIN", "LEGAL"),                                  # Legal 샘플(M3)
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


# ---------------------------------------------------------------- 답변 구성 (역할별 묶음)

def _finding(claim, chunk):
    return {"claim": claim, "chunk_ids": [chunk], "action_ids": [], "disease_ids": [], "concept_ids": []}


def _citation(chunk, page):
    return {"document_id": chunk.split(":")[0], "chunk_id": chunk, "pdf_page": page, "printed_page": page - 14,
            "excerpt": "원문"}


def test_compose_groups_by_role_merges_same_text_and_shortens_source():
    from rag.school.main_graph import compose_text
    a, b, c = "moe_school_manual_v3:p0036:t01r01", "gyeonggi_school_guide_2025:p0166:t01r01", "moe_school_manual_v3:p0054:b01"
    findings = [
        _finding("[교육부 매뉴얼 PDF 36쪽 · 대응 제1단계] 원문이 제시한 담당 예시: 담임교사 — 보호자에게 연락한다.", a),
        _finding("[경기도 가이드북 PDF 166쪽 · 대응 제1단계] 원문이 제시한 담당 예시: 담임교사 — 보호자에게  연락한다.", b),
        _finding("[교육부 매뉴얼 PDF 36쪽 · 단계 미기재] 원문에 담당 미기재 — 증상을 관찰한다.", a),
        _finding("[교육부 매뉴얼 PDF 54쪽 · 원문 배치: 대응 제1단계] 원문: “- 혼자 이동하는 것은 원칙적으로 금지”", c)]
    text = compose_text("ANSWERED", findings, [_citation(a, 36), _citation(b, 166), _citation(c, 54)], [])
    assert text.count("보호자에게 연락한다") == 1                       # 두 문서의 같은 문구는 한 줄
    assert "■ 담임교사" in text and "교육부 p.36 · 대응 1단계" in text and "경기도 p.166" in text
    assert "[1][2]" in text and "PDF" not in text and "원문이 제시한 담당 예시" not in text
    assert "■ 담당이 원문에 적혀 있지 않은 행동" in text and "■ 원문 그대로" in text
    assert "“혼자 이동하는 것은 원칙적으로 금지”" in text              # 글머리 기호 제거


def test_compose_limits_lines_per_role_and_counts_hidden():
    from rag.school.main_graph import MAX_PER_SECTION, compose_text
    chunk = "moe_school_manual_v3:p0036:t01r01"
    findings = [_finding(f"[교육부 매뉴얼 PDF 36쪽 · 대응 제1단계] 원문이 제시한 담당 예시: 담임교사 — 행동 {n}번을 한다.", chunk)
                for n in range(MAX_PER_SECTION + 2)]
    text = compose_text("ANSWERED", findings, [_citation(chunk, 36)], [])
    assert text.count("- 행동") == MAX_PER_SECTION and "그 밖의 근거 2건" in text


def test_compose_keeps_unparsable_claims_as_is():
    from rag.school.main_graph import compose_text
    chunk = "kdca_measles_2026:p0010:b01"
    text = compose_text("ANSWERED", [_finding("홍역의 잠복기는 7~21일이다.", chunk)], [_citation(chunk, 10)], [])
    assert "홍역의 잠복기는 7~21일이다." in text and "■ 질병관리청 홍역 관리지침" in text and "(질병청 홍역 p.10)" in text


def test_compose_marks_off_stage_action():
    from rag.school.main_graph import compose_text
    chunk = "moe_school_manual_v3:p0068:t02r04"
    text = compose_text("ANSWERED", [_finding(
        "[교육부 매뉴얼 PDF 68쪽 · 복구단계] 원문이 제시한 담당 예시: 학교장 — 조직을 해제한다."
        " — 원문 배치 단계가 질문 단계와 다름", chunk)], [_citation(chunk, 68)], [])
    assert "복구단계 · 질문 단계와 다름" in text and "원문 배치 단계가" not in text


# ---------------------------------------------------------------- 11/06 M4 재검색 루프 (가짜 Tool)

def _tool(name, status="OK", warnings=(), chunk="moe_school_manual_v3:p0036:t01r01", disease_ids=(), concept_ids=()):
    def run(tool_input):
        if status in ("NO_EVIDENCE", "ERROR"):
            return {"tool_name": name, "status": status, "findings": [], "citations": [], "graph_paths": [],
                    "warnings": list(warnings) or ["근거 없음"]}
        return {"tool_name": name, "status": status,
                "findings": [{"claim": f"{name}가 확인한 사실", "chunk_ids": [chunk], "action_ids": [],
                              "disease_ids": list(disease_ids), "concept_ids": list(concept_ids)}],
                "citations": [{"document_id": chunk.split(":")[0], "chunk_id": chunk, "pdf_page": 36,
                               "printed_page": 22, "excerpt": "원문"}],
                "graph_paths": [], "warnings": list(warnings)}
    return Recorder(run)


def _deps_no_db(books, monkeypatch):
    # 다른 Tool 결과의 DB 대조는 실DB가 필요하므로 이 검사에서는 통과로 둔다(실DB 검사는 노트북).
    import rag.school.main_graph as mg
    monkeypatch.setattr(mg, "verify_against_db", lambda *a, **k: {"bad_citations": {}, "bad_findings": {}})
    monkeypatch.setattr(mg, "check_graph_paths", lambda *a, **k: {})
    return deps(books)


MIXED = "성홍열 학생이 생기면 담임교사는 무엇을 하고, 학교장은 법적으로 보건소에 신고해야 하나요?"


def test_classify_adds_action_as_secondary_only_when_school_action_is_asked(books):
    assert classify_question(MIXED, books)["secondary"] == ["ACTION"]
    assert classify_question("성홍열도 학교장이 보건소에 신고해야 하나요?", books)["secondary"] == []   # 학교장=신고 주체
    assert classify_question("홍역 학생이 생기면 학교는 무엇을 해야 하고, 보건소 신고도 해야 하나요?",
                             books)["intent"] == "LEGAL"


def test_sufficient_first_tool_stops_without_extra_call(books, monkeypatch):
    legal, action = _tool("legal_search"), _tool("action_search")
    out = run_main(query("성홍열도 학교장이 보건소에 신고해야 하나요?"), _deps_no_db(books, monkeypatch),
                   {"legal_search": legal, "action_search": action})
    assert out["status"] == "ANSWERED" and out["search_rounds"] == 1 and action.calls == []


def test_secondary_intent_triggers_second_tool_with_context(books, monkeypatch):
    legal = _tool("legal_search", disease_ids=["SCARLET_FEVER"])
    action = _tool("action_search", chunk="gyeonggi_school_guide_2025:p0166:t01r01")
    d = _deps_no_db(books, monkeypatch)
    out = run_main(query(MIXED), d, {"legal_search": legal, "action_search": action})
    assert out["tools_used"] == ["legal_search", "action_search"] and out["search_rounds"] == 2
    assert out["status"] == "ANSWERED" and len(out["citations"]) == 2
    second = action.calls[0]
    assert "Disease:SCARLET_FEVER" in second["context_entity_ids"] and "남은 부분" in second["task"]
    decisions = [t["decision"] for t in d["main_trace"] if t["step"] == "evaluate_evidence"]
    assert decisions == ["재검색", "종료"]


def test_unresolved_warning_naming_a_tool_triggers_it_and_is_resolved_by_ok(books, monkeypatch):
    action = _tool("action_search", status="PARTIAL",
                   warnings=["[미해결] 법정 신고 의무 여부는 학교 문서만으로 확정하지 않는다 — legal_search 확인 필요"])
    legal = _tool("legal_search", chunk="kdca_measles_2026:p0093:b01")
    out = run_main({**query("담임교사는 무엇을 하나요?"), "allowed_document_ids": ALLOWED + ["kdca_measles_2026"]},
                   _deps_no_db(books, monkeypatch), {"action_search": action, "legal_search": legal})
    assert out["tools_used"] == ["action_search", "legal_search"] and out["status"] == "ANSWERED"
    assert not any("legal_search 확인 필요" in w for w in out["warnings"])


def test_call_limit_is_three_and_missing_check_is_reported(books, monkeypatch):
    chain = {"action_search": "legal_search", "legal_search": "disease_search", "disease_search": "crossdoc_graph_search"}
    registry = {name: _tool(name, status="PARTIAL", warnings=[f"[미해결] {nxt} 확인 필요"])
                for name, nxt in chain.items()}
    registry["crossdoc_graph_search"] = _tool("crossdoc_graph_search")
    out = run_main(query("담임교사는 무엇을 하나요?"), _deps_no_db(books, monkeypatch), registry)
    assert out["search_rounds"] == 3 and registry["crossdoc_graph_search"].calls == []
    assert out["status"] == "PARTIAL" and any("호출 한도(3회)" in w for w in out["warnings"])


def test_same_tool_is_never_called_twice(books, monkeypatch):
    action = _tool("action_search", status="PARTIAL", warnings=["[미해결] action_search로 다시 확인 필요"])
    out = run_main(query("담임교사는 무엇을 하나요?"), _deps_no_db(books, monkeypatch), {"action_search": action})
    assert len(action.calls) == 1 and out["search_rounds"] == 1 and out["status"] == "PARTIAL"


def test_second_tool_failure_keeps_first_evidence_as_partial(books, monkeypatch):
    def boom(tool_input):
        raise RuntimeError("DB down")
    legal = _tool("legal_search")
    out = run_main(query(MIXED), _deps_no_db(books, monkeypatch), {"legal_search": legal, "action_search": boom})
    assert out["search_rounds"] == 2 and out["status"] == "PARTIAL" and len(out["findings"]) == 1
    assert any("action_search를 호출했지만 근거를 찾지 못했다" in w for w in out["warnings"])


def test_unregistered_secondary_is_reported_not_substituted(books, monkeypatch):
    legal = _tool("legal_search")
    out = run_main(query(MIXED), _deps_no_db(books, monkeypatch), {"legal_search": legal})
    assert out["search_rounds"] == 1 and out["status"] == "PARTIAL"
    assert any("action_search" in w and "연결되지 않아" in w for w in out["warnings"])


def test_no_evidence_first_then_secondary_finds_evidence(books, monkeypatch):
    legal, action = _tool("legal_search", status="NO_EVIDENCE"), _tool("action_search")
    out = run_main(query(MIXED), _deps_no_db(books, monkeypatch), {"legal_search": legal, "action_search": action})
    assert out["tools_used"] == ["legal_search", "action_search"] and out["status"] == "PARTIAL"
    assert len(out["findings"]) == 1


def test_disease_fact_inside_action_question_becomes_secondary(books):
    mixed = classify_question("홍역의 잠복기는 며칠이고, 홍역 학생이 생기면 학교는 무엇을 해야 하나요?", books)
    assert mixed["intent"] == "ACTION" and mixed["secondary"] == ["DISEASE"]
    closure = classify_question("유행성각결막염 걸렸을 때도 전파차단 위한 휴업 해야하나요?", books)
    assert closure["secondary"] == []                                    # '전파'는 행동 문맥의 넓은 낱말


def test_failed_tool_pointer_is_reported_once(books, monkeypatch):
    legal = _tool("legal_search", status="NO_EVIDENCE")
    action = _tool("action_search", status="PARTIAL",
                   warnings=["[미해결] 법정 신고 의무 여부는 학교 문서만으로 확정하지 않는다 — legal_search 확인 필요"])
    out = run_main(query(MIXED), _deps_no_db(books, monkeypatch), {"legal_search": legal, "action_search": action})
    assert out["status"] == "PARTIAL"
    assert sum("legal_search" in w for w in out["warnings"]) == 1
