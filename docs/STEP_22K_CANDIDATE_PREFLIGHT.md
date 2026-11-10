# 22K — Luna 스킬 후보의 현재 장면 MuJoCo 사전검사

작성: 2026-11-10. 범위: 실제 기준 파지의 거절 → 저장된 후보 계산 → 같은 시작 상태의 수정 파지 검사 → 결과 보존.

**연결을 구현하고 실제 MuJoCo에서 기준 파지와 +1mm 파지를 각각 검사했다. 두 파지 모두 집기는 성공했으나 삽입 경로 간섭으로 거절됐다.** 집은 자세에서 계산한 예상 패드 여유는 -3.37mm에서 -2.37mm로 바뀌었다. 이 한 장면에서는 +1mm가 간섭을 없애기에 부족했다. 생성 코드를 바꾸거나 성공한 스킬로 등록하지 않고 이 실패를 별도 경험 기록으로 남겼다.

## 1. 이번 단계가 하는 일

22J까지는 Luna가 만든 함수가 숫자를 계산할 수 있는지만 확인했다. 이번에는 그 숫자로 실제 시뮬레이션 검사를 실행한다.

1. 바닥판과 트레이에 부품이 있는 10부품 장면을 새로 만든다. 활성 부품은 첫 기둥 `post_1`, 설치된 부품 목록은 비어 있다.
2. 원래 장면에서 3초 동안 중력과 제어기를 동작시켜 물체가 작업대에 안정되는지 확인한다.
3. 비교 기준으로 기둥 중심의 파지 `X=0, Y=0, Z=0mm, 닫힘 각도=0°`를 정한다. 이것은 실험자가 지정한 기준이며 새 Luna 판단이라고 표기하지 않는다.
4. 현재 물리 상태를 복사해 기존 집기·삽입 사전검사를 수행한다. 실제 접촉과 마찰로 집고 들어 올린 뒤 이동한다. 삽입 전 검사에서 발견한 실패를 현재 관측에 연결한다.
5. 원래 장면을 다시 관측한다. 상태가 같은지 확인하고, 기존 22I의 Luna 후보 원문을 22J 제한 계산기로 실행한다.
6. 후보가 반환한 파지 네 값을 수정 없이 기존 `check_grasp()`로 보낸다. 같은 시작 상태의 새 복사본에서 다시 집기·삽입 검사를 수행한다.
7. 두 결과, 접촉한 형상, 실패 시점, 계산의 근거, 후보 코드 식별값을 저장한다. 후보가 실패해도 기록을 남긴다.

스킬 후보는 **파지값을 계산하는 코드**다. 로봇 관절 명령을 직접 실행하지 않는다. 우리가 작성한 연결부가 입력·출력을 검사하고, 기존 로봇 실행기가 IK·경로 검사·제어를 담당한다. 이번에 새 API로 판단받지 않았으므로 API 호출과 추가 API 비용은 0이다.

`+1mm`는 기둥 중심을 기준으로 한 기둥의 위쪽 Z 방향이다. XY와 그리퍼 닫힘 각도는 그대로다. 도구 좌표의 Z 방향이나 매 이동 중간점의 보정값이 아니다.

## 2. 실제 비교 결과

| 항목 | 기준 파지 | 저장된 후보의 수정 파지 |
|---|---|---|
| 파지 위치 X/Y/Z (mm) | 0 / 0 / 0 | 0 / 0 / 1 |
| 닫힘 각도 | 0° | 0° |
| 접촉·마찰에 의한 집기/들기 | 성공 | 성공 |
| 삽입 경로 검사 | 거절 | 거절 |
| 실패 코드 | `PLANNED_CONTACT` | `PLANNED_CONTACT` |
| 실패 단계 | `insert_lower/preflight` | `insert_lower/preflight` |
| 간섭 형상 | `gripper_right_pad1` / `assembly_base_wall_0_1` | `gripper_right_pad1` / `assembly_base_wall_0_1` |
| 집은 자세로 예측한 패드의 구멍 테두리 여유 | -3.37 mm | -2.37 mm |
| 복사 장면 종료 시각 | 51.126 s | 51.064 s |
| 원래 장면의 조립 실행 | 하지 않음 | 하지 않음 |

