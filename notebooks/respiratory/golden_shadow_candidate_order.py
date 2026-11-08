"""기존 검색과 후보 순서 변경안을 같은 질문으로 비교하는 읽기 전용 실험.

저장소 루트: uv run python notebooks/respiratory/golden_shadow_candidate_order.py
변경안은 이 프로세스의 쿼리 문자열에만 적용하며 코드·DB·파일을 쓰지 않는다.
"""

from __future__ import annotations

import json

from golden_readonly_check import CASES, run_case

from index.school.dictionaries import load_dictionaries
from rag.respiratory import disease_tool as tool
from rag.school.capabilities import active_pages
from rag.school.graph import default_deps


class ReadProxy:
    """실험용 쿼리에만 우선 후보 ID를 전달한다."""

    def __init__(self, driver, state):
        self.driver = driver
        self.state = state

    def execute_query(self, query, **kwargs):
        if "$priority_ids" in query:
            kwargs["priority_ids"] = self.state["ids"]
        return self.driver.execute_query(query, **kwargs)


def main():
    deps = default_deps()
    deps["books"] = load_dictionaries()
    actual_driver = deps["driver"]
    database = deps.get("database")
    original_query = tool.DIRECT_QUERY
    original_search = tool.search_school_chunks
    state = {"ids": []}
    proxy = ReadProxy(actual_driver, state)
    deps["driver"] = proxy

    def capture_search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        state["ids"] = [hit["chunk_id"] for hit in result.get("hits", []) if "chunk_id" in hit]
        return result

    # RRF 후보에 들어온 청크를 먼저 살피되 같은 그룹 안의 기존 ID 순서는 유지한다.
    expected_order = "ORDER BY c.chunk_id, d.document_id SKIP $offset LIMIT $page_size"
    if expected_order not in original_query:
        raise RuntimeError("직접 후보 쿼리 형식이 변경돼 실험할 수 없음")
    shadow_query = original_query.replace(
        expected_order,
        "ORDER BY CASE WHEN c.chunk_id IN $priority_ids THEN 0 ELSE 1 END, "
        "c.chunk_id, d.document_id SKIP $offset LIMIT $page_size",
    )
    tool.search_school_chunks = capture_search
    result = []
    try:
        allowed = tool._all_document_ids(proxy, database)
        scope = active_pages()
        for case_id, question, answers in CASES:
            tool.DIRECT_QUERY = original_query
            before = run_case(case_id, question, answers, allowed, deps, scope)
            tool.DIRECT_QUERY = shadow_query
            after = run_case(case_id, question, answers, allowed, deps, scope)
            result.append({
                "id": case_id,
                "before_status": before["status"],
                "after_status": after["status"],
                "before": before["expected"],
                "after": after["expected"],
                "before_bad_findings": before["bad_findings"],
                "after_bad_findings": after["bad_findings"],
                "before_bad_paths": before["bad_paths"],
                "after_bad_paths": after["bad_paths"],
            })
        print(json.dumps({"mode": "read_only_shadow", "cases": result}, ensure_ascii=False, indent=2))
    finally:
        tool.DIRECT_QUERY = original_query
        tool.search_school_chunks = original_search
        actual_driver.close()


if __name__ == "__main__":
    main()
