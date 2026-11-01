# 현재 진행 보고서 — 2026-11-01

작업 위치: `C:\dev\llm-robot-assembly` · 브랜치: `codex/simulation-progress`.
사용자 요청에 따라 시뮬레이션만 연속 개발하고, 단계별 한국어 보고서와 로컬 커밋을 남겼다.

## 지금 되는 것

**10부품 프레임을 고정 장애물·첫 정렬 실패가 있는 조건에서 끝까지 물리 조립했다.** 바닥판 1개는 고정하고 기둥 4개·슬리브 4개·덮개 1개를 로봇이 접촉·마찰로 집어 설치했다. 로봇 설치 9/9, 초기화 없는 정렬 복구 1회다. 막힌 직접 경로는 현재 상태를 다시 확인하고 검사된 대체 경로로 운반했다.

23G 당시 코드의 전체 실행은 sim **957.474초**, 물리 측정 **478,934행**, 부품별 최종 검사 구간 최대 위치 오차 중 가장 큰 값 **0.1994mm**다. 자세한 근거와 범위는 [23G 통합 보고서](STEP_23G_FULL_FRAME_DETOUR.md)에 있다. 고정 개발 장면의 성공이며 임의 제품·장애물 일반화 결과는 아니다.

후속 **21C에서는 첫 결합의 실행 전 경로 실패를 LangGraph 정책에 전달**했다. 재관측 후 `재계획` 또는 `중단`을 선택하고, 우회 후 정렬 실패가 있으면 별도 복구를 거쳐 물리 삽입을 완료한다. 운반 재계획과 정렬 복구는 각각 최대 1회다. 전체 10부품에 정책 재계획을 확장한 것은 아니며 해당 실행기의 자동 우회는 기존대로 유지한다.

**LangGraph의 single/roles는 규칙 정책이다. 단일 LLM API 연결부는 구현했지만 실제 모델 호출은 아직 검증하지 않았다.** CAD 구조·결합 정보·파지점·순서는 개발용으로 작성했다. 실제 API·실물·파인튜닝은 실행하지 않았다. LLM을 붙이는 것만으로 판단 효과가 입증되는 것은 아니다.

## 실행 명령

```powershell
cd C:\dev\llm-robot-assembly
uv sync --locked
# 작은 조립과 실패 복구를 화면에서 확인
uv run --locked python scripts/run_frame_assembly.py --parts 2 --mode single --scenario first_alignment_offset --viewer --snapshots
# 전체 10부품 + 고정 장애물 + 정렬 실패 복구
uv run --locked python scripts/run_frame_assembly.py --parts 10 --mode single --scenario first_alignment_offset --transfer-obstacle --transport-strategy direct_then_rrt --snapshots
# 구멍 있는 슬리브의 개별 운반·삽입 (다른 부품은 사전 준비)
uv run --locked python scripts/run_component_probe.py --component sleeve_2 --transfer-obstacle --transport-strategy direct_then_rrt --snapshots
# 첫 결합: 정책의 운반 재계획 선택 + 정렬 복구
uv run --locked python scripts/run_assembly_graph.py --mode single --transfer-obstacle --transport-replan-budget 1 --scenario alignment_offset --snapshots
```

전체 통합 실행의 실제 시간은 이번 PC에서 약 **29.0분**이었다. 다른 시험과 일부 CPU를 공유했으므로 속도 비교 수치로 쓰지 않는다. `run_frame_assembly.py`에 `--viewer`를 추가하면 같은 동작을 창에서 볼 수 있고 창을 닫으면 취소로 기록한다. 첫 결합 전용 `run_assembly_graph.py`는 `--snapshots`로 이미지를 저장한다. 기본 `direct`와 장애물 없는 이전 시연도 유지한다.

## 이번에 이어서 완료한 단위

