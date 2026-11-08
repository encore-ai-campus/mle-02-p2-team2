"""괄호 안 질문 단어를 분리하는 대안을 실제 코드 변경 없이 실DB에서 비교한다.

저장소 루트: uv run python notebooks/respiratory/golden_shadow_question_brackets.py
DB·파일을 쓰지 않고 결과 JSON을 표준 출력한다.
"""

from __future__ import annotations

import json

from golden_readonly_check import CASES, run_case

from index.school.dictionaries import load_dictionaries
from rag.respiratory import disease_tool as tool
from rag.school.capabilities import active_pages
from rag.school.graph import default_deps


def main():
    deps = default_deps()
    deps["books"] = load_dictionaries()
    driver, database = deps["driver"], deps.get("database")
    original_focus = tool.focus_terms
    original_search = tool.search_school_chunks

    def old_focus(question, books):
        return tool._focus_terms(question, books, split_brackets=False)

    def separated_focus(question, books):
        return tool._focus_terms(question, books, split_brackets=True)

    def expanded_search(driver, request, books, **kwargs):
        original = set(old_focus(request["question"], books))
        extra = [term for term in separated_focus(request["question"], books) if term not in original]
        if extra:
            kwargs["extra_terms"] = extra
        return original_search(driver, request, books, **kwargs)

    results = []
    try:
        allowed = tool._all_document_ids(driver, database)
        scope = active_pages()
        for case_id, question, answers in CASES:
            tool.focus_terms, tool.search_school_chunks = old_focus, original_search
            before = run_case(case_id, question, answers, allowed, deps, scope)
            tool.focus_terms, tool.search_school_chunks = separated_focus, expanded_search
            after = run_case(case_id, question, answers, allowed, deps, scope)
            merged_books = tool.merge_books(deps["books"])
            results.append({
                "id": case_id,
                "before_focus": old_focus(question, merged_books),
                "after_focus": separated_focus(question, merged_books),
                "before_status": before["status"],
                "after_status": after["status"],
                "before": before["expected"],
                "after": after["expected"],
                "before_bad_findings": before["bad_findings"],
                "after_bad_findings": after["bad_findings"],
                "before_bad_paths": before["bad_paths"],
                "after_bad_paths": after["bad_paths"],
            })
        print(json.dumps({"mode": "read_only_shadow", "cases": results}, ensure_ascii=False, indent=2))
    finally:
        tool.focus_terms, tool.search_school_chunks = original_focus, original_search
        driver.close()


if __name__ == "__main__":
    main()
