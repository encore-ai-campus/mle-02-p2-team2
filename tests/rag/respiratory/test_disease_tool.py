from __future__ import annotations

from index.school.dictionaries import load_dictionaries
from rag.respiratory import disease_tool
from rag.respiratory.disease_tool import ToolInput, disease_search
from rag.school.search import SearchError


def validate_tool_result(result: dict, request: dict) -> None:
    required = {
        "tool_name", "status", "findings", "citations",
        "graph_paths", "warnings"
    }
    if not required.issubset(result):
        raise ValueError(f"Missing ToolResult keys: {required - result.keys()}")

    tool_names = {
        "action_search", "disease_search", "legal_search",
        "crossdoc_graph_search"
    }
    if result["tool_name"] not in tool_names:
        raise ValueError("Unknown tool_name")
    if result["status"] not in {"OK", "PARTIAL", "NO_EVIDENCE", "ERROR"}:
        raise ValueError("Unknown status")

    allowed = set(request["allowed_document_ids"])
    if not set(request.get("preferred_document_ids", [])).issubset(allowed):
        raise ValueError("preferred docs are outside allowed docs")

    chunks = {}
    for citation in result["citations"]:
        if citation["document_id"] not in allowed:
            raise ValueError("Citation outside allowed documents")
        if not citation["chunk_id"] or citation["chunk_id"] in chunks:
            raise ValueError("Missing or duplicate chunk_id")
        p = citation["pdf_page"]
        if type(p) is not int or p < 1:
            raise ValueError("Invalid 1-based pdf_page")
        if not isinstance(citation["excerpt"], str) or not citation["excerpt"].strip():
            raise ValueError("Empty excerpt")
        chunks[citation["chunk_id"]] = citation

    for finding in result["findings"]:
        if not finding["claim"].strip():
            raise ValueError("Empty claim")
        ids = finding["chunk_ids"]
        if not ids or not set(ids).issubset(chunks):
            raise ValueError("Finding has missing Citation")

    if result["status"] in {"OK", "PARTIAL"} and not result["findings"]:
        raise ValueError("Successful/partial result needs verified Finding")
    if result["status"] in {"NO_EVIDENCE", "ERROR"} and (
        result["findings"] or result["citations"]
    ):
        raise ValueError("No unverified claims on missing/effectively failed result")


class FakeRecord(dict):
    def data(self):
        return dict(self)


class FakeDriver:
    def __init__(self, about_rows=None, direct_rows=None, scoped_rows=None):
        self.about_rows = about_rows or []
        self.direct_rows = direct_rows or []
        self.scoped_rows = scoped_rows or []   # 2026-11-08 질병 전용 문서·소제목 후보 조회(SCOPED_QUERY)
        self.last_params = None

    def execute_query(self, query, *, database_, **params):
        if query == disease_tool.DIRECT_QUERY:
            return [FakeRecord(row) for row in self.direct_rows], None, None
        if query == disease_tool.SCOPED_QUERY:
            return [FakeRecord(row) for row in self.scoped_rows], None, None
        self.last_params = params
        rows = [
            row
            for row in self.about_rows
            if row["chunk_id"] in params["ids"]
            and row["disease_id"] in params["disease_ids"]
        ]
        return [FakeRecord(row) for row in rows], None, None


def make_tool_input(question="인플루엔자 잠복기는?", allowed=None) -> ToolInput:
    return {
        "question": question,
        "task": "질병별 사실과 원문 근거 검색",
        "region": "NATIONAL",
        "allowed_document_ids": allowed or ["kdca_flu_facility_2026"],
    }


def make_hit(
    chunk_id="kdca_flu_facility_2026:p0006:b01",
    document_id="kdca_flu_facility_2026",
    text="인플루엔자의 잠복기는 1∼4일 (평균 2일)이다.",
):
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "pdf_page": 6,
        "printed_page": 2,
        "text": text,
    }


def make_deps(about_rows=None):
    return {
        "driver": FakeDriver(about_rows),
        "database": "test",
        "embedder": object(),
        "books": load_dictionaries(),
    }


def stub_search(monkeypatch, hits=None, error=None, calls=None):
    def search_school_chunks(*args, **kwargs):
        if calls is not None:
            calls.update(kwargs)
        if error is not None:
            raise error
        return {"hits": list(hits or [])}

    monkeypatch.setattr(disease_tool, "search_school_chunks", search_school_chunks)


