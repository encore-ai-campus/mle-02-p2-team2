"""Apply source-reviewed table contexts and action decisions with stale-data guards.

This is document metadata, not question-specific retrieval logic. Raw review
documents remain untouched. Unknown captions stay unknown rather than invented.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

REVIEW_FILE = 'data/specs/school_reviewed_contexts_20261106.json'


def rows_hash(rows: list[dict]) -> str:
    value = [(r['chunk_id'], r['row_text']) for r in sorted(rows, key=lambda r: r['chunk_id'])]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def load_review(root: str | Path) -> dict:
    path = Path(root) / REVIEW_FILE
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def apply_reviewed_tables(rows: list[dict], review: dict) -> None:
    groups = defaultdict(list)
    for row in rows:
        groups[row['chunk_id'].rsplit('r', 1)[0]].append(row)
    for record in review.get('tables', []):
        group = groups.get(record['table_id'])
        if group is None:
            continue  # other document / unselected page
        if rows_hash(group) != record['rows_sha256']:
            raise ValueError(f"검수 이후 표 행 원문 변경: {record['table_id']}")
        section = record['resolved_section']
        if not section or '?' in section:
            raise ValueError(f"미확정 주소를 확정값으로 적용할 수 없음: {record['review_id']}")
        for row in group:
            # Keep original row/header; only the title/context changes.
            lines = row['text'].splitlines(keepends=True)
            header = next(i for i, line in enumerate(lines) if line.strip().startswith('|'))
            title = record['resolved_title']
            row['text'] = ((title + '\n') if title else '') + ''.join(lines[header:]).rstrip('\n')
            row.update(section=section, table_title=title,
                       table_title_verified=record['title_verified'],
                       context_status='source_reviewed', context_verified=True,
                       context_review_id=record['review_id'],
                       caption_pending=record['caption_pending'])


def apply_reviewed_actions(actions: list[dict], review: dict) -> list[dict]:
    result = deepcopy(actions)
    by_id = {a['action_id']: a for a in result}
    for decision in review.get('actions', []):
        for action_id in decision['action_ids']:
            if action_id not in by_id:
                raise ValueError(f'검수 행동 없음: {action_id}')
            action = by_id[action_id]
            if action['chunk_id'] != decision['chunk_id'] or action['stage'] != decision['old_stage']:
                raise ValueError(f'검수 이후 행동 소속/단계 변경: {action_id}')
            action['stage'] = decision['stage']
    return result
