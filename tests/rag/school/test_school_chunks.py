"""Structural chunking regressions; artificial documents require no DB or LLM."""
import json
from pathlib import Path

import pytest

from index.school.dictionaries import load_dictionaries
from preprocess.school.chunks import (
    _attach_table_context,
    body_chunks,
    build_all_chunks,
    clean_body_lines,
    squash,
)
from preprocess.school.structure import document_structure


def body(text, pages=(2,)):
    return body_chunks('moe_school_manual_v3', text, list(pages), 0, 'fixture')[0]


def test_nonselected_pages_supply_full_path_without_emitting_content():
    chunks = body('## PDF 1페이지\n제3장 학교 대응\nⅡ 학교의 대비 및 대응\n'
                  '4 학생 감염병\n5. 성홍열\n가. 개요\n'
                  '## PDF 2페이지\n라. 역학조사 시 협조사항\n학교에서 접촉자를 파악한다')
    assert all(c['pdf_page'] == 2 for c in chunks)
    assert chunks[0]['section'] == (
        '제3장 학교 대응 > Ⅱ 학교의 대비 및 대응 > 4 학생 감염병 > 5. 성홍열 > 라. 역학조사 시 협조사항')


def test_headings_and_table_references_survive_duplicate_filter():
    text = '5. 성홍열(Scarlet fever)\n[표 3-9] 역학조사 협조\n중복되는 표의 내용입니다'
    cleaned = clean_body_lines(text, set(), squash(text))
    assert cleaned == ['5. 성홍열(Scarlet fever)', '[표 3-9] 역학조사 협조']


def test_each_table_uses_its_own_caption_position():
    structure = document_structure({2: '제3장 대응\n1. 질병\n라. 역학조사\n'
                                    '<표 3-5> 원문 체크리스트\n바. 복구 단계'})
    rows = [{'pdf_page': 2, 'section': '표 3-5 작업자가 변경한 제목 — 원본 이미지 대조',
                 'text': '작업 제목\n|구분|내용|\n|조사|확인|'}]
    _attach_table_context(rows, structure, [], 'fixture')
    assert rows[0]['section'].endswith('라. 역학조사 > <표 3-5> 원문 체크리스트')
    assert '복구' not in rows[0]['section']
    assert rows[0]['text'].startswith('<표 3-5> 원문 체크리스트\n')
    assert '작업자가' in rows[0]['working_table_title']


def test_reviewed_reordered_caption_and_subsection():
    structure = document_structure({2: '6. 수두\n다. 환자관리\n<표 3-10> 체크리스트\n라. 역학조사'})
    rows = [{'pdf_page': 2, 'section': '표 3-10 수두 표', 'text': '표\n|구분|내용|\n|조사|확인|'}]
    correction = {'document_id': 'fixture', 'pdf_page': 2, 'table_number': '3-10', 'subsection': '라. 역학조사'}
    _attach_table_context(rows, structure, [correction], 'fixture')
    assert rows[0]['section'] == '6. 수두 > 라. 역학조사 > <표 3-10> 체크리스트'
    assert rows[0]['context_status'] == 'reviewed_layout_correction'
    correction['subsection'] = '없는 제목'
    with pytest.raises(ValueError, match='원문에서 찾지 못함'):
        _attach_table_context(rows, structure, [correction], 'fixture')


def test_short_sibling_sections_are_never_merged_or_discarded():
    chunks = body('## PDF 2페이지\n가. 감염 위험\n짧지만 중요한 내용\n나. 밀접 돌봄\n돌봄 관련 내용')
    assert len(chunks) == 2
    assert chunks[0]['section'] == '가. 감염 위험'
    assert '밀접 돌봄' not in chunks[0]['text']
    assert chunks[1]['section'] == '나. 밀접 돌봄'


def test_repeated_chapter_header_does_not_reset_inherited_item():
    cs = body('## PDF 1페이지\n제3장 학교 대응\nⅡ 대비\n2. 대응\n나. 학교 안\n'
              '3) 일시적 관찰 필요성 판단 및 실시\n'
              '## PDF 2페이지\n학교 대응 제 3 장\n[표 3-6] 일시적 관찰실', (2,))
    assert '나. 학교 안 > 3) 일시적 관찰 필요성 판단 및 실시' in cs[0]['section']


