"""Compile the user's completed review sheets into source metadata, not DB writes.

Never edit the review sheets or overwrite an existing compiled snapshot.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, 'src')
from preprocess.school.reviewed_context import REVIEW_FILE, rows_hash
from preprocess.school.structure import heading_kind

FOLDER = Path('notebooks/school/평가결과')
FILES = ['청킹_주소검토표_20261106.md', '청킹_인용이동검토표_20261106.md',
         '청킹_행동단계검토표_20261106.md']
BASELINE = Path('data/interim/school/chunking_v2_20261105T223223_186130Z/official_chunks.jsonl')


def field(block, name):
    # Spaces only: an empty field must not consume the next Markdown line.
    match = re.search(r'^- ' + re.escape(name) + r':[ \t]*(.*)$', block, re.MULTILINE)
    return match.group(1).strip() if match else ''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path(REVIEW_FILE))
    args = parser.parse_args()
    raw = [(FOLDER / name).read_text(encoding='utf-8') for name in FILES]
    chunks = [json.loads(line) for line in BASELINE.read_text(encoding='utf-8').splitlines()]
    by_table = {}
    for c in chunks:
        if c['kind'] == 'table_row':
            by_table.setdefault(c['chunk_id'].rsplit('r', 1)[0], []).append(c)
    snapshot = {'version': 1, 'reviewer': 'user', 'source_reviews': [
        {'path': str(FOLDER / name), 'sha256': hashlib.sha256((FOLDER / name).read_bytes()).hexdigest()}
        for name in FILES], 'tables': [], 'citations': [], 'actions': []}
    for block in re.split(r'(?m)(?=^## T\d+)', raw[0])[1:]:
        review_id = re.match(r'## (T\d+)', block).group(1)
        table_id = field(block, '표 ID').strip('`')
        verdict = field(block, '판정')
        if verdict not in {'주소 맞음', '주소 수정'}:
            raise ValueError(f'미완성 검수: {review_id}: {verdict}')
        current, confirmed, note = (field(block, n) for n in ['현재 주소', '확정 주소', '메모'])
        if verdict == '주소 수정' and not confirmed:
            raise ValueError(f'수정 주소 없음: {review_id}')
        section = confirmed or current
        pending = '?' in section
        section = re.sub(r'\s*>\s*\?\s*$', '', section)
        if '?' in section:
            raise ValueError(f'주소 중간이 미확정: {review_id}')
        quoted_titles = re.findall(r"'([^']+)'", note) if re.search(r'표\s*이름', note) else []
        original_title = by_table[table_id][0]['text'].split('\n', 1)[0]
        tail = section.split(' > ')[-1]
        title = original_title if verdict == '주소 맞음' else None
        title_verified = verdict == '주소 맞음'
        if verdict == '주소 수정' and not pending and not heading_kind(tail):
            title, title_verified = tail, True
        if quoted_titles:
            # "'후보 1'이 아니라 '<표 ...>'" contains both rejected and accepted labels.
            title, title_verified = quoted_titles[-1], True
            pieces = section.split(' > ')
            if not heading_kind(pieces[-1]):
                pieces.pop()
            section = ' > '.join([*pieces, title])
        snapshot['tables'].append({
            'review_id': review_id, 'table_id': table_id, 'verdict': verdict,
            'reviewed_section': confirmed or current, 'resolved_section': section,
            'resolved_title': title, 'title_verified': title_verified,
            'caption_pending': pending, 'note': note,
            'rows_sha256': rows_hash(by_table[table_id])})
    for block in re.split(r'(?m)(?=^## C\d+)', raw[1])[1:]:
        if field(block, '판정') != '동일 근거로 이동':
            raise ValueError('인용 이동 검수 미완료')
        snapshot['citations'].append({
            'cid': re.match(r'## (C\d+)', block).group(1),
            'old_chunk_id': field(block, '이전 ID').strip('`'),
            'reviewed_v2_chunk_id': field(block, '새 후보').strip('`'),
            'excerpt': field(block, '발췌'), 'verdict': '동일 근거로 이동'})
    for block in re.split(r'(?m)(?=^## `)', raw[2])[1:]:
        chunk_id = re.match(r'## `([^`]+)`', block).group(1)
        verdict = field(block, '판정')
        entries = re.findall(r'^\| (\S+:a\d+) \| .*? \| (\w+) \| (\w+) \|$', block, re.MULTILINE)
        if not entries or verdict not in {'기존 유지', '단계 수정'}:
            raise ValueError(f'단계 검수 미완료: {chunk_id}')
        for old_stage in {e[1] for e in entries}:
            selected = [e for e in entries if e[1] == old_stage]
            stages = {e[2] if verdict == '단계 수정' else e[1] for e in selected}
            if len(stages) != 1:
                raise ValueError('동일 검수 단위 내 단계가 모호함')
            snapshot['actions'].append({
                'chunk_id': chunk_id, 'action_ids': [e[0] for e in selected],
                'old_stage': old_stage, 'stage': stages.pop(), 'verdict': verdict,
                'note': field(block, '메모')})
    if len(snapshot['tables']) != 64 or len(snapshot['citations']) != 11:
        raise ValueError('검수 항목 수 불일치')
    with args.out.open('x', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('compiled: 64 tables, 11 citations,', sum(len(a['action_ids']) for a in snapshot['actions']),
          'action decisions; original sheets unchanged')


if __name__ == '__main__':
    main()
