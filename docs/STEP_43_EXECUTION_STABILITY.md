# STEP 43 · 실행기 중복 검사 줄이기와 셀 7 완료

2026-11-25. 기존 미커밋 변경, 원 CAD 목표, 물성·충돌·완료 기준을 보존했다. API 없는 개발 진단이다.

## 목적과 통과 기준

시뮬레이션이 느린 위치를 계측하고, 판정 기준과 실제 물리 상태를 바꾸지 않는 중복 계산을 줄인다. 시간 소진으로 멈춘 셀 7은 원 상태·제어 상태를 검증 복원하고 재관측한 뒤 후퇴와 501샘플 완료 검사를 수행한다.

## 수정

- `verification.py`, `fast_contacts.py`: 접촉 한 프레임 안에서 필요한 부품 자세를 묶어서 계산. 다음 프레임이나 별도 복사 장면에는 관측값을 재사용하지 않는다.
- `continue_offline_install.py --resume-stage retreating`: 실제 해제 후 후퇴 중 시간 소진만 허용. 빈 그리퍼·원본 상태·실제 해제 기록·검사한 후퇴 목표를 확인하고, 현재 로봇-제품 비접촉을 다시 확인한 뒤 새 경로로 후퇴한다.
- 물리 실패·미끄럼·출처 없는 목표를 무조건 재시도하지 않는다. 원 실험의 시간 제한은 소급 변경하지 않는다.

## 실제 결과

같은 체크포인트의 250단계(0.5초 시뮬레이션)를 계측했다. 3.266→2.889초, 약 11.5% 감소했다. 계측 오버헤드가 포함된 짧은 구간이며 전체 제품의 시간 단축률이 아니다. 최종 적분 상태 파일 SHA-256이 정확히 같았다: `2d240a8ef58687b1160ab819dcb26c0120c50db7cd4e2d1d82327d9f5bfdc2ba`.

회귀 검사 98개 통과. 실제 셀 7 후퇴·해제 후 검증은 `offline-install-1c0d8d3b`, 68.735초로 통과했다. 현재 같은 관찰 창에서 확인한 설치 부품은 **3/4/5/6/7/10, 6/12개**다.

셀 7 최대값: XYZ 각 축 0.0844/0.0451/0.1073mm, 회전 약 0.0139°, 경질 관통 0.4345mm, 이동속도 0.000000819m/s, 회전속도 0.0000441rad/s. 원래 5mm·3°·0.5mm·1mm/s·0.01rad/s·1초 501샘플 기준을 유지했다.

전체 조립 완주나 새 Astra 실행이 아니다. 원래 실험용 교체 손가락 모델의 scripted 후속 진단이며 실제 배터리 취급·전기 연결·실물 로봇은 다루지 않았다. 추가 API 0회 / $0.

## 실행 명령

```powershell
cd C:/dev/llm-robot-assembly
$assemblyPython = './runtime/luna-venv/Scripts/python.exe'
& $assemblyPython -B scripts/continue_offline_install.py `
  --source-run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_transport_continuation01' `
  --checkpoint 'checkpoints/offline-install-1508b00a_insert_stopped_diagnostic.json' `
  --resume-stage retreating `
  --output 'F:/dev/llm-robot-assembly-data/runs/NEW_RETREAT_DIAGNOSTIC' --seconds 180
```

출력 폴더는 새 이름이어야 한다. 이 명령은 성공 기록 재생이 아니라 원 중단 상태에서 새로 물리 검증하는 별도 개발 시험이다.

## 근거

- 계측: `F:/dev/llm-robot-assembly-data/research/20261125_execution_stability/profile01`, `profile02`.
- 실제 후속: `F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_retreat_completion01`의 `result.json`, `completion.json`, `resume_observation.json`, `source_evidence.json`, `media/execution.mp4`.
- 남은 병목: 물리 연산이 계측 시간 대부분을 차지한다. timestep 0.002초, solver, 충돌·힘 검사 빈도는 이번에 변경하지 않았다.
