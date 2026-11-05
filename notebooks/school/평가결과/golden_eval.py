import collections
import json
import sys

sys.path.insert(0, "src")
from index.school.dictionaries import load_dictionaries
from rag.school import run
from rag.school.graph import default_deps
from rag.school.search import search_school_chunks
from rag.school.validation_local import verify_against_db

G = json.load(open("data/specs/golden/school.json", encoding="utf-8"))
QS = {q["id"]: q for q in G["questions"]}
EXP = collections.defaultdict(set)
for c in G["citations"]:
    EXP[c["q"]].add(c["chunk_id"])
ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025", "kdca_measles_2026",
           "kdca_diagnosis_reporting_2026", "moleg_infectious_disease_act_21521"]
PREF = ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]
SKIP = {"S-G03", "S-G04", "S-D01", "S-L01"}   # 메인 그래프 라우팅 질문
deps = default_deps()
deps["trace"] = []
drv, db = deps["driver"], deps["database"]
books = load_dictionaries()

in_db = {r["id"] for r in drv.execute_query("MATCH (c:Chunk) RETURN c.chunk_id AS id", database_=db)[0]}
need = set().union(*EXP.values())
print("골든 청크", len(need), "DB에 있음", len(need & in_db))

# 1) 검색 순위
rank = collections.defaultdict(dict)
top = collections.defaultdict(dict)
for mode in ("fulltext", "vector", "hybrid"):
    for qid, chunks in EXP.items():
        hits = search_school_chunks(drv, {"question": QS[qid]["question"], "allowed_document_ids": ALLOWED,
                                          "preferred_document_ids": PREF}, books, embedder=deps["embedder"],
                                    mode=mode, k=10, database=db)["hits"]
        r = [h["rank"] for h in hits if h["chunk_id"] in chunks]
        rank[mode][qid] = min(r) if r else None
        top[mode][qid] = [h["chunk_id"] for h in hits[:5]]
print("\n[검색] 정답 청크 있는 문항 %d개" % len(EXP))
for mode in ("fulltext", "vector", "hybrid"):
    v = rank[mode]
    h5 = sum(1 for x in v.values() if x and x <= 5)
    h10 = sum(1 for x in v.values() if x)
    mrr = sum(1 / x for x in v.values() if x) / len(v)
    print(f"  {mode:9s} Hit@5 {h5}/{len(v)}  Hit@10 {h10}/{len(v)}  MRR {mrr:.3f}")

# 2) 현재 SchoolGraph 실행
rows = []
detail = {}
for qid, q in QS.items():
    if qid in SKIP:
        continue
    deps["trace"].clear()
    out = run({"question": q["question"], "region": q["region"], "allowed_document_ids": ALLOWED,
               "preferred_document_ids": PREF}, deps=deps)
    rep = verify_against_db(out, drv, db)
    cited = {c["chunk_id"] for c in out["citations"]} | {x for f in out["findings"] for x in f["chunk_ids"]}
    hit = len(cited & EXP[qid]) if EXP[qid] else None
    detail[qid] = {"cited": sorted(cited), "expected": sorted(EXP[qid]), "warnings": out["warnings"][:3], "findings": [(f["claim"][:90], bool(f["action_ids"])) for f in out["findings"][:40]]}
    rows.append({"id": qid, "split": q["split"], "want": q["status"], "got": out["status"],
                 "ok": out["status"] == q["status"], "hit": f"{hit}/{len(EXP[qid])}" if hit is not None else "-",
                 "n_find": len(out["findings"]), "n_act": sum(bool(f["action_ids"]) for f in out["findings"]),
                 "dbok": not rep["bad_citations"] and not rep["bad_findings"]})
print("\n[현재 SchoolGraph(v1.2) 실행]")
print("id     split want                  got                   ok   hit  find act dbok")
for r in rows:
    print(f"{r['id']} {r['split']:4s} {r['want']:21s} {r['got']:21s} {str(r['ok']):5s} {r['hit']:4s} {r['n_find']:4d} {r['n_act']:3d} {r['dbok']}")
ok = sum(r["ok"] for r in rows)
print(f"status 일치 {ok}/{len(rows)} | DB 대조 통과 {sum(r['dbok'] for r in rows)}/{len(rows)}")
withexp = [r for r in rows if r["hit"] != "-"]
full = sum(1 for r in withexp if r["hit"].split("/")[0] == r["hit"].split("/")[1])
anyhit = sum(1 for r in withexp if r["hit"].split("/")[0] != "0")
print(f"정답 청크를 인용한 문항(1개 이상) {anyhit}/{len(withexp)} | 전부 인용 {full}/{len(withexp)}")
json.dump({"rank": rank, "top": top, "rows": rows, "detail": detail}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
drv.close()
