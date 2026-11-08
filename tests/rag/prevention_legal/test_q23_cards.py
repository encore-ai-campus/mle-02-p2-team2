"""Q23(백일해 접촉자) 카드: 원문 대조(11/8 체크리스트 25번)로 고친 주장 범위·묶음·경고를 확인한다. DB 없이 실행한다.

설명
- 검색과 DB 조회를 가짜로 바꿔 카드 선택 규칙만 확인한다. 실DB 결과는 명세 7-28절에 적는다.
"""
import pytest

from rag.prevention_legal import run as run_module
from rag.prevention_legal.run import PAIR_GROUPS, Q23_CARDS, run

VAC = "kdca_vaccine_preventable_2026"
QUESTION = "학교에서 백일해 환자가 발생했을 때 접촉자 안내는 어떻게 하나요?"
R01, R04, R05, R06, R07 = (f"{VAC}:p0116:t01r0{n}" for n in (1, 4, 5, 6, 7))
TEXTS = {c["chunk_id"]: c["출처"][2] for c in Q23_CARDS}


def _by_chunk(chunk_id: str) -> dict:
    return next(c for c in Q23_CARDS if c["chunk_id"] == chunk_id)


def _ask(monkeypatch, retrieved):
    monkeypatch.setattr(run_module, "search_blocks", lambda *_a, **_k: [{"chunk_id": c} for c in retrieved])
    monkeypatch.setattr(run_module, "section_candidates", lambda *_a, **_k: [])
    monkeypatch.setattr(run_module, "fetch_chunks",
                        lambda ids: {i: {"text": TEXTS[i], "pdf_page": 116} for i in ids if i in TEXTS})
    return run({"question": QUESTION, "region": "NATIONAL", "allowed_document_ids": [VAC]})


def test_r04_claim_stays_within_its_quote():
    # r04 인용문은 '예방적 항생제 복용'만 말한다. 대상(고위험군)·시기(3주)는 인용문에 없으므로 주장에 넣지 않고
    # 각각 r01·r05 카드로 따로 인용한다.
    card = _by_chunk(R04)
    assert "접촉자는" not in card["주장"]
    assert "고위험군" not in card["주장"]
    assert "3주" not in card["주장"]
    assert "고위험군" in _by_chunk(R01)["출처"][2]
    assert "3주" in _by_chunk(R05)["출처"][2]


def test_all_cards_returned_with_single_scope_notices(monkeypatch):
    out = _ask(monkeypatch, [R01, R04, R05, R06, R07])
    assert out["status"] == "PARTIAL"
    assert sorted(c["chunk_id"] for c in out["citations"]) == sorted([R01, R04, R05, R06, R07])
    warnings = out["warnings"]
    assert len(warnings) == 2 and len(set(warnings)) == 2
    assert sum("학교 역할은 원문에 없어" in w for w in warnings) == 1       # 학교 역할 부재 안내는 한 번
    assert sum("동거인·기타 접촉자 행에는 적혀 있지 않습니다" in w for w in warnings) == 1   # 표 구조 안내도 한 번
    assert "학교가 할 역할" not in out["answer"]


@pytest.mark.parametrize("retrieved", [[R04], [R05], [R01, R04], [R04, R05], [R01, R05]])
def test_exposure_prophylaxis_cards_need_all_three(monkeypatch, retrieved):
    # 대상(r01)·요법(r04)·시기(r05)는 하나라도 빠지면 셋 모두 뺀다. 대상 없이 요법이나 3주 문장만 나가지 않게 한다.
    out = _ask(monkeypatch, retrieved)
    assert out["status"] == "INSUFFICIENT_EVIDENCE" and out["citations"] == []


def test_partial_retrieval_keeps_unrelated_contact_cards(monkeypatch):
    # 예방요법 묶음이 빠져도 예방접종(r06)·모니터링(r07)은 두 구분 모두에 있는 내용이라 그대로 답한다
    out = _ask(monkeypatch, [R04, R06, R07])
    assert sorted(c["chunk_id"] for c in out["citations"]) == sorted([R06, R07])
    assert "예방적 항생제" not in out["answer"]


def test_pertussis_prophylaxis_group_is_registered_by_identity():
    group = next(g for g in PAIR_GROUPS if g[0] is Q23_CARDS[0])
    assert [c["chunk_id"] for c in group] == [R01, R04, R05]
