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


def main():
    root = Path.cwd()
    books, manifest = load_dictionaries(), load_manifest(root)
    chunks, _ = build_all_chunks(root, manifest, books)
    old = read_jsonl(root / 'data/interim/school/full_20261105T032224_612625Z/official_chunks.jsonl')
    normalized_dir = root / 'data/interim/school/normalized_20261105T032224_612625Z'
    actions = read_jsonl(normalized_dir / 'normalized.jsonl')
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
    summary['code_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
        Path('src/preprocess/school/chunks.py'), Path('src/preprocess/school/structure.py'),
        Path('data/specs/source_manifest.csv'), Path('data/specs/school_layout_corrections.json')]}
    out = root / 'data/interim/school' / datetime.now(UTC).strftime('chunking_v2_%Y%m%dT%H%M%S_%fZ')
    out.mkdir()
    write_jsonl(out / 'official_chunks.jsonl', chunks)
    write_jsonl(out / 'extraction_inputs.jsonl', extracted)
    for name, value in [('summary', summary), ('migration', migration), ('citations', citations),
                        ('candidate_bundle', bundle)]:
        (out / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('ARTIFACTS', out.relative_to(root))


if __name__ == '__main__':
    main()
