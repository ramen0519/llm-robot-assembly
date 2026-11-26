# 연구실 6셀 배터리팩: 자동 입력→조립 시도 개선 기록

작성: 2026-11-25. 작업 저장소 `C:/dev/llm-robot-assembly`, 브랜치 `codex/general-mesh-v2`, 작업 시작 HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f`. 기존 미커밋 작업을 보존했고 reset·commit·push를 하지 않았다.

## 목표와 이번 검증 범위

목표는 완성 CAD/목표 배치에서 자동으로 준비하고, 로봇이 조립을 시도한 뒤 성공 또는 구체적 실패 결과를 반환하는 프로그램이다. 이번에는 연구실 배터리팩의 실제 13부품 입력을 사용했다. 케이스 0번 고정, 이동 부품 12개이며 순서 제안은 `3→4→6→10→5→7→9→8→2→1→11→12`다.

사용한 `assembly_hint.json`에는 CAD 대응, 단위, 원래 완성 배치, 순서 제안이 들어 있다. 이 입력에서 제품별 Python 실행/좌표 수정 없이 준비와 MuJoCo 시도를 연결했다. 이번에 원시 STEP 한 파일에서 분해 플래너를 새로 호출한 것은 아니다. 순서 제안은 로봇 경로나 실제 결합 가능성의 증명이 아니다.

**현재 전체 조립 성공은 아니다.** 조립 전 안정성 문제와 첫 홀더의 공급·파지는 개선했지만, 현재 파지의 설치 자세에서 2F-85 본체와 케이스가 간섭했다. 기준을 풀거나 케이스/공구 형상을 축소해서 성공시키지 않았다. 실제 Astra 호출 0회, 비용 $0, 이번 작업의 해결 경험 등록 0건이다.

## 문제 → 수정 → 실제 결과

| 문제 | 보완 | 확인 결과 / 남은 범위 |
|---|---|---|
| 완성 모양을 놓아도 작은 부품의 속도 검사 실패 | 같은 원본 모델·시작 상태에서 solver 정밀도와 충돌 위치 정밀도를 분리 비교. solver 4개 설정만 공통 설정에 적용 | 1~2초 501회 모두 통과. 위치·회전·속도·관통 한도 동일. 로봇 조립 성공과 별도 |
| 배터리용 임시 준비 코드에 의존 | 실제 원본 메시/충돌 자산을 검증·등록하는 공통 캐시와 `register-assets` 추가 | 13부품 등록 175.22초, 원래 입력에서 새 준비 68.17초. 제품별 registry/Python 불필요. 새 CAD 최초 분해 성능까지 입증한 것은 아님 |
| 많은 접촉 때문에 검사 지연 | 같은 프레임 안에서만 자세 계산 재사용, 접촉 배열 일괄 읽기, 상세 근거를 필요할 때 생성 | 정착 공급 장면 2,587개 접촉의 검사 27.62→13.17ms. 수정 전후 전체 접촉 근거 해시와 판정 동일. 500Hz 검사는 유지 |
| scripted 후보가 불필요하게 손목을 반 바퀴 회전 | 현재 TCP와 후보 방향의 차이를 제공. scripted만 작은 회전 우선 | Astra의 선택/직접 수정 계약은 유지. 방향 차이는 성공 점수가 아님 |
| 8mm 홀더를 평평한 작업대에서 잡으려면 손가락이 바닥에 닿음 | 실제 메시의 안정 자세와 크기로 낮은 부품용 좁은 공급 받침대 자동 생성 | 40mm 높이의 받침대 6개. 동적 부품을 중력·접촉으로 지지. 모든 공급 부품 정착 통과, 첫 홀더의 닫기·들기 통과 |
| 설치 전 80mm 대기 위치가 케이스보다 낮음 | 실제 파지 공구 외형과 설치된 물체의 높이로 접근 거리를 산출 | 저장된 파지에서 필요 거리 약 101.45mm로 계산. 경로/실행 검사 별도. 진단에서 140/200mm 대기 위치 경로는 통과, 80mm는 실패 |
| 집기는 가능하나 그 상태로 설치가 불가 | 실측 파지 상대변환으로 최종 TCP를 계산하고, 운반 전에 설치 끝 자세 IK·충돌 검사 | 첫 파지의 최종 위치에서 gripper_base↔케이스 약 11.86mm 겹침. 원래 CAD 목표/그리퍼 유지. 현재 공구 접근이 남은 핵심 문제 |
| 탐색 실패를 요약하면서 충돌 원인이 사라짐 | IK/경로 재탐색에서도 원래 접촉·부위·정확한 후보 상태를 보존 | 이제 실패 코드만이 아니라 gripper_base 등 이름 없는 geom의 실제 body와 충돌 상대를 제공 |
| 실패 그림이 마지막 정상 자세일 수 있음 | 검출 당시 후보 상태를 별도로 저장하고 출처 유지. 같은 상태의 측면 이미지 추가 | `planner_candidate`, `mujoco_preflight_copy`, `live_simulation`을 구분. 후보 그림을 실제 충돌 동작으로 보고하지 않음 |
| 상태만 있고 제어기 목표가 없으면 재개가 애매함 | 실제 집기·들기 완료 시 integration state + 팔 목표/그리퍼 명령/실측 파지/미끄럼 기준/설치목록 저장 | `checkpoints/`에 해시 포함 기록. 이번 설치 성공 여부는 여전히 false |
| 사용자가 결과에서 무엇이 막혔는지 알기 어려움 | `failure_summary.json`과 결과 페이지에 집기/설치 결과, 충돌 부위, 겹침량, 사진 표시 | UI·CLI가 동일한 결과를 읽음. 정상 취소를 시간 소진과 분리 |

공급 받침대는 **새로 명시한 작업 환경 가정**이다. 얇은 부품을 원래 맨바닥에 놓은 조건과 동일한 실험이 아니다. 원래 CAD 목표, 부품 형상, 질량·마찰, 중력은 유지했고 공중 고정/용접/실행 중 순간이동은 사용하지 않았다. 실물에서는 해당 받침을 실제로 준비해야 한다.

## 물리 검사와 계산 검사를 구분한 결과

- 원래 2초 상태를 이어간 별도 2~3초 검사는 실패로 보존한다. 복원 직후·forward 후 상태 일치, 501개 전체 상태/측정값의 반복 실행 일치를 확인했다. 원본을 수정하지 않았다. `docs/STEP_33_REFERENCE_CONTINUATION.md`와 별도 후속 결과 참조.
- solver 개선 비교에서는 모델 배열 483개가 같고 수치 옵션 4개만 다르다. 이동속도 최고 0.07904mm/s, 회전속도 최고 0.0013103rad/s. 한도는 1mm/s, 0.01rad/s 그대로다.
- `scripted_robot_01`: 초기 검사는 통과, 비효율적 첫 접근 중 1,200초 소진. 설치 0/12.
- `scripted_robot_02`: 평평한 공급 상태에서 홀더 접근 시 pad↔table 약 0.517mm 겹침. 후속 후보 반복을 정상 취소. 구버전 상태 이름은 `deadline`으로 남겨 보존한다.
- `scripted_robot_03`: 받침대가 있는 공급 상태에서 첫 홀더의 실제 접촉 파지·들기는 3개 후보에서 통과. 각 파지의 설치 전 경로 실패. 다른 부품 시도를 시작한 뒤 중복 시험을 취소했다. 설치 0/12, 본 장면 조립 동작 없음. 구버전 취소 상태는 `deadline`으로 보존한다.
- `installation_endpoints_02`: 실제로 든 상태의 최종 설치 자세를 재계산하여 본체↔케이스 간섭을 확인했다. 추가 후보 18개도 설치 끝 자세 계산에서 통과 후보를 찾지 못했다. 대안 후보는 동일한 관측 손가락 구성에서의 **명목 자세 진단**이며, 각각 새로 잡은 물리 실험이 아니다. IK 미발견과 충돌을 구분하고, 모든 파지/공구가 불가능하다고 일반화하지 않는다.
- `scripted_robot_04`: 보완 후 정상 CLI로 다시 확인한 결과는 아래 후속 측정 항목과 해당 폴더의 `result.json`을 따른다.

## 주요 변경 파일

- `scripts/assembly_v2/numerics.py`, `config/assembly_workcell.v2.json`: 선택적 solver 정밀도와 명시된 공급 받침 설정.
- `asset_cache.py`, `geometry.py`, `entry.py`: 기존 검증 형상 등록, 해시 검증 재사용, 공통 준비 경로.
- `preparation.py`, `scene.py`, `validation.py`: 일반 메시에서 받침·초기 배치 생성, 보이는 충돌 형상, 재현 가능한 입력 계약 검사.
- `verification.py`: 프레임 내부 접촉 성능 개선, 공급 받침 접촉 소유권, 이름 없는 그리퍼 부위 식별.
- `grasp.py`, `policy.py`, `packets.py`: 방향 차이 정보와 scripted 후보 선택. Astra는 그대로 후보/전체 pose 선택 가능.
- `joint_path_planner.py`, `installation.py`, `skills.py`: 공통 Mink IK 재사용, 설치 끝 자세 검사, 실측 공구 외형 기반 접근, 원인 보존, 실제 파지 checkpoint.
- `media.py`, `artifacts.py`, `execution.py`, `ui.py`: 출처별 실패 이미지/결과, 제어 단계, 취소 기록, 결과 화면.
- 테스트: `test_v2_numerics_cache.py`, `test_v2_contact_cache.py`, `test_v2_installation.py`.

## 실행 방법

공통 저장 위치는 `config/local.storage.v2.json`의 F 드라이브다. API 키 없이 현재 동작 확인:

```powershell
cd C:/dev/llm-robot-assembly
$assemblyPython = './runtime/luna-venv/Scripts/python.exe'
& $assemblyPython -B scripts/run_assembly.py run `
  --input 'F:/dev/llm-robot-assembly-data/inputs/lab_six_cell_battery_20261125_01/assembly_hint.json' `
  --output 'F:/dev/llm-robot-assembly-data/runs/lab_battery_new_attempt' `
  --policy scripted --condition R0 --seconds 480
```

