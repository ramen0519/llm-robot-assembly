# 실제 Astra 배터리팩 조립 시험

2026-11-25. 사용자 요청에 따라 새 Astra high 호출로 실행한 MuJoCo 개발 시험이다. 실물 명령은 실행하지 않았다.

**이번 결과: `deadline`, 주 장면 설치 0/12.**
run ID: `run-c7b84be2`. 원래 CAD 목표의 사전 안정성: `passed`.
이전 scripted 시험의 첫 홀더 1개 성공을 이번 Astra 실행 성적으로 합치지 않았다.

## 이번에 연결한 부분

- Astra에 실제 실험 손가락의 치수, TCP 연장, 벌림 폭별 측정 접촉 중심, 해제 방법과 미검증 범위를 전달했다.
- 제품 전용 정답 파지나 이전 성공 좌표를 주입하지 않았다. `--first-grasp`와 실패 유도 옵션은 사용하지 않았다.
- Astra가 현재 메시 후보를 고르거나 전체 pose를 제안하고, 호스트가 복사 장면 집기·설치 전체를 시험한 뒤에만 주 실행을 허용한다.
- R1: 조립·복구 역할이 같은 Astra high, 이력, 도구, 비용 한도를 공유한다. 메모리가 비어 있으면 검색 결과를 만들어 넣지 않는다.
- 실제 도구는 기본 2F-85 본체/링크에 길이 100 mm·폭 8 mm의 실험 손가락을 장착한 강체 모델이다. 제작/휨/허용 힘은 미검증이다.

## 실제 판단과 동작

| 순서 | 역할·부품·동작 | 복사 집기 / 설치 | 주 실행 | 설치 확인 |
|---|---|---|---|---|
| 1 | assembly / 3 / pick | passed / failed | False | False |
| 2 | recovery / 3 / pick | 미완료(시간 초과) / 설치 미실행 | False | False |

### 판단 1
제안: `{"mode": "candidate", "candidate_id": "3:r1:g184"}`.
모델이 반환한 짧은 이유: 부품이 지지되어 있고 양쪽 접촉은 없습니다. 간격 20 mm의 대향 외측면을 잡는 상부 후보를 선택합니다. 현재 TCP와의 자세 차이가 약 0.016 rad로 작습니다. 접근·파지·원래 CAD 목표 설치 가능성은 호스트 검증이 필요합니다.
검사 결과: `PLANNED_CONTACT` / `install_endpoint_check/preflight` — No collision-free installation endpoint found in bounded IK search; no motor command executed.
해당 상태·그림의 출처/해시: `TRIAL_SUMMARY.json`의 failure_snapshot 및 원본 failures/ 참조.

### 판단 2
제안: `{"mode": "pose", "frame": "part_local", "position_mm": [9.39929, -9.53741, 13.7828], "quaternion_xyzw": [0.7071067811865476, -0.7071067811865476, 0, 0], "opening_width_mm": 28, "parent_candidate_id": "3:r2:g184"}`.
모델이 반환한 짧은 이유: 복사본에서는 들어 올렸지만 설치 종점에서 백킹과 베이스가 0.478 mm 간섭했습니다. 본 장면은 미실행 상태이며 현재 부품 자세도 동일합니다. g184의 TCP를 1.5 mm 높이고 수직 하향으로 정렬해 상단 쪽을 잡도록 수정합니다. 원래 CAD 목표에서 접촉 유지와 설치 간섭을 재검증해야 합니다.
검사 결과: `TimeoutError` / `approach_pregrasp` — deadline:physics_step
해당 상태·그림의 출처/해시: `TRIAL_SUMMARY.json`의 failure_snapshot 및 원본 failures/ 참조.

시간 한도로 중단된 검사는 불가능성 증명이나 완료된 파지/설치 시험으로 해석하지 않는다. 복사 검사 통과와 주 장면 성공도 구분한다.

## 확인한 문제와 후속 수정

1. 첫 파지는 실제 인양을 통과했지만 목표 설치 자세에서 gripper_left_backing과 고정 케이스 0의 충돌이 검출되었다. 대표 검출 상태의 겹침은 약 0.477526 mm다. 로봇–제품 접촉은 허용되지 않는 접촉이므로 최종 제품끼리의 0.5 mm 허용 기준을 여기에 적용하지 않는다.
2. 복구 Astra는 해당 근거를 읽고 TCP를 1.5 mm 올리고 방향을 수직으로 수정했다. 원본 CAD 목표와 완료 기준을 바꾸지 않았다. 수정안의 물리 결과는 시간 초과로 미완료다.
3. 원본 event 2의 grasp_feasible은 당시 코드에서 failed로 저장되었지만 실패 코드는 TimeoutError다. 원본과 재생 묶음은 그대로 보존하고, 실행 종료 후 새 코드는 시간 초과/내부 오류를 unknown으로 구분하도록 수정했다. 이 보고서에서는 미완료로 해석한다.
4. 사용자의 시간 단축 요청에 따라 실행 종료 후 중복 입력 검사 제거, 같은 프레임의 부품 자세 계산 묶음 처리, 보수적인 거리 후보 필터를 추가했다. 주기·접촉·관통·속도·안정성 기준은 유지했다. 실제 전후 비교는 STEP_38 및 별도 속도 진단 폴더에 기록한다. 이 Astra 회차를 수정 후 새 실행으로 주장하지 않는다.

