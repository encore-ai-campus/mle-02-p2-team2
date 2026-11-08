# 공통 계약 모듈이 생기면 import로 교체
"""허용된 원문 청크에서 질병 사실과 출처를 반환하는 검색 도구."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from index.school.dictionaries import compare_key, load_dictionaries
from index.school.embed import make_embedder
from index.school.neo4j_local import get_driver
from rag.respiratory.graph import PROJECT_ROOT, detect_diseases
from rag.school.search import STOPWORDS, SearchError, search_school_chunks

Region = Literal["NATIONAL", "GYEONGGI"]
ToolName = Literal[
    "action_search",
    "disease_search",
    "legal_search",
    "crossdoc_graph_search",
]
ToolStatus = Literal["OK", "PARTIAL", "NO_EVIDENCE", "ERROR"]


class ToolInput(TypedDict):
    question: str
    task: str
    region: Region
    allowed_document_ids: list[str]
    preferred_document_ids: NotRequired[list[str]]
    context_entity_ids: NotRequired[list[str]]
    top_k: NotRequired[int]


class Citation(TypedDict):
    document_id: str
    chunk_id: str
    pdf_page: int
    printed_page: int | None
    excerpt: str


class Finding(TypedDict):
    claim: str
    chunk_ids: list[str]
    action_ids: list[str]
    disease_ids: list[str]
    concept_ids: list[str]


class GraphEdge(TypedDict):
    source_ref: str
    relationship_type: str
    target_ref: str


class GraphPath(TypedDict):
    edges: list[GraphEdge]


class ToolResult(TypedDict):
    tool_name: ToolName
    status: ToolStatus
    findings: list[Finding]
    citations: list[Citation]
    graph_paths: list[GraphPath]
    warnings: list[str]


ABOUT_QUERY = """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:ABOUT]->(x:Disease)
WHERE c.chunk_id IN $ids
  AND d.document_id IN $allowed_document_ids
  AND x.disease_id IN $disease_ids
RETURN c.chunk_id AS chunk_id, x.disease_id AS disease_id
ORDER BY c.chunk_id, x.disease_id
"""
DISEASE_ID_QUERY = """
MATCH (d:Disease)
WHERE d.disease_id IN $disease_ids
RETURN DISTINCT d.disease_id AS disease_id
"""
DIRECT_QUERY = """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
WHERE d.document_id IN $allowed_document_ids AND c.text IS NOT NULL
  AND any(name IN $names WHERE toLower(c.text) CONTAINS toLower(name))
  AND any(term IN $terms WHERE toLower(c.text) CONTAINS toLower(term))
RETURN d.document_id AS document_id, c.chunk_id AS chunk_id,
       c.pdf_page AS pdf_page, c.printed_page AS printed_page, c.text AS text
