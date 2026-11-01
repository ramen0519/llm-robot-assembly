# 현재 진행 보고서 — 2026-11-01

실제 작업 위치: `C:\dev\llm-robot-assembly`  
작업 브랜치: `codex/simulation-progress`  
사용자 요청에 따라 단계별 보고서를 남기며 연속 개발했다. 이 보고서는 시뮬레이션만 다룬다.

## 바로 확인할 수 있는 결과

기둥을 실제 접촉으로 잡아 구멍에 넣고, 손을 뗀 뒤 결합을 검사한다. 일부러 정렬을 6 mm 틀리게 하면 실패를 감지하고, 현재 파지를 재확인한 뒤 후퇴·재정렬해 같은 episode에서 복구한다. 이 흐름을 LangGraph로 실행하고 과거 경험 ID를 판단 문맥에 전달한다.

현재 정책은 규칙 코드다. LLM API/파인튜닝/실물 명령은 실행하지 않았다.

```powershell
cd C:\dev\llm-robot-assembly
uv sync --locked
# LangGraph + 측정 실패 + 복구 + 기존 경험 검색
uv run --locked python scripts/run_assembly_graph.py --mode roles --scenario alignment_offset --memory --snapshots
# 같은 도구를 사용하는 단일 정책 모드
uv run --locked python scripts/run_assembly_graph.py --mode single --scenario alignment_offset
# 창에서 첫 물리 결합 보기
uv run --locked python scripts/run_insertion.py
# 3부품 순차 조립
uv run --locked python scripts/run_multi_post.py --posts 2 --snapshots
```

메모리가 비어 있으면 검색 결과 0개로 진행한다. 현재 규칙 정책은 검색 내용을 보고 선택을 바꾸지 않는다.

## 단계별 상태와 보고서

| 단계 | 현재 확인 범위 | 보고서 |
|---|---|---|
| 15 | 기존 물리 pick/place를 LangGraph에 연결 | [15](STEP_15_LANGGRAPH.md) |
| 16 | 저장된 실제 분해 성공 결과를 조립 후보로 변환 | [16](STEP_16_DISASSEMBLY_ADAPTER.md) |
| 17 | 10부품 후보 CAD와 첫 결합의 물리 장면 | [17](STEP_17_PRODUCT_SCENE.md) |
| 18 | 첫 기둥 물리 삽입·해제·후퇴와 독립 판정 | [18](STEP_18_PHYSICAL_INSERTION.md) |
| 19 | 정렬/깊이/접촉력 실패 검사, 기존 파지 검사 재사용 | [19](STEP_19_INSERTION_FAILURES.md) |
| 20 | 초기화 없는 오정렬 복구 1종, 최대 1회 | [20](STEP_20_ALIGNMENT_RECOVERY.md) |
| 21 준비 | 정책 계약·역할 인계·단일 비교 구조. LLM 미연결 | [21](STEP_21_POLICY_GRAPH_FOUNDATION.md) |
| 22a/b | 경험 저장·조건 일치 검색·정책 문맥 전달 | [22](STEP_22_EXPERIENCE_MEMORY.md) |
| 23a | 3부품 순차 조립, 주변 부품 유지 검사 | [23a](STEP_23A_MULTIPLE_POSTS.md) |

5부품의 기둥 4개, 6부품의 첫 슬리브를 확장 시험 중이다. 10부품 CAD가 있다는 것과 10부품 전체 로봇 조립 성공은 다르다.

## 재현 근거

- 첫 삽입 성공: `runs/step18/20261101T043418_152942Z_ad1f4446`. 최종 오차 약 0.127 mm, 깊이 약 16.108 mm.
- 의도적 오정렬 실패: `runs/step18/20261101T043637_317870Z_01380fe9`. XY 오차 약 6.075 mm, 하강 전 중단.
- 같은 상태에서 복구 성공: `runs/step18/20261101T043858_731712Z_846593f5`.
- 역할 인계 그래프 성공: `runs/step21/20261101T044217_632132Z_0f502680`.
- 경험 2개 검색/전달 + 그래프 성공: `runs/step21/20261101T044438_478143Z_c7edee85`.
- 3부품 순차 조립 성공: `runs/step23/20261101T044621_478409Z_0bef54c3`.

실험 기록·PNG·OBJ·로컬 메모리는 Git에서 제외한다. 메모리는 `C:\dev\llm-robot-assembly\runtime\memory\simulation`에 있다. 보고서와 코드/환경 잠금 파일은 단계별 로컬 커밋에 남겼으며 원격 push는 하지 않았다.

20단계까지 전체 회귀 검사 65개 PASS(189.596초). 이후 정책 그래프 4개, 경험 저장/검색 5개, 문맥 전달 1개, 다부품 장면 3개 및 기존 작업대 6개 재검사가 통과했다. 복합 부품 장면/선행 조건 검사 3개도 통과했지만 슬리브의 전체 물리 실행 결과와 구분한다.

## 아직 완료하지 않은 핵심

1. 실제 LLM API 정책: 제공자·모델·이번 작업 비용 한도 답변 대기. 다른 프로젝트의 키/예산 승인을 가져오지 않음.
2. 전역 모션플래닝: 현재 Mink IK + 지역 waypoint + 샘플 충돌 검사. MoveIt/OMPL 장애물 우회 경로는 아직 없음.
3. 10부품 전체 조립과 다부품 LangGraph/복구/경험 통합.
4. 새 절차 후보 생성과 별도 검증 후 스킬 등록(22c/d).
5. 이번 제품 CAD로 외부 분해 플래너를 실제 호출하고 좌표·단위를 연결하는 검증.
6. 동결된 시험셋에서 LLM 단일/다중, 모델, 스킬/메모리 유무 비교. 현재 기능 검사 결과를 성능 우위로 해석하지 않음.

실물은 현재 작업 범위에서 제외한다. 개발 단계는 일부가 병렬로 준비되므로 23a 코드가 있다는 사실만으로 21~22 전체를 완료 처리하지 않는다.
