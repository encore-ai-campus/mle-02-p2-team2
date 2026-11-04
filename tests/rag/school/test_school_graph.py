"""SchoolGraph(B09)·Neo4j 대조(B10) 단위 검사. DB·LLM 없이 가짜 함수/드라이버를 주입한다."""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school import build_graph, graph, run
from rag.school.contract_local import ContractError
from rag.school.search import SearchError, build_search_terms, escape_lucene, fuse_rrf
from rag.school.validation_local import verify_against_db

BOOKS = load_dictionaries()
CONTRACT_KEYS = {"domain", "status", "findings", "citations", "action_ids", "warnings", "answer"}
DOC = "gyeonggi_school_guide_2025"
CHUNK = f"{DOC}:p0054:t01r01"
ROW = "| 담임교사 | 1) 보건교사에게 유선으로 상태를 전달 2) 교실 환기 |"
HIT = {"document_id": DOC, "title": "가이드북", "version": "v", "region": "GYEONGGI", "chunk_id": CHUNK,
       "text": f"인공 시나리오 주요 조치사항\n| 담당 | 주요 조치사항 |\n{ROW}", "section": "인공 시나리오",
       "pdf_page": 54, "printed_page": 44, "score": 1.0, "method": "fulltext", "rank": 1}


def action(aid, text, role="HOMEROOM_TEACHER", name="담임교사", concepts=("CONTACT_HEALTH_TEACHER",),
           stage="RESPONSE_1", basis="EXAMPLE", **extra):
    row = {"action_id": f"{DOC}:p0054:{aid}", "action_text": text, "scope": "COMMON", "stage": stage,
           "step_order": 1, "when_text": None, "timing_text": None, "modality": "UNSPECIFIED",
           "document_id": DOC, "chunk_id": CHUNK, "roles": [{"role_id": role, "name": name, "basis": basis}],
           "concept_ids": list(concepts), "disease_ids": []}
    return {**row, **extra}


@pytest.fixture
def fake(monkeypatch):
    calls = {"search": 0, "expand": 0}
    state = {"hits": [HIT], "actions": [action("a01", "보건교사에게 상태를 전달한다."),
                                        action("a02", "교실을 환기한다.", concepts=("VENTILATE_DISINFECT",))],
             "verify": {"bad_citations": {}, "bad_findings": {}}, "search_error": False}

    def search(driver, request, books, **kwargs):
        calls["search"] += 1
        if state["search_error"]:
            raise SearchError("ServiceUnavailable: 검색 질의 실패")
        return {"hits": state["hits"], "terms": ["x"], "mode": "fulltext"}

    def expand(driver, chunk_ids, allowed, **kwargs):
        calls["expand"] += 1
        return [a for a in state["actions"] if a["document_id"] in allowed]

    monkeypatch.setattr(graph, "search_school_chunks", search)
    monkeypatch.setattr(graph, "expand_school_actions", expand)
    monkeypatch.setattr(graph, "verify_against_db", lambda result, driver, database=None: state["verify"])
    return {"deps": {"driver": object(), "books": BOOKS, "trace": []}, "state": state, "calls": calls}


def request(question="유증상 학생이 발견되면 담임교사는 무엇을 하나요?", allowed=(DOC,)):
    return {"question": question, "region": "GYEONGGI", "allowed_document_ids": list(allowed)}


# ---------------------------------------------------------------- 계획

def test_plan_detects_roles_stage_and_scope():
    plan = graph.plan_question("학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요?", BOOKS)
    assert plan["roles"] == ["HOMEROOM_TEACHER", "HEALTH_TEACHER"] and plan["stages"] == ["RESPONSE_1"]
    assert plan["broad"] and not plan["out_of_scope"]


@pytest.mark.parametrize("question", ["학생 한 명이 열이 나서 보건실에 왔어요.", "학교장이 보건소에 신고해야 하나요?"])
def test_short_alias_inside_other_word_is_not_a_role(question):
    assert "HEALTH_TEACHER" not in graph.plan_question(question, BOOKS)["roles"]


@pytest.mark.parametrize("question", ["성홍열인지 판단해 주세요.", "학생 정보를 NEIS에 대신 입력해 주세요."])
def test_out_of_scope_questions_short_circuit(fake, question):
    result = run(request(question), deps=fake["deps"])
    assert result["status"] == "INSUFFICIENT_EVIDENCE" and result["warnings"]
    assert fake["calls"]["search"] == 0


