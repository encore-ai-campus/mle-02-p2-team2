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
8. 근거만으로 질문의 일부에 답할 수 없으면 마지막 줄에 '※ 확인하지 못한 부분: …'으로 짧게 적는다(이 줄은 번호 없이 쓴다).
9. 근거가 말하는 대상·범위를 넓히지 않는다(예: 근거가 '사람 간 전파'만 말하면 '어른에게도 옮는다'로 단정하지 않는다).
10. 근거에 '핵심 구절'이 있으면 그 표현을 살려 쓴다. 한 줄에는 한 가지 내용만 쓴다."""


# ---------------------------------------------------------------- 근거 묶음

def evidence_blocks(output: dict, picks: dict | None = None) -> list[dict]:
    """citations 순서대로 [n] 근거. 각 근거 = 문서 이름 + 그 청크를 쓴 Finding claim + 원문 발췌
    (+ 재순위가 고른 근거면 역할과 원문 대조를 통과한 핵심 구절)."""
    from rag.school.main_graph import DOC_TITLES

    blocks = []
    for number, citation in enumerate(output["citations"], 1):
        claims = [f["claim"] for f in output["findings"] if citation["chunk_id"] in f["chunk_ids"]]
        excerpt = re.sub(r"[-]", " ", citation["excerpt"])
        excerpt = re.sub(r"[ \t]+", " ", excerpt).strip()
        blocks.append({"n": number, "document": DOC_TITLES.get(citation["document_id"], (citation["document_id"],))[0],
                       "claims": claims, "excerpt": excerpt[:EXCERPT_CHARS],
                       "role": (picks or {}).get(citation["chunk_id"], {}).get("role"),
                       "quotes": (picks or {}).get(citation["chunk_id"], {}).get("quotes", [])})
    return blocks


ROLE_NAMES = {"DIRECT": "직접 답", "CONDITION": "조건·예외", "PREMISE": "질문 전제 확인"}


def evidence_prompt(question: str, output: dict, blocks: list[dict]) -> str:
    parts = [f"[질문]\n{question}\n", "[근거]"]
    for block in blocks:
        claims = "\n".join(f"  · {c}" for c in block["claims"])
        head = f"[{block['n']}] {block['document']}" + (f" ({ROLE_NAMES[block['role']]})" if block.get("role") else "")
        quotes = "".join(f"\n  핵심 구절: “{q}”" for q in block.get("quotes", []))
        parts.append(f"{head}\n  정리된 내용:\n{claims}{quotes}\n  원문 발췌: {block['excerpt']}")
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


class LineChecker:
    """줄 하나씩 검사한다(스트리밍용). 소제목이 가리키는 담당을 다음 줄 검사에 이어 쓴다."""

    HEADING_RE = re.compile(r"\*\*[^*\[\]]{1,30}\*\*:?|[^*\[\].。]{1,20}:")

    def __init__(self, blocks: list[dict], books: dict, notes: str = ""):
        self.terms = actor_terms(books)
        self.source = {b["n"]: " ".join([b["document"], *b["claims"], b["excerpt"]]) for b in blocks}
        self.source_actors = _actors(" ".join(self.source.values()), self.terms)
        self.notes = notes
        self.heading_actors: set[str] = set()     # 직전 소제목이 가리키는 담당(그 아래 줄의 근거에 있어야 한다)
        self.content_lines = 0

    def check(self, line: str) -> tuple[str, list[str]]:
        """(종류, 문제 목록). 종류: empty·heading·note(※)·content·plain(번호 없는 문장)."""
        line = line.strip()
        if not line:
            return "empty", []
        bare = re.sub(r"[*#\-\s·:]", "", REF_RE.sub("", line))
        refs = [int(n) for n in REF_RE.findall(line)]
        if line.startswith("※"):
            kind, evidence = "note", self.notes
        elif not refs:
            # 소제목은 '**담임교사**' 또는 '담임교사:'처럼 표시가 있는 짧은 줄만 인정한다(짧은 문장을 빠뜨리지 않게).
            if self.HEADING_RE.fullmatch(line.strip("- ")):
                self.heading_actors = _actors(line, self.terms)
                missing = self.heading_actors - self.source_actors
                return "heading", ([f"소제목 주체가 근거에 없음: {line}"] if missing else [])
            return ("plain", [f"인용 번호 없는 줄: {line[:60]}"]) if bare else ("empty", [])
        else:
            bad = [n for n in refs if n not in self.source]
            if bad:
                return "content", [f"없는 근거 번호 {bad}: {line[:60]}"]
            kind, evidence = "content", " ".join(self.source[n] for n in refs)
        problems = []
        missing_numbers = _numbers(line) - _numbers(evidence)
        if missing_numbers:
            problems.append(f"근거에 없는 숫자 {sorted(missing_numbers)}: {line[:60]}")
        evidence_actors = _actors(evidence, self.terms)
        line_actors = _actors(line, self.terms) | (self.heading_actors if kind == "content" else set())
        missing_actors = [a for a in line_actors if a not in evidence_actors]
        if missing_actors:
            problems.append(f"근거에 없는 주체 {missing_actors}: {line[:60]}")
        strong = [w for w in STRONG_WORDS if w in line and w not in evidence]
        if strong:
            problems.append(f"근거보다 강한 표현 {strong}: {line[:60]}")
        if kind == "content" and not problems:
            self.content_lines += 1
        return kind, problems


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


def make_answer_writer(llm=None) -> Callable[..., dict]:
    """writer(question, output, books, picks=None, on_line=None) →
    {'answer': str|None, 'problems': [...], 'draft': str, 'dropped': [...]}.
    on_line(text)는 검사를 통과한 줄을 받는다(화면 스트리밍). 템플릿으로 되돌리면 호출자가 최종 답변으로 덮어쓴다."""
    llm = llm or make_llm()

    def writer(question: str, output: dict, books: dict, picks: dict | None = None,
               on_line: Callable[[str], None] | None = None) -> dict:
        blocks = evidence_blocks(output, picks)
        if not blocks:
            return {"answer": None, "problems": ["근거 없음"], "draft": "", "dropped": []}
        notes = " ".join([question, *(w for w in output["warnings"] if w.startswith("[미해결]"))])
        checker = LineChecker(blocks, books, notes)
        kept: list[str] = []
        dropped: list[str] = []
        problems: list[str] = []
        draft: list[str] = []
        pending = {"heading": None, "first": None}     # 아직 내보내지 않은 소제목, 첫 내용 줄 통과 여부

        def emit(text: str) -> None:
            kept.append(text)
            if on_line:
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
                    pending["heading"] = None if found else line
                    dropped.extend([line] if found else [])
                    problems.extend(found)
                elif found:
                    dropped.append(line)
                    problems.extend(found)
                    if kind == "content" and pending["first"] is None:
                        pending["first"] = False
                else:
                    if kind == "content" and pending["first"] is None:
                        pending["first"] = True
                    if pending["heading"]:
                        emit(pending["heading"])
                        pending["heading"] = None
                    emit(line)

        messages = [("system", SYSTEM_PROMPT), ("user", evidence_prompt(question, output, blocks))]
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
        draft_text = "\n".join(draft).strip()
        dropped_content = [d for d in dropped if REF_RE.search(d)]
        reasons = []
        if checker.content_lines == 0:
            reasons.append("근거를 인용한 줄이 없음")
        elif pending["first"] is False:
            reasons.append("첫 내용 줄(직접 답)이 검사에 걸림")
        elif len(dropped_content) > checker.content_lines:
            reasons.append(f"검사에 걸린 줄({len(dropped_content)})이 통과한 줄({checker.content_lines})보다 많음")
        if reasons:
            return {"answer": None, "problems": reasons + problems, "draft": draft_text, "dropped": dropped}
        tail = []
        if any("담당 예시" in f["claim"] or "(EXAMPLE)" in f["claim"] for f in output["findings"]):
            tail.append(EXAMPLE_NOTE)
            if on_line:
                on_line("")
                on_line(EXAMPLE_NOTE)
        answer = "\n".join([*kept, "", *tail]).strip()
        return {"answer": answer, "problems": problems, "draft": draft_text, "dropped": dropped}

    return writer
