# STEP 45 · Astra의 짧은 동작 계획: 실제 호출과 물리 비교

2026-11-25. `codex/general-mesh-v2`, 기준 HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f` 위의 미커밋 작업. 기존 사용자 변경과 원본 실험을 보존했다. 이번 결과는 배터리 전체 조립 완주나 독립 평가 결과가 아니다.

## 무엇을 확인했나

셀 5를 실제로 잡았지만 100mm 들기 목표의 IK 탐색에 실패했던 상태를 두 번 똑같이 복원했다. 첫 조건은 Astra가 `lift`만 선택하고 공통 실행기가 높이를 정한다. 두 번째는 Astra가 제한된 범위 안에서 들기 높이를 직접 바꾼다. 두 조건 모두 실제 MuJoCo의 접촉·마찰로 부품을 들었다.

공통 조건은 Astra high, 한 번의 생성, 같은 관측·목표 이미지·도구·최대 예산, 같은 모델·원 상태·제어기·미끄럼 기준이다. 바뀐 것은 계획 수정 권한이다. 모델이 만든 높이를 통과시키기 위해 물성·목표·힘·충돌·성공 기준을 바꾸지 않았다. 이미 잡힌 체크포인트에서 시작하므로 새 파지 생성 능력을 측정하는 시험도 아니다.

이 시험은 공통 정책·실행기를 직접 호출하는 단일 동작 개발 도구를 사용했다. 새 LangGraph 전체 조립 회차나 경험 수집은 실행하지 않았다. 두 실제 API 조건의 코드와 해시는 `live_probe01/code_at_execution/manifest.json`에 보관했다. 그 뒤에는 복구 횟수 제한·사용량 표시·평가 설정·기존 기록 호환성을 보완하고 회귀 검사를 수행했다.

## 실제 Astra 결과

| 항목 | fixed: 기존 실행기 선택 | bounded: Astra의 제한된 수정 |
|---|---:|---:|
| run ID | `motion-probe-9799891d` | `motion-probe-a39bac4c` |
| 모델의 제안 | `lift`, 계획 null | `lift`, 높이 **65mm** |
| 실제 실행 높이 | 기본 100/80/65mm 탐색 중 **80mm** | 모델이 지정한 **65mm** |
| 실제 결과 | `lift_verified` | `lift_verified` |
| 처리 시간 | 171.891초 | 117.703초 |
| 계획된 이동의 시뮬레이션 시간 | 12.767초 | 7.151초 |
| 입력 토큰 | 3,413 | 3,406 |
| 출력 토큰 | 225 | 273 |
| 출력 중 추론 토큰 | 113 | 125 |
| 캐시 읽기 입력 토큰 | 0 | 1,181 |
| 생성 호출 | 1 | 1 |
| 설정 단가에 따른 비용 추정 | $0.053575 | $0.042306 |

Astra의 실제 제안 요지: 양쪽 접촉과 파지가 유지되고 있으므로 다시 집지 않고, IK 탐색이 실패한 100mm 대신 65mm만 들어 본다. 실행기는 실제 들기·접촉·미끄럼·바닥 이격을 검사했다. 65mm 조건의 마지막 바닥 이격은 약 64.7mm로, 원래 50mm 이상 조건을 만족했다.

**생성 2회, 입력 토큰 계수 요청 2회, HTTP 요청 합계 4회**다. 추가 비용 추정 합계는 **$0.095881**, 미정 사용량은 0건이다. 실제 청구서와 대조한 비용은 아니다. 이 시험에 알린 최대 $1.50 범위 안에서 끝났으며 자동 재호출은 없었다.

한 장면을 조건별 한 번만 실행했다. 수정 조건은 약 31.5% 빨랐으나 전체 제품의 속도 개선률로 해석하지 않는다. 실제 비용 차이에는 캐시 사용량 차이가 섞여 있다. 캐시 없이 동일한 입력 $10/출력 $50 per 1M 단가만 적용한 비교는 $0.04538 대 $0.04771로, 이 경우 수정 조건이 약 5.1% 높다. 토큰 합계는 3,638 대 3,679로 41개 증가했다. 따라서 더 넓은 판단 권한이 항상 더 저렴하다는 결론은 내리지 않는다.

기존 fixed 조건도 공통 100/80/65mm 대안을 가진 실행기다. 비교를 위해 기존 자동 복구를 고의로 끄지 않았다.

## 실행기·계획·기록 보완

- [STEP 43](STEP_43_EXECUTION_STABILITY.md): 같은 프레임의 반복 자세 계산을 줄이고, 해제 후 후퇴 중 시간 소진을 검증 복원해 이어간다. 짧은 250단계 계측은 3.266→2.889초였고 최종 적분 상태 해시가 같았다.
- [STEP 44](STEP_44_BOUNDED_MOTION_PLAN.md): 접근 거리·들기 높이·최대 두 운반 중간점·설치 접근 여유/방향·기존 해제 방식을 제한된 계약으로 전달한다. 실행기와 복사 시험에 같은 계획을 적용한다.
- 새로운 들기 복구는 부품별 1회로 제한한다. 조건별 모델·공유 이력·도구·총예산은 공통이다.
- 모델 사용량은 응답 직후 저장한다. 물리 계산이 길어도 화면/결과에서 호출 횟수가 계속 0으로 보이는 문제를 줄였다.
- 수집·평가 계획에도 `motion_plan_mode`를 고정한다. 평가 시 다른 모드로 실행하면 거절한다.
- `retries`는 기존 파지 후보 재시도 지표를 유지한다. 들기 제안과 실제 실행 횟수는 별도로 기록한다. 과거 기록에 새 지표가 없더라도 원본을 다시 쓰지 않고 과거 지표 자체는 엄격하게 대조한다.
- 해결 지침에 동작 계획을 저장하려면 실제 실행한 계획과 사건 ID가 일치하고, 원래 실패 및 주 장면의 설치 완료 근거가 있어야 한다. **이번 들기 비교는 설치하지 않았으므로 새 해결 지침을 등록하지 않았다.**

마지막 코드 검사는 **107개 통과, 9.508초**다. 기본 실행 권한에서는 설치된 `colorama` 읽기가 차단돼 2개 테스트 모듈을 읽지 못했으며, 프로젝트 환경 접근 권한으로 다시 실행해 전체를 확인했다. 접근 문제를 코드의 물리 실패로 기록하지 않았다.

## 현재 배터리팩 진행률과 한계

별도 scripted 후속 조립 분기에서 설치 부품 **3, 4, 5, 6, 7, 10의 6/12개**를 같은 1초 관찰 창, 501샘플로 확인했다. 고정 케이스 0은 설치 개수에 포함하지 않는다. 이번 Astra 들기 비교는 그보다 앞선 셀 5 체크포인트의 독립 시험이므로 설치 개수를 추가하지 않는다.

완성 목표와 timestep **0.002초**, 중력·접촉·solver 설정, XYZ 각 축 5mm·상대 회전 3°·최종 경질 관통 0.5mm·속도·해제 후 연속 관찰 기준은 유지했다. 현재 모델은 이전에 기록한 실험용 연장 손가락이며 기본 출고형 2F-85나 실물 제작 가능성을 검증했다고 주장하지 않는다.

아직 확인하지 않은 것: 배터리 12개 전체의 새 Astra 연속 조립, 이번 계획 변경에 대한 새 해결 지침 생성→등록→다음 실행 재사용, 새로운 제품에서의 일반화 성능, 임의 프로그램 생성/재파지, 실물·나사·전기적 연결. 구현한 모든 계획 값과 방향의 물리 검증을 완료한 것도 아니다.

## 근거·영상

- 실제 비교: `F:/dev/llm-robot-assembly-data/research/20261125_motion_plan/live_probe01/comparison.json`.
- 각 조건의 `proposal.json`, `observation.json`, `result.json`, `source_evidence.json`, `llm_events.jsonl`, `planned_motions.jsonl`, `checkpoints/`.
- 실제 상태 영상: 같은 폴더의 `fixed/media/execution.mp4`, `bounded/media/execution.mp4`. 목표 이미지와 실제 실행 영상을 혼동하지 않는다.
- API 없는 선행 비교: `.../20261125_motion_plan/offline_probe01`. 고정 기본값 대 scripted 80mm, 두 조건 통과. 모델이 선택한 결과가 아니다.
- 최신 6/12 분기: `F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_retreat_completion01`.
- 원 들기 상태 SHA-256: `e8f0b7c483f1aa223292d41bfb989817ee49449f61717bdb7bf22d25e486abea`.
- 공통 MuJoCo 모델 SHA-256: `1a8787479e9ee1c9c3654f85fe712d089fbea7d8071079807aac19090ecd9adb`.

## 재현 명령

기록만 확인하려면 위 영상/JSON을 연다. 아래 명령은 결과 재생이 아니라 **새 실행**이므로 반드시 새 출력 폴더를 지정한다.

```powershell
cd C:/dev/llm-robot-assembly
$assemblyPython = './runtime/luna-venv/Scripts/python.exe'
& $assemblyPython -B -m unittest discover -s tests -p 'test_v2*.py'

