# 배터리팩 검사 시간 단축

2026-11-25. 사용자의 시간 단축 요청에 따라 실제 Astra 회차가 종료된 뒤 수정하고, 같은 저장 상태로 비교했다.

## 결과

**13부품 완성 기준 장면에서 12개 이동 부품을 매 순간 함께 확인한 1초/501회 연속 검사가 239.844초 → 116.100초, 2.07배 빨라졌다.**
두 방식 모두 passed이며 501개 모든 적분 상태, 각 시점의 측정값 해시, 전체 완료 근거가 동일했다. 고정 베이스·중력·마찰·시간 간격·접촉 형상·완료 기준은 바꾸지 않았다.

| 비교 범위 | 기존 | 수정 | 개선 |
|---|---:|---:|---:|
| 완성 기준 장면 2→3초, 501회 관측 | 239.844 s | 116.100 s | 2.07배 |
| 실제 인양 상태에서 200 step 유지, 3회 중앙값 | 7.354 s | 5.246 s | 1.40배 |
| 같은 완성 프레임의 12부품–그리퍼 거리 계산만, 3회 중앙값 | 0.258044 s | 0.005129 s | 50.31배 |
| 준비된 입력으로 run 시작 시 의미 검증 횟수 | 2 | 1 | 중복 제거 |

짧은 인양 유지 비교의 기존 측정은 [2.9742807999718934, 7.3542309000040404, 7.911040999984834]초, 수정은 [5.014514400041662, 5.246057700016536, 5.59998119994998]초로 벽시계 편차가 있었다. 연속 관측 비교의 CPU 시간도 224.594초 → 109.453초였다.
거리 계산의 50배를 전체 조립 50배로 주장하지 않는다. 전체 Astra 회차는 수정 후 다시 실행하지 않았고, 전체 소요 시간·완주 여부는 아직 미검증이다.

## 왜 오래 걸렸나

- 유료 회차에는 충돌 형상 1,165개가 있었고, 물리 상태는 0.002초마다 검사한다. 실제 엔진 계산에 더해 모든 접촉과 다른 부품의 보존 상태를 Python에서 확인한다.
- 짧은 저장 상태 프로파일 30 step에서 전체 0.480초 중 contacts 약 0.215초, preserve 약 0.173초였다. 이는 한 구간의 계측이며 전체 회차 비율이 아니다.
- 준비된 입력을 CLI와 실행기가 연속으로 다시 검사했다. 직전 회차에서 실행기 진입 전 경과는 약 198.1초였다. 이 중복 경로를 제거했지만 남은 한 번의 원본 의미·해시 검증은 유지한다.
- 원래 물리 확인은 집기만으로 끝나지 않는다. 복사 장면에서 집기·설치를 확인한 후 주 장면을 움직이고 해제 후 안정성을 검사하므로 LLM 응답 시간보다 길 수 있다.

## 수정한 부분

1. `entry.py`: 준비만 하는 명령은 CLI가 검사한다. run은 실행기에서 한 번 검사한다. 실행기를 직접 호출해도 검사하며, 잘못된 입력은 물리/API 전에 차단한다.
2. `transforms.py`, `verification.py`: 같은 프레임의 여러 부품 자세 오차를 배열로 묶어 계산한다. 유한값·강체 행렬 검사는 기존 허용 오차로 그대로 수행하고, 기존 순서의 첫 위반을 반환한다.
3. `verification.py`: 거리 검사의 구체 경계 계산을 배열로 처리한다. 현재 프레임의 위치를 매번 새로 읽고, 가까울 가능성이 있는 모든 쌍은 기존 MuJoCo 정밀 거리 검사로 확인한다. 반올림 여유는 정밀 검사를 더 수행하는 방향으로만 적용한다.
4. `skills.py`: 시간 초과·내부 오류는 unknown, 실제 접촉/IK 위반은 failed로 구분한다. 이전 유료 회차의 원본 결과와 봉인된 코드는 수정하지 않았다.

## 검증

- v2 테스트 **64개 통과**: `tests_v2.txt`.
- 무작위 128개 강체 자세의 단건/배치 오차 대조, 잘못된 행렬 거절, 같은 시각에 위치를 바꾼 거리 검사, 시간 초과와 실제 실패 구분, 입력 검사 경로를 확인했다.
- 실제 인양 저장 상태에서 200 step씩 각 버전 3회 실행: 모든 적분 상태 비트 동일, 마지막 관측과 접촉 근거 해시 동일.
- 실제 완성 기준 저장 상태 2→3초에서 501회×12부품 관측: 모든 측정값·적분 상태·완료 근거 동일, 두 버전 passed.
- 이전 유료 회차 replay_bundle 무결성 재확인, 기록 audit passed. 기록 검사 통과를 조립 성공으로 바꾸지 않는다.
- 위 성능 진단은 추가 API **0회, $0**이며 새 조립 성공/경험 수집 회차가 아니다.

## 실제 Astra 회차는 별도로 보기

`run-c7b84be2`: 실제 Astra high 2회, $0.330675 추정, 주 설치 0/12, deadline.
첫 파지의 실제 인양은 통과했지만 설치 종점에서 손가락 지지대와 케이스가 충돌했다. Astra가 +1.5 mm/수직 방향 수정안을 냈으나 시간 한도로 재검사가 끝나지 않았다.
이 회차 설명은 [STEP 37](STEP_37_BATTERY_ASTRA_TOOL_TRIAL.md)에 있다. 성공 경험 등록 0건이다.

## 파일과 실행 명령

결과 폴더: `F:/dev/llm-robot-assembly-data/runs/lab_battery_speed_20261125_01`.
핵심 결과: `benchmark.json`, `reference_window_comparison.json`, `reference_baseline.json`, `reference_optimized.json`, 각 `reference_*_states.npz`, `preservation.json`.
변경 파일 해시: `changed_code_sha256.json`. 실행 전 검사 코드: `baseline_verification.py`.

아래 명령은 API 없이 저장된 상태를 다시 비교한다. 새 출력 이름을 사용한다. 전체 조립 재실행 명령과 UI는 STEP 37에 있다.

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B 'F:/dev/llm-robot-assembly-data/runs/lab_battery_speed_20261125_01/diagnostics/benchmark_assembly_speed.py' 'F:/dev/llm-robot-assembly-data/runs/NEW_speed_hold_check'
& $assemblyPython -B 'F:/dev/llm-robot-assembly-data/runs/lab_battery_speed_20261125_01/diagnostics/benchmark_reference_window.py' 'F:/dev/llm-robot-assembly-data/runs/NEW_speed_reference_check'
```

다음 큰 병목은 남아 있는 접촉 순회와 실제 로봇 동작 검증이다. 다음 실제 Astra 시험에서 수정 파지의 설치 가능성과 전체 소요 시간을 다시 확인해야 한다. 시간을 줄이려고 관측 간격이나 충돌 기준을 느슨하게 하지 않았다.

branch `codex/general-mesh-v2`, HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f`. 기존 미커밋 작업 보존, commit/push하지 않았다. E 드라이브·분해 원본·실물 로봇에는 접근/명령하지 않았다.