def test_influenza_question_returns_valid_tool_result(monkeypatch):
    hit = make_hit()
    stub_search(
        monkeypatch,
        [hit],
    )
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "INFLUENZA"}])
    request = make_tool_input()

    result = disease_search(request, deps=deps)

    validate_tool_result(result, request)
    assert result["status"] == "OK"
    assert result["tool_name"] == "disease_search"
    assert result["findings"][0]["disease_ids"] == ["INFLUENZA"]


def test_question_without_supported_disease_returns_no_evidence(monkeypatch):
    stub_search(monkeypatch, [make_hit()])
    request = make_tool_input("오늘 날씨를 알려줘")
    result = disease_search(request, deps=make_deps())

    assert result["status"] == "NO_EVIDENCE"
    assert result["warnings"] == ["질문에서 지원 질병을 찾지 못함"]
    assert result["findings"] == []
    assert result["citations"] == []


def test_hit_outside_allowed_documents_is_excluded(monkeypatch):
    allowed_hit = make_hit()
    outside_hit = make_hit(
        chunk_id="other_doc:p0006:b01",
        document_id="other_doc",
        text="인플루엔자의 잠복기는 외부 문서에만 있다.",
    )
    stub_search(monkeypatch, [allowed_hit, outside_hit])
    deps = make_deps(
        [
            {"chunk_id": allowed_hit["chunk_id"], "disease_id": "INFLUENZA"},
            {"chunk_id": outside_hit["chunk_id"], "disease_id": "INFLUENZA"},
        ]
    )
    request = make_tool_input(allowed=[allowed_hit["document_id"]])

    result = disease_search(request, deps=deps)

    assert result["status"] == "OK"
    assert [citation["document_id"] for citation in result["citations"]] == [
        allowed_hit["document_id"]
    ]
    assert deps["driver"].last_params["ids"] == [allowed_hit["chunk_id"]]
    validate_tool_result(result, request)


def test_search_error_returns_error_without_evidence(monkeypatch):
    stub_search(monkeypatch, error=SearchError("private database detail"))
    result = disease_search(make_tool_input(), deps=make_deps())

    assert result["status"] == "ERROR"
    assert result["warnings"] == ["SearchError"]
    assert result["findings"] == []
    assert result["citations"] == []
    assert result["graph_paths"] == []


def test_citations_are_unique_and_cover_every_finding(monkeypatch):
    hit = make_hit()
    stub_search(monkeypatch, [hit, hit.copy()])
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "INFLUENZA"}])
    request = make_tool_input()

    result = disease_search(request, deps=deps)

    chunk_ids = [citation["chunk_id"] for citation in result["citations"]]
    assert len(chunk_ids) == len(set(chunk_ids))
    citation_ids = set(chunk_ids)
    assert all(set(finding["chunk_ids"]).issubset(citation_ids) for finding in result["findings"])
    assert len(result["findings"]) == 1


def test_excerpt_is_a_verbatim_substring_of_chunk_text(monkeypatch):
    hit = make_hit(text="머리말\n인플루엔자의 잠복기는 1∼4일 (평균 2일)이다.\n추가 설명.")
    stub_search(monkeypatch, [hit])
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "INFLUENZA"}])

    result = disease_search(make_tool_input(), deps=deps)

    excerpt = result["citations"][0]["excerpt"]
    assert excerpt.replace("\n", " ") in hit["text"].replace("\n", " ")
    assert result["findings"][0]["claim"] in hit["text"]


def test_claim_uses_only_disease_line_from_multiline_chunk(monkeypatch):
    hit = make_hit(text="도입 설명입니다.\n인플루엔자 잠복기는 1∼4일입니다.\n추가 설명입니다.")
    stub_search(monkeypatch, [hit])
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "INFLUENZA"}])

    result = disease_search(make_tool_input(), deps=deps)

    assert result["status"] == "OK"
    assert result["findings"][0]["claim"] == "인플루엔자 잠복기는 1∼4일입니다."
    assert "\n" not in result["findings"][0]["claim"]


def test_english_alias_sentence_is_not_cut_at_letter_n(monkeypatch):
    books = load_dictionaries()
    influenza = next(
        entry for entry in books["disease"]["entries"] if entry["disease_id"] == "INFLUENZA"
    )
    influenza["aliases"].append("influenza")
    hit = make_hit(text="influenza incubation is usually one to four days.")
    stub_search(monkeypatch, [hit])
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "INFLUENZA"}])
    deps["books"] = books
    request = make_tool_input("influenza incubation?")

    result = disease_search(request, deps=deps)

    assert result["status"] == "OK"
    assert result["findings"][0]["claim"] == hit["text"]


