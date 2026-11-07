"""거부 카드 검사 (유종혁 / prevention_legal). DB 없이 실행한다.

설명
- 처벌 수위를 묻는 질문은 근거 없이 INSUFFICIENT_EVIDENCE 로 답하고, Citation·Finding 을 만들지 않는다.
- 처벌 단어가 없는 질문은 거부 카드가 걸리지 않는다. 이 경우 DB 검색이 필요하므로 여기서는 검사하지 않는다.
"""
from rag.prevention_legal import run as run_module
from rag.prevention_legal.run import run

ALLOWED = ["kdca_measles_2026", "moleg_infectious_disease_act_21521"]


def test_penalty_question_is_refused_without_citations(monkeypatch):
    # 거부 카드가 맞으면 검색 함수를 부르지 않아야 한다. 부르면 테스트가 실패하도록 막는다.
    def no_search(*_args, **_kwargs):
        raise AssertionError("거부 질문에서 DB 검색을 하면 안 됩니다")

    monkeypatch.setattr(run_module, "search_blocks", no_search)
    out = run({"question": "학교가 신고를 늦게 하면 어떤 처벌을 받는지 확정해서 알려 주세요.",
               "region": "NATIONAL", "allowed_document_ids": ALLOWED})
    assert out["status"] == "INSUFFICIENT_EVIDENCE"
    assert out["citations"] == [] and out["findings"] == []
    assert "확정할 수 없습니다" in out["answer"]
    assert "벌칙 조문이 없습니다" not in out["answer"]   # 벌칙 조문을 적재한 뒤라 이 문장은 사실이 아니다


def test_refusal_needs_both_penalty_and_delay(monkeypatch):
    # '처벌'만 있고 '늦게'가 없는 질문은 거부하지 않는다. 검색까지 가서 근거 없음으로 끝나야 한다.
    monkeypatch.setattr(run_module, "search_blocks", lambda *_a, **_k: [])
    monkeypatch.setattr(run_module, "section_candidates", lambda *_a, **_k: [])
    out = run({"question": "신고 절차와 처벌을 알려 주세요.", "region": "NATIONAL", "allowed_document_ids": ALLOWED})
    assert not any("확정할 수 없습니다" in w for w in out["warnings"])
