"""Read-only migration checks. A surviving positional ID is not proof of identity."""
from __future__ import annotations

from index.school.normalize import loose_find


def migration_audit(old: list[dict], new: list[dict]) -> list[dict]:
    by_id = {c['chunk_id']: c for c in new}
    result = []
    for c in old:
        same_id = by_id.get(c['chunk_id'])
        matches = [n['chunk_id'] for n in new
                   if n['document_id'] == c['document_id'] and n['pdf_page'] == c['pdf_page']
                   and (n['text'] == c['text'] or loose_find(n['text'], c['text']) >= 0)]
        result.append({'chunk_id': c['chunk_id'], 'candidates': matches,
                           'same_id_exists': same_id is not None,
                           'same_id_same_text': same_id is not None and same_id['text'] == c['text'],
                           'row_unchanged': (same_id is not None
                                          and same_id['row_text'] == c['row_text'])})
    return result


def citation_audit(citations: list[dict], chunks: list[dict]) -> list[dict]:
    """Use excerpt + document + page; never silently rewrite the golden set."""
    result = []
    for citation in citations:
        candidates = [c['chunk_id'] for c in chunks
                      if c['document_id'] == citation['document_id']
                      and c['pdf_page'] == citation['pdf_page']
                      and loose_find(c['text'], citation['excerpt']) >= 0]
        result.append({**citation, 'candidates': candidates,
                       'old_id_valid': citation['chunk_id'] in candidates})
    return result
