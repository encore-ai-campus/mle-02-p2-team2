"""B01: DB 기능·스키마·검색 경로 확인(읽기 전용) + 담당 문서의 현재 유효 범위.

- `inspect_search_capabilities`는 MATCH/SHOW만 실행한다. DDL·쓰기·재적재를 하지 않는다.
- 문서에 v3.1이 적혀 있다는 사실이 아니라 **실DB의 속성 채움 비율**로 v3.1 경로 사용 여부를 정한다.
  stage/basis가 비어 있는 행동은 단계·담당 주장을 하지 않고 보류(PARTIAL) 사유로 넘긴다.
- `active_pages`: 담당 문서(교육부·경기도)의 manifest 선정쪽수. 선정쪽수 밖 청크가 DB에 남아 있어도
  (11/05 경기도 54·56쪽, 사용자 결정으로 DB에 유지) Tool 결과에는 쓰지 않는다. 다른 담당자 문서는
  유효 범위를 알 수 없으므로 거르지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

SCHOOL_DOCUMENTS = ("moe_school_manual_v3", "gyeonggi_school_guide_2025")
ROOT = Path(__file__).resolve().parents[3]


def _rows(driver, query: str, database=None, **params) -> list[dict]:
    records, _, _ = driver.execute_query(query, database_=database, routing_="r", **params)
    return [r.data() for r in records]


def active_pages(root: str | Path = ROOT) -> dict[str, set[int]]:
    """{담당 document_id: 선정쪽수 집합}. manifest 형식이 쪽수 목록이 아니면 그 문서는 거르지 않는다."""
    from index.school.id_map import load_manifest
    from preprocess.school.chunks import selected_pages

    manifest = load_manifest(root)
    scope = {}
    for document_id in SCHOOL_DOCUMENTS:
        spec = (manifest.get(document_id) or {}).get("선정쪽수", "")
        try:
            pages = set(selected_pages(spec))
        except ValueError:
            continue
        if pages:
            scope[document_id] = pages
    return scope


def in_scope(document_id: str, pdf_page: int | None, scope: dict[str, set[int]]) -> bool:
    pages = scope.get(document_id)
    return pages is None or pdf_page in pages


def inspect_search_capabilities(driver, database=None, document_ids: list[str] | None = None) -> dict:
    """확인 시점·대상·스키마 기능·데이터 범위·사용 가능한 검색 경로 보고서(비밀값 없음)."""
    document_ids = list(document_ids or SCHOOL_DOCUMENTS)
    report: dict = {"checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "database": database or "(driver 기본 DB)", "document_ids": document_ids}

    report["documents"] = _rows(driver, """
MATCH (d:Document) WHERE d.document_id IN $docs
RETURN d.document_id AS document_id, d.title AS title, d.version AS version, d.region AS region,
       COUNT { (d)-[:HAS_CHUNK]->() } AS chunks
ORDER BY document_id""", database, docs=document_ids)
    report["missing_documents"] = sorted(set(document_ids) - {d["document_id"] for d in report["documents"]})

    report["chunks"] = _rows(driver, """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE d.document_id IN $docs
RETURN d.document_id AS document_id, count(c) AS chunks,
       sum(CASE WHEN c.embedding IS NULL THEN 0 ELSE 1 END) AS embedded,
       min(c.pdf_page) AS min_page, max(c.pdf_page) AS max_page
ORDER BY document_id""", database, docs=document_ids)

    report["actions"] = _rows(driver, """
MATCH (d:Document)-[:HAS_CHUNK]->(:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction) WHERE d.document_id IN $docs
WITH d, collect(DISTINCT a) AS acts
UNWIND acts AS a
WITH d.document_id AS document_id, a
RETURN document_id, count(a) AS actions,
       sum(CASE WHEN a.stage IS NULL THEN 0 ELSE 1 END) AS with_stage,
       sum(CASE WHEN a.stage = 'UNSPECIFIED' THEN 1 ELSE 0 END) AS stage_unspecified,
       sum(CASE WHEN a.step_order IS NULL THEN 0 ELSE 1 END) AS with_step_order,
       sum(CASE WHEN EXISTS { (a)-[:DONE_BY]->() } THEN 1 ELSE 0 END) AS with_done_by,
       sum(CASE WHEN EXISTS { (a)-[:INSTANCE_OF]->() } THEN 1 ELSE 0 END) AS with_concept,
       sum(CASE WHEN EXISTS { (a)-[:FOR_DISEASE]->() } THEN 1 ELSE 0 END) AS with_disease
ORDER BY document_id""", database, docs=document_ids)

    report["done_by_basis"] = _rows(driver, """
MATCH (d:Document)-[:HAS_CHUNK]->(:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction)-[r:DONE_BY]->(:SchoolRole)
WHERE d.document_id IN $docs
RETURN r.basis AS basis, count(DISTINCT r) AS n ORDER BY basis""", database, docs=document_ids)

    report["labels"] = _rows(driver, """
UNWIND ['Document','Chunk','SchoolAction','SchoolRole','ActionConcept','Disease','DiseaseGroup'] AS label
CALL (label) { MATCH (n) WHERE label IN labels(n) RETURN count(n) AS n }
RETURN label, n""", database)
    report["relationships"] = _rows(driver, """
UNWIND ['HAS_CHUNK','SUPPORTED_BY','DONE_BY','INSTANCE_OF','FOR_DISEASE','ABOUT'] AS rel
CALL (rel) { MATCH ()-[r]->() WHERE type(r) = rel RETURN count(r) AS n }
RETURN rel, n""", database)
    report["indexes"] = _rows(driver, """
SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties
WHERE type IN ['FULLTEXT', 'VECTOR', 'RANGE'] AND name IN ['chunk_text', 'chunk_embedding', 'action_stage', 'action_scope']
RETURN name, type, state, labelsOrTypes, properties ORDER BY name""", database)

    index_state = {i["name"]: i["state"] for i in report["indexes"]}
    total_actions = sum(a["actions"] for a in report["actions"])
    with_stage = sum(a["with_stage"] for a in report["actions"])
    total_chunks = sum(c["chunks"] for c in report["chunks"])
    embedded = sum(c["embedded"] for c in report["chunks"])
    report["paths"] = {
        "fixed_cypher_role_stage": total_actions > 0,
        "fulltext": index_state.get("chunk_text") == "ONLINE",
        "vector": index_state.get("chunk_embedding") == "ONLINE" and embedded == total_chunks > 0,
        "v31_stage": total_actions > 0 and with_stage == total_actions,
        "v31_basis": any(b["basis"] for b in report["done_by_basis"]),
    }
    report["policy"] = (
        "역할·단계가 질문에 있으면 고정 Cypher(DONE_BY·stage)로 먼저 찾고, 질문 표현은 청크 검색(벡터가 준비되면 "
        "hybrid, 아니면 fulltext)으로 보완한다. stage가 비었거나 UNSPECIFIED인 행동은 단계 주장을 하지 않는다. "
        "basis가 없는 DONE_BY는 담당 근거로 쓰지 않는다.")
    report["active_pages"] = {k: f"{len(v)}쪽" for k, v in active_pages().items()}
    out_of_scope = _rows(driver, """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE d.document_id IN $docs
RETURN d.document_id AS document_id, c.pdf_page AS pdf_page, count(c) AS n""", database,
                         docs=[d for d in document_ids if d in SCHOOL_DOCUMENTS])
    scope = active_pages()
    report["chunks_outside_active_pages"] = sorted(
        (r["document_id"], r["pdf_page"], r["n"]) for r in out_of_scope
        if not in_scope(r["document_id"], r["pdf_page"], scope))
    return report
