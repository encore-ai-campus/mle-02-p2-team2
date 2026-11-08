import pytest

from preprocess.respiratory.md_to_blocks import has_content


@pytest.mark.parametrize("marker", ["원본 PDF의 빈 페이지.", "[원본 빈 페이지]", "빈 페이지."])
def test_blank_page_markers_do_not_create_chunks(marker):
    assert not has_content(marker)
    assert not has_content(f"{marker}\n\n12 ｜")


def test_blank_page_words_inside_actual_content_are_kept():
    assert has_content("빈 페이지를 확인한 후 다음 지침을 읽는다.")
    assert has_content("[원본 빈 페이지]\n성홍열 대응 지침")
