"""B08: Chunk 우선 검색과 학교 행동 탐색 (그래프분석과검색 수업 S01~S05 응용).

- S01 전문검색(38-1 by_text): fulltext 인덱스 `chunk_text`(cjk) + HAS_CHUNK로 문서 확인·allowed 필터.
- S03 벡터(38-1 by_vector)·하이브리드(38-2): 벡터는 상위 후보를 넉넉히 뽑은 뒤 allowed로 거르고,
  모자라면 후보 수를 늘려 다시 찾는다(사후 필터만으로 비는 문제 대응). 두 점수는 범위가 달라 더하지 않고
  순위 기반 RRF로 합친다. 검색 점수는 관련성일 뿐 정확성·적용 가능성이 아니다.
- S02 출처 보존(38-2 with_source): 검색 결과는 처음부터 document_id·chunk_id·쪽수·원문을 구조로 들고 다닌다.
  문자열을 다시 해석하지 않는다.
- S04 그래프 확장(38-3 EXPAND·expanded_item): 검색된 청크 → SUPPORTED_BY ← 행동 → DONE_BY(basis)·INSTANCE_OF.
  역할·개념은 COLLECT 서브쿼리로 행동별로 모아 행이 곱해지지 않게 하고, 역할과 basis를 한 맵으로 묶어 짝을
  잃지 않는다. 같은 개념의 다른 출처 행동은 비교 질문에서만 따로 확장한다.
- S05 고정 도구(37-6 rows/@tool): 목적이 정해진 매개변수 Cypher 함수만 둔다. 자유 Text2Cypher는 쓰지 않는다.
- allowed_document_ids는 호출하는 쪽(요청 State)에서만 받는다. 모든 질의에 다시 적용한다. []이면 결과 없음.
  preferred는 같은 점수일 때의 순위 힌트일 뿐 allowed를 대신하지 않는다.
- DB 오류는 SearchError로 올리고, 결과 없음(빈 목록)과 구분한다.
"""

from __future__ import annotations

import re

from index.school.dictionaries import compare_key
from index.school.embed import VECTOR_INDEX

FULLTEXT_INDEX = "chunk_text"
LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
STOPWORDS = {"무엇을", "무엇", "어떻게", "어떤", "각각", "하나요", "해야", "있나요", "인가요", "되면", "학교에서"}


class SearchError(RuntimeError):
    """검색 중 DB 오류(결과 없음과 구분)."""


def _rows(driver, query: str, database=None, **params) -> list[dict]:
    """37-6 rows(): 매개변수 질의 → 사전 목록. DB 예외는 SearchError로 감싼다."""
    try:
        records, _, _ = driver.execute_query(query, database_=database, **params)
    except Exception as exc:  # 드라이버 예외 종류가 많아 한 곳에서 SearchError로 감싼다
        raise SearchError(f"{type(exc).__name__}: 검색 질의 실패") from exc
    return [record.data() for record in records]


# ---------------------------------------------------------------- 검색어

def escape_lucene(term: str) -> str:
    """사용자 문자열의 Lucene 연산자를 글자로 취급하게 한다(38-1 주의: 매개변수 ≠ 검색식 문법)."""
    return LUCENE_SPECIAL.sub(r"\\\1", term)


def build_search_terms(question: str, books: dict, extra: list[str] | None = None) -> list[str]:
    """질문은 보존하고, 질문 낱말 + 질문에 나온 역할·질병의 승인 표기·별칭으로 검색어를 만든다.

    LLM이 질문을 바꿔 쓰게 하지 않는다. 예: '보건교사' → '보건(담당)교사' 별칭도 함께 찾는다.
    """
    words = [w.strip(".,?!·…\"'()[]") for w in question.split()]
    terms = [w for w in words if len(w) >= 2 and w not in STOPWORDS]
    key = compare_key(question)
    for kind, field in (("role", "role_id"), ("disease", "disease_id")):
        for entry in books[kind]["entries"]:
            names = [entry["name"], *entry.get("aliases", [])]
            if any(compare_key(n) in key for n in names if len(compare_key(n)) >= 2):
                terms += names
    terms += extra or []
    return list(dict.fromkeys(t for t in terms if t))


def fulltext_expression(terms: list[str]) -> str:
    """검색어를 OR로 잇는다. 공백이 있는 표기는 구문 검색으로 묶는다."""
    parts = []
    for term in terms:
        escaped = escape_lucene(term)
        parts.append(f'"{escaped}"' if " " in term else escaped)
    return " OR ".join(parts)


