# 호흡기 골든 검색 결과 — 2026-11-06

대상: [DiseaseSearchTool](../../src/rag/respiratory/disease_tool.py), 동일 질문 7개, 기본 `top_k=8`, 실DB 읽기 전용 조회. 정답은 [호흡기 골든셋](../../data/specs/golden/respiratory.md)의 chunk_id로 비교했다. 수정 전은 이번 변경 직전 저장된 ToolResult, 수정 후는 요청한 `--save` 재실행 결과다. 순위는 citations 배열에서 1부터 세며 `없음`은 반환된 인용에 없다는 뜻이다. 저장 시점과 검색 코드가 다른 관찰값이며 통제된 성능 평가가 아니다.

| 질문ID | 정답 chunk_id | 수정 전 순위 | 수정 후 순위 | 인용 수 | status |
|---|---|---|---|---:|---|
| [R-F1](../../tests/fixtures/respiratory/R-F1_tool_result.json) | `kdca_flu_facility_2026:p0006:b01` | 1위 | 1위 | 8 | OK |
| [R-F2](../../tests/fixtures/respiratory/R-F2_tool_result.json) | `kdca_respiratory_2026:p0109:b01` | 없음 | 5위 | 8 | OK |
| [R-F3](../../tests/fixtures/respiratory/R-F3_tool_result.json) | `kdca_covid19_2026:p0039:b01` | 1위 | 1위 | 7 | OK |
| [R-F4](../../tests/fixtures/respiratory/R-F4_tool_result.json) | `kdca_covid19_2026:p0028:b01` | 없음 | 5위 | 8 | OK |
| [R-Q1](../../tests/fixtures/respiratory/R-Q1_tool_result.json) | `kdca_flu_facility_2026:p0015:b01` | 없음 | 2위 | 8 | OK |
| [R-Q4](../../tests/fixtures/respiratory/R-Q4_tool_result.json) | `kdca_respiratory_2026:p0116:b01` | 2위 | 2위 | 8 | OK |
| [R-T1](../../tests/fixtures/respiratory/R-T1_tool_result.json) | `kdca_flu_facility_2026:p0013:b01` | 없음 | 없음 | 8 | OK |

- pytest: `uv run pytest tests/rag/respiratory -q -p no:cacheprovider` — **34 passed in 0.26s**. `uv run ruff check --fix src/rag/respiratory tests/rag/respiratory notebooks/respiratory` — All checks passed.
- R-F2: 직접 후보에 포함됐지만 결합 순위 37위, 필터 후 5위로 앞에 다른 청크 4개가 있다.
- R-F4: 기존 RRF 22위인 청크가 직접 후보에도 포함돼 필터 후 5위가 됐으나 앞에 다른 청크 4개가 있다.
- R-T1: 직접 후보에 포함됐지만 결합 순위 33위, 필터 후 29위여서 최종 8개 제한 밖이다.

후보 원인 확인은 재실행 시점의 동일 검색 조건에서 최종 제한을 제거해 별도로 조회했다. 직접 후보는 질병명·핵심어의 존재만 확인하므로 순위나 문장 의미의 정답성을 보장하지 않는다. `OK`는 검색 도구 status이며 정답성 판정이 아니다.
