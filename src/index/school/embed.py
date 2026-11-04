"""Chunk.embedding 적재 - 팀 합의 임베딩(text-embedding-3-small, 1536차원).

- embedding은 의미 속성이 아니라 검색 인덱스용 값이다(온톨로지 v3.1 ④ Chunk '인덱스용').
- 질문 임베딩과 저장 임베딩은 반드시 같은 모델·차원을 쓴다. 모델 이름은 이 모듈 상수 하나로 관리하고
  DB 속성으로 저장하지 않는다(어떤 텍스트·모델로 만들었는지는 로컬 장부에 남긴다).
- 임베딩 대상은 Chunk.text 그대로(표 행은 제목·머리글·행). 텍스트 해시가 장부와 같고 DB에 벡터가
  있으면 다시 만들지 않는다.
참고: 38-1 셀 8·13(1536차원 벡터 인덱스, query embedding).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
VECTOR_INDEX = "chunk_embedding"


def make_embedder():
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model=EMBEDDING_MODEL)


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ledger(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row["chunk_id"]] = row
    return latest


def embed_chunks(driver, chunks: list[dict], embedder, *, ledger: str | Path, database=None) -> dict:
    """bundle의 Chunk 행에 임베딩을 넣는다. 반환: {'embedded': [...], 'skipped': [...]}."""
    ledger = Path(ledger)
    known = _ledger(ledger)
    ids = [c["chunk_id"] for c in chunks]
    records, _, _ = driver.execute_query(
        "MATCH (c:Chunk) WHERE c.chunk_id IN $ids "
        "RETURN c.chunk_id AS id, c.text AS text, c.embedding IS NOT NULL AS has_vec",
        ids=ids, database_=database)
    in_db = {r["id"]: r for r in records}
    missing = sorted(set(ids) - set(in_db))
    if missing:
        raise RuntimeError(f"DB에 없는 청크(먼저 적재): {missing}")
    todo = [cid for cid in ids
            if not in_db[cid]["has_vec"]
            or known.get(cid, {}).get("text_sha256") != text_sha(in_db[cid]["text"])
            or known.get(cid, {}).get("model") != EMBEDDING_MODEL]
    if todo:
        vectors = embedder.embed_documents([in_db[cid]["text"] for cid in todo])
        if any(len(v) != EMBEDDING_DIM for v in vectors):
            raise RuntimeError(f"임베딩 차원이 {EMBEDDING_DIM}이 아님")
        rows = [{"chunk_id": cid, "vec": vec} for cid, vec in zip(todo, vectors, strict=True)]
        result, _, _ = driver.execute_query(
            "UNWIND $rows AS row MATCH (c:Chunk {chunk_id: row.chunk_id}) "
            "CALL db.create.setNodeVectorProperty(c, 'embedding', row.vec) RETURN count(*) AS n",
            rows=rows, database_=database)
        if result[0]["n"] != len(rows):
            raise RuntimeError("임베딩 저장 건수 불일치")
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            for cid in todo:
                handle.write(json.dumps({"chunk_id": cid, "text_sha256": text_sha(in_db[cid]["text"]),
                                         "model": EMBEDDING_MODEL, "dim": EMBEDDING_DIM,
                                         "timestamp_utc": datetime.now(UTC).isoformat()},
                                        ensure_ascii=False) + "\n")
    check, _, _ = driver.execute_query(
        "MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN count(c) AS n, "
        "min(size(c.embedding)) AS min_dim, max(size(c.embedding)) AS max_dim",
        ids=ids, database_=database)
    if (check[0]["n"], check[0]["min_dim"], check[0]["max_dim"]) != (len(ids), EMBEDDING_DIM, EMBEDDING_DIM):
        raise RuntimeError(f"임베딩 확인 실패: {check[0].data()}")
    return {"embedded": todo, "skipped": sorted(set(ids) - set(todo))}
