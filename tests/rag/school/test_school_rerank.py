"""11/07 ② 근거 재순위·줄 단위 답변 검사·스트리밍·multi-query 전달(가짜 LLM, DB 없음)."""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school.action_tool import doc_queries
from rag.school.answer_writer import evidence_blocks, evidence_prompt, make_answer_writer
from rag.school.main_graph import run_main
from rag.school.rerank import (
    Coverage,
    Pick,
    RerankDecision,
    apply_decision,
    evidence_cards,
    filter_evidence,
    make_reranker,
    verify_quotes,
)
from rag.school.router import Part, RouteDecision, make_router

DOC = "moe_school_manual_v3"
C1, C2, C3 = (f"{DOC}:p00{n}:b01" for n in (36, 40, 44))
FINDINGS = [
    {"claim": "[교육부 매뉴얼 PDF 36쪽 · 대응 제1단계] 원문이 제시한 담당 예시: 담임교사 — 보호자에게 연락한다.",
     "chunk_ids": [C1], "action_ids": ["a1"], "disease_ids": [], "concept_ids": []},
    {"claim": "[교육부 매뉴얼 PDF 40쪽] 원문: “급식 중단을 검토한다.”",
     "chunk_ids": [C2], "action_ids": [], "disease_ids": [], "concept_ids": []},
    {"claim": "[교육부 매뉴얼 PDF 44쪽] 원문 2개 청크에 걸친 행동",
     "chunk_ids": [C3, C1], "action_ids": ["a3"], "disease_ids": [], "concept_ids": []},
]
CITATIONS = [
    {"document_id": DOC, "chunk_id": C1, "pdf_page": 36, "printed_page": 22,
     "excerpt": "담임교사는 보호자에게 연락하고 2일 동안 관찰한다."},
    {"document_id": DOC, "chunk_id": C2, "pdf_page": 40, "printed_page": 26, "excerpt": "학교장은 급식 중단을 검토한다."},
    {"document_id": DOC, "chunk_id": C3, "pdf_page": 44, "printed_page": 30, "excerpt": "유행 종료 후 소독을 실시한다."},
]
TITLES = {DOC: "교육부 매뉴얼"}
QUERY = {"question": "담임교사는 무엇을 하나요?", "region": "GYEONGGI", "allowed_document_ids": [DOC]}


@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


class FakeStructured:
    def __init__(self, decision):
        self.decision, self.calls = decision, []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


class FakeText:
    """stream이 있는 가짜 LLM(글자를 몇 개씩 끊어 보낸다)."""

    def __init__(self, text):
        self.text, self.prompts = text, []

    def stream(self, messages):
        self.prompts.append(messages)
        for i in range(0, len(self.text), 7):
            yield type("Chunk", (), {"content": self.text[i:i + 7]})()


def _action(tool_input):
    return {"tool_name": "action_search", "status": "OK", "findings": FINDINGS, "citations": CITATIONS,
            "graph_paths": [], "warnings": []}


# ---------------------------------------------------------------- 재순위 단위

def test_quotes_are_verified_against_source_text():
    kept, dropped = verify_quotes(["보호자에게  연락하고", "보건소에 신고한다", "담"], "담임교사는 보호자에게 연락하고 2일")
    assert kept == ["보호자에게  연락하고"] and dropped == ["보건소에 신고한다", "담"]


def test_apply_decision_drops_unknown_ids_and_defaults_coverage():
    cards = evidence_cards(FINDINGS, CITATIONS, TITLES)
    decision = RerankDecision(picks=[Pick(id="E1", role="DIRECT", quotes=["보호자에게 연락"]),
                                     Pick(id="E9", role="DIRECT"), Pick(id="e1", role="CONDITION")])
    result = apply_decision(decision, cards, ["질문"])
    assert result["order"] == [C1] and result["invalid"] == ["E9"]
    assert result["picks"][C1]["quotes"] == ["보호자에게 연락"]
    assert result["coverage"] == [{"part": 1, "question": "질문", "status": "ANSWERED", "missing": ""}]


def test_filter_keeps_every_chunk_a_kept_finding_needs():
    findings, citations = filter_evidence(FINDINGS, CITATIONS, [C3])
    assert [f["action_ids"] for f in findings] == [["a3"]]
    assert [c["chunk_id"] for c in citations] == [C3, C1]       # 고른 순서 먼저, Finding이 쓰는 다른 청크 뒤


# ---------------------------------------------------------------- 메인 그래프 연결

def _deps(books, decision, writer=None):
    deps = {"driver": None, "database": None, "books": books, "reranker": make_reranker(FakeStructured(decision))}
    if writer:
        deps["answer_writer"] = writer
    return deps


def test_select_evidence_keeps_only_picked_chunks(books):
    decision = RerankDecision(picks=[Pick(id="E1", role="DIRECT", quotes=["보호자에게 연락하고"])],
                              coverage=[Coverage(part=1, status="ANSWERED")])
    deps = _deps(books, decision)
    out = run_main(QUERY, deps, {"action_search": _action})
    assert out["status"] == "ANSWERED"
    assert [c["chunk_id"] for c in out["citations"]] == [C1]
    assert [f["action_ids"] for f in out["findings"]] == [["a1"]]
    step = next(t for t in deps["main_trace"] if t["step"] == "select_evidence")
    assert step["before"] == (3, 3) and step["after"] == (1, 1) and step["picks"][0]["quotes"] == ["보호자에게 연락하고"]


