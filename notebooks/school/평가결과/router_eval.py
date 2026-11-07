"""규칙 분류기 vs LLM 라우터 — 분류만 비교한다(Tool 호출·DB 없음). 2026-11-06.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/router_eval.py <결과.json> [반복 횟수=2]

질문셋
- golden: data/specs/golden/school.json 40문항. 기대값 = route(주 Tool 또는 GENERAL/OUT_OF_SCOPE).
- everyday: 골든 밖 일상 표현 20문항(Claude 작성, 11/06 진단과 같은 문항). 기대값에 허용 답이 여러 개일 수 있다.
- staff: 교직원 실사용형 15문항(staff_questions_probe.py와 같은 문항). 주 의도 기대값 + 하위 질문 분해를 사람이 읽는다.
채점: 주 의도(또는 GENERAL/OUT_OF_SCOPE)가 허용 답에 있으면 정답. 보조 의도는 채점하지 않고 기록만 한다.
LLM 결과는 반복 실행해 같은 답을 내는지(일관성)도 잰다.
"""

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "notebooks/school/평가결과")
from dotenv import load_dotenv
from staff_questions_probe import QUESTIONS as STAFF

from index.school.dictionaries import load_dictionaries
from rag.school.main_graph import classify_question
from rag.school.router import make_router

load_dotenv(".env")
ROUTE_LABEL = {"action_search": "ACTION", "disease_search": "DISEASE", "legal_search": "LEGAL",
               "crossdoc_graph_search": "CROSSDOC", "(없음: OUT_OF_SCOPE)": "OUT_OF_SCOPE",
               "(없음: GENERAL)": "GENERAL"}
EVERYDAY = [
    ("우리 반 애가 홍역이래요. 언제까지 학교 못 와요?", "ACTION|DISEASE"),
    ("수두 걸린 아이 동생도 학교 보내도 되나요?", "ACTION"),
    ("홍역은 어떻게 옮아요?", "DISEASE"),
    ("홍역 걸리면 열이 며칠이나 나요?", "DISEASE"),
    ("결핵 환자 생기면 보건소에 꼭 알려야 하는 거 맞죠?", "LEGAL"),
    ("학교장이 신고 안 하면 처벌받나요?", "LEGAL"),
    ("법 때문에 꼭 알려야 하는 병이 뭐예요?", "LEGAL"),
    ("교육부 매뉴얼이랑 경기도 가이드북에서 등교중지 기간이 똑같아요?", "CROSSDOC"),
    ("경기도 지침은 교육부 거랑 뭐가 달라요?", "CROSSDOC"),
    ("독감 유행하면 체육대회 취소해야 하나요?", "ACTION"),
    ("오늘 열나는 애가 3명이에요. 뭐부터 하죠?", "ACTION"),
    ("아이가 열이 나요. 해열제 먹여도 되나요?", "OUT_OF_SCOPE"),
    ("코로나 백신 어디서 맞아요?", "OUT_OF_SCOPE"),
    ("오늘 점심 메뉴 추천해줘", "OUT_OF_SCOPE"),
    ("성홍열 확진 학생 반 친구들도 검사 받아야 하나요? 그리고 성홍열은 잠복기가 얼마예요?", "ACTION|DISEASE"),
    ("보건교사 없을 때는 누가 대신해요?", "ACTION"),
    ("감염병 생기면 학부모한테 문자 어떻게 보내요?", "ACTION"),
    ("결핵은 법정감염병 몇 급이에요?", "LEGAL|DISEASE"),
    ("홍역 의심 학생 발생 시 학교의 법적 의무와 조치를 알려줘", "LEGAL|ACTION"),
    ("인플루엔자랑 코로나 증상 차이가 뭐예요?", "DISEASE"),
]
STAFF_EXPECT = {"A06": "ACTION|DISEASE"}       # 나머지 교직원 질문은 학교가 할 일(ACTION)
# 홀드아웃: 라우터 프롬프트 v1을 쓴 뒤 새로 작성해 고정한 문항(프롬프트 작성 때 보지 않음). 프롬프트 조정에 쓰지 않는다.
# 마지막 열 = 함께 물은 보조 의도(복합 질문 분해 확인용, 채점은 주 의도만).
HOLDOUT = [
    ("볼거리 걸린 애는 며칠 쉬어야 해요?", "ACTION|DISEASE", ""),
    ("수족구는 어른한테도 옮나요?", "DISEASE", ""),
    ("홍역 예방접종 두 번 다 맞았으면 안 걸리나요?", "DISEASE", ""),
    ("인플루엔자 학생 등교중지 해제는 누가 판단해요?", "ACTION", ""),
    ("감염병 의심 학생을 집에 보낼 때 혼자 보내도 되나요?", "ACTION", ""),
    ("학교에서 코로나 환자 나오면 보건소에 학교가 직접 신고해야 해요?", "LEGAL", ""),
    ("의사가 신고했으면 학교는 신고 안 해도 되나요?", "LEGAL", ""),
    ("신고 늦게 하면 학교장한테 불이익 있나요?", "LEGAL", ""),
    ("관찰실 운영 기준이 교육부랑 경기도랑 다른가요?", "CROSSDOC", ""),
    ("두 지침에서 능동감시 기간을 다르게 적었나요?", "CROSSDOC", ""),
    ("이번 주에 같은 학년에서 결석이 갑자기 늘었는데 뭘 확인해야 하나요?", "ACTION", ""),
    ("보건실에 온 학생이 발진이 있는데 수두 맞아요?", "OUT_OF_SCOPE", ""),
    ("우리 애 해열제 몇 시간 간격으로 먹이면 돼요?", "OUT_OF_SCOPE", ""),
    ("내일 날씨 어때?", "OUT_OF_SCOPE", ""),
    ("이 챗봇은 어떤 자료를 보고 답해요?", "GENERAL", ""),
    ("고마워요!", "GENERAL", ""),
    ("홍역 환자 나오면 교직원도 접종 확인해야 하나요? 홍역 증상은 어떤 게 있어요?", "ACTION", "DISEASE"),
    ("결핵 확진 학생이 나왔는데 학교가 할 일이랑 법적으로 신고해야 하는지 알려주세요", "ACTION|LEGAL", "LEGAL|ACTION"),
    ("노로바이러스는 어떻게 전파돼요?", "DISEASE", ""),
    ("휴업은 누가 결정해요?", "ACTION", ""),
]

