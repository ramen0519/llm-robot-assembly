# 배터리팩 실제 Astra 재검증 기록

2026-11-25. `codex/general-mesh-v2`, 시작 HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f`. 기존 미커밋 변경을 보존했다. 이번 작업에서 commit/push하지 않았다.

## 질문에 대한 답

확인된 첫 파지는 홀더를 집고 들어 올릴 수 있었지만, 케이스 바닥의 원래 CAD 목표에 놓는 자세에서 2F-85 본체와 케이스 벽이 약 11.86mm 겹쳤다. 배터리 셀 내부가 아니라 케이스 안쪽 접근 문제다. 이 충돌 자세는 계획 후보 검사에서 거절했으며 실제 모터 명령으로 실행하지 않았다. 제한된 후보의 실패가 모든 파지의 불가능성을 증명하지는 않는다.

## 문제와 수정

1. 첫 Astra 실행은 집기 성공 뒤 설치 간섭을 발견했다. 복구 모델에 전달할 공유 이력이 13,781토큰으로 늘어나 설정된 8,000토큰 한도에 막혔다. 복구 생성 요청은 발송되지 않았다.
2. 새 tool-result 메시지에서 시뮬레이터 복원 전용 controller/settled 배열을 제외하고 원본 파일·이벤트·해시를 남겼다. 기존 대화는 삭제하지 않는다. 첫 실패 도구 결과는 8883→5423자로 38.95% 감소했다. 충돌 부위·위치·깊이·판정·출처는 원문과 동일하다.
3. 요청당 입력 한도는 20,000으로 조정했다. 전체 승인 $1.50, 60,288토큰, 생성 6회/조회 6회는 늘리지 않았다. 첫 회차 사용량을 차감해 두 번째 회차는 생성/조회 각각 최대 4회, 54,319토큰, $1.4160875, 실행 1,400초만 배정했다. 초기 장면부터 다시 만든 별도 회차이며 앞선 실패를 덮어쓰지 않았다.
4. 물리 성공 기준, 원래 CAD 목표, 케이스/그리퍼 형상, 중력 및 접촉 설정은 유지했다. 이전 오프라인 보완(수치 정밀도·공급 받침대·운반 여유·설치 자세 검사)은 STEP_34에 별도로 기록되어 있다.

## 실제 실행 결과

| 회차 | run ID | 상태 | Astra 생성 | 입력 조회 | 설치 | 비용 추정 USD |
|---|---|---|---:|---:|---:|---:|
| 1 | `run-25e73360` | failed | 1 | 2 | 0/12 | 0.0839125 |
| 2 | `run-5429e185` | failed | 3 | 3 | 0/12 | 0.4433395 |

합계 생성 4회, 입력 조회 5회, 비용 추정 **$0.5272520**. 입력 40803 / 출력 1654토큰. 청구서 확정 금액이 아니라 프로젝트 설정 단가에 실제 응답 사용량을 적용한 값이다.

### 판단과 물리 검증 기록

#### run-25e73360
원래 목표 안정성: passed; 준비: reference_and_initial_stability_passed; audit: passed.
종료 원인: FailureEvent: Counted input exceeds the per-request limit.

- `run-25e73360:event:1`: assembly / pick / 부품 3
  - Astra 이유: 부품은 지지된 상태로 거의 정지해 있고 그리퍼는 비어 있습니다. 대향면·개구폭 검사를 통과하고 자세 변화 추정이 작은 상부 후보를 선택합니다. 파지 및 원래 CAD 목표에서의 설치 가능성은 호스트 검증이 필요합니다.
  - 파지: {'mode': 'candidate', 'candidate_id': '3:r1:g117'}
  - 복사 파지 passed, 해당 파지 설치 failed; 주 장면 동작 False; 실제 설치 확인 False.
  - 실패: PLANNED_CONTACT / install_endpoint_check/preflight / No collision-free installation endpoint found in bounded IK search; no motor command executed.
  - 접촉: body:gripper_base/geom:33 ↔ assembly_base_collision_0, 겹침 11.858641mm.

#### run-5429e185
원래 목표 안정성: passed; 준비: reference_and_initial_stability_passed; audit: passed.
종료 원인: FailureEvent: No complete request reservation fits the remaining episode budget.

- `run-5429e185:event:1`: assembly / pick / 부품 3
  - Astra 이유: 부품은 지지된 정지 상태이고 그리퍼는 비어 있습니다. 접촉 높이가 약 10.64 mm이고 자세 변화 추정이 0.016 rad인 상부 후보를 선택합니다. 충돌·파지·원래 CAD 목표 설치 가능 여부는 호스트 검증이 필요합니다.
  - 파지: {'mode': 'candidate', 'candidate_id': '3:r1:g117'}
  - 복사 파지 passed, 해당 파지 설치 failed; 주 장면 동작 False; 실제 설치 확인 False.
  - 실패: PLANNED_CONTACT / install_endpoint_check/preflight / No collision-free installation endpoint found in bounded IK search; no motor command executed.
  - 접촉: body:gripper_base/geom:33 ↔ assembly_base_collision_0, 겹침 11.858641mm.
- `run-5429e185:event:2`: recovery / pick / 부품 3
  - Astra 이유: 복사 검증에서 인양은 통과했지만 설치 끝점에서 그리퍼 베이스와 부품 0이 약 11.86 mm 겹쳤습니다. 메인은 미실행이고 부품 자세는 유지되었습니다. +Y 쪽 충돌 위치에서 몸체를 멀리하는 -Y 쪽 사선 후보 g52로 변경해 원래 CAD 목표에서 재검증합니다.
  - 파지: {'mode': 'candidate', 'candidate_id': '3:r2:g52'}
  - 복사 파지 failed, 해당 파지 설치 not_run; 주 장면 동작 False; 실제 설치 확인 False.
  - 실패: IK_NOT_CONVERGED / approach_pregrasp / No checked endpoint/path in the bounded multistart search; no physical command executed.
- `run-5429e185:event:3`: recovery / pick / 부품 3
  - Astra 이유: 복사 검증에서 사선 g52는 사전 접근 IK가 미수렴했고, g117은 인양 후 설치 끝점에서 부품 0과 충돌했습니다. 메인 상태는 그대로입니다. g117보다 TCP가 로컬 -Y로 약 19.4 mm 이동한 상부 후보 g51로 변경해 접근 가능성과 원래 CAD 목표에서의 간섭 감소를 검증합니다.
  - 파지: {'mode': 'candidate', 'candidate_id': '3:r3:g51'}
  - 복사 파지 passed, 해당 파지 설치 failed; 주 장면 동작 False; 실제 설치 확인 False.
  - 실패: PLANNED_CONTACT / install_endpoint_check/preflight / No collision-free installation endpoint found in bounded IK search; no motor command executed.
  - 접촉: body:gripper_base/geom:33 ↔ assembly_base_collision_0, 겹침 13.026212mm.

## 기록 및 재현

- `comparison.json`: 두 회차의 판단·파지·접촉·사용량·원본 보존 검사.
- 각 회차 `run/observations.jsonl`, `events.jsonl`, `conversation.jsonl`, `llm_events.jsonl`: 관측·실행·공유 이력·모델 요청 응답. 모델 원문에는 이미지와 추론 상태가 있으므로 콘솔에 통째로 출력하지 않는다.
- 각 회차 `run/failures/`: 검출 상태 및 정면/측면 그림. `planner_candidate`를 실제 실행 영상으로 해석하지 않는다.
- 각 회차 `run/media/execution.mp4`: 주 장면에서 저장된 프레임. 복사 검사의 움직임을 주 장면 조립으로 보여주지 않는다.
- 각 회차 `run/report.html`, `audit.json`, `replay_bundle/`: 결과와 검증·재생 자산. 실제 파일 존재 여부는 실행 결과와 함께 확인한다.

## 실행 방법

기존 로컬 UI: `http://127.0.0.1:8768`. 최근 실행 목록에서 위 run ID의 결과를 연다. 입력은 다음 파일이다.