## 사용량과 한도

- 실제 모델 생성 2회, 입력 토큰 계산 2회.
- 입력 20972 / 출력 1384 / 추론 1041 tokens.
- 프로젝트 요율 기준 실제 응답 사용량 추정 비용 `$0.330675`. 청구서 대조는 하지 않았다.
- 이번 범위: 최대 $1.50, 모델 생성 6회, 입력 계산 6회, 총 100,000 tokens, 본 실행 1,800초 + 영상 후처리 최대 180초.
- 기록된 pipeline 시간: 1812.469000000041초. 키는 로그/설정/명령 인수에 저장하지 않았다.

## 경험·검색·증거

- 실행 시작 메모리 0건, 이번 등록 0건.
- 실패 사건은 events.jsonl에 남긴다. 해결·설치·최종 관측 근거가 부족하면 해결 경험으로 등록하지 않는다.
- report.html, events.jsonl, llm_events.jsonl, observations.jsonl, candidates.jsonl 및 replay_bundle에 출처를 보존한다.
- media/execution.mp4는 주 장면 저장 상태만의 영상이다. 주 실행이 없으면 정지한 공급 장면이며, 복사 검사 움직임을 섞지 않는다.
- failures/*.png는 실패 시점의 실제 복사 검사 또는 계획 후보 상태다. goal_top/goal_oblique는 목표 렌더이며 조립 실적이 아니다.

## 검증과 남은 제한

- 이번 코드 변경 후 v2 단위/통합 계약 테스트 60개 통과. 기존 11개 TCP/IK 검사는 이전 단계에서 통과했으며 이번에 재실행한 것으로 세지 않는다.
- 입력 13부품의 메시 해시·CAD 목표·완료 프로파일 보존 확인: preservation.json.
- XYZ 각 축 5 mm, 상대 회전 3°, 최종 경질 관통 0.5 mm, 해제 후 연속 관측과 속도 기준은 그대로다.
- 실행 중 코드/설정은 바꾸지 않았다. 아직 실제 Astra 전체 제품 조립 완료나 새 공구의 실물 사용 가능성을 주장할 수 없다.
- 준비 입력의 중복 의미 검사와 촘촘한 접촉 검사가 많은 시간을 사용한다. 읽기 전용 호출 위치 검사에서 contacts → 물리 step 경로가 확인되었다. 정확한 전체 시간 비율은 아직 측정하지 않았다.
- 기존 결과, 분해 프로젝트, 원본 CAD를 수정하지 않았고 E 드라이브와 실물 장비는 사용하지 않았다.

## 변경 파일

- scripts/assembly_v2/packets.py: 실측 도구 정보 packet.
- scripts/assembly_v2/execution.py: 매 판단의 관측에 tool_configuration 연결.
- tests/test_v2_tooling.py: 도구 정보, 보정 출처, 제품 정답/재시작 상태 미포함 검사.
- docs/STEP_37_BATTERY_ASTRA_TOOL_TRIAL.md: 이 보고서.

branch `codex/general-mesh-v2`, 시작 HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f`. 기존 미커밋 변경 보존. commit/push하지 않았다.

## 실행 방법

아래는 이번 실행과 같은 설정을 재현하는 명령이다. 새 유료 실행이므로 이 보고서 작성 중 다시 실행하지 않았다. 키는 해당 터미널의 OPENAI_API_KEY에 설정하고, 새 출력 폴더를 사용한다.

```powershell
cd C:\dev\llm-robot-assembly
& '.\runtime\luna-venv\Scripts\python.exe' -B scripts/run_assembly.py run `
  --input 'F:\dev\llm-robot-assembly-data\runs\lab_battery_tool_options_20261125_01\end_contact_prepared\prepared.json' `
  --output 'F:/dev/llm-robot-assembly-data/runs/NEW_astra_tool_trial' `
  --policy astra --condition R1 --split development `
  --llm-config 'F:/dev/llm-robot-assembly-data/runs/lab_battery_astra_tool_20261125_01/llm.astra-high.toml' --seconds 1800
```

확인 명령:

```powershell
& '.\runtime\luna-venv\Scripts\python.exe' -B scripts/run_assembly.py audit --run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_astra_tool_20261125_01/run'
```

결과 폴더: `F:/dev/llm-robot-assembly-data/runs/lab_battery_astra_tool_20261125_01`.

## 실행 후 시간 단축 작업

속도 수정 후 v2 테스트 64개 통과. 501회 실제 관측 비교 239.844초 → 116.100초, 판정·측정·물리 상태 동일. 전체 유료 조립 재실행은 하지 않았다. STEP_38_BATTERY_VERIFICATION_SPEED.md 참조.

결과 화면: http://127.0.0.1:8768/results/8098ceba0dd80e58c1dd0d0c2a13c427/report.html

주 장면 영상(설치 동작 없음): http://127.0.0.1:8768/results/8098ceba0dd80e58c1dd0d0c2a13c427/media/execution.mp4
