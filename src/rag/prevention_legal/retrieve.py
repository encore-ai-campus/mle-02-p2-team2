"""전문 검색 리트리버 (유종혁 / prevention_legal). 수업 38-1 의 chunk_text 전문 인덱스를 쓴다.

이 파일의 역할
- 질문 단어로 Chunk 본문을 찾는다 (질문에 따라 순위가 바뀐다).
- 테스트 적재: PLTest 라벨과 test_ 접두사를 붙인 Chunk 만 넣고, 끝나면 같은 조건으로 지운다.
- 공동 DB 의 다른 팀원 노드는 읽기만 하고 수정하지 않는다.

주의
- 검색 결과가 0개인 것과 DB 연결 실패는 다르다. 연결 실패는 예외로 올라가게 둔다.
- 원문 대조는 fetch_chunks 로 공동 DB 청크 텍스트를 읽는다. 로컬 blocks.jsonl 은 테스트 노드를 만드는 개발용 함수(load_test_blocks)에서만 읽는다.
"""
import json
import re
import threading
from pathlib import Path

from neo4j.exceptions import ServiceUnavailable, SessionExpired

from rag.prevention_legal.neo4j_conn import connect

# 읽기 전용 조회(검색·원문 대조)가 함께 쓰는 드라이버 (2026-11-08, 주승우 팀장 승인 하에 Claude 수정).
# 예전에는 조회 함수마다 connect() 로 새 드라이버를 만들고 연결 확인까지 해서 Tool 호출 1회에 약 3초가 들었다
# (측정: 6문항 중앙값 3.43초 → 재사용 0.39초, 결과 상태 동일). neo4j 드라이버는 스레드 안전하고 끊긴 연결은 스스로 다시 맺는다.
# 테스트 노드 적재·삭제(load_test_blocks·delete_test_blocks)와 load_graph.py 의 쓰기는 지금처럼 connect() 를 쓴다.
_READ_DRIVER = None
_READ_LOCK = threading.Lock()


def _read_driver():
    """읽기 조회용 공용 드라이버. 처음 부를 때 한 번 만든다."""
    global _READ_DRIVER
    if _READ_DRIVER is None:
        with _READ_LOCK:
            if _READ_DRIVER is None:
                _READ_DRIVER = connect()
    return _READ_DRIVER


def _read_rows(query: str, **params) -> list[dict]:
    """공용 드라이버로 읽기 쿼리를 실행한다. 연결이 끊긴 경우에만 드라이버를 버리고 한 번 다시 연결해 재시도한다.
    쿼리 오류 등 다른 예외는 그대로 올린다(예전처럼 run 의 ERROR 처리로 간다)."""
    global _READ_DRIVER
    try:
        with _read_driver().session() as s:
            return s.run(query, **params).data()
    except (ServiceUnavailable, SessionExpired):
        with _READ_LOCK:
            broken, _READ_DRIVER = _READ_DRIVER, None
        if broken is not None:
            try:
                broken.close()
            except Exception:  # noqa: BLE001, S110 - 닫기 실패는 재시도를 막지 않는다
                pass
        with _read_driver().session() as s:
            return s.run(query, **params).data()


TEST_LABEL = "PLTest"   # 테스트 노드 표시 라벨
TEST_PREFIX = "test_"   # 테스트 chunk_id 접두사 (운영 ID 와 섞이지 않게 한다)
MAX_TERMS = 30          # 질문 단어 수 상한 (너무 긴 질문 방지)

# 단어 끝에서 떼는 조사·어미 목록 (긴 것부터 맞춘다). 형태소 분석기는 쓰지 않는다.
ENDINGS = ["으로", "에서", "에게", "하나요", "되나요", "인가요", "나요", "까요",
           "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도"]

BLOCKS_FILE = Path(__file__).resolve().parents[3] / "data" / "interim" / "prevention_legal" / "blocks.jsonl"


def _strip_ending(word: str) -> str:
    """단어 끝의 조사·어미 하나를 뗀다. 남는 글이 두 글자 미만이면 그대로 둔다."""
    for end in ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 2:
            return word[: -len(end)]
    return word


def lucene_query(question: str) -> str:
    """질문에서 한글·영문·숫자 단어만 골라 조사를 떼고 OR 로 묶는다. 따옴표·백슬래시 같은 특수문자는 모두 버린다.

    설명: 단어마다 큰따옴표로 감싸므로 Lucene 연산자(AND, NOT, *, ~ 등)가 해석되지 않는다.
    단어가 하나도 없으면 빈 문자열을 돌려주고, 호출한 쪽이 검색을 건너뛴다.
    """
    words = re.findall(r"[0-9A-Za-z가-힣]+", question)[:MAX_TERMS]
    terms = [_strip_ending(w) for w in words]
    return " OR ".join(f'"{t}"' for t in terms)


def load_test_blocks(document_ids: set[str]) -> list[str]:
    """블록 파일 중 허용 문서의 블록을 테스트 노드로 만든다. 만든 chunk_id 목록을 돌려준다 (삭제할 때 쓴다).

    설명
    - CREATE 를 쓴다. chunk_id 는 유일 제약이 있으므로 같은 ID 가 이미 있으면 DB 가 오류를 낸다.
      MERGE 는 기존 노드를 고칠 수 있어서 쓰지 않는다. 그래서 별도의 사전 확인 쿼리도 없다.
    - 오류가 나면 아무것도 돌려주지 않는다. 노트북은 반환값이 없으면 삭제할 것이 없다고 본다.
    """
    rows = []
    for line in BLOCKS_FILE.read_text(encoding="utf-8").splitlines():
        b = json.loads(line)
        if b["document_id"] in document_ids:
            rows.append({
                "chunk_id": TEST_PREFIX + b["chunk_id"],
                "document_id": b["document_id"],
                "text": b["text"],
                "pdf_page": b["pdf_page"],
            })

    driver = connect()
    try:
        with driver.session() as s:
            # consume() 로 결과를 끝까지 받아야 DB 오류(중복 ID 등)가 여기서 드러난다. 없으면 나중에 숨겨진다.
            s.run(
                f"""UNWIND $rows AS row
                    CREATE (c:Chunk:{TEST_LABEL} {{chunk_id: row.chunk_id, document_id: row.document_id,
                                                  text: row.text, pdf_page: row.pdf_page}})""",
                rows=rows,
            ).consume()
    finally:
        driver.close()
    return [r["chunk_id"] for r in rows]


