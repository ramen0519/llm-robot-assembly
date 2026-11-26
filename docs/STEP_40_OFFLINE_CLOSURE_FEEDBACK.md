# API 없는 파지·설치 충돌 보완

2026-11-25. 시작 HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f`, 브랜치 `codex/general-mesh-v2`.
기존 미커밋 작업·원본 CAD·유료 실행 결과를 보존했다. 이번 수정도 미커밋이다.

## 실제 문제

이전 Astra가 고른 셀 10번 후보는 작은 돌기의 두 표면 간격 약 4.997mm를 근거로 설치 여유를 계산했다.
하지만 실제 닫기에서는 손가락이 넓은 몸통에 먼저 닿았고, 들기 상태의 측정 벌림은 **17.849624mm**였다.
실측 벌림으로 목표 자세를 다시 계산하니 케이스와 약 **0.790mm** 겹쳤다. 이전 실제 IK 끝 자세 거절의 **0.792mm**와 원인이 일치한다.
TCP 변환 차이는 약 0.218mm / 0.0502도였으며, 주요 문제는 좁은 표면 간격을 실제 닫힌 벌림으로 간주한 것이다.

## 공통 수정

- `scripts/assembly_v2/grasp_screen.py`: 실제 손가락 전체 형상과 부품 충돌 형상을 사용해 빈 그리퍼 보정표의 닫기 상태를 순서대로 검사한다. 첫 접촉이 생기는 벌림 범위와 접촉 부위를 기록하고, 그 범위의 손가락으로 목표 설치 간섭을 다시 예상한다.
- 같은 모듈은 넓은 몸통이 먼저 닿는 경우 접근 방향의 반대로 0.5~2.5mm 물리는 제한된 자세 대안을 검사한다. 제품 ID나 배터리 좌표를 공통 코드에 넣지 않았다. 원래 후보를 바꾸거나 자동 실행하지 않으며 정책에 전체 pose와 근거를 제공한다.
- `scripts/assembly_v2/grasp.py`: 후보 v2.6의 공통 전달 자료에 닫기 검사와 선택 가능한 수정안을 연결한다.
- `config/policy_prompts.v2.json`: 표면 폭·접근 벌림·예상 닫힘 폭·실측 파지 폭을 구분하고, 수정안 역시 실제 물리 시험이 필요함을 명시한다. 새 프롬프트의 Astra 실행은 **not_run**이다.
- `scripts/check_offline_grasp.py`, `scripts/continue_offline_install.py`: 기록된 빈손 경계 또는 실제 들기 상태로부터 API 없이 독립 개발 시험을 수행한다. 들기 복원은 적분 상태와 관절 목표·그리퍼 명령·원래 슬립 기준을 함께 확인한다.
- `tests/test_v2_closure_screen.py`: 작은 돌기/넓은 몸통/좁은 설치 공간의 native MuJoCo 형상으로 오판 방지, 미검사 처리, 원본 상태 보존, 수정안 제시를 확인한다.

예상 검사는 IK·양쪽 접촉 유지·접촉력·미끄러짐·연속 경로를 증명하지 않는다. 기존 실제 집기·이동·설치·놓기·완료 검사는 그대로 유지했다.

## 이번 실제 검사

추가 API **0회 / $0**. 모델 호출을 하지 않았으며 성공 지침·연구 경험 등록도 **0건**이다.

1. 이전 실제 들기 상태를 같은 모델로 정확히 복원하고, 주 장면을 변경하지 않은 채 문제를 재현했다.
2. 높이 7개 × 회전 4개 = 28개 작은 자세 대안을 형상 검사로 비교했다. 이것은 로봇 조립 성공 수가 아니다.
3. 공통 후보 전달 흐름에서 기존 후보의 접촉 폭 **17.983~18.756mm**와 목표 간섭을 찾아냈고, **2.0 / 2.5mm** 수정안을 제시했다.
4. `trial01_height2` / `offline-d6755eec`: 2mm 수정 자세로 실제 집기·들기·운반·설치·놓기·후퇴까지 진행했다. `open_after_retreat`에서 240초 한도로 종료되어 **완료 검사 미완료(unknown)**다. 성공으로 집계하지 않는다.
5. `trial02_install_continuation` / `offline-install-348b7170`: 4번의 실제 들기 체크포인트를 복원하여 집기를 반복하지 않고 설치 이후를 다시 검증했다. 상태 **component_success**, 설치 검사 **passed**.
6. 회귀 검사 **81개 통과**, `git diff --check` 통과. 새 Astra 호출이나 전체 12부품 연속 완주를 의미하지 않는다.

두 시험 프로세스 누적 393.625초. 코드 수정·분석·별도 기하 진단 시간은 제외한다.

## 완료 기준과 측정

원 CAD 목표, 중력, 충돌 자산, 물성 및 제어 한도를 변경하지 않았다. 최종 XYZ 각 축 5mm, 상대 회전 3도, 경질 관통 0.5mm, 이동속도 1mm/s, 회전속도 0.01rad/s, 해제·그리퍼 이격·연속 1초 조건을 그대로 적용했다.

홀더 3, 셀 6, 새 셀 10을 같은 연속 501샘플 창에서 검사해 모두 통과했다.

| 부품 | XYZ 최대 절대 오차 mm | 최대 회전 오차 도 | 최대 속도 mm/s | 최대 각속도 rad/s | 최종 창 최대 경질 관통 mm |
|---|---:|---:|---:|---:|---:|
| 10 | 0.002282 / 0.080425 / 0.106164 | 0.054884 | 0.000203 | 0.00000837 | 0.434541 |
| 3 | 0.034528 / 0.098669 / 1.000812 | 0.073606 | 0.000006 | 0.00000049 | 0.434541 |
| 6 | 0.038957 / 0.078879 / 0.107681 | 0.047768 | 0.000087 | 0.00000900 | 0.434541 |

## 재현 명령

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B -m unittest discover -s tests -p 'test_v2*.py'

# 새 출력 폴더 이름을 사용한다. API 호출 없음, 최대 6분.
& $assemblyPython -B scripts/check_offline_grasp.py `
  --source-run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_hour_20261125_01/attempt05_component10_source_screen/run' `
  --proposal 'F:/dev/llm-robot-assembly-data/runs/lab_battery_offline_20261125_01/proposal_height2.json' `
  --output 'F:/dev/llm-robot-assembly-data/runs/lab_battery_offline_RECHECK' --seconds 360

# 이미 물리적으로 들었던 상태부터 설치 검사만 이어갈 때.
& $assemblyPython -B scripts/continue_offline_install.py `
  --source-run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_offline_20261125_01/trial01_height2' `
  --checkpoint 'checkpoints/offline-d6755eec_scripted_physically_lifted.json' `
  --output 'F:/dev/llm-robot-assembly-data/runs/lab_battery_offline_INSTALL_RECHECK' --seconds 240
```

