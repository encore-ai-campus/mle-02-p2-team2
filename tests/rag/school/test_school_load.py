"""B06 적재기의 DB 없는 단위 검사(가짜 트랜잭션). 실제 DB 대조는 school_load.ipynb에서 한다."""

import pytest

from index.school.load import LoadError, _run, bundle_hash, edge_query, node_query
from index.school.schema_init import schema_statements


class FakeTx:
    def __init__(self, written):
        self.written = written
        self.queries = []

    def run(self, query, rows):
        self.queries.append((query, rows))
        return self

    def single(self):
        return {"n": self.written}


def test_only_allowed_patterns_become_queries():
    assert "MERGE (s)-[r:DONE_BY]->(e) SET r.basis = row.basis" in edge_query(
        "SchoolAction", "DONE_BY", "SchoolRole")
    assert "SET" not in edge_query("Document", "HAS_CHUNK", "Chunk")
    with pytest.raises(LoadError):
        edge_query("Chunk", "DONE_BY", "SchoolRole")
    with pytest.raises(LoadError):
        node_query("Disease")                  # 사전 노드는 적재기가 만들지 않음


def test_node_query_merges_on_id_only():
    query = node_query("SchoolAction")
    assert "MERGE (n:SchoolAction {action_id: row.action_id})" in query
    assert "n.stage = row.stage" in query and "n.action_id = row.action_id" not in query


def test_row_count_mismatch_raises_for_rollback():
    rows = [{"start_id": "a", "end_id": "b"}, {"start_id": "a", "end_id": "missing"}]
    with pytest.raises(LoadError, match="입력 2행 중 1행"):
        _run(FakeTx(written=1), "Q", rows, "SUPPORTED_BY")
    assert _run(FakeTx(written=2), "Q", rows, "SUPPORTED_BY") == 2
    assert _run(FakeTx(written=0), "Q", [], "SUPPORTED_BY") == 0


def test_schema_is_idempotent_and_has_no_destructive_statements():
    statements = schema_statements()
    assert all("IF NOT EXISTS" in s for s in statements)
    assert not any(word in s.upper() for s in statements for word in ("DROP", "DELETE", "REMOVE"))
    assert sum(s.startswith("CREATE CONSTRAINT") for s in statements) == 6
    vector = [s for s in statements if "VECTOR INDEX" in s]
    assert len(vector) == 1 and "`vector.dimensions`: 1536" in vector[0] and "'cosine'" in vector[0]


def test_bundle_hash_ignores_report_fields():
    bundle = {"nodes": {"Document": []}, "edges": [], "warnings": ["a"]}
    assert bundle_hash(bundle) == bundle_hash({**bundle, "warnings": ["b"]})