항상 새 출력 폴더를 지정한다. `scripted`는 개발 검사이며 LLM 에이전트 실행이 아니다. 출력 코드가 성공이 아니어도 `result.json`, `report.html`, 실패 이미지에 중단 근거를 남긴다. 480초는 이번 짧은 진단 한도다. 모든 부품 완료 시간을 보장하는 값이 아니다.

```powershell
& $assemblyPython -B scripts/run_assembly.py ui --port 8768
& $assemblyPython -B scripts/run_assembly.py audit --run '<검사할 실행 폴더>'
& $assemblyPython -B scripts/run_assembly.py replay --run '<검사할 실행 폴더>'
& $assemblyPython -B -m unittest discover -s tests -p 'test_v2*.py'
```

현재 로컬 UI: `http://127.0.0.1:8768`. 최근 실행 목록에서 결과를 열 수 있다. 이미 서버가 실행 중이면 같은 포트로 중복 시작하지 않는다. 영상은 저장된 본 장면 상태의 재생이며, 복사 검사의 집기를 본 장면 영상에 섞지 않는다.

한 번 등록한 형상은 다음 준비에서 자동 재사용한다. 다른 컴퓨터에 기존 검증 자산을 등록하는 명령은 다음과 같다. 원본과 동일한 표면/체적/빈 공간 검사에 통과한 자산만 등록된다.