def test_ecp_nested_letters_return_to_outer_subsection():
    text = ('## PDF 2페이지\n별지 18 교육연속성계획\n나. 필요자원\n4) 비상연락망\n'
            '나. 교외 비상연락망\n연락망 관리\n다. 업무연속성계획 수립을 통한 학사운영\n'
            '1) 학교 핵심인력 지속 운영 방안(예시)\n가. 교원\n수업지원\n'
            '마. 행정직원\n기기대여\n바. 정보담당교사\n원격근무환경 조성\n'
            '라. 회복 등 조치\n회복 계획')
    cs = body(text)
    info = next(c for c in cs if '바. 정보담당교사' in c['text'])
    assert '다. 업무연속성계획' in info['section']
    assert '라. 회복' not in info['text']
    recovery = next(c for c in cs if c['text'].startswith('라. 회복'))
    assert '1) 학교 핵심인력' not in recovery['section']
    assert '다. 업무연속성계획' not in recovery['section']


def test_subsection_table_reference_keeps_its_introduction():
    cs = body('## PDF 2페이지\n3) 환자 관리\n담당자별 역할은 다음과 같음\n'
              '[표 3-12] 환자 관리 담당자별 역할\n4) 밀접접촉자 관리\n'
              '담당자별 역할은 다음과 같음\n[표 3-13] 접촉자 관리 담당자별 역할')
    for caption, intro in (('[표 3-12]', '3) 환자 관리'), ('[표 3-13]', '4) 밀접접촉자 관리')):
        owner = next(c for c in cs if caption in c['text'])
        assert intro in owner['text']
        assert owner['text'].index(intro) < owner['text'].index(caption)


def test_short_items_merge_within_parent_but_long_items_split():
    """v4: `1)` 항목 하나가 청크 하나가 되면 맥락 없는 조각이 검색 상위를 차지했다."""
    short = body('## PDF 2페이지\n가. 담임교사\n1) 보호자에게 연락\n2) 보건실로 이동\n'
                 '3) 교무부장에게 보고\n나. 보건교사\n1) 증상 확인')
    assert [c['text'].splitlines()[0] for c in short] == ['가. 담임교사', '나. 보건교사']
    assert short[0]['section'] == '가. 담임교사'
    assert '3) 교무부장에게 보고' in short[0]['text'] and '보건교사' not in short[0]['text']
    long = body('## PDF 2페이지\n가. 담임교사\n1) ' + '연락' * 110 + '\n2) 보건실로 이동')
    assert len(long) == 2 and long[1]['text'] == '2) 보건실로 이동'
    assert long[1]['section'] == '가. 담임교사 > 2) 보건실로 이동'


def test_long_lines_and_tiny_fragments_preserve_characters():
    for size in [19, 50, 199, 801, 1700]:
        chunks = body('## PDF 2페이지\n' + '가'*size)
        assert ''.join(c['text'] for c in chunks) == '가'*size
        assert max(len(c['text']) for c in chunks) <= 800


def test_ambiguous_caption_is_not_silently_assigned():
    structure = document_structure({2: '<표 3-5> 첫 표\n<표 3-5> 둘째 표'})
    with pytest.raises(ValueError, match='모호'):
        _attach_table_context([{'pdf_page': 2, 'section': '표 3-5', 'text': '표\n행'}],
                              structure, [], 'fixture')


def test_appendix_resets_chapter_and_unanchored_table_uses_common_parent():
    structure = document_structure({2: '제7장 국가위기\n### 부록\nⅢ 감염병\n'
                                    '가. 첫 절\n내용입니다\n나. 둘째 절\n다른 내용입니다'})
    rows = [{'pdf_page': 2, 'section': '후보 1', 'text': '후보 1\n|구분|내용|\n|조사|확인|'}]
    _attach_table_context(rows, structure, [], 'fixture')
    assert rows[0]['section'] == '부록 > Ⅲ 감염병 > 후보 1'
    assert not rows[0]['context_verified']


def test_migration_does_not_trust_surviving_positional_id():
    from preprocess.school.chunk_audit import citation_audit, migration_audit
    old = {'chunk_id': 'd:p0002:b01', 'document_id': 'd', 'pdf_page': 2,
               'text': '원래 내용', 'row_text': '원래 내용'}
    new = [{**old, 'text': '다른 내용', 'row_text': '다른 내용'},
           {**old, 'chunk_id': 'd:p0002:b02'}]
    audit = migration_audit([old], new)[0]
    assert audit['same_id_exists'] and not audit['same_id_same_text']
    assert audit['candidates'] == ['d:p0002:b02']
    citation = citation_audit([{**old, 'excerpt': '원래 내용'}], new)[0]
    assert not citation['old_id_valid'] and citation['candidates'] == ['d:p0002:b02']


