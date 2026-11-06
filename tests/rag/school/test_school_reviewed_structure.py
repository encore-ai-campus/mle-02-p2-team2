"""User-reviewed errors: structure grammar, provenance guards, actual review coverage."""
import copy
import json
from pathlib import Path

import pytest

from preprocess.school.reviewed_context import (
    apply_reviewed_actions,
    apply_reviewed_tables,
    load_review,
    rows_hash,
)
from preprocess.school.structure import document_structure, path_text
from tests.rag.school.test_school_chunks import actual_chunks  # noqa: F401


def test_hyphenated_scenario_resets_previous_disease_and_stage():
    structure = document_structure({
        1: '제2장 평상시 대비\nⅡ 모의훈련\n1. 학생 감염병 대응 모의훈련\n'
           '2-1. 시나리오 (인플루엔자)\n다. 대응 3단계\n설명',
        2: '2-2. 시나리오 (A형간염)\n1) 학교에 보고\n설명',
    })
    section = path_text(structure[2][-1]['path'])
    assert section == '제2장 평상시 대비 > Ⅱ 모의훈련 > 2-2. 시나리오 (A형간염) > 1) 학교에 보고'
    assert '인플루엔자' not in section and '대응 3단계' not in section


def test_attachment_circled_heading_and_continuation_page():
    structure = document_structure({
        1: '제5장 전파 차단\nⅦ 해외감염병 예방수칙\n3 해외여행 시 주의\n'
           '다. 모기매개 감염병\n붙임 3 모기매개감염병 국외 발생 현황\n① 뎅기열\n내용',
        2: '② 치쿤구니야열\n내용',
        3: '붙임 4 해외 여행 시 주의해야 할 감염병\n① 세균성이질\n내용',
    })
    assert path_text(structure[2][-1]['path']) == (
        '제5장 전파 차단 > Ⅶ 해외감염병 예방수칙 > 붙임 3 모기매개감염병 국외 발생 현황 > ② 치쿤구니야열')
    assert '붙임 3' not in path_text(structure[3][-1]['path'])
    assert '3 해외여행' not in path_text(structure[2][-1]['path'])


def test_referenced_footer_does_not_change_path_or_disappear():
    text = ('제3장 대응\n1. 감염병별 특성\n잠복기 [3)] / 격리 [4)]\n'
            + '표 내용\n' * 8 + '1) 원문 작성 근거\n2) 기간은 휴일 포함\n'
            '3) 감염 시작 시점부터 증상 발생 시점\n4) 의료기관 진료 전까지 격리하여 관찰하는 것')
    entries = document_structure({1: text})[1]
    notes = [e for e in entries if e.get('footnote')]
    assert len(notes) == 4 and all(not e['heading'] for e in notes)
    assert all(path_text(e['path']) == '제3장 대응 > 1. 감염병별 특성' for e in notes)
    assert notes[-1]['line'].startswith('4) 의료기관')


def test_real_numbered_instructions_remain_headings_without_reference_markers():
    entries = document_structure({1: '제3장 대응\n1. 관리\n배경 설명\n'
                                  '1) 의료기관에 연락\n2) 격리 실시\n3) 소독'})[1]
    assert entries[-1]['heading'] and not entries[-1].get('footnote')
    assert path_text(entries[-1]['path']).endswith('3) 소독')


def test_footer_detection_stops_before_a_new_section():
    entries = document_structure({1: '1. 첫 절\n참조 [1)]\n' + '표 데이터\n' * 8
                                  + '1) 각주 첫 설명\n2) 각주 둘 설명\n2. 새 절\n내용'})[1]
    assert entries[-1]['path'][-1][1] == '2. 새 절'