def test_focus_terms_remove_disease_name_and_particle():
    books = load_dictionaries()

    assert disease_tool.focus_terms("인플루엔자의 잠복기는?", books) == ["잠복기"]


def test_focus_terms_remove_question_style_and_normalize_attendance():
    books = disease_tool.merge_books(load_dictionaries())

    assert disease_tool.focus_terms(
        "인플루엔자로 진단받은 학생은 언제까지 등교하면 안 되나?", books
    ) == ["등교"]
    assert disease_tool.focus_terms(
        "인플루엔자가 유행하면 학교는 휴교해야 하나?", books
    ) == ["유행", "휴교"]
    assert disease_tool.focus_terms("성홍열 등교중지 기준은?", books) == ["등교", "기준"]


def test_direct_candidates_merge_with_rrf_and_exclude_false_matches(monkeypatch):
    hybrid = make_hit(text="인플루엔자 잠복기 1∼4일")
    direct = make_hit(chunk_id="kdca_flu_facility_2026:p0007:b01", text="인플루엔자 잠복기 2일")
    header_only = make_hit(
        chunk_id="kdca_flu_facility_2026:p0008:b01",
        text="2026년도 호흡기감염병 관리지침 인플루엔자\n다른 질환의 잠복기 2일",
    )
    animal = make_hit(
        chunk_id="kdca_flu_facility_2026:p0009:b01",
        text="조류인플루엔자 잠복기 2일",
    )
    outside = make_hit(
        chunk_id="outside:p0001:b01", document_id="outside", text="인플루엔자 잠복기 2일"
    )
    stub_search(monkeypatch, [hybrid])
    deps = make_deps()
    deps["driver"] = FakeDriver(direct_rows=[hybrid, hybrid, direct, header_only, animal, outside])

    result = disease_search(make_tool_input(), deps=deps)

    assert result["status"] == "OK"
    assert [c["chunk_id"] for c in result["citations"]] == [hybrid["chunk_id"], direct["chunk_id"]]
    merged = disease_tool._merge_direct_candidates(
        [{**hybrid, "score": 1 / 61}], [hybrid, direct], []
    )
    assert merged[0]["score"] == 2 / 61
    assert merged[1]["score"] == 1 / 61


def test_korean_disease_name_boundary_rejects_parainfluenza(monkeypatch):
    hits = [
        make_hit(
            chunk_id="parainfluenza:p0001:b01",
            text="파라인플루엔자 감염증의 잠복기는 1∼4일이다.",
        ),
        make_hit(
            chunk_id="seasonal-flu:p0001:b01",
            text="계절 인플루엔자의 잠복기는 1∼4일이다.",
        ),
        make_hit(
            chunk_id="parenthesized-flu:p0001:b01",
            text="(인플루엔자)의 잠복기는 1∼4일이다.",
        ),
    ]
    stub_search(monkeypatch, hits)

    result = disease_search(make_tool_input(), deps=make_deps())

    chunk_ids = {citation["chunk_id"] for citation in result["citations"]}
    assert chunk_ids == {"seasonal-flu:p0001:b01", "parenthesized-flu:p0001:b01"}
    assert disease_tool._name_position("파라인플루엔자", ["인플루엔자"]) is None
    assert disease_tool._name_position("계절 인플루엔자", ["인플루엔자"]) is not None


def test_claim_uses_focus_line_and_excerpt_contains_focus_term(monkeypatch):
    hit = make_hit(text="가. 유행 시기\n…\n**1) 잠복기:** 1∼4일 (평균 2일)")
    stub_search(monkeypatch, [hit])
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "INFLUENZA"}])

    result = disease_search(make_tool_input(), deps=deps)

    assert result["findings"][0]["claim"] == "1) 잠복기: 1∼4일 (평균 2일)"
    assert "잠복기" in result["citations"][0]["excerpt"]


def test_table_of_contents_without_focus_term_returns_no_evidence(monkeypatch):
    hit = make_hit(text="1 인플루엔자 개요 2 … 3 질의응답")
    stub_search(monkeypatch, [hit])
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "INFLUENZA"}])

    result = disease_search(make_tool_input(), deps=deps)

    assert result["status"] == "NO_EVIDENCE"
    assert result["findings"] == []
    assert result["citations"] == []
    assert result["warnings"] == ["질병명은 있으나 질문 핵심어(잠복기)가 든 청크 없음"]