```powershell
& $assemblyPython -B scripts/run_assembly.py register-assets `
  --prepared '<기존에 검증된 prepared.json>' `
  --output '<새 등록 보고서 폴더>' --seconds 1800
```

## 남은 제한 / Astra 실행 조건

1. 현재 파지의 공구 본체가 깊은 케이스에 걸린다. 성공 기준 완화나 좌표 보정으로 없앨 수 있는 오차가 아니다. 더 다양한 파지의 실제 재검증, 공구 접근 공간 검토가 필요하다. 길고 얇은 전용 손가락/다른 공구는 **설계 대안**이지 이번에 구현·검증한 해결책이 아니다.
2. 실제 Astra high의 신규 선택·수정은 실행하지 않았다. 실행 프로세스의 API 키가 없고, 이전 $7.50 승인은 종료된 회차용이다. 환경 설정의 $1.50는 지출 승인과 다르다.
3. 유료 재시도를 한다면 첫 홀더 복구부터 시작하는 한 회차에 생성 6회/입력 토큰 조회 6회, 총 $1.50, 최대 30분 실행+3분 후처리 범위를 제안한다. 현재 8,000입력/2,048출력 제한과 ledger를 유지한다. 실제 키와 이번 회차 승인이 필요하며 아직 호출하지 않았다. 순서 제안의 보류 기능에 따라 다음 부품을 고려할 수 있고, 두 역할·지침 생성 모두 같은 6회 제한에 포함된다. 전체 12개 설치를 이 범위에서 보장하지 않는다.
4. 이번에 해결된 실제 Astra 조립 경험이 없으므로 해결지침을 등록하거나 학습 성공으로 집계하지 않는다. 일반 개발 실패 기록과 물리 근거는 보존한다. 해결→지침 등록→다음 실행 재사용은 이번 배터리에서 미검증이다.
5. 원시 STEP→분해 플래너의 신규 호출, 나사 조임, 전기 연결, 배터리 안전 취급, 실물 UR3e는 이번 검증 범위 밖이다. E 드라이브와 실물 장비에 접근하지 않았다.

