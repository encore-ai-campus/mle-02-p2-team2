"""UI 서비스 계층 — 화면과 메인 LangGraph 사이의 조립 지점(composition root).

- 메인 그래프(rag.school.main_graph)는 다른 담당자 폴더를 import하지 않는다(팀 규칙). 대신 이 공통 UI 계층이
  각 담당자 Tool을 registry로 묶어 넘긴다. 평가 스크립트(golden_eval_main.py --all-tools)와 같은 조립이다.
- 자격 증명은 환경 변수로만 받는다. 로컬은 루트 `.env`, Streamlit Community Cloud는 앱 Secrets의 최상위 키
  (NEO4J_URI·NEO4J_USERNAME·NEO4J_PASSWORD·NEO4J_DATABASE·OPENAI_API_KEY)가 환경 변수로 노출된다.
  접속 문자열·키를 화면·로그에 출력하지 않는다.
- driver·사전·임베더는 프로세스당 한 번 만들고(화면이 캐시), 요청마다 deps를 얕게 복사해 실행 기록(main_trace)이
  동시 사용자끼리 섞이지 않게 한다.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

TOOL_LABELS = {
    "action_search": "학교 행동 검색 (ActionSearchTool · 주승우)",
    "disease_search": "질병 정보 검색 (DiseaseSearchTool · 송수림)",
    "legal_search": "법령·신고 검색 (LegalSearchTool · 유종혁)",
    "crossdoc_graph_search": "문서 횡단 그래프 (CrossDocGraphTool · 송수림)",
}
# 화면에 그대로 알리는 Tool 한계(사실만 적는다).
TOOL_LIMITS = {
    "legal_search": "현재 고정 샘플 질문 1개만 처리합니다. 다른 법령·신고 질문은 근거 부족으로 끝날 수 있습니다.",
}
STATUS_LABELS = {"ANSWERED": "답변 완료", "PARTIAL": "일부 확인", "INSUFFICIENT_EVIDENCE": "근거 부족",
                 "ERROR": "실행 오류"}
RESPONSE_LABELS = {"DOMAIN": "감염병 대응 질문", "GENERAL": "일반 안내", "OUT_OF_SCOPE": "지원 범위 밖"}
# 골든셋 평가에 쓴 문서 묶음(기본 선택). 나머지 DB 문서는 화면에서 추가할 수 있다.
EVALUATED_DOCUMENTS = ["moe_school_manual_v3", "gyeonggi_school_guide_2025", "kdca_measles_2026",
                       "kdca_diagnosis_reporting_2026", "moleg_infectious_disease_act_21521"]


def build_resources() -> dict:
    """driver·database·embedder·사전·문서 메타데이터. 호출자가 캐시하고 프로세스 종료 때 driver를 닫는다."""
    from index.school.dictionaries import load_dictionaries
    from rag.school.graph import default_deps

    deps = default_deps()               # 루트 .env가 없으면(클라우드) 이미 설정된 환경 변수를 그대로 쓴다
    deps["books"] = load_dictionaries()
    if os.getenv("OPENAI_API_KEY"):     # 답변 다듬기(11/07). 키가 없으면 템플릿 답변만 쓴다.
        from rag.school.answer_writer import make_answer_writer
        deps["answer_writer"] = make_answer_writer()
    with deps["driver"].session(database=deps["database"]) as session:
        documents = {r["id"]: {"title": r["title"], "version": r["version"], "issuer": r["issuer"]}
                     for r in session.run("MATCH (d:Document) RETURN d.document_id AS id, d.title AS title, "
                                          "d.version AS version, d.issuer AS issuer ORDER BY id")}
    return {"deps": deps, "documents": documents}


def build_registry(deps: dict) -> dict:
    """4개 Tool registry. 다른 담당자 Tool은 이 함수 안에서만 import한다."""
    from rag.prevention_legal.tool import legal_search
    from rag.respiratory.crossdoc_tool import crossdoc_graph_search
    from rag.respiratory.disease_tool import disease_search
    from rag.school.main_graph import default_registry

    return {**default_registry(deps), "legal_search": legal_search,
            "disease_search": lambda tool_input: disease_search(tool_input, deps=deps),
            "crossdoc_graph_search": lambda tool_input: crossdoc_graph_search(tool_input, deps=deps)}


def ask(resources: dict, question: str, region: str, allowed_document_ids: list[str], polish: bool = True) -> dict:
    """질문 하나를 메인 그래프로 실행한다. 반환: output(QueryOutput)·trace·tool_results·seconds·request_id.
    polish=False면 답변 다듬기를 끄고 템플릿 답변만 쓴다."""
    from rag.school.main_graph import run_main

    deps = dict(resources["deps"])      # 공유 자원(driver·사전)은 그대로, 실행 기록만 요청별로 분리
    if not polish:
        deps.pop("answer_writer", None)
    request_id = f"ui-{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    output = run_main({"question": question, "region": region, "allowed_document_ids": list(allowed_document_ids),
                       "request_id": request_id}, deps, build_registry(deps))
    trace = deps["main_trace"]
    tool_results = next((t["results"] for t in trace if t["step"] == "tool_results"), [])
    return {"output": output, "trace": [t for t in trace if t["step"] != "tool_results"],
            "tool_results": tool_results, "seconds": round(time.perf_counter() - started, 2),
            "request_id": request_id}
