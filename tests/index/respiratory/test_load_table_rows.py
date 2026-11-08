import json
import sys
from html import escape
from types import SimpleNamespace

import pytest

from index.respiratory import load
from index.school.bundle import edge_key


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    for name in ("TABLE_ROWS_PATH", "TABLE_REVIEW_PATH", "NORMALIZED_PATH", "BLOCKS_PATH", "BUNDLE_PATH"):
        monkeypatch.setattr(load, name, tmp_path / name)
    load.NORMALIZED_PATH.write_text("", encoding="utf-8")
    block = {
        "chunk_id": "kdca_respiratory_2026:p0116:b01",
        "document_id": "kdca_respiratory_2026", "text": "기존 본문",
        "pdf_page": 116, "printed_page": 96, "section": "기존 절",
    }
    load.BLOCKS_PATH.write_text(json.dumps(block) + "\n", encoding="utf-8")
    row = {
        "chunk_id": "kdca_respiratory_2026:p0113:t01r01",
        "document_id": "kdca_respiratory_2026", "pdf_page": 113,
        "printed_page": None, "table_title": "표 제목", "header": "| 항목 | 내용 |",
        "row_text": "| 등교 | A & B<br>24시간 |", "text": "사용하면 안 되는 가공 본문",
    }

    def write(rows=None, decisions=None):
        rows = rows if rows is not None else [row]
        decisions = decisions if decisions is not None else [(row["chunk_id"], "OK")]
        load.TABLE_ROWS_PATH.write_text(
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in rows), encoding="utf-8",
        )
        headers = ["chunk_id", "pdf_page", "table_title", "header", "row_text", "확인"]
        html = "<table><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
        for cid, decision in decisions:
            cells = [cid, "113", "검수표 제목은 사용하지 않음", "", "", decision]
            html += "<tr>" + "".join(f"<td><pre>{escape(v)}</pre></td>" for v in cells) + "</tr>"
        load.TABLE_REVIEW_PATH.write_text(html + "</table>", encoding="utf-8")

    write()
    return block, row, write


def test_only_exact_ok_uses_original_text_and_preserves_blocks(inputs):
    block, row, write = inputs
    ignored = {**row, "chunk_id": "kdca_respiratory_2026:p0113:t01r02"}
    write([row, ignored], [(row["chunk_id"], "OK"), (ignored["chunk_id"], " OK ")])
    actions, chunks, diseases = load.make_inputs(table_rows=True)
    assert chunks[0] == block
    assert len(chunks) == 2
    assert chunks[1] == {
        "chunk_id": row["chunk_id"], "document_id": row["document_id"],
        "text": "\n".join(row[k] for k in ("table_title", "header", "row_text")),
        "section": row["table_title"], "pdf_page": 113, "printed_page": None,
    }
    assert actions == [] and diseases == {}


@pytest.mark.parametrize("problem", ["review_duplicate", "json_duplicate", "missing", "existing"])
def test_id_errors_stop_before_bundle_write(inputs, problem):
    block, row, write = inputs
    if problem == "review_duplicate":
        write(decisions=[(row["chunk_id"], "OK")] * 2)
    elif problem == "json_duplicate":
        write([row, row])
    elif problem == "missing":
        write([])
    else:
        row["chunk_id"] = block["chunk_id"]
        write()
    with pytest.raises(ValueError):
        load.make_bundle(table_rows=True)
    assert not load.BUNDLE_PATH.exists()


@pytest.mark.parametrize("patch", [
    {"pdf_page": None}, {"pdf_page": True}, {"pdf_page": 114},
    {"printed_page": "93"}, {"document_id": "other"}, {"header": None},
])
def test_invalid_metadata_is_rejected(inputs, patch):
    _, row, write = inputs
    row.update(patch)
    write()
    with pytest.raises(ValueError):
        load.make_bundle(table_rows=True)


def test_bundle_adds_only_one_chunk_and_has_chunk(inputs):
    original = load.make_bundle()
    candidate = load.make_bundle(table_rows=True)
    for label in original["nodes"]:
        if label == "Chunk":
            assert len(candidate["nodes"][label]) == len(original["nodes"][label]) + 1
            assert all(row in candidate["nodes"][label] for row in original["nodes"][label])
        else:
            assert candidate["nodes"][label] == original["nodes"][label]
    old_edges = {edge_key(edge) for edge in original["edges"]}
    extra = [edge for edge in candidate["edges"] if edge_key(edge) not in old_edges]
    assert len(extra) == 1 and extra[0]["type"] == "HAS_CHUNK"


def test_dry_run_writes_bundle_without_db_or_embedding(inputs, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["load", "--table-rows"])
    monkeypatch.setattr(load, "get_driver", lambda *args: pytest.fail("DB 접속 금지"))
    monkeypatch.setattr(load.embed, "make_embedder", lambda: pytest.fail("외부 호출 금지"))
    load.main()
    assert len(json.loads(load.BUNDLE_PATH.read_text())["nodes"]["Chunk"]) == 2


def test_table_embedding_passes_only_new_chunks(inputs, monkeypatch):
    _, row, _ = inputs
    seen = []
    driver = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(sys, "argv", ["load", "--table-rows", "--embed"])
    monkeypatch.setattr(load, "get_driver", lambda *args: (driver, None))
    monkeypatch.setattr(load.embed, "make_embedder", lambda: object())

    def embed_chunks(driver, chunks, embedder, *, ledger, database):
        seen.extend(chunks)
        assert ledger == load.EMBED_LEDGER_PATH
        return {"embedded": [row["chunk_id"]], "skipped": []}

    monkeypatch.setattr(load.embed, "embed_chunks", embed_chunks)
    load.main()
    assert [chunk["chunk_id"] for chunk in seen] == [row["chunk_id"]]


@pytest.mark.parametrize("existing,matched", [(0, 1), (1, 1), (0, 0)])
def test_addition_transaction_creates_only_new_chunks(inputs, existing, matched):
    _, row, _ = inputs
    chunk = next(c for c in load.make_bundle(table_rows=True)["nodes"]["Chunk"]
                 if c["chunk_id"] == row["chunk_id"])
    queries = []

    class Transaction:
        def run(self, query, **params):
            queries.append((query, params))
            value = existing if len(queries) == 1 else matched
            return SimpleNamespace(single=lambda: {"n": value})

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute_write(self, function):
            return function(Transaction())

    driver = SimpleNamespace(session=lambda **kwargs: Session())
    if existing or not matched:
        with pytest.raises(ValueError):
            load.apply_table_chunks(driver, [chunk])
    else:
        assert load.apply_table_chunks(driver, [chunk]) == {"Chunk": 1, "HAS_CHUNK": 1}
        assert "CREATE (c:Chunk)" in queries[1][0]
        assert "SET c = row.props" in queries[1][0]
        assert "ABOUT" not in queries[1][0]
        assert queries[1][1]["rows"][0]["props"] == chunk
    if existing:
        assert len(queries) == 1
