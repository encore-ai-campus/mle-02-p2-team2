"""상황형 데모 질문 평가(11/07 답변 품질 개선 P1-8) — 교직원이 실제로 물을 법한 상황형 질문 15개를 메인 그래프로 실행한다.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/situation_demo_eval.py <결과.json> [--docs evaluated|all]

- 골든셋(정답 청크 인용)으로는 보이지 않던 '답변 유용성'을 사람이 읽고 루브릭(답변품질_개선_제안서 6장)으로 채점하기 위한 실행 기록.
- 정답 청크는 없다. 결과에는 답변·status·Tool·라우터 상황 해석·확인 항목·선별 근거·시간을 남긴다.
- --docs evaluated: 골든셋 평가 문서 5종(11/07 이전 화면 기본값). all: DB의 모든 문서(11/07 이후 화면 기본값).
- DB는 읽기만 한다. 실LLM을 부른다(OPENAI_API_KEY 필요).
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
from rag.school.main_graph import run_main
from rag.ui.service import EVALUATED_DOCUMENTS, build_registry, build_resources

QUESTIONS = [
    ("D01", "전교생 중 5명 코로나 확진인데, 교장이 해야 할 일은?"),
    ("D02", "내가 담임이고 지금 홍역이 한 반에 몇 명씩 유행하는 것 같은데, 신고 해야할까? 그리고 안하면 어떻게 돼?"),
    ("D03", "우리 반 학생이 수두 확진을 받았대요. 담임으로서 오늘 뭐부터 해야 하나요?"),
    ("D04", "독감 걸린 학생 언제부터 등교할 수 있어요? 학부모가 물어보는데요."),
    ("D05", "같은 반에서 설사·구토하는 학생이 3명 나왔어요. 식중독일 수도 있는데 보건교사는 뭘 해야 하죠?"),
    ("D06", "결핵 확진 교사가 나왔는데 학교는 무엇을 해야 하나요?"),
    ("D07", "성홍열 확진 학생이 1명 나왔는데 보건소에 신고해야 하나요?"),
    ("D08", "수족구병 걸린 학생이 생겼는데 등교중지 해야 하나요? 기간은 얼마나요?"),
    ("D09", "우리 반 학생이 열이 나서 보건실에 보내려는데, 혼자 보내도 되나요?"),
    ("D10", "인플루엔자가 학급마다 퍼지고 있어요. 휴업이나 단축수업을 해야 하나요?"),
    ("D11", "홍역 의심 학생이 나왔는데 같은 반 다른 학생들은 어떻게 해야 하나요?"),
    ("D12", "코로나 걸린 학생도 출석 인정이 되나요? 어떤 서류를 내야 해요?"),
    ("D13", "백일해 환자가 나오면 학교가 보건소에 알려야 하나요?"),
    ("D14", "감염병 유행이 끝났다는 건 언제 판단하고, 그다음엔 뭘 해야 하나요?"),
    ("D15", "보건교사가 출장 중인데 유증상 학생이 생겼어요. 누가 대신 처리하나요?"),
]


def main() -> None:
    out_path = Path(sys.argv[1])
    docs_mode = sys.argv[sys.argv.index("--docs") + 1] if "--docs" in sys.argv else "all"
    res = build_resources()
    allowed = EVALUATED_DOCUMENTS if docs_mode == "evaluated" else sorted(res["documents"])
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    rows = []
    for qid, question in QUESTIONS:
        if only and qid not in only:
            continue
        deps = dict(res["deps"])
        started = time.perf_counter()
        out = run_main({"question": question, "region": "GYEONGGI", "allowed_document_ids": allowed,
                        "request_id": f"demo-{qid}"}, deps, build_registry(deps))
        seconds = round(time.perf_counter() - started, 2)
        steps = {}
        for t in deps["main_trace"]:
            steps.setdefault(t["step"], t)
        compose = next((t for t in deps["main_trace"] if t["step"] == "compose_answer"), {})
        rows.append({
            "id": qid, "question": question, "docs": docs_mode, "status": out["status"],
            "tools_used": out["tools_used"], "answer": out["answer"], "seconds": seconds,
            "mode": compose.get("mode"), "problems": compose.get("problems", []), "dropped": compose.get("dropped", []),
            "attempts": compose.get("attempts"),
            "stage_seconds": {"route": steps.get("classify_scope", {}).get("seconds"),
                              "tools": round(sum(t.get("seconds") or 0 for t in deps["main_trace"]
                                                 if t["step"] == "execute_tool"), 2),
                              "checks": steps.get("check_facts", {}).get("seconds"),
                              "rerank": steps.get("select_evidence", {}).get("seconds"),
                              "write": compose.get("seconds")},
            "route": {k: steps.get("classify_scope", {}).get(k) for k in
                      ("intent", "secondary", "sub_questions", "situation", "checks")},
            "checks": steps.get("check_facts", {}),
            "coverage": steps.get("select_evidence", {}).get("coverage"),
            "citations": [{"chunk_id": c["chunk_id"], "pdf_page": c["pdf_page"], "excerpt": c["excerpt"][:300]}
                          for c in out["citations"]],
            "warnings": out["warnings"],
        })
        print(f"{qid} {out['status']:21s} {compose.get('mode')!s:17s} {seconds:5.1f}s {out['tools_used']}")
    secs = sorted(r["seconds"] for r in rows)
    summary = {"n": len(rows), "docs": docs_mode, "status": {s: sum(r["status"] == s for r in rows) for s in
                                                            {r["status"] for r in rows}},
               "template_fallback": sum(r["mode"] == "template_fallback" for r in rows),
               "seconds_median_max": [secs[len(secs) // 2], secs[-1]] if secs else None}
    print(json.dumps(summary, ensure_ascii=False))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
