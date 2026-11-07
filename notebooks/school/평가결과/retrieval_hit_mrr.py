"""검색 순위 지표(Hit@k·MRR@10) — 예전 방식과 현재 구조를 같은 골든 34문항으로 나란히 잰다. DB는 읽기만 한다.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/retrieval_hit_mrr.py <결과.md> [--no-router] [--stages A,B,C]

세 단계(분모는 모두 '정답 청크가 있는 골든 34문항', 정답 = 골든 citations의 chunk_id)
- A 단일 질의 hybrid: 원 질문 하나로 단어 기반(fulltext)+vector 검색을 RRF로 합친 상위 10개. 11/05~11/06 청킹 비교
  (v1 19/23/0.400 … v4.1 16/20/0.312)와 같은 호출·산식이다. 그래서 A는 예전 값과 바로 비교할 수 있다.
- B 현재 검색(두 질의): 원 질문(복합 질문이면 학교 행동 하위 질문) + 라우터의 '문서 용어 질문'을 각각 hybrid로 찾고
  RRF로 합친다. 학교 행동 Tool과 같은 후보 깊이(30, 조치어가 있으면 최대 64)까지 본다. 라우터는 LLM이라
  첫 실행 결과를 캐시에 저장해 다시 쓴다(--refresh-router로 새로 받음). --no-router면 B는 A와 같은 질의다.
- C Tool 출력 순서: 학교 행동 Tool 전체(그래프 조회·점수표·적합성 검사·역할/문서 번갈아 뽑기)를 거친 인용 순서.
  LLM 근거 선별에 들어가기 직전 순서다(판단 기준 원문 조회 결과는 넣지 않는다). LLM 호출 없음.
- LLM 선별 뒤 순서는 기존 '인용 순서 MRR'(golden_eval_main.py)이 잰다. 출력 인용이 2~3개라 여기서 다시 재지 않는다.

허용 문서·preferred는 예전 측정과 같게 둔다(허용 5개, preferred 학교 문서 2개). 임베딩 호출은 문항당 질의 수만큼 생긴다.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "src")

GOLDEN = "data/specs/golden/school.json"
ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025", "kdca_measles_2026",
           "kdca_diagnosis_reporting_2026", "moleg_infectious_disease_act_21521"]
PREF = ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]
CACHE = Path("data/interim/school/retrieval_eval/router_cache.json")
PAST = [  # 같은 산식(A)으로 잰 예전 값. 출처: 발표_슬라이드_구성_제안_20261107.md 경과-L2(청킹 비교 기록)
    ("11/05 청킹 v1", "19/34", "23/34", "0.400"),
    ("11/06 청킹 v3", "13/34", "17/34", "0.265"),
    ("11/06 청킹 v4", "17/34", "21/34", "0.331"),
    ("11/06 청킹 v4.1", "16/34", "20/34", "0.312"),
]


def first_rank(order: list[str], gold: set[str]) -> int | None:
    return next((i for i, cid in enumerate(order, 1) if cid in gold), None)


def summarize(ranks: dict[str, int | None], depth: int | None = None) -> dict:
    n = len(ranks)
    out = {"n": n,
           "hit5": sum(1 for r in ranks.values() if r and r <= 5),
           "hit10": sum(1 for r in ranks.values() if r and r <= 10),
           "mrr10": sum(1 / r for r in ranks.values() if r and r <= 10) / n if n else 0.0}
    if depth:
        out["depth"] = depth
        out["hit_depth"] = sum(1 for r in ranks.values() if r and r <= depth)
    return out


def route_all(questions: dict, books: dict, refresh: bool) -> dict:
    """문항별 라우터 결과(하위 질문·문서 용어 질문). 캐시가 있으면 쓴다."""
    from rag.school.main_graph import classify_question
    from rag.school.router import make_router

    cache = {} if refresh or not CACHE.exists() else json.loads(CACHE.read_text(encoding="utf-8"))
    router = None
    for qid, q in questions.items():
        if qid in cache:
            continue
        router = router or make_router()
        try:
            got = router(q["question"], classify_question(q["question"], books))
            cache[qid] = {k: got.get(k) for k in ("response_type", "intent", "sub_questions", "search_queries")}
        except Exception as exc:  # noqa: BLE001 - 라우터 실패는 원 질문만 쓰고 기록한다
            cache[qid] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def tool_queries(q: dict, route: dict) -> tuple[str, str | None]:
    """메인 그래프가 학교 행동 Tool에 넘기는 질문과 문서 용어 질문(execute_tool과 같은 규칙)."""
    subs = route.get("sub_questions") or {}
    question = (subs.get("action_search") if len(subs) > 1 else None) or q["question"]
    return question, (route.get("search_queries") or {}).get("action_search")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", help="결과 md 경로(같은 이름의 .json에 문항별 순위를 저장)")
    parser.add_argument("--stages", default="A,B,C")
    parser.add_argument("--no-router", action="store_true", help="라우터(LLM)를 부르지 않는다. B의 둘째 질의가 없다")
    parser.add_argument("--refresh-router", action="store_true", help="라우터 캐시를 무시하고 새로 받는다")
    args = parser.parse_args()
    stages = {s.strip().upper() for s in args.stages.split(",")}

    from index.school.dictionaries import load_dictionaries
    from rag.school.action_tool import TOOL_NAME, action_search, plan_action_task
    from rag.school.graph import default_deps
    from rag.school.main_graph import TOOL_TASKS
    from rag.school.search import fuse_rrf, search_school_chunks

    golden = json.loads(Path(GOLDEN).read_text(encoding="utf-8"))
    gold: dict[str, set[str]] = collections.defaultdict(set)
    for c in golden["citations"]:
        if c.get("chunk_id"):
            gold[c["q"]].add(c["chunk_id"])
    questions = {q["id"]: q for q in golden["questions"] if q["id"] in gold}

    deps = default_deps()
    deps["books"] = books = load_dictionaries()
    if deps["embedder"] is None:
        sys.exit("OPENAI_API_KEY가 없어 vector 검색을 할 수 없습니다(예전 값은 hybrid 기준).")
    driver, database = deps["driver"], deps["database"]
    routes = {} if args.no_router or not stages & {"B", "C"} else route_all(questions, books, args.refresh_router)

    def search(question: str, k: int) -> list[dict]:
        return search_school_chunks(driver, {"question": question, "allowed_document_ids": ALLOWED,
                                             "preferred_document_ids": PREF}, books,
                                    embedder=deps["embedder"], mode="hybrid", k=k, database=database)["hits"]

    ranks: dict[str, dict] = {s: {} for s in ("A", "B", "C")}
    detail: dict[str, dict] = {}
    depth_b = 0
    started = time.perf_counter()
    try:
        for qid, q in questions.items():
            row = detail.setdefault(qid, {"question": q["question"], "gold": sorted(gold[qid])})
            if "A" in stages:
                order = [h["chunk_id"] for h in search(q["question"], 10)]
                ranks["A"][qid] = row["A"] = first_rank(order, gold[qid])
            question, doc_query = tool_queries(q, routes.get(qid, {}))
            task = TOOL_TASKS[TOOL_NAME] + (f" (문서 용어 질문: {doc_query.replace(')', ' ')})" if doc_query else "")
            tool_input = {"question": question, "task": task, "region": q["region"],
                          "allowed_document_ids": ALLOWED, "preferred_document_ids": PREF}
            row |= {"tool_question": question, "doc_query": doc_query, "route": routes.get(qid, {})}
            if "B" in stages:
                plan = plan_action_task(tool_input, books, has_embedder=True)
                k = max(30, plan["top_k"] + 4)
                if plan["operation_terms"]:          # 학교 행동 Tool의 후보 깊이 규칙과 같다
                    k = min(64, max(32, plan["top_k"] * 4))
                lists = [[{**h, "method": f"q{n}"} for h in search(text, k)]
                         for n, text in enumerate(dict.fromkeys([question, *([doc_query] if doc_query else [])]), 1)]
                merged = lists[0] if len(lists) == 1 else fuse_rrf(lists, PREF, k)
                order = [h["chunk_id"] for h in merged]
                ranks["B"][qid] = row["B"] = first_rank(order, gold[qid])
                row["B_depth"] = k
                depth_b = max(depth_b, k)
            if "C" in stages:
                result = action_search(tool_input, deps)
                order = [c["chunk_id"] for c in result["citations"]]
                ranks["C"][qid] = row["C"] = first_rank(order, gold[qid])
                row["C_citations"] = len(order)
            print(qid, {s: row.get(s) for s in "ABC" if s in stages}, flush=True)
    finally:
        driver.close()

    seconds = round(time.perf_counter() - started, 1)
    sums = {s: summarize(ranks[s], depth=30 if s == "B" else None) for s in "ABC" if s in stages}
    if "C" in stages:
        sums["C"]["median_citations"] = sorted(d["C_citations"] for d in detail.values())[len(detail) // 2]
    routed = sum(1 for r in routes.values() if (r.get("search_queries") or {}).get("action_search"))
    failed = sum(1 for r in routes.values() if "error" in r)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"summary": sums, "detail": detail}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    label = {"A": "A 단일 질의 hybrid (예전 산식)", "B": "B 현재 검색: 두 질의 hybrid + RRF",
             "C": "C Tool 출력 순서 (LLM 선별 직전)"}
    lines = [f"# 검색 순위 지표 결과 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"- 골든 {GOLDEN}, 정답 청크가 있는 {len(questions)}문항. 허용 문서 {len(ALLOWED)}개, preferred 학교 문서 2개.",
             (f"- 라우터: {'사용 안 함' if not routes else f'문서 용어 질문 있음 {routed}/{len(routes)}, 실패 {failed}'}"
              f" (캐시 {CACHE}). 실행 {seconds}초. DB 쓰기 없음."), "",
             "| 단계 | Hit@5 | Hit@10 | MRR@10 | 비고 |", "|---|---|---|---|---|"]
    for name, hit5, hit10, mrr in PAST:
        lines.append(f"| {name} (A 산식) | {hit5} | {hit10} | {mrr} | 예전 기록 |")
    for s, v in sums.items():
        note = {"A": "현재 DB, 예전과 같은 산식",
                "B": f"Hit@30 {v.get('hit_depth')}/{v['n']} (후보 깊이 최대 {depth_b})",
                "C": f"문항당 인용 중앙값 {v.get('median_citations')}"}[s]
        lines.append(f"| **{label[s]}** | {v['hit5']}/{v['n']} | {v['hit10']}/{v['n']} | {v['mrr10']:.3f} | {note} |")
    lines += ["", "## 문항별 첫 정답 순위", "", "| 문항 | " + " | ".join(sums) + " | 문서 용어 질문 |",
              "|---|" + "---|" * len(sums) + "---|"]
    for qid, row in detail.items():
        cells = " | ".join(str(row.get(s) or "-") for s in sums)
        lines.append(f"| {qid} | {cells} | {row.get('doc_query') or ''} |")
    lines += ["", "- A·B는 원시 검색 순위다(적합성 검사·행동 확장 전). C는 Tool 출력 인용 목록의 순위다.",
              "- C의 분모와 목록 길이는 A·B와 다르다(인용이 적을수록 MRR이 높게 나온다). 같은 선에 이어 그리지 않는다."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out, json.dumps(sums, ensure_ascii=False))


if __name__ == "__main__":
    main()
