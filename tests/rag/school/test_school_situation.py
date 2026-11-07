"""11/07 답변 품질 개선(상황 해석·확인 항목·판단형 답변) — 가짜 LLM·가짜 DB, 실DB 없음.

실DB·실LLM 측정은 notebooks/school/평가결과/situation_demo_eval.py(상황형 15문항)와 answer_polish_eval.py(골든 40).
"""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school.answer_writer import (
    check_answer,
    evidence_blocks,
    evidence_prompt,
    make_answer_writer,
    penalty_backstop,
    user_note,
)
from rag.school.checks import (
    augment_checks,
    disease_names,
    gather_checks,
    target_roles,
    target_stages,
    window,
)
from rag.school.main_graph import compose_text, run_main
from rag.school.router import Part, RouteDecision, Situation, decision_to_route, make_router

LIST = "moe_school_manual_v3:p0059:b02"
LAW1 = "moleg_infectious_disease_act_21521:p0019:b01"
PEN = "kdca_respiratory_2026:p0034:b01"
ROLE = "gyeonggi_school_guide_2025:p0167:t01r01"
TEXTS = {
    LIST: "1) 신고가 필요한 법정감염병 법적 근거: 감염병의 예방 및 관리에 관한 법률 제12조 및 시행규칙 제8조 "
          "종류: 콜레라, 장티푸스, 파라티푸스, 세균성이질, 장출혈성 대장균 감염증, A형간염, 홍역, 결핵 "
          "신고방법: 교육(지원)청의 보고절차에 따라 나이스의 전자결재를 통해 교육(지원)청과 보건소에 보고",
    LAW1: "제12조(그 밖의 신고의무자) ① 다음 각 호의 어느 하나에 해당하는 사람은 제1급감염병부터 제3급감염병까지 … "
          "2. 학교, 사회복지시설, 병원 … 장소의 관리인, 경영자 또는 대표자",
    PEN: "미신고시 벌칙 … ｢감염병의 예방 및 관리에 관한 법률｣ 제12조제1항에 따른 신고를 게을리한 자 또는 세대주, "
         "관리인 등으로 하여금 제12조제1항에 따른 신고를 하지 아니하도록 한자는 200만원 이하의 벌금에 처한다.",
    ROLE: "학교 감염병 대응 단계별 주요 구성원의 주요 역할(예시) |구성원|단계|역할| |담임교사|대응<br>제3단계|"
          "1. 능동감시 체계 강화<br> - 매일 1교시 후 학년부장에게 보고<br> - 환자 발생을 나이스(NEIS)에 등록",
}


@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


class FakeDB:
    """Cypher WHERE 조건의 핵심 낱말로 TEXTS 중 맞는 청크를 돌려준다(질의 문자열을 보고 고른다)."""

    def __init__(self):
        self.queries = []

    def execute_query(self, query, **params):
        self.queries.append(query)
        picks = []
        if "'콜레라'" in query:
            picks = [LIST]
        elif "그 밖의 신고의무자" in query:
            picks = [LAW1]
        elif "제12조제1항" in query:
            picks = [PEN]
        elif "주요 역할" in query and "담임교사" in query and "제3단계" in query:
            picks = [ROLE]
        rows = [{"chunk_id": c, "text": TEXTS[c], "pdf_page": int(c.split(":p")[1][:4]), "printed_page": None,
                 "section": ""} for c in picks if any(c.startswith(d + ":") for d in params["docs"])]
        return [type("R", (), {"data": lambda self, r=r: r})() for r in rows], None, None


# ---------------------------------------------------------------- 상황 해석·확인 항목 고르기

def test_router_passes_situation_and_checks():
    decision = RouteDecision(response_type="DOMAIN", parts=[Part(intent="ACTION", question="q")],
                             situation=Situation(disease="홍역", status="의심", scope="한 반 여러 명", asker_role="담임교사"),
                             checks=["REPORTABLE", "PENALTY", "PENALTY"])
    route = decision_to_route(decision, {"diseases": [], "roles": []})
    assert route["situation"]["asker_role"] == "담임교사" and route["checks"] == ["REPORTABLE", "PENALTY"]


@pytest.mark.parametrize("question, situation, must", [
    ("전교생 중 5명 코로나 확진인데, 교장이 해야 할 일은?", {"disease": "코로나19", "status": "확진", "count": 5},
     {"REPORTABLE", "DISEASE_CLASS", "STAGE", "ROLE_TASKS", "REPORT_EDU"}),
    ("내가 담임이고 홍역이 한 반에 몇 명씩 유행하는 것 같은데 신고해야 할까? 안하면 어떻게 돼?",
     {"disease": "홍역", "asker_role": "담임교사"}, {"STAGE", "REPORTER_DUTY", "PENALTY", "ROLE_TASKS"}),
    ("코로나 걸린 학생도 출석 인정이 되나요?", {"disease": "코로나19", "status": "확진"}, {"ATTENDANCE", "EXCLUSION"}),
])
def test_augment_checks_adds_obvious_items(question, situation, must):
    assert must <= set(augment_checks(question, situation, []))


