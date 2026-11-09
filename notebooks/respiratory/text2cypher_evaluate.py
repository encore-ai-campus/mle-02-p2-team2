"""Text2Cypher 8문항·기존 3질문을 읽기 전용으로 평가한다. DB 변경 없음."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter

import text2cypher_demo as demo

FLU = "kdca_flu_facility_2026"
SCARLET = "kdca_respiratory_2026"
CASES = [
    {"id": "E01", "seen": True, "kind": "graph", "question": demo.EXAMPLES[0][0],
     "reference": demo.EXAMPLES[0][1]},
    {"id": "E02", "seen": True, "kind": "graph", "question": demo.EXAMPLES[1][0],
     "reference": demo.EXAMPLES[1][1]},
    {"id": "N01", "seen": False, "kind": "graph",
     "question": "호흡기감염병 관리지침에서 등교중지 실시·통보 개념의 행동과 근거를 그래프로 보여줘",
     "reference": (
         f"MATCH path=(doc:Document {{document_id:'{SCARLET}'}})-[:HAS_CHUNK]->"
         "(chunk:Chunk)<-[:SUPPORTED_BY]-(action:SchoolAction)-[:INSTANCE_OF]->"
         "(:ActionConcept {concept_id:'EXCLUDE_FROM_SCHOOL'}) "
         "RETURN path ORDER BY action.action_id LIMIT 50")},
    {"id": "N02", "seen": False, "kind": "graph",
     "question": "시설별 인플루엔자 지침에서 독감에 ABOUT으로 연결된 원문과 FOR_DISEASE로 연결된 행동을 그래프로 보여줘",
     "reference": (
         "MATCH (disease:Disease {disease_id:'INFLUENZA'}) CALL { WITH disease "
         f"MATCH path=(doc:Document {{document_id:'{FLU}'}})-[:HAS_CHUNK]->(chunk:Chunk)-[:ABOUT]->(disease) "
         "RETURN path UNION WITH disease "
         f"MATCH path=(doc:Document {{document_id:'{FLU}'}})-[:HAS_CHUNK]->(chunk:Chunk)<-[:SUPPORTED_BY]-"
         "(action:SchoolAction)-[:FOR_DISEASE]->(disease) RETURN path } RETURN path LIMIT 50")},
    {"id": "N03", "seen": False, "kind": "graph",
     "question": "시설별 인플루엔자 지침의 문서와 원문 청크만 그래프로 보여줘. 학교 행동 연결 유무와 무관하게 모든 원문 청크를 포함해줘",
     "reference": (
         f"MATCH path=(doc:Document {{document_id:'{FLU}'}})-[:HAS_CHUNK]->(chunk:Chunk) "
         "RETURN path ORDER BY chunk.chunk_id LIMIT 50")},
    {"id": "N04", "seen": False, "kind": "table",
     "question": "문서별로 원문 근거에 연결된 학교 행동 수를 세어줘. 같은 행동은 한 번만 세고 행동이 없는 문서는 0으로 보여줘",
     "reference": (
         "MATCH (doc:Document) OPTIONAL MATCH (doc)-[:HAS_CHUNK]->(chunk:Chunk)<-[:SUPPORTED_BY]-(action:SchoolAction) "
         "RETURN doc.document_id AS document_id,count(DISTINCT action) AS action_count ORDER BY document_id LIMIT 50")},
    {"id": "N05", "seen": False, "kind": "unsupported",
     "question": "달토끼열이라는 질병에 연결된 학교 행동을 그래프로 보여줘"},
    {"id": "N06", "seen": False, "kind": "write_rejection",
     "question": "성홍열 관련 학교 행동의 action_text를 '수정 완료'로 바꿔서 DB에 저장해 줘"},
]


def counts(driver, database):
    return {"nodes": demo._metadata_rows(driver, database, "MATCH (n) RETURN count(n) AS n")[0]["n"],
            "relationships": demo._metadata_rows(driver, database, "MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]}


def table_signature(rows, schema):
    ids = {row["id"] for row in schema["dictionaries"]["Document"]}
    return sorted((next((v for v in row.values() if isinstance(v, str) and v in ids), None),
                   tuple(v for v in row.values() if type(v) is int)) for row in rows)


def run_case(case, driver, database, schema, llm):
    started = perf_counter()
    result = {
        "case_id": case["id"], "run_at_kst": demo.now(), "seen_example": case["seen"],
        "question": case["question"], "kind": case["kind"], "schema_version": schema["schema_version"],
        "schema_sha256": hashlib.sha256(demo.build_schema_context(schema).encode()).hexdigest(),
        "model": llm.model_name, "libraries": demo.versions(), "status": "GENERATION_ERROR",
        "generated_cypher": "", "executed_cypher": None, "row_count": 0,
        "validation_status": None, "comparison_pass": False, "raw_graph_objects_preserved": False,
        "reference_review": "기존 시각화 질의·실제 스키마로 작성한 기준 쿼리; 별도 사람 판독은 미실시",
    }
    try:
        generated = demo.generate_cypher(case["question"], schema, llm)
        result.update(generated)
        validation = demo.validate_cypher(driver, database, generated["generated_cypher"], schema)
        result.update(validation_status=validation["status"], validation=validation,
                      status=validation["status"])
        if case["kind"] in {"unsupported", "write_rejection"}:
            result["comparison_pass"] = validation["status"] in {"UNSUPPORTED", "REJECTED"}
            result["execution_status"] = "NOT_EXECUTED"
            return result
        if validation["status"] != "VALID":
            result["execution_status"] = "NOT_EXECUTED"
            return result
        executed = demo.execute_cypher(driver, database, validation, schema)
        payload = demo.serialize_records(executed["records"])
        result.update(executed_cypher=executed["executed_cypher"], row_count=len(executed["records"]),
                      status="OK" if executed["records"] else "NO_RESULT", execution_status="OK",
                      execution_seconds=executed["execution_seconds"], truncated=executed["truncated"],
                      possibly_truncated=executed["possibly_truncated"], payload=payload,
                      browser_query=demo.prepare_browser_query(validation),
                      raw_graph_objects_preserved=bool(payload["graph"]["nodes"]))
        reference = demo.validate_cypher(driver, database, case["reference"], schema)
        if reference["status"] != "VALID":
            result.update(reference_validation=reference, status="REFERENCE_ERROR")
            return result
        expected = demo.execute_cypher(driver, database, reference, schema)
        expected_payload = demo.serialize_records(expected["records"])
        result.update(reference_cypher=case["reference"], reference_rows=len(expected["records"]),
                      reference_truncated=expected["possibly_truncated"])
        if case["kind"] == "graph":
            actual_ids, expected_ids = demo.identity_sets(payload), demo.identity_sets(expected_payload)
            result["comparison_pass"] = actual_ids == expected_ids and not payload["graph"]["truncated"]
            result["comparison"] = {"actual": actual_ids, "expected": expected_ids}
        else:
            actual_values = table_signature(payload["rows"], schema)
            expected_values = table_signature(expected_payload["rows"], schema)
            result["comparison_pass"] = actual_values == expected_values
            result["comparison"] = {"actual": actual_values, "expected": expected_values}
    except Exception as error:  # noqa: BLE001 -- 비밀을 포함할 수 있는 예외 원문은 저장하지 않음
        code = str(getattr(error, "code", ""))
        result.update(status="TIMEOUT" if "TimedOut" in code or "Timeout" in type(error).__name__ else "ERROR",
                      error=demo.safe_error(error))
    finally:
        result["total_seconds"] = perf_counter() - started
    return result


def legacy_comparison(driver, database, schema):
    results = []
    for number in (1, 2, 3):
        path = demo.PROJECT_ROOT / f"tests/fixtures/respiratory/T2C0{number}_t2c_result.json"
        old = json.loads(path.read_text(encoding="utf-8"))
        validation = demo.validate_cypher(driver, database, old["cypher"], schema)
        row = {"case_id": f"LEGACY0{number}", "run_at_kst": demo.now(),
               "kind": "previous_fixed_query_comparison", "generated_now": False,
               "cypher": old["cypher"], "validation_status": validation["status"]}
        if validation["status"] == "VALID":
            output = demo.execute_cypher(driver, database, validation, schema)
            rows = [record.data() for record in output["records"]]
            def normalize(values):
                return sorted(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in values)
            row.update(row_count=len(rows), comparison_pass=normalize(rows) == normalize(old["rows"]))
        results.append(row)
    return results


def main():
    driver = None
    try:
        driver, database, connection = demo.connect_project()
        schema = demo.collect_schema(driver, database, connection)
        schema["account"]["user_direction"] = "11/9 사용자 요청: 기존 계정으로 프로그램에서 읽기만 수행"
        demo.SCHEMA_PATH.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        before = counts(driver, database)
        llm = demo.make_llm()
        results = []
        output = demo.DIRECTORY / "text2cypher_eval.jsonl"
        with output.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run_case, case, driver, database, schema, llm) for case in CASES]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                print(json.dumps({k: result.get(k) for k in ("case_id", "status", "validation_status", "row_count", "comparison_pass", "total_seconds")}, ensure_ascii=False), flush=True)
            legacy = legacy_comparison(driver, database, schema)
            for result in legacy:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        after = counts(driver, database)
        summary = {"evaluated_at_kst": demo.now(), "generated_questions": len(results),
                   "seen": {str(seen): {"questions": sum(r["seen_example"] == seen for r in results),
                                        "passes": sum(r["seen_example"] == seen and r["comparison_pass"] for r in results)}
                            for seen in (True, False)},
                   "status_counts": dict(Counter(r["status"] for r in results)),
                   "retrieval_result_comparison": {"questions": sum(r["kind"] in {"graph", "table"} for r in results),
                                                   "passes": sum(r["kind"] in {"graph", "table"} and r["comparison_pass"] for r in results)},
                   "policy_checks": {"questions": 2, "passes": sum(r["kind"] in {"unsupported", "write_rejection"} and r["comparison_pass"] for r in results)},
                   "legacy": legacy, "db_counts_before": before, "db_counts_after": after,
                   "db_count_changes": {k: after[k] - before[k] for k in before},
                   "browser_verified": False, "readonly_account_verified": schema["account"]["readonly_account_verified"],
                   "limitations": ["소수 문항의 단회 실행으로 일반 성능을 보장하지 않음", "Chrome 제어 미승인으로 Aura 화면 대조 미확인",
                                   "기존 계정 사용. DB 계정 자체의 읽기 전용 권한은 보장하지 않음", "기준 쿼리는 에이전트 확인이며 별도 사람 판독은 미실시"]}
        (demo.DIRECTORY / "text2cypher_eval_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print('SUMMARY', json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as error:  # noqa: BLE001 -- 접속·키 보호
        print(json.dumps({"status": "ERROR", "error": demo.safe_error(error)}))
        raise SystemExit(1) from None
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    main()
