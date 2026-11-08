"""다른 담당 골든 질문 교차 측정 (주승우 / school, 2026-11-08). 읽기 전용.

호흡기 골든(R-*)과 법령 골든(Q01~Q30) 질문을 세 가지로 실행해 정답 쪽(문서+PDF 쪽)을 찾는지 비교한다.
- main: 화면과 같은 메인 그래프(4 Tool 조립, 허용 문서 전체). 라우팅·Tool·최종 인용.
- action: 학교 행동 Tool 단독(라우터 문서 용어 질문 없이 원 질문만).
- own: 질문 담당 Tool 단독(호흡기 → disease_search, 법령 → legal_search).
정답 쪽은 골든 md에 쪽수가 적힌 문항만 쓴다('확인 전'은 쪽 정답 없음 → hit 계산 제외).
주의: 담당 Tool의 카드·사전은 이 골든을 보고 만든 부분이 있어 own은 독립 평가가 아니다(회귀 성격).

    uv run python notebooks/school/평가결과/cross_tool_probe.py <out.json> [--only id,id] [--skip-main]
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from rag.ui import service

DOC_ALIAS = {
    "홍역 지침": "kdca_measles_2026", "법": "moleg_infectious_disease_act_21521",
    "수족구병 지침": "kdca_hfmd_2026", "예방접종 지침": "kdca_vaccine_preventable_2026",
}

# 표 칸 형식이 정규식과 달라 직접 적은 정답 쪽: Q10 '법 제11조·제12조 · 18~19쪽', Q23 명세 80행 '예방접종 지침 116쪽 표 32'
GOLD_FIX = {"Q10": {("moleg_infectious_disease_act_21521", 18), ("moleg_infectious_disease_act_21521", 19)},
            "Q23": {("kdca_vaccine_preventable_2026", 116)}}


def respiratory_questions() -> list[dict]:
    rows = []
    text = (ROOT / "data/specs/golden/respiratory.md").read_text(encoding="utf-8")
    for m in re.finditer(r"^\| (R-[A-Z]\d) \| ([^|]+) \| ([A-Z_]+[^|]*) \| ([^|]*) \|", text, re.MULTILINE):
        qid, question, status, basis = (x.strip() for x in m.groups())
        gold = set()
        for doc, pages in re.findall(r"(kdca_\w+|gyeonggi_\w+|moe_\w+) p(\d+(?:\(·p\d+\))?(?:·p\d+)*)", basis):
            for p in re.findall(r"\d+", pages):
                gold.add((doc, int(p)))
        rows.append({"id": qid, "question": question, "expected": status.split()[0], "gold": sorted(gold),
                     "domain": "respiratory"})
    return rows


def legal_questions() -> list[dict]:
    rows = []
    text = (ROOT / "data/specs/golden/prevention_legal.md").read_text(encoding="utf-8")
    for m in re.finditer(r"^\| (Q\d\d) \| ([A-G]) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|", text, re.MULTILINE):
        qid, cat, question, status, basis = (x.strip() for x in m.groups())
        gold = set()
        for name, doc in DOC_ALIAS.items():
            for seg in re.findall(rf"{name}(?:[^·|]*)· ?([\d~·쪽\s]+)쪽", basis):
                nums = [int(n) for n in re.findall(r"\d+", seg)]
                if "~" in seg and len(nums) == 2:
                    nums = list(range(nums[0], nums[1] + 1))
                gold |= {(doc, n) for n in nums}
        gold |= GOLD_FIX.get(qid, set())
        rows.append({"id": qid, "category": cat, "question": question, "expected": status.split()[0],
                     "gold": sorted(gold), "domain": "legal"})
    return rows


def pages(citations: list[dict]) -> list[tuple[str, int]]:
    return [(c["document_id"], c["pdf_page"]) for c in citations]


def hit(cited: list[tuple[str, int]], gold: list) -> bool | None:
    if not gold:
        return None
    gold_set = {tuple(g) for g in gold}
    return any(c in gold_set for c in cited)


def main() -> None:
    out_path = Path(sys.argv[1])
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    res = service.build_resources()
    allowed = service.default_documents(res["documents"])
    registry = service.build_registry(dict(res["deps"]))
    rows = []
    for item in respiratory_questions() + legal_questions():
        if only and item["id"] not in only:
            continue
        row = dict(item)
        tool_input = {"question": item["question"], "task": "질문에 답할 근거 찾기", "region": "NATIONAL",
                      "allowed_document_ids": allowed}
        for name, tool in (("action", "action_search"),
                           ("own", "disease_search" if item["domain"] == "respiratory" else "legal_search")):
            started = time.perf_counter()
            try:
                result = registry[tool](dict(tool_input))
                cited = pages(result["citations"])
                row[name] = {"status": result["status"], "cited": cited, "hit": hit(cited, item["gold"]),
                             "n": len(cited), "seconds": round(time.perf_counter() - started, 2)}
            except Exception as exc:  # noqa: BLE001
                row[name] = {"status": "EXC", "error": f"{type(exc).__name__}: {exc}"[:200]}
        if "--skip-main" not in sys.argv:
            started = time.perf_counter()
            out = service.ask(res, item["question"], "NATIONAL", allowed)
            trace = out["trace"]
            route = next((t for t in trace if t["step"] == "classify_scope"), {})
            cited = pages(out["output"]["citations"])
            row["main"] = {"status": out["output"]["status"], "intent": route.get("intent"),
                           "secondary": route.get("secondary"), "response_type": route.get("response_type"),
                           "tools": out["output"]["tools_used"],
                           "tool_status": [(t["tool"], t["status"]) for t in trace if t["step"] == "execute_tool"],
                           "cited": cited, "hit": hit(cited, item["gold"]), "n": len(cited),
                           "seconds": round(time.perf_counter() - started, 2),
                           "answer": out["output"]["answer"]}
        rows.append(row)
        m = row.get("main", {})
        print(f"{item['id']:5s} gold={len(item['gold'])} exp={item['expected']:22s} "
              f"action={row['action'].get('status')}/{row['action'].get('hit')} "
              f"own={row['own'].get('status')}/{row['own'].get('hit')} "
              f"main={m.get('status')}/{m.get('hit')} {m.get('intent')}{m.get('secondary')} {m.get('tools')}",
              flush=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