def test_relevance_keeps_matching_concepts_only():
    plan = graph.plan_question("보호자에게 연락해 진료를 요청하는 일을 두 문서는 각각 어떻게 설명하나요?", BOOKS)
    kept = graph.relevant_actions([action("a01", "진한이의 부모님에게 연락한다.", concepts=("CONTACT_GUARDIAN",)),
                                   action("a02", "교무부장에게 연락하여 지정을 요청한다.", concepts=()),
                                   action("a03", "교실을 환기한다.", concepts=("VENTILATE_DISINFECT",))],
                                  plan, BOOKS)
    assert [a["action_id"][-3:] for a in kept] == ["a01"]


# ---------------------------------------------------------------- 조립

def test_claim_marks_example_role_scenario_and_modality():
    claim = graph.build_claim(action("a01", "동행하여 이동한다.", modality="RECOMMENDED"), HIT)
    assert "담당 예시: 담임교사" in claim and "모의훈련 시나리오 예시" in claim and "원문 강도: 권장" in claim
    assert "반드시" not in claim and "의무" not in claim


def test_excerpt_is_row_without_pipes_and_inside_chunk():
    excerpt = graph.excerpt_of(HIT["text"])
    assert excerpt.startswith("담임교사 | 1)") and excerpt in HIT["text"]
    assert graph.excerpt_of("문단 원문\n둘째 줄") == "문단 원문\n둘째 줄"


# ---------------------------------------------------------------- run / build_graph

def test_run_and_direct_invoke_return_only_contract_keys(fake):
    result = run(request(), deps=fake["deps"])
    assert set(result) == CONTRACT_KEYS and result["status"] == "PARTIAL"   # 시나리오 표만 근거
    assert result["action_ids"] == [f["action_ids"][0] for f in result["findings"]]
    direct = build_graph(fake["deps"]).invoke(request())
    assert set(direct) == CONTRACT_KEYS
    assert [t["step"] for t in fake["deps"]["trace"]][:4] == ["plan", "search", "relevance", "expand"]


def test_non_scenario_evidence_is_answered(fake):
    fake["state"]["hits"] = [{**HIT, "section": "대응 제1단계 > 담임교사의 조치"}]
    assert run(request(), deps=fake["deps"])["status"] == "ANSWERED"


def test_missing_role_is_partial(fake):
    fake["state"]["hits"] = [{**HIT, "section": "본문"}]
    result = run(request("유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 하나요?"), deps=fake["deps"])
    assert result["status"] == "PARTIAL" and any("HEALTH_TEACHER" in w for w in result["warnings"])


def test_db_error_is_error_and_no_hits_is_insufficient(fake):
    fake["state"]["search_error"] = True
    error = run(request(), deps=fake["deps"])
    assert error["status"] == "ERROR" and not error["findings"]
    fake["state"]["search_error"] = False
    fake["state"]["hits"] = []
    assert run(request(), deps=fake["deps"])["status"] == "INSUFFICIENT_EVIDENCE"


def test_allowed_empty_never_searches(fake):
    result = run(request(allowed=()), deps=fake["deps"])
    assert result["status"] == "INSUFFICIENT_EVIDENCE" and fake["calls"]["search"] == 0


def test_db_verification_failure_drops_finding(fake):
    fake["state"]["hits"] = [{**HIT, "section": "본문"}]
    fake["state"]["verify"] = {"bad_citations": {}, "bad_findings": {0: ["DB에 없는 action_id"]}}
    result = run(request(), deps=fake["deps"])
    assert result["status"] == "PARTIAL" and len(result["findings"]) == 1
    assert any("DB 대조 실패" in w for w in result["warnings"])


def test_unknown_keyword_in_question_is_flagged(fake):
    fake["state"]["hits"] = [{**HIT, "section": "본문"}]
    result = run(request("열이 나는 학생을 지금 NEIS에 등록해야 하나요? 담임교사는 무엇을 하나요?"),
                 deps=fake["deps"])
    assert result["status"] == "PARTIAL" and any("NEIS" in w for w in result["warnings"])


def test_legal_duty_question_is_partial(fake):
    fake["state"]["hits"] = [{**HIT, "section": "본문"}]
    result = run(request("성홍열도 학교장이 보건소에 신고해야 하나요? 담임교사는 무엇을 하나요?"), deps=fake["deps"])
    assert result["status"] == "PARTIAL" and any("의무 없음" in w for w in result["warnings"])
    assert not graph.plan_question("보건소 신고 공문을 어떻게 보내나요?", BOOKS)["legal_duty"]


