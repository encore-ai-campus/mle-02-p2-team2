"""11/07 답변 다듬기 검사(LLM·DB 없이). 실DB·실LLM 비교는 notebooks/school/평가결과/answer_polish_eval.py."""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school.answer_writer import (
    EXAMPLE_NOTE,
    NEUTRAL_HEADING,
    SCENARIO_MARK,
    check_answer,
    condition_gaps,
    decorate,
    evidence_blocks,
    evidence_prompt,
    make_answer_writer,
)
from rag.school.main_graph import run_main, scenario_chunks

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
    ("※ 확인하지 못한 부분: 9일 기준인지", "근거에 없는 숫자"),
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

    def broken(*_, **__):
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


SECOND = {"document_id": "moe_school_manual_v3", "chunk_id": "moe_school_manual_v3:p0075:b01", "pdf_page": 75,
          "printed_page": 61, "excerpt": "교육청에 보고할 학생을 선택하고 보고서를 저장한다."}
TWO = {**OUTPUT, "citations": OUTPUT["citations"] + [SECOND]}


def test_line_under_role_heading_without_that_role_moves_to_neutral_heading(books):
    """11/07 ③: 소제목 담당이 그 줄의 근거에 없으면 줄을 지우지 않고 중립 소제목 아래로 옮긴다(S-A03·S-Q05)."""
    text = ("담임교사가 부모님에게 연락합니다.[1]\n\n**담임교사**\n- 부모님에게 연락합니다.[1]\n"
            "- 교육청에 보고할 학생을 선택하고 보고서를 저장합니다.[2]\n")
    assert check_answer(text, evidence_blocks(TWO), books) == []
    written = make_answer_writer(FakeLLM(text))("질문", TWO, books)
    lines = written["answer"].splitlines()
    assert written["dropped"] == [] and written["moved"] == ["- 교육청에 보고할 학생을 선택하고 보고서를 저장합니다.[2]"]
    assert lines.index(NEUTRAL_HEADING) < lines.index(written["moved"][0])
    assert lines.index("**담임교사**") < lines.index("- 부모님에게 연락합니다.[1]") < lines.index(NEUTRAL_HEADING)


def test_line_naming_a_role_missing_from_its_evidence_is_still_dropped(books):
    text = "담임교사가 부모님에게 연락합니다.[1]\n- 학교장이 보고서를 저장합니다.[2]\n"
    written = make_answer_writer(FakeLLM(text))("질문", TWO, books)
    assert written["dropped"] == ["- 학교장이 보고서를 저장합니다.[2]"]


# ---------------------------------------------------------------- 11/07 ③ 오탐 제거·맥락 표시

def test_unconfirmed_sentence_is_not_checked_as_a_claim(books, blocks):
    text = "담임교사가 부모님에게 연락합니다.[1] 다만 학교장이 반드시 신고해야 하는지는 확인되지 않았습니다."
    assert check_answer(text, blocks, books) == []
    assert check_answer("부모님에게 연락합니다.[1]\n※ 확인하지 못한 부분: 학교장의 신고 의무 여부", blocks, books) == []
    assert check_answer("담임교사가 연락하고 학교장이 반드시 신고합니다.[1]", blocks, books)   # 주장은 여전히 검사


@pytest.mark.parametrize("excerpt, line", [
    ("학교의 장은 감염병 학생이 있으면 즉시 교육감에게 보고하여야 한다.", "학교장은 즉시 교육감에게 보고합니다.[1]"),
    ("행정지원팀 | 학교 전체 소독: 방역·소독 활동 지원, 방역물품 구입", "행정실은 방역·소독 활동을 지원합니다.[1]"),
])
def test_role_synonyms_in_evidence(books, excerpt, line):
    out = {**OUTPUT, "findings": [], "citations": [{**OUTPUT["citations"][0], "excerpt": excerpt}]}
    assert check_answer(line, evidence_blocks(out), books) == []