def test_meningococcal_question_returns_ok(monkeypatch):
    hit = make_hit(
        chunk_id="kdca_respiratory_2026:p0109:b01",
        document_id="kdca_respiratory_2026",
        text="수막구균 감염증의 잠복기는 2~10일이다.",
    )
    stub_search(monkeypatch, [hit])
    deps = make_deps()
    request = make_tool_input(
        "수막구균 감염증의 잠복기는?",
        allowed=["kdca_respiratory_2026"],
    )

    result = disease_search(request, deps=deps)

    assert result["status"] == "OK"
    assert result["findings"][0]["disease_ids"] == ["MENINGOCOCCAL"]


def test_scarlet_fever_section_heading_matches_without_about(monkeypatch):
    hit = make_hit(
        chunk_id="kdca_respiratory_2026:p0109:b01",
        document_id="kdca_respiratory_2026",
        text="－\n단원 Ⅱ 각 론 성홍열\n\n**나** **역학적 특성**\n\n2) 잠복기\n\n○ 1~7일(평균 2~5일)",
    )
    hit["pdf_page"] = 109
    hit["printed_page"] = 89
    stub_search(monkeypatch, [hit])
    request = make_tool_input("성홍열 잠복기는?", allowed=["kdca_respiratory_2026"])

    result = disease_search(request, deps=make_deps())

    assert result["status"] == "OK"
    assert result["citations"][0]["chunk_id"] == hit["chunk_id"]
    assert result["findings"][0]["disease_ids"] == ["SCARLET_FEVER"]
    assert "잠복기" in result["findings"][0]["claim"]
    assert len(result["graph_paths"][0]["edges"]) == 1


def test_merge_books_does_not_mutate_original_books():
    books = load_dictionaries()
    original_count = len(books["disease"]["entries"])

    extended = disease_tool.merge_books(books)

    assert len(books["disease"]["entries"]) == original_count
    assert len(extended["disease"]["entries"]) == original_count + 1
    assert extended["disease"]["entries"] is not books["disease"]["entries"]


def test_merge_books_does_not_add_existing_disease_id():
    books = load_dictionaries()
    books["disease"]["entries"].append(
        {
            "disease_id": "MENINGOCOCCAL",
            "name": "기존 수막구균 표기",
            "aliases": [],
        }
    )
    original_count = len(books["disease"]["entries"])

    extended = disease_tool.merge_books(books)

    assert len(books["disease"]["entries"]) == original_count
    assert len(extended["disease"]["entries"]) == original_count
    assert sum(
        entry["disease_id"] == "MENINGOCOCCAL"
        for entry in extended["disease"]["entries"]
    ) == 1


def test_meningococcal_question_focus_terms_exclude_name_fragments():
    books = disease_tool.merge_books(load_dictionaries())

    assert disease_tool.focus_terms("수막구균 감염증의 잠복기는?", books) == ["잠복기"]


def test_meningococcal_claim_uses_focus_line_not_heading(monkeypatch):
    hit = make_hit(
        chunk_id="kdca_respiratory_2026:p0109:b01",
        document_id="kdca_respiratory_2026",
        text="단원 Ⅱ 각 론 수막구균 감염증\n…\n잠복기 2~10일(평균 3~4일)",
    )
    stub_search(monkeypatch, [hit])
    deps = make_deps()
    request = make_tool_input(
        "수막구균 감염증의 잠복기는?",
        allowed=["kdca_respiratory_2026"],
    )

    result = disease_search(request, deps=deps)

    assert result["status"] == "OK"
    claim = result["findings"][0]["claim"]
    assert "잠복기" in claim
    assert "단원" not in claim


def test_header_disease_name_does_not_match_body_disease(monkeypatch):
    hit = make_hit(
        chunk_id="kdca_respiratory_2026:p0001:b01",
        document_id="kdca_respiratory_2026",
        text=(
            "- ● 2026년도 호흡기감염병 관리지침"
            "(수막구균 감염증, 성홍열, 급성호흡기감염증)\n"
            "본문은 아데노바이러스의 잠복기를 설명한다."
        ),
    )
    stub_search(monkeypatch, [hit])
    request = make_tool_input(
        "수막구균 감염증의 잠복기는?",
        allowed=["kdca_respiratory_2026"],
    )

    result = disease_search(request, deps=make_deps())

    assert result["status"] == "NO_EVIDENCE"
    assert all("MENINGOCOCCAL" not in finding["disease_ids"] for finding in result["findings"])


