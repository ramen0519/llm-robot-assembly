# API 없는 배터리팩 연속 조립·실패 수정 기록

2026-11-25. 브랜치 `codex/general-mesh-v2`, 시작 HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f`. 기존 변경과 모든 실패/성공 결과를 보존했다. 미커밋 상태.

## 현재 상태

추가 API **0회 / $0**. 최신 검증 분기에 설치된 부품은 **6/12개**, ID `10, 3, 4, 5, 6, 7`다.
최신 장면: `F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_retreat_completion01`.
이 작업은 **scripted 개발 시험**이며 새 Astra 전체 조립 성공, 연구 경험 수집 또는 새 메모리 등록으로 집계하지 않는다.
이전 검증된 3부품 장면에서 이어지며, 빈 바닥판부터 새로 시작한 단일 실행이라고 주장하지 않는다.

## 문제·수정

| 확인된 문제 | 근거 | 수정·재검증 |
|---|---|---|
| 같은 모양이어도 설치 공간은 다름 | 홀더 3의 성공 파지를 동일 CAD 홀더 4로 옮기자 그리퍼 본체가 케이스에 닿는 후보가 나옴 | 모든 삼각형 대응을 검사한 뒤 현재 장면에서 접근·닫기·목표 형상을 별도 재검사. 작은 각도 대안 비교 |
| 넓게 접근할수록 좋은 것으로 판단 | 85mm 벌림에서 닫으면 손가락 뒷면이 홀더 외곽에 먼저 닿음. 좁은 벌림에서 같은 접촉점으로 닫기는 가능 | 접근 여유만으로 벌림을 고르지 않고, 해당 벌림부터의 닫기 전체와 설치 예상도 함께 확인 |
| 요청한 접근 벌림보다 더 닫힘 | 10.05mm 요청 뒤 약 6.93mm로 정착하는 기존 제어 기록 | 빈 그리퍼 보정표의 명령과 실제 관측을 함께 사용. 10.05/29.00mm의 마지막 0.5초 최대 오차 0.002/0.007mm 이내 native 확인 |
| 운반 중 실측 파지 변환이 조금 바뀜 | 홀더 4를 내리는 중 기울기가 3도 경계를 넘어 기존 홀더 접촉에서 중단 | 목표 직전 10/6/3/1.5/0mm 위치에서 실측 파지 변환을 다시 구함. 원래 CAD 목표·슬립 기준·힘·접촉·완료 기준은 유지 |
| 수정 자세 대안이 일반 모델 관측에서 누락 | 실행기 후보 요약의 필드 목록이 새 geometry_pose_alternatives를 포함하지 않았음 | 공통 candidate_packet으로 포함. 오프라인 단위 검사, 새 Astra 실행은 not_run |
| 고정 100mm 들기 자세를 찾지 못함 | 셀 5/7에서 물리 동작 전 IK 탐색 실패 | 실제 상태가 변하지 않은 계획 실패에 한해 100/80/65mm 높이를 비교. 80mm 실제 들기와 50mm 이상 이격을 검증 |
| 해제 뒤 시간 소진을 전체 조립 실패와 혼동 | 셀 5를 놓고 후퇴한 뒤 시간 종료 | 적분·제어 상태를 검증 복원해 해제 후 관찰만 별도 이어가기. 셀 5와 기존 설치 부품 501샘플 통과 |
| 운반 시간 소진 뒤 같은 계산을 반복 | 셀 7은 파지·들기 통과 뒤 transport_preinstall에서 시간 종료 | 실패 단계를 resource_or_cancellation으로 구별하고, 원래 파지/슬립 참조를 보존한 상태에서 재관측·새 경로 검사 후 제한적으로 이어가기. 실제 후속 결과는 아래 표 참조 |

## 실제 시험 기록

| 부품 | 실행 폴더 | 결과 | 실패 단계/코드 | 실행 초 |
|---|---|---|---|---:|
| 4 | part04_attempt01 | component_failed | grasp_close / UNEXPECTED_CONTACT | 96.5 |
| 4 | part04_attempt02 | component_failed | completion_observation / ValueError | 9.3 |
| 4 | part04_attempt03 | component_failed | insert_goal / UNEXPECTED_CONTACT | 197.8 |
| 4 | part04_install_feedback01 | component_success |  /  | 178.6 |
| 5 | part05_attempt01 | component_failed | pick_lift/ik / IK_NOT_CONVERGED | 239.5 |
| 5 | part05_completion01 | component_success |  /  | 34.4 |
| 5 | part05_lift_feedback01 | component_failed | open_after_retreat / TimeoutError | 374.4 |
| 7 | part07_attempt11 | component_failed | approach_pregrasp / IK_NOT_CONVERGED | 45.7 |
| 7 | part07_attempt12 | component_failed | transport_preinstall / TimeoutError | 435.4 |
| 7 | part07_attempt13 | component_failed | approach_pregrasp / IK_NOT_CONVERGED | 40.5 |
| 7 | part07_install_continuation01 | component_failed | transport_preinstall / TimeoutError | 250.5 |
| 7 | part07_retreat_completion01 | component_success |  /  | 68.7 |
| 7 | part07_transport_continuation01 | component_failed | retreat_limited_open / TimeoutError | 250.5 |

시험 프로세스 누적 2221.8초. 코드 수정·기하 검색·별도 진단 시간은 제외한다. 제한 시간 종료는 물리 불가능으로 바꾸지 않는다.

## 실행과 기록 원칙

부품마다 부모 모델/상태/완료 근거의 해시를 확인하고 적분 상태를 복원한다. 해제된 이전 부품을 다시 검사한 후에만 다음 동작을 시작한다.
실패 시 그 분기를 저장하고 종료한다. 다음 후보는 동일한 검증 부모에서 시작하는 **별도 시뮬레이션 시험**이며, 실패 상태를 지우거나 같은 실행에서 순간이동으로 되돌리지 않는다.
선택된 성공 분기의 후속 실제 상태만 다음 부품의 시작으로 이어진다. 각 결과에는 `parent_boundary.json` 또는 `source_checkpoint.json`을 남긴다.
들기 체크포인트를 재사용할 때는 제어기 목표와 원래 미끄러짐 기준도 함께 복원한다. CAD 완성 자세를 실행 상태에 대입하지 않는다.

최종 XYZ 각 축 5mm, 회전 3도, 경질 관통 0.5mm, 이동속도 1mm/s, 회전속도 0.01rad/s, 해제·이격 및 연속 501샘플 기준은 유지한다. 나사·전기 연결·실물 배터리 취급을 구현한 것이 아니다.

## 주요 코드

- `scripts/offline_sequence.py`: 완료된 장면의 검증 복원, 모든 삼각형을 비교한 모양 대응, 현재 형상의 재검사, 단일 부품 실제 분기 시험.
- `scripts/assembly_v2/grasp_screen.py`, `grasp.py`: 접근 벌림과 닫기 구간·설치 예상의 공동 검사 및 후보 v2.8.
- `scripts/assembly_v2/skills.py`: 보정표/측정 기반 접근 벌림 제어, 목표 직전 실측 파지 변환 갱신.
- `scripts/assembly_v2/packets.py`, `execution.py`: 수정 자세 대안을 실제 정책 관측에 포함.
- `tests/test_v2_offline_sequence.py`: 모양 재사용과 후보 관측의 회귀 검사.
- `scripts/continue_offline_install.py`: 시간 소진 뒤 해제/운반 상태를 검증 복원하는 별도 개발 진단. 물리 실패의 무조건 재실행은 거절.
- `scripts/assembly_v2/failure_diagnosis.py`, `scripts/review_assembly_failures.py`: 원본 근거를 보존한 실패 단계 분류와 분기별 검토표.

## API 없는 실행 예

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
# 실제로 있는 새 출력 폴더 이름과 부품 ID를 지정한다.
& $assemblyPython -B scripts/offline_sequence.py suggest `
  --parent 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_retreat_completion01' --part 9 `
  --experiences 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/development_grasps.json' `
  --output 'F:/dev/llm-robot-assembly-data/runs/NEW_SEARCH' --seconds 100
& $assemblyPython -B scripts/offline_sequence.py attempt `
  --parent 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_retreat_completion01' `
  --proposal 'F:/dev/llm-robot-assembly-data/runs/NEW_SEARCH/proposal_00.json' `
  --output 'F:/dev/llm-robot-assembly-data/runs/NEW_TRIAL' --seconds 420
```

이미 설치된 부품 ID를 지정하면 거절한다. 형상 제안이 없거나 실제 동작이 실패하면 원인과 `not_run/unknown/failed`를 그대로 남긴다.

결과 루트: `F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01`. 각 실행의 `result.json`, `completion.json`(성공 시), `failures/`, `checkpoints/`, `media/execution.mp4`에서 실제 근거를 확인할 수 있다.
