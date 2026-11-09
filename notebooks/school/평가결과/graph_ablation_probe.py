"""지식그래프 유무 비교 측정 (주승우 / school, 2026-11-09). DB 읽기 전용.

같은 메인 그래프(라우터·판단 기준 조회·근거 선별·답변 작성·줄 검사)를 두 조건으로 돌린다.
- full   : 현재 서비스 그대로.
- nograph: 청크 검색(질문+문서 용어 질문 hybrid RRF)과 원문 인용만 쓴다. 학교 행동·역할·개념·질병 관계 조회를
           모두 끈다(expand_school_actions·get_role_actions·scan_actions·get_action_evidence를 빈 결과로 바꿈).
           disease_search·crossdoc_graph_search 이름도 같은 청크 전용 Tool로 연결한다(법령 Tool은 그대로).
두 조건의 차이는 '그래프 관계로 찾기·묶기'뿐이다. 허용 문서·선정 쪽수·DB 대조는 같다.

정답(gold)은 DB 그래프에서 만든다: 개념(INSTANCE_OF)·질병(FOR_DISEASE)·역할×단계(DONE_BY)에 속한 행동이 근거로 삼는 청크.
따라서 '그래프가 가리키는 범위를 청크 검색이 얼마나 따라가는가'를 재는 것이고, 독립 정답이 아니다(결과 문서의 한계 참고).

    uv run python notebooks/school/평가결과/graph_ablation_probe.py <full|nograph> <out.json> [반복] [--only R1,C1]
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from rag.ui import service

# (id, 유형, 질문, gold 정의)
QUESTIONS = [
    ("R1", "역할×단계", "감염병 확진 학생이 2명 이상 나온 상황에서 담임교사가 해야 할 일을 모두 알려주세요.",
     {"kind": "role", "role": "HOMEROOM_TEACHER", "stage": "RESPONSE_3"}),
    ("R2", "역할×단계", "의심 증상 학생이 처음 발견됐을 때 보건교사가 해야 할 일은 무엇인가요?",
     {"kind": "role", "role": "HEALTH_TEACHER", "stage": "RESPONSE_1"}),
    ("R3", "역할", "학교장이 하는 일에는 어떤 것들이 있나요?",
     {"kind": "role", "role": "PRINCIPAL", "stage": None}),
    ("R4", "역할", "행정실은 감염병 대응에서 무슨 일을 맡나요?",
     {"kind": "role", "role": "ADMIN_OFFICE", "stage": None}),
    ("C1", "개념 비교", "보호자에게 감염병 발생을 알리는 일은 교육부 매뉴얼, 경기도 가이드북, 질병관리청 지침에서 각각 어떻게 안내하나요?",
     {"kind": "concept", "concept": "INFORM_GUARDIANS_NOTICE"}),
    ("C2", "개념 비교", "아픈 학생을 병원에 보내 진료받게 하는 일은 문서마다 어떻게 안내하나요?",
     {"kind": "concept", "concept": "REQUEST_MEDICAL_VISIT"}),
    ("C3", "개념 비교", "교실 환기와 소독은 문서별로 어떻게 하라고 하나요?",
     {"kind": "concept", "concept": "VENTILATE_DISINFECT"}),
    ("C4", "개념 비교", "보건소 역학조사에 학교는 어떻게 협조해야 하는지 문서별로 알려주세요.",
     {"kind": "concept", "concept": "COOPERATE_HEALTH_AUTHORITY"}),
    ("C5", "개념 비교", "결석·조퇴한 학생을 살피는 능동감시는 문서별로 어떻게 다른가요?",
     {"kind": "concept", "concept": "ACTIVE_SURVEILLANCE"}),
    ("D1", "질병별", "홍역 학생이 나오면 학교가 해야 할 일은 무엇인가요?",
     {"kind": "disease", "disease": "MEASLES"}),
    ("D2", "질병별", "백일해 환자가 생기면 학교는 어떤 조치를 하나요?",
     {"kind": "disease", "disease": "PERTUSSIS"}),
    ("D3", "질병별", "유행성이하선염(볼거리)이 발생하면 학교는 무엇을 하나요?",
     {"kind": "disease", "disease": "MUMPS"}),
    ("D4", "질병별", "수두에 걸린 학생이 있으면 학교가 해야 할 일은?",
     {"kind": "disease", "disease": "VARICELLA"}),
    ("D5", "질병별", "결핵 환자가 학교에서 확인되면 학교는 무엇을 해야 하나요?",
     {"kind": "disease", "disease": "TUBERCULOSIS"}),
    ("D6", "질병별", "인플루엔자가 유행할 때 학교와 시설이 해야 할 일은?",
     {"kind": "disease", "disease": "INFLUENZA"}),
    # 대조군: 그래프 관계 없이 원문 한 곳에 답이 있는 질문(정답 쪽·핵심 문구는 11/08 지침 전용 질문 기록과 같다)
    ("K1", "대조군", "마이코플라스마 폐렴 전염기간은?",
     {"kind": "pages", "pages": [["kdca_respiratory_2026", 201], ["kdca_respiratory_2026", 193]], "key": r"20\s*일"}),
    ("K2", "대조군", "코로나19 전염 기간은 얼마나 되나요?",
     {"kind": "pages", "pages": [["kdca_covid19_2026", 28]], "key": r"24\s*시간|증상"}),
    ("K3", "대조군", "성홍열 잠복기는 얼마인가요?",
     {"kind": "pages", "pages": [["kdca_respiratory_2026", 109]], "key": r"1\s*[~-]\s*7"}),
]

# 2차 세트(--set extra): 1차에서 개념 비교가 가장 크게 갈려서, 개념 9개와 역할×질병 2개를 더 확인한다.
EXTRA_QUESTIONS = [
    ("X1", "개념 비교", "학생들에게 감염병 예방 교육을 하라는 내용은 문서마다 어떻게 나와 있나요?",
     {"kind": "concept", "concept": "HEALTH_EDUCATION"}),
    ("X2", "개념 비교", "아픈 학생을 처음 발견하고 확인하는 일은 문서별로 어떻게 하라고 하나요?",
     {"kind": "concept", "concept": "DETECT_SYMPTOMATIC"}),
    ("X3", "개념 비교", "교육청에 감염병 발생을 보고하는 일은 문서마다 어떻게 안내하나요?",
     {"kind": "concept", "concept": "REPORT_EDU_OFFICE"}),
    ("X4", "개념 비교", "밀접접촉자를 파악하고 관리하는 일은 문서별로 어떻게 다른가요?",
     {"kind": "concept", "concept": "MANAGE_CLOSE_CONTACTS"}),
    ("X5", "개념 비교", "학생을 보건실로 데려가는 일은 문서마다 어떻게 설명하나요?",
     {"kind": "concept", "concept": "ESCORT_TO_HEALTH_ROOM"}),
    ("X6", "개념 비교", "학사일정이나 교육활동을 조정하는 일은 문서별로 어떻게 나오나요?",
     {"kind": "concept", "concept": "ADJUST_SCHOOL_ACTIVITIES"}),
    ("X7", "개념 비교", "유행이 끝났다고 판단하고 정리하는 일은 문서마다 어떻게 안내하나요?",
     {"kind": "concept", "concept": "DECLARE_OUTBREAK_END"}),
    ("X8", "개념 비교", "개별 학생의 보호자에게 연락하는 일은 문서별로 어떻게 안내하나요?",
     {"kind": "concept", "concept": "CONTACT_GUARDIAN"}),
    ("X9", "개념 비교", "등교중지를 시키는 일은 문서별로 어떻게 안내하나요?",
     {"kind": "concept", "concept": "EXCLUDE_FROM_SCHOOL"}),
    ("X10", "역할×질병", "홍역 학생이 나왔을 때 보건교사가 해야 할 일은 무엇인가요?",
     {"kind": "role_disease", "role": "HEALTH_TEACHER", "disease": "MEASLES"}),
    ("X11", "역할×질병", "수두 학생이 있을 때 담임교사가 해야 할 일은 무엇인가요?",
     {"kind": "role_disease", "role": "HOMEROOM_TEACHER", "disease": "VARICELLA"}),
]

GOLD_QUERIES = {
    "role_disease": """MATCH (a:SchoolAction)-[:DONE_BY]->(:SchoolRole {role_id:$role})
