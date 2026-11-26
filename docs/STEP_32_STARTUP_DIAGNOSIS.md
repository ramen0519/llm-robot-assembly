# 32. 분해 전달 세 제품의 시작 실패 진단

2026-11-25. 기준 HEAD는 `cf8add83cb2704c61ab4dc928119fb5276f1d33f`, 브랜치는 `codex/general-mesh-v2`다. 이미 미커밋 상태였던 추천 순서 입력·기하 처리·검증 변경은 보존했다. 과거 commit으로 되돌리거나 분해 쪽 입력과 결과를 수정하지 않았다.

## 실제로 어디에서 멈췄는가

`F:\dev\llm-robot-assembly-data\runs\asap_training_advisory_20261125_01`의 04688, 01657, 06534를 확인했다. 모두 `reference_invalid`, API 0회, 설치 0개다. **로봇 공급 배치의 정착이나 Astra 판단 전에, 완성 CAD 상태의 안정성을 검사하다가 중단됐다.**

완성 기준 장면에서는 추천 순서의 첫 부품 하나만 고정하고 나머지를 동적 물체로 둔다. 입력은 `assembly_order_hint/v1`이며 CAD/단위/원래 목표/순서만 전달한다. 분해의 자세·추가 지지·제거 경로를 전달하는 계약이 아니다. 분해 성공만으로 이 장면의 물리적 유지나 로봇 접근이 검증되는 것은 아니다.

| 제품 | 부품 수 / 고정 부품 | 원본 장면에서 재현한 중단 |
|---|---|---|
| 04688 | 24 / 8 | 0.028초에 0번이 약 3.91mm 내려오며 원래 목표 접촉 목록에 없는 8번과 접촉. 12번은 이미 3.235° 회전. |
| 01657 | 20 / 0 | 0.032초에 여러 부품이 5mm 넘게 내려감. 최대 축별 오차 5.227mm. |
| 06534 | 16 / 0 | 0.020초에 15번의 회전 오차 3.974°. |

실제 화면에서 01657의 바퀴 달린 조립체는 세로로 놓여 있고, 06534는 고정 기둥 외의 부재가 별도의 결합 구속 없이 중력을 받는다. 관측으로 확인한 것은 **현재 작업 자세·지지·물성·충돌 조건에서 원 CAD 목표가 유지되지 않는다**는 사실이다. 필요한 나사/축 고정/접착의 종류까지 확인했다고 주장하지 않는다.

CAD 단위는 전달 manifest의 `cm`를 사용한다. 원본 목표 변환은 유지됐고 이미 assembly 좌표인 메시를 이중 이동시키지 않았다. 01657의 3번은 이 입력 단위에서 약 1.83 × 1.83 × 1.15mm다. 실제 제품 크기가 아닌 정규화 자료인지와 실물 크기의 대응은 별도 확인 대상이며 임의 확대하지 않았다.

## 수정한 코드

1. `scripts/assembly_v2/preparation.py`: 공급 자세 선택이 목표 회전과 닮은 순서를 먼저 쓰던 문제를 수정했다. 예를 들어 04688의 13번은 기하 안정 자세 확률 0.002354인 좁은 모서리 자세를 선택하고 있었다. 이제 안정 자세 확률을 우선하고 대칭 동률일 때 목표 회전을 고려한다. 이 확률은 기하적인 자세 순위일 뿐 실제 물리 성공률이 아니다. 실제 3초 정착 검사를 그대로 유지한다.
2. `scripts/assembly_v2/verification.py`: 접촉 거부의 원인을 목표 쌍 미등록 / 목표 자세 이탈 / 단계상 허용되지 않음 등으로 기록한다. 정착 중 실제 물리 위반도 `failed`로 남기고, 중단 시각·단계·전체 부품의 원래 목표 대비 위치와 회전·속도를 저장한다. 시간 소진 등 관측 부족은 `unknown`을 유지한다.
3. `scripts/assembly_v2/readiness.py`, `scripts/check_assembly_start.py`: API 없이 완성 목표 검사와 공급 정착을 독립 진단한다. `--rebuild-supply-layout`은 새 출력에 공급 배치를 재생성하며 원본 prepared 파일을 수정하지 않는다. 진단 중 공급 정착이 통과해도 기준 장면 실패를 무시하고 로봇을 실행하지 않는다.
4. `scripts/assembly_v2/execution.py`: 실제 실행 경로도 같은 기준 검사 함수를 사용한다. 원래 `error: null`, `reference_status: unknown`으로 끝나던 물리 실패에 구체적 오류·단계·진단 파일을 연결한다.
5. `scripts/assembly_v2/artifacts.py`: 결과 HTML에 기준 장면의 전/후 이미지와 쉬운 설명을 표시한다. replay bundle에 기준 모델·상태·진단·이미지를 포함한다. 두 경계 상태를 연속 동작 영상으로 표기하지 않는다.
6. `tests/test_v2_readiness.py`: 안정 자세 우선, 대칭 동률, 첫 관측 전 물리 실패와 timeout 구분, 결과 HTML의 실행 출처 표시를 검증한다.