## 증거 위치

루트: `F:/dev/llm-robot-assembly-data/runs/lab_battery_autonomy_20261125_01`

- `numerics_summary.json`, `solver_precision/verification.json`: 수치 설정만 분리 비교, 12부품 연속 측정.
- `collision_import/result.json`, `new_input_prepared/`: 원본 형상 등록, 동일 입력의 공통 준비.
- `contact_batch_comparison_settled.json`: 실제 접촉 판정/근거의 수정 전후 동일성.
- `scripted_robot_01`~`04`: 각 회차의 독립된 결과/모델/로그/영상/재생 묶음. 이전 회차를 덮어쓰지 않음.
- `transport_diagnostic.json`, `transport_diagnostic_after.json`: 실제 저장 파지에서 네 목표의 경로 계산, 수정 전후 판정 유지.
- `installation_endpoints_02/result.json`: 실측 파지 및 명목 후보 18개의 설치 자세 계산.
- `diagnostic_views/actual_lifted.png`: 복사 장면에서 실제 물리로 들어 올린 상태.
- `diagnostic_views/installation_collision.png`: 충돌 판정된 설치 후보 상태. 실행된 충돌 사진이 아님.
- `diagnostic_views/manifest.json`: 두 그림의 원래 상태 파일·해시·출처.
- `PROGRESS.md`, `ISSUES.md`, 이 보고서: 진행·문제·해결·제한 기록.

## 보완 후 최종 재검증 (scripted_robot_04)

- run ID: `scripted_robot_04-5b39a147`. 총 경과 391.08초. 첫 실패 원인이 정상 기록된 뒤 중복 시도를 정상 취소했다. 상태 `cancelled`.
- 원래 입력 하나에서 공통 준비 → 완성 목표 501회 연속 검사 통과 → 공급 부품 정착 → 실제 파지/들기 → 별도 설치 끝 자세 검사까지 연결.
- 첫 파지: `passed`. 그 파지의 설치: `failed`. 실제 본 장면 설치 **0/12**. 본 장면은 복사 검사로 변경되지 않음.
- 첫 실패: `install_endpoint_check/preflight`, 출처 `planner_candidate`. `gripper_base`와 케이스 겹침 11.85864mm. 이 후보를 실제 충돌 운동으로 실행하지 않았다.
- 실제로 든 integration state는 이전 회차의 같은 시점과 원소별 같음: `True`. 실제 파지 checkpoint에서 0.1초를 두 번 이어 돌려 51개 전체 상태와 모든 측정값이 같았고 매 순간 양쪽 접촉이 유지됐다. 이는 저장 상태의 반복 재현이며, 중단하지 않은 별도 원실행과 대조했다고 주장하지 않는다.
- 자동 기록 audit: `passed`. 원본 결과 7개 파일 해시 모두 동일. replay 묶음 생성 오류 없음.
- v2 자동 검사 50개 및 기존 실제 로봇 동역학 검사 4개 통과. `git diff --check` 통과.
- UI HTTP 200, 최근 목록·해당 보고서·측면 실패 이미지 조회 확인. 실제 결과 링크: http://127.0.0.1:8768/results/b193fe5eab316e8391ba6051e83b9561/report.html
- 본 장면 영상은 초기 정착 3초, 62개 저장 상태 중 17프레임을 표시한다. **복사 장면의 집기 동영상은 아니다.** 실제 들어 올린 상태는 별도 checkpoint와 그림으로 확인한다.
- 실제 Astra 호출 0, 비용 $0, 이번 해결 경험 등록 0. 전체 조립 성공이나 경험 수집 준비 완료로 보고하지 않는다.