def test_header_and_short_focus_line_claim_includes_next_line(monkeypatch):
    hit = make_hit(
        chunk_id="kdca_respiratory_2026:p0109:b01",
        document_id="kdca_respiratory_2026",
        text=(
            "- ● 2026년도 호흡기감염병 관리지침(수막구균 감염증)\n"
            "1) 잠복기\n"
            "○ 2일~10일(평균 3~4일)"
        ),
    )
    stub_search(monkeypatch, [hit])
    deps = make_deps([{"chunk_id": hit["chunk_id"], "disease_id": "MENINGOCOCCAL"}])
    request = make_tool_input(
        "수막구균 감염증의 잠복기는?",
        allowed=["kdca_respiratory_2026"],
    )

    result = disease_search(request, deps=deps)

    assert result["status"] == "OK"
    assert "2일~10일" in result["findings"][0]["claim"]
    assert "관리지침" not in result["findings"][0]["claim"]


def test_searches_more_hits_then_limits_findings_to_top_k(monkeypatch):
    hits = [
        make_hit(
            chunk_id=f"flu-valid-{index}",
            text=f"인플루엔자의 잠복기는 {index + 1}일이다.",
        )
        for index in range(10)
    ]
    hits.extend(
        make_hit(
            chunk_id=f"other-disease-{index}",
            text="코로나19 감염에 관한 원문이다.",
        )
        for index in range(20)
    )
    calls = {}
    stub_search(monkeypatch, hits, calls=calls)

    result = disease_search(make_tool_input(), deps=make_deps())

    assert calls["k"] == max(8 * 4, 30)
    assert result["status"] == "OK"
    assert len(result["findings"]) == 8
    assert len(result["citations"]) == 8


def test_table_chunk_claim_uses_disease_name_row_and_replaces_br(monkeypatch):
    text = "표 제목\n| 감염병 | 잠복기 |\n| 수막구균성<br>수막염 | 2~10일 |"
    hit = make_hit(chunk_id="x:p0223:t01r09", text=text)
    stub_search(monkeypatch, [hit])
    request = make_tool_input("수막구균 감염증의 잠복기는?")
    result = disease_search(request, deps=make_deps())

    assert result["status"] == "OK"
    assert result["findings"][0]["claim"] == "| 수막구균성 수막염 | 2~10일 |"


def test_paragraph_chunk_keeps_focus_term_line_as_claim(monkeypatch):
    text = "표 제목\n| 감염병 | 잠복기 |\n| 수막구균성<br>수막염 | 2~10일 |"
    hit = make_hit(chunk_id="x:p0223:b01", text=text)
    stub_search(monkeypatch, [hit])
    request = make_tool_input("수막구균 감염증의 잠복기는?")
    result = disease_search(request, deps=make_deps())

    assert result["status"] == "OK"
    assert result["findings"][0]["claim"] == "| 감염병 | 잠복기 |"


def test_excluded_influenza_phrases_do_not_match_influenza(monkeypatch):
    hit = make_hit(
        chunk_id="x:p0223:t01r09",
        text="| 동물 인플루엔자 인체감염증 (조류인플루엔자) | 3-10일 |",
    )
    stub_search(monkeypatch, [hit])

    result = disease_search(make_tool_input("인플루엔자"), deps=make_deps())

    assert result["status"] == "NO_EVIDENCE"
    assert result["findings"] == []
    assert result["citations"] == []


def test_seasonal_influenza_phrase_still_matches(monkeypatch):
    hit = make_hit(text="계절 인플루엔자 잠복기 1∼4일")
    stub_search(monkeypatch, [hit])

    result = disease_search(make_tool_input(), deps=make_deps())

    assert result["status"] == "OK"
    assert result["findings"][0]["disease_ids"] == ["INFLUENZA"]


def test_influenza_exclusions_do_not_hide_other_diseases(monkeypatch):
    hit = make_hit(text="동물 인플루엔자와 코로나19 정보")
    stub_search(monkeypatch, [hit])

    result = disease_search(
        make_tool_input("인플루엔자 코로나19"),
        deps=make_deps(),
    )

    assert result["status"] == "PARTIAL"
    assert result["findings"][0]["disease_ids"] == ["COVID19"]