| 단계 | 실제 확인 범위 | 보고서 |
|---|---|---|
| 23E | 같은 장면에서 네 기둥 설치 4/4, 기존 조립물 보존, 필요한 구간의 우회 | [23E](STEP_23E_FOUR_POST_DETOUR.md) |
| 23F | 슬리브·덮개 각각 실제 설치 1개, 복합 벽 충돌·파지 유지·준비물 집계 구분 | [23F](STEP_23F_COMPOUND_TRANSPORT.md) |
| 23G | 바닥판 외 사전 조립 없이 전체 9개 설치, 장애물 우회·정렬 복구·전체 상태 검사 | [23G](STEP_23G_FULL_FRAME_DETOUR.md) |
| 25B | 운반 전략 외의 기록된 조건·소스가 같은 실행만 비교. 슬리브와 전체 프레임의 직접 경로 실패/대체 경로 성공 | [25B](STEP_25B_TRANSPORT_COMPARISON.md) |
| 21C | 첫 결합의 실패 후 재관측·정책 재계획/중단 선택, 원래 목표 보존, 별도 정렬 복구와 물리 삽입 | [21C](STEP_21C_POLICY_TRANSPORT_REPLAN.md) |

## 전체 개발 기반

| 구성 | 현재 상태 | 보고서 |
|---|---|---|
| 모델·TCP·IK·기초 제어 | UR3e + 2F-85, Mink IK, 실제 관절 제어와 접촉 검사 | [모델](STEP_03_UR3E_MODEL.md), [TCP](STEP_07_TCP_FRAME.md), [이동](STEP_09_TCP_MOTION.md) |
| pick/place·경로 | 실제 접촉·마찰로 집기·놓기, 기존 RRT-Connect 재사용 | [12–14](STEP_12_14_PICK_PLACE.md), [12B](STEP_12B_RRT_CONNECT.md), [12C](STEP_12C_LOADED_DETOUR.md) |
| LangGraph·정책 | 규칙 single/roles 흐름, 단일 API 연결부의 오프라인 계약 검사 | [15](STEP_15_LANGGRAPH.md), [21](STEP_21_POLICY_GRAPH_FOUNDATION.md), [21B](STEP_21B_LLM_API_ADAPTER.md) |
| 외부 분해 플래너 | 저장 결과 어댑터, 우리 CAD 내보내기와 실제 ASAP 로더 확인. 이 제품 계획 생성은 미실행 | [16](STEP_16_DISASSEMBLY_ADAPTER.md), [16B](STEP_16B_PLANNER_INPUT.md) |
| 결합·복구 | 정렬·삽입·해제·후퇴, 실패 판정, 첫 기둥 오정렬 복구 1종 | [18](STEP_18_PHYSICAL_INSERTION.md), [19](STEP_19_INSERTION_FAILURES.md), [20](STEP_20_ALIGNMENT_RECOVERY.md), [18B](STEP_18B_TRANSPORT_INSERTION.md) |
| 경험·스킬 | 경험 저장·조건 검색, 기록된 JSON 복구 절차 후보→새 물리 검증→조건 제한 등록 | [22](STEP_22_EXPERIENCE_MEMORY.md), [22C](STEP_22C_SKILL_RECIPES.md) |
| 전체 조립의 이전 근거 | 장애물 없는 10부품과 첫 복구, 두 기둥 우회 | [23C](STEP_23C_FULL_FRAME.md), [23D](STEP_23D_RETAINED_PART_DETOUR.md) |
| 평가 | 규칙 기준선 6회, 동일 조건의 운반 전략 비교. 실제 LLM 비교는 미실행 | [25A](STEP_25A_OFFLINE_BASELINE.md), [25B](STEP_25B_TRANSPORT_COMPARISON.md) |

전체 계획과 미완료 항목은 [개발 계획](ASSEMBLY_EXECUTION_ROADMAP.md)을 따른다. 실물 24단계는 현재 범위에서 제외한다.

## 최신 실제 실행 근거

