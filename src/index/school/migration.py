"""Plan a bounded replacement of the two school documents, preserving ABOUT.

No database calls here. Plans reject foreign references and stale candidate data.
"""
from __future__ import annotations

import copy
import hashlib
import json

from index.school.bundle import ALLOWED_EDGES, ID_FIELD, edge_key, expected_keys

SCHOOL_DOCS = {'moe_school_manual_v3', 'gyeonggi_school_guide_2025'}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def node_key(labels, props) -> tuple[str, str]:
    found = [(label, props[ID_FIELD[label]]) for label in labels
             if label in ID_FIELD and ID_FIELD[label] in props]
    if len(found) != 1:
        raise ValueError(f'끝점 ID/라벨이 모호함: {labels}')
    return found[0]


def snapshot_signature(state: dict) -> str:
    nodes = sorted(state['nodes'], key=lambda n: n['eid'])
    # Old backups may include full endpoint props. Only the endpoint identity
    # belongs in the edge signature; owned node properties are checked above.
    edges = sorted(({
        'eid': e['eid'], 'start_eid': e['start_eid'], 'end_eid': e['end_eid'],
        'start': node_key(e['start_labels'], e['start_props']),
        'end': node_key(e['end_labels'], e['end_props']),
        'type': e['type'], 'props': e['props'],
    } for e in state['edges']), key=lambda e: e['eid'])
    return digest({'nodes': nodes, 'edges': edges})


def semantic_signature(state: dict) -> str:
    """Compare restored properties/relations even when Neo4j allocates new IDs."""
    nodes = sorted([(node_key(n['labels'], n['props']), sorted(n['labels']), n['props'])
                    for n in state['nodes']], key=lambda item: item[0])
    edges = sorted([(node_key(e['start_labels'], e['start_props']), e['type'],
                     node_key(e['end_labels'], e['end_props']), e['props']) for e in state['edges']], key=str)
    return digest({'nodes': nodes, 'edges': edges})


def prepare_bundle(state: dict, candidate: dict) -> tuple[dict, dict]:
    docs = {d['document_id'] for d in candidate['nodes']['Document']}
    if docs != SCHOOL_DOCS:
        raise ValueError('학교 문서 두 개 외의 후보는 이행할 수 없음')
    owned_eids = {n['eid'] for n in state['nodes']}
    old = {node_key(n['labels'], n['props']): n['props'] for n in state['nodes']}
    for label, ident in old:
        if not (ident in docs if label == 'Document'
                else any(ident.startswith(d + ':') for d in docs)):
            raise ValueError('학교 범위 밖 노드가 스냅샷에 포함됨')
    result = copy.deepcopy(candidate)
    chunks = {c['chunk_id']: c for c in result['nodes']['Chunk']}
    preserved = []
    for e in state['edges']:
        if e['start_eid'] not in owned_eids:
            raise ValueError('다른 영역의 incoming 관계가 있어 자동 교체할 수 없음')
        start = node_key(e['start_labels'], e['start_props'])
        end = node_key(e['end_labels'], e['end_props'])
        if (start[0], e['type'], end[0]) not in ALLOWED_EDGES:
            raise ValueError(f"예상하지 못한 관계: {e['type']}")
        if e['type'] != 'ABOUT':
            continue
        if start[1] not in chunks:
            raise ValueError('폐기될 청크에 ABOUT 관계가 있음: 검토 필요')
        # Existing mappings are retained only when the actual table row has
        # not changed. Positional paragraph IDs alone do not prove identity.
        before = old[start]['text']
        after = chunks[start[1]]['text']
        if ':t' not in start[1] or before.splitlines()[-1] != after.splitlines()[-1]:
            raise ValueError(f'ABOUT 근거 행이 변경됨: {start[1]}')
        preserved.append({'start_label': start[0], 'start_id': start[1], 'type': 'ABOUT',
                          'end_label': end[0], 'end_id': end[1], 'props': e['props']})
    existing = {edge_key(e) for e in result['edges']}
    for e in preserved:
        if edge_key(e) not in existing:
            result['edges'].append(e)
    result['expected'] = expected_keys(result)
    result['dictionary_refs'] = sorted({(e['end_label'], e['end_id']) for e in result['edges']
                                        if e['end_label'] in {'Disease','SchoolRole','ActionConcept'}})
    new = {(label, row[ID_FIELD[label]]) for label, rows in result['nodes'].items() for row in rows}
    return result, {'preserved_about': len(preserved), 'obsolete': sorted(set(old) - new),
                    'new': sorted(new - set(old)), 'before_signature': snapshot_signature(state)}


def assert_matches(state: dict, bundle: dict, dimension: int) -> None:
    expected = {(label, row[ID_FIELD[label]]): row for label, rows in bundle['nodes'].items() for row in rows}
    actual = {node_key(n['labels'], n['props']): n['props'] for n in state['nodes']}
    if set(expected) != set(actual):
        raise ValueError('이행 후 노드 집합 불일치')
    for key, wanted in expected.items():
        for field, value in wanted.items():
            if actual[key].get(field) != value:
                raise ValueError(f'이행 후 속성 불일치: {key} {field}')
        if key[0] == 'Chunk' and len(actual[key].get('embedding') or []) != dimension:
            raise ValueError(f'이행 후 임베딩 누락/차원 오류: {key}')
    edges = set()
    for e in state['edges']:
        start, end = (node_key(e[f'{side}_labels'], e[f'{side}_props']) for side in ['start','end'])
        edges.add((start[0], start[1], e['type'], end[0], end[1], e['props'].get('basis')))
    expected_edges = {edge_key(e) for e in bundle['edges']}
    if edges != expected_edges or len(state['edges']) != len(expected_edges):
        raise ValueError('이행 후 관계 집합/중복 불일치')