## 실제 재검증

출력: `F:\dev\llm-robot-assembly-data\runs\initial_pose_recheck_20261125_01`

| 제품 | 별도 공급 정착: 수정 전 → 후 | 완성 목표 유지 |
|---|---|---|
| 04688 | 실패 → 통과, 동적 23개, 3초 | 여전히 실패 |
| 01657 | 실패 → 실패 | 여전히 실패 |
| 06534 | 통과 → 통과, 동적 15개, 3초 | 여전히 실패 |

01657은 자세 수정 후에도 3번의 작업대 관통이 0.854초에 약 2.337mm로 관측돼 중단됐다. 기준을 완화하거나 통과로 바꾸지 않았다. 작은 부품의 실제 크기·관성·접촉 수치 안정성은 후속 확인 대상이다.

고정 베이스가 실제로 작업대에 닿고 다른 부품이 작업대 아래로 들어가지 않는 베이스 안정 자세 후보도 제한적으로 진단했다. 04688 4후보, 01657 1후보 모두 기준 검사를 통과하지 못했다. 06534는 해당 베이스 안정 자세 목록에서 작업대와 양립하는 후보가 없었다. 이는 제한된 후보 내 미발견이며 가능한 지그/작업 방향이 전혀 없다는 증명이 아니다. 자동으로 고정 베이스를 바꾸거나 지지물·weld를 추가하지 않았다.

실제 production 실행 경로의 실패 보고: `04688_production-3c221b65`. 기준 장면 실패, 원인/이미지/상태 저장, API 0회, audit passed, archive 오류 없음.

기존 2부품 회귀: `two_part_regression-c971bd43`, **scripted 실제 MuJoCo physical_success**, 설치 부품 `1`, audit passed, API 0회. 이 검사는 새 세 제품의 성공도, 실제 Astra 실행도 아니다. 영상은 `two_part_regression/media/execution.mp4`, 완료 측정값은 `two_part_regression/final_verification.json`에 있다.

자동 검사: v2 37개 통과. 로그 `unit_tests.log`. 한 번의 제한 샌드박스 실행은 colorama 읽기 권한 때문에 중단됐고 정상 프로젝트 권한에서 재실행해 37개 모두 확인했다.

## 실행 방법

```powershell
cd C:\dev\llm-robot-assembly
$py = '.\runtime\luna-venv\Scripts\python.exe'
& $py -B scripts\check_assembly_start.py `
  --prepared 'F:\dev\llm-robot-assembly-data\runs\asap_training_advisory_20261125_01\04688\prepared.json' `
  --output 'F:\dev\llm-robot-assembly-data\runs\check_04688_new' `
  --rebuild-supply-layout --seconds 180
```

`--output`은 아직 없는 새 폴더여야 한다. 기준 또는 공급 검사가 실패하면 종료 코드 2이고, `readiness.json`에서 두 판정을 따로 확인한다. 기존 prepared를 그대로 실행하면 과거 초기 자세도 보존된다. 새 원본 입력을 `prepare`하면 수정된 선택 규칙을 사용한다.

전체 그림/표: `diagnosis_report.html`; 요약 데이터: `summary.json`; 각 제품 `reference_diagnostics.json`, `reference_verification.json`, `supply_verification.json`, `reference_media/initial.png`, `reference_media/stopped.png`.

## 남은 조치

현재 세 제품의 유료 경험 수집은 시작할 수 없다. 작업 방향·베이스 지지와 필요한 지그를 명시적으로 준비하고, 최종 목표를 유지하는 결합 조건을 구분해야 한다. 구속이나 체결이 필요한 제품을 단순히 자유 물체로 놓는 현재 장면으로는 검증할 수 없다. 이 조건을 먼저 물리적으로 검증한 뒤 파지·설치와 에이전트 판단으로 진행한다. 01657은 공급 부품의 작은 크기에 대한 추가 수치 진단도 필요하다.

이번 작업의 API 호출·비용·기억 등록은 모두 0이다. 원본 입력/기존 로그, 물리 임계치, 실물 로봇, E 드라이브는 변경·접근하지 않았다. 실제 물리 실패를 우회하거나 전체 프로토타입 성공이라고 보고하지 않는다.