```text
F:/dev/llm-robot-assembly-data/inputs/lab_six_cell_battery_20261125_01/assembly_hint.json
```

Astra 실행은 공통 명령 `scripts/run_assembly.py run --input <입력> --output <새 폴더> --policy astra --condition R0 --llm-config <승인 잔액 설정> --seconds <남은 시간>`을 사용했다. 이번 회차를 끝낸 설정으로 새 예산처럼 반복 실행하지 않는다. 키는 코드·설정·기록에 저장하지 않고 실행 프로세스 환경에만 주입했다.

## 검증 범위와 남은 항목

- 현재 결과가 전체 12부품 실제 조립 성공을 뜻하지 않는다. 각 회차 installed와 최종 물리 판정이 기준이다.
- 복사 검사 성공만으로 해결 경험을 등록하지 않는다. 이 제품의 해결→지침 등록→다음 실행 재사용은 등록 목록의 실제 결과로만 판단한다.
- 얇고 긴 손가락/전용 공구는 해결 대안이며 이번 실제 2F-85 검증을 대신하지 않는다. 공구 변경·재파지·나사 체결·전기 연결·실물 로봇은 구현 성공으로 주장하지 않는다.
- 원본 13부품 CAD와 분해 결과를 수정하지 않았다. 원시 STEP에서 분해를 재실행한 것이 아니라 전달된 assembly_hint 입력에서 공통 준비부터 실행했다.
- 새 테스트 2개 포함 v2 검사 52개 통과. 실패 근거 정확 보존·원본 불변·체크포인트 참조 연결을 검사했다. 물리 실행 결과는 위 실제 run으로 별도 기록한다.

관련 이전 기록: `docs/STEP_34_BATTERY_AUTONOMY.md`.
