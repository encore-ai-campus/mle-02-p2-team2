import gzip
import importlib.util
import json
from pathlib import Path

import pytest


def backup_module():
    path = Path(__file__).resolve().parents[3] / "notebooks/respiratory/backup_readonly.py"
    spec = importlib.util.spec_from_file_location("respiratory_backup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Record(dict):
    def data(self):
        return dict(self)


class Result(list):
    def single(self):
        return self[0]


class FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query):
        if query.startswith("SHOW "):
            return Result()
        if query.startswith("MATCH (n) RETURN elementId"):
            return Result([Record(id="node-1", labels=["Chunk"], props={"chunk_id": "x"})])
        if query.startswith("MATCH (a)-[r]"):
            return Result([Record(start="node-1", end="node-1", type="TEST", props={})])
        if query == "MATCH (n) RETURN count(n) AS count":
            return Result([Record(count=1)])
        if query == "MATCH ()-[r]->() RETURN count(r) AS count":
            return Result([Record(count=1)])
        raise AssertionError(f"읽기 전용 질의 외 실행: {query}")


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def begin_transaction(self):
        return FakeTransaction()


class FakeDriver:
    def session(self, *, database, default_access_mode):
        assert default_access_mode == "READ"
        return FakeSession()


def test_readonly_backup_validates_and_does_not_overwrite(tmp_path):
    module = backup_module()
    path = tmp_path / "backup.jsonl.gz"
    result = module.export_database(FakeDriver(), "fake", path)
    assert result["nodes"] == 1 and result["relationships"] == 1
    assert module.validate_backup(path) == {"nodes": 1, "relationships": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        module.export_database(FakeDriver(), "fake", path)


def test_backup_with_missing_endpoint_fails_validation(tmp_path):
    module = backup_module()
    path = tmp_path / "broken.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for item in (
            {"kind": "node", "id": "node-1"},
            {"kind": "relationship", "start": "node-1", "end": "missing"},
            {"kind": "footer", "nodes": 1, "relationships": 1},
        ):
            stream.write(json.dumps(item) + "\n")
    with pytest.raises(ValueError, match="끝점이 누락"):
        module.validate_backup(path)
