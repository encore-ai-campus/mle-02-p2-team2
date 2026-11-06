"""M4 실DB 읽기 전용 재현: Tool, Citation, GraphPath와 조건부 재검색.

저장소 루트: uv run python notebooks/respiratory/m4_readonly_check.py
결과는 표준 출력으로 내보내며 DB·파일을 쓰지 않는다.
"""

from __future__ import annotations

import json
from copy import deepcopy

from index.school.dictionaries import load_dictionaries
from rag.respiratory.crossdoc_tool import crossdoc_graph_search
from rag.respiratory.disease_tool import disease_search
from rag.school.action_tool import check_graph_paths
from rag.school.capabilities import active_pages, in_scope
from rag.school.contract_v2_local import validate_query_output, validate_tool_result
from rag.school.graph import default_deps
from rag.school.main_graph import default_registry, run_main
from rag.school.validation_local import verify_against_db

SCHOOL_DOCS = [
    "moe_school_manual_v3",
    "gyeonggi_school_guide_2025",
    "kdca_measles_2026",
    "kdca_diagnosis_reporting_2026",
    "moleg_infectious_disease_act_21521",
]
CROSSDOC_DOCS = [
    "gyeonggi_school_guide_2025",
    "kdca_flu_facility_2026",
    "moe_school_manual_v3",
]
CASES = [
    ("A", "학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요?"),
    ("B", "홍역의 잠복기는 며칠이고, 홍역 학생이 생기면 학교는 무엇을 해야 하나요?"),
    ("E", "보호자에게 연락해 진료를 요청하는 일을 교육부 매뉴얼과 경기도 가이드북은 각각 어떻게 설명하나요?"),
]


def checked_result(result, request, deps):
    """구조·DB 출처·실제 그래프 경로를 독립적으로 검사한다."""
    validate_tool_result(result, request, expected_tool=result["tool_name"])
    source_problems = verify_against_db(result, deps["driver"], deps.get("database"))
    path_problems = check_graph_paths(
        result["graph_paths"], request["allowed_document_ids"], deps["driver"], deps.get("database")
    )
    scope = active_pages()
    outside_scope = [
        {"document_id": c["document_id"], "pdf_page": c["pdf_page"]}
        for c in result["citations"]
        if not in_scope(c["document_id"], c["pdf_page"], scope)
    ]
    return {
        "bad_citations": source_problems["bad_citations"],
        "bad_findings": source_problems["bad_findings"],
        "bad_paths": path_problems,
        "outside_school_selected_pages": outside_scope,
    }


def summary(result, report):
    return {
        "status": result["status"],
        "findings": len(result["findings"]),
        "citations": len(result["citations"]),
        "graph_paths": len(result["graph_paths"]),
        "citation_locations": [
            {key: citation[key] for key in ("document_id", "chunk_id", "pdf_page", "printed_page")}
            for citation in result["citations"]
        ],
        "warnings": result["warnings"],
        "verification": report,
    }