def test_target_roles_and_stages():
    assert target_roles("내가 담임인데", {}) == ["담임교사"]
    assert target_roles("교장이 해야 할 일", {}) == ["학교장"]
    assert target_stages("한 반에 여러 명", {}) == [3]
    assert target_stages("수두 확진", {"status": "확진", "count": 1}) == [2, 1]
    assert target_stages("열이 나요", {}) == [1]
    assert target_stages("유행이 끝나면", {}) == [0]


def test_disease_names_expand_aliases_without_garbage(books):
    names = disease_names("코로나19", books)
    assert "코로나" in names and "COVID-" not in names


def test_window_keeps_original_text_around_anchor():
    text = "가" * 600 + " 미신고시\n벌칙 본문 " + "나" * 600
    cut = window(text, ["미신고시 벌칙"], 200)
    assert "미신고시\n벌칙" in cut and len(cut) <= 200


# ---------------------------------------------------------------- 확인 항목 조회(가짜 DB)

def test_gather_checks_finds_rules_and_code_facts(books):
    deps = {"driver": FakeDB(), "database": None, "books": books}
    docs = ["moe_school_manual_v3", "moleg_infectious_disease_act_21521", "kdca_respiratory_2026",
            "gyeonggi_school_guide_2025"]
    got = gather_checks(["REPORTABLE", "REPORTER_DUTY", "PENALTY", "ROLE_TASKS"],
                        {"disease": "코로나19", "asker_role": "담임교사", "_question": "담임인데 반에 여러 명"}, docs, deps)
    facts = [f["text"] for f in got["facts"]]
    assert "학교장 신고 대상 감염병 목록 원문에 '코로나19'이(가) 없다." in facts
    assert any("시행규칙 제8조" in f for f in facts)
    assert any(f.startswith("벌칙 원문:") and "200만원" in f and "300만원" not in f for f in facts)
    assert any("담임교사는 이 목록에 적혀 있지 않다" in f for f in facts)
    assert set(got["pinned"]) == {LIST, LAW1, PEN, ROLE}
    assert all(c["excerpt"] in TEXTS[c["chunk_id"]] or c["excerpt"] == TEXTS[c["chunk_id"]].strip()
               for c in got["citations"])


def test_gather_checks_respects_allowed_documents(books):
    deps = {"driver": FakeDB(), "database": None, "books": books}
    got = gather_checks(["PENALTY"], {"disease": "홍역"}, ["moe_school_manual_v3"], deps)
    assert got["citations"] == [] and got["facts"] == []


# ---------------------------------------------------------------- 메인 그래프 연결

ACTION_CHUNK = "gyeonggi_school_guide_2025:p0165:t02r02"


def _action(tool_input):
    return {"tool_name": "action_search", "status": "OK", "graph_paths": [], "warnings": [],
            "findings": [{"claim": "[경기도 가이드북 PDF 165쪽 · 대응 제2단계] 원문이 제시한 담당 예시: 학교장 — 능동감시 실시 지시",
                          "chunk_ids": [ACTION_CHUNK], "action_ids": ["a1"], "disease_ids": [], "concept_ids": []}],
            "citations": [{"document_id": "gyeonggi_school_guide_2025", "chunk_id": ACTION_CHUNK, "pdf_page": 165,
                           "printed_page": 155, "excerpt": "학교장|대응 제2단계|능동감시 실시 지시"}]}


class FakeStructured:
    def __init__(self, decision):
        self.decision = decision

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        return self.decision


def test_check_facts_node_adds_pinned_rule_evidence(books):
    route = RouteDecision(response_type="DOMAIN", parts=[Part(intent="ACTION", question="교장이 할 일")],
                          situation=Situation(disease="코로나19", status="확진", count=5), checks=["REPORTABLE"])
    deps = {"driver": FakeDB(), "database": None, "books": books, "router": make_router(FakeStructured(route))}
    out = run_main({"question": "전교생 중 5명 코로나 확진인데 교장이 해야 할 일은?", "region": "GYEONGGI",
                    "allowed_document_ids": ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]},
                   deps, {"action_search": _action})
    assert LIST in [c["chunk_id"] for c in out["citations"]]
    step = next(t for t in deps["main_trace"] if t["step"] == "check_facts")
    assert step["mode"] == "db" and any("코로나19" in f for f in step["facts"])


# ---------------------------------------------------------------- 답변 작성·검사

OUT = {"status": "ANSWERED", "warnings": [],
       "findings": [{"claim": f"[감염병예방법 PDF 19쪽 · 판단 기준: 법 제12조①] 원문: “{TEXTS[LAW1]}”", "chunk_ids": [LAW1]},
                    {"claim": f"[질병관리청 호흡기감염병 관리지침 PDF 34쪽 · 판단 기준: 미신고 벌칙] 원문: “{TEXTS[PEN]}”",
                     "chunk_ids": [PEN]}],
       "citations": [{"document_id": "moleg_infectious_disease_act_21521", "chunk_id": LAW1, "pdf_page": 19,
                      "printed_page": None, "excerpt": TEXTS[LAW1]},
                     {"document_id": "kdca_respiratory_2026", "chunk_id": PEN, "pdf_page": 34, "printed_page": None,
                      "excerpt": TEXTS[PEN]}]}
