"""2026 질병별 지침 전용 질문 측정 (주승우 / school, 2026-11-08). 읽기 전용.

학교 문서(교육부 매뉴얼 3차 개정판·경기도 가이드북 2024)에는 답이 없고 질병관리청 2026 지침에만 답이 있는 질문(K*),
그리고 학교 문서와 2026 지침이 다르게 말하는 질문(C*)을 화면과 같은 메인 그래프로 실행한다.
정답 쪽·핵심 문구는 DB 청크 원문에서 확인한 값이다(2026-11-08). 질문은 Claude가 지침 원문을 보고 만들었다.

판정(자동, 사람 확인 전):
- gold_hit: 최종 인용에 정답 문서·쪽이 있는가
- key_hit: 답변에 핵심 문구(정규식)가 있는가
- first_key: 첫 문단(결론)에 핵심 문구가 있는가 — C*에서는 '2026 지침 기준이 결론에 오는가'

    uv run python notebooks/school/평가결과/guideline_only_probe.py <out.json> [--only K01,C01]
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from rag.ui import service  # noqa: E402

R = "kdca_respiratory_2026"
QUESTIONS = [
    ("K01", "마이코플라스마 폐렴 전염기간은?", [(R, 193), (R, 67)], r"20\s*일"),
    ("K02", "마이코플라스마 폐렴 진단서에 등교중지 기간이 안 적혀 있어요. 며칠 쉬게 하고 출석인정 해야 하나요?",
     [(R, 201), (R, 193), (R, 67), (R, 68)], r"급성기"),
    ("K03", "마이코플라스마 폐렴 잠복기는?", [(R, 193), (R, 197)], r"2\s*[~∼-]\s*3\s*주"),
    # K04~K11·K16(아데노·RSV·리노·메타뉴모·파라인플루엔자·클라미디아·수막구균·사람 코로나)은 DB Disease 11개 밖이라
    # 서비스 범위가 아니다(11/08 승우 님). 1차 실행에는 있었지만 집계에서 뺐고 목록에서도 지웠다.
    ("K12", "인플루엔자 학생이 해열제를 먹었다면 언제부터 등교할 수 있나요?", [("kdca_flu_facility_2026", 17)],
     r"48\s*시간"),
    ("K13", "코로나19 전염기간은?", [("kdca_covid19_2026", 39)], r"1\s*[~∼-]\s*3\s*일"),
    ("K14", "성홍열 환자 접촉자는 며칠까지 추가 발생을 감시하나요?", [(R, 118)], r"7\s*일"),
    ("K15", "홍역 환자의 전염기간은 언제까지인가요?", [("kdca_measles_2026", 93), ("kdca_measles_2026", 94)],
     r"4\s*일"),
    ("K17", "코로나19 잠복기는?", [("kdca_covid19_2026", 43), ("kdca_covid19_2026", 48)], r"1\s*[~∼-]\s*14\s*일"),
    ("K18", "백일해 환자와 접촉한 학생은 예방적 항생제를 먹어야 하나요?", [("kdca_vaccine_preventable_2026", 116)],
     r"3\s*주"),
    ("K19", "수족구병은 다 나은 뒤에도 바이러스가 나오나요?", [("kdca_hfmd_2026", 30)], r"8\s*주"),
    ("K20", "홍역 환자와 같은 반 학생(접촉자)은 어떻게 관리하나요?", [("kdca_measles_2026", 30)], r"예방접종"),
    ("K21", "마이코플라스마 폐렴 걸린 학생은 언제부터 등교할 수 있나요?", [(R, 201)], r"급성기"),
    ("C01", "수족구병 진단받은 학생, 언제부터 등교시키면 되나요?", [("kdca_hfmd_2026", 30)], r"수포가 마른"),
    ("C02", "코로나19 확진 학생은 언제부터 다시 등교할 수 있나요?", [("kdca_covid19_2026", 28)], r"24\s*시간"),
    ("C03", "인플루엔자 진단받은 학생은 언제까지 등교하면 안 되나요?",
     [("kdca_flu_facility_2026", 15), ("kdca_flu_facility_2026", 17)], r"24\s*시간"),
    # 교육부 매뉴얼 표(50·223쪽)는 성홍열 잠복기 '1-3일', 2026 호흡기 지침(109쪽)은 '1~7일(평균 2~5일)'.
    ("C04", "성홍열 잠복기는 며칠인가요?", [(R, 109)], r"1\s*[~∼-]\s*7\s*일"),
]


def main() -> None:
    out_path = Path(sys.argv[1])
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    res = service.build_resources()
    allowed = service.default_documents(res["documents"])
    rows = []
    for qid, question, gold, key in QUESTIONS:
        if only and qid not in only:
            continue
        started = time.perf_counter()
        out = service.ask(res, question, "GYEONGGI", allowed)
        trace = out["trace"]
        route = next((t for t in trace if t["step"] == "classify_scope"), {})
        select = next((t for t in trace if t["step"] == "select_evidence"), {})
        answer = out["output"]["answer"]
        first = answer.split("\n\n", 1)[0]
        cited = [(c["document_id"], c["pdf_page"]) for c in out["output"]["citations"]]
        tool_cited = [(c["document_id"], c["pdf_page"]) for r in out["tool_results"] for c in r["citations"]]
        gold_set = {tuple(g) for g in gold}
        rows.append({
            "id": qid, "question": question, "gold": gold, "key": key,
            "status": out["output"]["status"], "intent": route.get("intent"), "secondary": route.get("secondary"),
            "tools": [(t["tool"], t["status"]) for t in trace if t["step"] == "execute_tool"],
            "gold_in_tools": any(c in gold_set for c in tool_cited),
            "gold_hit": any(c in gold_set for c in cited),
            "key_hit": bool(re.search(key, answer)), "first_key": bool(re.search(key, first)),
            "kdca_cited": sum(d.startswith("kdca_") for d, _ in cited), "school_cited": sum(
                d in ("moe_school_manual_v3", "gyeonggi_school_guide_2025") for d, _ in cited),
            "rerank_cards": select.get("cards"), "coverage": select.get("coverage"),
            "cited": cited, "seconds": round(time.perf_counter() - started, 2), "answer": answer,
        })
        r = rows[-1]
        print(f"{qid} {r['status']:21s} {str(r['intent']):8s} tools={r['tools']} gold_in_tools={r['gold_in_tools']} "
              f"gold_hit={r['gold_hit']} key={r['key_hit']} first={r['first_key']} kdca={r['kdca_cited']} "
              f"school={r['school_cited']} {r['seconds']}s", flush=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(rows)
    print(json.dumps({"n": n, **{k: sum(bool(r[k]) for r in rows) for k in
                                 ("gold_in_tools", "gold_hit", "key_hit", "first_key")},
                      "insufficient": sum(r["status"] == "INSUFFICIENT_EVIDENCE" for r in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
