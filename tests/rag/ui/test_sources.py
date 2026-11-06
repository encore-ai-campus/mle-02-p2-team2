"""근거 문서 목록 만들기 검사 (유종혁). Streamlit·DB 없이 실행한다."""
from rag.ui import sources

DOCS = {"gyeonggi": {"title": "경기도 가이드북", "version": "2024.05", "issuer": "경기도교육청"},
        "law": {"title": "감염병예방법", "version": "제21521호", "issuer": "법제처"}}
CITES = [
    {"document_id": "gyeonggi", "chunk_id": "a", "pdf_page": 172, "printed_page": 162, "excerpt": "x"},
    {"document_id": "law", "chunk_id": "b", "pdf_page": 19, "printed_page": None, "excerpt": "y"},
    {"document_id": "gyeonggi", "chunk_id": "c", "pdf_page": 171, "printed_page": 161, "excerpt": "z"},
]


def test_groups_by_document_in_first_seen_order_with_sorted_pages():
    groups = sources.group_sources(CITES, DOCS)
    assert [g["document_id"] for g in groups] == ["gyeonggi", "law"]
    assert groups[0]["pages"] == [171, 172] and groups[0]["printed"] == [161, 162]


def test_source_line_without_and_with_url():
    lines = sources.source_lines(CITES, DOCS, {"law": "https://example.org/law"})
    assert lines[0] == "경기도 가이드북 · 판본 2024.05 · PDF 171쪽, 172쪽 (인쇄 161쪽, 162쪽)"
    assert lines[1] == "[감염병예방법](https://example.org/law) · 판본 제21521호 · PDF 19쪽"


def test_unknown_document_falls_back_to_id_and_no_citations_gives_empty():
    assert sources.source_lines([{"document_id": "zz", "pdf_page": 3, "printed_page": None}], {}, {}) == ["zz · PDF 3쪽"]
    assert sources.source_lines([], DOCS, {}) == []


def test_load_urls_keeps_only_http_urls(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text("document_id,문서명,공식URL\nA,a,https://kdca.go.kr/x\nB,b,확인 필요\nC,c,javascript:alert(1)\n",
                    encoding="utf-8-sig")
    assert sources.load_urls(path) == {"A": "https://kdca.go.kr/x"}
    assert sources.load_urls(tmp_path / "none.csv") == {}


def test_real_manifest_loads_without_error():
    urls = sources.load_urls()
    assert all(u.startswith(("http://", "https://")) for u in urls.values())


