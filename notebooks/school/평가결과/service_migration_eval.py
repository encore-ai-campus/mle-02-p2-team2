"""Reuse existing raw-search/Tool/main evaluators in a fresh migration result folder.

The main evaluator is run once with all registered tools. No DB writes.
"""
import argparse
import json
import runpy
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('기존 평가 결과를 덮어쓰지 않음: 새 폴더 필요')
    here = Path(__file__).resolve().parent
    module = runpy.run_path(str(here / 'golden_eval_relevance.py'))
    evaluate = module['main']
    evaluate.__globals__['OUT'] = args.out.resolve()
    original_run = runpy.run_path

    def all_tools(path, *values, **kwargs):
        if Path(path).name == 'golden_eval_main.py' and '--all-tools' not in sys.argv:
            sys.argv.append('--all-tools')
        return original_run(path, *values, **kwargs)

    try:
        runpy.run_path = all_tools
        evaluate()
    finally:
        runpy.run_path = original_run
    path = args.out / 'metadata.json'
    meta = json.loads(path.read_text(encoding='utf-8'))
    meta['main_registry'] = ['action_search','disease_search','legal_search','crossdoc_graph_search']
    active = json.loads(Path('data/specs/school_active_dataset.json').read_text(encoding='utf-8'))
    meta['data_version'] = active['dataset_version'] + '_' + active['status']
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
