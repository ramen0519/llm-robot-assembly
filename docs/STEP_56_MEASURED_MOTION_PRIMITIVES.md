# STEP 56 · 배터리팩으로 공통 집기·설치·해제 동작 보완

2026-11-26. 작업 기준은 `codex/general-mesh-v2`, HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f`다. 기존 미커밋 변경을 보존했고 이번에 commit/push하지 않았다. 이 단계는 API 없는 동작 함수 개발·검증이다. 실제 Astra 판단이나 연구용 경험 수집 성공으로 대신하지 않는다.

## 이번에 바꾼 것

1. **`open_until_clear` 공통 해제 함수 추가.** 기존 제한 개방은 항상 2mm 추가 개방을 목표로 했다. 이제 그 폭은 상한이며, 실제 손가락 접촉이 없어지고 부품과 최소 0.1mm 떨어지면 개방 증가를 멈춘다. 이후 1초 동안 재접촉·원래 목표 이탈·개방 상한 초과를 감시한다. 지지와 속도가 확인돼야 후퇴로 넘어간다. 최종 완료의 그리퍼 이격 5mm 조건은 그대로다.
2. **파지 벌림폭 추천 보완.** CAD의 잡을 부위 두께뿐 아니라 전체 손가락 접근·닫는 구간·설치 끝 형상을 같이 검사한다. 기존 3mm 여유 선호를 유지하고, 그 여유를 가진 후보가 없으면 함께 통과한 후보 중 작은 벌림폭을 추천한다. Astra는 여전히 직접 위치·방향·벌림폭을 제안/수정할 수 있다. 점수만으로 성공시키지 않는다.
3. **개발 정책의 후보 선택 개선.** 이미 명목상 접근/설치 간섭이 알려진 후보보다 함께 가능한 후보를 우선하고, 거절되면 다음 후보를 시험한다. Astra의 선택 순서를 강제하는 규칙이 아니다.
4. **다음 부품 재시도 오류 수정.** 첫 부품의 개발용 파지 지정 때문에 다른 부품에서도 시도 번호를 한 번 빼서 거절한 후보를 반복하던 문제를 고쳤다.
5. **부품 간 해제 경계 보존.** 앞 부품 설치 뒤 손이 실제로 비고 열린 경우에만 후퇴 완료 상태를 다음 실행기에 전달한다. 새 집기를 시작하면 초기화한다. 뒤 부품의 복사 검사만 실패했을 때 앞 부품의 최종 공동 관찰이 이유 없이 생략되지 않게 했다.
6. **실패 진단 표시와 재현 도구.** `probe_component_primitive.py`는 인증된 빈 손 상태를 복원한 별도 개발 분기에서 집기·설치를 실제 시험한다. 설치를 시도한 뒤의 실패를 `not_run`으로 표시하지 않는다. 물리 실패는 `failed`, 시간/환경 오류는 `unknown`이다. 복사/복원 진단을 새 전체 Astra 실행으로 집계하지 않는다.

주요 파일: `scripts/assembly_v2/release.py`, `skills.py`, `grasp_screen.py`, `policy.py`, `execution.py`, `motion_plan.py`, `scripts/probe_component_primitive.py`, `tests/test_v2_measured_release.py`, `tests/test_v2_primitive_recommendations.py`.

이번에는 배터리 부품 번호, 부품별 파지 좌표, 강제 결합, 목표 순간이동을 실행 함수에 추가하지 않았다. 기하 진단용 시험 제안 파일은 별도 연구 폴더에 두고 최종 자동 시험에서는 사용하지 않았다.

## 실제 시험 결과

| 실행 | 실제 정책 | 결과 | 설치 | 시간(초) |
|---|---|---|---|---:|
| `adaptive_release01` | scripted_development | `component_failed` | 없음 | 31.250 |
| `automatic_battery01` | scripted | `deadline` | 3,4,6 | 1855.235 |
| `baseline_2ms01` | scripted | `partial` | 3 | 503.781 |
| `fixture_regression01` | scripted | `reference_invalid` | 없음 | 7.734 |
| `pick_release01` | scripted_development | `component_failed` | 없음 | 39.734 |
| `pick_release02` | scripted_development | `component_failed` | 없음 | 41.032 |
| `pick_release03` | scripted_development | `component_failed` | 없음 | 46.172 |
| `pick_release04` | scripted_development | `component_failed` | 없음 | 76.297 |
| `released_boundary_followup01` | diagnostic_observation_only | `passed` | (관찰) 3,4,6 | 8.968 |
| `socket_regression01` | scripted | `physical_success` | 1,2 | 360.047 |

`automatic_battery01`은 **CAD와 순서가 담긴 hint 한 입력에서 새 장면을 준비한 실행**이다. `--first-grasp`, 제품별 registry나 별도 Python을 사용하지 않았다. 로봇 환경은 기존의 공통 `workcell.hour.source_screen.json`이다. 원래 CAD 목표, 형상 해시, 충돌 자산, 질량, 설치 조건, 초기 배치가 이전 준비본과 동일한지 `input_equivalence.json`으로 확인했다.

최종 상태 `deadline`, 설치 **3/12**, 완료 부품 `3, 4, 6`. 전체 입력부터 종료 기록까지 1855.235초다. 후보 시도 6회, 거절 3회, 재시도 2회. 거절 집계에는 시간 한도로 끝까지 검사하지 못한 후보도 포함되며 물리적 불가능과 구분한다. 본 장면의 설치 완료 이벤트만 다음 표에 집계했다.

| 본 장면 검증 부품 | 각 축 중 최대 위치 오차(mm) | 상대 회전(도) | 경질 관통(mm) | 관측수 |
|---|---:|---:|---:|---:|
| 3 | 1.000709 | 0.061641 | 0.112162 | 501 |
| 4 | 0.475421 | 1.113739 | 0.112162 | 501 |
| 6 | 0.107345 | 0.006675 | 0.112162 | 501 |

실패/중단 이벤트:

- 부품 4: `PART_LOST`, `pick_lift`, 출처 `mujoco_preflight_copy`. Bilateral grasp lost or relative part pose drifted.
- 부품 10: `PLANNED_CONTACT`, `install_endpoint_check/preflight`, 출처 `planner_candidate`. No collision-free installation endpoint found in bounded IK search; no motor command executed.
- 부품 10: `TimeoutError`, `pick_lift`, 출처 `mujoco_preflight_copy`. deadline:physics_step

마지막 공동 관찰 상태: `unknown`, 0샘플. 이 마지막 미완료 관찰을 전체 조립 통과로 표시하지 않는다.

별도 후속 진단 `released_boundary_followup01`: `passed`, 501샘플, 8.968초. 원래 본 장면 종료 체크포인트의 전체 적분 상태와 제어 참조를 복원하고, 추가 조립 이동 없이 기존 설치 부품을 재관찰했다. 출처는 `mujoco_preflight_copy`이며 **원래 회차의 미완료 최종 관찰을 통과로 덮어쓰지 않았다**. 이 진단은 새로운 설치/Astra 성공/전체 12개 성공이 아니다.

`socket_regression01`은 이전 실제 성공 입력의 새 장면 회귀 시험이다. 2/2 설치, 해제 실패 후 제한 개방 재시도, 마지막 공동 1초/501샘플 검사를 통과했다. 삽입축/깊이가 정의된 두 부품이므로 삽입 깊이 1mm 기준도 적용했다. 최대 축 위치 오차/삽입 깊이 오차 약 0.108mm, 최대 회전 오차 약 0.00068°, 경질 관통 약 0.108mm였다. 전체 약 360초, API 0회다.

초기 `fixture_regression01`은 다른 자유 기립 예제다. 육각 기둥이 완성 기준 장면에서 각속도 0.011011rad/s, 이동속도 1.09668mm/s를 보여 **로봇 실행 전에 거부**됐다. 이 실패를 숨기거나 기준을 완화하지 않았고, 소켓 회귀 성공과 구분한다.

## 0.004초 실패와 0.002초의 의미

처음 0.004초에서는 제한 해제만 바꾸어도 케이스와 그리퍼 연결부 접촉이 재현됐다. 파지 경사 +5° 후보들은 들기 중 회전 미끄럼으로, 작은 경사 후보는 해제 접촉으로 실패했다. 초기 벌림만 줄인 경우도 설치 끝 자세가 막혔다. 이 진단들은 모두 보존했다.

0.002초 새 장면에서는 첫 홀더가 통과했다. 기준 간격과 초기 정착부터 달라지는 비교이므로 특정 코드 한 줄만이 성공 원인이라고 단정하지 않는다. 0.004초를 연구 수집 기준으로 승격하지 않았다. 원래 `config/completion.v1.2.json` SHA256 `676ca2fefb74374024d391f613b1e454731bf4498ed0bcbe6b9aed083fda0ad3`는 유지한다.

기준은 각 축 5mm, 상대 회전 3°, 최종 경질 관통 0.5mm, 이동속도 1mm/s, 회전속도 0.01rad/s와 해제 후 연속 관찰이다. 필요한 경우의 삽입 깊이/정렬 검사는 생략하지 않는다. 집기, 경로, 비허용 접촉, 원래 미끄럼 기준도 완화하지 않았다.

## 코드·기록 검증과 경험

- `python -B -m unittest discover -s tests -p 'test_v2_*.py'`: **168개 통과**, 5.748초. 명령 인자 오류가 출력되는 항목은 잘못된 시간 범위를 거부하는 예상 테스트다.
- 실제 물리 실행은 위 표와 별도다. 단위 테스트나 명목상 후보 여유를 물리 성공으로 세지 않았다.
- `baseline_2ms01`과 `socket_regression01`의 audit 및 보존 bundle 해시를 재확인했다. 자동 배터리의 최종 결과는 아래 저장 자료와 상세 결과에 따른다.
- 이번 작업의 **Astra 호출 0회, API 비용 $0, 연구용 경험 등록 0건**이다. 개발 실패/해결 로그는 보존했다. `scripted` 결과를 실제 Astra 수집 DB에 넣지 않았다.
- 실물 로봇과 E:를 사용하지 않았다. 키를 읽거나 출력하지 않았고 사용자 UI 서버도 종료하지 않았다. 분해 프로젝트·원본 CAD·기존 run은 보존했다.

## 실행 및 확인

새 API 없는 입력→준비→실행 시험. 출력 폴더 이름은 새 이름을 사용한다.

```powershell
cd C:\dev\llm-robot-assembly
& .\runtime\luna-venv\Scripts\python.exe -B scripts\run_assembly.py run `
  --input 'F:\dev\llm-robot-assembly-data\research\20261126_battery_4ms\support_fixed\input\assembly_hint.json' `
  --profile 'F:\dev\llm-robot-assembly-data\runs\lab_battery_hour_20261125_01\workcell.hour.source_screen.json' `
  --output 'F:\dev\llm-robot-assembly-data\runs\NEW_BATTERY_PRIMITIVE_CHECK' `
  --policy scripted --condition R0 --split development --physics-timestep 0.002 --seconds 1800
