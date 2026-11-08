"""Tool 호출 흐름·안전장치 비용 측정 (주승우 / school, 2026-11-07). 읽기 전용. 코드 동작은 바꾸지 않는다.

질문마다 메인 그래프를 화면과 같은 조립(service.build_registry, 허용 문서=DB 전체)으로 실행하고,
함수를 감싸서 아래를 기록한다.
- 노드별 시간: 그래프 log 사이 간격(벽시계). Tool은 순서대로 한 번에 하나씩 돈다.
- Tool별 호출 시간·상태·순서, 2번째 이후 호출이 생긴 이유
- 학교 행동 Tool 내부: 검색(retrieve) / 결과 만들기 / DB 대조 시간
- 메인 DB 재대조(verify_against_db·check_graph_paths) 시간
- 법령 Tool의 Neo4j 새 연결(connect) 횟수·시간
- 2번째 이후 Tool이 낸 근거가 최종 선별(인용)에 남았는지

    uv run python notebooks/school/평가결과/tool_flow_profile.py <결과.json> [--set demo|golden|all] [--only id,id]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from situation_demo_eval import QUESTIONS as DEMO

from rag.ui import service

TIMES: dict[str, list[float]] = {}
COUNTS: dict[str, int] = {}


def timed(module, name: str, key: str):
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            TIMES.setdefault(key, []).append(time.perf_counter() - started)
            COUNTS[key] = COUNTS.get(key, 0) + 1
    setattr(module, name, wrapper)


def patch() -> None:
    from rag.prevention_legal import retrieve as legal_retrieve
    from rag.school import action_tool, main_graph
    timed(action_tool, "retrieve_action_records", "action.retrieve")
    timed(action_tool, "records_to_tool_result", "action.build")
    timed(action_tool, "verify_tool_result_db", "action.db_verify")
    timed(main_graph, "verify_against_db", "main.db_verify")
    timed(main_graph, "check_graph_paths", "main.path_verify")
    timed(legal_retrieve, "connect", "legal.connect")


def questions(which: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if which in ("demo", "all"):
        items += list(DEMO)
    if which in ("golden", "all"):
        golden = json.loads((ROOT / "data/specs/golden/school.json").read_text(encoding="utf-8"))
        items += [(q["id"], q["question"]) for q in golden["questions"]]
    return items


def main() -> None:
    out_path = Path(sys.argv[1])
    which = sys.argv[sys.argv.index("--set") + 1] if "--set" in sys.argv else "demo"
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    patch()
    res = service.build_resources()
    allowed = service.default_documents(res["documents"])
    rows = []
    for qid, question in questions(which):
        if only and qid not in only:
            continue
        TIMES.clear()
        COUNTS.clear()
        stamps: list[tuple[str, float, dict]] = []
        started = time.perf_counter()

        def on_event(step, info, _stamps=stamps):
            _stamps.append((step, time.perf_counter(), info))

        out = service.ask(res, question, "GYEONGGI", allowed, on_event=on_event)
        total = time.perf_counter() - started
        # 노드 시간: 이전 log 시각부터 이번 log 시각까지(그 노드가 끝날 때 log를 남긴다)
        nodes: dict[str, float] = {}
        prev = started
        for step, at, _ in stamps:
            nodes[step] = round(nodes.get(step, 0) + at - prev, 2)
            prev = at
        trace = out["trace"]
        calls = [t for t in trace if t["step"] == "execute_tool"]
        selects = [t for t in trace if t["step"] == "select_tool"]
        evals = [t for t in trace if t["step"] == "evaluate_evidence"]
        route = next((t for t in trace if t["step"] == "classify_scope"), {})
        final_chunks = {c["chunk_id"] for c in out["output"]["citations"]}
        by_tool = {r["tool_name"]: {c["chunk_id"] for c in r["citations"]} for r in out["tool_results"]}
        later = [c["tool"] for c in calls[1:]]
        rows.append({
            "id": qid, "question": question, "status": out["output"]["status"], "seconds": round(total, 2),
            "intent": route.get("intent"), "secondary": route.get("secondary"),
            "tools": [{"tool": c["tool"], "status": c["status"], "seconds": c["seconds"],
                       "findings": c["findings"], "citations": c["citations"]} for c in calls],
            "select_reasons": [s.get("reason") for s in selects],
            "eval_status": [e.get("status") for e in evals],
            "nodes": nodes,
            "inner": {k: {"n": COUNTS[k], "sec": round(sum(v), 2)} for k, v in TIMES.items()},
            "final_from_tool": {t: len(ids & final_chunks) for t, ids in by_tool.items()},
            "later_tools": later,
            "later_kept": sum(len(by_tool.get(t, set()) & final_chunks) for t in later),
            "final_citations": len(final_chunks),
        })
        r = rows[-1]
        print(f"{qid:6s} {r['status']:21s} {r['seconds']:5.1f}s tools="
              + ",".join(f"{c['tool'].split('_')[0]}:{c['status']}:{c['seconds']}" for c in r["tools"])
              + f" kept_later={r['later_kept']} inner={r['inner']}", flush=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
