"""Aura 연결 보조 모듈 (유종혁 / prevention_legal).

Windows 인증서 저장소를 Python 의 SSL 이 쓰도록 바꾼다. 인증서 검증은 그대로 둔다.
노트북에서 Neo4j 에 연결하기 전에 `use_windows_trust()` 를 한 번 호출한다.
"""
import os

from dotenv import find_dotenv, load_dotenv
from neo4j import GraphDatabase


def use_windows_trust() -> None:
    """Windows 인증서 저장소를 사용하도록 SSL 을 바꾼다 (검증은 유지)."""
    import truststore

    truststore.inject_into_ssl()


def connect():
    """.env 의 NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD 로 드라이버를 만든다. 값은 출력하지 않는다."""
    load_dotenv(find_dotenv(usecwd=True), override=True)
    uri, user, pw = os.getenv("NEO4J_URI"), os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")
    if not (uri and user and pw):
        raise RuntimeError(".env 에 NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD 가 모두 있어야 합니다.")
    driver = GraphDatabase.driver(uri, auth=(user, pw))
    try:
        driver.verify_connectivity()
    except Exception:
        driver.close()
        raise
    return driver
