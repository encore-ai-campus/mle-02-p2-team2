"""검색 적합성 수정 후 3차 평가. 루트에서 실행; DB 쓰기와 제품 코드 조정 없음.

python notebooks/school/평가결과/golden_eval_relevance.py

기존 golden_eval.py의 검색 지표 산식과 golden_eval_tool/main.py를 재사용한다.
질문 임베딩은 실행 내 캐시로 고정하여 세 단계에서 같은 벡터를 사용한다.
"""

from __future__ import annotations

import collections
import hashlib
import json
import runpy
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from index.school.dictionaries import load_dictionaries  # noqa: E402
from rag.school import graph  # noqa: E402
from rag.school.capabilities import active_pages, in_scope  # noqa: E402
from rag.school.search import search_school_chunks  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "data/interim/school/eval_relevance_20261105"
GOLDEN = ROOT / "data/specs/golden/school.json"
ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025", "kdca_measles_2026",
           "kdca_diagnosis_reporting_2026", "moleg_infectious_disease_act_21521"]
PREFERRED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rank_metrics(ranks):
    """첫 정답 청크의 순위로 MRR@10을 계산한다. 미검색은 0이며 분모에 포함한다."""
    values = list(ranks)
    n = len(values)
    return {"n": n, "hit5": sum(r is not None and r <= 5 for r in values),
            "hit10": sum(r is not None and r <= 10 for r in values),
            "mrr10": sum(1 / r for r in values if r is not None and r <= 10) / n if n else None}


class CachedEmbedder:
    def __init__(self, base):
        self.base, self.vectors = base, {}

    def embed_query(self, question):
        if question not in self.vectors:
            self.vectors[question] = self.base.embed_query(question)
        return self.vectors[question]


def main():
    sys.stdout.reconfigure(line_buffering=True)
    OUT.mkdir(parents=True, exist_ok=True)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    expected = collections.defaultdict(set)
    for citation in golden["citations"]:
        expected[citation["q"]].add(citation["chunk_id"])
    sources = [GOLDEN, ROOT / "data/specs/source_manifest.csv", ROOT / "src/rag/school/relevance.py",
               ROOT / "src/rag/school/action_tool.py", ROOT / "src/rag/school/main_graph.py",
               ROOT / "src/rag/school/search.py", ROOT / "src/rag/school/graph.py",
               HERE / "golden_eval_tool.py", HERE / "golden_eval_main.py", Path(__file__).resolve()]
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    meta = {"started_at": datetime.now(timezone.utc).isoformat(), "read_only_db": True,
            "question_count": len(golden["questions"]), "scored_questions": len(expected),
            "citation_rows": len(golden["citations"]), "unique_expected_chunks": len(set().union(*expected.values())),
            "hashes": hashes, "allowed": ALLOWED, "preferred_raw_search": PREFERRED,
            "preferred_tool_main": [], "scope_policy": "raw ranking includes all allowed pages; Tool filters active manifest pages",
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()}
    save(OUT / "metadata.json", meta)
    # 산식 자체 확인. 실패/미검색 질문도 분모에 포함한다.
    assert rank_metrics([1, 2, None, 10]) == {"n": 4, "hit5": 2, "hit10": 3, "mrr10": 0.4}
    original_default = graph.default_deps
    deps = original_default()
    if deps["embedder"] is None:
        deps["driver"].close()
        raise RuntimeError("vector/hybrid 비교에는 실제 embedder가 필요하다.")
    cached = CachedEmbedder(deps["embedder"])
    meta["embedding_model"] = getattr(deps["embedder"], "model", type(deps["embedder"]).__name__)
    books = load_dictionaries()
    ranks, top, details = collections.defaultdict(dict), collections.defaultdict(dict), {}
    try:
        docs = deps["driver"].execute_query(
            "MATCH (d:Document) WHERE d.document_id IN $ids "
            "RETURN d.document_id AS id, d.version AS version, "
            "COUNT { (d)-[:HAS_CHUNK]->() } AS chunks", ids=ALLOWED, database_=deps.get("database"), routing_="r")[0]
        meta["db_documents"] = [r.data() for r in docs]
        need = sorted(set().union(*expected.values()))
        available = deps["driver"].execute_query(
            "MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN c.chunk_id AS id",
            ids=need, database_=deps.get("database"), routing_="r")[0]
        found_ids = {r["id"] for r in available}
        meta["expected_chunks_missing_from_db"] = sorted(set(need) - found_ids)
        scope = active_pages()
        meta["active_page_counts"] = {doc: len(pages) for doc, pages in scope.items()}
        meta["expected_chunks_outside_active_pages"] = sorted(
            cid for cid in need if not in_scope(cid.split(":")[0], int(cid.split(":")[1][1:]), scope))
        save(OUT / "metadata.json", meta)
        for mode in ("fulltext", "vector", "hybrid"):
            for q in golden["questions"]:
                if q["id"] not in expected:
                    continue
                started = time.perf_counter()
                result = search_school_chunks(deps["driver"], {
                    "question": q["question"], "allowed_document_ids": ALLOWED,
                    "preferred_document_ids": PREFERRED}, books, embedder=cached,
                    mode=mode, k=10, database=deps.get("database"))
                assert result["mode"] == mode
                hits = result["hits"]
                first = next((h["rank"] for h in hits if h["chunk_id"] in expected[q["id"]]), None)
                ranks[mode][q["id"]] = first
                top[mode][q["id"]] = [h["chunk_id"] for h in hits[:5]]
                details.setdefault(q["id"], {})[mode] = {
                    "first_rank": first, "seconds": round(time.perf_counter() - started, 3),
                    "hits": [{"chunk_id": h["chunk_id"], "rank": h["rank"], "pdf_page": h["pdf_page"],
                              "active": in_scope(h["document_id"], h["pdf_page"], scope)} for h in hits]}
            print("SEARCH", mode, rank_metrics(ranks[mode].values()), flush=True)
        save(OUT / "search.json", {"rank": ranks, "top": top, "detail": details,
                                   "metrics": {m: rank_metrics(r.values()) for m, r in ranks.items()}})
    finally:
        deps["driver"].close()

    def cached_deps():
        result = original_default()
        result["embedder"] = cached
        return result

    graph.default_deps = cached_deps
    try:
        for filename, output in [("golden_eval_tool.py", "tool.json"), ("golden_eval_main.py", "main.json")]:
            print("RUN", filename, flush=True)
            sys.argv = [str(HERE / filename), str(OUT / output)]
            runpy.run_path(str(HERE / filename), run_name="__main__")
    finally:
        graph.default_deps = original_default
    changed = [name for name, sha in hashes.items() if digest(ROOT / name) != sha]
    meta.update({"finished_at": datetime.now(timezone.utc).isoformat(), "unique_embedded_questions": len(cached.vectors),
                 "files_changed_during_eval": changed})
    save(OUT / "metadata.json", meta)
    assert not changed, "평가 중 코드/질문/선정 범위가 변경됐다. 결과 비교를 중단한다."
    print("Evaluation complete:", OUT, flush=True)


if __name__ == "__main__":
    main()
