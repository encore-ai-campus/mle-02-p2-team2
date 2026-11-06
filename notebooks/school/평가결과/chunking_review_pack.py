"""검토 대상 JSON을 표 단위 주소·인용 이동·행동 단계 비교 문서로 변환한다.

원본 PDF 확인이나 승인, 골든셋/DB 수정은 수행하지 않는다.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, 'src')
from index.school.normalize import settle_stage
from preprocess.school.chunks import SOURCES


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, default=Path('notebooks/school/평가결과'))
    args = parser.parse_args()
    root = Path.cwd()
    chunks = read_jsonl(args.artifacts / 'official_chunks.jsonl')
    by_id = {c['chunk_id']: c for c in chunks}
    citations = json.loads((args.artifacts / 'citations.json').read_text(encoding='utf-8'))
    citations = [c for c in citations if not c['old_id_valid']]
    groups = defaultdict(list)
    for c in chunks:
        if c.get('context_verified') is False:
            groups[c['chunk_id'].rsplit('r', 1)[0]].append(c)
    source_lines = {}
    for doc, spec in SOURCES.items():
        path = root / spec['body']
        source_lines[doc] = (path, {int(m.group(1)): i for i, line in enumerate(
            path.read_text(encoding='utf-8-sig').splitlines(), 1)
            if (m := re.match(r'^## PDF (\d+)페이지', line))})

    def source_link(c):
        path, lines = source_lines[c['document_id']]
        return f"[본문 Markdown 열기](</{path.as_posix()}:{lines[c['pdf_page']]}>)"

    def doc_name(c):
        return '교육부 매뉴얼' if c['document_id'].startswith('moe') else '경기도 가이드북'

    tables = ['# 청킹 주소 검토표 — 표 단위', '',
              (f'미확정 {sum(map(len, groups.values()))}개 행을 **{len(groups)}개 표**로 묶었다. '
              '각 표에서 PDF 쪽수·소속 제목·표 제목을 한 번 확인한다. 전체 행별 정답을 작성하는 문서가 아니다.'), '',
              ('본문 Markdown 링크는 위치를 찾는 보조 자료다. 변환 순서가 뒤집힌 경우 최종 소속은 원본 PDF의 시각적 배치로 확인한다. '
              '이 문서의 현재 주소는 수정 후보이며 정답이 아니다.'), '',
              '| 검토 | 문서 | PDF / 인쇄 | 표 작업본 제목 | 행 수 |',
              '|---|---|---|---|---|']
    details = []
    for i, (table_id, rows) in enumerate(groups.items(), 1):
        c = rows[0]
        label = f'T{i:02d}'
        title = c['working_table_title'].replace('|', '\\|')
        tables.append(f"| [{label}](#{label.lower()}) | {doc_name(c)} | {c['pdf_page']} / {c['printed_page']} | {title} | {len(rows)} |")
        details += [f'<a id="{label.lower()}"></a>', f'## {label} — {doc_name(c)} PDF {c["pdf_page"]}쪽', '',
                    f'- 인쇄 쪽수: {c["printed_page"]}', f'- 표 ID: `{table_id}`',
                    f'- 작업본 제목: {c["working_table_title"]}',
                    '- 현재 주소: ' + c['section'], f'- 원문 위치: {source_link(c)}', '',
                    '**행 미리보기 — 표 식별용이며 전체 표를 대신하지 않음**', '', '```text',
                    '\n'.join(row['row_text'].strip() for row in rows[:2]), '```', '',
                    '- 판정: 미검토 (주소 맞음 / 주소 수정 / 원문 확인 불가)',
                    '- 확정 주소: ', '- 근거: PDF 쪽과 실제 상위·중간·하위 제목',
                    '- 메모: ', '']
    tables += ['', *details]
    cite_doc = ['# 골든 인용 ID 이동 검토표', '',
                (f'기존 ID에서 발췌가 사라진 인용 {len(citations)}건이다. 문서·PDF 쪽·발췌로 다시 찾은 후보를 표시한다. '
                '원래의 정답 의미를 바꾸지 않고 위치만 옮길 수 있는지 검토한다. 실제 골든 파일은 아직 수정하지 않았다.'), '',
                '| 인용 / 질문 | PDF | 이전 ID 끝부분 | 새 후보 ID 끝부분 | 기계 검사 |',
                '|---|---|---|---|---|']
    cite_details = []
    for c in citations:
        candidates = c['candidates']
        assert candidates and all(by_id[cid]['document_id'] == c['document_id']
                                  and by_id[cid]['pdf_page'] == c['pdf_page'] for cid in candidates)
        cite_doc.append(f"| {c['cid']} / {c['q']} | {c['pdf_page']} | {c['chunk_id'].split(':',1)[1]} | "
                        f"{', '.join(cid.split(':',1)[1] for cid in candidates)} | {'단일 후보' if len(candidates)==1 else '복수 후보'} |")
        cite_details += [f"## {c['cid']} — {c['q']} / {doc_name(c)} PDF {c['pdf_page']}쪽", '',
                         f"- 이전 ID: `{c['chunk_id']}`", f"- 발췌: {c['excerpt']}",
                         f'- 원문: {source_link(c)}', '']
        for cid in candidates:
            candidate = by_id[cid]
            cite_details += [f'- 새 후보: `{cid}`', '- 새 주소: ' + candidate['section'],
                             '', '```text', candidate['text'], '```', '']
        cite_details += ['- 판정: 미확정 (동일 근거로 이동 / 다른 근거라 보류)',
                         '- 메모: 조건·예외·수행자가 원래 정답과 같은지 확인', '']
    cite_doc += ['', *cite_details]

    actions = read_jsonl(root / 'data/interim/school/normalized_20261105T032224_612625Z/normalized.jsonl')
    stage_groups = defaultdict(list)
    for action in actions:
        candidate, _ = settle_stage('UNSPECIFIED', {'section': by_id[action['chunk_id']]['section']})
        if candidate != 'UNSPECIFIED' and candidate != action['stage']:
            stage_groups[action['chunk_id']].append((action, candidate))
    stage_count = sum(map(len, stage_groups.values()))
    stages = ['# 행동 단계 재검토 후보', '',
              (f'기존 행동 438개 중 **{stage_count}개 행동 / {len(stage_groups)}개 근거 청크**에서 '
              '저장 단계와 새 주소로부터 읽은 단계가 다르다. **새 주소가 언제나 행동의 정답 단계라는 뜻은 아니다.** '
              '표의 단계 열·조건에 따라 개별 행은 상위 절과 다른 단계를 명시할 수 있다. 자동 수정하지 않는다.'), '',
              '특히 기존 단계가 미지정이면 틀렸다고 단정하지 않는다. 원문에서 단계를 확정할 수 있을 때만 보완한다.', '']
    for cid, rows in stage_groups.items():
        c = by_id[cid]
        stages += [f'## `{cid}`', '', f'- 위치: {doc_name(c)} PDF {c["pdf_page"]} / 인쇄 {c["printed_page"]}',
                   '- 새 주소: ' + c['section'], f'- 원문: {source_link(c)}', '',
                   '```text', c['text'], '```', '', '| 행동 ID | 행동 | 저장 단계 | 주소 기반 후보 |',
                   '|---|---|---|---|']
        for a, proposed in rows:
            stages.append(f"| {a['action_id']} | {a['action_text'].replace('|', '/')} | {a['stage']} | {proposed} |")
        stages += ['', '- 판정: 미검토 (기존 유지 / 단계 수정 / 근거 부족 보류)',
                   '- 메모: 표의 단계 열·조건과 예시 문맥을 우선 확인', '']
    outputs = {'청킹_주소검토표_20261106.md': tables,
               '청킹_인용이동검토표_20261106.md': cite_doc,
               '청킹_행동단계검토표_20261106.md': stages}
    for name, lines in outputs.items():
        path = args.out_dir / name
        # Review notes must survive repeated executions.
        with path.open('x', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    print(json.dumps({'table_groups': len(groups), 'table_rows': sum(map(len, groups.values())),
                      'citation_rows': len(citations),
                      'citation_single_candidate': sum(len(c['candidates']) == 1 for c in citations),
                      'stage_action_candidates': stage_count, 'stage_source_groups': len(stage_groups)},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
