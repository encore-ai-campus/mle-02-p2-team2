"""LLM 라우터(가짜 LLM, DB 없음). 실LLM 라우팅 비교는 notebooks/school/평가결과/router_eval.py."""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school.main_graph import run_main
from rag.school.router import Part, RouteDecision, decision_to_route, make_router

CHUNK = "moe_school_manual_v3:p0036:t01r01"
FINDINGS = [{"claim": "[교육부 매뉴얼 PDF 36쪽 · 대응 제1단계] 원문이 제시한 담당 예시: 담임교사 — 부모님에게 연락한다.",
             "chunk_ids": [CHUNK], "action_ids": ["moe_school_manual_v3:p0036:a01"], "disease_ids": [],
             "concept_ids": []}]
CITATIONS = [{"document_id": "moe_school_manual_v3", "chunk_id": CHUNK, "pdf_page": 36, "printed_page": 22,
              "excerpt": "담임교사는 부모님에게 연락하고 2일 동안 관찰한다."}]
QUERY = {"question": "수두 걸린 학생 반 친구들은 등교해도 되나요? 그리고 수두 잠복기는 얼마예요?", "region": "GYEONGGI",
         "allowed_document_ids": ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]}


@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


class FakeLLM:
    """with_structured_output(...).invoke(...)만 흉내 낸다."""

    def __init__(self, decision):
        self.decision, self.calls = decision, []

    def with_structured_output(self, schema):
        assert schema is RouteDecision
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


def _registry(seen):
    def action(tool_input):
        seen["action_search"] = tool_input
        return {"tool_name": "action_search", "status": "OK", "findings": FINDINGS, "citations": CITATIONS,
                "graph_paths": [], "warnings": []}

    def disease(tool_input):
        seen["disease_search"] = tool_input
        return {"tool_name": "disease_search", "status": "NO_EVIDENCE", "findings": [], "citations": [],
                "graph_paths": [], "warnings": ["근거 없음"]}
    return {"action_search": action, "disease_search": disease}


def _deps(books, router):
    return {"driver": None, "database": None, "books": books, "router": router}


def test_single_intent_route():
    route = decision_to_route(RouteDecision(response_type="DOMAIN", parts=[Part(intent="LEGAL", question="질문")]), {})
    assert route["intent"] == "LEGAL" and route["secondary"] == [] and route["sub_questions"] == {"legal_search": "질문"}


def test_parts_are_deduplicated_and_mapped_to_tools():
    decision = RouteDecision(response_type="DOMAIN", parts=[
        Part(intent="ACTION", question="반 친구 등교?"), Part(intent="ACTION", question="중복"),
        Part(intent="DISEASE", question="수두 잠복기?")])
    route = decision_to_route(decision, {"diseases": ["d"], "roles": []})
    assert route["secondary"] == ["DISEASE"] and route["diseases"] == ["d"]
    assert route["sub_questions"] == {"action_search": "반 친구 등교?", "disease_search": "수두 잠복기?"}


def test_out_of_scope_keeps_llm_reason():
    route = decision_to_route(RouteDecision(response_type="OUT_OF_SCOPE", reason="진단은 할 수 없습니다."), {})
    assert route == {"response_type": "OUT_OF_SCOPE", "reason": "진단은 할 수 없습니다.", "confidence": "llm"}


def test_domain_without_parts_is_rejected():
    with pytest.raises(ValueError):
        decision_to_route(RouteDecision(response_type="DOMAIN", parts=[]), {})


def test_main_graph_sends_sub_questions_to_each_tool(books):
    decision = RouteDecision(response_type="DOMAIN", parts=[
        Part(intent="ACTION", question="수두 걸린 학생 반 친구들은 등교해도 되나요?"),
        Part(intent="DISEASE", question="수두 잠복기는 얼마인가요?")])
    deps, seen = _deps(books, make_router(FakeLLM(decision))), {}
    out = run_main(QUERY, deps, _registry(seen))
    assert out["tools_used"] == ["action_search", "disease_search"]
    assert seen["action_search"]["question"] == "수두 걸린 학생 반 친구들은 등교해도 되나요?"
    assert seen["disease_search"]["question"] == "수두 잠복기는 얼마인가요?"
    assert QUERY["question"] in seen["disease_search"]["task"]
    step = next(t for t in deps["main_trace"] if t["step"] == "classify_scope")
    assert step["mode"] == "llm" and step["rule"]["response_type"] == "DOMAIN"


def test_single_intent_passes_original_question(books):
    decision = RouteDecision(response_type="DOMAIN", parts=[Part(intent="ACTION", question="다시 쓴 질문")])
    seen = {}
    run_main(QUERY, _deps(books, make_router(FakeLLM(decision))), _registry(seen))
    assert seen["action_search"]["question"] == QUERY["question"]


def test_single_intent_disease_gets_document_terms(books):
    decision = RouteDecision(response_type="DOMAIN", parts=[Part(intent="DISEASE", question="홍역의 전파 경로는?")])
    seen = {}
    run_main({**QUERY, "question": "홍역은 어떻게 옮아요?"}, _deps(books, make_router(FakeLLM(decision))),
             _registry(seen))
    assert seen["disease_search"]["question"] == "홍역의 전파 경로는?"
    assert "홍역은 어떻게 옮아요?" in seen["disease_search"]["task"]


def test_llm_out_of_scope_skips_tools(books):
    decision = RouteDecision(response_type="OUT_OF_SCOPE", reason="학생 진단은 도와드릴 수 없습니다.")
    seen = {}
    out = run_main(QUERY, _deps(books, make_router(FakeLLM(decision))), _registry(seen))
    assert out["response_type"] == "OUT_OF_SCOPE" and "학생 진단은 도와드릴 수 없습니다." in out["answer"]
    assert seen == {} and out["tools_used"] == []


def test_router_failure_falls_back_to_rules(books):
    deps, seen = _deps(books, make_router(FakeLLM(TimeoutError("느림")))), {}
    out = run_main(QUERY, deps, _registry(seen))
    step = next(t for t in deps["main_trace"] if t["step"] == "classify_scope")
    assert step["mode"] == "rule_fallback" and "TimeoutError" in step["error"]
    assert out["response_type"] == "DOMAIN" and out["tools_used"]