MATCH (a)-[:FOR_DISEASE]->(:Disease {disease_id:$disease})
MATCH (a)-[:SUPPORTED_BY]->(c:Chunk)<-[:HAS_CHUNK]-(d:Document) WHERE d.document_id IN $allowed
RETURN DISTINCT a.action_id AS action_id, c.chunk_id AS chunk_id, d.document_id AS doc""",
    "role": """MATCH (a:SchoolAction)-[:DONE_BY]->(:SchoolRole {role_id:$role})
WHERE $stage IS NULL OR a.stage = $stage
MATCH (a)-[:SUPPORTED_BY]->(c:Chunk)<-[:HAS_CHUNK]-(d:Document) WHERE d.document_id IN $allowed
RETURN DISTINCT a.action_id AS action_id, c.chunk_id AS chunk_id, d.document_id AS doc""",
    "concept": """MATCH (a:SchoolAction)-[:INSTANCE_OF]->(:ActionConcept {concept_id:$concept})
MATCH (a)-[:SUPPORTED_BY]->(c:Chunk)<-[:HAS_CHUNK]-(d:Document) WHERE d.document_id IN $allowed
RETURN DISTINCT a.action_id AS action_id, c.chunk_id AS chunk_id, d.document_id AS doc""",
    "disease": """MATCH (a:SchoolAction)-[:FOR_DISEASE]->(:Disease {disease_id:$disease})
