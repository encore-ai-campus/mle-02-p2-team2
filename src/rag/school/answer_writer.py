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
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable

from index.school.dictionaries import compare_key

EXCERPT_CHARS = 700
DEFAULT_MODEL = "gpt-6-luna"
REF_RE = re.compile(r"\[(\d+)\]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
STRONG_WORDS = ("반드시", "의무", "법적", "법령상", "하여야", "필수", "신고")   # 신고 주체 오귀속도 강도처럼 막는다
# 원문에서 같은 사람을 가리키는 다른 표기(검사에서만 같게 본다. 답변 문구는 바꾸지 않는다).
ACTOR_EQUIV = {"부모님": "보호자", "부모": "보호자", "학부모": "보호자",
               "교육(지원)청": "교육청", "교육지원청": "교육청", "시도 교육청": "교육청"}
EXAMPLE_NOTE = "※ 담당은 원문이 제시한 예시입니다. 학교 상황에 따라 조정할 수 있습니다."
SYSTEM_PROMPT = """너는 학교 감염병 대응 안내 서비스의 답변 작성자다. 교사·보건교사가 바로 행동할 수 있게 쓴다.
규칙:
1. 아래 [근거]에 있는 내용만 쓴다. 근거에 없는 사실·숫자·기관·역할·기간을 더하지 않는다. 일반 상식으로 보충하지 않는다.
2. 내용이 있는 모든 줄 끝에 그 내용의 근거 번호를 [n] 형식으로 붙인다(여러 개면 [1][3]). 근거 번호는 [근거]의 번호만 쓴다.
3. 첫 줄은 질문에 대한 직접적인 답 1~2문장이다(예: 기간을 물으면 기간부터). 그다음 필요하면 담당·시점별로 '**담당 이름**' 소제목과 '- ' 목록으로 정리한다.
   담당 이름 소제목 아래에는 근거에서 그 담당이 하는 행동만 둔다. 담당이 다르거나 적혀 있지 않은 행동은 다른 소제목(예: '**담당이 적혀 있지 않은 일**')에 둔다.
4. 근거의 조건·시점·예외를 빼지 않는다. 문서마다 내용이 다르면 문서 이름(교육부 매뉴얼, 경기도 가이드북 등)을 밝혀 구분한다.
5. '예시' 담당을 의무처럼 쓰지 않는다. 근거에 없는 '반드시', '의무', '법적' 같은 강한 표현을 쓰지 않는다.
6. 쪽수·청크 ID·내부 용어(Finding, Tool 등)를 쓰지 않는다. 출처 표시는 화면이 따로 한다.
7. 질문과 관계없는 근거는 쓰지 않아도 된다. 같은 내용은 한 번만 쓴다. 전체 12줄 이내, 존댓말(~합니다/~하세요).
8. 근거만으로 질문의 일부에 답할 수 없으면 마지막 줄에 '※ 확인하지 못한 부분: …'으로 짧게 적는다(이 줄은 번호 없이 쓴다)."""


# ---------------------------------------------------------------- 근거 묶음

def evidence_blocks(output: dict) -> list[dict]:
    """citations 순서대로 [n] 근거. 각 근거 = 문서 이름 + 그 청크를 쓴 Finding claim + 원문 발췌."""
    from rag.school.main_graph import DOC_TITLES

    blocks = []
    for number, citation in enumerate(output["citations"], 1):
        claims = [f["claim"] for f in output["findings"] if citation["chunk_id"] in f["chunk_ids"]]
        excerpt = re.sub(r"[-]", " ", citation["excerpt"])
        excerpt = re.sub(r"[ \t]+", " ", excerpt).strip()
        blocks.append({"n": number, "document": DOC_TITLES.get(citation["document_id"], (citation["document_id"],))[0],
                       "claims": claims, "excerpt": excerpt[:EXCERPT_CHARS]})
    return blocks


def evidence_prompt(question: str, output: dict, blocks: list[dict]) -> str:
    parts = [f"[질문]\n{question}\n", "[근거]"]
    for block in blocks:
        claims = "\n".join(f"  · {c}" for c in block["claims"])
        parts.append(f"[{block['n']}] {block['document']}\n  정리된 내용:\n{claims}\n  원문 발췌: {block['excerpt']}")
    unresolved = [w for w in output["warnings"] if w.startswith("[미해결]")]
    if output["status"] == "PARTIAL" and unresolved:
        parts.append("\n[확인하지 못한 부분(검색 도구 보고)]\n" + "\n".join(f"- {w}" for w in unresolved[:3]))
    return "\n".join(parts)


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


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(REF_RE.sub(" ", text))}


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


