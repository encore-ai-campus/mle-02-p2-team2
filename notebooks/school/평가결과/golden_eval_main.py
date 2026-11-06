"""질문셋 v1.1 40문항을 메인 그래프(run_main)로 실행해 라우팅·최종 상태·호출 기록·정답 인용을 채점한다.

    uv run python notebooks/school/평가결과/golden_eval_main.py <결과.json> [--all-tools] [--router]

- --router(11/06~): LLM 라우터(rag.school.router)로 분류한다. 없으면 규칙 분류기.
- --all-tools(11/06~): 팀원 Tool(disease/legal/crossdoc)까지 registry로 연결해 실행하고 모든 문항의 status를 채점한다.
  legal_search는 고정 샘플 1개만 받으므로 legal 문항의 결과는 그 한계를 함께 읽는다.

- 오늘(11/05) registry에는 action_search만 연결돼 있다. disease/legal/crossdoc로 라우팅된 문항은 호출 0회·ERROR가
  '정상 동작'이며, 그 문항의 최종 status는 기대값과 비교하지 않고 라우팅만 채점한다.
"""

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
from rag.school.graph import default_deps
from rag.school.main_graph import run_main
from rag.school.validation_local import verify_against_db

G = json.loads(Path("data/specs/golden/school.json").read_text(encoding="utf-8"))
EXP = collections.defaultdict(set)
for c in G["citations"]:
    EXP[c["q"]].add(c["chunk_id"])
ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025", "kdca_measles_2026",
           "kdca_diagnosis_reporting_2026", "moleg_infectious_disease_act_21521"]


def expected_route(route: str) -> str:
    return route.split(":")[1].strip(" )") if route.startswith("(없음") else route


deps = default_deps()
ALL_TOOLS = "--all-tools" in sys.argv
REGISTRY = None
if "--router" in sys.argv:
    from rag.school.router import make_router
    deps["router"] = make_router()
if ALL_TOOLS:
    from index.school.dictionaries import load_dictionaries
    from rag.prevention_legal.tool import legal_search
    from rag.respiratory.crossdoc_tool import crossdoc_graph_search
    from rag.respiratory.disease_tool import disease_search
    from rag.school.main_graph import default_registry
    deps["books"] = load_dictionaries()
    REGISTRY = {**default_registry(deps), "legal_search": legal_search,
                "disease_search": lambda ti: disease_search(ti, deps=deps),
                "crossdoc_graph_search": lambda ti: crossdoc_graph_search(ti, deps=deps)}
rows = []
for q in G["questions"]:
    out = run_main({"question": q["question"], "region": q["region"], "allowed_document_ids": ALLOWED,
                    "request_id": f"golden-{q['id']}"}, deps, REGISTRY)
    steps = {t["step"]: t for t in deps["main_trace"]}
    selected = steps.get("select_tool", {}).get("tool") or out["response_type"]
    want = expected_route(q["route"])
    cited = {c["chunk_id"] for c in out["citations"]}
    cited_order = [c["chunk_id"] for c in out["citations"]]
    first_rank = next((i for i, cid in enumerate(cited_order, 1) if cid in EXP[q["id"]]), None)
    report = verify_against_db(out, deps["driver"], deps.get("database")) if out["findings"] else None
    connected = ALL_TOOLS or selected == "action_search" or selected in ("GENERAL", "OUT_OF_SCOPE")
    rows.append({"id": q["id"], "split": q["split"], "want_route": want, "got_route": selected,
                 "route_ok": want == selected, "want_status": q["status"], "got_status": out["status"],
                 "status_ok": out["status"] == q["status"] if connected else None,
                 "response_type": out["response_type"], "tools_used": out["tools_used"],
                 "rounds": out["search_rounds"], "hit": len(cited & EXP[q["id"]]), "total": len(EXP[q["id"]]),
                 "first_citation_rank": first_rank, "cited_order": cited_order,
                 "dbok": not report["bad_citations"] and not report["bad_findings"] if report else None,
                 "warnings": out["warnings"],
                 "n_find": len(out["findings"])})
    r = rows[-1]
    print(f"{r['id']} {r['split']:4s} route {r['want_route'][:14]:14s}->{r['got_route'][:14]:14s} "
          f"{'O' if r['route_ok'] else 'X'} | {r['got_status']:21s} "
          f"{'' if r['status_ok'] is None else ('O' if r['status_ok'] else 'X')} | calls {r['rounds']} "
          f"| cite {r['hit']}/{r['total']} F{r['n_find']}")
route_ok = sum(r["route_ok"] for r in rows)
graded = [r for r in rows if r["status_ok"] is not None]
print(f"\n라우팅 일치 {route_ok}/{len(rows)} (dev {sum(r['route_ok'] for r in rows if r['split'] == 'dev')}/"
      f"{sum(r['split'] == 'dev' for r in rows)}, eval {sum(r['route_ok'] for r in rows if r['split'] == 'eval')}/"
      f"{sum(r['split'] == 'eval' for r in rows)})")
print(f"연결된 경로 문항 status 일치 {sum(r['status_ok'] for r in graded)}/{len(graded)}")
print("호출 0회 문항:", [r["id"] for r in rows if r["rounds"] == 0])
print("호출 2회 이상:", [r["id"] for r in rows if r["rounds"] > 1] or "없음")
print("호출 수 분포:", dict(collections.Counter(r["rounds"] for r in rows)))
Path(sys.argv[1]).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
deps["driver"].close()