def test_quote_findings_respect_stage_and_role():
    plan = graph.plan_question("유증상 학생이 발견되면 담임교사는 무엇을 하나요?", BOOKS)
    stage2 = {**HIT, "chunk_id": f"{DOC}:p0168:t01r01", "section": "역할표",
              "text": "역할표\n| 구성원 | 단계 | 역할 |\n| 담임교사 | 대응 제2단계 | 능동감시 실시 |"}
    other_role = {**HIT, "chunk_id": f"{DOC}:p0165:t02r01", "section": "역할표",
                  "text": "역할표\n| 구성원 | 단계 | 역할 |\n| 학교장 | 대응 제1단계 | 총괄 |"}
    body = {**HIT, "chunk_id": f"{DOC}:p0164:b01", "section": "2. 대응 제1단계 > 가. 개요",
            "text": "가. 개요\n유증상 학생을 발견하면 담임교사가 보건교사에게 연락\n짧은 줄"}
    state = {"question": "유증상 학생이 발견되면 담임교사는 무엇을 하나요?", "plan": plan}
    rows = graph.quote_findings(state, [stage2, other_role, body], BOOKS, set())
    assert [r["hit"]["chunk_id"] for r in rows] == [body["chunk_id"]]   # 2단계 행·다른 역할 행 제외
    assert rows[0]["excerpt"] in body["text"] and "담임교사가 보건교사에게 연락" in rows[0]["excerpt"]


def test_invalid_input_raises(fake):
    with pytest.raises(ContractError):
        run({**request(), "region": "SEOUL"}, deps=fake["deps"])


# ---------------------------------------------------------------- 검색 부품

def test_search_terms_add_aliases_and_escape():
    terms = build_search_terms("보건교사는 무엇을 하나요?", BOOKS)
    assert "보건(담당)교사" in terms and "무엇을" not in terms
    assert escape_lucene("보건(담당)교사") == "보건\\(담당\\)교사"


def test_rrf_uses_ranks_and_preferred_only_breaks_ties():
    a = [{"chunk_id": "x", "document_id": "d1", "rank": 1, "method": "fulltext", "score": 9.0},
         {"chunk_id": "y", "document_id": "d2", "rank": 2, "method": "fulltext", "score": 8.0}]
    b = [{"chunk_id": "y", "document_id": "d2", "rank": 1, "method": "vector", "score": 0.9},
         {"chunk_id": "x", "document_id": "d1", "rank": 2, "method": "vector", "score": 0.8}]
    assert [h["chunk_id"] for h in fuse_rrf([a, b], preferred=["d2"])] == ["y", "x"]   # 동점 → preferred
    assert fuse_rrf([a], preferred=["d2"])[0]["chunk_id"] == "x"   # 점수 차이는 preferred가 뒤집지 않음


# ---------------------------------------------------------------- Neo4j 대조 (T6)

class FakeRecord(dict):
    def data(self):
        return dict(self)


class FakeDriver:
    def __init__(self, chunks, actions):
        self.chunks, self.actions = chunks, actions

    def execute_query(self, query, database_=None, **params):
        if "MATCH (c:Chunk)" in query:
            rows = [FakeRecord(c) for c in self.chunks if c["chunk_id"] in params["ids"]]
        elif "MATCH (a:SchoolAction)" in query:
            rows = [FakeRecord(a) for a in self.actions if a["action_id"] in params["ids"]]
        else:
            rows = [FakeRecord(concepts=[c for c in params["concepts"] if c == "CONTACT_HEALTH_TEACHER"],
                               diseases=[])]
        return rows, None, None


def test_fake_chunk_id_and_wrong_page_fail_db_check():
    excerpt = graph.excerpt_of(HIT["text"])
    db = FakeDriver([{"chunk_id": CHUNK, "text": HIT["text"], "pdf_page": 54, "printed_page": 44,
                      "documents": [DOC]}],
                    [{"action_id": f"{DOC}:p0054:a01", "chunks": [CHUNK]}])
    fake_chunk = f"{DOC}:p0999:b01"
    result = {"findings": [{"claim": "c", "chunk_ids": [CHUNK], "action_ids": [f"{DOC}:p0054:a01"],
                            "disease_ids": [], "concept_ids": ["CONTACT_HEALTH_TEACHER"]},
                           {"claim": "fake", "chunk_ids": [fake_chunk], "action_ids": [],
                            "disease_ids": [], "concept_ids": ["MADE_UP"]}],
              "citations": [{"document_id": DOC, "chunk_id": CHUNK, "pdf_page": 54, "printed_page": 44,
                             "excerpt": excerpt},
                            {"document_id": DOC, "chunk_id": fake_chunk, "pdf_page": 999,
                             "printed_page": None, "excerpt": "없는 원문"}]}
    report = verify_against_db(result, db)
    assert list(report["bad_citations"]) == [fake_chunk] and list(report["bad_findings"]) == [1]
    result["citations"][0]["pdf_page"] = 55
    assert "쪽수 불일치" in verify_against_db(result, db)["bad_citations"][CHUNK]
