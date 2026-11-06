"""법 제12조 ① 카드(LAW_Q12_CARD, LAW_Q10B_CARD)와 답 카드 없음 경고 검사 (유종혁 / prevention_legal). DB 없이 실행한다.

설명
- 검색과 DB 조회(search_blocks, section_candidates, fetch_chunks)를 가짜로 바꿔, 카드 조건(필수어·질문어·제외어)과 중복 발화만 확인한다.
- 카드 인용문이 실제 청크 원문에 있는지는 로컬 blocks.jsonl 이 있을 때만 확인한다(Git 제외 파일이라 없으면 건너뛴다).
  실DB 로 같은 질문을 돌린 결과는 명세 7-25절에 적는다.
"""
import json

import pytest

from rag.prevention_legal import run as run_module
from rag.prevention_legal.retrieve import BLOCKS_FILE
from rag.prevention_legal.run import (
    LAW12_QUOTE,
    LAW_Q10A_CARD,
    LAW_Q10B_CARD,
    LAW_Q11_CARD,
    LAW_Q12_CARD,
    LAW_Q13_CARD,
    run,
)

LAW = "moleg_infectious_disease_act_21521"
CHUNK_18 = f"{LAW}:p0018:b01"
CHUNK_19_B01 = f"{LAW}:p0019:b01"
CHUNK_19_B02 = f"{LAW}:p0019:b02"
CHUNK_19_B03 = f"{LAW}:p0019:b03"
ALLOWED = [LAW]


def _patch(monkeypatch, chunk_ids):
    # 검색이 chunk_ids 를 돌려주고, DB 청크 원문에는 각 카드의 인용문이 들어 있는 것처럼 꾸민다
    texts = {CHUNK_18: LAW_Q10A_CARD["출처"][2], CHUNK_19_B01: LAW12_QUOTE,
             CHUNK_19_B02: LAW_Q11_CARD["출처"][2], CHUNK_19_B03: LAW_Q13_CARD["출처"][2]}
    monkeypatch.setattr(run_module, "search_blocks", lambda *_a, **_k: [{"chunk_id": c} for c in chunk_ids])
    monkeypatch.setattr(run_module, "section_candidates", lambda *_a, **_k: [])
    monkeypatch.setattr(run_module, "fetch_chunks",
                        lambda ids: {i: {"text": texts[i], "pdf_page": int(i.split(":p")[1][:4])} for i in ids if i in texts})


def _ask(question):
    return run({"question": question, "region": "NATIONAL", "allowed_document_ids": ALLOWED})


def _fired(out):
    return sorted(f["claim"] for f in out["findings"])


def test_school_head_obligation_question_gets_partial_with_law_12(monkeypatch):
    _patch(monkeypatch, [CHUNK_19_B01])
    out = _ask("감염병예방법상 학교장의 신고 의무는 무엇인가요?")
    assert out["status"] == "PARTIAL"                       # 학교장↔관리인 대응이 확정되지 않아 부분 답
    assert [c["chunk_id"] for c in out["citations"]] == [CHUNK_19_B01]
    assert any("학교장" in w for w in out["warnings"])
    # 제1급~제3급·보건복지부령 한정과 신고 주체(제2호)가 답과 인용문에 모두 들어 있다
    assert "제1급~제3급감염병" in out["answer"] and "관리인, 경영자 또는 대표자" in out["answer"]
    assert "제1급감염병부터 제3급감염병까지" in out["citations"][0]["excerpt"]
    assert "관리인, 경영자 또는 대표자" in out["citations"][0]["excerpt"]


def test_manager_question_is_answered_by_the_same_card(monkeypatch):
    _patch(monkeypatch, [CHUNK_19_B01])
    out = _ask("감염병예방법상 학교의 관리인이나 대표자도 신고해야 하는 경우가 있나요?")
    assert out["status"] == "PARTIAL" and len(out["findings"]) == 1


