"""공동 Neo4j를 로컬 gzip JSONL로 보관한다. DB에는 쓰지 않는다.

원문·임베딩이 포함되므로 backups/의 Git 제외 규칙을 유지한다.
이 내보내기는 Neo4j dump가 아니고 복구 실행·동시 쓰기 없는 스냅샷을 보장하지 않는다.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from neo4j import READ_ACCESS

from index.school.neo4j_local import get_driver

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = Path(__file__).resolve().parent / "backups"


def write_record(stream, record):
    stream.write((json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


def validate_backup(path):
    """압축 해제, 중복 노드, 관계 끝점과 footer 개수를 점검한다."""
    nodes, edges, footer = set(), [], None
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            kind = item.get("kind")
            if kind == "node":
                if item["id"] in nodes:
                    raise ValueError("백업에 중복 노드 식별자가 있습니다.")
                nodes.add(item["id"])
            elif kind == "relationship":
                edges.append((item["start"], item["end"]))
            elif kind == "footer":
                if footer is not None:
                    raise ValueError("백업 footer가 중복됐습니다.")
                footer = item
    if footer is None or footer["nodes"] != len(nodes) or footer["relationships"] != len(edges):
        raise ValueError("백업 개수가 일치하지 않습니다.")
    if any(start not in nodes or end not in nodes for start, end in edges):
        raise ValueError("백업 관계의 끝점이 누락됐습니다.")
    return {"nodes": len(nodes), "relationships": len(edges)}


def export_database(driver, database, destination):
    """새 파일만 만들고 검증한다. 오류 시 이 함수가 만든 임시 파일만 정리한다."""
    destination = Path(destination)
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists():
        raise FileExistsError("같은 이름의 백업이 있습니다.")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as stream:  # noqa: SIM117
            with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
                with session.begin_transaction() as tx:
                    write_record(stream, {"kind": "header", "format": "neo4j-jsonl-v1",
                                          "captured_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                                          "isolation": "read committed; concurrent writes are not excluded"})
                    for row in tx.run("SHOW CONSTRAINTS YIELD name,type,labelsOrTypes,properties RETURN *"):
                        write_record(stream, {"kind": "constraint", **row.data()})
                    for row in tx.run("SHOW INDEXES YIELD name,type,state,labelsOrTypes,properties,options RETURN *"):
                        write_record(stream, {"kind": "index", **row.data()})
                    node_count = 0
                    for row in tx.run("MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props"):
                        write_record(stream, {"kind": "node", **row.data()})
                        node_count += 1
                    edge_count = 0
                    for row in tx.run("MATCH (a)-[r]->(b) RETURN elementId(a) AS start, elementId(b) AS end, "
                                      "type(r) AS type, properties(r) AS props"):
                        write_record(stream, {"kind": "relationship", **row.data()})
                        edge_count += 1
                    db_nodes = tx.run("MATCH (n) RETURN count(n) AS count").single()["count"]
                    db_edges = tx.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]
                    if (node_count, edge_count) != (db_nodes, db_edges):
                        raise ValueError("내보내기 중 DB 개수가 변경됐습니다.")
                    write_record(stream, {"kind": "footer", "nodes": node_count, "relationships": edge_count})
        counts = validate_backup(partial)
        # O_EXCL과 같은 효과로 새 백업만 확정한다. 같은 이름을 덮어쓰지 않는다.
        os.link(partial, destination)
        partial.unlink()
        digest = hashlib.sha256()
        with destination.open("rb") as saved:
            for block in iter(lambda: saved.read(1024 * 1024), b""):
                digest.update(block)
        return {**counts, "bytes": destination.stat().st_size, "sha256": digest.hexdigest()}
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    destination = BACKUP_DIR / f"neo4j_readonly_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl.gz"
    driver, database = get_driver(ROOT / ".env")
    try:
        result = export_database(driver, database, destination)
        print(json.dumps({"file": str(destination.relative_to(ROOT)), **result}, ensure_ascii=False))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