def check_answer(text: str, blocks: list[dict], books: dict, notes: str = "") -> list[str]:
    """문제 목록(빈 목록 = 통과). notes는 '※'(확인하지 못한 부분) 줄이 기댈 수 있는 글(질문·미해결 경고)."""
    problems = []
    terms = actor_terms(books)
    source = {b["n"]: " ".join([b["document"], *b["claims"], b["excerpt"]]) for b in blocks}
    content_lines = 0
    heading_actors: set[str] = set()     # 직전 소제목이 가리키는 담당(그 아래 줄의 근거에 있어야 한다)
    lines = [part.strip() for raw in text.splitlines() for part in re.split(r"(?=※)", raw)]
    for line in lines:
        if not line:
            continue
        bare = re.sub(r"[*#\-\s·:]", "", REF_RE.sub("", line))
        refs = [int(n) for n in REF_RE.findall(line)]
        if line.startswith("※"):
            evidence = notes
        elif not refs:
            # 소제목은 '**담임교사**' 또는 '담임교사:'처럼 표시가 있는 짧은 줄만 인정한다(짧은 문장을 빠뜨리지 않게).
            if re.fullmatch(r"\*\*[^*\[\]]{1,30}\*\*:?|[^*\[\].。]{1,20}:", line.strip("- ")):
                heading_actors = _actors(line, terms)
                if heading_actors - _actors(" ".join(source.values()), terms):
                    problems.append(f"소제목 주체가 근거에 없음: {line}")
                continue
            if bare:
                problems.append(f"인용 번호 없는 줄: {line[:60]}")
            continue
        else:
            bad = [n for n in refs if n not in source]
            if bad:
                problems.append(f"없는 근거 번호 {bad}: {line[:60]}")
                continue
            evidence = " ".join(source[n] for n in refs)
            content_lines += 1
        missing_numbers = _numbers(line) - _numbers(evidence)
        if missing_numbers:
            problems.append(f"근거에 없는 숫자 {sorted(missing_numbers)}: {line[:60]}")
        evidence_actors = _actors(evidence, terms)
        line_actors = _actors(line, terms) | (heading_actors if not line.startswith("※") else set())
        missing_actors = [a for a in line_actors if a not in evidence_actors]
        if missing_actors:
            problems.append(f"근거에 없는 주체 {missing_actors}: {line[:60]}")
        strong = [w for w in STRONG_WORDS if w in line and w not in evidence]
        if strong:
            problems.append(f"근거보다 강한 표현 {strong}: {line[:60]}")
    if content_lines == 0:
        problems.append("근거를 인용한 줄이 없음")
    return problems


# ---------------------------------------------------------------- 작성기

def make_llm():
    from langchain_openai import ChatOpenAI

    model = os.getenv("SCHOOL_ANSWER_MODEL", DEFAULT_MODEL)
    extra = {"reasoning_effort": "none"} if model == "gpt-6-luna" else {}
    return ChatOpenAI(model=model, temperature=0, timeout=40, max_retries=1, **extra)


def make_answer_writer(llm=None) -> Callable[[str, dict, dict], dict]:
    """writer(question, output, books) → {'answer': str|None, 'problems': [...], 'draft': str}."""
    llm = llm or make_llm()

    def writer(question: str, output: dict, books: dict) -> dict:
        blocks = evidence_blocks(output)
        if not blocks:
            return {"answer": None, "problems": ["근거 없음"], "draft": ""}
        response = llm.invoke([("system", SYSTEM_PROMPT), ("user", evidence_prompt(question, output, blocks))])
        draft = response.content if isinstance(response.content, str) else str(response.content)
        draft = draft.strip()
        notes = " ".join([question, *(w for w in output["warnings"] if w.startswith("[미해결]"))])
        problems = check_answer(draft, blocks, books, notes)
        if problems:
            return {"answer": None, "problems": problems, "draft": draft}
        tail = []
        if any("담당 예시" in f["claim"] or "(EXAMPLE)" in f["claim"] for f in output["findings"]):
            tail.append(EXAMPLE_NOTE)
        return {"answer": "\n".join([draft, "", *tail]).strip(), "problems": [], "draft": draft}

    return writer
