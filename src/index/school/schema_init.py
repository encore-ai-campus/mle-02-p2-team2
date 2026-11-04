"""공통 DB 스키마(제약·인덱스)와 표준 사전 노드 초기화 - 온톨로지 v3.1 5-3절.

- 모든 문장은 IF NOT EXISTS / MERGE라 여러 번 실행해도 같다. 삭제·변경(DROP, DELETE)은 하지 않는다.
- 벡터 인덱스는 임베딩 모델·차원이 정해질 때까지 만들지 않는다(차원은 나중에 바꿀 수 없음).
- Chunk 전문 검색 인덱스는 한국어 검색을 위해 cjk 분석기(2글자 단위)를 쓴다.
- 사전 노드는 표준 entries만 MERGE하고 name·aliases를 SET한다. 승인 대기 개념은 넣지 않는다.
  공통 사전(data/specs/ids/)이 생기면 그 파일을 같은 함수에 넣으면 된다.
src/common/schema/로 옮길 때 이 파일을 그대로 옮기면 된다.
"""

from __future__ import annotations

from index.school.dictionaries import KINDS, neo4j_rows

CONSTRAINTS = {
    "document_id": ("Document", "document_id"),
    "chunk_id": ("Chunk", "chunk_id"),
    "disease_id": ("Disease", "disease_id"),
    "role_id": ("SchoolRole", "role_id"),
    "action_id": ("SchoolAction", "action_id"),
    "concept_id": ("ActionConcept", "concept_id"),
}
INDEXES = [
    ("CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text] "
     "OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}"),
    "CREATE INDEX action_stage IF NOT EXISTS FOR (a:SchoolAction) ON (a.stage)",
    "CREATE INDEX action_scope IF NOT EXISTS FOR (a:SchoolAction) ON (a.scope)",
]


def schema_statements() -> list[str]:
    statements = [f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                  for name, (label, prop) in CONSTRAINTS.items()]
    return statements + INDEXES


def apply_schema(driver, database=None) -> None:
    for statement in schema_statements():
        driver.execute_query(statement, database_=database)
    driver.execute_query("CALL db.awaitIndexes(300)", database_=database)


def schema_state(driver, database=None) -> dict:
    """현재 DB의 고유 제약과 인덱스 이름. 필요한 제약이 빠졌으면 missing에 나온다."""
    records, _, _ = driver.execute_query(
        "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN *", database_=database)
    unique = {(r["labelsOrTypes"][0], r["properties"][0]) for r in records
              # Neo4j 버전에 따라 UNIQUENESS(5.x 초기) 또는 NODE_PROPERTY_UNIQUENESS(이후)로 나온다.
              if r["type"] in ("UNIQUENESS", "NODE_PROPERTY_UNIQUENESS", "NODE_KEY")
              and r["labelsOrTypes"]}
    index_records, _, _ = driver.execute_query(
        "SHOW INDEXES YIELD name, type, state RETURN *", database_=database)
    return {"missing_constraints": sorted(set(CONSTRAINTS.values()) - unique),
            "indexes": {r["name"]: (r["type"], r["state"]) for r in index_records}}


def seed_dictionaries(driver, books: dict, database=None) -> dict[str, int]:
    """표준 사전 노드를 MERGE한다. 반환: 라벨별 입력 행 수(= DB에서 확인된 행 수)."""
    counts = {}
    for kind, (_, id_field, label, props) in KINDS.items():
        rows = neo4j_rows(kind, books)
        sets = ", ".join(f"n.{p} = row.{p}" for p in props if p != id_field)
        records, _, _ = driver.execute_query(
            f"UNWIND $rows AS row MERGE (n:{label} {{{id_field}: row.{id_field}}}) SET {sets} "
            "RETURN count(*) AS n", rows=rows, database_=database)
        if records[0]["n"] != len(rows):
            raise RuntimeError(f"{label} 사전 적재 건수 불일치: {records[0]['n']} != {len(rows)}")
        counts[label] = len(rows)
    return counts


def missing_dictionary_refs(driver, refs: list, database=None) -> list[tuple[str, str]]:
    """적재 패키지가 연결할 사전 노드 중 DB에 없는 것(있으면 적재하면 안 됨: MATCH에서 조용히 빠짐)."""
    missing = []
    for _, id_field, label, _ in KINDS.values():
        wanted = sorted(ident for lab, ident in refs if lab == label)
        if not wanted:
            continue
        records, _, _ = driver.execute_query(
            f"MATCH (n:{label}) WHERE n.{id_field} IN $ids RETURN n.{id_field} AS id",
            ids=wanted, database_=database)
        found = {r["id"] for r in records}
        missing += [(label, ident) for ident in wanted if ident not in found]
    return missing
