"""벌칙 카드(제81조·제79조의4·제80조) 검사 (유종혁 / prevention_legal). DB 없이 실행한다.

설명
- 검색과 DB 조회를 가짜로 바꿔 카드 조건(필수어·하나라도·제외어)과 묶음 규칙만 확인한다.
- 실DB 로 같은 질문을 돌린 결과는 명세 7-27절에 적는다.
"""
import json

import pytest

from rag.prevention_legal import run as run_module
from rag.prevention_legal.retrieve import BLOCKS_FILE
from rag.prevention_legal.run import (
    ELEM_ROLE_CARD,
    PAIR_GROUPS,
    PENALTY_DOCTOR_A_CARD,
    PENALTY_DOCTOR_B_CARD,
    PENALTY_REPORT_CARD,
    UNIV_ROLE_CARD,
    run,
)

LAW = "moleg_infectious_disease_act_21521"
C81 = f"{LAW}:p0057:b02"
C79_4 = f"{LAW}:p0056:b04"
C80 = f"{LAW}:p0056:b05"
CARDS = {C81: PENALTY_REPORT_CARD, C79_4: PENALTY_DOCTOR_A_CARD, C80: PENALTY_DOCTOR_B_CARD}
MEASLES = "kdca_measles_2026"
C94 = f"{MEASLES}:p0094:b01"
C93 = f"{MEASLES}:p0093:b05"


def _patch(monkeypatch, chunk_ids):
    # 검색이 chunk_ids 를 돌려주고, DB 청크 원문에는 각 카드의 인용문이 들어 있는 것처럼 꾸민다
    texts = {cid: card["출처"][2] for cid, card in CARDS.items()}
    texts[C94] = UNIV_ROLE_CARD["출처"][2]   # 대학교·초중고 비교 카드 묶음 회귀 확인용
    texts[C93] = ELEM_ROLE_CARD["출처"][2]
    monkeypatch.setattr(run_module, "search_blocks", lambda *_a, **_k: [{"chunk_id": c} for c in chunk_ids])
    monkeypatch.setattr(run_module, "section_candidates", lambda *_a, **_k: [])
    monkeypatch.setattr(run_module, "fetch_chunks",
                        lambda ids: {i: {"text": texts[i], "pdf_page": int(i.split(":p")[1][:4])} for i in ids if i in texts})


def _ask(question):
    return run({"question": question, "region": "NATIONAL", "allowed_document_ids": [LAW, MEASLES]})


def test_school_report_penalty_gets_partial_with_law_81(monkeypatch):
    _patch(monkeypatch, [C81, C79_4, C80])
    for q in ("홍역 환자 발생을 보건소에 신고하지 않으면 학교는 어떤 벌칙을 받나요?",
              "감염병예방법 제12조제1항에 따른 신고를 게을리하면 벌금이 얼마인가요?",
              "신고를 게을리한 경우의 벌칙은 무엇인가요?"):
        out = _ask(q)
        assert out["status"] == "PARTIAL", q
        assert [c["chunk_id"] for c in out["citations"]] == [C81], q   # 학교·게을리 질문은 제81조만 쓴다
        assert "200만원 이하" in out["answer"] and "확정할 수 없습니다" in " ".join(out["warnings"])


def test_doctor_penalty_answers_both_grades_together(monkeypatch):
    _patch(monkeypatch, [C79_4, C80])
    out = _ask("의사가 감염병 환자를 신고하지 않으면 어떤 벌칙을 받나요?")
    assert out["status"] == "PARTIAL"
    assert sorted(c["chunk_id"] for c in out["citations"]) == sorted([C79_4, C80])
    assert "500만원 이하" in out["answer"] and "300만원 이하" in out["answer"]   # 급수별 형량을 함께 보여 준다


def test_doctor_penalty_is_not_answered_when_only_one_grade_is_retrieved(monkeypatch):
    # 제1·2급 형량만 나오면 제3·4급 형량이 빠진 반쪽 답이 되므로 묶음 전체를 뺀다
    _patch(monkeypatch, [C79_4])
    out = _ask("의사가 감염병 환자를 신고하지 않으면 어떤 벌칙을 받나요?")
    assert out["status"] == "INSUFFICIENT_EVIDENCE" and out["citations"] == []