예상 여유가 음수라는 것은 현재 잡은 관계가 유지된 채 기둥을 명목 깊이까지 끼우면 패드의 가장 낮은 부분이 테두리 높이보다 아래에 온다는 뜻이다. **이 값은 집기 직후 기하를 삽입 완료 자세에 투영한 진단값이다. 실제 삽입 완료 상태에서 측정한 여유가 아니다.** 1.00mm 증가했지만 여전히 음수였고, 실제 경로 검사도 거절했다.

집기는 두 경우 모두 통과했으므로 집기 성공률 개선을 입증한 것이 아니다. 삽입도 두 경우 모두 거절했으므로 복구 성공으로 세지 않는다. 한 조건의 비교만 수행했으며 여러 부품·장면의 성능이나 성공률을 추정하지 않는다.

`PLANNED_CONTACT`는 **내려가기 전에 검사한 후보 경로의 예상 간섭**이다. 실제 그리퍼를 바닥판 벽에 충돌시키고 충격력을 측정한 실험은 아니다. 그 이전의 접근·집기·들기·운반은 복사 장면에서 물리 시간과 함께 실행했다.

첫 검출 지점의 signed distance는 각각 -0.6766mm, -0.6614mm로 기록했다. 두 값은 서로 다른 경로 표본에서 처음 검출한 겹침이다. 이를 나눠 ‘충돌이 몇 % 감소했다’고 보고하지 않는다. 실패 후보의 자세와 실제로 도달했던 복사 장면의 상태도 별도 파일로 구분했다.

## 3. 현재 장면과 계산 결과를 연결하는 검사

- 기존 경험의 실패를 새 실패로 가장하지 않고, 이번 기준 검사 결과를 현재 관측과 묶는다.
- 후보 ID·코드 hash·회차·부품·목표·관측 번호·실패 ID가 일치해야 한다.
- 전달된 관측이 실제 검사할 실행기의 장면과 같은지 직접 관측해 대조한다. 외부 reader가 예전 값을 계속 반환하는 경우도 거절한다.
- 제한 계산 전후와 사전검사 직전에 상태 변경을 확인한다. 관측 번호만 늘고 상태가 그대로라면 앞선 기준 거절을 사용할 수 있다.
- 출력은 정확히 네 유한 숫자여야 하며 부품 경계와 각도 범위를 지켜야 한다. 범위를 벗어난 값을 임의로 잘라 쓰지 않는다.
- 원래 장면의 MuJoCo 통합 상태, 공유 model, 설정·설치 목록·실행기 상태를 전후 해시로 비교한다.
- 기록 저장에 오류가 나면 정상 완료로 표시하지 않는다. 치명적 시뮬레이터/제어 오류도 일반 파지 거절로 바꾸지 않는다.

장면을 직렬로 소유하는 이 개발 runner를 대상으로 한다. 여러 프로그램이 동시에 같은 장면을 변경하는 환경을 위한 잠금 장치를 구현한 것은 아니다. 해시는 변경 감지 표식이며 관측의 진실성을 보장하는 전자서명은 아니다.

## 4. 실패도 경험으로 남기는 방법

실행 폴더: `runs/step22k_candidate_preflight/20261110T093953_964326Z_95559297e37b`.

| 파일 | 의미 |
|---|---|
| `candidate_snapshot.json` | 사용한 원래 후보와 그 교훈·근거 사본 |
| `baseline_observation.json`, `candidate_observation.json` | 기준 검사와 후보 계산 때의 현재 관측 |
| `baseline_rejection_binding.json` | 현재 관측과 실제 기준 거절의 연결 |
| `candidate/evaluation.json` | 제한 계산 입력 검사·실행 정보·원문 코드 hash·계산 출력 |
| `candidate/submitted_grasp.json` | 물리 검사에 실제 전달한 파지값 |
| 각 검사 폴더의 `trial_results.json` | 실제 반환된 집기 등의 완료 결과 |
| `samples.jsonl`, `stages.jsonl` | 복사 장면의 물리 측정과 단계별 시간 |
| `failure_events.jsonl` | 거절한 경로 후보 자세와 접촉 형상·위치·거리 |
| `check_failure.json`, `terminal.json` | 최종 실패와 검사 종료 기록 |
| `source_before/after…json` | 원래 상태 보존 확인 |
| `comparison.json`, `experience.json` | 기준과 수정 비교, 후보·관측·실패로 돌아갈 수 있는 연결 |
| `result.json`, `implementation_hashes.json` | 회차 전체 결과와 물리 실행 당시 소스 식별값 |

