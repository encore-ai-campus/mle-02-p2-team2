import json
import sys
from types import SimpleNamespace

import pytest

from rag.respiratory import text2cypher_tool as tool


class FakeLLM:
    def __init__(self, cypher):
        self.cypher = cypher
        self.messages = []

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.cypher)


class FakeSession:
    def __init__(self, query_type="r", rows=None, error=None):
        self.query_type = query_type
        self.rows = rows or []
        self.error = error
        self.explained = []
        self.executed = []
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query):
        self.explained.append(query)
        assert query.startswith("EXPLAIN ")
        return SimpleNamespace(consume=lambda: SimpleNamespace(query_type=self.query_type))

    def execute_read(self, work):
        self.read_calls += 1
        assert work.timeout == 10

        def run(query):
            assert isinstance(query, str)
            self.executed.append(query)
            if self.error:
                raise self.error
            return iter(SimpleNamespace(data=lambda row=row: row) for row in self.rows)

        return work(SimpleNamespace(run=run))


class FakeDriver:
    def __init__(self, **kwargs):
        self.fake_session = FakeSession(**kwargs)
        self.closed = False

    def session(self, **kwargs):
        assert kwargs["default_access_mode"] == "READ"
        return self.fake_session

    def close(self):
        self.closed = True


def test_clean_code_fence_and_trailing_semicolon():
    assert tool._clean_cypher(" \n```cypher\nMATCH (n) RETURN n;\n``` ") == "MATCH (n) RETURN n"
    assert tool._clean_cypher("```\nRETURN 1\n```") == "RETURN 1"


def test_clean_rejects_multiple_statements():
    with pytest.raises(ValueError, match="여러 Cypher"):
        tool._clean_cypher("RETURN 1; CREATE (n)")
    assert tool._clean_cypher("RETURN ';' AS value;") == "RETURN ';' AS value"


@pytest.mark.parametrize("query,expected", [
    ("RETURN 1", "RETURN 1\nLIMIT 50"),
    ("RETURN 1 LIMIT 100", "RETURN 1 LIMIT 50"),
    ("RETURN 1 LIMIT 20", "RETURN 1 LIMIT 20"),
    ("RETURN 'LIMIT 100' AS text", "RETURN 'LIMIT 100' AS text\nLIMIT 50"),
    ("RETURN 1 // LIMIT 100", "RETURN 1 // LIMIT 100\nLIMIT 50"),
])
def test_limit(query, expected):
    assert tool._ensure_limit(query) == expected


def test_write_query_is_only_explained_not_executed():
    driver = FakeDriver(query_type="w")
    result = tool.text2cypher_search("생성", {"driver": driver, "llm": FakeLLM("CREATE (n) RETURN n")})
    assert result["status"] == "NOT_READ_ONLY"
    assert "CREATE (n)" in result["cypher"]
    assert driver.fake_session.explained == ["EXPLAIN " + result["cypher"]]
    assert driver.fake_session.read_calls == 0
    assert driver.fake_session.executed == []
    assert tool.CITATION_WARNING in result["warnings"]


def test_read_query_returns_two_rows():
    rows = [{"document_id": "first"}, {"document_id": "second"}]
    driver = FakeDriver(rows=rows)
    llm = FakeLLM("MATCH (d:Document) RETURN d.document_id AS document_id")
    result = tool.text2cypher_search("문서", {"driver": driver, "llm": llm})
    assert result["status"] == "OK"
    assert result["row_count"] == 2 and result["rows"] == rows
    assert driver.fake_session.executed == [result["cypher"]]
    assert tool.SCHEMA in llm.messages[0][1]
    assert not driver.closed


def test_read_result_is_capped_even_when_driver_yields_more_rows():
    driver = FakeDriver(rows=[{"value": i} for i in range(60)])
    result = tool.text2cypher_search("목록", {"driver": driver, "llm": FakeLLM("RETURN 1 LIMIT 100")})
    assert result["row_count"] == 50
    assert result["cypher"].endswith("LIMIT 50")


def test_no_result():
    result = tool.text2cypher_search("없음", {"driver": FakeDriver(), "llm": FakeLLM("RETURN 1")})
    assert result["status"] == "NO_RESULT"
    assert result["row_count"] == 0
    assert tool.CITATION_WARNING in result["warnings"]


def test_execution_error_preserves_cypher_without_error_details():
    driver = FakeDriver(error=RuntimeError("비밀 접속 정보"))
    result = tool.text2cypher_search("조회", {"driver": driver, "llm": FakeLLM("RETURN 1")})
    assert result["status"] == "ERROR" and result["cypher"]
    assert "RuntimeError" in result["warnings"]
    assert "비밀 접속 정보" not in json.dumps(result, ensure_ascii=False)


def test_multiple_statements_never_reach_database():
    driver = FakeDriver()
    result = tool.text2cypher_search("조회", {"driver": driver, "llm": FakeLLM("RETURN 1; RETURN 2")})
    assert result["status"] == "NOT_READ_ONLY"
    assert result["cypher"] == "RETURN 1; RETURN 2"
    assert not driver.fake_session.explained and not driver.fake_session.executed


def test_default_driver_is_closed(monkeypatch):
    driver = FakeDriver(rows=[{"value": 1}])
    monkeypatch.setattr(tool, "_default_deps", lambda: {"driver": driver, "llm": FakeLLM("RETURN 1")})
    assert tool.text2cypher_search("조회")["status"] == "OK"
    assert driver.closed


def test_cli_save_and_name_validation(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tool, "FIXTURE_DIR", tmp_path)
    monkeypatch.setattr(tool, "text2cypher_search", lambda question: {"question": question, "status": "OK"})
    monkeypatch.setattr(sys, "argv", ["text2cypher", "조회", "--save", "T2C01"])
    tool.main()
    assert json.loads(capsys.readouterr().out)["question"] == "조회"
    assert (tmp_path / "T2C01_t2c_result.json").exists()
    monkeypatch.setattr(sys, "argv", ["text2cypher", "조회", "--save", "../other"])
    with pytest.raises(SystemExit) as error:
        tool.main()
    assert error.value.code == 2