MATCH (a)-[:SUPPORTED_BY]->(c:Chunk)<-[:HAS_CHUNK]-(d:Document) WHERE d.document_id IN $allowed
RETURN DISTINCT a.action_id AS action_id, c.chunk_id AS chunk_id, d.document_id AS doc""",
    "pages": """MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE [d.document_id, c.pdf_page] IN $pages
RETURN DISTINCT null AS action_id, c.chunk_id AS chunk_id, d.document_id AS doc""",
}


def build_gold(res: dict, allowed: list[str], questions: list) -> dict:
    gold = {}
    with res["deps"]["driver"].session(database=res["deps"]["database"]) as session:
        for qid, _, _, spec in questions:
            rows = [dict(r) for r in session.run(GOLD_QUERIES[spec["kind"]], allowed=allowed,
                                                 **{k: v for k, v in spec.items() if k not in ("kind", "key")})]
            gold[qid] = {"actions": sorted({r["action_id"] for r in rows if r["action_id"]}),
                         "chunks": sorted({r["chunk_id"] for r in rows}),
                         "docs": sorted({r["doc"] for r in rows})}
    return gold


def nograph_registry(deps: dict) -> dict:
    """모든 그래프 Tool 이름을 청크 전용 Tool로 연결한다(법령 Tool은 그래프가 아니라 카드이므로 그대로)."""
    from rag.prevention_legal.tool import legal_search
    from rag.school.main_graph import default_registry

    base = default_registry(deps)["action_search"]

    def chunk_only(name: str):
        def call(tool_input):
            return {**base(tool_input), "tool_name": name}
        return call

    return {"action_search": base, "legal_search": legal_search,
            "disease_search": chunk_only("disease_search"), "crossdoc_graph_search": chunk_only("crossdoc_graph_search")}


def main() -> None:
    mode, out_path = sys.argv[1], Path(sys.argv[2])
    repeat = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 1
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    questions = EXTRA_QUESTIONS if "--set" in sys.argv and sys.argv[sys.argv.index("--set") + 1] == "extra" else QUESTIONS
    res = service.build_resources()
    allowed = service.default_documents(res["documents"])
    gold = build_gold(res, allowed, questions)
    patches = []
    if mode == "nograph":
        empty = lambda *a, **k: []
        patches = [mock.patch(f"rag.school.action_tool.{name}", empty)
                   for name in ("expand_school_actions", "get_role_actions", "scan_actions", "get_action_evidence")]
        patches.append(mock.patch.object(service, "build_registry", nograph_registry))
    for p in patches:
        p.start()
    rows = []
    try:
        for run in range(1, repeat + 1):
            for qid, kind, question, spec in questions:
                if only and qid not in only:
                    continue
                started = time.perf_counter()
                out = service.ask(res, question, "GYEONGGI", allowed)
                o = out["output"]
                tool_chunks = sorted({c["chunk_id"] for r in out["tool_results"] for c in r["citations"]})
                cited = [c["chunk_id"] for c in o["citations"]]
                g = gold[qid]
                gold_chunks = set(g["chunks"])
                docs_cited = sorted({c["document_id"] for c in o["citations"]})
                row = {
                    "run": run, "id": qid, "kind": kind, "question": question, "mode": mode, "status": o["status"],
                    "tools": [(t["tool"], t["status"]) for t in out["trace"] if t["step"] == "execute_tool"],
                    "gold_chunks": len(gold_chunks), "gold_docs": g["docs"],
                    "tool_candidates": len(tool_chunks), "tool_gold": len(gold_chunks & set(tool_chunks)),
                    "tool_docs": sorted({c.split(":")[0] for c in tool_chunks} & set(g["docs"])),
                    "cited": len(cited), "cited_gold": len(gold_chunks & set(cited)),
                    "cited_docs": docs_cited, "docs_covered": sorted(set(docs_cited) & set(g["docs"])),
                    "final_actions": len(o["action_ids"]), "gold_actions_final": len(set(g["actions"]) & set(o["action_ids"])),
                    "key_hit": bool(re.search(spec["key"], o["answer"])) if spec.get("key") else None,
                    "seconds": round(time.perf_counter() - started, 1), "answer": o["answer"],
                }
                rows.append(row)
                print(f'{run} {qid} {mode} {row["status"]:21s} gold={row["gold_chunks"]} tool_gold={row["tool_gold"]} '
                      f'cited={row["cited"]} cited_gold={row["cited_gold"]} docs={len(row["docs_covered"])}/{len(g["docs"])} '
                      f'{row["seconds"]}s', flush=True)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps({"gold": gold, "rows": rows}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    main()
