"""LLM 라우터(11/06 진단 후속) — 질문의 범위·의도·하위 질문을 LLM 구조화 출력으로 정한다.

배경: 규칙 분류기(main_graph.classify_question)는 골든 40문항에서 40/40이었지만, 골든 밖 일상 표현 20문항에서는 9/20이었다.
교직원 질문 15개는 모두 ACTION 하나로만 갔다(라우터_후보선별_진단_20261106.md).

원칙
- 결정은 LLM이 한다. 출력은 고정된 형식(enum)으로만 받고, 형식 위반·시간 초과·예외가 나면 규칙 분류기로 되돌린다.
- 질병·역할 이름은 지금처럼 사전으로만 인식한다(규칙 분류기의 diseases/roles를 그대로 쓴다). LLM이 사전 밖 ID를 만들지 않는다.
- 하위 질문(문서 용어로 다듬은 의도별 질문)을 Tool에 넘기는 경우는 둘이다.
  (1) 의도가 둘 이상인 복합 질문, (2) 질문 낱말이 청크에 있어야 답하는 disease_search(일상어 '옮아요'로는
  NO_EVIDENCE, '전파 경로'로는 OK — 11/06 확인). 그 밖에는 원 질문을 넘겨 각 Tool의 기존 동작을 보존한다
  (action_search는 사전 기반 계획, legal_search는 원 질문 문장으로 답 카드를 맞춘다).
- 라우터는 답하지 않는다. 범위 밖 사유(reason)만 사용자에게 보일 수 있는 한 문장으로 쓴다.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_MODEL = "gpt-6-luna"
INTENT_TOOL = {"ACTION": "action_search", "DISEASE": "disease_search", "LEGAL": "legal_search",
               "CROSSDOC": "crossdoc_graph_search"}
MAX_PARTS = 3

ROUTER_PROMPT = """너는 학교 감염병 대응 안내 서비스의 질문 분류기다. 질문에 답하지 말고 분류만 한다.
서비스 자료: 교육부 학생 감염병 예방·위기대응 매뉴얼, 경기도 학교 감염병 대응 가이드북(학교 업무·역할·단계·질병별 등교중지 표),
질병관리청 질병별 지침(홍역·인플루엔자·코로나19·수족구 등), 감염병예방법.

response_type
- DOMAIN: 학교(교직원) 입장에서 감염병 대응·예방에 관한 질문. 일상 말투여도 학교 상황이면 DOMAIN이다
  (예: "오늘 열나는 애가 3명이에요. 뭐부터 하죠?", "학부모가 등교시켜도 되냐고 물어요").
- GENERAL: 인사, 서비스 소개·사용법 질문.
- OUT_OF_SCOPE: 학교 감염병 대응과 무관한 질문(잡담, 메뉴 추천), 특정 학생의 질병을 진단·판정해 달라는 요청,
  처방·투약 같은 개인 의료 상담, 예방접종 받을 장소 같은 개인 생활 정보, 시스템 입력(NEIS 등)을 대신 해 달라는 요청.

DOMAIN이면 parts에 질문을 의도별로 나눈다(중요한 순서, 최대 3개, 같은 의도는 한 번만).
- ACTION: 학교·교직원이 할 일, 담당 역할, 대응 단계, 교육청 보고·NEIS 등록, 출결·서류, 가정통신문·학부모 안내,
  관찰실·소독·급식, 유행 기준·종료, 질병별 격리·등교중지 기간과 등교 가능 여부(학교 문서의 질병별 표로 답한다),
  그리고 학교·학교장이 보건소·교육청에 신고·보고하는 일(누가 언제 무엇을 신고하는지, 신고 대상 질병 목록, 신고 공문).
  학교 업무 질문은 병명이 없어도 ACTION이다.
- DISEASE: 특정 질병 자체의 사실 — 잠복기, 전파 경로, 증상, 합병증, 전염 기간, 질병 간 증상 비교.
- LEGAL: 법 조항과 법적 의무의 근거, 과태료·처벌·불이익, 법정감염병 분류(급수)처럼 **법을 직접 묻는** 질문.
  질문에 법·법령·법적·감염병예방법·과태료·처벌이 들어 있으면 LEGAL이다.
  학교의 신고 의무만 묻고 법 조항은 말하지 않는 질문(예: "백일해 환자가 나오면 학교가 보건소에 알려야 하나요?",
  "병원에서 먼저 신고했으면 학교장은 따로 안 해도 되나요?")는 학교 문서가 신고 대상 목록과 학교장의 신고 행동을 설명하므로
  parts를 [ACTION, LEGAL] 순서로 둔다(학교 문서 먼저, 법적 확정은 법령 확인). 이때 LEGAL part의 question은 법적 의무만 묻게 쓴다.
  법·처벌을 직접 묻는 질문은 LEGAL 하나만 둔다.
  신고 공문·NEIS 보고서를 어떻게 작성·발송하는지처럼 **절차·방법**만 묻는 질문은 법적 의무를 묻는 것이 아니므로 ACTION 하나만 둔다(LEGAL을 더하지 않는다).
