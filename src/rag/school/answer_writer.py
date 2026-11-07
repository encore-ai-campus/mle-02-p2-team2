"""답변 다듬기(11/07) — 검증된 Finding·Citation을 '서비스 답변' 문장으로 다시 쓰고, 코드로 검사한다.

원칙
- 다시 쓰는 것은 QueryOutput.answer 문자열뿐이다. findings·citations·status는 바꾸지 않는다.
- LLM에는 이번 답변의 근거([n] = citations의 n번째)만 준다. 모든 내용 줄은 [n]으로 끝나야 한다.
- 결정적 검사(check_answer)에 하나라도 걸리면 쓰지 않고 템플릿 답변(compose_text)으로 되돌린다.
  · 인용 번호: 내용 줄마다 1개 이상, 범위 안.
  · 숫자: 그 줄이 인용한 근거 글에 같은 숫자가 있어야 한다(기간·횟수·단계 날조 방지).
  · 주체: 역할·기관 이름(역할 사전 이름·별칭, 제외/미등록 표기)은 그 줄이 인용한 근거에 있어야 한다(오귀속 방지).
  · 강도: '반드시'·'의무'·'법적' 등 강한 표현은 인용한 근거에 같은 표현이 있을 때만 쓴다(예시를 의무로 바꾸지 않음).
- 역할 예시·확인 못 한 부분 안내는 코드가 붙인다(LLM이 쓰지 않는다).
- 11/07 ②: 줄 단위로 검사한다. 통과한 줄만 내보내고(on_line으로 스트리밍), 걸린 줄은 뺀다.
  첫 내용 줄(직접 답)이 걸리거나, 통과한 내용 줄이 없거나, 뺀 줄이 남은 줄보다 많으면 템플릿으로 되돌린다.
  다시 쓰게 하지 않는다(질문당 LLM 호출 고정). 재순위가 뽑은 핵심 구절(원문 대조 통과)을 프롬프트에 함께 준다.
- 11/07 ③(줄 단위 판독: 근거 없는 줄 0/128, 검사 차단 6건 모두 오탐, 맥락 누락 7/128):
  · 오탐 제거: '확인되지 않았다'류 문장과 '※ 확인하지 못한 부분' 줄은 주체·강도 검사를 하지 않는다(주장이 아니다).
    담당 동의어(학교의 장=학교장, 근거의 행정지원팀은 행정실로도 인정)를 더한다.
  · 소제목 담당이 그 줄의 근거에 없으면 줄을 지우지 않고 중립 소제목(NEUTRAL_HEADING) 아래로 옮긴다.
  · 맥락 표시: 모의훈련 시나리오 근거, 질문과 다른 단계에 배치된 근거, 원문 조건('격리 해제 후' 등)이 빠진 줄에는
    코드가 표시를 덧붙인다(줄을 지우거나 LLM에 다시 쓰게 하지 않는다).
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable

from index.school.dictionaries import compare_key
from rag.school.rerank import compact

EXCERPT_CHARS = 700
DEFAULT_MODEL = "gpt-6-luna"
REF_RE = re.compile(r"\[(\d+)\]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
STRONG_WORDS = ("반드시", "의무", "법적", "법령상", "하여야", "필수", "신고")   # 신고 주체 오귀속도 강도처럼 막는다
# 원문에서 같은 사람을 가리키는 다른 표기(검사에서만 같게 본다. 답변 문구는 바꾸지 않는다).
ACTOR_EQUIV = {"부모님": "보호자", "부모": "보호자", "학부모": "보호자",
               "교육(지원)청": "교육청", "교육지원청": "교육청", "시도 교육청": "교육청",
               "학교의 장": "학교장"}
# 근거에 앞 표기가 있으면 뒤 담당도 근거에 있는 것으로 본다(행정지원팀은 행정실 직원이 맡는 팀, 11/07 S-R02).
EVIDENCE_IMPLIES = {"행정지원팀": ("행정실",),
                    # 법 제12조①의 학교 신고의무자. 교육부 매뉴얼 59쪽이 이 조문을 학교장 신고의 법적 근거로 든다.
                    "관리인, 경영자 또는 대표자": ("학교장",)}
NEUTRAL_HEADING = "**담당이 적혀 있지 않은 내용**"
SCENARIO_MARK = "모의훈련 시나리오 예시"
SHORT_MARKS = {SCENARIO_MARK: "시나리오 예시"}          # 답변 줄에 붙이는 짧은 표기
# '확인되지 않았다'처럼 근거가 없음을 밝히는 문장(주장이 아니라 주체·강도 검사를 하지 않는다).
UNCONFIRMED_RE = re.compile(r"확인되지\s*않|확인하지\s*못|확인할\s*수\s*없|알\s*수\s*없|나와\s*있지\s*않|"
                            r"명시되어\s*있지\s*않|적혀\s*있지\s*않|단정할\s*수\s*없|단정하기\s*어렵")
STAGE_RE = re.compile(r"대응\s*제\s*(\d)\s*단계")
LIST_NUMBER_RE = re.compile(r"^\s*(?:[-*]\s*)?\d{1,2}\.\s+")       # '1. ' 번호 목록 머리
# 첫 줄의 예/아니요 결론어. 시나리오 근거만 인용한 줄에서는 지운다(본문 지침과 다를 수 있다, 11/07 S-T03).
VERDICT_RE = re.compile(r"^(?:네|예|아니요|아니오)\s*[.,!]\s*")
# 원문의 시간 순서 조건 구절('격리 해제 후', '유행 종료 이후'). '~경우·~때·~시'는 답변이 '~면'으로 옮기는 일이 많아
# 글자 대조로는 오탐이 많았다(11/07 재생: 12건 중 11건 오탐). 그래서 순서 표지만 본다.
CONDITION_RE = re.compile(r"((?:[가-힣]+\s+)?[가-힣]{2,})\s*(이후|후)(?:에는|에도|에)?(?=[\s,;.)]|$)")
SEGMENT_SPLIT_RE = re.compile(r"<br\s*/?>|[;|∙•·\n]|\s-\s|\d\)\s|(?<=[.。])\s")
EXAMPLE_NOTE = "※ 담당은 원문이 제시한 예시입니다. 학교 상황에 따라 조정할 수 있습니다."
SYSTEM_PROMPT = """너는 학교 감염병 대응 안내 서비스의 답변 작성자다. 질문한 교직원이 지금 무엇을 해야 하는지 바로 알 수 있게,
결론부터 판단과 할 일을 정리해 쓴다(11/07 답변 품질 개선: 근거 나열이 아니라 근거로 판단해 주는 답변).