# 일상어와 어미가 달라도 문서에 쓰인 전파 용어로 검색해야 한다.
def test_focus_terms_normalize_everyday_transmission():
    books = disease_tool.merge_books(load_dictionaries())
    assert "전파" in disease_tool.focus_terms("홍역은 어떻게 옮아요?", books)


# 조사와 어미를 제거한 뒤 두 핵심어를 순서대로 보존해야 한다.
def test_focus_terms_normalize_adult_transmission():
    books = disease_tool.merge_books(load_dictionaries())
    assert disease_tool.focus_terms("수족구병은 성인에게도 전파되나요?", books) == ["성인", "전파"]


# 핵심어가 없는 질병 본문은 경고와 PARTIAL로 대체하되 허용 문서만 인용한다.
def test_missing_focus_uses_disease_body_with_warning(monkeypatch):
    hit = make_hit(text="인플루엔자는 바이러스에 의해 발생하는 호흡기 감염병입니다.")
    excluded = make_hit(
        chunk_id="x:p0001:b01", text="동물 인플루엔자 인체감염증에 대한 설명입니다."
    )
    stub_search(monkeypatch, [excluded, hit])
    request = make_tool_input()
    # ABOUT 관계가 잘못 연결된 제외 문구도 대체 결과로 섞이지 않아야 한다.
    deps = make_deps([{"chunk_id": excluded["chunk_id"], "disease_id": "INFLUENZA"}])
    result = disease_search(request, deps=deps)

    validate_tool_result(result, request)
    assert result["status"] == "PARTIAL"
    assert [citation["chunk_id"] for citation in result["citations"]] == [hit["chunk_id"]]
    assert "핵심어(잠복기) 든 청크 없음→질병 관련 청크로 대체" in result["warnings"]


# ---------------------------------------------------------------- 2026-11-08 지침 전용 근거 (주승우 팀장 승인 하에 Claude 추가)

def test_infectious_period_is_not_rewritten_to_transmission():
    # '전파': ['전염'] 앞부분 일치 때문에 '전염기간'이 '전파'로 바뀌어 전파경로 줄을 근거로 냈다(마이코플라스마 전염기간).
    books = disease_tool.merge_books(load_dictionaries())
    assert disease_tool.focus_terms("마이코플라스마 폐렴 전염기간은?", books) == ["전염기간"]
    assert disease_tool.focus_terms("마이코플라스마 폐렴의 전염 기간은 어떻게 되나요?", books) == ["전염기간"]
    assert "전파" in disease_tool.focus_terms("홍역은 어떻게 옮아요?", books)


def test_dedicated_guideline_chunk_without_disease_name_is_used(monkeypatch):
    # 수족구 지침 '복귀 기준' 청크는 본문에 병명이 없고 ABOUT 관계도 없다. 전용 문서이고 소제목에 핵심어가 있으면 근거로 쓴다.
    row = {"document_id": "kdca_hfmd_2026", "chunk_id": "kdca_hfmd_2026:p0030:b01", "pdf_page": 30,
           "printed_page": None, "text": "발열이 없고, 수포가 마른 후 복귀", "section": "다. 복귀 기준(등교, 등원 중지자) (30쪽)"}
    stub_search(monkeypatch, [])
    deps = {**make_deps(), "driver": FakeDriver(scoped_rows=[row])}
    result = disease_search(make_tool_input("수족구병 진단받은 학생, 언제부터 등교시키면 되나요?",
                                            allowed=["kdca_hfmd_2026"]), deps=deps)
    assert result["status"] == "OK"
    assert [c["chunk_id"] for c in result["citations"]] == ["kdca_hfmd_2026:p0030:b01"]
    assert all(e["relationship_type"] != "ABOUT" for p in result["graph_paths"] for e in p["edges"])  # 없는 관계를 만들지 않는다


def test_dedicated_document_does_not_override_exclusion_phrase():
    names = {"INFLUENZA": ["인플루엔자"]}
    exclusions = {"INFLUENZA": ["파라인플루엔자"]}
    hit = {"document_id": "kdca_flu_facility_2026", "text": "파라인플루엔자 감염증", "section": ""}
    assert disease_tool._scope_disease_ids(hit, ["INFLUENZA"], names, exclusions) == set()
    hit = {"document_id": "kdca_flu_facility_2026", "text": "해열제 없이 24시간", "section": ""}
    assert disease_tool._scope_disease_ids(hit, ["INFLUENZA"], names, exclusions) == {"INFLUENZA"}
