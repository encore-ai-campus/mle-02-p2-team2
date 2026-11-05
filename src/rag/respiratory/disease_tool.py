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
FIXTURE_DIR = PROJECT_ROOT / "tests/fixtures/respiratory"
EXTRA_DISEASES_PATH = Path(__file__).with_name("extra_diseases.json")
MAX_TOP_K = 100
HEADER_RE = re.compile(r"^\W*\d{4}년도?\s*\S+(\s\S+)?\s*관리지침")
TABLE_CHUNK_RE = re.compile(r":t\d+r\d+$")
PARTICLES = ("에서", "으로", "에게", "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로", "란")


def _result(status: ToolStatus, warnings: list[str] | None = None) -> ToolResult:
    return {
        "tool_name": "disease_search",
        "status": status,
        "findings": [],
        "citations": [],
        "graph_paths": [],
        "warnings": list(warnings or []),
    }


def extend_books(books: dict) -> dict:
    """외부 질병 항목을 복사본에만 추가한다."""
    extended = deepcopy(books)
    extra = json.loads(EXTRA_DISEASES_PATH.read_text(encoding="utf-8"))
    entries = extended["disease"]["entries"]
    existing_ids = {entry["disease_id"] for entry in entries}
    for entry in extra["entries"]:
        disease_id = entry["disease_id"]
        if disease_id not in existing_ids:
            entries.append(entry)
            existing_ids.add(disease_id)
    extended["exclude_phrases"] = deepcopy(extra.get("exclude_phrases", {}))
    return extended


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


def _context_diseases(tool_input: ToolInput, books: dict) -> list[str]:
    disease_ids = list(detect_diseases(tool_input["question"], books))
    for entity_id in tool_input.get("context_entity_ids", []):
        if isinstance(entity_id, str) and entity_id.startswith("Disease:"):
            disease_id = entity_id.removeprefix("Disease:")
            if disease_id and disease_id not in disease_ids:
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


def focus_terms(question: str, books: dict) -> list[str]:
    """질문에서 조사·문장부호·질병명·검색 불용어를 뺀 핵심어를 만든다."""
    stopword_keys = {compare_key(word) for word in STOPWORDS}
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
    for raw_term in question.split():
        term = "".join(
            character
            for character in raw_term
            if not unicodedata.category(character).startswith("P")
        )
        for particle in PARTICLES:
            if term.endswith(particle):
                term = term[: -len(particle)]
                break
        key = compare_key(term)
        if len(term) < 2 or key in stopword_keys or key in disease_keys or key in seen:
            continue
        terms.append(term)
        seen.add(key)
    return terms


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
    books = extend_books(deps["books"])

    disease_ids = _context_diseases(tool_input, books)
    if not disease_ids:
        return _result("NO_EVIDENCE", ["질문에서 지원 질병을 찾지 못함"])
    requested_focus_terms = focus_terms(tool_input["question"], books)

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

    searched = search_school_chunks(
        driver,
        request,
        books,
        embedder=embedder,
        mode="hybrid",
        k=max(top_k * 4, 30),
        database=database,
    )
    raw_hits = searched.get("hits", [])
    if not isinstance(raw_hits, list):
        raise TypeError("검색 결과 hits가 목록이 아닙니다.")

    allowed = set(allowed_document_ids)
    hits = []
    seen_chunk_ids = set()
    warnings = []
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

    names = _disease_names(books, disease_ids)
    about_by_chunk = _about_diseases(
        driver,
        [hit["chunk_id"] for hit in hits],
        allowed_document_ids,
        disease_ids,
        database,
    )

    findings: list[Finding] = []
    citations: list[Citation] = []
    graph_paths: list[GraphPath] = []
    found_disease_ids: set[str] = set()
    disease_name_chunk_seen = False
    focus_chunk_seen = False
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
        if requested_focus_terms and focus_position is None:
            continue
        focus_chunk_seen = focus_chunk_seen or focus_position is not None
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

        findings.append(
            {
                "claim": claim,
                "chunk_ids": [chunk_id],
                "action_ids": [],
                "disease_ids": matched_ids,
                "concept_ids": [],
            }
        )
        citations.append(citation)
        graph_paths.extend(_graph_paths(hit, matched_ids, actual_about_ids))
        found_disease_ids.update(matched_ids)
        if len(findings) >= top_k:
            break

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
    status: ToolStatus = "PARTIAL" if missing_disease_ids else "OK"
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
