"""v2 Action Tool의 검색 적합성 검사. 점수 계산보다 먼저 적용하며 DB 관계를 만들지 않는다.

질병별 표는 행의 질병명을 우선한다. 본문은 가장 가까운 질병 절을 사용한다.
COMMON이나 사전에 없는 표기를 특정 질병으로 추정하지 않는다.
"""

from __future__ import annotations

import re
import unicodedata

from index.school.dictionaries import compare_key

# 학교 조치를 직접 지칭하는 표현만 사용한다. '전파', '관리' 같은 넓은 표현은 제외한다.
OPERATION_TERMS = ("휴업", "휴교", "등교중지", "소독", "환기", "능동감시")
DISEASE_COLUMNS = {"감염병", "감염병명", "질병", "질병명"}


def text_key(text: str) -> str:
    return compare_key(re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE))


def disease_mentions(text: str, books: dict) -> set[str]:
    """승인 name/aliases만 인식한다. review_aliases(예: 눈병)는 자동 연결하지 않는다."""
    key = text_key(text)
    spaced = unicodedata.normalize("NFKC", re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)).casefold()
    found = set()
    for entry in books["disease"]["entries"]:
        for name in [entry["name"], *entry.get("aliases", [])]:
            name_key = text_key(name)
            if len(name_key) < 2:
                continue
            # Flu, TB 같은 짧은 영문 별칭을 다른 영문 단어 안에서 찾지 않는다.
            pattern = re.escape(name_key)
            if name_key.isascii():
                pattern = r"(?<![a-z])" + r"\s*".join(map(re.escape, name_key)) + r"(?![a-z])"
            if re.search(pattern, spaced if name_key.isascii() else key):
                found.add(entry["disease_id"])
                break
    return found


def mask_disease_names(text: str, books: dict) -> str:
    """단계 힌트용 비교 문자열. '유행성이하선염 유행'에서 뒤의 유행은 남긴다."""
    key = text_key(text)
    names = {text_key(n) for e in books["disease"]["entries"]
             for n in [e["name"], *e.get("aliases", [])] if len(text_key(n)) >= 2}
    for name in sorted(names, key=len, reverse=True):
        pattern = re.escape(name)
        if name.isascii():
            pattern = r"(?<![a-z])" + pattern + r"(?![a-z])"
        key = re.sub(pattern, "#", key)
    return key


def evidence_diseases(hit: dict, books: dict, *, excerpt: str | None = None) -> set[str] | None:
    """명시 질병 집합. None=일반 근거, 빈 집합=질병 표의 주제를 사전으로 확인 못 함.

    표 행이 가장 구체적이다. 추출 시 앞 절 제목이 이어 붙은 경우에도 행을 우선한다.
    본문 발췌에 질병명이 없어도 원문의 질병 절 정보는 보존한다.
    """
    table_lines = [line.strip() for line in hit.get("text", "").splitlines()
                   if line.strip().startswith("|")]
    if len(table_lines) >= 2:
        header = table_lines[0].strip("|").split("|")[0]
        if text_key(header) in DISEASE_COLUMNS:
            subject = table_lines[-1].strip("|").split("|")[0]
            return disease_mentions(subject, books)
    for section in reversed((hit.get("section") or "").split(">")):
        diseases = disease_mentions(section, books)
        if diseases:
            return diseases
    return disease_mentions(hit.get("text", "") if excerpt is None else excerpt, books) or None


def disease_mismatch(diseases: set[str] | None, wanted: list[str]) -> bool:
    return bool(wanted) and diseases is not None and not diseases.intersection(wanted)


def action_disease_mismatch(action: dict, wanted: list[str], books: dict) -> bool:
    if not wanted:
        return False
    known = {e["disease_id"] for e in books["disease"]["entries"]}
    declared = set(action.get("disease_ids", [])) & known
    if declared and not declared.intersection(wanted):
        return True
    text = " ".join(action.get(k) or "" for k in ("action_text", "when_text", "timing_text"))
    mentioned = disease_mentions(text, books)
    if mentioned and not mentioned.intersection(wanted):
        return True
    if declared or mentioned:
        return False
    return disease_mismatch(evidence_diseases({"section": action.get("section"), "text": ""}, books), wanted)


def operation_terms(question: str, roles: list[str]) -> list[str]:
    # 역할 질문은 '휴업 시 담임교사가 할 일'처럼 조치가 배경일 수 있어 수행 목록을 자르지 않는다.
    if roles:
        return []
    key = text_key(question)
    return [term for term in OPERATION_TERMS if term in key]


def matches_operation(text: str, terms: list[str]) -> bool:
    return not terms or any(term in text_key(text) for term in terms)
