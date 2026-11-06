"""Existing DB read-only smoke checks. Does not load the new candidate chunks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, 'src')
from rag.school.graph import default_deps
from rag.school.main_graph import run_main


def main():
    deps = default_deps()
    deps['embedder'] = None  # fulltext; no paid embedding or LLM call
    allowed = ['moe_school_manual_v3', 'gyeonggi_school_guide_2025']
    report = {'data': 'existing_db_not_v2_candidate', 'mode': 'fulltext', 'db_writes': 0, 'cases': []}
    try:
        for question in ['유행성각결막염 걸렸을 때도 전파차단 위한 휴업 해야하나요?',
                         '유행성이하선염의 휴업 기준은?',
                         '학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요?']:
            deps['trace'] = []
            out = run_main({'question': question, 'region': 'GYEONGGI',
                            'allowed_document_ids': allowed}, deps)
            assert out['tools_used'] == ['action_search']
            assert out['status'] in {'ANSWERED', 'PARTIAL'}
            assert out['citations']
            assert all(c['document_id'] in allowed for c in out['citations'])
            checks = [t['db_report'] for t in deps['trace'] if 'db_report' in t]
            assert checks and all(not any(r.values()) for r in checks)
            report['cases'].append({'question': question, 'output': out, 'db_checks': checks})
            print(out['status'], len(out['citations']), question, flush=True)
        out = run_main({'question': '감염병 발생 시 학교가 할 일', 'region': 'GYEONGGI',
                        'allowed_document_ids': []}, deps)
        assert not out['citations']
        report['empty_allowed_status'] = out['status']
    finally:
        deps['driver'].close()
        Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
