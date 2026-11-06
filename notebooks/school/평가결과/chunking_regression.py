"""재청킹·후속 입력·적재 패키지·골든 인용 호환성 검사. DB/LLM 쓰기 없음.

.venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/chunking_regression.py
"""
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, 'src')
from index.school.bundle import build_load_bundle
from index.school.dictionaries import load_dictionaries
from index.school.id_map import document_node, load_manifest
from index.school.normalize import loose_find, read_jsonl, write_jsonl
from preprocess.school.chunk_audit import citation_audit, migration_audit
from preprocess.school.chunks import CHUNKER_VERSION, build_all_chunks
from preprocess.school.extract import build_prompt, build_role_row_chunks, load_concept_draft
from preprocess.school.markdown_data import load_school_data
from preprocess.school.reviewed_context import REVIEW_FILE, apply_reviewed_actions, load_review


def main():
    root = Path.cwd()
    books, manifest = load_dictionaries(), load_manifest(root)
    chunks, _ = build_all_chunks(root, manifest, books)
    old = read_jsonl(root / 'data/interim/school/full_20261105T032224_612625Z/official_chunks.jsonl')
    normalized_dir = root / 'data/interim/school/normalized_20261105T032224_612625Z'
    actions = read_jsonl(normalized_dir / 'normalized.jsonl')
    review = load_review(root)
    actions = apply_reviewed_actions(actions, review)
    by_id = {c['chunk_id']: c for c in chunks}
    assert len(by_id) == len(chunks)
    assert all(loose_find(by_id[a['chunk_id']]['row_text'], a['evidence']) >= 0 for a in actions)
    documents = [document_node(d, manifest) for d in sorted({c['document_id'] for c in chunks})]
    # ABOUT deliberately omitted: mapping work is outside this regression scope.
    bundle = build_load_bundle(actions, chunks, documents, books)
    _, sources, blocks = load_school_data(root)
    ids = json.loads((root / 'data/specs/golden/school_extraction_input_ids.json').read_text(encoding='utf-8'))['chunk_ids']
    extracted = build_role_row_chunks(blocks, sources, ids,
                                     {c['chunk_id']: c['section'] for c in chunks})
    raw_sources = {r.source_id: r.text for r in sources.itertuples()}
    concept = load_concept_draft(root)
    for c in extracted:
        assert c['official_chunk_id'] in by_id
        for part in c['source_parts']:
            assert raw_sources[part['source_id']][part['start_offset']:part['end_offset']] == part['text']
        prompt = build_prompt(c, concept)
        assert by_id[c['official_chunk_id']]['section'] in prompt
    golden = json.loads((root / 'data/specs/golden/school.json').read_text(encoding='utf-8'))
    citations = citation_audit(golden['citations'], chunks)
    citation_by_id = {c['cid']: c for c in citations}
    reviewed_moves = []
    golden_candidate = json.loads(json.dumps(golden))
    applied_reviews = set()
    for record in review.get('citations', []):
        c = citation_by_id[record['cid']]
        if c['excerpt'] == record['excerpt'] and c['chunk_id'] != record['old_chunk_id']:
            # 11/06 v3 이행에서 이미 golden에 반영된 검수 이동. 이번 재분할로 다시 바뀐 ID는
            # 아래 '같은 문서·쪽·발췌의 단일 후보' 규칙으로만 옮긴다.
            applied_reviews.add(record['cid'])
            continue
        if c['excerpt'] != record['excerpt'] or c['chunk_id'] != record['old_chunk_id']:
            raise ValueError(f"검수 이후 골든 원문 변경: {record['cid']}")
        if len(c['candidates']) != 1:
            raise ValueError(f"인용 이동 후보가 모호함: {record['cid']}")
        new_id = c['candidates'][0]
        reviewed_moves.append({'cid': c['cid'], 'old_chunk_id': c['chunk_id'], 'new_chunk_id': new_id})
        next(g for g in golden_candidate['citations'] if g['cid'] == c['cid'])['chunk_id'] = new_id
    automatic_moves = []
    reviewed_ids = {r['cid'] for r in review.get('citations', [])} - applied_reviews
    for c in citations:
        if not c['old_id_valid'] and c['cid'] not in reviewed_ids:
            if len(c['candidates']) != 1:
                raise ValueError(f"추가 인용 이동 후보가 모호함: {c['cid']}")
            new_id = c['candidates'][0]
            automatic_moves.append({'cid': c['cid'], 'old_chunk_id': c['chunk_id'],
                                    'new_chunk_id': new_id, 'basis': 'same document/page/excerpt, unique candidate'})
            next(g for g in golden_candidate['citations'] if g['cid'] == c['cid'])['chunk_id'] = new_id
    assert all(c['old_id_valid'] for c in citation_audit(golden_candidate['citations'], chunks))
    migration = migration_audit(old, chunks)
    summary = {'chunker_version': CHUNKER_VERSION, 'old_chunks': len(old), 'new_chunks': len(chunks),
                   'kinds': dict(Counter(c['kind'] for c in chunks)),
                   'table_contexts': dict(Counter(c['context_status'] for c in chunks if c['kind'] == 'table_row')),
                   'table_row_ids_and_content_unchanged': all(
                       by_id[c['chunk_id']]['row_text'] == c['row_text'] for c in old if c['kind'] == 'table_row'),
                   'extraction_inputs_checked': len(extracted), 'source_evidence_checked': len(actions),
                   'candidate_bundle_nodes': {k: len(v) for k, v in bundle['nodes'].items()},
                   'citations': len(citations), 'citations_same_id': sum(c['old_id_valid'] for c in citations),
                   'citations_locatable': sum(bool(c['candidates']) for c in citations),
                   'positional_id_changed_text': sum(c['same_id_exists'] and not c['same_id_same_text'] for c in migration),
                   'downstream_status': 'candidate_only_requires_metadata_review_and_db_migration',
                   'db_writes': 0, 'llm_calls': 0}
    summary['reviewed_tables'] = len(review.get('tables', []))
    summary['reviewed_citation_moves'] = len(reviewed_moves)
    summary['additional_unique_citation_moves'] = len(automatic_moves)
    summary['action_stage_decisions'] = sum(len(d['action_ids']) for d in review.get('actions', []))
    summary['review_source_sha256'] = review.get('source_reviews', [])
    summary['code_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
        Path('src/preprocess/school/chunks.py'), Path('src/preprocess/school/structure.py'),
        Path('data/specs/source_manifest.csv'), Path('data/specs/school_layout_corrections.json'),
        Path(REVIEW_FILE)]}
    out = root / 'data/interim/school' / datetime.now(UTC).strftime('chunking_v4_%Y%m%dT%H%M%S_%fZ')
    out.mkdir()
    write_jsonl(out / 'official_chunks.jsonl', chunks)
    write_jsonl(out / 'extraction_inputs.jsonl', extracted)
    write_jsonl(out / 'reviewed_actions.jsonl', actions)
    for name, value in [('summary', summary), ('migration', migration), ('citations', citations),
                        ('candidate_bundle', bundle), ('golden_candidate', golden_candidate),
                        ('reviewed_citation_moves', reviewed_moves), ('additional_citation_moves', automatic_moves)]:
        (out / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('ARTIFACTS', out.relative_to(root))


if __name__ == '__main__':
    main()