# 홀드아웃 2: 신고 라우팅 규칙(학교 신고 의무 질문 = 학교 문서 먼저, 법령 보조)을 정한 직후, 실행 결과를 보기 전에 작성해 고정했다.
# 프롬프트·규칙 조정에 쓰지 않는다. 기대값은 Claude가 정했다(팀 검수 전). 'BOTH:ACTION+LEGAL' = 두 Tool이 주·보조 어디든 잡히면 정답.
HOLDOUT2 = [
    ("유행성이하선염 환자가 생기면 학교장은 보건소에 신고해야 하나요?", "BOTH:ACTION+LEGAL"),
    ("수막구균 감염이 의심되면 학교가 따로 신고해야 하나요?", "BOTH:ACTION+LEGAL"),
    ("신고 대상 감염병 목록에 학교가 알아야 하는 질병이 뭐가 있어요?", "ACTION"),
    ("감염병예방법에서 신고를 안 한 관리인에게 과태료가 있나요?", "LEGAL"),
    ("법적으로 학교가 환자 정보를 보건소에 제공해야 하나요?", "LEGAL"),
    ("신고를 안 하면 처벌되는 감염병이 있나요?", "LEGAL"),
    ("풍진에 걸리면 임신한 교사에게 위험한가요?", "DISEASE"),
    ("수두는 예방접종을 몇 번 맞아야 하나요?", "DISEASE"),
    ("지금 학급에 확진자가 나왔는데 오늘 급식은 어떻게 해야 하나요?", "ACTION"),
    ("교실 소독은 누가 얼마나 자주 하나요?", "ACTION"),
    ("결석한 학생에게 학교가 먼저 연락해야 하나요?", "ACTION"),
    ("교육부와 경기도 자료가 학부모 안내 시점을 다르게 말하나요?", "CROSSDOC"),
    ("우리 아이 열이 나는데 병원 어디가 좋아요?", "OUT_OF_SCOPE"),
    ("성홍열 같은데 항생제 먹여도 될까요?", "OUT_OF_SCOPE"),
    ("이 서비스에서 물어볼 수 있는 질문 예시를 보여주세요", "GENERAL"),
    ("수족구 학생이 나오면 학교는 어떻게 하고 전염 기간은 얼마예요?", "BOTH:ACTION+DISEASE"),
]
# 11/06 기대값 수정(원래 LEGAL 주): 법 조항·처벌을 말하지 않고 학교의 신고 의무만 묻는 질문은 학교 문서 + 법령 둘 다 필요하다.
# 같은 날 S-Q03 분석(법령만 부르면 막다른 길)에서 나온 원칙이며, 결과를 본 뒤 바꾼 것이므로 원래 기대 결과도 함께 기록한다.
BOTH_OVERRIDE = {"E05", "H06", "H07"}
ORIGINAL_EXPECTED = {"E05": "LEGAL", "H06": "LEGAL", "H07": "LEGAL"}

