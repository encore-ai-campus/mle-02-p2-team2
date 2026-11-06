"""build_citations 의 가짜 출처 검출 검사 (유종혁 / prevention_legal). DB 없이 실행한다.

설명
- 카드의 인용문이 DB 청크 원문에 없거나, 청크가 DB 에 없으면 ValueError 로 막는지 확인한다.
- DB 조회 함수(fetch_chunks)는 가짜로 바꿔 쓴다. 정상 경우도 함께 확인해 검사가 항상 실패하는 것이 아님을 보인다.
"""
import pytest

from rag.prevention_legal import run as run_module
from rag.prevention_legal.run import UNIV_ROLE_CARD, build_citations

CHUNK = "kdca_measles_2026:p0094:b01"
QUOTE = UNIV_ROLE_CARD["출처"][2]


def test_real_quote_passes(monkeypatch):
    monkeypatch.setattr(run_module, "fetch_chunks", lambda ids: {CHUNK: {"text": QUOTE + " (이하 생략)", "pdf_page": 94}})
    citations = build_citations([UNIV_ROLE_CARD])
    assert [c["chunk_id"] for c in citations] == [CHUNK]
    assert citations[0]["pdf_page"] == 94


def test_quote_not_in_chunk_text_is_rejected(monkeypatch):
    # 인용문이 청크 원문에 없으면 가짜 출처로 보고 막아야 한다
    monkeypatch.setattr(run_module, "fetch_chunks", lambda ids: {CHUNK: {"text": "전혀 다른 문장입니다.", "pdf_page": 94}})
    with pytest.raises(ValueError, match="원문 안에 없습니다"):
        build_citations([UNIV_ROLE_CARD])


def test_missing_chunk_is_rejected(monkeypatch):
    # DB 에 청크가 없으면 근거를 만들 수 없으므로 막아야 한다
    monkeypatch.setattr(run_module, "fetch_chunks", lambda ids: {})
    with pytest.raises(ValueError, match="찾지 못했습니다"):
        build_citations([UNIV_ROLE_CARD])