`comparison_recorded`는 **비교와 저장이 완료됐다는 상태**다. 파지 성공 상태가 아니다. 후보 검사 상태는 `preflight_rejected`, 비교 결과는 `candidate_still_rejected`다.

새 경험은 후보 평가용 별도 기록이다. 기존 `grasp_v2` 검색 DB에 자동 반입하거나 Lesson을 자동 수정하지 않는다. 생성 당시 후보·교훈의 과거 기록도 덮어쓰지 않는다. 실행 기록과 후보 원문은 계속 Git에서 제외한다.

## 5. 검증 범위

- 새 연결부 검사 21개, 기록 모듈 검사 6개: 모두 통과. 모의 경계 검사와 실제 물리 결과를 구분한다.
- 기존 VM·호스트·후보 검사 64개, 후보 생성 검사 8개, 교훈 검사 35개: 모두 통과(107개).
- 실제 MuJoCo 3.17.0 비교 1회: 기준과 후보 총 2개의 물리 사전검사. 양쪽 모두 집기 성공 후 삽입 전 간섭으로 거절.
- 원래 장면은 3초 안정화 후 두 검사 내내 같은 상태였다. 원래 장면 설치 목록은 `[]`, 본 조립 실행은 0회.
- 이전 자료 68개와 후보 원문 보존 확인. API 호출·추가 비용 0.
- Luna 코드 hash: `92418c389b6d2a178cbfffe978a84babd93b835ccb1cc3b29d4e551ca371b2a8`. 이번에 +1mm를 다른 값으로 고치지 않았다.

물리 실행의 정확한 소스 범위는 해당 폴더의 `implementation_hashes.json`을 따른다. 그 실행 이후 실제 실행기와 관측의 직접 대조 및 로그 쓰기 실패의 완료 차단을 보강하고 단위 검사로 확인했다. 이 보강 뒤 물리 비교 2회를 반복한 것은 아니다. 기존 로봇 제어·IK·경로 검사·Luna 후보 원문은 바꾸지 않았다.

## 6. 실행 명령

```powershell
cd C:\dev\llm-robot-assembly
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -u scripts/run_grasp_candidate_preflight.py --candidate runtime/skill_candidates/simulation/post_grasp_v1/96fb203da360bcc6ca2b876d25a5206986fcf1fc44281003ad6a039026003bee.json
```

화면 viewer 없이 검사하며 단계별 진행을 출력한다. 예상 결과는 `status: comparison_recorded`와 `candidate_still_rejected`다. CLI 종료 코드 0도 비교 기록 완료를 뜻한다. 기준 실패가 예상 조건과 다르거나 후보가 보류하면 그대로 기록하고 종료 코드 1로 끝낸다. 원래 후보·교훈·경험은 Git에서 제외돼 있어 새 clone에는 이전 단계 자료를 먼저 준비해야 한다.

```powershell
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_grasp_candidate_preflight.py -q
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_grasp_preflight_trace.py -q
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_grasp_skill*.py' -q
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_generate_grasp_skill_candidate.py -q
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_*grasp_lesson*.py' -q
```

## 7. 코드에서 볼 곳과 다음 한 단위

- `scripts/grasp_candidate_preflight.py`: 현재 관측·후보·제안 바인딩, 물리 검사 연결, 원래 상태 보존, 비교 지표.
- `scripts/grasp_preflight_trace.py`: 기존 검사 함수를 그대로 호출하며 단계·측정·실패 후보를 기록.
- `scripts/run_grasp_candidate_preflight.py`: 10부품 장면의 첫 기둥에서 기준→후보 비교를 실행하는 작은 재현 프로그램.

이번 결과는 ‘스킬 코드가 실행된다’와 ‘그 스킬이 효과가 있다’가 다른 문제임을 보여준다. 다음 한 단위는 **이 새 실패 근거를 사용해 후보를 수정하거나 보류하도록 하는 것**이다. 수정 후보도 별도로 검증한 뒤에만 검증된 스킬로 승격해야 한다. 본 장면 조립 실행·등록·새 API 호출은 이번 단계에 포함하지 않았다.
