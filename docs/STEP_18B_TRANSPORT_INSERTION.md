# 18B: 장애물 우회 운반을 첫 기둥의 실제 조립에 연결

작성: 2026-11-01. [12C의 상자 운반·놓기](STEP_12C_LOADED_DETOUR.md)에서 검증한 모션 도구를 기존 첫 기둥 조립과 LangGraph 실행기에 선택적으로 연결했다. 시뮬레이션 전용이며 전체 10부품 자동 우회 완료를 뜻하지 않는다.

## 이번 단계의 통과 기준

**장애물이 있는 장면에서 실제로 기둥을 집고 → 다른 길로 운반하고 → 구멍 위 정렬을 확인하고 → 삽입·해제·후퇴한 뒤 → 결합 상태를 측정한다.** 같은 장애물에서 우회를 끄면 운반 전에 멈춰야 한다. 우회를 마쳤더라도 삽입 경로가 막히면 별도의 우회 없이 멈춰야 한다.

고정 바닥판 1개와 로봇이 조립하는 기둥 1개를 쓰는 개발 단계다. 최종 제품 목표는 기존 10부품 프레임을 유지한다.

## 초보자 관점에서 보는 코드 흐름

LangGraph는 `pick → insert → finish`라는 큰 작업을 실행한다. `insert`의 내부는 다시 운반, 정렬 확인, 하강, 해제, 후퇴, 판정으로 나뉜다. 이번 변경은 그중 **운반**에 들어갔다.

1. `pick`이 실제 접촉과 마찰로 기둥을 들어 올린다.
2. `insert`가 기둥을 구멍 위로 운반할 목표 TCP 위치·방향을 계산한다.
3. 기본 직접 경로를 사전 검사한다. 막혀 있으면 실행하지 않고 FailureReport를 남긴다.
4. 현재 파지를 다시 확인하고 목표 IK와 대안 경로 계산을 요청한다. 관절 보간 경로도 막혀 있으면 RRT-Connect가 경로를 탐색한다.
5. 검사된 경로를 기존 모터 제어기로 실제 실행한다. 물리 계산과 파지·접촉 검사는 계속된다.
6. 도착 후 기둥 위치·방향과 구멍 위 여유 높이를 새로 검사한다. 통과해야만 삽입 접촉을 허용한다.
7. 내려 끼우고, 깊이를 확인한 뒤 손가락을 열고 후퇴한다. 독립적인 8개 결합 판정 항목을 확인한다.

`--transport-strategy direct_then_rrt`는 사용자가 실행 설정으로 선택하는 운반 전략이다. 현재 시연의 정책은 규칙 기반이며 LLM이 우회 전략을 선택한 것은 아니다. 상위 정책과 하위 모션 도구를 구분해서 해석해야 한다.

## 변경한 파일

| 파일 | 변경 내용 |
|---|---|
| `scripts/insertion_scene.py` | 선택형 고정 시험 장애물과 해당 장면 조건을 추가 |
| `scripts/insertion_skills.py` | 첫 기둥의 `insert_transfer`에서만 우회 도구를 호출하는 `_transfer` 연결부와 시도 기록 추가 |
| `scripts/run_assembly_graph.py` | 전략·장애물 실행 옵션, 운반 충돌 예측 기록, 운반 측정값·중간 이미지 저장 추가 |
| `tests/test_insertion_detour.py` | 실제 우회·삽입, 중단 경계, 정렬 복구, 기록 조건 분리 검사 |

기존 호출의 기본 전략은 `direct`다. `insert_lower` 등 접촉 단계는 기존의 지역 경로 검사·실행을 사용한다. Mink와 기존 MIT 라이선스 RRT-Connect를 재사용하며 새 IK·탐색 알고리즘을 만들지 않았다. 출처는 [12B](STEP_12B_RRT_CONNECT.md)에 있다.

## 고정 시험 장면

빨간 장애물은 시뮬레이션 시작부터 존재하는 반지름 25mm의 고정 구다. 중심은 작업대 좌표 `[-0.010, -0.125, 0.115]`m, 충돌 여유는 6mm다. 작업대 높이가 기본 0.75m이면 월드 중심 높이는 0.865m다. 기본 성공 시연에서는 장애물·로봇·부품을 중간에 재배치하지 않는다.