def test_coverage_gap_makes_partial_with_missing_part(books):
    decision = RerankDecision(picks=[Pick(id="E2", role="DIRECT")],
                              coverage=[Coverage(part=1, status="PARTIAL", missing="담임교사의 연락 시점")])
    out = run_main(QUERY, _deps(books, decision), {"action_search": _action})
    assert out["status"] == "PARTIAL"
    assert any("담임교사의 연락 시점" in w for w in out["warnings"])


def test_no_pick_means_insufficient_evidence(books):
    def noisy(tool_input):
        return {**_action(tool_input), "warnings": ["원문 인용 Finding은 검색된 원문을 그대로 옮긴 것이다."]}

    out = run_main(QUERY, _deps(books, RerankDecision()), {"action_search": noisy})
    assert out["status"] == "INSUFFICIENT_EVIDENCE" and out["findings"] == [] and out["citations"] == []
    assert "원문 인용 Finding" not in out["answer"] and "직접 답하는 근거를 찾지 못했다" in out["answer"]


def test_reranker_failure_keeps_all_evidence(books):
    deps = _deps(books, TimeoutError("느림"))
    out = run_main(QUERY, deps, {"action_search": _action})
    assert out["status"] == "ANSWERED" and len(out["citations"]) == 3
    assert next(t for t in deps["main_trace"] if t["step"] == "select_evidence")["mode"] == "fallback"


def test_router_search_query_reaches_action_search_task_only(books):
    seen = {}

    def action(tool_input):
        seen["action_search"] = tool_input
        return _action(tool_input)

    route = RouteDecision(response_type="DOMAIN", parts=[
        Part(intent="ACTION", question="담임교사는 무엇을 하나요?", search_query="감염병 의심 학생 발생 시 담임교사 역할")])
    deps = {"driver": None, "database": None, "books": books, "router": make_router(FakeStructured(route))}
    run_main(QUERY, deps, {"action_search": action})
    assert doc_queries(seen["action_search"]["task"]) == ["감염병 의심 학생 발생 시 담임교사 역할"]
    assert seen["action_search"]["question"] == QUERY["question"]


# ---------------------------------------------------------------- 줄 단위 답변 검사·스트리밍

PICKS = {C1: {"role": "DIRECT", "part": 1, "quotes": ["보호자에게 연락하고"]}}
ONE = {"status": "ANSWERED", "warnings": [], "findings": FINDINGS[:1], "citations": CITATIONS[:1]}


def test_prompt_shows_role_and_verified_quotes():
    blocks = evidence_blocks(ONE, PICKS)
    prompt = evidence_prompt("질문", ONE, blocks)
    assert "(직접 답)" in prompt and "핵심 구절: “보호자에게 연락하고”" in prompt


def test_failing_later_line_is_dropped_and_passing_lines_stream(books):
    text = ("담임교사가 보호자에게 연락합니다.[1]\n\n**담임교사**\n- 2일 동안 관찰합니다.[1]\n"
            "- 3일 뒤 보건소에 신고합니다.[1]\n")
    streamed = []
    written = make_answer_writer(FakeText(text))("질문", ONE, books, picks=PICKS, on_line=streamed.append)
    assert written["answer"] and "보건소" not in written["answer"]
    assert written["dropped"] == ["- 3일 뒤 보건소에 신고합니다.[1]"]
    assert streamed[0] == "담임교사가 보호자에게 연락합니다.[1]" and "**담임교사**" in streamed
    assert not any("보건소" in line for line in streamed)


def test_first_line_failure_falls_back_to_template(books):
    text = "담임교사는 반드시 보건소에 신고합니다.[1]\n- 보호자에게 연락합니다.[1]\n"
    written = make_answer_writer(FakeText(text))("질문", ONE, books)
    assert written["answer"] is None and written["problems"][0] == "첫 내용 줄(직접 답)이 검사에 걸림"


def test_heading_without_passing_line_is_not_emitted(books):
    text = "담임교사가 보호자에게 연락합니다.[1]\n**학교장**\n- 학교장이 검토합니다.[1]\n"
    written = make_answer_writer(FakeText(text))("질문", ONE, books)
    assert written["answer"] and "학교장" not in written["answer"]


# ---------------------------------------------------------------- 11/08 본문 일치 구간

def test_text_windows_find_answer_line_outside_tool_excerpt():
    from rag.school.rerank import evidence_cards, key_terms, text_windows, verify_quotes
    text = ("병원체 - 마이코플라스마 폐렴균\n전파경로 - 비말 전파\n잠복기 - 2~3주\n" + "임상 양상 설명 " * 40
            + "\n**전염기간** - 증상 발현 2∼8일 전부터 증상 발생 후 20일 이내\n치료 - 항생제")
    terms = key_terms(["마이코플라스마 폐렴 전염기간은?"], weak={"마이코플라스마 폐렴"})
    assert "전염기간" in terms and "마이코플라스마" not in terms
    excerpt = text[:60]
    windows = text_windows(text, terms, excerpt)
    assert windows and "증상 발생 후 20일 이내" in windows[0]
    citation = {"document_id": "kdca_respiratory_2026", "chunk_id": "c1", "pdf_page": 193, "excerpt": excerpt}
    card = evidence_cards([{"claim": "전파경로 - 비말 전파", "chunk_ids": ["c1"]}], [citation], {},
                          {"c1": text}, terms)[0]
    kept, dropped = verify_quotes(["증상 발생 후 20일 이내"], card["source"])
    assert kept and not dropped                               # 본문 일치 구간의 구절은 원문 대조를 통과한다


def test_text_windows_skip_part_already_in_excerpt():
    from rag.school.rerank import text_windows
    text = "잠복기 - 1~7일(평균 2~5일)"
    assert text_windows(text, ["잠복기"], excerpt=text) == []
