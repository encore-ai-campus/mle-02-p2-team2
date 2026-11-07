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
EVIDENCE_IMPLIES = {"행정지원팀": ("행정실",)}
NEUTRAL_HEADING = "**담당이 적혀 있지 않은 내용**"
SCENARIO_MARK = "모의훈련 시나리오 예시"
SHORT_MARKS = {SCENARIO_MARK: "시나리오 예시"}          # 답변 줄에 붙이는 짧은 표기
# '확인되지 않았다'처럼 근거가 없음을 밝히는 문장(주장이 아니라 주체·강도 검사를 하지 않는다).
UNCONFIRMED_RE = re.compile(r"확인되지\s*않|확인하지\s*못|확인할\s*수\s*없|알\s*수\s*없|나와\s*있지\s*않|"
                            r"명시되어\s*있지\s*않|적혀\s*있지\s*않|단정할\s*수\s*없|단정하기\s*어렵")
STAGE_RE = re.compile(r"대응\s*제\s*(\d)\s*단계")
# 첫 줄의 예/아니요 결론어. 시나리오 근거만 인용한 줄에서는 지운다(본문 지침과 다를 수 있다, 11/07 S-T03).
VERDICT_RE = re.compile(r"^(?:네|예|아니요|아니오)\s*[.,!]\s*")
# 원문의 시간 순서 조건 구절('격리 해제 후', '유행 종료 이후'). '~경우·~때·~시'는 답변이 '~면'으로 옮기는 일이 많아
# 글자 대조로는 오탐이 많았다(11/07 재생: 12건 중 11건 오탐). 그래서 순서 표지만 본다.
CONDITION_RE = re.compile(r"((?:[가-힣]+\s+)?[가-힣]{2,})\s*(이후|후)(?:에는|에도|에)?(?=[\s,;.)]|$)")
SEGMENT_SPLIT_RE = re.compile(r"<br\s*/?>|[;|∙•·\n]|\s-\s|\d\)\s|(?<=[.。])\s")
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
10. 근거에 '핵심 구절'이 있으면 그 표현을 살려 쓴다. 한 줄에는 한 가지 내용만 쓴다.
11. 근거에 '표시'가 있으면 그 근거를 쓴 줄에 밝힌다(예: '교육부 모의훈련 시나리오에서는 …', '원문의 대응 제3단계에서는 …').
    시나리오 예시를 일반 지침처럼 쓰지 않는다.
