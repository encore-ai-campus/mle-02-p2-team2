"""school 개인 임시 표준 사전(Disease·SchoolRole·ActionConcept) 로더와 무결성 검사.

공통 사전(data/specs/ids/)이 3인 승인되기 전까지 school 파이프라인이 쓰는 임시 사전이다.
승인되면 JSON 파일을 공통 위치로 옮기거나 DICT_DIR만 바꾸면 된다.

- 비교 키는 수업 42-5 normalize의 학교 버전(R03 ref_compare_text)과 같다:
  NFKC 정규화 → 공백 제거 → casefold. 원문 표기는 바꾸지 않고 비교할 때만 쓴다.
- 이 모듈은 사전을 읽고 검사하는 일만 한다. 표기를 ID로 연결하는 resolve_* 함수는
  B03(역할·질병·개념 매퍼)에서 이 모듈의 alias_index를 이용해 만든다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

DICT_DIR = Path(__file__).resolve().parent

# 사전 종류 → (파일명, ID 필드, Neo4j 노드 라벨, Neo4j에 적재할 속성)
KINDS = {
    "disease": ("diseases.json", "disease_id", "Disease", ("disease_id", "name", "aliases")),
    "role": ("roles.json", "role_id", "SchoolRole", ("role_id", "name", "aliases")),
    "concept": ("concepts.json", "concept_id", "ActionConcept", ("concept_id", "name")),
}

# 온톨로지 v3.1 초안 사전의 항목 수. 바뀌면 3인 승인 후 이 값도 함께 고친다.
EXPECTED_COUNTS = {"disease": 11, "role": 11, "concept": 27}

UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")


class DictionaryError(ValueError):
    """사전 파일의 형식·중복·충돌 오류."""


def compare_key(text: str) -> str:
    """비교용 키. 원문 표기는 그대로 두고 비교할 때만 쓴다."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def load_dictionary(kind: str, base: Path | None = None) -> dict:
    """사전 하나를 읽는다. kind는 'disease', 'role', 'concept' 중 하나."""
    if kind not in KINDS:
        raise DictionaryError(f"알 수 없는 사전 종류: {kind}")
    path = (base or DICT_DIR) / KINDS[kind][0]

    def reject(constant):
        raise DictionaryError(f"{path.name}: 표준 JSON에 없는 값 {constant}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def load_dictionaries(base: Path | None = None) -> dict[str, dict]:
    """세 사전을 모두 읽고 무결성 검사를 통과한 결과만 돌려준다."""
    data = {kind: load_dictionary(kind, base) for kind in KINDS}
    problems = validate_dictionaries(data)
    if problems:
        raise DictionaryError("사전 검사 실패:\n- " + "\n- ".join(problems))
    return data


def alias_index(entries: list[dict], id_field: str) -> dict[str, str]:
    """비교 키 → 표준 ID. 이름과 aliases만 넣는다(review_aliases는 넣지 않는다).

    서로 다른 ID가 같은 비교 키를 가지면 자동 연결이 모호해지므로 오류를 낸다.
    """
    index: dict[str, str] = {}
    for entry in entries:
        for surface in (entry["name"], *entry.get("aliases", [])):
            key = compare_key(surface)
            owner = index.get(key)
            if owner and owner != entry[id_field]:
                raise DictionaryError(f"별칭 충돌: '{surface}' → {owner}, {entry[id_field]}")
            index[key] = entry[id_field]
    return index


def validate_dictionaries(data: dict[str, dict]) -> list[str]:
    """형식·개수·중복·별칭 충돌·보조 목록의 참조를 검사해 문제 목록을 돌려준다."""
    problems: list[str] = []
    for kind, (filename, id_field, _label, _props) in KINDS.items():
        book = data[kind]
        entries = book.get("entries", [])
        if book.get("id_field") != id_field:
            problems.append(f"{filename}: id_field가 {id_field}가 아님")
        ids = [entry.get(id_field) for entry in entries]
        if len(ids) != len(set(ids)):
            problems.append(f"{filename}: 중복 ID")
        if len(entries) != EXPECTED_COUNTS[kind]:
            problems.append(f"{filename}: 항목 {len(entries)}개 (기대 {EXPECTED_COUNTS[kind]}개)")
        for entry in entries:
            if not isinstance(entry.get(id_field), str) or not UPPER_SNAKE.match(entry[id_field]):
                problems.append(f"{filename}: ID 형식 오류 {entry.get(id_field)!r}")
            if not isinstance(entry.get("name"), str) or not entry["name"].strip():
                problems.append(f"{filename}: {entry.get(id_field)} 이름 없음")
            if not all(isinstance(a, str) and a.strip() for a in entry.get("aliases", [])):
                problems.append(f"{filename}: {entry.get(id_field)} 빈 별칭")
        try:
            index = alias_index(entries, id_field)
        except DictionaryError as exc:
            problems.append(f"{filename}: {exc}")
            index = {}
        # 넓은 표현(review_aliases)이 다른 항목의 확정 별칭과 겹치면 안 된다.
        for entry in entries:
            for surface in entry.get("review_aliases", []):
                if compare_key(surface) in index:
                    problems.append(f"{filename}: review_alias '{surface}'가 확정 별칭과 겹침")

        if kind == "role":
            role_ids = set(ids)
            for item in book.get("compound_mentions", []):
                missing = set(item.get("role_ids", [])) - role_ids
                if missing:
                    problems.append(f"{filename}: 복합 표기 '{item['mention']}'의 없는 ID {sorted(missing)}")
            # 제외·미등록 표기가 확정 별칭으로 연결되면 규칙이 서로 모순된다.
            for group in ("excluded_mentions", "unregistered_mentions"):
                for item in book.get(group, []):
                    if compare_key(item["mention"]) in index:
                        problems.append(f"{filename}: {group} '{item['mention']}'가 별칭과 겹침")
        if kind == "concept":
            for item in book.get("pending_candidates", []):
                if item.get("proposed_id") in set(ids):
                    problems.append(f"{filename}: 후보 {item['proposed_id']}가 이미 표준 ID")
    return problems


def neo4j_rows(kind: str, data: dict[str, dict]) -> list[dict]:
    """Neo4j 사전 노드에 넣을 속성만 남긴다(group·evidence·note 등 메타데이터 제외)."""
    _file, _id_field, _label, props = KINDS[kind]
    return [{prop: entry.get(prop, []) if prop == "aliases" else entry[prop] for prop in props}
            for entry in data[kind]["entries"]]
