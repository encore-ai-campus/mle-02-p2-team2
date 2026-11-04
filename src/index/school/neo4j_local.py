"""school 임시 Neo4j 접속(공통 neo4j_client.py가 병합되면 그 모듈로 교체).

자격 증명은 .env의 NEO4J_URI·NEO4J_USERNAME·NEO4J_PASSWORD·NEO4J_DATABASE로만 받는다.
접속 문자열·비밀번호를 출력하거나 로그에 남기지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_driver(env_path: str | Path | None = None):
    """(driver, database) 반환. 사내망 인증서 문제를 피하려고 OS 인증서 저장소(truststore)를 쓴다."""
    import truststore
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    truststore.inject_into_ssl()
    if env_path:
        load_dotenv(env_path)
    missing = [k for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not os.getenv(k)]
    if missing:
        raise RuntimeError(f".env에 없는 Neo4j 설정: {missing}")
    # Cypher 25의 'queryNodes → SEARCH' 폐기 예고와 아직 없는 관계 이름(FOR_DISEASE 등) 경고는 끈다.
    # 이 DB의 기본 Cypher 5에서는 queryNodes가 정상 문법이다. 질의 오타는 테스트·키 대조로 잡는다.
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
                                  notifications_disabled_classifications=["DEPRECATION", "UNRECOGNIZED"])
    driver.verify_connectivity()
    return driver, os.getenv("NEO4J_DATABASE") or None
