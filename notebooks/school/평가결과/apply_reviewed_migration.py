"""Explicit school v3 migration; inspect is read-only, snapshots stay local.

No URI, credential or embedding values are printed.
"""
import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, 'src')
from index.school.bundle import ALLOWED_EDGES, ID_FIELD, validate_bundle
from index.school.dictionaries import load_dictionaries
from index.school.embed import EMBEDDING_DIM, EMBEDDING_MODEL, _ledger, make_embedder, text_sha
from index.school.load import _edge_groups, _run, edge_query, node_query
from index.school.migration import (
    assert_matches,
    prepare_bundle,
    semantic_signature,
    snapshot_signature,
)
from index.school.neo4j_local import get_driver
from index.school.schema_init import missing_dictionary_refs, schema_state

DOCS = ['moe_school_manual_v3', 'gyeonggi_school_guide_2025']
IN_SCOPE = """(n:Document AND n.document_id IN $docs) OR
 (n:Chunk AND any(d IN $docs WHERE n.chunk_id STARTS WITH d + ':')) OR
 (n:SchoolAction AND any(d IN $docs WHERE n.action_id STARTS WITH d + ':'))"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def snapshot(reader):
    nodes = reader.run(f'MATCH (n) WHERE {IN_SCOPE} '
                       'RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props',
                       docs=DOCS).data()
    eids = [n['eid'] for n in nodes]
    edges = reader.run('MATCH (s)-[r]->(t) WHERE elementId(s) IN $eids OR elementId(t) IN $eids '
                       'RETURN elementId(r) AS eid, elementId(s) AS start_eid, '
                       'elementId(t) AS end_eid, labels(s) AS start_labels, '
                       's {.document_id,.chunk_id,.action_id,.disease_id,.role_id,.concept_id} AS start_props, '
                       'labels(t) AS end_labels, '
                       't {.document_id,.chunk_id,.action_id,.disease_id,.role_id,.concept_id} AS end_props, '
                       'type(r) AS type, properties(r) AS props', eids=eids).data()
    nodes.sort(key=lambda n: n['eid'])
    edges.sort(key=lambda e: e['eid'])
    return {'nodes': nodes, 'edges': edges}


def node_key(labels, props):
    found = [(label, props[ID_FIELD[label]]) for label in labels
             if label in ID_FIELD and ID_FIELD[label] in props]
    if len(found) != 1:
        raise ValueError(f'끝점 ID/라벨이 모호함: {labels}')
    return found[0]


def inspect(args):
    bundle = json.loads((args.candidate / 'candidate_bundle.json').read_text(encoding='utf-8'))
    validate_bundle(bundle, load_dictionaries())
    assert sorted(d['document_id'] for d in bundle['nodes']['Document']) == sorted(DOCS)
    driver, database = get_driver(Path('.env'))
    try:
        with driver.session(database=database) as session:
            state = session.execute_read(snapshot)
        out = Path('data/interim/school') / datetime.now(UTC).strftime('service_migration_%Y%m%dT%H%M%S_%fZ')
        out.mkdir()
        save(out / 'before.json', state)
        save(out / 'candidate_bundle.json', bundle)
        wanted = {(label, row[ID_FIELD[label]]) for label, rows in bundle['nodes'].items() for row in rows}
        actual = {node_key(n['labels'], n['props']) for n in state['nodes']}
        eids = {n['eid'] for n in state['nodes']}
        foreign = [e for e in state['edges'] if e['start_eid'] not in eids]
        summary = {'candidate': str(args.candidate), 'snapshot_sha256': digest(state),
                   'candidate_sha256': digest(bundle),
                   'before_counts': dict(Counter(node_key(n['labels'], n['props'])[0] for n in state['nodes'])),
                   'candidate_counts': {k: len(v) for k, v in bundle['nodes'].items()},
                   'obsolete_nodes': sorted(actual - wanted), 'new_nodes': sorted(wanted - actual),
                   'foreign_incoming_edges': len(foreign),
                   'foreign_incoming_details': [{k: v for k, v in e.items() if k not in {'start_props','end_props'}} for e in foreign],
                   'relationship_counts': dict(Counter(e['type'] for e in state['edges'])),
                   'missing_dictionary_refs': missing_dictionary_refs(driver, bundle['dictionary_refs'], database),
                   'schema': schema_state(driver, database), 'db_writes': 0}
        save(out / 'preflight.json', summary)
        safe = {k: v for k, v in summary.items() if k not in {'schema','new_nodes','foreign_incoming_details'}}
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        print('MIGRATION_DIR', out)
    finally:
        driver.close()


def load_plan(args):
    folder = args.migration_dir
    before = json.loads((folder / 'before.json').read_text(encoding='utf-8'))
    preflight = json.loads((folder / 'preflight.json').read_text(encoding='utf-8'))
    candidate = json.loads((folder / 'candidate_bundle.json').read_text(encoding='utf-8'))
    if digest(before) != preflight['snapshot_sha256'] or digest(candidate) != preflight['candidate_sha256']:
        raise ValueError('스냅샷/후보 체크섬 불일치')
    if args.candidate.resolve() != Path(preflight['candidate']).resolve():
        raise ValueError('사전 점검과 다른 후보 폴더')
    if candidate != json.loads((args.candidate / 'candidate_bundle.json').read_text(encoding='utf-8')):
        raise ValueError('사전 점검 후 후보 파일 변경')
    # Regenerate from current source to prevent applying a stale reviewed artifact.
    from index.school.bundle import build_load_bundle
    from index.school.id_map import document_node, load_manifest
    from index.school.normalize import read_jsonl
    from preprocess.school.chunks import build_all_chunks
    from preprocess.school.reviewed_context import apply_reviewed_actions, load_review
    books, manifest = load_dictionaries(), load_manifest('.')
    chunks, _ = build_all_chunks('.', manifest, books)
    actions = apply_reviewed_actions(read_jsonl(Path(
        'data/interim/school/normalized_20261105T032224_612625Z/normalized.jsonl')), load_review('.'))
    fresh = build_load_bundle(actions, chunks, [document_node(d, manifest) for d in sorted(DOCS)], books)
    if digest(fresh) != digest(candidate):
        raise ValueError('현재 코드/원문과 후보 패키지 불일치')
    effective, plan = prepare_bundle(before, candidate)
    validate_bundle(effective, books)
    return before, effective, plan


def valid_vector(vector):
    return isinstance(vector, list) and len(vector) == EMBEDDING_DIM and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vector)


def prepare(args):
    before, bundle, plan = load_plan(args)
    # No DB writes: embeddings must be ready before the atomic data switch.
    driver, database = get_driver(Path('.env'))
    try:
        with driver.session(database=database) as session:
            current = session.execute_read(snapshot)
        if snapshot_signature(current) != plan['before_signature']:
            raise ValueError('사전 점검 후 학교 DB 변경: 스냅샷부터 다시 확인 필요')
        if missing_dictionary_refs(driver, bundle['dictionary_refs'], database):
            raise ValueError('사전 노드 누락')
    finally:
        driver.close()
    chunks = bundle['nodes']['Chunk']
    old = {n['props']['chunk_id']: n['props'] for n in before['nodes'] if 'Chunk' in n['labels']}
    ledger = _ledger(Path('data/interim/school/embed_ledger.jsonl'))
    path = args.migration_dir / 'vectors.json'
    cache = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'model': EMBEDDING_MODEL, 'dimension': EMBEDDING_DIM, 'vectors': {}, 'text_hashes': {}}
    if cache['model'] != EMBEDDING_MODEL or cache['dimension'] != EMBEDDING_DIM:
        raise ValueError('임베딩 체크포인트 모델 불일치')
    generated, reused = 0, 0
    todo = []
    for c in chunks:
        cid, sha = c['chunk_id'], text_sha(c['text'])
        if cid in cache['vectors'] and cache['text_hashes'].get(cid) == sha and valid_vector(cache['vectors'][cid]):
            continue
        props, known = old.get(cid, {}), ledger.get(cid, {})
        if (props.get('text') == c['text'] and valid_vector(props.get('embedding'))
                and known.get('model') == EMBEDDING_MODEL and known.get('text_sha256') == sha):
            cache['vectors'][cid] = props['embedding']
            cache['text_hashes'][cid] = sha
            reused += 1
        else:
            todo.append(c)
    embedder = make_embedder()
    for index in range(0, len(todo), 96):
        batch = todo[index:index + 96]
        vectors = embedder.embed_documents([c['text'] for c in batch])
        if len(vectors) != len(batch) or not all(valid_vector(v) for v in vectors):
            raise ValueError('임베딩 응답 개수/차원/수치 오류')
        for c, vector in zip(batch, vectors, strict=True):
            cache['vectors'][c['chunk_id']] = vector
            cache['text_hashes'][c['chunk_id']] = text_sha(c['text'])
        generated += len(batch)
        path.write_text(json.dumps(cache, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        print('embedding prepared', min(index + len(batch), len(todo)), '/', len(todo), flush=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    plan.update({'generated_embeddings_this_run': generated, 'reused_embeddings_this_run': reused,
                 'vector_count': len(cache['vectors']), 'model': EMBEDDING_MODEL,
                 'dimension': EMBEDDING_DIM, 'vector_file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    save(args.migration_dir / 'effective_bundle.json', bundle)
    save(args.migration_dir / 'prepared_plan.json', plan)
    print(json.dumps({k: v for k,v in plan.items() if k not in {'new','obsolete'}}, ensure_ascii=False))


def other_signature(reader):
    # Fingerprint the other teams' data and dictionary nodes, including vectors.
    rows = reader.run(f'MATCH (n) WHERE NOT ({IN_SCOPE}) '
                      'RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props', docs=DOCS).data()
    eids = [r['eid'] for r in rows]
    edges = reader.run('MATCH (s)-[r]->(t) WHERE elementId(s) IN $eids AND elementId(t) IN $eids '
                       'RETURN elementId(r) AS eid, elementId(s) AS s, elementId(t) AS t, '
                       'type(r) AS type, properties(r) AS props', eids=eids).data()
    return digest({'nodes': sorted(rows,key=lambda r:r['eid']), 'edges': sorted(edges,key=lambda r:r['eid'])})


def apply(args):
    _, bundle, plan = load_plan(args)
    staged = json.loads((args.migration_dir / 'prepared_plan.json').read_text(encoding='utf-8'))
    path = args.migration_dir / 'vectors.json'
    if hashlib.sha256(path.read_bytes()).hexdigest() != staged['vector_file_sha256']:
        raise ValueError('임베딩 체크섬 불일치')
    cache = json.loads(path.read_text(encoding='utf-8'))
    if cache['model'] != EMBEDDING_MODEL or cache['dimension'] != EMBEDDING_DIM:
        raise ValueError('임베딩 모델/차원 불일치')
    for c in bundle['nodes']['Chunk']:
        if cache['text_hashes'].get(c['chunk_id']) != text_sha(c['text']) or not valid_vector(cache['vectors'].get(c['chunk_id'])):
            raise ValueError('본문과 다른 임베딩 또는 누락')
    driver, database = get_driver(Path('.env'))
    try:
        if missing_dictionary_refs(driver, bundle['dictionary_refs'], database):
            raise ValueError('사전 노드 누락')

        def replace(tx):
            current = snapshot(tx)
            # Resume after an uncertain client disconnect without reapplying.
            try:
                assert_matches(current, bundle, EMBEDDING_DIM)
                return {'already_applied': True, 'after_signature': snapshot_signature(current)}
            except ValueError:
                pass
            if snapshot_signature(current) != plan['before_signature']:
                raise ValueError('사전 점검 이후 학교 데이터 변경: 이행 중단')
            outside = other_signature(tx)
            # All incident edges were checked above: no foreign incoming or unknown types.
            eids = [n['eid'] for n in current['nodes']]
            tx.run('MATCH (n)-[r]->() WHERE elementId(n) IN $eids DELETE r', eids=eids).consume()
            for label in ['SchoolAction','Chunk']:
                ids = [ident for kind, ident in plan['obsolete'] if kind == label]
                # DELETE (not DETACH) fails and rolls back if a new foreign reference appeared.
                tx.run(f'MATCH (n:{label}) WHERE n.{ID_FIELD[label]} IN $ids DELETE n', ids=ids).consume()
            for label in ['Document','Chunk','SchoolAction']:
                _run(tx, node_query(label), bundle['nodes'][label], label)
            for (start, rel, end), rows in _edge_groups(bundle['edges'],
                    {'HAS_CHUNK','ABOUT','SUPPORTED_BY','DONE_BY','INSTANCE_OF','FOR_DISEASE'}).items():
                _run(tx, edge_query(start, rel, end), rows, rel)
            chunks = bundle['nodes']['Chunk']
            for index in range(0, len(chunks), 128):
                rows = [{'id': c['chunk_id'], 'vector': cache['vectors'][c['chunk_id']]} for c in chunks[index:index+128]]
                count = tx.run('UNWIND $rows AS row MATCH (c:Chunk {chunk_id: row.id}) '
                               'SET c.embedding = row.vector RETURN count(c) AS n', rows=rows).single()['n']
                if count != len(rows):
                    raise ValueError('임베딩 저장 건수 불일치')
            after = snapshot(tx)
            assert_matches(after, bundle, EMBEDDING_DIM)
            if other_signature(tx) != outside:
                raise ValueError('다른 영역 상태 변경 감지: 트랜잭션 롤백')
            return {'already_applied': False, 'after_signature': snapshot_signature(after),
                    'other_scope_sha256': outside, 'other_scope_unchanged': True,
                    'nodes': {k:len(v) for k,v in bundle['nodes'].items()}, 'edges':len(bundle['edges'])}

        with driver.session(database=database) as session:
            receipt = session.execute_write(replace)
        # A receipt is written only after transaction commit.
        receipt.update({'committed_at': datetime.now(UTC).isoformat(), 'candidate_sha256': digest(bundle),
                        'preserved_about':plan['preserved_about']})
        receipt_path = args.migration_dir / 'receipt.json'
        if receipt['already_applied'] and receipt_path.exists():
            original = json.loads(receipt_path.read_text(encoding='utf-8'))
            if original['after_signature'] != receipt['after_signature']:
                raise ValueError('기존 적용 영수증과 재실행 결과 불일치')
            save(args.migration_dir / 'resume_check.json', receipt)
            # Preserve the original before/after proof and rollback anchor.
            receipt = original
        else:
            save(receipt_path, receipt)
        driver.execute_query('CALL db.awaitIndexes(30)', database_=database)
        with driver.session(database=database) as session:
            current = session.execute_read(snapshot)
        assert_matches(current, bundle, EMBEDDING_DIM)
        save(args.migration_dir / 'after.json', current)
        # Existing helper ledgers can now skip unchanged vectors on later runs.
        with Path('data/interim/school/embed_ledger.jsonl').open('a', encoding='utf-8') as handle:
            for c in bundle['nodes']['Chunk']:
                handle.write(json.dumps({'chunk_id':c['chunk_id'], 'text_sha256':text_sha(c['text']),
                    'model':EMBEDDING_MODEL,'dim':EMBEDDING_DIM,'timestamp_utc':receipt['committed_at']})+'\n')
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    finally:
        driver.close()


def verify(args):
    _, bundle, _ = load_plan(args)
    receipt = json.loads((args.migration_dir / 'receipt.json').read_text(encoding='utf-8'))
    driver, database = get_driver(Path('.env'))
    try:
        with driver.session(database=database) as session:
            current = session.execute_read(snapshot)
            outside = session.execute_read(other_signature)
        assert_matches(current, bundle, EMBEDDING_DIM)
        if snapshot_signature(current) != receipt['after_signature']:
            raise ValueError('적용 영수증 이후 학교 데이터 변경')
        if outside != receipt['other_scope_sha256']:
            raise ValueError('외부 영역이 평가 중 변경됨: 담당자 확인 필요')
        from index.school.normalize import loose_find
        from preprocess.school.reviewed_context import load_review
        review = load_review('.')
        chunks = {n['props']['chunk_id']:n['props'] for n in current['nodes'] if 'Chunk' in n['labels']}
        rows_checked = 0
        for record in review['tables']:
            rows = [c for cid,c in chunks.items() if cid.rsplit('r',1)[0] == record['table_id']]
            if not rows or not all(c['section'] == record['resolved_section'] for c in rows):
                raise ValueError(f"DB 검수 주소 불일치: {record['review_id']}")
            rows_checked += len(rows)
        golden = json.loads(Path('data/specs/golden/school.json').read_text(encoding='utf-8'))
        if not all(c['chunk_id'] in chunks and loose_find(chunks[c['chunk_id']]['text'],c['excerpt']) >= 0
                   for c in golden['citations']):
            raise ValueError('DB 골든 인용 불일치')
        result = {'verified_at':datetime.now(UTC).isoformat(),'school_matches_candidate':True,
                  'other_scope_unchanged':True,'reviewed_tables':len(review['tables']),
                  'reviewed_rows':rows_checked,'golden_citations':len(golden['citations']),
                  'chunk_vectors':len(chunks),'dimension':EMBEDDING_DIM}
        save(args.migration_dir/'final_verification.json',result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        driver.close()


def rollback(args):
    folder = args.migration_dir
    before = json.loads((folder / 'before.json').read_text(encoding='utf-8'))
    preflight = json.loads((folder / 'preflight.json').read_text(encoding='utf-8'))
    receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
    if digest(before) != preflight['snapshot_sha256']:
        raise ValueError('복구 스냅샷 체크섬 불일치')
    golden_hashes = json.loads((folder / 'golden_applied_hashes.json').read_text(encoding='utf-8'))
    for path, sha in golden_hashes.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha:
            raise ValueError('이행 이후 골든셋 변경: 자동 복구 중단')
    driver, database = get_driver(Path('.env'))
    try:
        def restore(tx):
            current = snapshot(tx)
            if snapshot_signature(current) != receipt['after_signature']:
                raise ValueError('이행 이후 학교 DB 변경: 자동 복구 중단')
            outside = other_signature(tx)
            keep = {node_key(n['labels'],n['props']) for n in before['nodes']}
            tx.run('MATCH (n)-[r]->() WHERE elementId(n) IN $eids DELETE r',
                   eids=[n['eid'] for n in current['nodes']]).consume()
            for label in ['SchoolAction','Chunk']:
                ids = [n['props'][ID_FIELD[label]] for n in current['nodes']
                       if label in n['labels'] and node_key(n['labels'],n['props']) not in keep]
                tx.run(f'MATCH (n:{label}) WHERE n.{ID_FIELD[label]} IN $ids DELETE n', ids=ids).consume()
            for label in ['Document','Chunk','SchoolAction']:
                rows = [{'id':n['props'][ID_FIELD[label]],'props':n['props']} for n in before['nodes']
                        if n['labels'] == [label]]
                count = tx.run(f'UNWIND $rows AS row MERGE (n:{label} {{{ID_FIELD[label]}:row.id}}) '
                               'SET n = row.props RETURN count(n) AS n', rows=rows).single()['n']
                if count != len(rows):
                    raise ValueError('복구 노드 건수 불일치')
            for start, rel, end in ALLOWED_EDGES:
                rows = [{'start':node_key(e['start_labels'],e['start_props'])[1],
                         'end':node_key(e['end_labels'],e['end_props'])[1], 'props':e['props']}
                        for e in before['edges'] if (e['start_labels'],e['type'],e['end_labels']) == ([start],rel,[end])]
                tx.run(f'UNWIND $rows AS row MATCH (s:{start} {{{ID_FIELD[start]}:row.start}}) '
                       f'MATCH (t:{end} {{{ID_FIELD[end]}:row.end}}) MERGE (s)-[r:{rel}]->(t) '
                       'SET r = row.props', rows=rows).consume()
            restored = snapshot(tx)
            if semantic_signature(restored) != semantic_signature(before) or other_signature(tx) != outside:
                raise ValueError('복구 결과/다른 영역 검증 실패: 롤백')
        with driver.session(database=database) as session:
            session.execute_write(restore)
        Path('data/specs/golden/school.json').write_bytes((folder/'golden_before.json').read_bytes())
        Path('data/specs/golden/school.md').write_bytes((folder/'golden_before.md').read_bytes())
        active_path = Path('data/specs/school_active_dataset.json')
        if active_path.exists():
            active = json.loads(active_path.read_text(encoding='utf-8'))
            active.update(status='rolled_back', bundle_path=None,
                          restored_snapshot=str(folder/'before.json'))
            save(active_path,active)
        save(folder/'rollback_receipt.json', {'restored_at':datetime.now(UTC).isoformat(),
                                            'restored_properties_and_vectors':True})
        print('School snapshot and golden references restored; other scope unchanged.')
    finally:
        driver.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--phase', choices=['inspect','prepare','apply','verify','rollback'], default='inspect')
    parser.add_argument('--migration-dir', type=Path)
    args = parser.parse_args()
    if args.phase != 'inspect' and args.migration_dir is None:
        parser.error('--migration-dir required')
    {'inspect':inspect,'prepare':prepare,'apply':apply,'verify':verify,'rollback':rollback}[args.phase](args)


if __name__ == '__main__':
    main()