golden = json.loads(Path("data/specs/golden/school.json").read_text(encoding="utf-8"))
SETS = {"golden": [(q["id"], q["question"], ROUTE_LABEL[q["route"]]) for q in golden["questions"]],
        "everyday": [(f"E{i:02d}", q, exp) for i, (q, exp) in enumerate(EVERYDAY, 1)],
        "staff": [(qid, q, STAFF_EXPECT.get(qid, "ACTION")) for qid, _, q in STAFF],
        "holdout": [(f"H{i:02d}", q, exp) for i, (q, exp, _) in enumerate(HOLDOUT, 1)],
        "holdout2": [(f"K{i:02d}", q, exp) for i, (q, exp) in enumerate(HOLDOUT2, 1)]}
for _name, _items in SETS.items():       # 수정한 기대값 반영
    SETS[_name] = [(i, q, "BOTH:ACTION+LEGAL" if i in BOTH_OVERRIDE else e) for i, q, e in _items]
HOLDOUT_SECONDARY = {f"H{i:02d}": sec for i, (_, _, sec) in enumerate(HOLDOUT, 1) if sec}
repeat = int(sys.argv[2]) if len(sys.argv) > 2 else 2

books = load_dictionaries()
router = make_router()


def graded_ok(primary: str, secondary: list, expected: str) -> bool:
    if expected.startswith("BOTH:"):
        return set(expected[5:].split("+")) <= {primary, *secondary}
    return primary in expected.split("|")


def label(result: dict) -> str:
    return result["intent"] if result["response_type"] == "DOMAIN" else result["response_type"]


rows, summary = [], {}
for set_name, items in SETS.items():
    for qid, question, expected in items:
        rule = classify_question(question, books)
        runs = []
        for _ in range(repeat):
            started = time.perf_counter()
            try:
                result, error = router(question, rule), None
            except Exception as exc:  # noqa: BLE001 - 실패도 결과로 기록한다
                result, error = rule, f"{type(exc).__name__}: {exc}"
            runs.append({"label": label(result), "secondary": result.get("secondary", []),
                         "sub_questions": result.get("sub_questions", {}), "reason": result.get("reason", ""),
                         "seconds": round(time.perf_counter() - started, 2), "error": error})
        row = {"set": set_name, "id": qid, "question": question, "expected": expected,
               "expected_original": ORIGINAL_EXPECTED.get(qid),
               "rule": label(rule), "rule_secondary": rule.get("secondary", []),
               "rule_ok": graded_ok(label(rule), rule.get("secondary", []), expected),
               "llm": runs[0]["label"], "llm_ok": graded_ok(runs[0]["label"], runs[0]["secondary"], expected),
               "llm_all_ok": all(graded_ok(r["label"], r["secondary"], expected) for r in runs),
               "consistent": len({(r["label"], tuple(r["secondary"])) for r in runs}) == 1, "runs": runs}
        if qid in HOLDOUT_SECONDARY:   # 복합 질문: 주 의도 외에 함께 물은 의도가 주·보조 어딘가에 잡혔는가
            got = {runs[0]["label"], *runs[0]["secondary"]}
            row["compound_ok"] = set(HOLDOUT_SECONDARY[qid].split("|")) <= got or len(got) >= 2
        rows.append(row)
        mark = lambda ok: "O" if ok else "X"
        print(f"{set_name:8s} {qid:6s} 기대 {expected:15s} 규칙 {mark(row['rule_ok'])} {row['rule']:12s} "
              f"LLM {mark(row['llm_ok'])} {row['llm']:12s} 보조 {runs[0]['secondary']} "
              f"{'' if row['consistent'] else '(반복 불일치)'} {runs[0]['seconds']}s")
    part = [r for r in rows if r["set"] == set_name]
    summary[set_name] = {"n": len(part), "rule_ok": sum(r["rule_ok"] for r in part),
                         "llm_ok": sum(r["llm_ok"] for r in part), "llm_all_runs_ok": sum(r["llm_all_ok"] for r in part),
                         "consistent": sum(r["consistent"] for r in part)}
seconds = [run["seconds"] for r in rows for run in r["runs"]]
summary["latency"] = {"median": statistics.median(seconds), "p90": sorted(seconds)[int(len(seconds) * 0.9)],
                      "max": max(seconds), "errors": sum(bool(run["error"]) for r in rows for run in r["runs"])}
summary["multi_intent"] = sum(len(r["runs"][0]["sub_questions"]) > 1 for r in rows)
print(json.dumps(summary, ensure_ascii=False, indent=2))
Path(sys.argv[1]).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