ORDER BY c.chunk_id, d.document_id SKIP $offset LIMIT $page_size
"""
FIXTURE_DIR = PROJECT_ROOT / "tests/fixtures/respiratory"
RESPIRATORY_DISEASES_PATH = PROJECT_ROOT / "src/index/respiratory/dictionaries/diseases.json"
EXCLUDE_PHRASES_PATH = Path(__file__).with_name("exclude_phrases.json")
MAX_TOP_K = 100
HEADER_RE = re.compile(r"^\W*\d{4}년도?\s*\S+(\s\S+)?\s*관리지침")
TABLE_CHUNK_RE = re.compile(r":t\d+r\d+$")
PARTICLES = ("에서", "으로", "에게", "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로", "란")
BRACKET_SEPARATORS = str.maketrans({character: " " for character in "()（）[]"})


def _result(status: ToolStatus, warnings: list[str] | None = None) -> ToolResult:
    return {
        "tool_name": "disease_search",
        "status": status,
        "findings": [],
        "citations": [],
        "graph_paths": [],
        "warnings": list(warnings or []),
    }


def merge_books(books: dict) -> dict:
    """머지 전 각자 사전을 disease_id 기준으로 합친다"""
    merged = deepcopy(books)
    respiratory = json.loads(RESPIRATORY_DISEASES_PATH.read_text(encoding="utf-8"))
    entries = merged["disease"]["entries"]
    existing_ids = {entry["disease_id"] for entry in entries}
    for entry in respiratory["entries"]:
        disease_id = entry["disease_id"]
        if disease_id not in existing_ids:
            entries.append(entry)
            existing_ids.add(disease_id)
    merged["exclude_phrases"] = json.loads(EXCLUDE_PHRASES_PATH.read_text(encoding="utf-8"))
    return merged


def _default_deps() -> dict:
    driver, database = get_driver(PROJECT_ROOT / ".env")
    try:
        return {
            "driver": driver,
            "database": database,
            "embedder": make_embedder(),
            "books": load_dictionaries(),
        }
    except Exception:
        driver.close()
        raise


def _remove_question_exclusions(question: str, disease_id: str, books: dict) -> str:
    remaining = question
    for phrase in books.get("exclude_phrases", {}).get(disease_id, []):
        if isinstance(phrase, str) and phrase.strip():
            pattern = r"\s*".join(re.escape(part) for part in phrase.split())
            remaining = re.sub(pattern, " ", remaining, flags=re.IGNORECASE)
    return remaining


def _context_diseases(tool_input: ToolInput, books: dict) -> list[str]:
    question = tool_input["question"]
    disease_ids = []
    for disease_id in detect_diseases(question, books):
        # 다른 질병의 이름 안에 포함된 별칭만 지운 뒤 다시 판별한다.
        # 같은 질문에 계절 인플루엔자도 별도로 언급된 경우는 남겨 둔다.
        remaining = _remove_question_exclusions(question, disease_id, books)
        if disease_id in detect_diseases(remaining, books):
            disease_ids.append(disease_id)
    for entity_id in tool_input.get("context_entity_ids", []):
        if isinstance(entity_id, str) and entity_id.startswith("Disease:"):
            disease_id = entity_id.removeprefix("Disease:")
            remaining = _remove_question_exclusions(question, disease_id, books)
            excluded_only = remaining != question and disease_id not in detect_diseases(remaining, books)
            if disease_id and disease_id not in disease_ids and not excluded_only:
                disease_ids.append(disease_id)
    return disease_ids


def _disease_names(books: dict, disease_ids: list[str]) -> dict[str, list[str]]:
    selected = set(disease_ids)
    names: dict[str, list[str]] = {}
    for entry in books["disease"]["entries"]:
        disease_id = entry["disease_id"]
        if disease_id in selected:
            names[disease_id] = [entry["name"], *entry.get("aliases", [])]
    return names


def _about_diseases(
    driver,
    chunk_ids: list[str],
    allowed_document_ids: list[str],
    disease_ids: list[str],
    database,
) -> dict[str, set[str]]:
    if not chunk_ids or not disease_ids:
        return {}
    try:
        records, _, _ = driver.execute_query(
            ABOUT_QUERY,
            database_=database,
            ids=chunk_ids,
            allowed_document_ids=allowed_document_ids,
            disease_ids=disease_ids,
        )
    except Exception as error:
        raise SearchError(f"{type(error).__name__}: ABOUT 조회 실패") from error

    result: dict[str, set[str]] = {}
    for record in records:
        row = record.data() if hasattr(record, "data") else dict(record)
        chunk_id = row.get("chunk_id")
        disease_id = row.get("disease_id")
        if chunk_id in chunk_ids and disease_id in disease_ids:
            result.setdefault(chunk_id, set()).add(disease_id)
    return result


def _known_disease_ids(driver, disease_ids: list[str], database) -> set[str]:
    """로컬 별칭 사전과 별도로 실제 DB에 존재하는 질병 ID만 확인한다."""
    if not disease_ids:
        return set()
    try:
        records, _, _ = driver.execute_query(
            DISEASE_ID_QUERY, database_=database, routing_="r", disease_ids=disease_ids
        )
    except Exception as error:
        raise SearchError(f"{type(error).__name__}: Disease ID 조회 실패") from error
    requested = set(disease_ids)
    return {record["disease_id"] for record in records if record["disease_id"] in requested}


def _focus_terms(question: str, books: dict, *, split_brackets: bool) -> list[str]:
    """괄호 분리 여부에 따라 질문의 핵심어를 만든다."""
    stopword_keys = {compare_key(word) for word in STOPWORDS}
    config = books.get("exclude_phrases", {})
    stopword_keys.update(compare_key(word) for word in config.get("focus_stopwords", []))
    stopword_prefixes = [compare_key(word) for word in config.get("focus_stopword_prefixes", [])]
    aliases = config.get("focus_term_aliases", {})
    disease_keys = set()
    for entry in books["disease"]["entries"]:
        for surface in (entry["name"], *entry.get("aliases", [])):
            disease_keys.add(compare_key(surface))
            disease_keys.update(
                compare_key(fragment)
                for fragment in surface.split()
                if len(fragment) >= 2
            )

    terms = []
    seen = set()
    # '전염 기간'처럼 띄어 쓴 문서 용어를 붙여 쓴다(띄어 쓰면 '전염'이 '전파'로 바뀌어 '전염기간' 줄을 찾지 못했다, 2026-11-08).
    question = re.sub(r"(전염|감염)\s+(가능\s*)?(기간|력)", lambda m: m.group(1) + (m.group(2) or "").replace(" ", "")
                      + m.group(3), question)
    words = question.translate(BRACKET_SEPARATORS).split() if split_brackets else question.split()
    for raw_term in words:
        term = "".join(
            character
            for character in raw_term
            if not unicodedata.category(character).startswith("P")
        )
        for particle in PARTICLES:
            if term.endswith(particle):
                term = term[: -len(particle)]
                break
        # 조사 뒤에 남은 어미를 긴 순서로 한 번만 제거해 원문 용어와 비교한다.
        for ending in sorted(config.get("focus_endings", []), key=len, reverse=True):
            if ending and term.endswith(ending):
                term = term[: -len(ending)]
                break
        # 어미 제거 후 일상어를 문서 용어로 정규화하면 한 글자 어간도 살릴 수 있다.
        for canonical, variants in aliases.items():
            if any(compare_key(term).startswith(compare_key(variant)) for variant in variants):
                term = canonical
                break
        key = compare_key(term)
        if (
            len(term) < 2
            or key in stopword_keys
            or any(key.startswith(prefix) for prefix in stopword_prefixes if prefix)
            or key in disease_keys
            or key in seen
        ):
            continue
        terms.append(term)
        seen.add(key)
    return terms


def focus_terms(question: str, books: dict) -> list[str]:
    """괄호 안의 단어를 분리한 뒤 조사·질병명·검색 불용어를 뺀다."""
    return _focus_terms(question, books, split_brackets=True)


def _name_positions(text: str, name: str) -> list[int]:
    if not name:
        return []
    return [
        match.start()
        for match in re.finditer(re.escape(name), text, re.IGNORECASE)
        if match.start() == 0 or not ("가" <= text[match.start() - 1] <= "힣")
    ]


def _text_disease_ids(text: str, disease_ids: list[str], names: dict[str, list[str]]) -> set[str]:
    matched = set()
    for disease_id in disease_ids:
        if any(_name_positions(text, name) for name in names.get(disease_id, [])):
            matched.add(disease_id)
    return matched


def _name_position(text: str, names: list[str]) -> int | None:
    positions = [
        position
        for name in names
        for position in _name_positions(text, name)
    ]
    return min(positions) if positions else None


def _mask_header_lines(text: str) -> str:
    """머리말 줄은 길이를 보존한 공백으로 바꿔 검색 위치 계산에 사용한다."""
    masked_lines = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        if HEADER_RE.match(content):
            masked_lines.append(" " * len(content) + ending)
        else:
            masked_lines.append(line)
    return "".join(masked_lines)


def _mask_excluded_lines(text: str, exclude_phrases: list[str]) -> str:
    """질병별 제외 문구가 있는 줄을 같은 길이의 공백으로 가린다."""
    phrases = [phrase for phrase in exclude_phrases if isinstance(phrase, str) and phrase]
    if not phrases:
        return text
    masked_lines = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        if any(phrase in content for phrase in phrases):
            masked_lines.append(" " * len(content) + ending)
        else:
            masked_lines.append(line)
    return "".join(masked_lines)


def _focus_position(text: str, terms: list[str]) -> int | None:
    positions = [
        match.start()
        for term in terms
        for match in re.finditer(re.escape(term), text, re.IGNORECASE)
    ]
    return min(positions) if positions else None


def _direct_candidates(driver, database, allowed, disease_ids, names, terms, exclusions):
    """허용 문서의 직접 일치 후보를 필터 적용 후 최대 20개 반환한다."""
    if not allowed or not disease_ids or not terms:
        return []
    surfaces = list(dict.fromkeys(name for values in names.values() for name in values if name))
    if not surfaces:
        return []
    hits = []
    seen = set()
    offset = 0
    page_size = 100
    while len(hits) < 20:
        try:
            records, _, _ = driver.execute_query(
                DIRECT_QUERY,
                database_=database,
                allowed_document_ids=allowed,
                names=surfaces,
                terms=terms,
                offset=offset,
                page_size=page_size,
            )
        except Exception as error:
            raise SearchError(f"{type(error).__name__}: 직접 후보 조회 실패") from error
        for record in records:
            hit = record.data() if hasattr(record, "data") else dict(record)
            chunk_id = hit.get("chunk_id")
            text = hit.get("text")
            if (
                hit.get("document_id") not in allowed
                or not isinstance(chunk_id, str)
                or not chunk_id
                or chunk_id in seen
                or not isinstance(text, str)
            ):
                continue
            searchable = _mask_header_lines(text)
            if _focus_position(searchable, terms) is None:
                continue
            if not any(
                _text_disease_ids(
                    _mask_excluded_lines(searchable, exclusions.get(disease_id, [])),
                    [disease_id],
                    names,
                )
                for disease_id in disease_ids
            ):
                continue
            seen.add(chunk_id)
            hits.append(hit)
            if len(hits) == 20:
                break
        if len(records) < page_size:
            break
        offset += page_size
    return hits


# 한 질병만 다루는 질병관리청 2026 지침(문서 제목 기준). 이 문서의 청크는 본문에 병명이 없어도 그 질병의 근거다.
DEDICATED_DOCS = {
    "kdca_hfmd_2026": "HFMD",
    "kdca_measles_2026": "MEASLES",
    "kdca_flu_facility_2026": "INFLUENZA",
    "kdca_covid19_2026": "COVID19",
}
SCOPED_QUERY = """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
WHERE d.document_id IN $allowed_document_ids AND c.text IS NOT NULL
  AND (d.document_id IN $docs OR any(name IN $names WHERE coalesce(c.section, '') CONTAINS name))
  AND any(term IN $terms WHERE c.text CONTAINS term OR coalesce(c.section, '') CONTAINS term)
