"""11/07 답변 다듬기 검사(LLM·DB 없이). 실DB·실LLM 비교는 notebooks/school/평가결과/answer_polish_eval.py."""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school.answer_writer import EXAMPLE_NOTE, check_answer, evidence_blocks, make_answer_writer
from rag.school.main_graph import run_main

CHUNK = "moe_school_manual_v3:p0036:t01r01"
OUTPUT = {
    "status": "ANSWERED", "warnings": [],
    "findings": [{"claim": "[교육부 매뉴얼 PDF 36쪽 · 대응 제1단계] 원문이 제시한 담당 예시: 담임교사 — 부모님에게 연락한다.",
                  "chunk_ids": [CHUNK], "action_ids": ["moe_school_manual_v3:p0036:a01"], "disease_ids": [],
                  "concept_ids": []}],
    "citations": [{"document_id": "moe_school_manual_v3", "chunk_id": CHUNK, "pdf_page": 36, "printed_page": 22,
                   "excerpt": "담임교사는 부모님에게 연락하고 2일 동안 관찰한다."}],
}


@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


@pytest.fixture(scope="module")
def blocks():
    return evidence_blocks(OUTPUT)


def test_evidence_blocks_follow_citation_order_and_attach_claims(blocks):
    assert [b["n"] for b in blocks] == [1] and blocks[0]["document"] == "교육부 매뉴얼"
    assert "부모님에게 연락한다" in blocks[0]["claims"][0]


def test_grounded_answer_passes_with_synonym_actor_and_subheading(books, blocks):
    text = "담임교사가 보호자에게 연락하고 2일 동안 관찰합니다.[1]\n\n**담임교사**\n- 부모님께 연락합니다.[1]"
    assert check_answer(text, blocks, books) == []


@pytest.mark.parametrize("text, problem", [
    ("담임교사가 보호자에게 연락합니다.", "인용 번호 없는 줄"),
    ("담임교사가 연락합니다.[2]", "없는 근거 번호"),
    ("담임교사가 3일 동안 관찰합니다.[1]", "근거에 없는 숫자"),
    ("보건교사가 부모님에게 연락합니다.[1]", "근거에 없는 주체"),
    ("담임교사는 반드시 부모님에게 연락해야 합니다.[1]", "근거보다 강한 표현"),
    ("담임교사는 보건소에 신고합니다.[1]", "근거에 없는 주체"),
    ("※ 확인하지 못한 부분: 보건소 신고 여부", "근거에 없는 주체"),
])
def test_check_answer_rejects_ungrounded_lines(books, blocks, text, problem):
    assert any(p.startswith(problem) for p in check_answer(text, blocks, books))


class FakeLLM:
    def __init__(self, text):
        self.text, self.prompts = text, []

    def invoke(self, messages):
        self.prompts.append(messages)
        return type("Response", (), {"content": self.text})()


def _deps(books, writer):
    return {"driver": None, "database": None, "books": books, "answer_writer": writer}


def _action(tool_input):
    return {"tool_name": "action_search", "status": "OK", "findings": OUTPUT["findings"],
            "citations": OUTPUT["citations"], "graph_paths": [], "warnings": []}


QUERY = {"question": "담임교사는 무엇을 하나요?", "region": "GYEONGGI",
         "allowed_document_ids": ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]}


def test_main_graph_uses_checked_llm_answer_and_keeps_evidence(books):
    llm = FakeLLM("담임교사가 부모님에게 연락합니다.[1]")
    d = _deps(books, make_answer_writer(llm))
    out = run_main(QUERY, d, {"action_search": _action})
    assert out["answer"].startswith("담임교사가 부모님에게 연락합니다.[1]") and EXAMPLE_NOTE in out["answer"]
    assert out["findings"] == OUTPUT["findings"] and out["citations"] == OUTPUT["citations"]
    step = next(t for t in d["main_trace"] if t["step"] == "compose_answer")
    assert step["mode"] == "llm" and "원문에서 확인한 내용" in step["template"]
    assert "[1] 교육부 매뉴얼" in llm.prompts[0][1][1]


def test_main_graph_falls_back_to_template_when_check_fails_or_llm_errors(books):
    d = _deps(books, make_answer_writer(FakeLLM("담임교사는 반드시 보건소에 신고합니다.[1]")))
    out = run_main(QUERY, d, {"action_search": _action})
    step = next(t for t in d["main_trace"] if t["step"] == "compose_answer")
    assert step["mode"] == "template_fallback" and step["problems"] and out["answer"].startswith("원문에서 확인한 내용")

    def broken(*_):
        raise TimeoutError
    d = _deps(books, broken)
    out = run_main(QUERY, d, {"action_search": _action})
    assert out["answer"].startswith("원문에서 확인한 내용")
    assert next(t for t in d["main_trace"] if t["step"] == "compose_answer")["problems"] == ["작성기 실패: TimeoutError"]


def test_general_answer_is_not_rewritten(books):
    llm = FakeLLM("무시")
    d = _deps(books, make_answer_writer(llm))
    out = run_main({**QUERY, "question": "안녕하세요, 이 서비스로 무엇을 할 수 있나요?"}, d, {"action_search": _action})
    assert out["response_type"] == "GENERAL" and llm.prompts == []


def test_line_under_role_heading_must_cite_that_role(books):
    two = evidence_blocks({**OUTPUT, "citations": OUTPUT["citations"] + [
        {"document_id": "moe_school_manual_v3", "chunk_id": "moe_school_manual_v3:p0040:b01", "pdf_page": 40,
         "printed_page": 26, "excerpt": "학교장은 급식 중단을 검토한다."}]})
    assert check_answer("**담임교사**\n- 부모님에게 연락합니다.[1]", two, books) == []
    problems = check_answer("**담임교사**\n- 급식 중단을 검토합니다.[2]", two, books)
    assert any("근거에 없는 주체" in p and "담임교사" in p for p in problems)