```

현재 결과 루트: `F:/dev/llm-robot-assembly-data/research/20261126_motion_primitives/`

- `PROGRESS.md`: 문제와 수정 기록. `MOTION_FUNCTIONS.md`: 쉬운 동작 함수 설명.
- `RESULTS.json`, `results.csv`: 실행별 성공/실패/시간/측정 요약.
- `automatic_battery01/report.html`, `events.jsonl`, `final_verification.json`(생성된 경우), `media/execution.mp4`: 자동 선택 배터리의 본 장면과 결과.
- `socket_regression01/report.html`, `final_verification.json`, `media/execution.mp4`: 실제 삽입·해제 회귀 결과.
- `baseline_2ms01/`: 개발용 첫 파지 지정 대조 시험. 자동 선택 실행과 구분한다.
- `pick_release01`–`04`, `adaptive_release01`: 실패한 물리 진단. `release_pose_scan01/02`: 명목상 기하 진단이며 조립 성공 결과가 아니다.
- `source_manifest.json`, `before/`, `before_opening_selection/`, 각 run의 `code_versions.json`: 출처와 수정 버전 증거.

기존 서버 주소는 `http://127.0.0.1:8765`다. 이번 CLI 결과는 위 로컬 보고서/영상을 직접 열 수 있다. 저장 결과를 확인하는 데 API 키는 필요 없다.

