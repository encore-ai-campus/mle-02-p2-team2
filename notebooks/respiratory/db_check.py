"""호흡기 Neo4j 상태를 읽기 전용으로 점검하고 표와 JSON으로 저장한다."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from neo4j import READ_ACCESS

from index.school.neo4j_local import get_driver

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "data/specs/source_manifest.csv"
RESULT_PATH = Path(__file__).resolve().with_name("db_check_result.json")

ALLOWED_PATTERNS = {
    ("Document", "HAS_CHUNK", "Chunk"),
    ("Chunk", "ABOUT", "Disease"),
    ("SchoolAction", "SUPPORTED_BY", "Chunk"),
    ("SchoolAction", "FOR_DISEASE", "Disease"),
    ("SchoolAction", "DONE_BY", "SchoolRole"),
    ("SchoolAction", "INSTANCE_OF", "ActionConcept"),
}

LABEL_COUNTS_QUERY = """
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(DISTINCT n) AS count
ORDER BY label
"""
DOCUMENTS_QUERY = """
MATCH (d:Document)
RETURN d.document_id AS document_id, d.title AS title
ORDER BY document_id
"""
CHUNK_COUNTS_QUERY = """
MATCH (d:Document)
OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
RETURN d.document_id AS document_id, count(DISTINCT c) AS count
ORDER BY document_id
"""
ACTION_COUNTS_QUERY = """
MATCH (a:SchoolAction)
RETURN split(a.action_id, ":")[0] AS document_id, count(a) AS count
ORDER BY document_id
"""
RELATION_PATTERNS_QUERY = """
MATCH (s)-[r]->(t)
RETURN labels(s) AS start_labels, type(r) AS relationship_type,
       labels(t) AS end_labels, count(*) AS count
ORDER BY relationship_type, start_labels, end_labels
"""
UNSUPPORTED_ACTIONS_QUERY = """
MATCH (a:SchoolAction)
WHERE NOT EXISTS { MATCH (a)-[:SUPPORTED_BY]->(:Chunk) }
RETURN split(a.action_id, ":")[0] AS document_id, count(a) AS count
ORDER BY document_id
"""
CHUNKS_WITHOUT_DOCUMENT_QUERY = """
MATCH (c:Chunk)
WHERE NOT EXISTS { MATCH (:Document)-[:HAS_CHUNK]->(c) }
RETURN split(c.chunk_id, ":")[0] AS document_id, count(c) AS count
ORDER BY document_id
"""
DONE_BY_WITHOUT_BASIS_QUERY = """
MATCH (a:SchoolAction)-[r:DONE_BY]->(:SchoolRole)
WHERE r.basis IS NULL
RETURN split(a.action_id, ":")[0] AS document_id, count(r) AS count
ORDER BY document_id
"""
CHUNKS_WITHOUT_EMBEDDING_QUERY = """
MATCH (c:Chunk)
WHERE c.embedding IS NULL
RETURN split(c.chunk_id, ":")[0] AS document_id, count(c) AS count
ORDER BY document_id
"""
DISEASES_QUERY = """
MATCH (d:Disease)
RETURN d.disease_id AS disease_id, d.name AS name
ORDER BY disease_id
"""
ABOUT_COUNTS_QUERY = """
MATCH (doc:Document)-[:HAS_CHUNK]->(c:Chunk)-[:ABOUT]->(disease:Disease)
RETURN doc.document_id AS document_id, disease.disease_id AS disease_id,
       count(*) AS count
ORDER BY disease_id, document_id
"""
FOR_DISEASE_COUNTS_QUERY = """
MATCH (a:SchoolAction)-[:FOR_DISEASE]->(disease:Disease)
RETURN split(a.action_id, ":")[0] AS document_id,
       disease.disease_id AS disease_id, count(*) AS count
ORDER BY disease_id, document_id
"""
CONCEPTS_QUERY = """
MATCH (concept:ActionConcept)
RETURN concept.concept_id AS concept_id, concept.name AS name
ORDER BY concept_id
"""
INSTANCE_OF_COUNTS_QUERY = """
MATCH (a:SchoolAction)-[:INSTANCE_OF]->(concept:ActionConcept)
RETURN split(a.action_id, ":")[0] AS document_id,
       concept.concept_id AS concept_id, count(*) AS count