def test_disease_penalty_and_unrelated_questions_are_not_answered(monkeypatch):
    # 질병 이름·제재 낱말이 든 질문, 학교와 무관한 신고 질문은 이 카드가 답하지 않는다
    _patch(monkeypatch, [CHUNK_19_B01])
    for q in ("결핵 환자가 생기면 학교장이 보건소에 신고해야 하나요?", "성홍열도 학교장이 보건소에 신고해야 하나요?",
              "수두 환자 발생 시 학교의 신고 의무는 무엇인가요?", "학교가 신고 의무를 어기면 과태료는 얼마인가요?",
              "병원의 신고 의무는 무엇인가요?"):
        out = _ask(q)
        assert out["status"] == "INSUFFICIENT_EVIDENCE" and out["citations"] == [], q


def test_q11_and_q13_style_questions_do_not_double_fire_with_q12(monkeypatch):
    # 학교장이 들어 있어도 Q11(신고의무자가 아닌 사람의 알림)·Q13(보건소장의 보고) 문장은 Q12 카드를 끌어오지 않는다
    _patch(monkeypatch, [CHUNK_19_B01, CHUNK_19_B02, CHUNK_19_B03])
    out11 = _ask("신고의무자가 아닌 학교장이 의심 환자를 발견한 경우 어떻게 해야 하나요?")
    assert LAW_Q12_CARD["주장"] not in _fired(out11)
    out13 = _ask("학교장 신고를 받은 보건소장은 그 다음에 어떤 보고를 하나요?")
    assert LAW_Q12_CARD["주장"] not in _fired(out13)


def test_q10_uses_the_two_existing_cards_without_duplicate_claims(monkeypatch):
    # 구분·법적이 든 Q10 은 Q10A·Q10B 두 카드만 쓴다. 같은 주장이 두 번 나오면 안 된다
    _patch(monkeypatch, [CHUNK_18, CHUNK_19_B01])
    out = _ask("의사가 하는 신고와 학교장 등이 하는 신고는 법적으로 어떻게 구분되나요?")
    claims = _fired(out)
    assert len(claims) == 2 and len(set(claims)) == 2
    assert LAW_Q10B_CARD["주장"] in claims and LAW_Q10A_CARD["주장"] in claims


def test_card_whose_document_is_not_allowed_is_not_used(monkeypatch):
    _patch(monkeypatch, [CHUNK_19_B01])
    out = run({"question": "감염병예방법상 학교장의 신고 의무는 무엇인가요?", "region": "NATIONAL",
               "allowed_document_ids": ["kdca_measles_2026"]})
    assert out["status"] == "INSUFFICIENT_EVIDENCE" and out["citations"] == []


def test_card_whose_chunk_is_not_retrieved_is_not_used(monkeypatch):
    _patch(monkeypatch, [])                                  # 검색 후보에 청크가 없다
    out = _ask("감염병예방법상 학교장의 신고 의무는 무엇인가요?")
    assert out["status"] == "INSUFFICIENT_EVIDENCE"


def test_no_card_warning_text_is_current(monkeypatch):
    _patch(monkeypatch, [])
    out = _ask("무관한 질문입니다 알려 주세요")
    assert out["status"] == "INSUFFICIENT_EVIDENCE"
    text = " ".join(out["warnings"])
    assert "답 카드" in text and "허용 목록" in text and "검색되지" in text and "고정 샘플" not in text


def test_law12_quote_is_in_real_block_text_when_local_blocks_exist():
    if not BLOCKS_FILE.exists():
        pytest.skip("로컬 blocks.jsonl 이 없어 실제 원문 대조를 건너뜁니다")
    texts = {}
    for line in BLOCKS_FILE.read_text(encoding="utf-8").splitlines():
        b = json.loads(line)
        texts[b["chunk_id"]] = b["text"]
    assert LAW12_QUOTE in texts[CHUNK_19_B01]