근거 원칙
1. [근거]에 있는 내용만 쓴다. 근거에 없는 사실·숫자·기관·역할·기간·금액을 더하지 않는다. 일반 상식으로 보충하지 않는다.
2. 단, [질문 상황]의 사실(질병·환자 수·범위·질문자 역할)을 근거의 규칙에 대입해 결론을 내는 것은 해야 한다(적용 판단).
   예: 근거가 '신고 대상: 콜레라 … 홍역, 결핵'이고 질문이 코로나19면 "코로나19는 이 목록에 없습니다"라고 쓴다.
   예: 근거가 '동일 학급 2명 이상이면 유행의심 → 대응 제3단계'이고 질문이 '한 반에 여러 명'이면 그 기준에 해당할 수 있다고 쓴다.
   [확인 항목]에 코드가 원문과 대조한 사실이 있으면 그 결과를 그대로 쓴다(뒤집지 않는다). [확인 항목]이나 근거로 답한 내용은
   '확인하지 못한 부분'에 다시 쓰지 않는다. [확인하지 못한 부분(검색 결과 보고)]이 [확인 항목]·근거와 어긋나면 근거를 따른다.
   '판단 기준: 역할 예시: ○○ 대응 제N단계' 근거는 그 역할이 그 단계에서 할 일 표다. 질문자(또는 질문 속 역할)의 할 일은
   이 표의 항목을 '지금 바로 할 일'에 번호 목록으로 옮긴다(원문 순서·표현을 살리고, 질문과 무관한 항목은 뺀다).
   벌칙 원문이 있으면 금액과 적용 대상(원문 표현 그대로, 예: '제12조제1항에 따른 신고를 게을리한 자')을 쓴다.
   질문에 판단할 사실이 빠졌으면(예: 5명이 같은 반인지) 단정하지 말고 "같은 반이면 …, 아니면 …"으로 나눠 쓴다.
3. 내용이 있는 모든 줄 끝에 그 줄의 근거 번호를 [n]으로 붙인다(여러 개면 [1][3]). 적용 판단 줄에는 규칙 근거의 번호를 붙인다.
   줄에 담당 이름(담임교사·학교장 등)을 쓰면 그 담당이 적힌 근거(역할 표 등)의 번호도 함께 붙인다.
   '문서 성격'이 적힌 근거는 그 대상에게만 해당한다(예: 의료기관의 신고 기준을 학교가 할 일로 쓰지 않는다).
4. 신고·보고 주체를 바꾸지 않는다. 근거가 학교장(학교의 장)·의사가 신고한다고 하면 질문자가 직접 신고한다고 쓰지 않는다.
   질문자가 신고의무자가 아니면 그렇게 밝히고, 질문자가 할 일(보건교사·학교장에게 알리기 등 근거에 있는 행동)을 쓴다.
5. 벌칙·금액·기간 숫자는 근거 그대로 쓰고, 누구에게 적용되는지(근거의 대상)를 함께 쓴다. 질문자 개인에게 적용된다는 근거가 없으면
   그렇게 단정하지 않는다.
6. 근거의 조건·시점·예외를 빼지 않는다. 문서마다 내용이 다르면 문서 이름(교육부 매뉴얼, 경기도 가이드북, 질병관리청 지침, 감염병예방법)을 밝힌다.
7. '예시' 담당을 의무처럼 쓰지 않는다. 근거에 없는 '반드시', '의무', '법적' 같은 강한 표현을 쓰지 않는다.
8. 근거에 '표시'(시나리오 예시·원문 단계)가 있으면 그 근거를 쓴 줄에 밝힌다. 시나리오 예시를 일반 지침처럼 쓰지 않는다.
   예/아니요는 '표시'가 없는 본문 지침·법령 근거로 정한다. 근거가 말하는 대상·범위를 넓히지 않는다.
