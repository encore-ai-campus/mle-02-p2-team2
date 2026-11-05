"""Citation 발췌 대조: 서식 기호만 무시하고 낱말은 그대로 비교한다(11/05 M3 legal_search 사례)."""

from rag.school.validation_local import excerpt_in_text

TEXT = "1. 교내 **홍역(의심)환자** **[*]** **발생 시 반드시 의료기관에서 진료 받도록 안내하고, 관할 보건소에 신고**"


def test_exact_and_format_only_difference_pass():
    assert excerpt_in_text("**발생 시 반드시", TEXT)
    assert excerpt_in_text("교내 홍역(의심)환자 발생 시 반드시 의료기관에서 진료 받도록 안내하고, 관할 보건소에 신고", TEXT)
    assert excerpt_in_text("담당 | 조치<br>보호자 연락", "| 담당 | 조치 보호자 연락 |")


def test_wording_change_still_fails():
    assert not excerpt_in_text("교내 홍역 환자 발생 시 반드시 의료기관 진료를 안내하고", TEXT)   # 의역
    assert not excerpt_in_text("교내 홍역(의심)환자 발생 시 의료기관에서 진료 받도록 안내하고", TEXT)   # '반드시' 삭제
    assert not excerpt_in_text("***", TEXT)                                                        # 서식만 있는 발췌
