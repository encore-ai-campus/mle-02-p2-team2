"""M6 호흡기 적재 패키지 생성, DB 확인 및 선택적 적재·임베딩.

기본 실행: uv run python -m index.respiratory.load
  승인된 행동과 전체 청크로 패키지를 만들고 로컬 bundle.json에 저장한다.
  기본 실행에서는 Neo4j에 접속하지 않는다.

--check-db: 사전 노드 수, 제약조건과 인덱스를 읽기만 한다.
--apply: 안전 검사를 통과한 패키지를 승우 적재기로 적재하고 결과를 검증한다.
--embed: 안전 검사를 통과한 패키지의 청크만 승우 임베딩 모듈에 전달한다.
--apply와 --embed를 함께 지정하면 적재 후 임베딩한다.
"""

import argparse
import json
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from index.respiratory.extractor import read_jsonl
from index.school import embed
from index.school.bundle import build_load_bundle
from index.school.dictionaries import load_dictionaries
from index.school.ids_local import chunk_row_id, parse_id
from index.school.load import load_bundle, verify_loaded_bundle
from index.school.neo4j_local import get_driver

# 발행기관·판본 등은 사용자가 지정한 세 문서의 정보만 사용한다.
DOCUMENTS = [
    {
        "document_id": "kdca_respiratory_2026",
        "title": "2026년도 호흡기감염병 관리지침",
        "issuer": "질병관리청",
        "version": "2026",
        "region": "NATIONAL",
        "source_url": None,
    },
    {
        "document_id": "kdca_flu_facility_2026",
        "title": "2026 인플루엔자 관리지침(어린이집·유치원·학교용)",
        "issuer": "질병관리청",
        "version": "2026",
        "region": "NATIONAL",
        "source_url": None,
    },
    {
        "document_id": "kdca_covid19_2026",
        "title": "2026년도 코로나바이러스감염증-19 관리지침",
        "issuer": "질병관리청",
        "version": "2026",
        "region": "NATIONAL",
        "source_url": None,
    },
]
DOCUMENT_IDS = frozenset(document["document_id"] for document in DOCUMENTS)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INTERIM_DIR = PROJECT_ROOT / "data/interim/respiratory"
NORMALIZED_PATH = INTERIM_DIR / "steps_normalized.jsonl"
BLOCKS_PATH = INTERIM_DIR / "blocks.jsonl"
BUNDLE_PATH = INTERIM_DIR / "bundle.json"
EMBED_LEDGER_PATH = INTERIM_DIR / "embed_ledger.jsonl"
TABLE_ROWS_PATH = INTERIM_DIR / "table_rows.jsonl"
TABLE_REVIEW_PATH = PROJECT_ROOT / "notebooks/respiratory/table_rows_검수표_20261108.md"
CHUNK_FIELDS = ("chunk_id", "document_id", "text", "pdf_page", "printed_page", "section")
ACTION_FIELDS = (
    "action_id", "action_text", "scope", "stage", "step_order", "when_text",
    "timing_text", "modality",
)


