"""최종 인용 8개를 유지하면서 중간 검색 후보 수만 비교한다.

실행: uv run python notebooks/respiratory/golden_candidate_width_check.py
DB·파일을 쓰지 않고 결과 JSON을 표준 출력한다.
"""

from __future__ import annotations

import json

from golden_readonly_check import CASES, fingerprint, run_case

from index.school.dictionaries import load_dictionaries
from rag.respiratory import disease_tool as tool
from rag.school.capabilities import active_pages
from rag.school.graph import default_deps


def main():
    deps = default_deps()
    deps["books"] = load_dictionaries()
    driver, database = deps["driver"], deps.get("database")
    original_search = tool.search_school_chunks
    try:
        allowed = tool._all_document_ids(driver, database)
        scope = active_pages()
        cases = []
        for case_id, question, recorded_answers in CASES:
            answers = list(recorded_answers)
            if case_id == "R-Q3":
                answers.extend([
                    ("참고", "kdca_respiratory_2026", 115),
                ])
            observations = []
            for width in (32, 48, 64):
                def width_search(*args, _width=width, **kwargs):
                    kwargs["k"] = _width
                    return original_search(*args, **kwargs)

                tool.search_school_chunks = width_search
                result = run_case(case_id, question, answers, allowed, deps, scope)
                observations.append({"candidate_k": width, **result})
            cases.append({"id": case_id, "results": observations})
        print(json.dumps({
            "scope": "Tool 단독, 전체 허용 문서, 최종 top_k=8; 중간 후보 수만 변경",
            "limitations": "골든 질문을 사용한 순차 실험; 독립 시험셋이나 고정 DB A/B 아님",
            "allowed_document_ids": allowed,
            "sha256": {path: fingerprint(path) for path in (
                "data/specs/golden/respiratory.md",
                "data/specs/source_manifest.csv",
                "src/rag/respiratory/disease_tool.py",
                "src/rag/school/search.py",
                "notebooks/respiratory/golden_candidate_width_check.py",
            )},
            "cases": cases,
        }, ensure_ascii=False, indent=2))
    finally:
        tool.search_school_chunks = original_search
        driver.close()


if __name__ == "__main__":
    main()
