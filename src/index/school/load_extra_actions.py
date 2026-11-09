"""기존 청크에 학교 행동만 추가 적재·검증·삭제한다 (주승우 / school, 2026-11-08).

전체 적재기(load.py)는 문서·청크까지 다시 SET하므로, 표 행에서 빠진 행동 몇 개를 보충할 때는 이 스크립트를 쓴다.
- 문서·청크·사전 노드(Disease·ActionConcept)는 MATCH만 한다. 끝점이 없으면 트랜잭션 전체를 되돌린다(load._run).
- 노드·관계 쿼리는 load.node_query / load.edge_query를 그대로 쓴다(허용 속성·허용 관계만).
- delete는 명세에 적힌 action_id만 지운다(DETACH DELETE). 다른 노드는 건드리지 않는다.

    uv run python -m index.school.load_extra_actions <spec.json> [--apply | --verify | --delete]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from index.school.bundle import NODE_PROPERTIES
from index.school.load import _run, edge_query, node_query

EDGES = (("SUPPORTED_BY", "Chunk", "chunk_id"), ("INSTANCE_OF", "ActionConcept", "concept_id"),
         ("FOR_DISEASE", "Disease", "disease_ids"))


def action_rows(spec: dict) -> list[dict]:
    return [{p: a.get(p) for p in NODE_PROPERTIES["SchoolAction"]} for a in spec["actions"]]


def edge_rows(spec: dict) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = {}
    for a in spec["actions"]:
        for rel, label, field in EDGES:
            ends = a.get(field) or []
            for end in ([ends] if isinstance(ends, str) else ends):
                groups.setdefault(("SchoolAction", rel, label), []).append(
                    {"start_id": a["action_id"], "end_id": end})
    return groups


def apply(driver, spec: dict, database=None) -> dict:
    def tx_fn(tx):
        done = {"SchoolAction": _run(tx, node_query("SchoolAction"), action_rows(spec), "SchoolAction")}
        for (s, rel, e), rows in edge_rows(spec).items():
            done[rel] = _run(tx, edge_query(s, rel, e), rows, rel)
        return done
    with driver.session(database=database) as session:
        return session.execute_write(tx_fn)


def verify(driver, spec: dict, database=None) -> list[dict]:
    ids = [a["action_id"] for a in spec["actions"]]
    with driver.session(database=database) as session:
        return [dict(r) for r in session.run("""
MATCH (a:SchoolAction) WHERE a.action_id IN $ids
RETURN a.action_id AS action_id, a.action_text AS action_text,
       COLLECT { MATCH (a)-[:SUPPORTED_BY]->(c:Chunk)<-[:HAS_CHUNK]-(d:Document) RETURN d.document_id + ' / ' + c.chunk_id } AS evidence,
       COLLECT { MATCH (a)-[:INSTANCE_OF]->(k:ActionConcept) RETURN k.concept_id } AS concepts,
       COLLECT { MATCH (a)-[:FOR_DISEASE]->(x:Disease) RETURN x.disease_id } AS diseases
ORDER BY action_id""", ids=ids)]


def delete(driver, spec: dict, database=None) -> int:
    ids = [a["action_id"] for a in spec["actions"]]
    with driver.session(database=database) as session:
        return session.execute_write(lambda tx: tx.run(
            "MATCH (a:SchoolAction) WHERE a.action_id IN $ids DETACH DELETE a RETURN count(*) AS n",
            ids=ids).single()["n"])


def main() -> None:
    from rag.prevention_legal.neo4j_conn import connect

    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    mode = sys.argv[2] if len(sys.argv) > 2 else "--verify"
    driver = connect()
    try:
        if mode == "--apply":
            print("적재:", apply(driver, spec))
        elif mode == "--delete":
            print("삭제한 행동 수:", delete(driver, spec))
        for row in verify(driver, spec):
            print(json.dumps(row, ensure_ascii=False))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