9. 근거의 문서 이름 뒤 괄호는 판본이다. 질병관리청 질병별 지침(2026)은 매년 개정되는 최신 기준이고, 교육부 매뉴얼(2023)·
   경기도 가이드북(2024)은 그보다 오래됐다. 같은 항목(등교중지·복귀 기준, 전염기간, 잠복기, 접촉자 관리 등)을 두 쪽이 다르게
   말하면 결론(첫 줄)은 질병관리청 2026 지침 기준으로 쓰고 그 번호를 붙인다. 학교 문서 기준은 지우지 말고 **상황 판단**에
   "교육부 매뉴얼(2023)은 '…'로 적고 있습니다"처럼 함께 쓰고, '판단 기준: 지침 우선 적용' 근거가 있으면 그 번호를 붙여
   학교 문서도 방역당국 지침을 우선 적용하도록 한다고 밝힌다. 두 쪽이 같은 말을 하면 차이를 만들어 쓰지 않는다.
   질병관리청 지침에만 있는 내용(학교 문서에 없는 기준)도 그대로 답에 쓴다.

형식(마크다운, 존댓말, 전체 24줄 이내)
- 첫 줄: 굵은 글씨의 결론 1~2문장(**…**[n]). 질문에 바로 답한다(예/아니요, 누가, 무엇을). 인사·공감 문장으로 시작하지 않는다.
- 그다음, 아래 소제목 중 필요한 것만 이 순서로 쓴다. 소제목은 글자 그대로 쓰고(괄호·담당 이름을 붙이지 않는다) 소제목 줄에는 번호를 붙이지 않는다.
  **상황 판단** — 신고 대상 여부, 법정감염병 급수, 유행의심·대응 단계 판단(적용 판단 줄). '- ' 목록.
  **지금 바로 할 일** — 질문자(역할을 밝혔으면 그 역할, 아니면 학교)가 할 일을 시간 순서로. '1. ' 번호 목록. 줄마다 누가 하는지 쓴다.
    근거에 담당이 적혀 있지 않으면 '학교는'처럼 주어를 지어 붙이지 말고 원문 표현대로 쓴다. 보건소·의료기관이 하는 조치
    (예방접종·면역글로불린 투여·역학조사)는 학교가 하는 일로 쓰지 않는다.
  **이후에 할 일** — 상황이 이어질 때 할 일(진료 결과 확인, 등교중지·복귀, 추가 환자 감시 등). '- ' 목록.
  **학교 내 다른 담당의 역할** — 질문자가 아닌 담당(보건교사·학교장 등)이 할 일. '- ' 목록. 담당 이름을 줄 안에 쓴다.
  **신고·법적 사항** — 신고 주체·방법·기한, 벌칙(근거가 있을 때만). '- ' 목록.