- CROSSDOC: 교육부 매뉴얼과 경기도 가이드북(두 문서)이 같은지·다른지·각각 어떻게 설명하는지 비교.
  두 질병을 비교하는 것은 CROSSDOC이 아니라 DISEASE다. 학교 업무를 묻고 "두 문서가 같은가요?"처럼 문서 비교를 함께 물으면
  CROSSDOC 하나로 둔다(CROSSDOC이 문서별 행동·담당을 함께 가져오므로 ACTION을 따로 더하지 않는다).
한 질문에 여러 가지를 함께 물을 때만 parts를 여러 개로 만든다. 학교 업무 질문 안에 병명이 나온다는 이유만으로 DISEASE를 더하지 않는다.

각 part의 question은 그 의도만 묻는 독립된 한국어 질문으로 쓴다. 원 질문의 병명·역할·상황·지역을 유지하고,
사용자가 묻지 않은 내용을 더하지 않는다. 일상어는 지침 문서에 쓰이는 용어로 바꿔 쓴다
(예: '옮아요'→'전파 경로', '며칠 쉬어야'→'등교중지 기간', '독감'→'인플루엔자'). 뜻은 바꾸지 않는다.
reason은 분류 이유를 한 문장으로 쓴다. OUT_OF_SCOPE이면 사용자에게 그대로 보여 줄 수 있게 존댓말로 쓴다."""


class Part(BaseModel):
    intent: Literal["ACTION", "DISEASE", "LEGAL", "CROSSDOC"]
    question: str = Field(description="이 의도만 묻는 독립된 한국어 질문")


class RouteDecision(BaseModel):
    response_type: Literal["DOMAIN", "GENERAL", "OUT_OF_SCOPE"]
    parts: list[Part] = Field(default_factory=list, description="DOMAIN일 때 의도별 하위 질문(중요한 순서)")
    reason: str = ""


def make_llm():
    from langchain_openai import ChatOpenAI

    model = os.getenv("SCHOOL_ROUTER_MODEL", DEFAULT_MODEL)
    extra = {"reasoning_effort": "none"} if model == "gpt-6-luna" else {}
    return ChatOpenAI(model=model, temperature=0, timeout=15, max_retries=1, **extra)


def decision_to_route(decision: RouteDecision, rule: dict) -> dict:
    """LLM 결정 → classify_question과 같은 모양 + sub_questions(Tool 이름 → 하위 질문). 어떤 Tool에 하위 질문을
    넘길지는 메인 그래프(use_sub_question)가 정한다."""
    if decision.response_type != "DOMAIN":
        return {"response_type": decision.response_type, "reason": decision.reason.strip() or rule.get("reason", ""),
                "confidence": "llm"}
    parts, seen = [], set()
    for part in decision.parts:
        question = part.question.strip()
        if part.intent not in seen and question:
            seen.add(part.intent)
            parts.append((part.intent, question))
    parts = parts[:MAX_PARTS]
    if not parts:
        raise ValueError("DOMAIN인데 의도가 없다")
    sub_questions = {INTENT_TOOL[intent]: question for intent, question in parts}
    return {"response_type": "DOMAIN", "intent": parts[0][0], "secondary": [i for i, _ in parts[1:]],
            "diseases": rule.get("diseases", []), "roles": rule.get("roles", []), "confidence": "llm",
            "reason": decision.reason.strip(), "sub_questions": sub_questions}


def make_router(llm=None) -> Callable[[str, dict], dict]:
    """router(question, rule_result) → 분류 결과. 실패하면 예외를 올린다(호출자가 규칙 결과로 되돌린다)."""
    structured = (llm or make_llm()).with_structured_output(RouteDecision)

    def router(question: str, rule: dict) -> dict:
        raw = structured.invoke([("system", ROUTER_PROMPT), ("user", question)])
        decision = raw if isinstance(raw, RouteDecision) else RouteDecision.model_validate(raw)
        return decision_to_route(decision, rule)

    return router
