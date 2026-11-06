"""action_search 후보 선별 진단(읽기 전용, 2026-11-06).

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/selection_diagnosis.py <결과.json>

골든 정답 청크가 있는 문항마다 후보 풀 크기, 경로(via)별 기여, 정답 행동의 점수·순위, 문턱/다양화/최종 인용 단계별 생존을 잰다."""

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
import rag.school.action_tool as at
from index.school.dictionaries import load_dictionaries
from rag.school.graph import default_deps

G = json.loads(Path("data/specs/golden/school.json").read_text(encoding="utf-8"))
EXP = collections.defaultdict(set)
for c in G["citations"]:
    EXP[c["q"]].add(c["chunk_id"])
ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025", "kdca_measles_2026",
           "kdca_diagnosis_reporting_2026", "moleg_infectious_disease_act_21521"]
TASK = "학교가 수행하는 행동, 역할, 단계 및 근거 찾기"

deps = default_deps()
deps["books"] = load_dictionaries()
drv, db = deps["driver"], deps["database"]
rec, _, _ = drv.execute_query(
    "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction) WHERE d.document_id IN $al "
    "RETURN a.action_id AS a, collect(DISTINCT c.chunk_id) AS cs", al=ALLOWED, database_=db)
A2C = {r["a"]: set(r["cs"]) for r in rec}
CHUNK_HAS_ACTION = set().union(*A2C.values())
print("허용 문서 행동 수", len(A2C), "행동이 붙은 청크 수", len(CHUNK_HAS_ACTION))

CAP = {}
orig_score, orig_div = at._score, at.diversify


def score(item, plan, chunk_rank, names):
    s = orig_score(item, plan, chunk_rank, names)
    focus = set(plan["focus"])
    CAP.setdefault("items", {})[item["action_id"]] = {
        "score": s, "via": sorted(item["via"]),
        "bigram": len((at.bigrams(item["action_text"]) - at.GENERIC_BIGRAMS) & focus),
        "chunk_rank": chunk_rank.get(item["chunk_id"]), "stage": item["stage"],
        "roles": [r["role_id"] for r in item["roles"]], "text": item["action_text"][:60]}
    return s


def div(ranked, plan, k):
    CAP["keep"] = [x["action_id"] for x in ranked]
    out = orig_div(ranked, plan, k)
    CAP["selected"] = [x["action_id"] for x in out]
    return out


at._score, at.diversify = score, div
rows = []
for q in G["questions"]:
    gold = EXP[q["id"]]
    if not gold:
        continue
    CAP.clear()
    deps["trace"] = []
    res = at.action_search({"question": q["question"], "task": TASK, "region": q["region"],
                            "allowed_document_ids": ALLOWED}, deps)
    items = CAP.get("items", {})
    ranked = sorted(items, key=lambda a: (-items[a]["score"], a))
    gold_actions = [a for a in ranked if A2C.get(a, set()) & gold]
    gold_ranks = [ranked.index(a) + 1 for a in gold_actions]
    cited = [c["chunk_id"] for c in res["citations"]]
    act_cited = {c for f in res["findings"] if f["action_ids"] for c in f["chunk_ids"]}
    quote_cited = {c for f in res["findings"] if not f["action_ids"] for c in f["chunk_ids"]}
    log = {s["step"]: s for s in deps["trace"][0]["log"]} if deps["trace"] else {}
    via_counter = collections.Counter(tuple(items[a]["via"]) for a in items)
    rows.append({
        "id": q["id"], "route": q["route"], "status": res["status"],
        "pool": len(items), "keep": len(CAP.get("keep", [])), "selected": len(CAP.get("selected", [])),
        "via": {"+".join(k): v for k, v in via_counter.items()},
        "chunk_hits": log.get(next((k for k in log if k.startswith("chunk_")), ""), {}).get("relevant_hits"),
        "gold_n": len(gold), "gold_with_action": len(gold & CHUNK_HAS_ACTION),
        "gold_actions_in_pool": len(gold_actions), "gold_best_rank": min(gold_ranks) if gold_ranks else None,
        "gold_best_score": round(items[gold_actions[0]]["score"], 1) if gold_actions else None,
        "gold_best_via": items[gold_actions[0]]["via"] if gold_actions else None,
        "gold_in_keep": sum(a in CAP.get("keep", []) for a in gold_actions),
        "gold_in_selected": sum(a in CAP.get("selected", []) for a in gold_actions),
        "top8_scores": [round(items[a]["score"], 1) for a in ranked[:8]],
        "n_cited": len(cited), "gold_cited": len(set(cited) & gold),
        "gold_cited_by_action": len(act_cited & gold), "gold_cited_by_quote": len(quote_cited & gold - act_cited),
        "gold_scores": [round(items[a]["score"], 1) for a in gold_actions[:5]],
        "gold_features": [items[a] for a in gold_actions[:3]],
        "top3": [items[a] | {"id": a, "gold": a in gold_actions} for a in ranked[:3]],
    })
    r = rows[-1]
    print(f"{r['id']:6s} pool {r['pool']:4d} keep {r['keep']:3d} sel {r['selected']:2d} | gold청크 {r['gold_n']}"
          f"(행동有 {r['gold_with_action']}) 풀 안 정답행동 {r['gold_actions_in_pool']:2d} 최고순위 {r['gold_best_rank']}"
          f" 점수 {r['gold_best_score']} via {r['gold_best_via']} | keep {r['gold_in_keep']} sel {r['gold_in_selected']}"
          f" | 인용 {r['gold_cited']}/{r['gold_n']} (행동 {r['gold_cited_by_action']}, 인용문 {r['gold_cited_by_quote']})"
          f" of {r['n_cited']}")
drv.close()
Path(sys.argv[1]).write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
