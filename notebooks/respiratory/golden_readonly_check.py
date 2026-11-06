"""호흡기 골든 질문의 현재 ToolResult와 Neo4j 근거를 읽기 전용으로 점검한다.

저장소 루트: uv run python notebooks/respiratory/golden_readonly_check.py
출력은 JSON이며 DB·파일을 변경하지 않는다. 직접 판단한 정답을 프로그램에 보정하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from index.school.dictionaries import load_dictionaries
from rag.respiratory.crossdoc_tool import crossdoc_graph_search
from rag.respiratory.disease_tool import _all_document_ids, disease_search
from rag.school.action_tool import check_graph_paths
from rag.school.capabilities import active_pages, in_scope
from rag.school.contract_v2_local import validate_tool_result
from rag.school.graph import default_deps
from rag.school.validation_local import verify_against_db

# 기준: data/specs/golden/respiratory.md의 질문·PDF 쪽. R-Q2는 양쪽을 따로 보고한다.
CASES = [
    ("R-F1", "인플루엔자 잠복기는?", [("주", "kdca_flu_facility_2026", 6)]),
    ("R-F2", "성홍열 잠복기는?", [("주", "kdca_respiratory_2026", 109)]),
    ("R-F3", "코로나19 잠복기는?", [("주", "kdca_covid19_2026", 39)]),
    ("R-F4", "코로나19 확진 학생의 격리 권고 기간은?", [("주", "kdca_covid19_2026", 28)]),
    ("R-Q1", "인플루엔자로 진단받은 학생은 언제까지 등교하면 안 되나?", [("주", "kdca_flu_facility_2026", 15)]),
    ("R-Q2", "인플루엔자 발생 시 학교(교직원)가 참고할 시설별 예방 조치는?", [
        ("주", "kdca_flu_facility_2026", 17), ("주", "kdca_flu_facility_2026", 18),
    ]),
    ("R-Q3", "성홍열 진단 학생의 등교중지 기준은?", [
        ("직접 근거", "kdca_respiratory_2026", 116),
        ("직접 근거", "kdca_respiratory_2026", 118),
        ("직접 근거", "kdca_respiratory_2026", 121),
    ]),
    ("R-Q4", "성홍열 집단발생 기준과 그때 학교가 할 일은?", [("주", "kdca_respiratory_2026", 116)]),
    ("R-T1", "인플루엔자가 유행하면 학교는 휴교해야 하나?", [
        ("주", "kdca_flu_facility_2026", 13), ("보조", "gyeonggi_school_guide_2025", 236),
    ]),
    ("T01-MEN", "수막구균 감염증 잠복기는?", [("주", "kdca_respiratory_2026", 90)]),
]
CROSS_DOCS = {
    "gyeonggi_school_guide_2025", "kdca_flu_facility_2026", "moe_school_manual_v3",
}
ROOT = Path(__file__).resolve().parents[2]


def fingerprint(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def run_case(case_id, question, answers, allowed, deps, scope):
    request = {
        "question": question,
        "task": "질병별 사실과 원문 근거 검색",
        "region": "NATIONAL",
        "allowed_document_ids": allowed,
    }
    result = disease_search(request, deps=deps)
    validate_tool_result(result, request, expected_tool="disease_search")
    if result["status"] == "ERROR":
        raise RuntimeError(f"{case_id}: Tool 조회 오류")
    check = verify_against_db(result, deps["driver"], deps.get("database"))
    bad_paths = check_graph_paths(result["graph_paths"], allowed, deps["driver"], deps.get("database"))
    answers_found = []
    for kind, document_id, page in answers:
        positions = [
            i for i, citation in enumerate(result["citations"], 1)
            if citation["document_id"] == document_id and citation["pdf_page"] == page
        ]
        answers_found.append({"kind": kind, "document_id": document_id, "pdf_page": page, "ranks": positions})
    case = {
        "id": case_id,
        "question": question,
        "tool": result["tool_name"],
        "status": result["status"],
        "expected": answers_found,
        "citations": [
            {"rank": i, **{key: citation[key] for key in ("document_id", "chunk_id", "pdf_page", "printed_page")},
             "in_school_selected_pages": in_scope(citation["document_id"], citation["pdf_page"], scope)}
            for i, citation in enumerate(result["citations"], 1)
        ],
        "warnings": result["warnings"],
        "bad_citations": check["bad_citations"],
        "bad_findings": check["bad_findings"],
        "paths_count": len(result["graph_paths"]),
        "bad_paths": bad_paths,
    }
    if case_id == "R-Q3":
        direct_ranks = [rank for answer in answers_found if answer["kind"] == "직접 근거" for rank in answer["ranks"]]
        case["best_direct_rank"] = min(direct_ranks, default=None)
    return case


def main():
    deps = default_deps()
    deps["books"] = load_dictionaries()
    driver, database = deps["driver"], deps.get("database")
    try:
        allowed = _all_document_ids(driver, database)
        scope = active_pages()
        result = {
            "scope": "Tool 단독 검색, 실행 시 DB의 전체 문서를 허용; PDF 원본 의미 판독 아님",
            "allowed_document_ids": allowed,
            "sha256": {
                str(path): fingerprint(path) for path in (
                    "data/specs/golden/respiratory.md", "data/specs/source_manifest.csv",
                    "src/rag/respiratory/disease_tool.py", "src/rag/respiratory/crossdoc_tool.py",
                    "src/rag/school/search.py", "notebooks/respiratory/golden_readonly_check.py",
                )
            },
            "cases": [run_case(case_id, question, answers, allowed, deps, scope)
                      for case_id, question, answers in CASES],
        }
        request = {
            "question": "보호자 연락은 문서마다 누가 하나요?",
            "task": "문서별 학교 행동과 담당 비교",
            "region": "NATIONAL",
            "allowed_document_ids": allowed,
            "context_entity_ids": ["ActionConcept:CONTACT_GUARDIAN"],
        }
        cross = crossdoc_graph_search(request, deps=deps)
        validate_tool_result(cross, request, expected_tool="crossdoc_graph_search")
        if cross["status"] == "ERROR":
            raise RuntimeError("T04: Tool 조회 오류")
        verification = verify_against_db(cross, driver, database)
        result["crossdoc"] = {
            "id": "T04",
            "status": cross["status"],
            "documents": sorted({citation["document_id"] for citation in cross["citations"]}),
            "required_documents": sorted(CROSS_DOCS),
            "all_required_documents": CROSS_DOCS <= {c["document_id"] for c in cross["citations"]},
            "citations": [{"rank": i, "document_id": c["document_id"], "chunk_id": c["chunk_id"],
                           "pdf_page": c["pdf_page"]} for i, c in enumerate(cross["citations"], 1)],
            "warnings": cross["warnings"],
            "bad_citations": verification["bad_citations"],
            "bad_findings": verification["bad_findings"],
            "paths_count": len(cross["graph_paths"]),
            "bad_paths": check_graph_paths(cross["graph_paths"], allowed, driver, database),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