## 산출물과 제한

결과 루트: `F:/dev/llm-robot-assembly-data/runs/lab_battery_offline_20261125_01`

- `grasp_fit_diagnosis.json`: 실제 파지 폭·변환·케이스 간섭.
- `closure_scan.json`: 28개 후보의 예상 검사 결과.
- `closure_pipeline_check.json`: 공통 후보 흐름의 수정안 및 상태 불변 증거.
- `trial01_height2/media/execution.mp4`: 실제 집기부터 첫 후퇴까지의 기록 영상.
- `trial02_install_continuation/media/execution.mp4`: 복원한 들기 상태부터 설치·해제·안정성까지의 기록 영상.
- `trial02_install_continuation/completion.json`: 후속 실제 연속 검사 결과(통과 시 생성).
- `SUMMARY.json`: 실행 ID·비용·검증 구분·변경 코드 해시.

이는 **scripted 개발 시험의 한 부품 개선**이다. 기존 Astra 실행의 2/12 결과를 덮어쓰지 않는다.
별도 후속 물리 장면에서는 기존 2개와 셀 10을 합쳐 3개가 검증됐다.
새 제품 전체 자율 조립·나머지 9개 설치·수정 프롬프트의 Astra 판단·경험 등록/검색 재사용은 이번에 검증하지 않았다.
100mm 실험용 강체 손가락을 유지했으며 실물 제작/배터리 전기 연결을 검증한 것이 아니다.

API 없이 Codex가 코드를 수정하며 찾은 해결과, 배포된 조립 에이전트가 혼자 해결한 성능은 별도로 평가해야 한다.