ORDER BY concept_id, document_id
"""
CONSTRAINTS_QUERY = """
SHOW CONSTRAINTS YIELD name, type
RETURN name, type
ORDER BY name
"""
INDEXES_QUERY = """
SHOW INDEXES YIELD name, type, state
RETURN name, type, state
ORDER BY name
"""


def read_rows(driver: Any, database: str | None, query: str) -> list[dict[str, Any]]:
    """Neo4j 읽기 세션으로 고정된 조회문만 실행한다."""
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        return [record.data() for record in session.run(query)]


def grouped_document_counts(
    rows: list[dict[str, Any]],
    document_ids: list[str],
) -> list[dict[str, Any]]:
    counts = {row["document_id"]: row["count"] for row in rows}
    all_ids = set(document_ids) | set(counts)
    return [
        {"document_id": document_id, "count": counts.get(document_id, 0)}
        for document_id in sorted(all_ids, key=lambda value: "" if value is None else str(value))
    ]


def count_map(
    rows: list[dict[str, Any]],
    key_fields: tuple[str, ...],
) -> dict[tuple[Any, ...], int]:
    return {
        tuple(row[field] for field in key_fields): row["count"]
        for row in rows
    }


def load_manifest_ids() -> set[str]:
    with MANIFEST_PATH.open(encoding="utf-8-sig", newline="") as manifest_file:
        return {
            row["document_id"].strip()
            for row in csv.DictReader(manifest_file)
            if row.get("document_id", "").strip()
        }


def collect_report(driver: Any, database: str | None) -> dict[str, Any]:
    documents = read_rows(driver, database, DOCUMENTS_QUERY)
    document_ids = [row["document_id"] for row in documents]
    document_id_set = set(document_ids)

    chunk_counts = {
        row["document_id"]: row["count"]
        for row in read_rows(driver, database, CHUNK_COUNTS_QUERY)
    }
    action_counts = {
        row["document_id"]: row["count"]
        for row in read_rows(driver, database, ACTION_COUNTS_QUERY)
    }
    document_counts = [
        {
            "document_id": row["document_id"],
            "title": row["title"],
            "chunk_count": chunk_counts.get(row["document_id"], 0),
            "school_action_count": action_counts.get(row["document_id"], 0),
        }
        for row in documents
    ]
    for extra_id in sorted((set(chunk_counts) | set(action_counts)) - document_id_set,
                           key=lambda value: "" if value is None else str(value)):
        document_counts.append({
            "document_id": extra_id,
            "title": None,
            "chunk_count": chunk_counts.get(extra_id, 0),
            "school_action_count": action_counts.get(extra_id, 0),
        })

    manifest_ids = load_manifest_ids()
    db_document_ids = {row["document_id"] for row in documents}
    manifest_comparison = {
        "manifest_only": sorted(manifest_ids - db_document_ids),
        "db_only": sorted(db_document_ids - manifest_ids),
    }

    relationship_patterns = read_rows(driver, database, RELATION_PATTERNS_QUERY)
    relationship_type_counts = Counter()
    outside_pattern_count = 0
    pattern_rows = []
    for row in relationship_patterns:
        start_labels = row["start_labels"]
        end_labels = row["end_labels"]
        relation_type = row["relationship_type"]
        count = row["count"]
        relationship_type_counts[relation_type] += count
        allowed = any(
            set(start_labels) == {start_label}
            and relation_type == expected_type
            and set(end_labels) == {end_label}
            for start_label, expected_type, end_label in ALLOWED_PATTERNS
        )
        if not allowed:
            outside_pattern_count += count
        pattern_rows.append({
            "start_labels": start_labels,
            "relationship_type": relation_type,
            "end_labels": end_labels,
            "count": count,
            "allowed": allowed,
        })

    unsupported_actions = read_rows(driver, database, UNSUPPORTED_ACTIONS_QUERY)
    chunks_without_document = read_rows(driver, database, CHUNKS_WITHOUT_DOCUMENT_QUERY)
    done_by_without_basis = read_rows(driver, database, DONE_BY_WITHOUT_BASIS_QUERY)
    integrity_ids = (
        document_id_set
        | {row["document_id"] for row in unsupported_actions}
        | {row["document_id"] for row in chunks_without_document}
        | {row["document_id"] for row in done_by_without_basis}
    )
    unsupported_counts = {
        row["document_id"]: row["count"] for row in unsupported_actions
    }
    unattached_chunk_counts = {
        row["document_id"]: row["count"] for row in chunks_without_document
    }
    basis_counts = {
        row["document_id"]: row["count"] for row in done_by_without_basis
    }
    integrity_by_document = [
        {
            "document_id": document_id,
            "school_actions_without_supported_by": unsupported_counts.get(document_id, 0),
            "chunks_without_has_chunk": unattached_chunk_counts.get(document_id, 0),
            "done_by_without_basis": basis_counts.get(document_id, 0),
        }
        for document_id in sorted(integrity_ids, key=lambda value: "" if value is None else str(value))
    ]

    chunks_without_embedding = grouped_document_counts(
        read_rows(driver, database, CHUNKS_WITHOUT_EMBEDDING_QUERY),
        document_ids,
    )

    diseases = read_rows(driver, database, DISEASES_QUERY)
    about_counts = count_map(
        read_rows(driver, database, ABOUT_COUNTS_QUERY),
        ("document_id", "disease_id"),
    )
    for_disease_counts = count_map(
        read_rows(driver, database, FOR_DISEASE_COUNTS_QUERY),
        ("document_id", "disease_id"),
    )
    disease_document_ids = sorted(
        document_id_set
        | {document_id for document_id, _ in about_counts}
        | {document_id for document_id, _ in for_disease_counts},
        key=lambda value: "" if value is None else str(value),
    )
    disease_relationship_counts = [
        {
            "disease_id": disease["disease_id"],
            "disease_name": disease["name"],
            "counts_by_document_id": {
                document_id: {
                    "ABOUT": about_counts.get((document_id, disease["disease_id"]), 0),
                    "FOR_DISEASE": for_disease_counts.get((document_id, disease["disease_id"]), 0),
                }
                for document_id in disease_document_ids
                if about_counts.get((document_id, disease["disease_id"]), 0)
                or for_disease_counts.get((document_id, disease["disease_id"]), 0)
            },
        }
        for disease in diseases
    ]

    concepts = read_rows(driver, database, CONCEPTS_QUERY)
    instance_counts = count_map(
        read_rows(driver, database, INSTANCE_OF_COUNTS_QUERY),
        ("document_id", "concept_id"),
    )
    concept_document_ids = sorted(
        document_id_set | {document_id for document_id, _ in instance_counts},
        key=lambda value: "" if value is None else str(value),
    )
    concept_instance_counts = [
        {
            "concept_id": concept["concept_id"],
            "concept_name": concept["name"],
            "instance_of_by_document_id": {
                document_id: instance_counts.get((document_id, concept["concept_id"]), 0)
                for document_id in concept_document_ids
                if instance_counts.get((document_id, concept["concept_id"]), 0) > 0
            },
        }
        for concept in concepts
    ]
    concepts_in_multiple_documents = []
    for concept in concepts:
        supporting_documents = [
            document_id
            for document_id in document_ids
            if instance_counts.get((document_id, concept["concept_id"]), 0) > 0
        ]
        if len(supporting_documents) >= 2:
            concepts_in_multiple_documents.append({
                "concept_id": concept["concept_id"],
                "concept_name": concept["name"],
                "document_ids": supporting_documents,
                "document_count": len(supporting_documents),
            })

    constraints = [
        {"name": row["name"], "type": row["type"], "state": "N/A"}
        for row in read_rows(driver, database, CONSTRAINTS_QUERY)
    ]
    indexes = read_rows(driver, database, INDEXES_QUERY)

    dictionary_property_keys = {}
    for label in ("Disease", "SchoolRole", "ActionConcept"):
        query = (
            f"MATCH (n:{label}) UNWIND keys(n) AS property_key "
            "RETURN collect(DISTINCT property_key) AS property_keys"
        )
        rows = read_rows(driver, database, query)
        dictionary_property_keys[label] = sorted(rows[0]["property_keys"]) if rows else []

    return {
        "database": database,
        "label_node_counts": read_rows(driver, database, LABEL_COUNTS_QUERY),
        "document_counts": document_counts,
        "manifest_comparison": manifest_comparison,
        "relationship_type_counts": [
            {"type": relation_type, "count": count}
            for relation_type, count in sorted(relationship_type_counts.items())
        ],
        "relationship_patterns": pattern_rows,
        "relationships_outside_allowed_patterns": outside_pattern_count,
        "integrity_counts_by_document": integrity_by_document,
        "chunks_without_embedding_by_document": chunks_without_embedding,
        "disease_relationship_counts_by_document": disease_relationship_counts,
        "action_concept_instance_counts_by_document": concept_instance_counts,
        "action_concepts_in_2plus_documents": concepts_in_multiple_documents,
        "constraints": constraints,
        "indexes": indexes,
        "dictionary_property_keys": dictionary_property_keys,
    }


def display_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def print_table(title: str, headers: list[str], rows: list[list[Any]]) -> None:
    print(f"\n{title}")
    values = [[display_value(value) for value in row] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in values))
        for index, header in enumerate(headers)
    ]
    print("| " + " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)) + " |")
    print("|-" + "-|-".join("-" * width for width in widths) + "-|")
    if not values:
        print("| " + " | ".join("없음".ljust(widths[index]) for index in range(len(headers))) + " |")
        return
    for row in values:
        print("| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(row)) + " |")


def print_report(report: dict[str, Any]) -> None:
    print(f"Database: {display_value(report['database'])}")
    print_table(
        "① 라벨별 노드 수",
        ["라벨", "노드 수"],
        [[row["label"], row["count"]] for row in report["label_node_counts"]],
    )
    print_table(
        "② Document별 Chunk·SchoolAction 수",
        ["document_id", "문서명", "Chunk", "SchoolAction"],
        [[row["document_id"], row["title"], row["chunk_count"], row["school_action_count"]]
         for row in report["document_counts"]],
    )
    print_table(
        "③ manifest와 DB Document 비교",
        ["구분", "document_id"],
        [["manifest에만 있음", value] for value in report["manifest_comparison"]["manifest_only"]]
        + [["DB에만 있음", value] for value in report["manifest_comparison"]["db_only"]],
    )
    print_table(
        "④ 관계 타입별 수",
        ["관계 타입", "수"],
        [[row["type"], row["count"]] for row in report["relationship_type_counts"]],
    )
    print(f"허용 6패턴 밖 관계 수: {report['relationships_outside_allowed_patterns']}")
    print_table(
        "허용 패턴 대조",
        ["시작 라벨", "관계 타입", "끝 라벨", "수", "허용"],
        [[row["start_labels"], row["relationship_type"], row["end_labels"],
          row["count"], row["allowed"]] for row in report["relationship_patterns"]],
    )
    print_table(
        "⑤ 연결 누락 (document_id별)",
        ["document_id", "SUPPORTED_BY 없는 SchoolAction", "HAS_CHUNK 없는 Chunk",
         "basis 없는 DONE_BY"],
        [[row["document_id"], row["school_actions_without_supported_by"],
          row["chunks_without_has_chunk"], row["done_by_without_basis"]]
         for row in report["integrity_counts_by_document"]],
    )
    print_table(
        "⑥ embedding 없는 Chunk (document_id별)",
        ["document_id", "Chunk 수"],
        [[row["document_id"], row["count"]]
         for row in report["chunks_without_embedding_by_document"]],
    )
    print_table(
        "⑦ Disease별 ABOUT·FOR_DISEASE 수 (document_id별)",
        ["disease_id", "질병명", "document_id별 관계 수 (0은 생략)"],
        [[row["disease_id"], row["disease_name"], row["counts_by_document_id"]]
         for row in report["disease_relationship_counts_by_document"]],
    )
    print_table(
        "⑧ ActionConcept별 INSTANCE_OF 수 (document_id별)",
        ["concept_id", "개념명", "document_id별 INSTANCE_OF 수 (0은 생략)"],
        [[row["concept_id"], row["concept_name"], row["instance_of_by_document_id"]]
         for row in report["action_concept_instance_counts_by_document"]],
    )
    print_table(
        "2개 이상 문서에 걸린 ActionConcept",
        ["concept_id", "개념명", "문서 수", "document_id 목록"],
        [[row["concept_id"], row["concept_name"], row["document_count"], row["document_ids"]]
         for row in report["action_concepts_in_2plus_documents"]],
    )
    print_table(
        "⑨ 제약조건 (state는 Neo4j SHOW CONSTRAINTS에 없어 N/A)",
        ["이름", "종류", "상태"],
        [[row["name"], row["type"], row["state"]] for row in report["constraints"]],
    )
    print_table(
        "⑨ 인덱스",
        ["이름", "종류", "상태"],
        [[row["name"], row["type"], row["state"]] for row in report["indexes"]],
    )
    print_table(
        "⑩ 사전 노드 실제 속성 키",
        ["라벨", "속성 키"],
        [[label, keys] for label, keys in report["dictionary_property_keys"].items()],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="호흡기 Neo4j 읽기 전용 DB 점검")
    parser.parse_args()

    driver, database = get_driver(PROJECT_ROOT / ".env")
    try:
        report = collect_report(driver, database)
    finally:
        driver.close()

    print_report(report)
    RESULT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nJSON 저장: {RESULT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"DB 점검 실패: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