이 구는 경로를 막는 통제된 시험용 형상이다. 공중의 구를 지지하는 실제 구조물을 재현한 것은 아니며, 무작위 작업대 장애물이나 새로운 제품에 대한 일반화 결과로 해석하지 않는다.

장애물 정보는 `resolved_config.json`의 `joint.transport_test_obstacle`에도 저장한다. 환경 조건 지문에 포함되므로 장애물 없는 기존 경험과 같은 조건으로 검색되지 않는다. 기록된 복구 절차 재생과 새 우회 옵션의 동시 사용은 현재 거부한다. 이 새 조합을 검증된 스킬로 자동 등록하지 않는다.

## 실행 명령과 예상 결과

```powershell
cd C:\dev\llm-robot-assembly
# 1. 같은 장애물, 우회 없음: PLANNED_CONTACT로 중단, 종료 코드 1
uv run --locked python scripts/run_assembly_graph.py --mode single --transfer-obstacle --transport-strategy direct
# 2. 우회 후 첫 기둥 조립: success
uv run --locked python scripts/run_assembly_graph.py --mode single --transfer-obstacle --transport-strategy direct_then_rrt --snapshots
# 3. 우회 + 6mm 정렬 오류 + 재관측/복구 + 조립: success
uv run --locked python scripts/run_assembly_graph.py --mode single --transfer-obstacle --transport-strategy direct_then_rrt --scenario alignment_offset --snapshots
```

`single`은 여기서 단일 **규칙 정책**이다. 명시적인 `--llm-config` 없이 실제 API를 호출하지 않는다. 위 명령은 창 없이 계산하며 `--snapshots`가 있으면 정렬 확인 시점·해제 직전·최종 PNG를 저장한다.

## 실제 실행 결과

| 같은 고정 장애물에서의 조건 | 결과 | 로봇 설치 | 시뮬레이션 시간 | 최종 검사 구간 최대 위치 오차 |
|---|---|---:|---:|---:|
| 우회 꺼짐 | 운반 사전 검사에서 중단 | 0개 | 31.494초 | 조립 미실행 |
| 우회 켜짐 | RRT 우회 → 조립 성공 | 1개 | 89.698초 | 약 0.172mm |
| 우회 + 정렬 오류 | RRT 우회 → 정렬 복구 1회 → 조립 성공 | 1개 | 103.044초 | 약 0.283mm |

기본 우회 성공 실행은 목표 IK 38회, RRT 경로 검사 질의 1,265회, 반환된 관절 자세 448개를 사용했다. 예정 운반 시간은 약 33.412초, 운반 중 최대 상대 미끄러짐은 약 1.222mm였고 기존 5mm 이내 조건을 통과했다. 운반 중 기둥 최대 기울기는 약 25.3도였다. 도착 후 기존 1도 이내 정렬 검사를 통과한 뒤 삽입했다.

최종 삽입 깊이는 약 16.108mm였으며 바닥 지지, 해제, 위치, 방향, 깊이, 선속도, 각속도, 침투량의 8개 항목을 통과했다. 기본 우회 시연은 실제 벽시계 약 30.5초, 정렬 복구 포함 시연은 약 40.7초였으며 다른 검사와 동시에 실행했다. 시뮬레이션 측정값을 실물 정확도로 해석하지 않는다.

실행 증거:

- 우회 없음: `runs/step21/20261101T074533_644579Z_2604489e`.
- RRT 운반·조립: `runs/step21/20261101T074325_472434Z_c9249f9f`.
- RRT 운반·정렬 복구·조립: `runs/step21/20261101T074547_926168Z_2e400be5`.

처음에는 반지름 10mm·작업대 기준 높이 96mm의 더 작은 장애물도 시험했다. `runs/step21/20261101T074229_309517Z_5ad59d6d`에서 직접 TCP 경로는 막혔지만 관절 보간 경로가 통과하여 조립에 성공했다. 당시 선택된 방법은 `checked_joint_interpolation`이며 RRT 탐색 성공으로 세지 않는다. 이후 위의 큰 장애물로 변경하여 실제 RRT 탐색 경로도 검증했다.

같은 고정 장면과 seed 3으로 진행한 개발 시험이다. 여러 새 작업에 대한 성공률 실험이나 LLM 모델 비교가 아니다. 모든 시연의 실제 API 호출은 0회다.

## 두 종류의 복구 기록

