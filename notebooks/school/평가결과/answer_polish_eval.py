"""답변 다듬기(11/07) 평가 — 골든 40문항을 4개 Tool registry + answer_writer로 실행해 템플릿 답변과 비교한다.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/answer_polish_eval.py <결과.json>

한 번 실행으로 둘 다 얻는다: compose_answer 실행 기록에 template(기존)과 draft(LLM)가 함께 남는다.
- 다듬기 적용률: 다듬기 대상(DOMAIN·ANSWERED/PARTIAL) 중 검사를 통과해 LLM 답변이 쓰인 비율.
- 검사 실패 사유 분포(번호·숫자·주체·강도).
- 답변 본문 정답 인용: 답변 글의 [n]이 가리키는 청크 중 골든 정답 청크가 있는 문항 수(템플릿/다듬은 답변 각각).
  Finding·Citation은 두 방식이 같으므로 QueryOutput 인용 지표는 바뀌지 않는다. 바뀌는 것은 '답변 글에 드러난 근거'다.
- 길이·지연. 사실 정확성은 검사기가 막는 범위(숫자·주체·강도·번호)까지만 자동이며, 나머지는 사람 판독이 필요하다.
"""

import collections
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
from rag.school.main_graph import run_main
from rag.ui.service import EVALUATED_DOCUMENTS, build_registry, build_resources

REF = re.compile(r"\[(\d+)\]")
G = json.loads(Path("data/specs/golden/school.json").read_text(encoding="utf-8"))
EXP = collections.defaultdict(set)
for c in G["citations"]:
    EXP[c["q"]].add(c["chunk_id"])


def cited_chunks(text: str, citations: list[dict]) -> set[str]:
    return {citations[int(n) - 1]["chunk_id"] for n in REF.findall(text or "") if 0 < int(n) <= len(citations)}


res = build_resources()
assert "answer_writer" in res["deps"], "OPENAI_API_KEY가 없어 답변 작성기를 만들지 못했다"
rows = []
for q in G["questions"]:
    deps = dict(res["deps"])
    started = time.perf_counter()
    out = run_main({"question": q["question"], "region": q["region"], "allowed_document_ids": EVALUATED_DOCUMENTS,
                    "request_id": f"polish-{q['id']}"}, deps, build_registry(deps))
    seconds = round(time.perf_counter() - started, 2)
    step = next(t for t in deps["main_trace"] if t["step"] == "compose_answer")
    template = step.get("template", out["answer"])
    draft = step.get("draft", "")
    gold = EXP[q["id"]]
    rows.append({"id": q["id"], "split": q["split"], "question": q["question"], "status": out["status"],
                 "response_type": out["response_type"], "tools_used": out["tools_used"], "mode": step["mode"],
                 "problems": step.get("problems", []), "polish_seconds": step.get("seconds"), "seconds": seconds,
                 "template": template, "draft": draft, "answer": out["answer"],
                 "n_citations": len(out["citations"]), "gold_total": len(gold),
                 "gold_in_output": len({c["chunk_id"] for c in out["citations"]} & gold),
                 "gold_in_template_text": len(cited_chunks(template, out["citations"]) & gold),
                 "gold_in_draft_text": len(cited_chunks(draft, out["citations"]) & gold),
                 "refs_template": len(set(REF.findall(template))), "refs_draft": len(set(REF.findall(draft))),
                 "len_template": len(template), "len_answer": len(out["answer"]), "len_draft": len(draft)})
    r = rows[-1]
    print(f"{r['id']} {r['status']:21s} {r['mode']:17s} gold out {r['gold_in_output']}/{r['gold_total']} "
          f"text T{r['gold_in_template_text']} D{r['gold_in_draft_text']} | {r['polish_seconds']}s "
          f"| {'; '.join(r['problems'])[:90]}")
res["deps"]["driver"].close()

target = [r for r in rows if r["mode"] in ("llm", "template_fallback")]
graded = [r for r in target if r["gold_total"]]
summary = {
    "questions": len(rows), "polish_target": len(target),
    "llm_used": sum(r["mode"] == "llm" for r in target),
    "fallback": sum(r["mode"] == "template_fallback" for r in target),
    "problem_kinds": dict(collections.Counter(p.split(" ")[0] + " " + p.split(" ")[1] for r in target
                                              for p in r["problems"])),
    "gold_graded": len(graded),
    "gold_in_output_any": sum(r["gold_in_output"] > 0 for r in graded),
    "gold_in_template_text_any": sum(r["gold_in_template_text"] > 0 for r in graded),
    "gold_in_draft_text_any": sum(r["gold_in_draft_text"] > 0 for r in graded),
    "gold_in_final_text_any": sum((r["gold_in_draft_text"] if r["mode"] == "llm" else r["gold_in_template_text"]) > 0
                                  for r in graded),
    "median_len_template": sorted(r["len_template"] for r in target)[len(target) // 2] if target else None,
    "median_len_draft": sorted(r["len_draft"] for r in target)[len(target) // 2] if target else None,
    "median_polish_seconds": sorted(r["polish_seconds"] or 0 for r in target)[len(target) // 2] if target else None,
    "status_counts": dict(collections.Counter(r["status"] for r in rows)),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
Path(sys.argv[1]).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