def test_questions_outside_the_cards_are_not_answered(monkeypatch):
    _patch(monkeypatch, [C81, C79_4, C80])
    for q in ("결핵 환자를 신고하지 않으면 벌금이 있나요?",         # 결핵은 다른 법의 벌칙이라 제외
              "신고를 하지 않으면 과태료는 얼마인가요?",             # 과태료는 이 카드가 다루지 않는다
              "홍역 환자 발생 신고 시기는 언제인가요?",              # 벌칙 낱말이 없다
              "벌금은 어떤 경우에 내나요?"):                          # 신고 낱말이 없다
        out = _ask(q)
        assert not any(c["chunk_id"] in CARDS for c in out["citations"]), q


def test_confirmation_demanding_penalty_question_still_uses_the_refusal_card(monkeypatch):
    # 골든 Q28(처벌 '확정' 요구)은 벌칙 카드보다 거부 카드가 먼저 걸러 근거 없이 답한다
    _patch(monkeypatch, [C81, C79_4, C80])
    out = _ask("학교가 신고를 늦게 하면 어떤 처벌을 받는지 확정해서 알려 주세요.")
    assert out["status"] == "INSUFFICIENT_EVIDENCE" and out["citations"] == []


def test_pair_groups_contain_both_penalty_cards():
    assert [PENALTY_DOCTOR_A_CARD, PENALTY_DOCTOR_B_CARD] in PAIR_GROUPS


def test_penalty_quotes_are_in_real_block_text_when_local_blocks_exist():
    if not BLOCKS_FILE.exists():
        pytest.skip("로컬 blocks.jsonl 이 없어 실제 원문 대조를 건너뜁니다")
    texts = {}
    for line in BLOCKS_FILE.read_text(encoding="utf-8").splitlines():
        b = json.loads(line)
        texts[b["chunk_id"]] = b["text"]
    for cid, card in CARDS.items():
        assert card["출처"][2] in texts[cid], cid


def test_substring_lookalike_words_do_not_fire(monkeypatch):
    # '벌써'·'벌어지다'는 벌칙 낱말이 아니다(단독 '벌' 부분 문자열로 걸리면 안 된다)
    _patch(monkeypatch, [C81, C79_4, C80])
    for q in ("학교에 신고가 벌써 접수됐나요?", "병원 신고 이후 무슨 일이 벌어지나요?"):
        out = _ask(q)
        assert not any(c["chunk_id"] in CARDS for c in out["citations"]), q


def test_penalty_question_without_confirmation_demand_is_answered_conditionally(monkeypatch):
    # '확정'을 요구하지 않은 같은 사실 질문은 거부하지 않고 조건부(PARTIAL)로 답한다
    _patch(monkeypatch, [C81])
    out = _ask("학교가 신고를 늦게 하면 어떤 처벌을 받나요?")
    assert out["status"] == "PARTIAL" and [c["chunk_id"] for c in out["citations"]] == [C81]
    assert "처할 수 있습니다" in out["answer"] and "처해집니다" not in out["answer"]   # 처벌을 단정하지 않는다


def test_doctor_and_school_question_uses_all_three_cards(monkeypatch):
    _patch(monkeypatch, [C81, C79_4, C80])
    out = _ask("의사와 학교가 신고하지 않으면 어떤 벌칙을 받나요?")
    assert sorted(c["chunk_id"] for c in out["citations"]) == sorted([C81, C79_4, C80])


def test_university_pair_still_needs_both_cards(monkeypatch):
    # 묶음 규칙을 일반화한 뒤에도 대학교·초중고 비교 묶음이 그대로 동작하는지 확인한다
    q = "대학교에서 홍역 환자가 발생했을 때 학교 담당자의 역할은 초·중·고와 어떻게 다른가요?"
    _patch(monkeypatch, [C94, C93])
    both = _ask(q)
    assert both["status"] == "ANSWERED" and sorted(c["chunk_id"] for c in both["citations"]) == sorted([C94, C93])
    _patch(monkeypatch, [C94])
    one = _ask(q)
    assert one["status"] == "INSUFFICIENT_EVIDENCE" and one["citations"] == []


def test_pair_membership_uses_object_identity():
    # 내용이 같아도 다른 객체인 카드는 묶음 구성원으로 보지 않는다(is 비교)
    clone = dict(PENALTY_DOCTOR_A_CARD)
    group = PAIR_GROUPS[1]
    assert clone == group[0] and not any(clone is member for member in group)
