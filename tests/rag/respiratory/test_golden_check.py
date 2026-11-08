import importlib.util
from pathlib import Path


def test_rq3_best_rank_accepts_any_direct_page(monkeypatch):
    path = Path(__file__).resolve().parents[3] / "notebooks/respiratory/golden_readonly_check.py"
    spec = importlib.util.spec_from_file_location("respiratory_golden_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    citations = [
        {"document_id": "kdca_respiratory_2026", "chunk_id": f"chunk-{page}",
         "pdf_page": page, "printed_page": page - 20}
        for page in (121, 115, 116)
    ]
    result = {"tool_name": "disease_search", "status": "OK", "citations": citations,
              "graph_paths": [], "warnings": []}
    monkeypatch.setattr(module, "disease_search", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(module, "validate_tool_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "verify_against_db", lambda *_args: {"bad_citations": {}, "bad_findings": {}})
    monkeypatch.setattr(module, "check_graph_paths", lambda *_args: {})
    monkeypatch.setattr(module, "in_scope", lambda *_args: True)
    expected = [("직접 근거", "kdca_respiratory_2026", page) for page in (116, 118, 121)]
    expected.append(("참고", "kdca_respiratory_2026", 115))

    case = module.run_case("R-Q3", "질문", expected, ["kdca_respiratory_2026"], {"driver": None}, {})

    assert case["best_direct_rank"] == 1
    assert [x["ranks"] for x in case["expected"]] == [[3], [], [1], [2]]
    result["citations"] = [citations[1]]
    case = module.run_case("R-Q3", "질문", expected, ["kdca_respiratory_2026"], {"driver": None}, {})
    assert case["best_direct_rank"] is None