## 준비한 유료 재개 명령 (현재 미실행)

유효한 키를 실행 환경의 `OPENAI_API_KEY`에 등록하고, 이번 한 회차 지출 범위가 승인된 다음 사용한다. `pending_live_scope.json`과 `llm.astra-high.pending-6calls.toml`에 준비했다. 가격은 기존 프로젝트 설정을 사용하며 실제 유료 실행 전 적용 가격을 확인해야 한다.

```powershell
cd C:/dev/llm-robot-assembly
& ./runtime/luna-venv/Scripts/python.exe -B scripts/run_assembly.py run `
  --input 'F:/dev/llm-robot-assembly-data/inputs/lab_six_cell_battery_20261125_01/assembly_hint.json' `
  --output 'F:/dev/llm-robot-assembly-data/runs/lab_battery_live_new_attempt' `
  --policy astra --condition R0 --seconds 1800 `
  --llm-config 'F:/dev/llm-robot-assembly-data/runs/lab_battery_autonomy_20261125_01/llm.astra-high.pending-6calls.toml'
```

조립·복구가 공유하는 생성 상한 6회, 입력 조회 6회, 총 토큰 상한 60,288, 금액 상한 $1.50. 현재 프로젝트 가격 설정의 최대 예약 합계는 $1.2144다. 이는 가격 변경을 반영한 새 견적이나 승인으로 간주하지 않는다. 실제 입력 문맥이 8,000토큰을 넘으면 해당 요청은 중단된다. 무조건 6회를 모두 사용할 수 있다는 보장도 아니다.

## 진단 보조 코드의 실행 오류도 구분

독립 진단 초안에서 pad 간격 함수의 model 인자 누락과 JSON 회전행렬의 배열 변환 누락이 있었다. 각 보조 스크립트를 수정해 **새 출력 폴더**에서 재실행했다. 이 오류를 로봇 물리 실패로 집계하지 않았고, 최종 근거는 `installation_endpoints_02`, `grasp_checkpoint_reproduction_02`다. 새 단위 테스트의 대체 IK 기록 누락도 테스트 코드에서 수정해 최종 50개 통과를 확인했다. pytest는 설치돼 있지 않아 사용하지 않았으며 프로젝트의 unittest를 실행했다.

## 계산한 접근 높이의 실제 운반 검사

`transport_clearance_physics/`는 첫 홀더를 실제로 든 checkpoint에서 시작한 **별도 복사 상태 후속 진단**이다. 현재 설치 끝 자세가 충돌한다는 사실은 유지하고, 공구/케이스 형상으로 계산한 접근 대기 위치까지만 물리 실행했다. 정상 전체 실행에서 설치 끝 자세 검사를 무시하거나 성공 처리하지 않았다.

자동 계산 거리 101.445mm, 결과 `transport_waypoint_passed`, 경과 53.06초. 시뮬레이션 33.482~49.928초 동안 실제 모터/접촉으로 운반했다. 다음 설치/해제는 실행하지 않았다.


- 영상: `transport_clearance_physics/transport.mp4` (실제 저장 상태의 재생, 보간 없음)
- 결과/측정: `transport_clearance_physics/result.json`
- 상태: `transport_clearance_physics/integration_states.npz`
- 출처/해시: `transport_clearance_physics/video_manifest.json`

이 결과로 접근 높이 계산의 운반 단계는 확인됐으나, 케이스 내부 최종 설치 성공은 여전히 0/12다.