12. 각 줄은 그 줄만 읽어도 누가·언제 하는지 알 수 있게 쓴다(근거에 담당·조건이 있으면 빼지 않는다).
13. 예/아니요를 묻는 질문은 '표시'가 없는 본문 지침 근거로 답한다. 시나리오 예시만으로 '네/아니요'를 정하지 않는다.
    본문 지침과 시나리오 예시가 다르면 본문 지침을 먼저 쓰고, 시나리오는 예시로 밝혀 뒤에 쓴다."""


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
    from rag.school.main_graph import DOC_TITLES

    blocks = []
    for number, citation in enumerate(output["citations"], 1):
        claims = [f["claim"] for f in output["findings"] if citation["chunk_id"] in f["chunk_ids"]]
        excerpt = re.sub(r"[-]", " ", citation["excerpt"])
        excerpt = re.sub(r"[ \t]+", " ", excerpt).strip()
        blocks.append({"n": number, "document": DOC_TITLES.get(citation["document_id"], (citation["document_id"],))[0],
                       "claims": claims, "excerpt": excerpt[:EXCERPT_CHARS],
                       "role": (picks or {}).get(citation["chunk_id"], {}).get("role"),
                       "quotes": (picks or {}).get(citation["chunk_id"], {}).get("quotes", []),
                       "marks": context_marks(claims, citation["chunk_id"] in (scenario or set()))})
    return blocks


ROLE_NAMES = {"DIRECT": "직접 답", "CONDITION": "조건·예외", "PREMISE": "질문 전제 확인"}


def evidence_prompt(question: str, output: dict, blocks: list[dict]) -> str:
    parts = [f"[질문]\n{question}\n", "[근거]"]
    for block in blocks:
        claims = "\n".join(f"  · {c}" for c in block["claims"])
        head = f"[{block['n']}] {block['document']}" + (f" ({ROLE_NAMES[block['role']]})" if block.get("role") else "")
        quotes = "".join(f"\n  핵심 구절: “{q}”" for q in block.get("quotes", []))
        marks = f"\n  표시: {', '.join(block['marks'])}(이 근거를 쓴 줄에 밝힐 것)" if block.get("marks") else ""
        parts.append(f"{head}{marks}\n  정리된 내용:\n{claims}{quotes}\n  원문 발췌: {block['excerpt']}")
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


def asserted_text(line: str) -> str:
    """주장하는 부분만: '확인되지 않았습니다'류 문장·절을 뺀다(인용 번호는 지운다)."""
    text = REF_RE.sub(" ", line)
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
        bullet = re.match(r"^(\s*[-*]\s+)", line)
        head = bullet.group(1) if bullet else ""
        line = head + "".join(f"({p}) " for p in prefixes) + line[len(head):]
    return line


class LineChecker:
    """줄 하나씩 검사한다(스트리밍용). 소제목이 가리키는 담당을 다음 줄 검사에 이어 쓴다.
    check 뒤 self.moved(소제목 담당이 근거에 없어 중립 소제목으로 옮길 줄), self.prefixes·self.conditions(덧붙일 표시)를
    읽는다. 이 셋은 줄을 지우는 사유가 아니다."""

    HEADING_RE = re.compile(r"\*\*[^*\[\]]{1,30}\*\*:?|[^*\[\].。]{1,20}:")

    def __init__(self, blocks: list[dict], books: dict, notes: str = ""):
        self.terms = actor_terms(books)
        self.blocks = {b["n"]: b for b in blocks}
        self.source = {b["n"]: " ".join([b["document"], *b["claims"], b["excerpt"]]) for b in blocks}
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
        if line.startswith("※"):
            # 확인하지 못한 부분 안내는 주장이 아니다. 숫자만 질문·미해결·근거 어디엔가 있는지 본다.
            missing_numbers = _numbers(line) - _numbers(" ".join([self.notes, *self.source.values()]))
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
        missing_numbers = _numbers(claim) - _numbers(evidence)
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


def make_answer_writer(llm=None) -> Callable[..., dict]:
    """writer(question, output, books, picks=None, on_line=None) →
    {'answer': str|None, 'problems': [...], 'draft': str, 'dropped': [...]}.
    on_line(text)는 검사를 통과한 줄을 받는다(화면 스트리밍). 템플릿으로 되돌리면 호출자가 최종 답변으로 덮어쓴다."""
    llm = llm or make_llm()

    def writer(question: str, output: dict, books: dict, picks: dict | None = None,
               on_line: Callable[[str], None] | None = None, scenario: set[str] | None = None) -> dict:
        blocks = evidence_blocks(output, picks, scenario)
        if not blocks:
            return {"answer": None, "problems": ["근거 없음"], "draft": "", "dropped": []}
        notes = " ".join([question, *(w for w in output["warnings"] if w.startswith("[미해결]"))])
        checker = LineChecker(blocks, books, notes)
        kept: list[str] = []
        dropped: list[str] = []
        problems: list[str] = []
        draft: list[str] = []
        moved: list[str] = []
        marked: list[str] = []
        # heading: 지금 유효한 소제목(검사 통과), shown: 마지막으로 내보낸 소제목, first: 첫 내용 줄 통과 여부
        state = {"heading": None, "shown": None, "first": None}

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
                    # 담당이 근거에 없는 소제목은 내지 않는다. 그 아래 줄은 중립 소제목으로 옮겨진다.
                    state["heading"] = None if found else line
                    dropped.extend([line] if found else [])
                    problems.extend(found)
                elif found:
                    dropped.append(line)
                    problems.extend(found)
                    if kind == "content" and state["first"] is None:
                        state["first"] = False
                else:
                    if kind == "content" and state["first"] is None:
                        state["first"] = True
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
        elif state["first"] is False:
            reasons.append("첫 내용 줄(직접 답)이 검사에 걸림")
        elif len(dropped_content) > checker.content_lines:
            reasons.append(f"검사에 걸린 줄({len(dropped_content)})이 통과한 줄({checker.content_lines})보다 많음")
        if reasons:
            return {"answer": None, "problems": reasons + problems, "draft": draft_text, "dropped": dropped,
                    "moved": moved, "marked": marked}
        tail = []
        if any("담당 예시" in f["claim"] or "(EXAMPLE)" in f["claim"] for f in output["findings"]):
            tail.append(EXAMPLE_NOTE)
            if on_line:
                on_line("")
                on_line(EXAMPLE_NOTE)
        answer = "\n".join([*kept, "", *tail]).strip()
        return {"answer": answer, "problems": problems, "draft": draft_text, "dropped": dropped,
                "moved": moved, "marked": marked}

    return writer
