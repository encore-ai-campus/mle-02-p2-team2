"""Neo4j ERD 대조: 속성 이름·관계 패턴·예방 법령 문서 사례를 읽기만 한다.

실행: uv run python notebooks/respiratory/erd_readonly_check.py
외부 AI를 호출하지 않으며 DB와 파일에 쓰지 않는다. JSON을 표준 출력한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from index.school.neo4j_local import get_driver

ROOT = Path(__file__).resolve().parents[2]
PREVENTION_DOCUMENTS = [
    "kdca_measles_2026", "kdca_diagnosis_reporting_2026", "kdca_hfmd_2026",
    "kdca_vaccine_preventable_2026", "moleg_infectious_disease_act_21521",
]
QUERIES = {
    "labels": "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY label",
    "properties": (
        "MATCH (n) UNWIND labels(n) AS label UNWIND keys(n) AS property "
        "RETURN label, collect(DISTINCT property) AS properties ORDER BY label"
    ),
    "patterns": (
        "MATCH (a)-[r]->(b) RETURN labels(a) AS start, type(r) AS type, "
        "labels(b) AS end, count(r) AS count ORDER BY type, start, end"
    ),
    "documents": (
        "MATCH (d:Document) WHERE d.document_id IN $docs "
        "OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk) "
        "RETURN d.document_id AS document_id, count(DISTINCT c) AS chunks ORDER BY document_id"
    ),
    "actions": (
        "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction) "
        "WHERE d.document_id IN $docs "
        "OPTIONAL MATCH (a)-[by:DONE_BY]->(role:SchoolRole) "
        "OPTIONAL MATCH (a)-[:FOR_DISEASE]->(dis:Disease) "
        "OPTIONAL MATCH (a)-[:INSTANCE_OF]->(concept:ActionConcept) "
        "RETURN d.document_id AS document_id, c.chunk_id AS chunk_id, a.action_id AS action_id, "
        "collect(DISTINCT role.role_id) AS roles, collect(DISTINCT by.basis) AS bases, "
        "collect(DISTINCT dis.disease_id) AS diseases, collect(DISTINCT concept.concept_id) AS concepts "
        "ORDER BY document_id, action_id LIMIT 12"
    ),
    "indexes": "SHOW INDEXES YIELD name, type, state RETURN name, type, state ORDER BY name",
    "constraints": (
        "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
        "RETURN name, type, labelsOrTypes, properties ORDER BY name"
    ),
}


def main():
    driver, database = get_driver(ROOT / ".env")
    try:
        result = {}
        for name, query in QUERIES.items():
            params = {"docs": PREVENTION_DOCUMENTS} if name in {"documents", "actions"} else {}
            rows, _, _ = driver.execute_query(query, database_=database, routing_="r", **params)
            result[name] = [record.data() for record in rows]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
