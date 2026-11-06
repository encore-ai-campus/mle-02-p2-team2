"""Destructive migration guardrails use artificial snapshots, never a live DB."""
import copy

import pytest

from index.school.migration import (
    SCHOOL_DOCS,
    assert_matches,
    prepare_bundle,
    semantic_signature,
    snapshot_signature,
)


def world():
    document = 'moe_school_manual_v3'
    cid = document + ':p0001:t01r01'
    docs = [{'document_id': d} for d in sorted(SCHOOL_DOCS)]
    chunk = {'chunk_id': cid, 'text': '원문 제목\n|질병|내용|\n|수두|격리|',
             'section': '새 주소', 'pdf_page': 1, 'printed_page': 1}
    candidate = {'nodes': {'Document': docs, 'Chunk': [chunk], 'SchoolAction': []},
                 'edges': [{'start_label': 'Document', 'start_id': document, 'type': 'HAS_CHUNK',
                            'end_label': 'Chunk', 'end_id': cid, 'props': {}}]}
    nodes = [{'eid': 'd'+str(i), 'labels': ['Document'], 'props': d} for i, d in enumerate(docs)]
    nodes += [{'eid': 'c', 'labels': ['Chunk'], 'props': {**chunk, 'text': chunk['text'].replace('원문 제목','작업 제목'),
                                                       'section': '이전 주소', 'embedding': [1.,2.,3.]}}]
    doc_eid = next(n['eid'] for n in nodes if n['props'].get('document_id') == document)
    edge = {'eid': 'h', 'start_eid': doc_eid, 'end_eid': 'c', 'start_labels': ['Document'],
            'start_props': {'document_id': document}, 'end_labels': ['Chunk'],
            'end_props': {'chunk_id': cid}, 'type': 'HAS_CHUNK', 'props': {}}
    about = {'eid': 'a', 'start_eid': 'c', 'end_eid': 'disease', 'start_labels': ['Chunk'],
             'start_props': {'chunk_id': cid}, 'end_labels': ['Disease'],
             'end_props': {'disease_id': 'VARICELLA'}, 'type': 'ABOUT', 'props': {}}
    return {'nodes': nodes, 'edges': [edge, about]}, candidate


def test_plan_preserves_about_for_unchanged_row_and_does_not_mutate_inputs():
    state, candidate = world()
    original = copy.deepcopy(candidate)
    prepared, plan = prepare_bundle(state, candidate)
    assert candidate == original
    assert plan['preserved_about'] == 1
    assert any(e['type'] == 'ABOUT' for e in prepared['edges'])


def test_foreign_incoming_relationship_prevents_destructive_replacement():
    state, candidate = world()
    incoming = copy.deepcopy(state['edges'][1])
    incoming.update(start_eid='foreign', end_eid='c')
    state['edges'].append(incoming)
    with pytest.raises(ValueError, match='incoming'):
        prepare_bundle(state, candidate)


def test_about_mapping_does_not_follow_a_changed_or_retired_row():
    state, candidate = world()
    candidate['nodes']['Chunk'][0]['text'] = '제목\n|홍역|다른 조치|'
    with pytest.raises(ValueError, match='근거 행이 변경'):
        prepare_bundle(state, candidate)
    candidate['nodes']['Chunk'] = []
    with pytest.raises(ValueError, match='폐기될 청크'):
        prepare_bundle(state, candidate)


def test_unknown_relationship_and_out_of_scope_document_are_rejected():
    state, candidate = world()
    state['edges'][0]['type'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='예상하지 못한 관계'):
        prepare_bundle(state, candidate)
    candidate['nodes']['Document'] = [{'document_id': 'other_team'}]
    with pytest.raises(ValueError, match='학교 문서 두 개 외'):
        prepare_bundle(state, candidate)


def test_snapshot_signature_detects_changed_content_and_ignores_endpoint_projection():
    state, _ = world()
    projected = copy.deepcopy(state)
    projected['edges'][0]['end_props']['text'] = 'an endpoint may have full properties in older backups'
    assert snapshot_signature(projected) == snapshot_signature(state)
    projected['nodes'][-1]['props']['text'] = 'concurrent update'
    assert snapshot_signature(projected) != snapshot_signature(state)


def test_after_check_rejects_wrong_metadata_missing_vector_and_duplicate_edges():
    state, candidate = world()
    bundle, _ = prepare_bundle(state, candidate)
    state['nodes'][-1]['props'].update(candidate['nodes']['Chunk'][0])
    assert_matches(state, bundle, 3)
    for field, value in [('section', '잘못된 주소'), ('embedding', [])]:
        changed = copy.deepcopy(state)
        changed['nodes'][-1]['props'][field] = value
        with pytest.raises(ValueError):
            assert_matches(changed, bundle, 3)
    state['edges'].append(copy.deepcopy(state['edges'][0]))
    with pytest.raises(ValueError, match='관계 집합/중복'):
        assert_matches(state, bundle, 3)


def test_restore_comparison_ignores_element_ids_but_detects_property_loss():
    state, _ = world()
    restored = copy.deepcopy(state)
    for node in restored['nodes']:
        node['eid'] += '-new'
    for edge in restored['edges']:
        edge['eid'] += '-new'
        edge['start_eid'] += '-new'
        edge['end_eid'] += '-new'
    assert semantic_signature(restored) == semantic_signature(state)
    restored['nodes'][-1]['props']['embedding'] = []
    assert semantic_signature(restored) != semantic_signature(state)