- 질문이 단순하면(예: 잠복기, 서류) 결론과 소제목 한두 개면 충분하다. 같은 내용을 되풀이하지 않는다.
- 근거로 답할 수 없는 부분은 맨 끝에 '※ 확인하지 못한 부분: …' 한 줄로 쓴다(번호 없이). 학교가 확인할 곳(보건소 등)이 근거에 있으면 덧붙인다.
- 쪽수·청크 ID·내부 용어(Finding, Tool, 카드)를 쓰지 않는다. 출처는 [n]으로만 표시한다.
- 근거에 '핵심 구절'이 있으면 그 표현을 살린다. 한 줄에는 한 가지 내용만 쓴다."""


# ---------------------------------------------------------------- 근거 묶음

def context_marks(claims: list[str], scenario: bool) -> list[str]:
    """근거 표시: 모의훈련 시나리오, 질문과 다른 단계(그 청크의 Finding이 모두 '질문 단계와 다름'일 때)."""
    marks = [SCENARIO_MARK] if scenario or any("모의훈련 시나리오" in c for c in claims) else []
    if claims and all("질문 단계와 다름" in c for c in claims):
        stages = {m.group(1) for c in claims if (m := STAGE_RE.search(c))}
        if len(stages) == 1:
            marks.append(f"원문: 대응 제{stages.pop()}단계")
    return marks


def evidence_blocks(output: dict, picks: dict | None = None, scenario: set[str] | None = None) -> list[dict]:
    """citations 순서대로 [n] 근거. 각 근거 = 문서 이름 + 그 청크를 쓴 Finding claim + 원문 발췌
    (+ 재순위가 고른 근거면 역할과 원문 대조를 통과한 핵심 구절, + 시나리오·다른 단계 표시).
    scenario: 모의훈련 시나리오 청크 ID(메인 그래프가 DB section으로 확인한다. 없으면 claim 표기로만 판단)."""
    from rag.school.main_graph import DOC_EDITIONS, DOC_TITLES

    blocks = []
    for number, citation in enumerate(output["citations"], 1):
        claims = [f["claim"] for f in output["findings"] if citation["chunk_id"] in f["chunk_ids"]]
        excerpt = re.sub(r"[-]", " ", citation["excerpt"])
        excerpt = re.sub(r"[ \t]+", " ", excerpt).strip()
        blocks.append({"n": number, "chunk_id": citation["chunk_id"],
                       "document": DOC_TITLES.get(citation["document_id"], (citation["document_id"],))[0],
                       # 판본은 프롬프트 표시에만 쓴다(줄 검사 원문에 넣으면 '제3차·2023' 숫자가 근거 숫자로 잡힌다).
                       "edition": DOC_EDITIONS.get(citation["document_id"], ""),
                       "claims": claims, "excerpt": excerpt[:EXCERPT_CHARS],
                       "role": (picks or {}).get(citation["chunk_id"], {}).get("role"),
                       "quotes": (picks or {}).get(citation["chunk_id"], {}).get("quotes", []),
                       "marks": context_marks(claims, citation["chunk_id"] in (scenario or set()))})
    return blocks


ROLE_NAMES = {"DIRECT": "직접 답", "CONDITION": "조건·예외", "PREMISE": "질문 전제 확인", "RULE": "판단 기준",
              "ROLE": "역할 표 — 질문자(질문 속 역할)의 '지금 바로 할 일'에 쓸 것"}
# 문서의 대상(누가 쓰는 문서인지). 의료기관용 신고 기준을 학교의 신고로 옮기지 않게 한다(11/07 데모 D07).
DOC_NOTES = {"kdca_diagnosis_reporting_2026": "의사·의료기관이 감염병을 진단·신고하는 기준(학교의 신고 절차가 아님)",
             # 11/08: '보건소·의료기관용'만 적으면 작성기가 등교·집단생활 제한 기준과 질병 정보까지 학교에 쓰지 않았다.
             "kdca_covid19_2026": ("보건소·의료기관용 관리지침. 질병 정보(잠복기·전염기간)와 등교·출근 제한 권고는 학교에도 "
                                   "적용되는 최신 기준이고, 역학조사·신고·검사는 보건소·의료기관의 업무"),
             "kdca_respiratory_2026": ("보건소·의료기관용 관리지침. 질병 정보(잠복기·전염기간)와 등교·집단생활 제한 권고는 학교에도 "
                                       "적용되는 최신 기준이고, 역학조사·신고·검사는 보건소·의료기관의 업무")}
SITUATION_NAMES = {"disease": "질병", "status": "확진/의심", "count": "환자 수", "scope": "발생 범위",
                   "asker_role": "질문자 역할", "asks": "묻는 것"}


def evidence_prompt(question: str, output: dict, blocks: list[dict], context: dict | None = None) -> str:
    parts = [f"[질문]\n{question}\n"]
    situation = {k: v for k, v in ((context or {}).get("situation") or {}).items() if v not in ("", None, [], "모름")}
    if situation:
        parts.append("[질문 상황]\n" + "\n".join(f"- {SITUATION_NAMES.get(k, k)}: {', '.join(v) if isinstance(v, list) else v}"
                                                for k, v in situation.items()) + "\n")
    numbers = {b["chunk_id"]: b["n"] for b in blocks if b.get("chunk_id")}
    facts = [f"- {f['text']} [{numbers[f['chunk_id']]}]" for f in (context or {}).get("facts", [])
             if f.get("chunk_id") in numbers]
    if facts:
        parts.append("[확인 항목 — 코드가 원문과 글자 대조한 사실]\n" + "\n".join(facts) + "\n")
    parts.append("[근거]")
    for block in blocks:
        claims = "\n".join(f"  · {c}" for c in block["claims"])
        edition = f"({block['edition']})" if block.get("edition") else ""
        head = (f"[{block['n']}] {block['document']}{edition}"
                + (f" ({ROLE_NAMES[block['role']]})" if block.get("role") else ""))
        doc_note = DOC_NOTES.get(block.get("chunk_id", "").split(":", 1)[0])
        head += f"\n  문서 성격: {doc_note}" if doc_note else ""
        quotes = "".join(f"\n  핵심 구절: “{q}”" for q in block.get("quotes", []))
        marks = f"\n  표시: {', '.join(block['marks'])}(이 근거를 쓴 줄에 밝힐 것)" if block.get("marks") else ""
        parts.append(f"{head}{marks}\n  정리된 내용:\n{claims}{quotes}\n  원문 발췌: {block['excerpt']}")
    unresolved = [user_note(w) for w in output["warnings"] if w.startswith("[미해결]")]
    if output["status"] == "PARTIAL" and unresolved:
        parts.append("\n[확인하지 못한 부분(검색 결과 보고)]\n" + "\n".join(f"- {w}" for w in unresolved[:3]))
    return "\n".join(parts)


TOOL_WORDS = {"action_search": "학교 대응 자료", "disease_search": "질병 자료", "legal_search": "법령 자료",
              "crossdoc_graph_search": "문서 비교 자료"}


def user_note(warning: str) -> str:
    """'[미해결] …' 내부 경고 → 사용자에게 보여 줄 수 있는 문장(Tool 이름·내부 용어 제거)."""
    text = warning.removeprefix("[미해결]").strip()
    text = text.removeprefix("근거에서 확인하지 못한 부분:").removeprefix("근거 선별:").strip()
    match = re.match(r"(\w+?)(?:를|가) (호출했지만 근거를 찾지 못했다|연결되지 않아 확인하지 못했다)", text)
    if match and match.group(1) in TOOL_WORDS:
        return f"{TOOL_WORDS[match.group(1)]}에서 관련 근거를 찾지 못했습니다."
    if text.startswith("호출 한도"):
        return "일부 자료는 확인하지 못했습니다."
    if text.startswith("질문의 주 의도"):
        return "질문의 핵심 부분에 직접 답하는 근거를 찾지 못했습니다."
    for name, word in TOOL_WORDS.items():
        text = text.replace(name, word)
    return text


# ---------------------------------------------------------------- 결정적 검사

# 다른 낱말 안에서도 흔히 나오는 두 글자 별칭(보건실·교과서·상담실·교무실). 검사 대상에서 뺀다.
AMBIGUOUS_ALIASES = {"보건", "교과", "상담", "교무"}


def actor_terms(books: dict) -> dict[str, str]:
    """표기 → 대표 이름. 역할 사전 이름·별칭(대표 이름으로 묶음) + 학교 밖 기관(제외 표기) + 미등록 역할 표기 +
    보호자 계열. 긴 표기부터 대조한다(부분 일치 중복 방지)."""
    role = books["role"]
    terms = {}
    for entry in role["entries"]:
        for surface in (entry["name"], *entry.get("aliases", [])):
            if surface not in AMBIGUOUS_ALIASES:
                terms[surface] = entry["name"]
    for group in ("excluded_mentions", "unregistered_mentions"):
        for mention in role.get(group, []):
            terms.setdefault(mention["mention"], mention["mention"])
    for surface in ("질병관리청", "보건복지부", "의료기관", "보호자"):
        terms.setdefault(surface, surface)
    terms.update(ACTOR_EQUIV)
    return dict(sorted(((t, c) for t, c in terms.items() if len(compare_key(t)) >= 2),
                       key=lambda item: -len(compare_key(item[0]))))


NAME_NUMBER_RE = re.compile(r"(?:코로나\s*|COVID-?|covid-?)19")      # 병명 속 숫자(11/07 데모 D01·D12 오탐)
# 문서 판본 표기(11/08): 답변이 출처의 판본을 밝히는 '(2026)'·'(제3차 개정판, 2023)'·'2026년 지침'은 근거 내용 숫자가 아니다.
# 프롬프트가 판본을 밝히라고 하므로 숫자 검사에서 뺀다(빼지 않으면 2026 지침 우선 줄이 모두 걸려 템플릿으로 떨어졌다, C01).
EDITION_RE = re.compile(r"\((?:제\s*3\s*차\s*개정판,?\s*)?20\d\d(?:\.\d{1,2}\.\d{1,2}\.?\s*시행)?\)"
                        r"|20\d\d\s*년?\s*(?:판|도판|지침|관리지침|개정)|제\s*3\s*차\s*개정판")


def _numbers(text: str) -> set[str]:
    text = EDITION_RE.sub(" ", NAME_NUMBER_RE.sub(" ", REF_RE.sub(" ", text)))
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


def _pattern(term: str) -> re.Pattern:
    """낱말 앞 경계(앞 글자가 한글이 아님) + 글자 사이 공백 허용. '학교 감염병'의 '교감', '학교장'의 '교장'을 잡지 않는다."""
    chars = [re.escape(c) for c in compare_key(term)]
    return re.compile(r"(?<![가-힣])" + r"\s*".join(chars))


def _actors(text: str, terms: dict[str, str]) -> set[str]:
    norm = unicodedata.normalize("NFKC", text).casefold()
    found = set()
    for term, canonical in terms.items():
        pattern = _pattern(term)
        if pattern.search(norm):
            found.add(canonical)
            norm = pattern.sub(" ", norm)          # '보건교사' 안의 '교사'를 따로 세지 않는다
    return found


def asserted_text(line: str) -> str:
    """주장하는 부분만: '확인되지 않았습니다'류 문장·절을 뺀다(인용 번호는 지운다)."""
    text = LIST_NUMBER_RE.sub("", REF_RE.sub(" ", line))       # '2. …' 목록 번호는 숫자 검사 대상이 아니다
    clauses = re.split(r"(?<=[.!?])\s+|(?:지만|으나|는데)\s*,?\s+|,?\s*다만\s+", text)
    return " ".join(c for c in clauses if c and not UNCONFIRMED_RE.search(c))


def _evidence_actors(text: str, terms: dict[str, str]) -> set[str]:
    actors = _actors(text, terms)
    for surface, implied in EVIDENCE_IMPLIES.items():
        if surface in text:
            actors.update(implied)
    return actors


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def condition_gaps(line: str, excerpts: list[str]) -> list[str]:
    """줄이 옮긴 원문 구간에 조건 구절('격리 해제 후', '확진된 경우')이 있는데 줄에 없으면 그 구절.
    구간 = 원문을 항목 단위로 나눈 것. 조건을 뺀 구간 글자쌍의 절반 이상이 줄에 있으면 '그 구간을 옮긴 줄'로 본다.
    조건 낱말 앞 두 글자(예: '해제', '확진', '필요')가 줄에 있으면 조건을 살린 것으로 본다."""
    key = compact(line)
    line_pairs = _bigrams(key)
    gaps: list[str] = []
    for excerpt in excerpts:
        for segment in SEGMENT_SPLIT_RE.split(excerpt):
            segment = (segment or "").strip()
            for match in CONDITION_RE.finditer(segment):
                if match.group(1).split()[-1].endswith(("전", "직")):        # '전후', '직후'는 조건이 아니다
                    continue
                stem = compact(match.group(1).split()[-1])[:2]
                rest = compact(segment.replace(match.group(0), " "))
                pairs = _bigrams(rest)
                if stem in key or len(rest) < 6 or not pairs:
                    continue
                if len(pairs & line_pairs) / len(pairs) >= 0.5:
                    phrase = re.sub(r"\s+", " ", match.group(0)).strip()
                    if phrase not in gaps:
                        gaps.append(phrase)
    return gaps


def decorate(line: str, prefixes: list[str], conditions: list[str]) -> str:
    """맥락 표시를 덧붙인다: 목록 기호 뒤에 '(표시) ', 끝 인용 번호 앞에 '(원문 조건: …)'."""
    if conditions:
        tail = REF_RE.search(line) and re.search(r"((?:\[\d+\])+)\s*$", line)
        note = f" (원문 조건: {', '.join(conditions)})"
        line = line[:tail.start()].rstrip() + note + tail.group(1) if tail else line + note
    if prefixes:
        bullet = re.match(r"^(\s*(?:[-*]|\d+\.)\s+)", line)
        head = bullet.group(1) if bullet else ""
        line = head + "".join(f"({p}) " for p in prefixes) + line[len(head):]
    return line


class LineChecker:
    """줄 하나씩 검사한다(스트리밍용). 소제목이 가리키는 담당을 다음 줄 검사에 이어 쓴다.
    check 뒤 self.moved(소제목 담당이 근거에 없어 중립 소제목으로 옮길 줄), self.prefixes·self.conditions(덧붙일 표시)를
    읽는다. 이 셋은 줄을 지우는 사유가 아니다."""

    HEADING_RE = re.compile(r"\*\*[^*\[\]]{1,30}\*\*:?|[^*\[\].。]{1,20}:")

    def __init__(self, blocks: list[dict], books: dict, notes: str = "", question: str = ""):
        self.terms = actor_terms(books)
        self.question_numbers = _numbers(question)   # 질문 속 숫자(예: 5명)는 적용 판단 줄에 다시 써도 된다
        self.blocks = {b["n"]: b for b in blocks}
        # 코드가 원문과 대조해 그 청크에 붙인 사실(facts)도 그 근거의 글로 본다(11/07 데모 D02: '담임교사는 신고의무자 목록에 없다').
        # 근거 선별이 원문과 글자 대조한 핵심 구절(quotes)도 그 근거의 글로 본다(11/08: 청크 본문 일치 구간에서 고른 답 줄이
        # Tool 발췌 밖에 있을 수 있다. 예: 마이코플라스마 전염기간 '증상 발생 후 20일 이내').
        self.source = {b["n"]: " ".join([b["document"], *b["claims"], b["excerpt"], *b.get("facts", []),
                                         *b.get("quotes", [])]) for b in blocks}
        self.source_actors = _evidence_actors(" ".join(self.source.values()), self.terms)
        self.notes = notes
        self.heading_actors: set[str] = set()     # 직전 소제목이 가리키는 담당
        self.content_lines = 0
        self.moved = False
        self.prefixes: list[str] = []
        self.conditions: list[str] = []
        self.strip_verdict = False
        self.has_body_evidence = any(SCENARIO_MARK not in b.get("marks", []) for b in blocks)

    def _marks(self, line: str, refs: list[int]) -> list[str]:
        """인용한 근거가 모두 같은 표시를 가졌고 줄이 그것을 밝히지 않았으면 그 표시."""
        common = set.intersection(*(set(self.blocks[n].get("marks", [])) for n in refs)) if refs else set()
        key = compact(line)
        out = []
        for mark in sorted(common):
            if mark == SCENARIO_MARK and ("시나리오" in line or "모의훈련" in line):
                continue
            stage = STAGE_RE.search(mark)
            if stage and f"제{stage.group(1)}단계" in key:
                continue
            out.append(mark)
        return out

    def check(self, line: str) -> tuple[str, list[str]]:
        """(종류, 문제 목록). 종류: empty·heading·note(※)·content·plain(번호 없는 문장)."""
        self.moved, self.prefixes, self.conditions, self.strip_verdict = False, [], [], False
        line = line.strip()
        if not line:
            return "empty", []
        bare = re.sub(r"[*#\-\s·:]", "", REF_RE.sub("", line))
        refs = [int(n) for n in REF_RE.findall(line)]
        if line.startswith("※") or (not refs and UNCONFIRMED_RE.search(line)):
            # 확인하지 못한 부분 안내('※ …', 번호 없는 '…는 확인되지 않습니다')는 주장이 아니다.
            # 숫자만 질문·미해결·근거 어디엔가 있는지 본다.
            missing_numbers = (_numbers(LIST_NUMBER_RE.sub("", line))
                               - _numbers(" ".join([self.notes, *self.source.values()])))
            return "note", ([f"근거에 없는 숫자 {sorted(missing_numbers)}: {line[:60]}"] if missing_numbers else [])
        if not refs:
            # 소제목은 '**담임교사**' 또는 '담임교사:'처럼 표시가 있는 짧은 줄만 인정한다(짧은 문장을 빠뜨리지 않게).
            if self.HEADING_RE.fullmatch(line.strip("- ")):
                self.heading_actors = _actors(line, self.terms)
                missing = self.heading_actors - self.source_actors
                return "heading", ([f"소제목 주체가 근거에 없음: {line}"] if missing else [])
            return ("plain", [f"인용 번호 없는 줄: {line[:60]}"]) if bare else ("empty", [])
        bad = [n for n in refs if n not in self.source]
        if bad:
            return "content", [f"없는 근거 번호 {bad}: {line[:60]}"]
        evidence = " ".join(self.source[n] for n in refs)
        claim = asserted_text(line)
        problems = []
        missing_numbers = _numbers(claim) - _numbers(evidence) - self.question_numbers
        if missing_numbers:
            problems.append(f"근거에 없는 숫자 {sorted(missing_numbers)}: {line[:60]}")
        evidence_actors = _evidence_actors(evidence, self.terms)
        missing_actors = [a for a in _actors(claim, self.terms) if a not in evidence_actors]
        if missing_actors:
            problems.append(f"근거에 없는 주체 {missing_actors}: {line[:60]}")
        strong = [w for w in STRONG_WORDS if w in claim and w not in evidence]
        if strong:
            problems.append(f"근거보다 강한 표현 {strong}: {line[:60]}")
        if not problems:
            self.strip_verdict = bool(
                self.content_lines == 0 and VERDICT_RE.match(line) and self.has_body_evidence
                and all(SCENARIO_MARK in self.blocks[n].get("marks", []) for n in refs))
            self.content_lines += 1
            self.moved = bool(self.heading_actors - evidence_actors)
            if claim.strip(" .") and not UNCONFIRMED_RE.search(line):     # '확인할 수 없다'는 줄에는 붙이지 않는다
                self.prefixes = self._marks(line, refs)
                self.conditions = condition_gaps(line, [self.blocks[n]["excerpt"] for n in refs])
        return "content", problems


def split_lines(text: str) -> list[str]:
    """줄 + 줄 중간의 '※'에서 나눈다."""
    return [part.strip() for raw in text.splitlines() for part in re.split(r"(?=※)", raw)]


def check_answer(text: str, blocks: list[dict], books: dict, notes: str = "") -> list[str]:
    """답변 전체의 문제 목록(빈 목록 = 통과). 평가·테스트용. 작성기는 LineChecker로 줄마다 판단한다."""
    checker = LineChecker(blocks, books, notes)
    problems, content = [], 0
    for line in split_lines(text):
        kind, found = checker.check(line)
        problems += found
        content += kind == "content"
    if content == 0:
        problems.append("근거를 인용한 줄이 없음")
    return problems


# ---------------------------------------------------------------- 작성기

def make_llm():
    from langchain_openai import ChatOpenAI

    model = os.getenv("SCHOOL_ANSWER_MODEL", DEFAULT_MODEL)
    extra = {"reasoning_effort": "none"} if model == "gpt-6-luna" else {}
    return ChatOpenAI(model=model, temperature=0, timeout=40, max_retries=1, **extra)


PENALTY_ASK_RE = re.compile(r"처벌|벌금|벌칙|과태료|안\s*하면|안\s*했을|불이익|어떻게\s*돼|어떻게\s*되")


def penalty_backstop(question: str, kept: list[str], blocks: list[dict], context: dict | None) -> list[str]:
    """처벌을 물었는데 답변에 벌칙이 없고, 확인 항목이 벌칙 원문을 찾았으면 원문 인용 줄을 덧붙인다(11/07 데모 D02:
    벌칙 원문을 찾고도 LLM이 빠뜨린 경우). 새 문장을 만들지 않고 원문 문장을 그대로 옮긴다."""
    if not PENALTY_ASK_RE.search(question) or any("벌금" in line or "과태료" in line for line in kept):
        return []
    numbers = {b["chunk_id"]: b["n"] for b in blocks}
    for fact in (context or {}).get("facts", []):
        if fact["text"].startswith("벌칙 원문:") and fact.get("chunk_id") in numbers:
            lines = [] if any(line.strip() == "**신고·법적 사항**" for line in kept) else ["**신고·법적 사항**"]
            quote = fact["text"].removeprefix("벌칙 원문:").strip()
            return [*lines, f"- 미신고 벌칙(원문): {quote}[{numbers[fact['chunk_id']]}]"]
    return []


class _FirstLineFailed(Exception):
    """첫 내용 줄(결론)이 검사에 걸렸다. 같은 시도를 더 읽지 않고 다시 쓰게 한다."""


RETRY_NOTE = ("방금 쓴 답변의 첫 줄(결론)이 근거 검사에 걸렸다: {problems}\n"
              "근거에 있는 주체·숫자·표현만 써서 답변 전체를 처음부터 다시 써라. 규칙과 형식은 같다.")


def make_answer_writer(llm=None) -> Callable[..., dict]:
    """writer(question, output, books, picks=None, on_line=None, scenario=None, context=None) →
    {'answer': str|None, 'problems': [...], 'draft': str, 'dropped': [...], 'moved': [...], 'marked': [...], 'attempts': n}.
    on_line(text)는 검사를 통과한 줄을 받는다(화면 스트리밍). 첫 내용 줄이 통과하기 전에는 아무것도 내보내지 않는다.
    첫 내용 줄이 검사에 걸리면 걸린 이유를 알려 한 번만 다시 쓰게 한다(11/07 P0-5, LLM 호출은 실패할 때만 +1).
    그래도 실패하면 answer=None(호출자가 템플릿 답변을 쓴다)."""
    llm = llm or make_llm()

    def attempt(messages: list, question: str, output: dict, books: dict, blocks: list[dict],
                on_line: Callable[[str], None] | None) -> dict:
        notes = " ".join([question, *(w for w in output["warnings"] if w.startswith("[미해결]"))])
        checker = LineChecker(blocks, books, notes, question=question)
        kept: list[str] = []
        held: list[str] = []
        dropped: list[str] = []
        problems: list[str] = []
        draft: list[str] = []
        moved: list[str] = []
        marked: list[str] = []
        # heading: 지금 유효한 소제목(검사 통과), shown: 마지막으로 내보낸 소제목, first: 첫 내용 줄 통과 여부
        state = {"heading": None, "shown": None, "first": None, "number": 0}

        def emit(text: str) -> None:
            if LIST_NUMBER_RE.match(text):          # 번호 목록은 소제목마다 1부터 다시 매긴다(검사에서 뺀 줄이 있어도)
                state["number"] += 1
                text = LIST_NUMBER_RE.sub(f"{state['number']}. ", text, count=1)
            elif text.startswith("**") or not text:
                state["number"] = 0 if text.startswith("**") else state["number"]
            kept.append(text)
            if state["first"] is None:          # 결론 줄이 통과하기 전에는 화면에 내보내지 않는다(다시 쓸 수 있게)
                held.append(text)
            elif on_line:
                on_line(text)

        def feed(raw: str) -> None:
            draft.append(raw)
            for line in split_lines(raw) or [""]:
                kind, found = checker.check(line)
                if kind == "empty":
                    if kept and kept[-1] != "":
                        emit("")
                elif kind == "heading":
                    # 소제목은 아래 내용 줄이 통과할 때 함께 낸다(내용 없는 소제목을 남기지 않는다).
                    # 담당이 근거에 없는 소제목은 내지 않는다. 그 아래 줄은 중립 소제목으로 옮겨진다.
                    state["heading"] = None if found else line
                    dropped.extend([line] if found else [])
                    problems.extend(found)
                elif found:
                    dropped.append(line)
                    problems.extend(found)
                    if kind == "content" and state["first"] is None:
                        state["first"] = False
                        raise _FirstLineFailed
                else:
                    target = NEUTRAL_HEADING if checker.moved else state["heading"]
                    if checker.moved:
                        moved.append(line)
                    if target and target != state["shown"]:
                        if kept and kept[-1] != "":
                            emit("")
                        emit(target)
                        state["shown"] = target
                    # 표시는 줄마다 붙인다(구역당 한 번만 붙이면 뒤 줄이 일반 지침처럼 읽혔다, S-Q04).
                    prefixes = [SHORT_MARKS.get(p, p) for p in checker.prefixes]
                    base = VERDICT_RE.sub("", line, count=1) if checker.strip_verdict else line
                    text = decorate(base, prefixes, checker.conditions) if kind == "content" else line
                    if text != line:
                        marked.append(text)
                    emit(text)
                    if kind == "content" and state["first"] is None:
                        state["first"] = True
                        for held_text in held:
                            if on_line:
                                on_line(held_text)
                        held.clear()

        try:
            if hasattr(llm, "stream"):
                buffer = ""
                for chunk in llm.stream(messages):
                    buffer += chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        feed(line)
                if buffer.strip():
                    feed(buffer)
            else:
                response = llm.invoke(messages)
                content = response.content if isinstance(response.content, str) else str(response.content)
                for line in content.strip().split("\n"):
                    feed(line)
        except _FirstLineFailed:
            pass
        reasons = []
        dropped_content = [d for d in dropped if REF_RE.search(d)]
        if state["first"] is False:
            reasons.append("첫 내용 줄(직접 답)이 검사에 걸림")
        elif checker.content_lines == 0:
            reasons.append("근거를 인용한 줄이 없음")
        elif len(dropped_content) > checker.content_lines:
            reasons.append(f"검사에 걸린 줄({len(dropped_content)})이 통과한 줄({checker.content_lines})보다 많음")
        return {"kept": kept, "reasons": reasons, "problems": problems, "draft": "\n".join(draft).strip(),
                "dropped": dropped, "moved": moved, "marked": marked, "first": state["first"]}

    def writer(question: str, output: dict, books: dict, picks: dict | None = None,
               on_line: Callable[[str], None] | None = None, scenario: set[str] | None = None,
               context: dict | None = None) -> dict:
        blocks = evidence_blocks(output, picks, scenario)
        for block in blocks:
            block["facts"] = [f["text"] for f in (context or {}).get("facts", []) if f.get("chunk_id") == block["chunk_id"]]
        if not blocks:
            return {"answer": None, "problems": ["근거 없음"], "draft": "", "dropped": [], "attempts": 0}
        messages = [("system", SYSTEM_PROMPT), ("user", evidence_prompt(question, output, blocks, context))]
        result = attempt(messages, question, output, books, blocks, on_line)
        attempts = 1
        if result["first"] is False:
            first_problems = "; ".join(result["problems"][:3])
            retry = [*messages, ("assistant", result["draft"]), ("user", RETRY_NOTE.format(problems=first_problems))]
            result = attempt(retry, question, output, books, blocks, on_line)
            result["problems"] = [f"1차 첫 줄 실패: {first_problems}", *result["problems"]]
            attempts = 2
        extra = {"draft": result["draft"], "dropped": result["dropped"], "moved": result["moved"],
                 "marked": result["marked"], "attempts": attempts}
        if result["reasons"]:
            return {"answer": None, "problems": result["reasons"] + result["problems"], **extra}
        kept = result["kept"]
        added = penalty_backstop(question, kept, blocks, context)
        if added:
            kept = [*kept, "", *added]
            extra["added"] = added
            for text in ["", *added]:
                if on_line:
                    on_line(text)
        tail = []
        if any("담당 예시" in f["claim"] or "(EXAMPLE)" in f["claim"] for f in output["findings"]):
            tail.append(EXAMPLE_NOTE)
            if on_line:
                on_line("")
                on_line(EXAMPLE_NOTE)
        answer = "\n".join([*kept, "", *tail]).strip()
        return {"answer": answer, "problems": result["problems"], **extra}

    return writer