FACTS = [{"chunk_id": LAW1, "text": "법 제12조①은 학교 등 여러 사람이 모이는 장소의 '관리인, 경영자 또는 대표자'를 신고의무자로 "
                                    "적고 있다(담임교사는 이 목록에 적혀 있지 않다)."},
         {"chunk_id": PEN, "text": "벌칙 원문: “제12조제1항에 따른 신고를 게을리한 자 … 200만원 이하의 벌금에 처한다.”"}]


class FakeLLM:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def invoke(self, messages):
        self.calls.append(messages)
        return type("R", (), {"content": self.texts[min(len(self.calls), len(self.texts)) - 1]})()


def test_fact_text_counts_as_evidence_for_its_chunk(books):
    """'담임교사는 신고의무자 목록에 없다'는 코드 대조 사실이므로 그 청크를 인용한 줄의 근거로 본다(데모 D02)."""
    text = "**담임교사는 제12조제1항의 신고의무자가 아니며, 학교장이 신고합니다.**[1]\n"
    written = make_answer_writer(FakeLLM(text))("내가 담임인데 신고해야 하나?", OUT, books, context={"facts": FACTS})
    assert written["answer"] and written["answer"].startswith("**담임교사는") and written["attempts"] == 1


def test_first_line_failure_retries_once_with_feedback(books):
    bad = "**담임교사가 3일 안에 보건소에 신고합니다.**[2]\n"
    good = "**법상 신고의무자는 학교의 관리인·경영자 또는 대표자입니다.**[1]\n"
    llm = FakeLLM(bad, good)
    streamed = []
    written = make_answer_writer(llm)("신고해야 하나?", OUT, books, context={"facts": FACTS}, on_line=streamed.append)
    assert written["attempts"] == 2 and written["answer"].startswith("**법상 신고의무자는")
    assert "근거 검사에 걸렸다" in llm.calls[1][-1][1]
    assert not any("3일" in s for s in streamed)          # 실패한 시도의 줄은 화면에 나가지 않는다


def test_penalty_backstop_quotes_original_when_answer_omits_it(books):
    text = "**법상 신고의무자는 학교의 관리인·경영자 또는 대표자입니다.**[1]\n"
    written = make_answer_writer(FakeLLM(text))("신고 안 하면 어떻게 돼?", OUT, books, context={"facts": FACTS})
    assert "**신고·법적 사항**" in written["answer"] and "200만원 이하의 벌금" in written["answer"]
    assert written["added"][-1].endswith("[2]")
    assert penalty_backstop("잠복기는?", [], evidence_blocks(OUT), {"facts": FACTS}) == []


def test_list_numbers_and_disease_name_digits_are_not_checked_as_facts(books):
    blocks = evidence_blocks(OUT)
    assert check_answer("2. 코로나19는 관리인, 경영자 또는 대표자가 신고하는 조항과 다릅니다.[1]", blocks, books) == []
    text = "**결론입니다 관리인, 경영자 또는 대표자.**[1]\n**지금 바로 할 일**\n3. 관리인이 신고합니다.[1]\n7. 대표자가 신고합니다.[1]\n"
    written = make_answer_writer(FakeLLM(text))("질문", OUT, books)
    assert "1. 관리인이 신고합니다.[1]" in written["answer"] and "2. 대표자가 신고합니다.[1]" in written["answer"]


def test_prompt_shows_situation_facts_and_role_table(books):
    picks = {LAW1: {"role": "ROLE", "quotes": []}}
    blocks = evidence_blocks(OUT, picks)
    prompt = evidence_prompt("q", OUT, blocks, {"situation": {"disease": "홍역", "asker_role": "담임교사", "status": "모름"},
                                               "facts": FACTS})
    assert "- 질병: 홍역" in prompt and "질문자 역할: 담임교사" in prompt and "확진/의심" not in prompt
    assert "[확인 항목" in prompt and "200만원" in prompt and "역할 표" in prompt
    assert "문서 성격" in prompt      # 의료기관·보건소용 지침 표시


# ---------------------------------------------------------------- 사용자 화면 문구

def test_user_note_hides_tool_names_and_internal_terms():
    assert user_note("[미해결] legal_search를 호출했지만 근거를 찾지 못했다: 질문의 일부(LEGAL)") == \
        "법령 자료에서 관련 근거를 찾지 못했습니다."
    assert user_note("[미해결] 근거에서 확인하지 못한 부분: 단축수업 기준") == "단축수업 기준"


def test_template_answer_shows_no_internal_tool_warnings():
    text = compose_text("PARTIAL", OUT["findings"], OUT["citations"], [
        "원문 인용 Finding은 검색된 원문을 그대로 옮긴 것이다.", "이 질문에 맞는 답 카드가 없거나 …",
        "[미해결] legal_search를 호출했지만 근거를 찾지 못했다: 질문의 일부(LEGAL)"])
    assert "Finding" not in text and "답 카드" not in text and "legal_search" not in text
    assert "법령 자료에서 관련 근거를 찾지 못했습니다." in text