def delete_test_blocks(chunk_ids: list[str]) -> int:
    """이번 실행에서 만든 테스트 노드만 지운다. PLTest 라벨과 test_ 접두사, 목록의 ID를 모두 만족해야 한다.

    설명: 목록이 비어 있으면 아무것도 지우지 않는다. 지운 개수를 돌려준다.
    """
    if not chunk_ids:
        return 0
    driver = connect()
    try:
        with driver.session() as s:
            match = (f"MATCH (c:Chunk:{TEST_LABEL}) WHERE c.chunk_id IN $ids "
                     f"AND c.chunk_id STARTS WITH $prefix")
            # 삭제 결과의 요약에서 실제 지운 노드 수를 읽는다 (세기 쿼리와 따로 맞추지 않는다)
            summary = s.run(f"{match} DETACH DELETE c", ids=chunk_ids, prefix=TEST_PREFIX).consume()
            n = summary.counters.nodes_deleted
    finally:
        driver.close()
    return n


def section_candidates(question: str, allowed_document_ids: list[str], k: int = 20) -> list[str]:
    """질문 단어가 청크의 소제목(section)에 들어 있는 블록을 찾는다. 전문 인덱스는 본문만 보므로 따로 찾는다.

    설명
    - 소제목은 공동 DB 청크 속성 section 에 있다. 질문 단어 중 2글자 이상이 소제목에 들어 있으면 후보가 된다.
    - 많이 겹친 순서로 k개를 돌려준다. 카드의 필수어·질문어·제외어 조건은 그대로 적용된다.
    - 질문 단어가 없거나 허용 문서가 없으면 빈 목록을 돌려준다.
    """
    terms = [t for t in (_strip_ending(w) for w in re.findall(r"[0-9A-Za-z가-힣]+", question)) if len(t) >= 2]
    terms = terms[:MAX_TERMS]
    if not terms or not allowed_document_ids:
        return []
    rows = _read_rows("MATCH (c:Chunk) WHERE c.section IS NOT NULL "
                      "AND any(d IN $docs WHERE c.chunk_id STARTS WITH d + ':') "
                      "AND any(t IN $terms WHERE c.section CONTAINS t) "
                      "RETURN c.chunk_id AS chunk_id, c.section AS section LIMIT 500",
                      docs=allowed_document_ids, terms=terms)
    scored = sorted(((sum(t in r["section"] for t in terms), r["chunk_id"]) for r in rows), reverse=True)
    return [cid for _, cid in scored[:k]]


def fetch_chunks(chunk_ids: list[str]) -> dict[str, dict]:
    """운영 청크의 원문(text)과 쪽 번호(pdf_page)를 Neo4j 에서 읽는다. 로컬 파일을 읽지 않는다.

    설명: 원문 대조(Citation 발췌 확인)에 쓴다. 없는 ID 는 결과에 빠지므로 호출한 쪽이 확인한다.
    """
    if not chunk_ids:
        return {}
    rows = _read_rows("MATCH (c:Chunk) WHERE c.chunk_id IN $ids "
                      "RETURN c.chunk_id AS chunk_id, c.text AS text, c.pdf_page AS pdf_page",
                      ids=chunk_ids)
    return {r["chunk_id"]: r for r in rows}


def fulltext_search(question: str, allowed_document_ids: list[str], k: int = 10) -> list[dict]:
    """질문 단어로 전문 인덱스를 검색하고, 테스트 노드 중 허용 문서의 상위 k개를 점수 순으로 돌려준다.

    설명
    - 인덱스 결과에는 다른 팀원의 Chunk 도 함께 나온다. 그래서 LIMIT 을 먼저 걸지 않고 전부 받은 뒤 필터한다.
    - 돌려주는 chunk_id 는 test_ 를 뗀 원래 ID 다 (expected_chunk_id 와 맞춘다).
    - 검색어가 비면 DB 를 부르지 않고 빈 목록을 돌려준다.
    """
    q = lucene_query(question)
    if not q:
        return []

    rows = _read_rows(
        f"""CALL db.index.fulltext.queryNodes('chunk_text', $q) YIELD node, score
            WHERE any(d IN $docs WHERE node.chunk_id STARTS WITH d + ':'
                      OR node.chunk_id STARTS WITH '{TEST_PREFIX}' + d + ':')
            RETURN node.chunk_id AS chunk_id, node.text AS text, node.pdf_page AS pdf_page, score
            ORDER BY score DESC""",
        q=q, docs=allowed_document_ids,
    )

    # test_ 를 떼어 원래 블록 ID 로 되돌리고, 같은 블록이 두 번 나오면 점수가 높은 쪽 하나만 남긴다
    seen, out = set(), []
    for r in rows:
        cid = r["chunk_id"].removeprefix(TEST_PREFIX)
        if cid in seen:
            continue
        seen.add(cid)
        out.append({**r, "chunk_id": cid})
        if len(out) == k:
            break
    return out
