# 23F: 구멍이 있는 슬리브와 덮개의 대체 운반 경로

작성: 2026-11-01. [23E](STEP_23E_FOUR_POST_DETOUR.md) 뒤에 기둥 외의 복합 부품을 개별 물리 시험했다.

## 무엇을 구현했나

슬리브와 덮개에는 구멍이 있다. 경로 검사에서 전체를 막힌 상자로 취급하면 들어갈 수 있는 공간도 막혔다고 판단한다. 반대로 부품의 중심점만 검사하면 실제 벽이 기둥과 부딪힐 수 있다. 기존 복합 상자 형상과 FrameVerifier를 그대로 사용해 모든 실제 충돌 벽을 검사하며 대체 운반 경로를 연결했다.

`scripts/run_component_probe.py`에 `--transport-strategy`, `--transfer-obstacle`, 부품별 운반·빈 그리퍼 접근 시도, 사전 충돌 보고서, 미끄러짐·기울기·기존 조립물 오차, 실행 시간과 소스 지문을 추가했다. `FrameSkills`와 장면 생성기는 개별 복합 부품 시험에도 사용할 수 있다. 전체 프레임 실행기의 우회 범위는 이 단계에서 아직 2~5부품으로 제한한다.

초기 준비물은 자유 물체로 결합 위치에 배치한다. 초기화 후에는 부품 위치를 강제로 바꾸지 않는다. 준비한 기둥·슬리브는 **초기 사전 조립 부품**으로 기록하고 로봇 설치 수에 넣지 않는다. 따라서 덮개가 올라간 최종 모양만 보고 전체 로봇 조립 성공이라고 부르지 않는다.

## 실행

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/run_component_probe.py --component sleeve_2 --transfer-obstacle --transport-strategy direct_then_rrt --snapshots
uv run --locked python scripts/run_component_probe.py --component cover --transfer-obstacle --transport-strategy direct_then_rrt --snapshots
# 같은 슬리브 조건에서 대체 경로를 끈 비교군
uv run --locked python scripts/run_component_probe.py --component sleeve_2 --transfer-obstacle --transport-strategy direct
```

장애물은 23D/23E와 같은 고정 구이며 시작부터 존재한다. 제품 형상·파지 위치·삽입 경로·판정 임계값은 바꾸지 않았다. 우회는 빈 그리퍼 접근과 자유 공간 운반에만 적용한다. 실제 접촉 하강 중 충돌이 예측되면 멈춘다.

## 실제 결과

| 설치 대상 | 실제 운반 계획 방법 | sim / 실제 실행 시간(초) | 최종 검사 최대 위치 오차(mm) | 최대 운반 미끄러짐(mm) |
|---|---|---:|---:|---:|
| sleeve_2 | checked_joint_interpolation | 93.432 / 94.5 | 0.0640 | 0.789 |
| cover | RRT-Connect | 125.756 / 293.8 | 0.1002 | 1.739 |

- `sleeve_2`: `runs/component_probes/20261101T080656_722545Z_35a70fa7`, 측정 46,737행. 미리 조립한 제품 부품 6개(바닥판 포함), 로봇 설치 **1/1**. 운반 중 최대 기울기 0.07도. 충돌 질의 407회, 관절 자세 203개.
- `cover`: `runs/component_probes/20261101T080743_149902Z_b257005a`, 측정 62,899행. 미리 조립한 제품 부품 9개(바닥판 포함), 로봇 설치 **1/1**. 운반 중 최대 기울기 25.55도. 충돌 질의 1,453회, 관절 자세 621개.

두 실행 모두 해제·후퇴 뒤 기존 8가지 물리 결합 검사를 통과했다. 기존 조립물의 지지·위치·방향·속도도 계속 검사했다. `whole_frame_robot_assembly=false`, `policy_kind=scripted`, `api_calls=0`이다. 최종 PNG는 실제 물리 상태를 렌더링한 것이다.

슬리브는 직접 TCP 경로가 고정 구와 그리퍼의 접촉을 예측해 거부됐다. 다른 관절 경로를 검사하자 통과했고, **RRT 탐색 없이 관절 보간 경로**로 운반했다. `direct_then_rrt`는 필요할 때 탐색을 허용하는 옵션 이름이며 모든 성공이 RRT 결과라는 뜻은 아니다.

직접 경로만 사용한 비교 실행 `runs/component_probes/20261101T080854_643987Z_fe306d86`은 sim **53.478초**, `insert_transfer/preflight`에서 `PLANNED_CONTACT`로 중단했다. 로봇 설치 0개이며 실패 결과를 보존했다. 실패한 실제 명령을 반복한 것이 아니라 사전에 거부한 경로와 실행 가능한 대안을 비교한 시험이다.

## 검증과 개발 이력

신규 검사는 실제 슬리브 집기·운반·삽입 전 과정에서 물리 상태와 고정 형상을 감시한다. 초기 시험 준비가 끝난 뒤 실제 `mj_step` 바깥에서 위치·속도·고정 형상·등식 제약을 변경하면 실패한다. 추가 부착·외력·중력 해제도 검사한다.

운반 시작 시 **슬리브 벽이 기존 기둥과 겹치는 계산용 목표**를 별도 요청했다. 실제 벽과 기둥의 충돌 형상을 검출하고 `PLANNED_CONTACT`로 거부하며 실제 시간·위치·속도·제어 입력은 바뀌지 않았다. 그 뒤 정상 목표로 실제 조립을 완료했다. 구멍이 열린 형상과 막힌 형상의 물리 차이는 기존 장면 검사로 다시 확인했다.

처음 신규 검사를 실행할 때 조립 자체와 벽 충돌·상태 보존 검사는 통과했지만, 검사 코드가 대체 경로를 무조건 RRT라고 가정해 마지막 방법 이름 비교에서 실패했다. 별도 CLI 실행에서 관절 보간임을 확인하고 그 기대값을 수정했다. 물리 성공 조건은 바꾸지 않았다. 최초 로그 `.scratch/step23f_tests.log`를 보존하고 두 신규 검사를 다시 실행했다.

- `step23f_tests_verified.log`: Ran 2 tests in 102.841s, PASS.
- `step23f_scene.log`: Ran 4 tests in 2.252s, PASS.
- `step23f_scope.log`: Ran 1 test in 0.193s, PASS.

```powershell
uv run --locked python -m unittest discover -s tests -p test_component_detour.py -v
uv run --locked python -m unittest discover -s tests -p test_frame_scene.py -v
uv run --locked python -m unittest discover -s tests -p test_frame_detour.py -k full_frame_compound -v
```

준비물만 배치하고 즉시 중단하는 반례는 설치 0개로 집계되는지도 확인했다. 검사 수는 독립적인 로봇 조립 성공 횟수가 아니다.

## 한계와 다음 단계

특정 슬리브 위치 하나와 중앙 파지 덮개의 개별 개발 시험이다. 전체 10부품을 이 장애물 조건에서 연속 우회 조립한 결과는 아직 아니다. RRT 경로 중간의 수직 유지, 임의 장애물 일반화, 연속 충돌 안전성은 보장하지 않는다. 실제 LLM 판단·실물 작업은 수행하지 않았다.

다음은 개별 운반 기능을 전체 프레임 실행기에 연결해 같은 장면에서 처음부터 끝까지 검증하는 것이다. 코드가 바뀌었으므로 기존 등록 복구 절차의 코드 호환성도 새 물리 재검증 후 갱신해야 한다.