def test_reviewed_context_overrides_only_matching_unchanged_table():
    row = {'chunk_id': 'doc:p0001:t01r01', 'row_text': '|학교|연락|', 'text': '후보 1\n|역할|내용|\n|학교|연락|'}
    original = copy.deepcopy(row)
    record = {'table_id': 'doc:p0001:t01', 'review_id': 'T01', 'rows_sha256': rows_hash([row]),
                  'resolved_section': '붙임 1 > ① 질병', 'resolved_title': None,
                  'title_verified': False, 'caption_pending': True}
    apply_reviewed_tables([row], {'tables': [record]})
    assert row['section'] == '붙임 1 > ① 질병'
    assert row['text'] == '|역할|내용|\n|학교|연락|'
    assert row['row_text'] == original['row_text'] and row['caption_pending']
    row['row_text'] = '|학교|다른 업무|'
    with pytest.raises(ValueError, match='표 행 원문 변경'):
        apply_reviewed_tables([row], {'tables': [record]})


def test_reviewed_action_decision_preserves_input_and_checks_prior_state():
    actions = [{'action_id': 'a', 'chunk_id': 'c', 'stage': 'RESPONSE_3'}]
    review = {'actions': [{'action_ids': ['a'], 'chunk_id': 'c', 'old_stage': 'RESPONSE_3', 'stage': 'RESPONSE_2'}]}
    corrected = apply_reviewed_actions(actions, review)
    assert actions[0]['stage'] == 'RESPONSE_3' and corrected[0]['stage'] == 'RESPONSE_2'
    with pytest.raises(ValueError, match='소속/단계 변경'):
        apply_reviewed_actions(corrected, review)


def test_all_64_reviewed_tables_are_applied_and_user_files_preserved(actual_chunks):  # noqa: F811
    root = Path(__file__).resolve().parents[3]
    review = load_review(root)
    for record in review['tables']:
        rows = [c for c in actual_chunks if c['kind'] == 'table_row'
                and c['chunk_id'].rsplit('r', 1)[0] == record['table_id']]
        assert rows and rows_hash(rows) == record['rows_sha256']
        assert all(c['section'] == record['resolved_section'] and c['context_review_id'] == record['review_id']
                   and '?' not in c['section'] and c['context_status'] == 'source_reviewed' for c in rows)
    assert len(review['tables']) == 64


def test_actual_reviewed_paths_titles_and_footnotes(actual_chunks):  # noqa: F811
    review_rows = {c.get('context_review_id'): c for c in actual_chunks if c.get('context_review_id')}
    assert '2-1.' in review_rows['T01']['section']
    assert '2-2.' in review_rows['T04']['section'] and '대응 3단계' not in review_rows['T04']['section']
    assert '6) 관리 방안' in review_rows['T07']['section']
    assert '4) 전파 우려' not in review_rows['T14']['section']
    assert '4) 전파 우려' not in review_rows['T19']['section']
    assert '<표 5-1> 보건학적 고위험군의 범위' in review_rows['T37']['text']
    assert '후보 1' not in review_rows['T37']['section']
    assert '사. 장애 학생의 감염병 취약 특성' in review_rows['T39']['section']
    assert '3 특수학교(학급) 감염병 모의훈련 시 유의 사항' in review_rows['T40']['section']
    assert '붙임 3' in review_rows['T45']['section'] and '① 뎅기열' in review_rows['T45']['section']
    assert '붙임 4' in review_rows['T53']['section'] and '⑤ 치군구니야열' in review_rows['T53']['section']


def test_reviewed_actions_maintain_54_unspecified_and_correct_one():
    root = Path(__file__).resolve().parents[3]
    source = root / 'data/interim/school/normalized_20261105T032224_612625Z/normalized.jsonl'
    if not source.exists():
        pytest.skip('로컬 승인 행동이 없어서 실제 자료 검사는 별도 실행 필요')
    actions = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
    review = load_review(root)
    corrected = {a['action_id']: a for a in apply_reviewed_actions(actions, review)}
    ids = [a for r in review['actions'] if r['verdict'] == '기존 유지' for a in r['action_ids']]
    assert len(ids) == 54 and all(corrected[a]['stage'] == 'UNSPECIFIED' for a in ids)
    assert corrected['moe_school_manual_v3:p0060:a05']['stage'] == 'RESPONSE_2'
