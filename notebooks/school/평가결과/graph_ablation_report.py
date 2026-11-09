"""graph_ablation_probe.py 결과(full·nograph)를 질문별·유형별로 비교한다 (주승우 / school, 2026-11-09).

    uv run python notebooks/school/평가결과/graph_ablation_report.py <full.json> <nograph.json>
"""

from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path


def load(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["rows"]


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def by_question(rows: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["id"]].append(r)
    return grouped


def main() -> None:
    full, nog = by_question(load(sys.argv[1])), by_question(load(sys.argv[2]))
    print("| 문항 | 유형 | 정답 청크 | 정답 문서 | 도구 후보 중 정답(full / nograph) | 최종 인용 중 정답(full / nograph) | "
          "정답 문서 덮음(full / nograph) | 상태(full / nograph) |")
    print("|---|---|---:|---:|---|---|---|---|")
    kinds = defaultdict(lambda: defaultdict(list))
    for qid in full:
        f, n = full[qid], nog.get(qid, [])
        if not n:
            continue
        g, docs = f[0]["gold_chunks"], len(f[0]["gold_docs"])
        row = {
            "tool_gold": (mean(r["tool_gold"] for r in f), mean(r["tool_gold"] for r in n)),
            "cited_gold": (mean(r["cited_gold"] for r in f), mean(r["cited_gold"] for r in n)),
            "docs": (mean(len(r["docs_covered"]) for r in f), mean(len(r["docs_covered"]) for r in n)),
            "cited": (mean(r["cited"] for r in f), mean(r["cited"] for r in n)),
            "key": (mean(bool(r["key_hit"]) for r in f if r["key_hit"] is not None),
                    mean(bool(r["key_hit"]) for r in n if r["key_hit"] is not None)),
        }
        for name, pair in row.items():
            kinds[f[0]["kind"]][name].append(pair)
        stat = lambda rs: "/".join(r["status"][:3] for r in rs)
        print(f'| {qid} | {f[0]["kind"]} | {g} | {docs} | {row["tool_gold"][0]:.1f} / {row["tool_gold"][1]:.1f} | '
              f'{row["cited_gold"][0]:.1f} / {row["cited_gold"][1]:.1f} | {row["docs"][0]:.1f} / {row["docs"][1]:.1f} | '
              f'{stat(f)} / {stat(n)} |')
    print("\n유형별 평균(문항 평균의 평균, 2회 평균)")
    print("| 유형 | 문항 | 도구 후보 중 정답 full | nograph | 최종 인용 중 정답 full | nograph | 정답 문서 덮음 full | nograph |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for kind, data in kinds.items():
        t, c, d = data["tool_gold"], data["cited_gold"], data["docs"]
        print(f"| {kind} | {len(t)} | {mean(a for a, _ in t):.1f} | {mean(b for _, b in t):.1f} | "
              f"{mean(a for a, _ in c):.1f} | {mean(b for _, b in c):.1f} | {mean(a for a, _ in d):.2f} | {mean(b for _, b in d):.2f} |")
    for label, groups in (("full", full), ("nograph", nog)):
        secs = [r["seconds"] for rs in groups.values() for r in rs]
        print(f"\n{label} 응답 시간 중앙값 {st.median(secs):.1f}초, 최대 {max(secs):.1f}초, 문항·회 {len(secs)}")


if __name__ == "__main__":
    main()