# ---------------------------------------------------------------- 청크 검색 (S01·S02·S03)

HIT_FIELDS = """d.document_id AS document_id, d.title AS title, d.version AS version, d.region AS region,
       c.chunk_id AS chunk_id, c.text AS text, c.section AS section,
       c.pdf_page AS pdf_page, c.printed_page AS printed_page"""


def search_chunks_fulltext(driver, terms: list[str], allowed: list[str], k: int = 8,
                           database=None) -> list[dict]:
    """38-1 by_text의 학교 버전. allowed 필터는 LIMIT 전에 적용한다."""
    if not allowed or not terms:
        return []
    rows = _rows(driver, f"""
CALL db.index.fulltext.queryNodes($index, $expression) YIELD node AS c, score
MATCH (d:Document)-[:HAS_CHUNK]->(c)
WHERE d.document_id IN $allowed
RETURN {HIT_FIELDS}, score
ORDER BY score DESC, c.chunk_id LIMIT $k""", database, index=FULLTEXT_INDEX,
                 expression=fulltext_expression(terms), allowed=allowed, k=k)
    return [{**row, "method": "fulltext", "rank": i} for i, row in enumerate(rows, 1)]


def search_chunks_vector(driver, question: str, embedder, allowed: list[str], k: int = 8,
                         database=None, max_candidates: int = 400) -> list[dict]:
    """38-1 by_vector의 학교 버전. 후보 → allowed 필터 → 부족하면 후보를 두 배로(상한 있음)."""
    if not allowed:
        return []
    vector = embedder.embed_query(question)
    candidates = max(k * 5, 50)
    while True:
        rows = _rows(driver, f"""
CALL db.index.vector.queryNodes($index, $n, $vector) YIELD node AS c, score
MATCH (d:Document)-[:HAS_CHUNK]->(c)
WHERE d.document_id IN $allowed
RETURN {HIT_FIELDS}, score
ORDER BY score DESC, c.chunk_id LIMIT $k""", database, index=VECTOR_INDEX, n=candidates,
                     vector=vector, allowed=allowed, k=k)
        total = _rows(driver, "MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) AS n",
                      database)[0]["n"]
        if len(rows) >= k or candidates >= min(max_candidates, total):
            break
        candidates = min(candidates * 2, max_candidates)
    return [{**row, "method": "vector", "rank": i} for i, row in enumerate(rows, 1)]


def fuse_rrf(result_lists: list[list[dict]], preferred: list[str] | None = None, k: int = 8,
             constant: int = 60) -> list[dict]:
    """순위 기반 결합(RRF). preferred 문서는 같은 점수일 때만 앞에 둔다(순위 힌트)."""
    preferred = set(preferred or [])
    merged: dict[str, dict] = {}
    for hits in result_lists:
        for hit in hits:
            item = merged.setdefault(hit["chunk_id"], {**{f: hit[f] for f in hit
                                                          if f not in ("score", "rank", "method")},
                                                       "score": 0.0, "ranks": {}})
            item["score"] += 1.0 / (constant + hit["rank"])
            item["ranks"][hit["method"]] = hit["rank"]
    ordered = sorted(merged.values(), key=lambda h: (-round(h["score"], 12),
                                                     h["document_id"] not in preferred, h["chunk_id"]))
    return [{**h, "method": "+".join(sorted(h["ranks"])), "rank": i}
            for i, h in enumerate(ordered[:k], 1)]


def search_school_chunks(driver, request: dict, books: dict, *, embedder=None, mode: str = "hybrid",
                         k: int = 8, database=None, extra_terms: list[str] | None = None) -> dict:
    """요청의 허용 문서 안에서 청크를 찾는다. 반환: {'hits', 'terms', 'mode'}.

    mode: 'fulltext'(기준선) | 'vector' | 'hybrid'(fulltext+vector RRF). embedder가 없으면 fulltext.
    """
    allowed = list(request["allowed_document_ids"])
    terms = build_search_terms(request["question"], books, extra_terms)
    if mode != "fulltext" and embedder is None:
        mode = "fulltext"
    lists = []
    if mode in ("fulltext", "hybrid"):
        lists.append(search_chunks_fulltext(driver, terms, allowed, k * 2, database))
    if mode in ("vector", "hybrid"):
        lists.append(search_chunks_vector(driver, request["question"], embedder, allowed, k * 2,
                                          database))
    hits = fuse_rrf(lists, request.get("preferred_document_ids"), k)
    return {"hits": hits, "terms": terms, "mode": mode}