def test_scenario_and_off_stage_evidence_lines_get_marks(books):
    stage_claim = ("[교육부 매뉴얼 PDF 66쪽 · 대응 제3단계] 원문이 제시한 담당 예시: 담임교사 — 부모님에게 연락한다. "
                   "— 원문 배치 단계가 질문 단계와 다름")
    out = {**TWO, "findings": [{**OUTPUT["findings"][0], "claim": stage_claim}]}
    blocks = evidence_blocks(out, scenario={SECOND["chunk_id"]})
    assert blocks[0]["marks"] == ["원문: 대응 제3단계"] and blocks[1]["marks"] == [SCENARIO_MARK]
    assert "표시: 원문: 대응 제3단계" in evidence_prompt("질문", out, blocks)
    text = ("담임교사가 부모님에게 연락합니다.[1]\n- 교육청에 보고할 학생을 선택합니다.[2]\n"
            "- 모의훈련 시나리오에서는 보고서를 저장합니다.[2]\n- 연락과 보고를 합니다.[1][2]\n")
    written = make_answer_writer(FakeLLM(text))("질문", out, books, scenario={SECOND["chunk_id"]})
    lines = written["answer"].splitlines()
    assert lines[0] == "(원문: 대응 제3단계) 담임교사가 부모님에게 연락합니다.[1]"
    assert "- (시나리오 예시) 교육청에 보고할 학생을 선택합니다.[2]" in lines
    assert "- 모의훈련 시나리오에서는 보고서를 저장합니다.[2]" in lines       # 이미 밝힌 줄은 그대로
    assert "- 연락과 보고를 합니다.[1][2]" in lines                           # 표시가 다른 근거를 섞으면 붙이지 않는다


def test_yes_no_verdict_from_scenario_only_line_is_removed(books):
    """S-T03: 시나리오 근거만으로 '네.'라고 답하면 결론어를 지운다(본문 지침 근거가 따로 있을 때)."""
    text = "네. 시나리오에서는 교육청에 보고할 학생을 선택합니다.[2]\n- 담임교사가 부모님에게 연락합니다.[1]\n"
    written = make_answer_writer(FakeLLM(text))("질문", TWO, books, scenario={SECOND["chunk_id"]})
    assert written["answer"].splitlines()[0] == "시나리오에서는 교육청에 보고할 학생을 선택합니다.[2]"
    body = make_answer_writer(FakeLLM("아니요. 담임교사가 부모님에게 연락합니다.[1]\n"))("질문", TWO, books,
                                                                                    scenario={SECOND["chunk_id"]})
    assert body["answer"].startswith("아니요. 담임교사가")                 # 본문 근거로 낸 결론은 그대로


def test_dropped_time_condition_is_appended():
    excerpt = "방법 | 교사와 학생은 2m 이상의 거리 유지; 사용 시설은 격리 해제 후 즉시 환기, 소독 실시."
    assert condition_gaps("- 사용 시설을 즉시 환기하고 소독합니다.[1]", [excerpt]) == ["격리 해제 후"]
    assert condition_gaps("- 격리 해제 후 사용 시설을 즉시 환기합니다.[1]", [excerpt]) == []
    assert condition_gaps("- 교사와 학생은 2m 이상 거리를 둡니다.[1]", [excerpt]) == []
    assert condition_gaps("- 발진 전후 4일 접촉자를 파악합니다.[1]", ["발진 전후 4일까지 접촉한 접촉자 명단 확보"]) == []
    assert decorate("- 사용 시설을 소독합니다.[3]", [], ["격리 해제 후"]) == "- 사용 시설을 소독합니다. (원문 조건: 격리 해제 후)[3]"


class FakeDriver:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or [], error, []

    def execute_query(self, query, **params):
        self.calls.append((query, params))
        if self.error:
            raise self.error
        return [{"chunk_id": c} for c in self.rows], None, None


def test_scenario_chunks_reads_db_once_and_tolerates_failure():
    assert scenario_chunks({"driver": None}, ["a"]) == set()
    driver = FakeDriver(rows=["a"])
    assert scenario_chunks({"driver": driver, "database": "neo4j"}, ["a", "b", "a"]) == {"a"}
    assert driver.calls[0][1]["ids"] == ["a", "b"] and driver.calls[0][1]["routing_"] == "r"
    assert scenario_chunks({"driver": FakeDriver(error=RuntimeError("down"))}, ["a"]) == set()