class TableReviewParser(HTMLParser):
    """검수표의 HTML 셀을 읽는다. 파이프·줄바꿈·HTML 엔티티를 보존한다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def approved_table_chunks(existing_chunks):
    """확인이 정확히 OK인 ID만 원본 JSONL에서 가져와 청크로 변환한다."""
    parser = TableReviewParser()
    parser.feed(TABLE_REVIEW_PATH.read_text(encoding="utf-8"))
    headers = ["chunk_id", "pdf_page", "table_title", "header", "row_text", "확인"]
    if not parser.rows or parser.rows[0] != headers:
        raise ValueError("표 행 검수표의 열 형식이 다릅니다.")
    approved, seen = [], set()
    for row in parser.rows[1:]:
        if len(row) != len(headers) or not row[0]:
            raise ValueError("표 행 검수표의 행 형식이 다릅니다.")
        if row[0] in seen:
            raise ValueError(f"검수표 중복 ID: {row[0]}")
        seen.add(row[0])
        if row[-1] == "OK":
            approved.append(row[0])
    if not approved:
        raise ValueError("확인이 OK인 표 행이 없습니다.")

    candidates = {}
    for _, row in read_jsonl(TABLE_ROWS_PATH):
        cid = row.get("chunk_id")
        if not isinstance(cid, str) or cid in candidates:
            raise ValueError(f"표 행 JSONL의 누락·중복 ID: {cid}")
        candidates[cid] = row
    existing = {row["chunk_id"] for row in existing_chunks}
    chunks = []
    for cid in approved:
        if cid not in candidates:
            raise ValueError(f"OK 행이 JSONL에 없습니다: {cid}")
        if cid in existing:
            raise ValueError(f"기존 청크와 중복 ID: {cid}")
        row = candidates[cid]
        parsed = parse_id(cid)
        if (parsed["kind"] != "chunk_row" or row.get("document_id") not in DOCUMENT_IDS
                or type(row.get("pdf_page")) is not int
                or parsed["document_id"] != row["document_id"]
                or parsed["page"] != row["pdf_page"]
                or chunk_row_id(row["document_id"], row["pdf_page"],
                                parsed["table"], parsed["row"]) != cid):
            raise ValueError(f"표 행 ID·문서·PDF 쪽 불일치: {cid}")
        if ("printed_page" not in row or (row["printed_page"] is not None
                and (type(row["printed_page"]) is not int or row["printed_page"] < 1))):
            raise ValueError(f"표 행 인쇄 쪽 오류: {cid}")
        if any(not isinstance(row.get(key), str) for key in ("table_title", "header", "row_text")):
            raise ValueError(f"표 행 원문 필드 누락: {cid}")
        chunks.append({
            "chunk_id": cid, "document_id": row["document_id"],
            "text": "\n".join(row[key] for key in ("table_title", "header", "row_text")),
            "section": row["table_title"], "pdf_page": row["pdf_page"],
            "printed_page": row["printed_page"],
        })
    return chunks


def make_inputs(*, table_rows=False):
    """정규화된 행동과 원문 청크를 승우의 적재 패키지 입력으로 변환한다.

    다른 상태의 행동은 적재 후보에서 제외한다. 같은 청크를 여러 행동이
    참조할 때는 질병 ID를 합치되 중복 관계를 만들지 않는다.
    """
    actions = []
    chunk_diseases = {}
    for _, row in read_jsonl(NORMALIZED_PATH):
        review = row.get("review")
        if not isinstance(review, dict) or review.get("status") != "APPROVED":
            continue
        if review.get("issues") != []:
            raise ValueError(f"APPROVED이지만 검수 문제가 남은 행동: {row.get('action_id')}")
        supported = row.get("supported_by")
        if not isinstance(supported, list) or not supported or not isinstance(supported[0], str):
            raise ValueError(f"APPROVED 행동에 근거 청크가 없습니다: {row.get('action_id')}")
        chunk_id = supported[0]
        disease_ids = row.get("for_disease")
        roles = row.get("done_by")
        concept_id = row.get("instance_of")
        if not isinstance(disease_ids, list) or not isinstance(roles, list):
            raise TypeError(f"질병 또는 역할이 배열이 아닙니다: {row.get('action_id')}")
        if not isinstance(concept_id, str) or concept_id == "NEW_CONCEPT_CANDIDATE":
            raise ValueError(f"승인된 행동의 개념이 확정되지 않았습니다: {row.get('action_id')}")

        try:
            action = {field: row[field] for field in ACTION_FIELDS}
        except KeyError as error:
            raise ValueError(f"승인된 행동에 필드가 없습니다: {error.args[0]}") from error
        action.update({
            "chunk_id": chunk_id,
            "roles": roles,
            "disease_ids": disease_ids,
            "concept": {"status": "RESOLVED", "id": concept_id},
            "candidate_key": row["action_id"],
        })
        actions.append(action)
        # 청크 ABOUT은 승인 행동의 for_disease만 사용한다는 요청을 따른다.
        selected = chunk_diseases.setdefault(chunk_id, [])
        for disease_id in disease_ids:
            if disease_id not in selected:
                selected.append(disease_id)

    official_chunks = []
    for _, row in read_jsonl(BLOCKS_PATH):
        try:
            official_chunks.append({field: row[field] for field in CHUNK_FIELDS})
        except KeyError as error:
            raise ValueError(f"청크에 필드가 없습니다: {error.args[0]}") from error
    if table_rows:
        official_chunks.extend(approved_table_chunks(official_chunks))
    return actions, official_chunks, chunk_diseases


def make_bundle(*, table_rows=False):
    """학교 공통 함수와 사전으로 패키지를 만들고 허용 구조를 검사한다."""
    actions, chunks, chunk_diseases = make_inputs(table_rows=table_rows)
    books = load_dictionaries()
    return build_load_bundle(
        actions,
        chunks,
        DOCUMENTS,
        books,
        chunk_diseases=chunk_diseases,
    )


def apply_table_chunks(driver, chunks, database=None):
    """새 표 행과 HAS_CHUNK만 한 트랜잭션으로 추가하고 기존 속성은 건드리지 않는다."""
    ids = [chunk["chunk_id"] for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("추가 표 행 ID 중복")
    rows = [{"document_id": parse_id(chunk["chunk_id"])["document_id"], "props": chunk}
            for chunk in chunks]

    def create(tx):
        existing = tx.run(
            "MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN count(c) AS n", ids=ids,
        ).single()["n"]
        if existing:
            raise ValueError("추가 표 행 ID가 이미 DB에 있습니다.")
        count = tx.run(
            "UNWIND $rows AS row MATCH (d:Document {document_id: row.document_id}) "
            "CREATE (c:Chunk) SET c = row.props CREATE (d)-[:HAS_CHUNK]->(c) "
            "RETURN count(c) AS n", rows=rows,
        ).single()["n"]
        if count != len(chunks):
            raise ValueError("표 행 추가 건수 불일치: 문서 끝점을 확인하세요.")
        return {"Chunk": count, "HAS_CHUNK": count}

    with driver.session(database=database) as session:
        return session.execute_write(create)


def require_our_ids(bundle):
    """우리 세 문서와 그 문서에서 시작한 청크·행동 ID만 통과시킨다.

    --apply와 --embed 전에 호출한다. 사전 노드 ID는 문서 접두어 형식이
    아니므로 검사하지 않으며, 승우의 build_load_bundle이 사전을 검증한다.
    """
    nodes = bundle.get("nodes", {})
    for document in nodes.get("Document", []):
        if document.get("document_id") not in DOCUMENT_IDS:
            raise ValueError(f"안전 검사 실패: 허용되지 않은 document_id {document.get('document_id')!r}")
    for label, key in (("Chunk", "chunk_id"), ("SchoolAction", "action_id")):
        for node in nodes.get(label, []):
            value = node.get(key)
            if not isinstance(value, str) or not any(
                value.startswith(document_id + ":") for document_id in DOCUMENT_IDS
            ):
                raise ValueError(f"안전 검사 실패: 허용되지 않은 {key} {value!r}")

    # 노드뿐 아니라 관계 끝점도 검사해 다른 문서에 쓰는 것을 막는다.
    for edge in bundle.get("edges", []):
        for side in ("start", "end"):
            label = edge.get(f"{side}_label")
            value = edge.get(f"{side}_id")
            if label == "Document" and value not in DOCUMENT_IDS:
                raise ValueError(f"안전 검사 실패: 허용되지 않은 Document 관계 끝점 {value!r}")
            if label in {"Chunk", "SchoolAction"} and (
                not isinstance(value, str)
                or not any(value.startswith(document_id + ":") for document_id in DOCUMENT_IDS)
            ):
                raise ValueError(f"안전 검사 실패: 허용되지 않은 {label} 관계 끝점 {value!r}")


def print_bundle_counts(bundle):
    """패키지의 노드와 관계 개수를 라벨·종류별로 보여준다."""
    print("노드 수:")
    for label, rows in bundle["nodes"].items():
        print(f"  {label}: {len(rows)}")
    print("관계 수:")
    counts = Counter(edge["type"] for edge in bundle["edges"])
    for relation in ("HAS_CHUNK", "ABOUT", "SUPPORTED_BY", "FOR_DISEASE", "DONE_BY", "INSTANCE_OF"):
        print(f"  {relation}: {counts[relation]}")


def call_external(function, *args, **kwargs):
    """DB·임베딩 오류에 접속 정보가 섞이지 않도록 예외 종류만 남긴다."""
    try:
        return function(*args, **kwargs)
    except Exception as error:  # noqa: BLE001
        # 드라이버·외부 API 오류 원문에는 .env의 접속 값이 포함될 수 있다.
        raise RuntimeError(f"DB 또는 임베딩 오류: {type(error).__name__}") from None


def check_database(driver, database):
    """DB를 변경하지 않고 사전 노드 수, 제약조건과 인덱스만 조회한다."""
    for label in ("Disease", "ActionConcept", "SchoolRole"):
        # 라벨은 위 고정 목록에서만 가져오며 사용자 입력을 쿼리에 넣지 않는다.
        records, _, _ = driver.execute_query(
            f"MATCH (n:{label}) RETURN count(n) AS count",
            database_=database,
        )
        print(f"{label}: {records[0]['count']}")
    records, _, _ = driver.execute_query(
        "SHOW CONSTRAINTS YIELD name RETURN name ORDER BY name",
        database_=database,
    )
    print("제약조건:")
    for record in records:
        print(f"  {record['name']}")
    records, _, _ = driver.execute_query(
        "SHOW INDEXES YIELD name, type, state RETURN name, type, state ORDER BY name",
        database_=database,
    )
    print("인덱스(이름 / 종류 / 상태):")
    for record in records:
        print(f"  {record['name']} / {record['type']} / {record['state']}")


def print_verification(report):
    """적재 후 누락·추가 키와 속성 불일치를 항목별로 보여준다."""
    for key in ("missing_nodes", "extra_nodes", "property_mismatch", "missing_edges", "extra_edges"):
        print(f"{key}: {json.dumps(report.get(key, []), ensure_ascii=False)}")
    print(f"actual_counts: {json.dumps(report.get('actual_counts', {}), ensure_ascii=False)}")


def main():
    parser = argparse.ArgumentParser(description="호흡기 그래프 적재 패키지")
    parser.add_argument("--apply", action="store_true", help="안전 검사 후 공통 Neo4j에 적재")
    parser.add_argument("--check-db", action="store_true", help="DB 상태 읽기 전용 조회")
    parser.add_argument("--embed", action="store_true", help="호흡기 청크의 임베딩 생성·적재")
    parser.add_argument("--table-rows", action="store_true", help="검수표 OK 행만 청크로 추가")
    args = parser.parse_args()
    if args.check_db and (args.apply or args.embed):
        parser.error("--check-db는 읽기 전용이므로 --apply 또는 --embed와 함께 쓸 수 없습니다.")

    if args.check_db:
        driver, database = call_external(get_driver, PROJECT_ROOT / ".env")
        try:
            call_external(check_database, driver, database)
        finally:
            call_external(driver.close)
        return

    bundle = make_bundle(table_rows=args.table_rows)
    table_chunks = []
    if args.table_rows:
        original_ids = {row["chunk_id"] for row in make_bundle()["nodes"]["Chunk"]}
        table_chunks = [row for row in bundle["nodes"]["Chunk"]
                        if row["chunk_id"] not in original_ids]
        print(f"validate_bundle: 통과 / 추가 청크: {len(table_chunks)}")
    print_bundle_counts(bundle)
    if not args.apply and not args.embed:
        # 기본 모드는 DB에 접속하지 않고 요청한 패키지 파일만 저장한다.
        BUNDLE_PATH.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"패키지: {BUNDLE_PATH}")
        return

    # DB 접속과 어떤 쓰기보다 먼저 세 문서 ID 범위를 검사한다.
    # 범위 밖 ID가 하나라도 있으면 ValueError로 종료 코드 1을 반환한다.
    require_our_ids(bundle)
    driver, database = call_external(get_driver, PROJECT_ROOT / ".env")
    try:
        if args.apply:
            if args.table_rows:
                written = call_external(apply_table_chunks, driver, table_chunks, database)
            else:
                written = call_external(load_bundle, driver, bundle, database)
            print(f"적재 처리 건수: {json.dumps(written, ensure_ascii=False)}")
            print_verification(call_external(verify_loaded_bundle, driver, bundle, database))
        if args.embed:
            # 승우 모듈의 상수를 그대로 쓰며 패키지에 속한 우리 청크만 전달한다.
            # 장부는 같은 텍스트에 대한 임베딩을 중복 요청하지 않기 위해 사용한다.
            embedder = call_external(embed.make_embedder)
            result = call_external(
                embed.embed_chunks,
                driver,
                table_chunks if args.table_rows else bundle["nodes"]["Chunk"],
                embedder,
                ledger=EMBED_LEDGER_PATH,
                database=database,
            )
            print(f"임베딩 모델: {embed.EMBEDDING_MODEL}, 차원: {embed.EMBEDDING_DIM}")
            print(f"임베딩 생성: {len(result['embedded'])}건 / 기존 유지: {len(result['skipped'])}건")
    finally:
        call_external(driver.close)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        # DB·임베딩 오류는 call_external에서 종류만 남기므로 .env 값은 출력하지 않는다.
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)
