"""질문셋 v1로 action_search(ToolResult)를 평가한다. 프로젝트 루트에서 실행.

    uv run python notebooks/school/평가결과/golden_eval_tool.py <결과.json>

- 대상: 정답 청크가 있는 문항 전부(기대 Tool이 action_search가 아닌 문항도 Tool 단독 능력 참고용으로 돌리되
  표에 기대 Tool을 함께 적는다).
- 지표: 정답 청크를 1개 이상 인용 / 전부 인용, Tool status, Finding 수, DB 대조 제외 수, GraphPath 수.
- 정답 청크는 채점에만 쓴다(검색·선별 코드에 넣지 않는다).
"""

import collections
import json
import sys
import time

sys.path.insert(0, "src")
from rag.school.action_tool import action_search  # noqa: E402
from rag.school.graph import default_deps  # noqa: E402

G = json.load(open("data/specs/golden/school.json", encoding="utf-8"))
EXP = collections.defaultdict(set)
for c in G["citations"]:
    EXP[c["q"]].add(c["chunk_id"])
ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025", "kdca_measles_2026",
           "kdca_diagnosis_reporting_2026", "moleg_infectious_disease_act_21521"]
TASK = "학교가 수행하는 행동, 역할, 단계 및 근거 찾기"

deps = default_deps()
deps["trace"] = []
rows, detail = [], {}
for q in G["questions"]:
    if not EXP[q["id"]]:
        continue
    deps["trace"].clear()
    started = time.perf_counter()
    result = action_search({"question": q["question"], "task": TASK, "region": q["region"],
                            "allowed_document_ids": ALLOWED}, deps)
    elapsed = time.perf_counter() - started
    cited = {c["chunk_id"] for c in result["citations"]}
    cited_order = [c["chunk_id"] for c in result["citations"]]
    first_rank = next((i for i, cid in enumerate(cited_order, 1) if cid in EXP[q["id"]]), None)
    hit = len(cited & EXP[q["id"]])
    trace = deps["trace"][-1] if deps["trace"] else {}
    rows.append({"id": q["id"], "split": q["split"], "route": q["route"], "status": result["status"],
                 "hit": hit, "total": len(EXP[q["id"]]), "n_find": len(result["findings"]),
                 "n_act": sum(bool(f["action_ids"]) for f in result["findings"]),
                 "paths": len(result["graph_paths"]), "dropped": len((trace.get("db_report") or {}).get("bad_findings", {})),
                 "first_citation_rank": first_rank,
                 "dbok": not any(trace["db_report"].values()) if "db_report" in trace else None,
                 "sec": round(elapsed, 1)})
    detail[q["id"]] = {"cited": sorted(cited), "expected": sorted(EXP[q["id"]]), "warnings": result["warnings"],
                       "claims": [f["claim"][:120] for f in result["findings"]], "trace": trace,
                       "cited_order": cited_order}
    r = rows[-1]
    print(f"{r['id']} {r['split']:4s} {r['route'][:12]:12s} {r['status']:11s} {r['hit']}/{r['total']} "
          f"F{r['n_find']:2d}(A{r['n_act']}) P{r['paths']} drop{r['dropped']} {r['sec']}s")
withexp = rows
anyh = sum(r["hit"] > 0 for r in withexp)
full = sum(r["hit"] == r["total"] for r in withexp)
act = [r for r in rows if r["route"] == "action_search"]
print(f"\n전체 {len(rows)}문항: 정답 1개 이상 인용 {anyh}/{len(rows)}, 전부 {full}/{len(rows)}")
print(f"기대 Tool=action_search {len(act)}문항: 1개 이상 {sum(r['hit'] > 0 for r in act)}/{len(act)}, "
      f"전부 {sum(r['hit'] == r['total'] for r in act)}/{len(act)}")
ranks = [r["first_citation_rank"] for r in rows]
print(f"순위 지표(Citation 순서 기준): 첫 정답 Top-3 {sum(1 for x in ranks if x and x <= 3)}/{len(rows)}, "
      f"Top-5 {sum(1 for x in ranks if x and x <= 5)}/{len(rows)}, "
      f"MRR {sum(1 / x for x in ranks if x) / len(rows):.3f}")
print("status", dict(collections.Counter(r["status"] for r in rows)),
      "| 평균 Finding", round(sum(r["n_find"] for r in rows) / len(rows), 1))
json.dump({"rows": rows, "detail": detail}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False,
          indent=1, default=str)
deps["driver"].close()