@pytest.fixture(scope='module')
def actual_chunks():
    root = Path(__file__).resolve().parents[3]
    if not (root / 'data/raw/경기도_실무가이드북_본문_표.md').exists():
        pytest.skip('Git 제외 원문이 없어 실제 자료 검사는 별도 실행 필요')
    from index.school.id_map import load_manifest
    return build_all_chunks(root, load_manifest(root), load_dictionaries())[0]


@pytest.mark.parametrize(('page', 'disease'), [(171, '1. 결핵'), (179, '4. 백일해'),
                                              (182, '5. 성홍열'), (185, '6. 수두')])
def test_actual_checklist_paths(actual_chunks, page, disease):
    rows = [c for c in actual_chunks if c['document_id'] == 'gyeonggi_school_guide_2025'
            and c['pdf_page'] == page and c['kind'] == 'table_row']
    assert rows
    assert all(disease in c['section'] and '라. 역학조사 시 협조사항' in c['section']
               and '복구' not in c['section'] and '원본 이미지 대조' not in c['text'] for c in rows)


def test_actual_rows_stable_and_all_paths_bounded(actual_chunks):
    root = Path(__file__).resolve().parents[3]
    baseline = root / 'data/interim/school/full_20261105T032224_612625Z/official_chunks.jsonl'
    if baseline.exists():
        old = [json.loads(s) for s in baseline.read_text(encoding='utf-8').splitlines()]
        old = {c['chunk_id']: c['row_text'] for c in old if c['kind'] == 'table_row'}
        new = {c['chunk_id']: c['row_text'] for c in actual_chunks if c['kind'] == 'table_row'}
        assert old == new
    assert len({c['chunk_id'] for c in actual_chunks}) == len(actual_chunks)
    assert all(len(c['text']) <= 800 for c in actual_chunks if c['kind'] == 'paragraph')


def test_actual_remaining_noted_cases(actual_chunks):
    guide = [c for c in actual_chunks if c['document_id'] == 'gyeonggi_school_guide_2025']
    restart = next(c for c in guide if c['pdf_page'] == 238
                   and '다. 정상수업 재개 시' in c['text'])
    assert restart['section'].startswith('제5장 전파 차단을 위한 별도 조치 > Ⅰ 휴업 및 휴교 > ')
    risk = next(c for c in guide if c['pdf_page'] == 244 and '가. 일상생활' in c['text'])
    assert '나. 밀접 돌봄' not in risk['text']
    staff = next(c for c in guide if c['pdf_page'] == 310 and '마. 행정직원' in c['text'])
    assert '다. 업무연속성계획 수립을 통한 학사운영 > 1) 학교 핵심인력' in staff['section']
    assert '라. 회복 등 조치' not in staff['text']
    observation = next(c for c in actual_chunks if c['chunk_id'] == 'moe_school_manual_v3:p0056:t01r02')
    assert '3) 일시적 관찰 필요성 판단 및 실시' in observation['section']
    references = [c for c in actual_chunks if c['document_id'] == 'moe_school_manual_v3'
                  and c['pdf_page'] == 66 and c['kind'] == 'paragraph']
    for number in [12, 13, 14]:
        assert any(f'[표 3-{number}]' in c['text'] for c in references)


def test_circled_bullet_outside_attachment_keeps_disease_section():
    """11/06 v4: 본문 ②가 rank 30 제목이 되어 `13. 홍역` 절을 밀어냈다(p202~218)."""
    cs = body('## PDF 1페이지\n4 학생 빈발 감염병\n13. 홍역(Measles)\n다. 접촉자 관리\n'
              '## PDF 2페이지\n② 보건소 시스템으로 2차 미등록자 선별\n라. 역학조사 시 협조사항\n추가 환자 연락')
    assert all('13. 홍역(Measles)' in c['section'] for c in cs)
    assert '미등록자' not in cs[-1]['section']
    attached = body('## PDF 2페이지\n붙임 1 해외감염병\n① 뎅기열\n모기 매개\n② 지카\n모기 매개')
    assert [c['section'] for c in attached][-1] == '붙임 1 해외감염병 > ② 지카'
