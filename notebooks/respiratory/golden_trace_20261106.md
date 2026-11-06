# 호흡기 골든 청크 검색 단계 추적 — 2026-11-06

수정 전 코드와 실DB를 읽기 전용으로 조회했다. [golden_trace.py](golden_trace.py)는 CLI와 같은 전체 허용 문서, 기본 `top_k=8`, FULLTEXT·VECTOR 각 상위 64개 및 RRF 결합 상위 32개를 사용한다. 순위는 1부터 시작한다. `c`는 32개 후보에 필터를 적용하되 최종 8개 제한 없이 얻은 순위, `d`는 실제 8개 제한 후 순위다. 후보에서 빠진 경우 후속 단계는 미진입으로 표시했다. 아래 결과는 실행 시점의 검색 순위다.

| 질문ID | 정답 chunk_id | 빠진 단계(a~d) | 이유 | 고쳤나 |
|---|---|---|---|---|
| R-F2 | `kdca_respiratory_2026:p0109:b01` | a 이후 결합에서 제외; c·d 미진입 | a FULLTEXT 64개에 없음, b VECTOR 61위. 두 목록 RRF 결합 시 83위여서 상위 32개 제한에 걸림. 원본 청크 직접 확인 시 질병명 `성홍열`과 핵심어 `잠복기`는 머리말 처리 후에도 인식되며 ABOUT 관계는 없음. 따라서 질병명 필터에서 탈락했다는 가설은 맞지 않음. | 검색 코드 수정 없음 |
| R-F4 | `kdca_covid19_2026:p0028:b01` | d 최종 top_k | a FULLTEXT 33위, b VECTOR 44위, RRF 22위. c 필터 통과 후 10위이나 d에서 앞선 8개로 제한됨. | 아니요 |
| R-Q1 | `kdca_flu_facility_2026:p0015:b01` | c 거르기 | a FULLTEXT 9위, b VECTOR 5위, RRF 2위. 질병명은 인식되나 핵심어 `진단받`, `학생`, `언제까지`, `등교하면`, `되나` 중 어느 것도 처리된 청크에 없어 핵심어 필터에서 제외됨. d 미진입. | 아니요 |
| R-Q3 | `kdca_respiratory_2026:p0118:b01` | a·b 후보에서 제외 | a FULLTEXT와 b VECTOR의 각 상위 64개 모두 없음; RRF·c·d 미진입. 별도 청크 점검에서는 공통 지침 머리말을 가린 뒤 성홍열 이름이 남지 않고 ABOUT 관계도 없어, 후보에 포함되더라도 현재 질병 필터를 통과하지 못함. | 아니요 |
| R-T1 | `kdca_flu_facility_2026:p0013:b01` | a 이후 결합에서 제외; c·d 미진입 | a FULLTEXT 33위, b VECTOR 64개에 없음. RRF 결합 66위로 상위 32개 제한에 걸림. 별도 청크 점검에서는 핵심어 `유행하면`, `학교`, `휴교해야`, `하나`가 없어 후보에 포함되더라도 핵심어 필터를 통과하지 못함. | 아니요 |

- R-F2 회귀 테스트: 실제 각론 머리말과 잠복기 형태를 가진 청크를 검색 후보로 직접 주고 ABOUT 관계 없이 질병명·인용이 인정되는지 검증했다. 검색 후보 생성까지 검증하는 테스트는 아니다.
- Ruff: `uv run ruff check --fix src/rag/respiratory tests/rag/respiratory notebooks/respiratory` — All checks passed.
- pytest: `uv run pytest tests/rag/respiratory -q -p no:cacheprovider` — **32 passed in 0.23s**.
- R-F2 재실행: `NO_EVIDENCE`, 정답 청크 순위 없음, 인용 0개. [결과](../../tests/fixtures/respiratory/R-F2_tool_result.json)
- R-F1 재실행: `OK`, 정답 `kdca_flu_facility_2026:p0006:b01` 1위, 인용 4개. [결과](../../tests/fixtures/respiratory/R-F1_tool_result.json)
