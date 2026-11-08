"""AI 없이 전문검색의 중간 후보 수만 비교한다. DB는 읽기 전용이다."""

from __future__ import annotations

import json
from pathlib import Path

from golden_readonly_check import CASES, fingerprint, run_case

from index.school.dictionaries import load_dictionaries
from index.school.neo4j_local import get_driver
from rag.respiratory import disease_tool as tool
from rag.school.capabilities import active_pages

ROOT = Path(__file__).resolve().parents[2]


def main():
    driver, database = get_driver(ROOT / ".env")
    deps = {"driver": driver, "database": database, "books": load_dictionaries(), "embedder": None}
    original = tool.search_school_chunks
    try:
        allowed = tool._all_document_ids(driver, database)
        scope = active_pages()
        cases = []
        for case_id, question, recorded_answers in CASES:
            answers = list(recorded_answers)
            observations = []
            for width in (32, 48, 64):
                def search_at_width(*args, _width=width, **kwargs):
                    kwargs["k"] = _width
                    return original(*args, **kwargs)

                tool.search_school_chunks = search_at_width
                result = run_case(case_id, question, answers, allowed, deps, scope)
                observations.append({"candidate_k": width, **result})
            cases.append({"id": case_id, "results": observations})
        print(json.dumps({
            "scope": "전문검색·직접 후보, 외부 AI 없음, 전체 허용 문서, 최종 top_k=8",
            "limitations": "벡터 포함 검색 아님; 골든 질문 순차 실험, 독립 시험셋 아님",
            "allowed_document_ids": allowed,
            "sha256": {path: fingerprint(path) for path in (
                "data/specs/golden/respiratory.md", "src/rag/respiratory/disease_tool.py",
                "src/rag/school/search.py", "notebooks/respiratory/golden_fulltext_width_check.py",
            )},
            "cases": cases,
        }, ensure_ascii=False, indent=2))
    finally:
        tool.search_school_chunks = original
        driver.close()


if __name__ == "__main__":
    main()