RETURN d.document_id AS document_id, c.chunk_id AS chunk_id, c.pdf_page AS pdf_page,
       c.printed_page AS printed_page, c.text AS text, c.section AS section
ORDER BY c.chunk_id LIMIT 200
"""


def _scope_disease_ids(
    hit: dict,
    disease_ids: list[str],
    names: dict[str, list[str]],
    exclusions: dict | None = None,
) -> set[str]:
    """질병 전용 문서이거나 소제목에 병명이 있으면 그 질병 ID.
    단, 본문·소제목에 그 질병의 제외 문구(예: 인플루엔자의 '파라인플루엔자'·'조류인플루엔자')가 있으면 보지 않는다."""
    section = hit.get("section") or ""
    body = section + " " + (hit.get("text") or "")
    matched = set()
    for disease_id in disease_ids:
        if any(phrase and phrase in body for phrase in (exclusions or {}).get(disease_id, [])):
            continue
        if DEDICATED_DOCS.get(hit.get("document_id")) == disease_id or any(
            name and name in section for name in names.get(disease_id, [])
        ):
            matched.add(disease_id)
    return matched


def _scoped_candidates(driver, database, allowed, disease_ids, names, terms, exclusions=None, limit=10):
    """질병 전용 문서·병명 소제목 청크 중 핵심어가 든 것(핵심어를 많이 담은 순서로 최대 limit개)."""
    if not allowed or not disease_ids or not terms:
        return []
    docs = [doc for doc, disease_id in DEDICATED_DOCS.items() if disease_id in disease_ids and doc in allowed]
    surfaces = [name for disease_id in disease_ids for name in names.get(disease_id, []) if len(name) >= 2]
    try:
        records, _, _ = driver.execute_query(
            SCOPED_QUERY, database_=database, allowed_document_ids=list(allowed), docs=docs,
            names=surfaces, terms=list(terms),
        )
    except Exception as error:
        raise SearchError(f"{type(error).__name__}: 질병 전용 후보 조회 실패") from error
    rows = [record.data() if hasattr(record, "data") else dict(record) for record in records]
    rows = [row for row in rows if _scope_disease_ids(row, disease_ids, names, exclusions)]

    def overlap(row):
        body = (row.get("section") or "") + " " + (row.get("text") or "")
        return sum(term in body for term in terms)

    rows.sort(key=lambda row: (-overlap(row), row["chunk_id"]))
    return rows[:limit]


def _merge_direct_candidates(hits, direct, preferred):
    """직접 후보마다 RRF 1위의 가중치를 더하고 청크를 중복 제거한다."""
    merged = {}
    for rank, hit in enumerate(hits, 1):
        if not isinstance(hit, dict) or not isinstance(hit.get("chunk_id"), str):
            continue
        chunk_id = hit["chunk_id"]
        if chunk_id not in merged:
            merged[chunk_id] = {**hit, "score": hit.get("score", 1 / (60 + rank))}
    for hit in direct:
        chunk_id = hit["chunk_id"]
        if chunk_id not in merged:
            merged[chunk_id] = {**hit, "score": 0.0}
        merged[chunk_id]["score"] += 1 / 61
    preferred = set(preferred)
    return sorted(
        merged.values(),
        key=lambda hit: (-round(hit["score"], 12), hit["document_id"] not in preferred, hit["chunk_id"]),
    )


def _clean_claim_line(line: str) -> str:
    return re.sub(r"[#*\x60]", "", line).strip()


def _claim_at(
    text: str,
    position: int | None,
    searchable_text: str | None = None,
) -> str:
    lines = text.split("\n")
    searchable_lines = (searchable_text or _mask_header_lines(text)).split("\n")
    line_index = None
    line_start = 0
    if position is not None:
        for index, line in enumerate(lines):
            line_end = line_start + len(line)
            if line_start <= position <= line_end and searchable_lines[index].strip():
                line_index = index
                break
            line_start = line_end + 1
    if line_index is None:
        line_index = next(
            (index for index, line in enumerate(searchable_lines) if line.strip()),
            len(lines) - 1,
        )

    claim = _clean_claim_line(lines[line_index]).replace("<br>", " ")
    if len(claim) < 10:
        next_index = next(
            (
                index
                for index in range(line_index + 1, len(lines))
                if searchable_lines[index].strip()
            ),
            None,
        )
        if next_index is not None:
            next_line = _clean_claim_line(lines[next_index]).replace("<br>", " ")
            if next_line:
                claim = f"{claim} {next_line}".strip()
    return claim[:200]


def _excerpt_around(text: str, position: int | None, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    center = position or 0
    start = max(0, center - limit // 2)
    end = min(len(text), start + limit)
    if end - start < limit:
        start = max(0, end - limit)
    return text[start:end]


def _citation(hit: dict, excerpt: str) -> Citation | None:
    document_id = hit.get("document_id")
    chunk_id = hit.get("chunk_id")
    pdf_page = hit.get("pdf_page")
    text = hit.get("text")
    if (
        not isinstance(document_id, str)
        or not document_id
        or not isinstance(chunk_id, str)
        or not chunk_id
        or type(pdf_page) is not int
        or pdf_page < 1
        or not isinstance(text, str)
        or not excerpt.strip()
    ):
        return None
    printed_page = hit.get("printed_page")
    if type(printed_page) is not int:
        printed_page = None
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "excerpt": excerpt,
    }


def _graph_paths(
    hit: dict,
    matched_disease_ids: list[str],
    actual_about_ids: set[str],
) -> list[GraphPath]:
    document_ref = f"Document:{hit['document_id']}"
    chunk_ref = f"Chunk:{hit['chunk_id']}"
    has_chunk: GraphEdge = {
        "source_ref": document_ref,
        "relationship_type": "HAS_CHUNK",
        "target_ref": chunk_ref,
    }
    about_ids = [disease_id for disease_id in matched_disease_ids if disease_id in actual_about_ids]
    if not about_ids:
        return [{"edges": [has_chunk]}]
    return [
        {
            "edges": [
                has_chunk,
                {
                    "source_ref": chunk_ref,
                    "relationship_type": "ABOUT",
                    "target_ref": f"Disease:{disease_id}",
                },
            ]
        }
        for disease_id in about_ids
    ]


def _search(tool_input: ToolInput, deps: dict) -> ToolResult:
    driver = deps["driver"]
    database = deps.get("database")
    embedder = deps.get("embedder")
    books = merge_books(deps["books"])

    disease_ids = _context_diseases(tool_input, books)
    if not disease_ids:
        return _result("NO_EVIDENCE", ["질문에서 지원 질병을 찾지 못함"])
    excluded_question_diseases = [
        disease_id for disease_id in detect_diseases(tool_input["question"], books)
        if _remove_question_exclusions(tool_input["question"], disease_id, books) != tool_input["question"]
    ]
    requested_focus_terms = focus_terms(tool_input["question"], books)
    unsplit_focus_terms = _focus_terms(tool_input["question"], books, split_brackets=False)
    bracket_terms = [term for term in requested_focus_terms if term not in unsplit_focus_terms]

    allowed_document_ids = list(tool_input["allowed_document_ids"])
    request = {
        "question": tool_input["question"],
        "region": tool_input["region"],
        "allowed_document_ids": allowed_document_ids,
    }
    if "preferred_document_ids" in tool_input:
        request["preferred_document_ids"] = [
            document_id
            for document_id in tool_input["preferred_document_ids"]
            if document_id in allowed_document_ids
        ]

    top_k = tool_input.get("top_k", 8)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        top_k = 8
    top_k = min(top_k, MAX_TOP_K)

    search_options = {
        "embedder": embedder,
        "mode": "hybrid",
        "k": max(top_k * 4, 30),
        "database": database,
    }
    if bracket_terms:
        search_options["extra_terms"] = bracket_terms
    searched = search_school_chunks(driver, request, books, **search_options)
    raw_hits = searched.get("hits", [])
    if not isinstance(raw_hits, list):
        raise TypeError("검색 결과 hits가 목록이 아닙니다.")

    names = _disease_names(books, disease_ids)
    direct_hits = _direct_candidates(
        driver,
        database,
        allowed_document_ids,
        disease_ids,
        names,
        requested_focus_terms,
        books.get("exclude_phrases", {}),
    )
    if direct_hits:
        raw_hits = _merge_direct_candidates(raw_hits, direct_hits, request.get("preferred_document_ids", []))
    # 질병 전용 지침·소제목 후보(2026-11-08, 주승우 팀장 승인 하에 Claude 추가). 2026 질병별 지침 청크는 본문에 병명이 없는
    # 경우가 많고(수족구 지침 '발열이 없고, 수포가 마른 후 복귀'), ABOUT 관계도 거의 없어(코로나19·홍역·수족구 0개)
    # 위 경로로는 찾지 못했다. 질문 질병의 전용 문서이거나 소제목에 병명이 있는 청크 중 핵심어가 든 것을 후보에 더한다.
    scoped_hits = _scoped_candidates(driver, database, allowed_document_ids, disease_ids, names, requested_focus_terms,
                                     books.get("exclude_phrases", {}))
    if scoped_hits:
        raw_hits = _merge_direct_candidates(raw_hits, scoped_hits, request.get("preferred_document_ids", []))

    allowed = set(allowed_document_ids)
    hits = []
    seen_chunk_ids = set()
    warnings = []
    if excluded_question_diseases:
        warnings.append("질문의 별도 질환 표현은 현재 질병 검색에서 제외되어 전체 비교는 확인 필요")
    for hit in raw_hits:
        if not isinstance(hit, dict):
            warnings.append("잘못된 검색 결과 청크를 건너뜀")
            continue
        if hit.get("document_id") not in allowed:
            continue
        chunk_id = hit.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in seen_chunk_ids:
            continue
        if not isinstance(hit.get("text"), str):
            warnings.append("원문이 없는 검색 결과 청크를 건너뜀")
            continue
        seen_chunk_ids.add(chunk_id)
        hits.append(hit)

    if not hits:
        return _result("NO_EVIDENCE", ["허용 문서에서 질병 근거를 찾지 못함"])

    about_by_chunk = _about_diseases(
        driver,
        [hit["chunk_id"] for hit in hits],
        allowed_document_ids,
        disease_ids,
        database,
    )
    known_disease_ids = _known_disease_ids(driver, disease_ids, database)

    findings: list[Finding] = []
    citations: list[Citation] = []
    graph_paths: list[GraphPath] = []
    found_disease_ids: set[str] = set()
    disease_name_chunk_seen = False
    focus_chunk_seen = False
    # 핵심어가 전혀 없는 경우에만 사용할 수 있도록 질병 근거 후보를 따로 보관한다.
    fallback_candidates = []
    for hit in hits:
        chunk_id = hit["chunk_id"]
        text = hit["text"]
        searchable_text = _mask_header_lines(text)
        exclude_phrases = books.get("exclude_phrases", {})
        searchable_by_disease = {
            disease_id: _mask_excluded_lines(
                searchable_text,
                exclude_phrases.get(disease_id, []),
            )
            for disease_id in disease_ids
        }
        text_matches = {
            disease_id
            for disease_id in disease_ids
            if disease_id
            in _text_disease_ids(
                searchable_by_disease[disease_id],
                [disease_id],
                names,
            )
        }
        # 질병 전용 문서·소제목의 병명도 본문 병명과 같게 본다(2026-11-08).
        text_matches |= _scope_disease_ids(hit, disease_ids, names, exclude_phrases)
        disease_name_chunk_seen = disease_name_chunk_seen or bool(text_matches)
        actual_about_ids = about_by_chunk.get(chunk_id, set())
        matched_ids = [
            disease_id
            for disease_id in disease_ids
            if disease_id in actual_about_ids or disease_id in text_matches
        ]
        if not matched_ids:
            continue

        focus_position = _focus_position(searchable_text, requested_focus_terms)
        if focus_position is None and _focus_position(hit.get("section") or "", requested_focus_terms) is not None:
            focus_position = 0   # 핵심어가 소제목에만 있는 짧은 청크(예: '복귀 기준' 절의 한 줄)는 청크 처음을 근거 위치로 본다
        is_fallback = bool(requested_focus_terms and focus_position is None)
        focus_chunk_seen = focus_chunk_seen or focus_position is not None
        if is_fallback:
            # ABOUT 관계가 있어도 해당 질병의 제외 문구만 있는 줄은 대체 근거에서 가린다.
            excluded = [
                phrase
                for disease_id in matched_ids
                for phrase in exclude_phrases.get(disease_id, [])
            ]
            searchable_text = _mask_excluded_lines(searchable_text, excluded)
            if not searchable_text.strip():
                continue
            # 목차의 점선·쪽수와 표 머리글만 있는 청크는 본문 근거로 대체하지 않는다.
            if re.search(r"(?:…|\.{3,})\s*\d", searchable_text):
                continue
            visible_lines = [line.strip() for line in searchable_text.splitlines() if line.strip()]
            if (
                visible_lines
                and all(line.startswith("|") for line in visible_lines)
                and not any(re.search(r"\d", line) for line in visible_lines)
            ):
                continue
        name_positions = [
            position
            for disease_id in matched_ids
            for name in names.get(disease_id, [])
            for position in _name_positions(searchable_by_disease[disease_id], name)
        ]
        name_position = min(name_positions) if name_positions else None
        is_table_chunk = bool(TABLE_CHUNK_RE.search(chunk_id))
        basis_position = (
            name_position
            if is_table_chunk
            else focus_position if focus_position is not None else name_position
        )
        claim = _claim_at(text, basis_position, searchable_text)
        citation = _citation(hit, _excerpt_around(text, basis_position))
        if not claim or citation is None:
            warnings.append("유효한 Citation을 만들 수 없는 청크를 건너뜀")
            continue

        finding = {
            "claim": claim,
            "chunk_ids": [chunk_id],
            "action_ids": [],
            # 텍스트의 질병명은 검색에 쓰되, 실제 Disease 노드가 없는 ID는 반환하지 않는다.
            "disease_ids": [disease_id for disease_id in matched_ids if disease_id in known_disease_ids],
            "concept_ids": [],
        }
        if is_fallback:
            # 허용 문서·질병 일치·제외 문구·인용 검증을 마친 후보만 순서대로 둔다.
            fallback_candidates.append((finding, citation, hit, matched_ids, actual_about_ids))
            continue
        findings.append(finding)
        citations.append(citation)
        graph_paths.extend(_graph_paths(hit, matched_ids, actual_about_ids))
        found_disease_ids.update(matched_ids)
        if len(findings) >= top_k:
            break

    used_fallback = False
    if not focus_chunk_seen and fallback_candidates:
        # 핵심어가 든 청크가 전혀 없을 때만 검색 순위의 상위 근거를 반환한다.
        used_fallback = True
        terms_text = ", ".join(requested_focus_terms)
        warnings.append(f"핵심어({terms_text}) 든 청크 없음→질병 관련 청크로 대체")
        for finding, citation, hit, matched_ids, actual_about_ids in fallback_candidates[:top_k]:
            findings.append(finding)
            citations.append(citation)
            graph_paths.extend(_graph_paths(hit, matched_ids, actual_about_ids))
            found_disease_ids.update(matched_ids)

    if not findings:
        if requested_focus_terms and disease_name_chunk_seen and not focus_chunk_seen:
            terms_text = ", ".join(requested_focus_terms)
            return _result(
                "NO_EVIDENCE",
                [f"질병명은 있으나 질문 핵심어({terms_text})가 든 청크 없음"],
            )
        return _result("NO_EVIDENCE", ["허용 문서에서 질병 근거를 찾지 못함", *warnings])

    missing_disease_ids = [disease_id for disease_id in disease_ids if disease_id not in found_disease_ids]
    warnings.extend(f"근거를 찾지 못한 disease_id: {disease_id}" for disease_id in missing_disease_ids)
    missing_nodes = [
        disease_id for disease_id in disease_ids
        if disease_id in found_disease_ids and disease_id not in known_disease_ids
    ]
    warnings.extend(
        f"DB에 Disease 노드가 없어 disease_id를 반환하지 않음: {disease_id}"
        for disease_id in missing_nodes
    )
    status: ToolStatus = (
        "PARTIAL" if missing_disease_ids or missing_nodes or used_fallback or excluded_question_diseases else "OK"
    )
    return {
        "tool_name": "disease_search",
        "status": status,
        "findings": findings,
        "citations": citations,
        "graph_paths": graph_paths,
        "warnings": warnings,
    }


def disease_search(tool_input: ToolInput, *, deps=None) -> ToolResult:
    """ToolInput을 받아 질병 Finding·Citation·GraphPath를 반환한다."""
    owned_driver = None
    result = _result("ERROR")
    try:
        if deps is None:
            deps = _default_deps()
            owned_driver = deps["driver"]
        result = _search(tool_input, deps)
    except Exception as error:  # noqa: BLE001
        result = _result("ERROR", [type(error).__name__])
    finally:
        if owned_driver is not None:
            try:
                owned_driver.close()
            except Exception as error:  # noqa: BLE001
                result["warnings"].append(type(error).__name__)
    return result


def _all_document_ids(driver, database) -> list[str]:
    query = """