def main():
    deps = default_deps()
    deps["books"] = load_dictionaries()
    driver, database = deps["driver"], deps.get("database")
    registry = {
        **default_registry(deps),
        "disease_search": lambda request: disease_search(request, deps=deps),
        "crossdoc_graph_search": lambda request: crossdoc_graph_search(request, deps=deps),
    }
    report = {"mode": "read_only", "routing": "rule", "answer_writer": "template"}
    try:
        cross_request = {
            "question": "보호자 연락은 문서마다 누가 하나요?",
            "task": "문서별 학교 행동과 담당 비교",
            "region": "GYEONGGI",
            "allowed_document_ids": CROSSDOC_DOCS,
            "context_entity_ids": ["ActionConcept:CONTACT_GUARDIAN"],
        }
        cross = crossdoc_graph_search(cross_request, deps=deps)
        if cross["status"] == "ERROR":
            raise RuntimeError("CrossDoc 조회 실패")
        cross_report = checked_result(cross, cross_request, deps)
        report["crossdoc"] = summary(cross, cross_report)
        report["crossdoc"]["sample_graph_path"] = cross["graph_paths"][0] if cross["graph_paths"] else None

        empty_request = {**cross_request, "allowed_document_ids": []}
        empty = crossdoc_graph_search(empty_request, deps=deps)
        validate_tool_result(empty, empty_request, expected_tool="crossdoc_graph_search")
        report["empty_allowed"] = {
            "status": empty["status"],
            "findings": len(empty["findings"]),
            "citations": len(empty["citations"]),
        }

        # 실제 경로 하나의 관계 방향을 뒤집어 검증기가 오류를 잡는지 확인한다.
        flipped = deepcopy(cross["graph_paths"][0])
        edge = next(e for e in flipped["edges"] if e["relationship_type"] == "HAS_CHUNK")
        edge["source_ref"], edge["target_ref"] = edge["target_ref"], edge["source_ref"]
        report["reversed_edge_detected"] = bool(check_graph_paths([flipped], CROSSDOC_DOCS, driver, database))

        report["main_cases"] = []
        for label, question in CASES:
            request = {
                "question": question,
                "region": "GYEONGGI",
                "allowed_document_ids": SCHOOL_DOCS,
                "request_id": f"resp-m4-{label}",
            }
            result = run_main(request, deps, registry)
            validate_query_output(result, request)
            source_problems = verify_against_db(result, driver, database)
            tool_results = next(t["results"] for t in deps["main_trace"] if t["step"] == "tool_results")
            paths = [path for tool in tool_results for path in tool["graph_paths"]]
            report["main_cases"].append({
                "id": label,
                "question": question,
                "status": result["status"],
                "tools_used": result["tools_used"],
                "rounds": result["search_rounds"],
                "findings": len(result["findings"]),
                "citations": len(result["citations"]),
                "bad_citations": source_problems["bad_citations"],
                "bad_findings": source_problems["bad_findings"],
                "graph_paths": len(paths),
                "bad_paths": check_graph_paths(paths, SCHOOL_DOCS, driver, database),
                "decisions": [
                    {key: event.get(key) for key in ("status", "decision", "next_tool")}
                    for event in deps["main_trace"] if event.get("step") == "evaluate_evidence"
                ],
            })

        # 로컬 질병 사전에는 있으나 DB Disease 노드가 없을 수 있는 사례.
        men_question = "수막구균 감염증 잠복기는?"
        men_docs = ["kdca_respiratory_2026"]
        men_tool_request = {
            "question": men_question,
            "task": "질병별 사실과 원문 근거 검색",
            "region": "NATIONAL",
            "allowed_document_ids": men_docs,
        }
        men_tool = disease_search(men_tool_request, deps=deps)
        men_checked = checked_result(men_tool, men_tool_request, deps)
        men_query = {"question": men_question, "region": "NATIONAL", "allowed_document_ids": men_docs}
        men_main = run_main(men_query, deps, registry)
        validate_query_output(men_main, men_query)
        men_main_check = verify_against_db(men_main, driver, database)
        report["meningococcal"] = {
            "tool": summary(men_tool, men_checked),
            "returned_disease_ids": sorted({d for f in men_tool["findings"] for d in f["disease_ids"]}),
            "main": {
                "status": men_main["status"],
                "tools_used": men_main["tools_used"],
                "findings": len(men_main["findings"]),
                "citations": len(men_main["citations"]),
                "expected_page_ranks": [
                    i for i, citation in enumerate(men_main["citations"], 1)
                    if citation["document_id"] == "kdca_respiratory_2026" and citation["pdf_page"] == 90
                ],
                "warnings": men_main["warnings"],
                "bad_citations": men_main_check["bad_citations"],
                "bad_findings": men_main_check["bad_findings"],
            },
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
