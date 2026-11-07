"""근거 재순위·충분성 판정(11/07 ②) — 모든 Tool 결과를 합친 뒤 LLM 1회로 '질문에 답하는 데 꼭 필요한 근거'만 고른다.

배경(11/06 진단): 학교 행동 Tool은 문항당 인용 9개 안팎을 냈고, 그중 골든 정답은 10%였다(하한). 점수 문턱(4점)은
걸러내지 못했다. 근거를 넓게 찾는 것은 Tool이, 필요한 근거를 고르는 것은 이 단계가 맡는다.

원칙
- 단위는 Citation(청크) 하나 = 근거 카드 하나다. 카드에는 그 청크를 쓴 Finding claim과 원문 발췌를 보여 준다.
- LLM은 카드 ID 중에서만 고른다(없는 ID는 버린다). 근거를 새로 만들 수 없다.
- 고른 근거마다 원문에서 글자 그대로 옮긴 핵심 구절을 받고, 코드가 원문에 실제로 있는지 대조한다(없으면 버린다).
  이 구절은 답변 작성기 프롬프트와 규칙 검사(③)의 기준점이다. 근거마다 LLM을 따로 부르지 않는다.
- 같은 호출로 하위 질문마다 '답함/일부/근거 없음'을 받아 status와 '확인하지 못한 부분'에 반영한다.
- 실패·시간 초과·형식 위반이면 호출자가 재순위 없이 기존 근거를 그대로 쓴다.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_MODEL = "gpt-6-luna"
MAX_CARDS = 36
MAX_PICKS = 8                  # 문서별 근거를 따로 고르므로 6 → 8(11/07)
EXCERPT_CHARS = 420
CLAIM_CHARS = 220
MIN_QUOTE = 4

RERANK_PROMPT = """너는 학교 감염병 대응 안내 서비스의 근거 선별자다. 답변을 쓰지 말고, 아래 [근거 카드] 중에서
[하위 질문]에 답하는 데 **꼭 필요한** 근거만 고른다.

고르는 기준
1. DIRECT: 하위 질문에 직접 답하는 근거. 질문이 묻는 대상(질병·역할·단계·상황)과 맞아야 한다.
2. CONDITION: 직접 답을 적용할 때 꼭 알아야 하는 조건·예외·시점·담당이 담긴 근거.
3. PREMISE: 질문의 전제(예: '반드시 ~해야 하나요', '바로 ~해야 하나요')가 원문과 다를 때 그것을 보여 주는 근거.
   이런 근거는 질문 표현과 덜 비슷해도 반드시 고른다.
- 서로 다른 문서(교육부 매뉴얼·경기도 가이드북·질병관리청 지침 등)에 같은 내용이 있으면 문서마다 하나씩 고른다
  (문서별 근거를 합치지 않는다). 같은 문서 안에서 같은 내용이 반복되면 하나만 고른다.
- '모의훈련 시나리오 예시'와 '질문 단계와 다름' 표시가 있는 카드는 본문 지침 근거가 없을 때만 고른다.
- 질문이 여러 담당('각각')·여러 단계·여러 항목을 물으면 항목마다 그에 답하는 근거를 고른다(한 항목만 답하지 않는다).
- 질문과 주제만 비슷하고 답에 쓰이지 않는 카드는 고르지 않는다. 최대 {max_picks}개.
- 답할 근거가 하나도 없으면 picks를 비운다.

각 pick
- id: 카드 ID(예: E3). part: 이 근거가 답하는 하위 질문 번호.
- quotes: 그 카드의 '원문' 또는 '정리'에서 **글자 그대로 복사한** 핵심 구절 1~2개(각 10~80자). 고쳐 쓰거나 요약하지 않는다.

