"""교직원 실사용형 질문 탐색(정답 없음, 읽기 전용 DB + 실LLM). 2026-11-06.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/staff_questions_probe.py <결과.json>

골든셋 밖의 실제 교직원 질문(학부모 응대, 지금 할 일·이후 할 일, 행정 처리)을 UI와 같은 조립(service.ask)으로 실행한다.
허용 문서는 DB의 전체 Document. 정답이 없으므로 채점하지 않고 라우팅·호출 Tool·status·근거 수·답변을 기록해 사람이 읽는다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
from rag.ui.service import ask, build_resources

QUESTIONS = [
    ("P01", "학부모 응대", "학부모가 우리 아이 반에 수두 환자가 나왔다는데 등교시켜도 되냐고 물어요. 어떻게 답하면 될까요?"),
    ("P02", "학부모 응대", "홍역 확진 학생이 등교했던 걸 뒤늦게 알았어요. 같은 반 학부모들에게 뭐라고 안내해야 하나요?"),
    ("P03", "학부모 응대", "학부모가 진단서 없이 등교중지를 풀어 달라고 하는데 어떻게 하죠?"),
    ("P04", "학부모 응대", "학부모가 다른 반에 감염자가 있다는데 왜 알려주지 않냐고 항의해요. 어떻게 응대하죠?"),
    ("N01", "지금·이후", "담임인데 반 학생이 인플루엔자 확진을 받았다고 연락이 왔어요. 지금 당장 할 일과 이후에 할 일을 알려주세요."),
    ("N02", "지금·이후", "보건교사입니다. 같은 반에서 이틀 사이 설사 환자가 4명 나왔어요. 지금 뭘 해야 하나요?"),
    ("N03", "지금·이후", "교감입니다. 유행의심 상황이면 교육청에는 언제, 어떻게 보고하나요?"),
    ("N04", "지금·이후", "결핵 확진 교사가 나왔어요. 학교는 무엇을 해야 하나요?"),
    ("N05", "지금·이후", "감염병 유행이 끝나면 학교에서 마무리로 해야 할 일은 뭔가요?"),
    ("A01", "행정·운영", "등교중지된 학생은 출석 처리를 어떻게 하나요?"),
    ("A02", "행정·운영", "감염병 가정통신문에는 어떤 내용을 넣어야 하나요?"),
    ("A03", "행정·운영", "급식실 조리원이 장염 증상이 있어요. 어떻게 해야 하나요?"),
    ("A04", "행정·운영", "수학여행 가기 전에 감염병 관련해서 준비할 게 있나요?"),
    ("A05", "행정·운영", "신학기 전에 감염병 예방을 위해 학교가 미리 해 둘 일은 뭔가요?"),
    ("A06", "행정·운영", "학생이 코로나 양성인데 증상이 없대요. 학교에 와도 되나요?"),
]


def main() -> None:
    res = build_resources()
    allowed = sorted(res["documents"])
    rows = []
    for qid, kind, question in QUESTIONS:
        out = ask(res, question, "GYEONGGI", allowed)
        o, trace = out["output"], out["trace"]
        intent = next((t for t in trace if t["step"] == "classify_intent"), {})
        compose = next((t for t in trace if t["step"] == "compose_answer"), {})
        rows.append({"id": qid, "kind": kind, "question": question, "response_type": o["response_type"],
                     "intent": intent.get("intent"), "secondary": intent.get("secondary"),
                     "tools_used": o["tools_used"], "status": o["status"], "findings": len(o["findings"]),
                     "citations": len(o["citations"]), "documents": sorted({c["document_id"] for c in o["citations"]}),
                     "mode": compose.get("mode"), "problems": compose.get("problems", []), "seconds": out["seconds"],
                     "answer": o["answer"], "claims": [f["claim"][:160] for f in o["findings"]],
                     "warnings": o["warnings"]})
        r = rows[-1]
        print(f"{qid} {r['response_type']:12s} {r['intent']!s:8s} {r['tools_used']} {r['status']:21s} "
              f"F{r['findings']} C{r['citations']} {r['mode']} {r['seconds']}s")
    res["deps"]["driver"].close()
    Path(sys.argv[1]).write_text(json.dumps({"allowed": allowed, "rows": rows}, ensure_ascii=False, indent=1),
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
