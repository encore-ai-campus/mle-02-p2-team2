"""B06 배치 적재·실제 키 검증 + B07 적재 장부.

참고: school_learning_reference.ipynb R06(UNWIND·MERGE), R07(content_hash·run_all), R08(gold_keys).
- 노드는 ID만 MERGE 키, 속성은 허용 목록을 SET한다. DONE_BY는 끝점으로 MERGE한 뒤 basis를 SET한다.
- 트랜잭션 2개: (1) 문서·청크·HAS_CHUNK·ABOUT, (2) 행동과 행동의 모든 관계. 각 문장이 처리한 행
  수가 입력 행 수와 다르면(MATCH 끝점 누락 등) 예외를 던져 그 트랜잭션 전체를 되돌린다.
  행동만 있고 근거·역할 관계가 빠진 '불완전한 경로'가 남지 않게 하기 위해서다.
- 적재 카운터만으로 완료를 판단하지 않고, verify_loaded_bundle로 담당 문서 범위의 실제 키를 다시 읽는다.
- 삭제·마이그레이션은 하지 않는다. DB에만 있는 관계(extra)는 보고만 한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from index.school.bundle import ALLOWED_EDGES, ID_FIELD, NODE_PROPERTIES, edge_key

ID_FIELDS_ANY = ("document_id", "chunk_id", "action_id", "disease_id", "role_id", "concept_id")


class LoadError(RuntimeError):
    """적재 중 입력 행 수와 처리 행 수가 다름 등."""


def bundle_hash(bundle: dict) -> str:
    body = json.dumps({"nodes": bundle["nodes"], "edges": bundle["edges"]}, ensure_ascii=False,
                      sort_keys=True, allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def node_query(label: str) -> str:
    if label not in NODE_PROPERTIES:
        raise LoadError(f"적재할 수 없는 노드 라벨: {label}")
    key = ID_FIELD[label]
    sets = ", ".join(f"n.{p} = row.{p}" for p in NODE_PROPERTIES[label] if p != key)
    return f"UNWIND $rows AS row MERGE (n:{label} {{{key}: row.{key}}}) SET {sets} RETURN count(*) AS n"


def edge_query(start_label: str, rel: str, end_label: str) -> str:
    """허용 패턴만 문자열로 만든다(라벨·관계 이름은 매개변수로 넘길 수 없어서 허용 목록으로 제한)."""
    if (start_label, rel, end_label) not in ALLOWED_EDGES:
        raise LoadError(f"허용하지 않은 관계: {(start_label, rel, end_label)}")
    tail = " SET r.basis = row.basis" if rel == "DONE_BY" else ""
    return (f"UNWIND $rows AS row "
            f"MATCH (s:{start_label} {{{ID_FIELD[start_label]}: row.start_id}}) "
            f"MATCH (e:{end_label} {{{ID_FIELD[end_label]}: row.end_id}}) "
            f"MERGE (s)-[r:{rel}]->(e){tail} RETURN count(*) AS n")


def _edge_groups(edges: list[dict], types: set[str]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = {}
    for edge in edges:
        if edge["type"] in types:
            groups.setdefault((edge["start_label"], edge["type"], edge["end_label"]), []).append(
                {"start_id": edge["start_id"], "end_id": edge["end_id"], **edge["props"]})
    return groups


def _run(tx, query: str, rows: list[dict], what: str) -> int:
    if not rows:
        return 0
    written = tx.run(query, rows=rows).single()["n"]
    if written != len(rows):
        # MATCH 끝점이 없으면 행이 조용히 사라진다. 트랜잭션 전체를 되돌린다.
        raise LoadError(f"{what}: 입력 {len(rows)}행 중 {written}행만 처리됨 - 끝점 누락")
    return written


def load_bundle(driver, bundle: dict, database=None) -> dict:
    """적재 패키지를 두 트랜잭션으로 넣는다. 반환: 단계별 처리 행 수."""
    nodes = bundle["nodes"]

    def sources(tx):
        done = {"Document": _run(tx, node_query("Document"), nodes["Document"], "Document"),
                "Chunk": _run(tx, node_query("Chunk"), nodes["Chunk"], "Chunk")}
        for (s, rel, e), rows in _edge_groups(bundle["edges"], {"HAS_CHUNK", "ABOUT"}).items():
            done[rel] = _run(tx, edge_query(s, rel, e), rows, rel)
        return done

    def actions(tx):
        done = {"SchoolAction": _run(tx, node_query("SchoolAction"), nodes["SchoolAction"],
                                     "SchoolAction")}
        types = {"SUPPORTED_BY", "DONE_BY", "INSTANCE_OF", "FOR_DISEASE"}
        for (s, rel, e), rows in _edge_groups(bundle["edges"], types).items():
            done[rel] = _run(tx, edge_query(s, rel, e), rows, rel)
        return done

    with driver.session(database=database) as session:
        result = session.execute_write(sources)
        result.update(session.execute_write(actions))
    return result


def _scope(bundle: dict) -> dict:
    return {"docs": sorted({d["document_id"] for d in bundle["nodes"]["Document"]})}


def fetch_scope_keys(driver, bundle: dict, database=None) -> dict:
    """담당 문서 범위(문서 ID와 그 ID로 시작하는 청크·행동)의 실제 노드·관계 키와 속성.

    embedding(1536개 실수)은 비교 대상이 아니므로 가져오지 않는다.
    """
    scope = _scope(bundle)
    in_scope = ("(n:Document AND n.document_id IN $docs) OR "
                "(n:Chunk AND any(d IN $docs WHERE n.chunk_id STARTS WITH d + ':')) OR "
                "(n:SchoolAction AND any(d IN $docs WHERE n.action_id STARTS WITH d + ':'))")
    node_records, _, _ = driver.execute_query(
        f"MATCH (n) WHERE {in_scope} RETURN labels(n)[0] AS label, n {{.*, embedding: null}} AS props",
        database_=database, **scope)
    coalesce = "coalesce(" + ", ".join(f"{{v}}.{f}" for f in ID_FIELDS_ANY) + ")"
    edge_records, _, _ = driver.execute_query(
        f"MATCH (n)-[r]->(m) WHERE {in_scope} "
        f"RETURN labels(n)[0] AS s_label, {coalesce.format(v='n')} AS s_id, type(r) AS rel, "
        f"labels(m)[0] AS e_label, {coalesce.format(v='m')} AS e_id, r.basis AS basis",
        database_=database, **scope)
    nodes = {(r["label"], r["props"][ID_FIELD[r["label"]]]): r["props"] for r in node_records
             if r["label"] in NODE_PROPERTIES}
    edges = {(r["s_label"], r["s_id"], r["rel"], r["e_label"], r["e_id"], r["basis"])
             for r in edge_records}
    return {"nodes": nodes, "edges": edges}


def verify_loaded_bundle(driver, bundle: dict, database=None) -> dict:
    """기대 키·속성과 실제 DB를 비교한다. 모두 비어 있으면 일치."""
    actual = fetch_scope_keys(driver, bundle, database)
    expected_nodes = {(label, row[ID_FIELD[label]]): row for label, rows in bundle["nodes"].items()
                      for row in rows}
    expected_edges = {edge_key(e) for e in bundle["edges"]}
    mismatched = []
    for key, row in expected_nodes.items():
        props = actual["nodes"].get(key)
        if props is None:
            continue
        want = {k: v for k, v in row.items() if v is not None}       # null 속성은 DB에 저장되지 않음
        have = {k: v for k, v in props.items() if k in NODE_PROPERTIES[key[0]]}
        if want != have:
            mismatched.append({"key": key, "diff": sorted(set(want.items()) ^ set(have.items()))})
    return {
        "missing_nodes": sorted(set(expected_nodes) - set(actual["nodes"])),
        "extra_nodes": sorted(set(actual["nodes"]) - set(expected_nodes)),
        "property_mismatch": mismatched,
        "missing_edges": sorted(expected_edges - actual["edges"], key=str),
        "extra_edges": sorted(actual["edges"] - expected_edges, key=str),
        "actual_counts": {"nodes": len(actual["nodes"]), "edges": len(actual["edges"])},
    }


def is_clean(report: dict) -> bool:
    return not any(report[k] for k in ("missing_nodes", "extra_nodes", "property_mismatch",
                                       "missing_edges", "extra_edges"))


# ---------------------------------------------------------------- B07 장부

def record_load_result(ledger: str | Path, *, bundle_dir: str, bundle: dict, status: str,
                       written: dict | None, report: dict | None, error: Exception | None = None,
                       attempt: int = 1) -> dict:
    """적재 결과를 로컬 JSONL 장부에 덧붙인다(공통 DB에 실행 상태 속성을 만들지 않는다).

    오류는 유형과 메시지 첫 줄만 남긴다(접속 문자열·키가 섞이지 않게 driver 예외 원문 전체를 쓰지 않음).
    """
    entry = {
        "timestamp_utc": datetime.now(UTC).isoformat(), "bundle_dir": bundle_dir,
        "bundle_sha256": bundle_hash(bundle), "status": status, "attempt": attempt,
        "documents": sorted(d["document_id"] for d in bundle["nodes"]["Document"]),
        "expected": {"nodes": len(bundle["expected"]["nodes"]), "edges": len(bundle["expected"]["edges"])},
        "written": written,
        "verify": None if report is None else {
            "clean": is_clean(report), **{k: len(v) if isinstance(v, list) else v
                                          for k, v in report.items()}},
        "error_type": type(error).__name__ if error else None,
        "error": str(error).splitlines()[0][:200] if isinstance(error, LoadError) else None,
    }
    path = Path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
    return entry


def load_with_retry(driver, bundle: dict, *, ledger, bundle_dir: str, database=None,
                    max_attempts: int = 2) -> dict:
    """적재 → 검증을 하고 장부에 남긴다. 일시 오류만 제한된 횟수로 다시 시도한다.

    LoadError(끝점 누락 등 데이터 문제)는 다시 해도 같으므로 바로 멈춘다.
    """
    from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

    for attempt in range(1, max_attempts + 1):
        try:
            written = load_bundle(driver, bundle, database)
        except LoadError as exc:
            record_load_result(ledger, bundle_dir=bundle_dir, bundle=bundle, status="FAILED",
                               written=None, report=None, error=exc, attempt=attempt)
            raise
        except (ServiceUnavailable, SessionExpired, TransientError) as exc:
            record_load_result(ledger, bundle_dir=bundle_dir, bundle=bundle, status="RETRYABLE_ERROR",
                               written=None, report=None, error=exc, attempt=attempt)
            if attempt == max_attempts:
                raise
            continue
        report = verify_loaded_bundle(driver, bundle, database)
        status = "SUCCESS" if is_clean(report) else "LOADED_WITH_DIFF"
        record_load_result(ledger, bundle_dir=bundle_dir, bundle=bundle, status=status,
                           written=written, report=report, attempt=attempt)
        return {"written": written, "report": report, "status": status}
    raise AssertionError("도달하지 않음")