MATCH (d:Document)
RETURN d.document_id AS document_id
ORDER BY d.document_id
"""
    try:
        records, _, _ = driver.execute_query(query, database_=database)
    except Exception as error:
        raise SearchError(f"{type(error).__name__}: Document 조회 실패") from error
    return [record["document_id"] for record in records if record.get("document_id")]


def main() -> None:
    parser = argparse.ArgumentParser(description="질병 사실 원문 검색 Tool")
    parser.add_argument("question", help="확인할 질병 질문")
    parser.add_argument("--save", help="입출력 fixture 이름")
    args = parser.parse_args()
    if args.save and not re.fullmatch(r"[A-Za-z0-9_-]+", args.save):
        parser.error("--save 이름에는 영문·숫자·하이픈·밑줄만 사용할 수 있습니다.")

    driver = None
    request: ToolInput = {
        "question": args.question,
        "task": "질병별 사실과 원문 근거 검색",
        "region": "NATIONAL",
        "allowed_document_ids": [],
    }
    result = _result("ERROR")
    try:
        deps = _default_deps()
        driver = deps["driver"]
        request["allowed_document_ids"] = _all_document_ids(driver, deps.get("database"))
        result = disease_search(request, deps=deps)
    except Exception as error:  # noqa: BLE001
        result = _result("ERROR", [type(error).__name__])
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception as error:  # noqa: BLE001
                result["warnings"].append(type(error).__name__)

    if args.save:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        for suffix, data in (("tool_input", request), ("tool_result", result)):
            path = FIXTURE_DIR / f"{args.save}_{suffix}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