- 정책 운반 재계획 + 정렬 복구: `runs/step21/20261101T090033_453696Z_291f77b1`, success, 첫 기둥 설치 1/1. sim 103.044초, 51,549행, 최대 최종 오차 0.2826mm. 정책 선택 6회·재계획 1회·정렬 복구 1회·실제 API 0회.
- 10부품 + 장애물 + 정렬 복구: `runs/step23_frame/20261101T081302_175687Z_3c24ac3a`, success, 설치 9/9.
- 같은 조건의 직접 경로: `runs/step23_frame/20261101T081637_610192Z_d40ea38b`, 첫 운반 사전 충돌로 failed, 설치 0/9.
- 두 실행의 비교: `runs/step25_transport/20261101T084224_970377Z_d99d6344`. 운반 전략 외 기록된 설정·코드 지문 일치.
- 네 기둥 우회: `runs/step23_frame/20261101T080219_665496Z_47fd476f`, success, 설치 4/4.
- 슬리브 개별 대체 경로: `runs/component_probes/20261101T080656_722545Z_35a70fa7`, success, 설치 1개. RRT 탐색 없이 검사된 관절 보간 경로 사용.
- 덮개 개별 RRT 운반: `runs/component_probes/20261101T080743_149902Z_b257005a`, success, 설치 1개. 준비한 나머지 9부품은 설치 수에서 제외.
- 21C 코드 범위의 복구 절차 재검증: `runs/step22_recipes/20261101T090035_787981Z_7d816771`, success, 현재 호환 등록본 1개. 기존 장애물 없는 첫 기둥 복구 조건에만 유효.
- 이전 장애물 없는 전체 조립: `runs/step23_frame/20261101T052809_562769Z_f77121fb`.
- 외부 로더 확인: `runs/planner_inputs/20261101T054017_590834Z_53056bf4`. 실제 분해 탐색 성공과는 다름.

23F의 신규 물리/집계 검사 2개, 관련 장면 검사 4개와 당시 범위 검사 1개가 통과했다. 23G에서 갱신한 초기화·입력 범위 검사 1개도 통과했다. 25B의 합성 로그 계약 검사 4개가 통과했고 실제 로그 두 쌍을 별도로 비교했다. 자세한 최초 실패·수정·재검사 이력은 각 STEP 보고서에 있다.

21C에서는 신규 정책 재계획 검사 8개, 기존 그래프 4개, API 연결부 오프라인 계약 17개, 두 기둥 연속 조립 회귀 1개로 관련 검사 30개가 통과했다. 별도 CLI 통합 실행과 기존 복구 절차 재검증도 성공했다. 10부품 전체 통합 실행을 이 변경 뒤 다시 돌린 것은 아니며, 실제 LLM API 호출은 0회다.

전체 저장소 검사는 API 연결부 추가 전에 104개를 통과한 이력이 있다. 이후 추가 기능은 각 보고서의 관련 검사를 실행했으며 최신 코드의 전체 검사를 다시 돌렸다고 주장하지 않는다. 실제 조립 결과와 합성 계약 검사 수를 섞지 않는다.

## 남은 핵심 작업

1. 제공자·모델·API 예산과 키를 준비해 구현된 단일 LLM 정책의 실제 호출·조립을 검증한다. 현재 해당 설정은 답변 대기 중이다.
2. 검증된 대체 파지·접근·복구 후보와 변화 조건을 늘린다. 21C에서는 첫 결합의 운반 재계획/중단을 정책에 노출했고, 실제 경로 생성·검사는 기존 일반 코드가 수행한다. 파지 변경·작업 분해와 전체 작업의 예산 배분은 남아 있다.
3. 우리 CAD를 기존 분해 플래너에서 실제 계획하고 ID·단위·좌표·지지 조건을 확인해 실행 후보로 연결한다.
4. 단일/역할 분리, 모델, 동결한 경험·스킬을 같은 관측·도구·전체 예산의 별도 시험셋에서 비교한다. 현재 성공 사례를 일반 성능 향상으로 주장하지 않는다.
5. 새로운 배치·장애물·파지에서 실행 범위를 검증한다. 현재 전 부품 우회 연결의 성공은 한 제품·고정 장면·seed 3의 결과다.

API 없이 요청 형식을 볼 때는 `uv run --locked python scripts/preview_llm_request.py`를 사용한다. 라이브 설정은 [21B](STEP_21B_LLM_API_ADAPTER.md)를 따른다. 다른 프로젝트의 키나 과금 승인은 사용하지 않았다.

## 저장과 Git

실험 로그·PNG·OBJ·로컬 DB·메모리·등록 스킬은 Git에서 제외한다. 실행 데이터는 `C:\dev\llm-robot-assembly\runs`, 메모리·스킬은 `runtime` 아래에 있어 OneDrive 밖이다. 코드와 보고서는 `codex/simulation-progress`에 로컬 커밋했다. main 변경·원격 push·실물 명령은 수행하지 않았다.
