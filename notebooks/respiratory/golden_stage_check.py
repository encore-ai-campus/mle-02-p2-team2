"""낮은 순위의 골든 청크를 검색·결합·거르기 단계별로 읽기 전용 추적한다.

저장소 루트: uv run python notebooks/respiratory/golden_stage_check.py
고정 질문을 현재 DB로 조회하고 JSON을 표준 출력한다. DB·파일 쓰기는 하지 않는다.
"""

from __future__ import annotations

import json

from index.school.dictionaries import load_dictionaries
from rag.respiratory import disease_tool as tool
from rag.school import search
from rag.school.graph import default_deps

CASES = [
    ("R-F2", "성홍열 잠복기는?", ["kdca_respiratory_2026:p0109:b01"]),
    ("R-F4", "코로나19 확진 학생의 격리 권고 기간은?", ["kdca_covid19_2026:p0028:b01"]),
    ("R-Q2", "인플루엔자 발생 시 학교(교직원)가 참고할 시설별 예방 조치는?", [
        "kdca_flu_facility_2026:p0017:b01", "kdca_flu_facility_2026:p0018:b01",
    ]),
    ("R-Q3", "성홍열 진단 학생의 등교중지 기준은?", [
        "kdca_respiratory_2026:p0118:b01", "kdca_respiratory_2026:p0116:b01",
    ]),
    ("R-T1", "인플루엔자가 유행하면 학교는 휴교해야 하나?", [
        "kdca_flu_facility_2026:p0013:b01", "gyeonggi_school_guide_2025:p0236:b04",
    ]),
]


def position(hits, target):
    return next((i for i, hit in enumerate(hits, 1) if hit["chunk_id"] == target), None)


def target_details(driver, database, allowed, target, ids, names, focus, exclusions):
    records, _, _ = driver.execute_query(
        """MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk {chunk_id:$target})
        WHERE d.document_id IN $allowed
        RETURN c.text AS text""",
        database_=database,
        routing_="r",
        target=target,
        allowed=allowed,
    )
    if not records:
        return {"exists_in_allowed_documents": False}
    text = records[0]["text"] or ""
    masked = tool._mask_header_lines(text)
    text_matches = sorted({
        disease_id for disease_id in ids
        if tool._text_disease_ids(
            tool._mask_excluded_lines(masked, exclusions.get(disease_id, [])),
            [disease_id],
            names,
        )
    })
    about = tool._about_diseases(driver, [target], allowed, ids, database).get(target, set())
    return {
        "exists_in_allowed_documents": True,
        "focus_matches": [term for term in focus if tool._focus_position(masked, [term]) is not None],
        "text_disease_ids": text_matches,
        "about_disease_ids": sorted(about),
    }


def main():
    deps = default_deps()
    deps["books"] = load_dictionaries()
    driver, database = deps["driver"], deps.get("database")
    original_search = tool.search_school_chunks
    result = []
    try:
        allowed = tool._all_document_ids(driver, database)
        books = tool.merge_books(deps["books"])
        exclusions = books.get("exclude_phrases", {})
        for case_id, question, targets in CASES:
            request = {
                "question": question,
                "task": "질병별 사실과 원문 근거 검색",
                "region": "NATIONAL",
                "allowed_document_ids": allowed,
            }
            ids = tool._context_diseases(request, books)
            names = tool._disease_names(books, ids)
            focus = tool.focus_terms(question, books)
            terms = search.build_search_terms(question, books)
            fulltext = search.search_chunks_fulltext(driver, terms, allowed, 64, database)
            vector = search.search_chunks_vector(driver, question, deps["embedder"], allowed, 64, database)
            all_rrf = search.fuse_rrf([fulltext, vector], k=128)
            rrf = all_rrf[:32]
            direct = tool._direct_candidates(driver, database, allowed, ids, names, focus, exclusions)
            merged = tool._merge_direct_candidates(rrf, direct, []) if direct else rrf
            # 후보 목록은 고정하고 최종 개수 제한만 해제해 필터 통과 순위를 확인한다.
            tool.search_school_chunks = lambda *args, _hits=rrf, **kwargs: {"hits": _hits}
            all_findings = tool._search({**request, "top_k": 100}, deps)
            final = tool._search(request, deps)
            if all_findings["status"] == "ERROR" or final["status"] == "ERROR":
                raise RuntimeError(f"{case_id}: 검색 오류")
            case = {
                "id": case_id,
                "question": question,
                "focus_terms": focus,
                "direct_candidate_count": len(direct),
                "direct_candidate_ids": [hit["chunk_id"] for hit in direct],
                "targets": [],
            }
            for target in targets:
                case["targets"].append({
                    "chunk_id": target,
                    "fulltext_64": position(fulltext, target),
                    "vector_64": position(vector, target),
                    "rrf_all": position(all_rrf, target),
                    "rrf_32": position(rrf, target),
                    "direct_20": position(direct, target),
                    "merged": position(merged, target),
                    "filtered_no_top_limit": position(all_findings["citations"], target),
                    "final_top_8": position(final["citations"], target),
                    "target_details": target_details(
                        driver, database, allowed, target, ids, names, focus, exclusions
                    ),
                })
            result.append(case)
        print(json.dumps({"allowed_document_ids": allowed, "cases": result}, ensure_ascii=False, indent=2))
    finally:
        tool.search_school_chunks = original_search
        driver.close()


if __name__ == "__main__":
    main()