## 남은 한계

이 배터리 환경은 stock 손가락만의 검증이 아니다. 기존 75mm 연장, 폭 8mm의 시험용 end-contact 손가락 설정을 유지한 MuJoCo 시험이며 실물 제작/검증은 하지 않았다. 현재 보완은 실제 접촉 기반 공통 동작이며, 재파지·나사 체결·케이블 연결·전기적 배터리 연결을 구현한 것이 아니다.

배터리 전체 설치 미완료 부품: `10, 5, 7, 9, 8, 2, 1, 11, 12`. 이번 회차에서 파지 시도 전인 부품: `5, 7, 9, 8, 2, 1, 11, 12`. 시간 제한/미시도를 물리적 조립 불가능으로 해석하지 않는다. 아직 전체 배터리 12/12 성공이나 최종 프로토타입 전체 검증 완료로 보고하지 않는다.

실제 Astra가 최신 후보/동작을 선택해 전체를 완료하는 시험과 실패 해결→지침 등록→다음 실행 검색·재검증, 연구 수집/독립 평가는 이 API 없는 검증으로 대체하지 않는다. 위에서 검증한 범위만 완료로 보고한다.


## 이어서 진행하는 작업

사용자는 시간 경과만으로 전체 작업을 중단하지 말고 완료까지 이어가도록 요청했다. 위 결과는 첫 30분 회차의 보존 기록이다. `continue-development` 명령을 추가해 실제 설치 증거와 저장 상태를 대조하고, 빈 손·후퇴 상태를 복원한 뒤 다시 1초/501회 관찰하도록 연결했다. 이 기능은 API 없는 개발에만 허용한다. 코드 검사 172개가 통과했다. `battery_continuation01`에서 남은 조립을 진행한다. 이전 실행을 덮어쓰거나 복원을 새로운 설치 성공으로 집계하지 않는다.
