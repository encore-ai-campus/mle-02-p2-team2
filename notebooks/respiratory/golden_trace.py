"""읽기 전용 골든 청크 추적. 구현 변경 전에 실행하여 JSON을 표준 출력한다."""

import json

from rag.respiratory import disease_tool as tool
from rag.school import search

CASES = [
    ("R-F2", "성홍열 잠복기는?", "kdca_respiratory_2026:p0109:b01"),
    ("R-F4", "코로나19 확진 학생의 격리 권고 기간은?", "kdca_covid19_2026:p0028:b01"),
    ("R-Q1", "인플루엔자로 진단받은 학생은 언제까지 등교하면 안 되나?", "kdca_flu_facility_2026:p0015:b01"),
    ("R-Q3", "성홍열 진단 학생의 등교중지 기준은?", "kdca_respiratory_2026:p0118:b01"),
    ("R-T1", "인플루엔자가 유행하면 학교는 휴교해야 하나?", "kdca_flu_facility_2026:p0013:b01"),
]


def position(hits, target):
    return next((i for i, hit in enumerate(hits, 1) if hit["chunk_id"] == target), None)


def main():
    deps = tool._default_deps()
    driver, database = deps["driver"], deps["database"]
    original_search = tool.search_school_chunks
    try:
        allowed = tool._all_document_ids(driver, database)
        books = tool.merge_books(deps["books"])
        for question_id, question, target in CASES:
            terms = search.build_search_terms(question, books)
            fulltext = search.search_chunks_fulltext(driver, terms, allowed, 64, database)
            vector = search.search_chunks_vector(driver, question, deps["embedder"], allowed, 64, database)
            fused_all = search.fuse_rrf([fulltext, vector], k=128)
            fused = fused_all[:32]
            input_data = {
                "question": question,
                "task": "질병별 사실과 원문 근거 검색",
                "region": "NATIONAL",
                "allowed_document_ids": allowed,
            }
            tool.search_school_chunks = lambda *args, _fused=fused, **kwargs: {"hits": _fused}
            all_results = tool._search({**input_data, "top_k": 100}, deps)
            final_results = tool._search(input_data, deps)
            records, _, _ = driver.execute_query(
                """MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk {chunk_id: $chunk_id})
                WHERE d.document_id IN $allowed
                RETURN d.document_id AS document_id, c.chunk_id AS chunk_id,
                       c.pdf_page AS pdf_page, c.printed_page AS printed_page, c.text AS text""",
                database_=database,
                chunk_id=target,
                allowed=allowed,
            )
            target_hit = records[0].data() if records else None
            result = {
                "id": question_id,
                "target": target,
                "fulltext_64_rank": position(fulltext, target),
                "vector_64_rank": position(vector, target),
                "rrf_32_rank": position(fused, target),
                "rrf_unlimited_rank": position(fused_all, target),
                "filtered_rank_without_top_k": position(all_results["citations"], target),
                "final_8_rank": position(final_results["citations"], target),
                "focus_terms": tool.focus_terms(question, books),
                "target_exists": bool(target_hit),
            }
            if target_hit:
                ids = tool._context_diseases(input_data, books)
                names = tool._disease_names(books, ids)
                text = target_hit["text"]
                masked = tool._mask_header_lines(text)
                about = tool._about_diseases(driver, [target], allowed, ids, database).get(target, set())
                text_ids = {
                    disease_id for disease_id in ids
                    if tool._text_disease_ids(
                        tool._mask_excluded_lines(masked, books.get("exclude_phrases", {}).get(disease_id, [])),
                        [disease_id], names,
                    )
                }
                result.update({
                    "disease_ids": ids,
                    "about_ids": sorted(about),
                    "unmasked_text_ids": sorted(tool._text_disease_ids(text, ids, names)),
                    "masked_text_ids": sorted(text_ids),
                    "focus_found": tool._focus_position(masked, result["focus_terms"]) is not None,
                    "first_lines": text.splitlines()[:8],
                })
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        tool.search_school_chunks = original_search
        driver.close()


if __name__ == "__main__":
    main()