# ---------------------------------------------------------------- 그래프 확장·고정 조회 (S04·S05)

ACTION_FIELDS = """a.action_id AS action_id, a.action_text AS action_text, a.scope AS scope,
       a.stage AS stage, a.step_order AS step_order, a.when_text AS when_text,
       a.timing_text AS timing_text, a.modality AS modality,
       d.document_id AS document_id, c.chunk_id AS chunk_id,
       COLLECT { MATCH (a)-[r:DONE_BY]->(role:SchoolRole)
                 RETURN {role_id: role.role_id, name: role.name, basis: r.basis} } AS roles,
       COLLECT { MATCH (a)-[:INSTANCE_OF]->(k:ActionConcept) RETURN k.concept_id } AS concept_ids,
       COLLECT { MATCH (a)-[:FOR_DISEASE]->(x:Disease) RETURN x.disease_id } AS disease_ids"""


def expand_school_actions(driver, chunk_ids: list[str], allowed: list[str], *,
                          stages: list[str] | None = None, role_ids: list[str] | None = None,
                          limit: int = 100, database=None) -> list[dict]:
    """검색된 청크에 근거를 둔 학교 행동(행동별 1행). stages·role_ids는 선택 필터."""
    if not allowed or not chunk_ids:
        return []
    return _rows(driver, f"""
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction)
WHERE c.chunk_id IN $chunk_ids AND d.document_id IN $allowed
  AND ($stages IS NULL OR a.stage IN $stages)
  AND ($role_ids IS NULL OR EXISTS {{ (a)-[:DONE_BY]->(r:SchoolRole) WHERE r.role_id IN $role_ids }})
RETURN {ACTION_FIELDS}
ORDER BY document_id, chunk_id, a.step_order, action_id LIMIT $limit""", database,
                 chunk_ids=chunk_ids, allowed=allowed, stages=stages, role_ids=role_ids, limit=limit)


def get_role_actions(driver, role_ids: list[str], allowed: list[str], *, stages: list[str] | None = None,
                     limit: int = 100, database=None) -> list[dict]:
    """특정 담당·단계의 행동(검색 결과와 별개로 역할에서 출발). 범용 역할은 결과가 많으므로 limit 필수."""
    if not allowed or not role_ids:
        return []
    return _rows(driver, f"""
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction)-[:DONE_BY]->(r:SchoolRole)
WHERE r.role_id IN $role_ids AND d.document_id IN $allowed AND ($stages IS NULL OR a.stage IN $stages)
WITH DISTINCT a, c, d
RETURN {ACTION_FIELDS}
ORDER BY document_id, chunk_id, a.step_order, action_id LIMIT $limit""", database,
                 role_ids=role_ids, allowed=allowed, stages=stages, limit=limit)


def compare_action_sources(driver, concept_ids: list[str], allowed: list[str], *,
                           exclude_action_ids: list[str] | None = None, limit: int = 50,
                           database=None) -> list[dict]:
    """같은 개념의 다른 출처 행동(비교 질문 전용). 같은 개념은 비교 후보일 뿐 상호 증명이 아니다."""
    if not allowed or not concept_ids:
        return []
    return _rows(driver, f"""
MATCH (k:ActionConcept)<-[:INSTANCE_OF]-(a:SchoolAction)-[:SUPPORTED_BY]->(c:Chunk)<-[:HAS_CHUNK]-(d:Document)
WHERE k.concept_id IN $concept_ids AND d.document_id IN $allowed AND NOT a.action_id IN $exclude
WITH DISTINCT a, c, d
RETURN {ACTION_FIELDS}
ORDER BY document_id, action_id LIMIT $limit""", database, concept_ids=concept_ids, allowed=allowed,
                 exclude=exclude_action_ids or [], limit=limit)


def get_action_evidence(driver, action_ids: list[str], allowed: list[str], database=None) -> list[dict]:
    """행동의 실제 근거 경로(SUPPORTED_BY → Chunk ← HAS_CHUNK − Document)와 원문."""
    if not allowed or not action_ids:
        return []
    return _rows(driver, f"""
MATCH (a:SchoolAction)-[:SUPPORTED_BY]->(c:Chunk)<-[:HAS_CHUNK]-(d:Document)
WHERE a.action_id IN $action_ids AND d.document_id IN $allowed
RETURN a.action_id AS action_id, {HIT_FIELDS}
ORDER BY action_id, chunk_id""", database, action_ids=action_ids, allowed=allowed)