coverage: 하위 질문마다 고른 근거로 답할 수 있는지 ANSWERED(충분)·PARTIAL(일부만)·NONE(근거 없음)과,
ANSWERED가 아니면 missing에 확인하지 못한 부분을 한 문장으로 쓴다."""


class Pick(BaseModel):
    id: str
    role: Literal["DIRECT", "CONDITION", "PREMISE"]
    part: int = 1
    quotes: list[str] = Field(default_factory=list)


class Coverage(BaseModel):
    part: int
    status: Literal["ANSWERED", "PARTIAL", "NONE"]
    missing: str = ""


class RerankDecision(BaseModel):
    picks: list[Pick] = Field(default_factory=list)
    coverage: list[Coverage] = Field(default_factory=list)


# ---------------------------------------------------------------- 카드

def _clean(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def compact(text: str) -> str:
    """구절 대조용: NFKC·소문자, 공백·기호 제거."""
    text = unicodedata.normalize("NFKC", _clean(text)).casefold()
    return re.sub(r"[\s|·•∙\-–—\"'“”‘’()\[\]{}<>.,:;!?~※■□○●▶→「」『』]", "", text)


def evidence_cards(findings: list[dict], citations: list[dict], titles: dict[str, str]) -> list[dict]:
    cards = []
    for number, citation in enumerate(citations[:MAX_CARDS], 1):
        claims = [f["claim"] for f in findings if citation["chunk_id"] in f["chunk_ids"]]
        excerpt = _clean(citation["excerpt"])
        cards.append({"id": f"E{number}", "chunk_id": citation["chunk_id"],
                      "document": titles.get(citation["document_id"], citation["document_id"]),
                      "page": citation.get("pdf_page"), "claims": [_clean(c)[:CLAIM_CHARS] for c in claims[:4]],
                      "excerpt": excerpt[:EXCERPT_CHARS], "source": " ".join([excerpt, *claims])})
    return cards


def rerank_prompt(question: str, parts: list[str], cards: list[dict]) -> str:
    lines = [f"[원 질문]\n{question}\n", "[하위 질문]"]
    lines += [f"{i}. {p}" for i, p in enumerate(parts, 1)]
    lines.append("\n[근거 카드]")
    for card in cards:
        claims = "\n".join(f"  정리: {c}" for c in card["claims"])
        lines.append(f"[{card['id']}] {card['document']} PDF {card['page']}쪽\n{claims}\n  원문: {card['excerpt']}")
    return "\n".join(lines)


def verify_quotes(quotes: list[str], source: str) -> tuple[list[str], list[str]]:
    """(원문에 있는 구절, 없는 구절)."""
    haystack = compact(source)
    kept, dropped = [], []
    for quote in quotes:
        key = compact(quote)
        (kept if len(key) >= MIN_QUOTE and key in haystack else dropped).append(quote.strip())
    return kept, dropped


# ---------------------------------------------------------------- 적용

def apply_decision(decision: RerankDecision, cards: list[dict], parts: list[str]) -> dict:
    """LLM 결정 → {'picks': {chunk_id: {...}}, 'order': [...], 'coverage': [...], 'invalid': [...]}."""
    by_id = {c["id"]: c for c in cards}
    picks, order, invalid = {}, [], []
    for pick in decision.picks:
        card = by_id.get(pick.id.strip().upper())
        if not card:
            invalid.append(pick.id)
            continue
        if card["chunk_id"] in picks or len(order) >= MAX_PICKS:
            continue
        kept, dropped = verify_quotes(pick.quotes, card["source"])
        part = pick.part if 1 <= pick.part <= len(parts) else 1
        picks[card["chunk_id"]] = {"card": card["id"], "role": pick.role, "part": part, "quotes": kept,
                                   "dropped_quotes": dropped}
        order.append(card["chunk_id"])
    coverage = []
    for number, part in enumerate(parts, 1):
        found = next((c for c in decision.coverage if c.part == number), None)
        status = found.status if found else ("ANSWERED" if any(p["part"] == number for p in picks.values()) else "NONE")
        coverage.append({"part": number, "question": part, "status": status,
                         "missing": (found.missing.strip() if found else "") or ("" if status == "ANSWERED" else part)})
    return {"picks": picks, "order": order, "coverage": coverage, "invalid": invalid}


def filter_evidence(findings: list[dict], citations: list[dict], order: list[str]) -> tuple[list[dict], list[dict]]:
    """대표 근거(첫 chunk_id)가 골라진 Finding만 남긴다(다른 청크만 겹치는 Finding이 끼어들지 않게).
    Citation은 고른 순서 먼저, 남은 Finding이 쓰는 다른 청크를 뒤에 둔다(Finding의 모든 chunk_id가 Citation에 있어야
    하는 계약)."""
    chosen = set(order)
    kept = [f for f in findings if f["chunk_ids"] and f["chunk_ids"][0] in chosen]
    needed = {c for f in kept for c in f["chunk_ids"]}
    by_chunk = {c["chunk_id"]: c for c in citations}
    ordered = [by_chunk[c] for c in order if c in by_chunk]
    ordered += [c for c in citations if c["chunk_id"] in needed and c["chunk_id"] not in chosen]
    return kept, ordered


# ---------------------------------------------------------------- 실행기

def make_llm():
    from langchain_openai import ChatOpenAI

    model = os.getenv("SCHOOL_RERANK_MODEL", DEFAULT_MODEL)
    extra = {"reasoning_effort": "none"} if model == "gpt-6-luna" else {}
    return ChatOpenAI(model=model, temperature=0, timeout=30, max_retries=1, **extra)


def make_reranker(llm=None) -> Callable[[str, list[str], list[dict], list[dict], dict], dict]:
    """reranker(question, parts, findings, citations, titles) → apply_decision 결과 + 'cards'. 실패하면 예외."""
    structured = (llm or make_llm()).with_structured_output(RerankDecision)

    def reranker(question: str, parts: list[str], findings: list[dict], citations: list[dict],
                 titles: dict[str, str]) -> dict:
        cards = evidence_cards(findings, citations, titles)
        if not cards:
            return {"picks": {}, "order": [], "coverage": [], "invalid": [], "cards": 0}
        raw = structured.invoke([("system", RERANK_PROMPT.format(max_picks=MAX_PICKS)),
                                 ("user", rerank_prompt(question, parts, cards))])
        decision = raw if isinstance(raw, RerankDecision) else RerankDecision.model_validate(raw)
        return {**apply_decision(decision, cards, parts), "cards": len(cards)}

    return reranker