# API 없이 두 조건의 실행기 확인
& $assemblyPython -B scripts/probe_motion_plans.py `
  --source-run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part05_attempt01' `
  --checkpoint 'checkpoints/offline-seq-b7e28b26_scripted_stopped_development_branch.json' `
  --output 'F:/dev/llm-robot-assembly-data/research/NEW_SCRIPTED_PROBE' `
  --policy scripted --seconds-per-condition 360

# 실제 Astra 재검증이 필요한 경우에만 사용: 새로운 유료 호출 2회
& $assemblyPython -B scripts/probe_motion_plans.py `
  --source-run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part05_attempt01' `
  --checkpoint 'checkpoints/offline-seq-b7e28b26_scripted_stopped_development_branch.json' `
  --output 'F:/dev/llm-robot-assembly-data/research/NEW_ASTRA_PROBE' `
  --policy astra --seconds-per-condition 360 --max-usd-per-condition 0.75
```

키는 기존 환경변수 또는 가려진 입력으로만 받는다. 문서·명령줄·로그에 값을 적지 않는다. 이 문서의 명령이 새 예산을 승인하는 것은 아니다.

## 마지막 추가 검사 · 바꾼 설치 계획의 복사 검증

API 없이 실제 셀 7 파지 체크포인트를 복원하고 설치 계획만 바꿨다. 목표 위 100mm 중간점, 월드 +Z 접근, 기하 최소 여유에 2mm 추가, 제한 개방 후 후퇴를 사용했다. 이 값은 **scripted 개발 제안**이며 Astra가 만든 설치 계획이 아니다.

- 검사 ID: `install-recipe-copy01:copy`.
- 설치 가능성 판정: **`unknown`**, 채택 여부 `False`.
- 처리 시간: **479.812초**, API 0회 / $0, 주 장면 물리 진행 0단계.
- 주 장면 적분 상태는 전후 정확히 일치(`main_state_unchanged=true`).
- 원 모델 SHA-256: `1a8787479e9ee1c9c3654f85fe712d089fbea7d8071079807aac19090ecd9adb`.
- 완료 관찰: `not_completed`, 샘플 `0`.

중단 근거: `TimeoutError` / `retreat_limited_open`. deadline:physics_step
시간·자원 소진은 물리적으로 설치가 불가능하다는 증거가 아니다. 바뀐 계획은 이번 시험에서 채택하지 않았으며 완료나 새 설치 개수로 세지 않는다.

검사 결과·계획·접촉/경로 근거: `F:/dev/llm-robot-assembly-data/research/20261125_motion_plan/install_copy01`의 `result.json`, `proposal.json`, `phases.jsonl`, `planned_motions.jsonl`, `diagnostic_evidence.json`. 이 검사는 복사 상태를 검사했고 주 장면 실행 영상은 새로 만들지 않았다. 정확한 진단 코드는 같은 폴더의 `diagnostic_source.py`에 보관했다.

재실행하려면 `diagnostic_source.py`의 `out`을 새로운 F: 폴더로 지정한 별도 복사본을 실행한다. API는 필요 없고 검사 시간은 480초다. 기존 폴더를 덮어쓰지 않으며 제품별 테스트 스크립트를 공통 조립 입력 계약으로 사용하지 않는다.
