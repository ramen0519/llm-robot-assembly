# STEP 46 · 물리 계산 간격 0.002초와 0.004초 비교

2026-11-25. 사용자의 계산 간격 확대 질문에 대한 별도 API 없는 진단이다. 기본 실행기·완료 프로파일·제품 원본·기존 실행 자료는 변경하지 않았다. 브랜치 `codex/general-mesh-v2`, HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f` 위의 미커밋 진단 도구/보고서다.

## 결론

0.004초는 짧은 유지 시험에서 계산 시간을 줄였고 기존 실행 중 접촉·파지·부품 보존 검사도 통과했다. **더 큰 간격을 시험할 근거는 생겼지만, 전체 조립의 기본값을 바꿀 검증까지 끝난 것은 아니다.** 현재 실행기는 계속 0.002초다.

## 방법과 실제 결과

실제 상태 두 개(셀 5를 잡아 든 상태, 셀 7을 놓고 후퇴하던 상태)를 인증된 모델·적분 상태 해시로 복원했다. 각 조건에서 기존 관절 목표·그리퍼 명령을 고정해 시뮬레이션 1초씩 두 번 진행했다. 순서는 첫 반복 0.002→0.004, 두 번째 반복 0.004→0.002로 바꿨다. **총 8개 실제 MuJoCo 시험, API 0회, 비용 $0**이다.

| 장면 | 0.002초 평균 처리 시간 | 0.004초 평균 처리 시간 | 소요 시간 감소 |
|---|---:|---:|---:|
| 실제 파지 유지 | 10.816초 | 6.111초 | 43.5% |
| 해제 후 기존 제어 목표 유지 | 12.838초 | 7.346초 | 42.8% |

한 번에 진행한 시뮬레이션 시간은 두 조건 모두 1초다. 물리 계산은 각각 500회/250회다. 초기 관측을 포함한 측정은 501개/251개이며, 0.004초 결과를 501샘플 완료 검사로 가장하지 않았다. 표의 시간은 파일 저장·모델 로딩을 제외한 제어·물리·관측 구간이다. 반복별 시간이 흔들리므로 전체 조립이 같은 비율로 빨라진다고 추정하지 않는다.

셀 5는 두 조건 모두 양쪽 손가락 접촉을 유지했다. 같은 시각의 부품 위치 차이는 각 축 최대 약 0.00151/0.00175/0.01917mm, 회전 차이는 약 0.00346°였다. 최대 파지 상대 이동은 0.002초에서 0.1291mm, 0.004초에서 0.1482mm였다. 움직임은 동일하지 않지만 이 짧은 구간에서 기존 실행 검사를 위반하지 않았다.

셀 7 해제 후 상태는 두 설정 사이 위치 차이가 각 축 약 0.0000752/0.0000422/0.0000758mm, 회전 차이 약 0.000406°였다. 장면의 최대 경질 관통은 두 설정 모두 약 0.43454mm였다. 원래 접촉 역할·힘·관통·미끄럼·다른 부품 보존 검사와 native 경고 검사를 사용했다.

## 기본 설정을 바로 바꾸지 않은 이유

현재 `WorkcellMotion`은 0.002초 이하만 허용하고 완료 프로파일은 0.002초/1초/501샘플을 요구한다. 그 계약을 느슨하게 만들지 않았다. 진단은 기존 `ArmPositionController`와 `MeshVerifier`를 재사용하는 별도 원시 물리 루프이며, 메모리 등록·조립 완료 판정을 호출하지 않는다.

실험용 메모리 내 모델에서는 `opt.timestep`만 바꿨다. 계산과 제어 갱신 횟수도 같이 달라지므로 이는 단순 영상 배속이 아니며 수치·접촉 응답이 달라질 수 있다. 원 MJB, CAD, 마찰, 중력, solver 설정과 원 제어 명령은 그대로 보존했다.

이번에는 접근하면서 손가락이 닿는 순간, 그리퍼 닫기, 실제 삽입·충돌·해제 전환을 시험하지 않았다. 한 초 동안 잡은 상태가 유지된 것으로 전체 파지/삽입 안정성을 대신하지 않는다. 다음 적용 후보는 0.004초이며, 실제 접근·닫기·들기·삽입·해제 동작을 같은 조건으로 비교한 후 결정한다. 자유 이동 0.004초/접촉 동작 0.002초 같은 혼합 방식도 아직 구현하거나 검증하지 않았다.

## 도구와 실행

`scripts/probe_physics_timestep.py`는 지정한 실제 체크포인트에서 제어 목표를 유지하며 간격에 따른 결과를 비교한다. 연구 경험 수집, 실제 Astra 행동, 전체 설치 성공으로 집계하지 않는다. 각 조건 최대 2초, 반복 최대 3회, 전체 시간 최대 180초로 제한한다.

```powershell
cd C:/dev/llm-robot-assembly
$assemblyPython = './runtime/luna-venv/Scripts/python.exe'
& $assemblyPython -B scripts/probe_physics_timestep.py `
  --source-run 'F:/dev/llm-robot-assembly-data/research/20261125_motion_plan/live_probe01/bounded' `
  --checkpoint 'checkpoints/motion-probe-a39bac4c_lift_physically_lifted.json' `
  --output 'F:/dev/llm-robot-assembly-data/research/NEW_TIMESTEP_HELD' `
  --simulation-seconds 1 --timesteps 0.002 0.004 --repeats 2 --wall-seconds 120

& $assemblyPython -B scripts/probe_physics_timestep.py `
  --source-run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_transport_continuation01' `
  --checkpoint 'checkpoints/offline-install-1508b00a_insert_stopped_diagnostic.json' `
  --output 'F:/dev/llm-robot-assembly-data/research/NEW_TIMESTEP_RELEASED' `
  --simulation-seconds 1 --timesteps 0.002 0.004 --repeats 2 --wall-seconds 120
```

출력 경로는 새 이름을 사용한다. 이미 만들어진 결과를 확인하는 데에는 재실행이 필요 없다.

## 근거

`F:/dev/llm-robot-assembly-data/research/20261125_timestep/held01`, `released01`의 `comparison.json`, 각 조건 `result.json`, `samples.json`, `trace.npz`, `end_state.npy`를 보존했다. 비교는 실제로 관측한 동일 시각끼리만 수행하며 중간 프레임을 만들어내지 않았다. 영상 생성과 별도의 전체 회귀 테스트는 이번 작은 진단에 포함하지 않았다.

`code_at_execution.py`는 실행 당시 진단 코드다. 실험 모델 변경 여부를 모호하게 표현했던 메타데이터 필드명은 실험용 timestep 변경과 원본 파일 보존을 명확히 구분하도록 정정했다. 기존 JSON은 `metadata_original`에 그대로 보관하고 전후 해시를 `metadata_clarification.json`에 기록했다. 수치 결과와 상태 파일은 바꾸지 않았다.
