import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from neo4j import Record
from neo4j.graph import Graph, Node, Relationship
from neo4j.graph import Path as GraphPath

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("respiratory_t2c_demo", ROOT / "notebooks/respiratory/text2cypher_demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


@pytest.fixture
def schema():
    return {
        "properties": {key: [] for key in demo.LABEL_KEYS}, "relationships": [],
        "dictionaries": {"Disease": [{"id": "INFLUENZA"}, {"id": "SCARLET_FEVER"}],
                         "ActionConcept": [{"id": "CONTACT_GUARDIAN"}]},
        "action_values": [{"scope": "NAMED_DISEASES"}], "done_by_basis": [{"basis": "EXPLICIT"}],
        "semantics": [],
    }


class Driver:
    def __init__(self, rows=(), query_type="r", operator="ProduceResults"):
        self.rows = list(rows)
        self.query_type = query_type
        self.operator = operator
        self.explains = []
        self.executions = []

    def session(self, **kwargs):
        assert kwargs["default_access_mode"] == "READ"
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query):
        assert query.text.startswith("EXPLAIN ")
        self.explains.append(query.text)
        return SimpleNamespace(consume=lambda: SimpleNamespace(
            query_type=self.query_type, plan={"operatorType": self.operator, "children": []}))

    def execute_read(self, work):
        assert work.timeout == 10

        def run(query):
            self.executions.append(query)
            return iter(self.rows)

        return work(SimpleNamespace(run=run))


def test_generation_is_separate_and_uses_actual_schema(schema):
    calls = []

    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content="MATCH (d:Document) RETURN d.document_id LIMIT 50")

    output = demo.generate_cypher("새 질문", schema, SimpleNamespace(invoke=invoke))
    assert output["generated_cypher"].startswith("MATCH")
    assert "SCARLET_FEVER" in calls[0][0][1]
    assert calls[0][1] == ("user", "새 질문")


@pytest.mark.parametrize("query", [
    "MATCH (d:Document) DELETE d RETURN d LIMIT 50",
    "MATCH (d:Document) RETURN d LIMIT 50; CREATE (x)",
    "MATCH (d:Document) CALL apoc.load.json('https://example.invalid') YIELD value RETURN value LIMIT 50",
    "MATCH p=(d:Document)-[:HAS_CHUNK*]->(c:Chunk) RETURN p LIMIT 50",
    "MATCH (c:Chunk) RETURN c.embedding LIMIT 50",
    "MATCH (c:Chunk) RETURN c['embedding'] AS vector LIMIT 50",
    "MATCH (d:Document) RETURN d",
    "MATCH (d:Document) RETURN d LIMIT 100",
    "MATCH (d:Secret) RETURN d LIMIT 50",
])
def test_unsafe_queries_never_reach_execution_or_explain(schema, query):
    driver = Driver()
    validation = demo.validate_cypher(driver, None, query, schema)
    assert validation["status"] == "REJECTED"
    assert driver.explains == [] and driver.executions == []


def test_read_subquery_union_is_supported_without_rewriting(schema):
    query = (
        "MATCH (d:Disease {disease_id:'INFLUENZA'}) CALL { WITH d "
        "MATCH p=(c:Chunk)-[:ABOUT]->(d) RETURN p UNION WITH d "
        "MATCH p=(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction)-[:FOR_DISEASE]->(d) RETURN p } "
        "RETURN p LIMIT 50"
    )
    driver = Driver()
    validation = demo.validate_cypher(driver, None, query, schema)
    assert validation["status"] == "VALID"
    assert validation["cypher"] == query
    assert demo.prepare_browser_query(validation) == query


def test_server_query_type_is_required(schema):
    driver = Driver(query_type="rw")
    result = demo.validate_cypher(driver, None, "MATCH (d:Document) RETURN d LIMIT 50", schema)
    assert result["status"] == "REJECTED"
    assert len(driver.explains) == 1 and not driver.executions


def test_cartesian_plan_is_rejected(schema):
    driver = Driver(operator="CartesianProduct")
    result = demo.validate_cypher(driver, None, "MATCH (d:Document),(c:Chunk) RETURN d,c LIMIT 50", schema)
    assert result["status"] == "REJECTED"


def test_unknown_ids_are_not_invented(schema):
    result = demo.validate_cypher(Driver(), None,
        "MATCH (d:Disease {disease_id:'ALIEN_FEVER'}) RETURN d LIMIT 50", schema)
    assert result["status"] == "UNSUPPORTED"


def test_forged_validation_cannot_execute_a_write(schema):
    driver = Driver()
    forged = {"status": "VALID", "query_type": "r", "cypher": "CREATE (n) RETURN n LIMIT 50"}
    with pytest.raises(ValueError):
        demo.execute_cypher(driver, None, forged, schema)
    assert not driver.executions


def test_native_records_preserved_and_exact_query_executed(schema):
    record = Record({"document_id": "first"})
    driver = Driver([record] * 51)
    query = "MATCH (d:Document) RETURN d.document_id AS document_id LIMIT 50"
    validation = demo.validate_cypher(driver, None, query, schema)
    output = demo.execute_cypher(driver, None, validation, schema)
    assert output["records"][0] is record
    assert len(output["records"]) == 50 and output["truncated"]
    assert driver.executions == [query]
    assert output["executed_cypher"] == demo.prepare_browser_query(validation)


def test_graph_serialization_preserves_direction_and_omits_embeddings():
    graph = Graph()
    doc = Node(graph, "doc-element", 1, {"Document"}, {"document_id": "doc", "title": "문서"})
    chunk = Node(graph, "chunk-element", 2, {"Chunk"},
                 {"chunk_id": "doc:p0001:b01", "text": "가" * 300, "pdf_page": 1, "embedding": [0.1] * 1536})
    rel = MagicMock(spec=Relationship)
    rel.start_node, rel.end_node, rel.type = doc, chunk, "HAS_CHUNK"
    rel.keys.return_value = []
    rel.__iter__.return_value = iter([])
    path = MagicMock(spec=GraphPath)
    path.nodes, path.relationships = (doc, chunk), (rel,)
    records = [Record({"path": path})]
    output = demo.serialize_records(records)
    assert records[0]["path"] is path
    edge = output["graph"]["relationships"][0]
    assert (edge["source"], edge["type"], edge["target"]) == ("Document:doc", "HAS_CHUNK", "Chunk:doc:p0001:b01")
    assert "embedding" not in json.dumps(output)
    assert len(output["graph"]["nodes"][1]["properties"]["text"]) == 200


def test_schema_context_does_not_include_connection_secrets(schema):
    schema["connection"] = {"uri": "must-not-be-sent", "password": "secret"}
    context = demo.build_schema_context(schema)
    assert "must-not-be-sent" not in context and "secret" not in context


def test_long_numeric_array_is_not_logged_even_with_an_unknown_alias():
    output = demo.serialize_records([Record({"vector": [0.123] * 1536})])
    assert output["rows"][0]["vector"] == {"omitted": "long numeric array", "length": 1536}
    assert "0.123" not in json.dumps(output)