운반 사전 검사에서 막힌 경로를 거부한 사건은 `transport_preflight_failures`와 `transport_preflight_001.json`에 남긴다. `transport_attempts`에는 사용한 계획 방법·탐색 횟수·시작/종료 시각·실행 상태가 들어간다. 충돌 후보는 계산용 상태이며 실제 로봇을 장애물에 부딪힌 기록이 아니다.

정렬 검사 실패는 기존 `graph_state.failures`, 복구 횟수는 `graph_state.recoveries`에 남긴다. 복구 포함 시연은 운반 대안 계획 1회와 정렬 복구 1회다. 상위 정책의 행동은 `pick → insert → recover_alignment → insert → finish`였으며 하나의 episode에서 초기화 없이 이어졌다. 두 번째 운반은 이미 정렬을 복구한 상태여서 직접 경로로 충분했다.

## 검사 결과와 한계

새 검사 6개를 각각 통과했다: 실제 RRT 운반·삽입, 같은 장애물에서 직접 경로 중단, 우회와 정렬 복구의 연속 실행, 삽입 하강 장애물에서 우회 금지, 장애물이 없을 때 직접 경로 유지, 환경 조건·절차 재생 범위 구분.

정상 실행의 실제 물리 계산 전후를 감시해 로봇·제품의 `qpos/qvel` 강제 변경, 추가 부착, 외력, 중력 해제가 없음을 확인했다. 장애물 위치도 정상 실행 중 고정이었다. 물리 기록 간격은 최대 2ms였다. 삽입 중단 반례에서는 운반 완료 후 **시험 코드가 장애물만** 하강 경로로 옮겼다. 로봇·부품은 바꾸지 않았으며 실제 형상 사전 검사로 `insert_lower/preflight`에서 중단했다. 그리퍼를 열거나 RRT를 추가 호출하지 않았다.

첫 신규 검사 실행은 5개 통과, 1개는 조건 비교 테스트에 NumPy 배열을 JSON 변환 없이 전달한 오류였다(전체 147.589초). 실제 실행기의 저장 형식과 같게 테스트 입력을 변환한 뒤 해당 검사만 재실행하여 통과했다(0.340초). 물리 판정 조건은 변경하지 않았다. 수정 후 전체 6개를 한 번에 재실행한 것은 아니다.

기존 삽입 검사 5개(25.414초), LangGraph 검사 4개(46.278초), API 실패 사용량 기록 검사 1개(1.394초)도 통과했다. 신규 6개와 관련 10개가 각각 통과한 범위이며 전체 저장소 검사나 전체 10부품 조립을 이번 코드로 재실행한 것은 아니다. API 검사에서는 HTTP 테스트 응답을 사용했으며 실제 모델 호출은 없다.

```powershell
uv run --locked python -m unittest discover -s tests -p test_insertion_detour.py -v
uv run --locked python -m unittest discover -s tests -p test_insertion.py -v
uv run --locked python -m unittest discover -s tests -p test_assembly_graph.py -v
uv run --locked python -m unittest discover -s tests -p test_openai_policy.py -k runner -v
```

로그: `.scratch/test_insertion_detour.log`, `.scratch/test_insertion_detour_conditions_fixed.log`, `.scratch/step18b_insertion_regression.log`, `.scratch/step18b_graph_regression.log`, `.scratch/step18b_api_runner_regression.log`.

실행 코드 지문이 바뀌어 기존 첫 기둥 정렬 복구 절차도 새 물리 episode에서 재검증했다. `runs/step22_recipes/20261101T074630_443637Z_756b7ed8`에서 성공했고 호환 등록본 1개를 확인했다. 등록 ID는 `aa19ae9a76876066d3cb9f8c8c3413ea4abcd8dba4b681cce52dffb1683dc72e`다. 이 등록본은 기존 장애물 없는 정렬 복구 조건에 한정된다.

이 18B 단계는 첫 기둥·고정 장애물 1개에 대한 선택형 연결이다. 후속 [23D](STEP_23D_RETAINED_PART_DETOUR.md)에서 두 기둥 연속 조립과 기존 조립물 보존, 빈 그리퍼 접근 우회도 검증했다. 운반 중 방향 유지, 움직이는 장애물, 여러 기둥·슬리브·덮개·이미 조립된 구조물 주변의 자동 우회는 별도 검증이 필요하다. 이산 경로 검사와 충돌 여유가 연속 경로 안전성을 보장하지는 않는다. 실제 LLM 판단·실물 실행은 이번에 검증하지 않았다.
