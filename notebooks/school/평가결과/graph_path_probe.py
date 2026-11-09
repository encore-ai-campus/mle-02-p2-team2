"""추가 적재한 학교 행동이 실제 답변에서 지식그래프 경로로 쓰이는지 확인한다 (주승우 / school, 2026-11-08). 읽기 전용.

화면과 같은 조립(service.ask, 허용 문서 DB 전체)으로 질문을 실행한다. 다음을 기록한다.
- Tool별 GraphPath 중 명세의 action_id를 지나는 경로와 그 관계 종류
- 최종 QueryOutput.action_ids에 명세 행동이 남았는지
- 최종 인용 청크

    uv run python notebooks/school/평가결과/graph_path_probe.py <spec.json> <out.json> [반복 횟수]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from rag.ui import service

QUESTIONS = [
    ("A", "수족구병 진단받은 학생, 언제부터 등교시키면 되나요?"),
    ("B", "수족구병 등교중지 기간이 교육부 매뉴얼, 경기도 가이드북, 질병관리청 지침에서 각각 어떻게 다른가요?"),
]


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_path = Path(sys.argv[2])
    repeat = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    targets = {a["action_id"] for a in spec["actions"]}
    res = service.build_resources()
    allowed = service.default_documents(res["documents"])
    rows = []
    for run in range(repeat):
        for qid, question in QUESTIONS:
            started = time.perf_counter()
            out = service.ask(res, question, "GYEONGGI", allowed)
            o = out["output"]
            tool_paths = []
            for r in out["tool_results"]:
                for p in r.get("graph_paths", []):
                    edges = p.get("edges", [])
                    refs = {e["source_ref"] for e in edges} | {e["target_ref"] for e in edges}
                    hit = sorted(t for t in targets if f"SchoolAction:{t}" in refs)
                    if hit:
                        tool_paths.append({"tool": r["tool_name"], "actions": hit,
                                           "edges": [f'{e["source_ref"]} -{e["relationship_type"]}-> {e["target_ref"]}'
                                                     for e in edges]})
            trace = out["trace"]
            rows.append({
                "run": run + 1, "id": qid, "question": question, "status": o["status"],
                "route": next((t.get("intent") for t in trace if t["step"] == "classify_scope"), None),
                "tools": [(t["tool"], t["status"]) for t in trace if t["step"] == "execute_tool"],
                "tool_paths_with_targets": tool_paths,
                "final_action_ids": o["action_ids"],
                "final_target_actions": sorted(targets & set(o["action_ids"])),
                "cited": [c["chunk_id"] for c in o["citations"]],
                "seconds": round(time.perf_counter() - started, 2), "answer": o["answer"],
            })
            r = rows[-1]
            print(f'{run + 1} {qid} {r["status"]} route={r["route"]} tools={r["tools"]} '
                  f'paths={[(p["tool"], p["actions"]) for p in tool_paths]} final={r["final_target_actions"]} '
                  f'cited={r["cited"]} {r["seconds"]}s', flush=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
