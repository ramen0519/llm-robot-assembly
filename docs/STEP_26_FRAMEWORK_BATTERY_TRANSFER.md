# 26 · 실제 분해 검증 결과 → Framework 배터리 조립 시도

작성: 2026-11-20. 작업 저장소: `C:\dev\llm-robot-assembly`, 브랜치: `codex/framework-battery-transfer`.

**입력 전달과 실제 CAD 장면을 구현했고, 첫 CELL 5번의 파지·운반·해제까지 실행했다. 정확한 설치 성공은 아직 0/5다.** 목표보다 약 4.3~4.4 mm 높게 걸렸으며, 별도로 충돌용 근사 형상이 완성 CAD 위치에서도 약 0.51 mm 겹치는 문제가 확인됐다. 현재 실행기는 이 형상 문제를 먼저 검출해 추가 조립을 중단한다. 실패를 성공으로 처리하거나 다음 부품으로 넘어가지 않는다.

이번 실행은 API 0회, 비용 $0, 실물 명령 0회인 **scripted MuJoCo 검사**다. 전달받은 분해 결과에는 과거 Astra 판단이 들어 있지만, 이번 로봇의 파지·이동을 Astra가 결정한 것은 아니다. 배터리 전용 LangGraph/LLM 호출도 아직 연결하지 않았다.

## 1. 원본에서 확인한 내용

읽은 분해 프로젝트는 `C:\dev\disassembly_codex_ready_v2`다. `worker_result.json`의 `selected_plan`만 순서의 근거로 사용했다. 각 edge의 parent가 직전 child와 같고, 이동 부품 하나만 제거되며, feasible=true인지를 검사했다. `connected.complete_chain`과 `native_stats.sequence`도 대조했다. 다른 탐색 분기의 성공 기록은 합치지 않았다.

| 단계 | 제거 부품 | 제거 후 남은 부품 | 베이스 외 추가 지지 | pose |
|---|---|---|---|---|
| 1 | 2 | 0, 1, 3, 4, 5 | 0 | null |
| 2 | 3 | 0, 1, 4, 5 | 0 | null |
| 3 | 1 | 0, 4, 5 | 0 | null |
| 4 | 4 | 0, 5 | 0 | null |
| 5 | 5 | 0 | 0 | null |

고정 베이스는 0이다. 따라서 **0 고정 → 5 → 4 → 1 → 3 → 2**를 조립 후보로 받는다. 원본의 자세 값은 null이며 별도의 자세 변환을 적용하지 않는다. 원본의 분해 action은 앞 네 단계 `[0,-1,0]`, 마지막 `[0,1,0]`이지만 메타데이터로만 보존한다. TCP 이동 목표에는 사용하지 않는다.

원본의 검증 범위는 Astra 후보 순위 + 원래 ASAP 탐색 + RedMax의 **부품 분해 가능성**이다. 로봇 도달성, 2F-85 파지, 로봇 포함 충돌, 삽입, 설치 후 안정성은 검증하지 않은 자료다. 부품 수준의 추가 지지 0개가 로봇 조립 지그 불필요를 뜻하지 않는다.

## 2. 복사한 전달 묶음과 좌표

새 묶음은 `runtime/products/framework_battery_v1/`에 있다. OneDrive 밖이며 Git에서 제외되는 로컬 자료다. 원본 CAD·결과·설명·출처·정규화 메타데이터·제조사 STEP을 복사했다. `transfer.json`에 모든 원본 경로/해시와 묶음 내부 해시를 보존했다. 원본 12개 파일의 SHA-256을 작업 후 다시 비교했고 모두 동일했다.

| ID | 원본 OBJ | CAD 이름 | 크기: 원본 축 X/Y/Z, mm | 완성 상태 중심: 제품 좌표, m |
|---|---|---|---|---|
| 0 | source/obj/0.obj | FRAME | 240 / 6.42 / 95.099536 | (0, 0, 0.047549768) |
| 1 | source/obj/1.obj | PCB | 210.6 / 1.35 / 10 | (-0.003, -0.001315, 0.012999536) |
| 2 | source/obj/2.obj | CELL | 58.25 / 5.4 / 73.7 | (0.0894, 0.0003, 0.051349536) |
| 3 | source/obj/3.obj | CELL | 58.25 / 5.4 / 73.7 | (0.0298, 0.0003, 0.051349536) |
| 4 | source/obj/4.obj | CELL | 58.25 / 5.4 / 73.7 | (-0.0298, 0.0003, 0.051349536) |
| 5 | source/obj/5.obj | CELL | 58.25 / 5.4 / 73.7 | (-0.0894, 0.0003, 0.051349536) |

STEP 출처는 복사한 `source/source.json`에 있는 Framework 제조사 저장소의 고정 커밋이다. STEP 해시는 `1c8ae3b3026c64a1473c2c2662f0746a3734c47cd318c80a08695ccbb065f1c9`. STEP의 길이 단위 선언이 mm임을 직접 확인했다.

**분해용 OBJ는 mm 원본이 아니라 정규화된 좌표**다. 원본 정규화 기록의 `asap_per_mm = 1/24`에 따라:

```text
제품 좌표(m) = OBJ 정점 × 0.024
원래 STEP 좌표(mm) = OBJ 정점 × 24 + [0, -6.81, 127.150464]
```

OBJ 정점에 이미 부품별 완성 위치가 들어 있다. 각 메시의 중심을 한 번 빼서 로컬 메시를 만들고, 그 중심을 body 위치로 한 번만 사용한다. 부품별 제품 좌표 회전은 단위행렬이다. 재검사에서 `로컬 정점 + body 중심 == 원본 정점 × 0.024`를 확인하므로 위치를 두 번 더하지 않는다.

작업대에서는 제품 전체에 공통 회전을 적용해 CAD +Y가 위를 향하게 놓는다. 완성 위치는 `resolved_scene.json`의 각 부품 `target_position_world_m`, `target_rotation_world`에 저장된다. 이 배치는 분해 action을 뒤집어서 만든 경로가 아니다.

## 3. 기존 기능을 보존한 연결 경로

```text
복사한 selected_plan + 실제 CAD + 단위 출처
  → import_framework_battery.py: 연속 성공 체인·해시·좌표 검사
  → transfer.json: 부품·완성 자세·고정 베이스·역순 후보
  → prepare_battery_collision.py: 실제 CAD의 볼록 충돌 근사
  → battery_scene.py: UR3e + 원래 2F-85 + 실제 여섯 CAD + 명시 지그
  → 완성 목표에서 근사 형상이 잘못 겹치지 않는지 검사
  → battery_execution.py: 첫 부품의 물리 파지·운반·설치 시도
  → 측정 검증 통과 시에만 다음 부품
```

기존 `planner_candidate_input.py`의 프레임 제품 검사를 우회하지 않았다. 이 파일과 기존 기둥/프레임 장면·실행·결과는 수정하지 않았다. 배터리용 입력 경로를 별도로 추가했다. 현재는 이 6부품/고정 베이스/null-pose 자료를 지원하며 임의 CAD 전체를 지원하는 범용 입력기라고 주장하지 않는다.

기존 `WorkcellMotion`, Mink IK, 관절 위치 제어기, 그리퍼 모델을 재사용한다. 이 경로는 TCP 중간점과 샘플별 충돌 검사를 사용하는 지역 경로다. 배터리 실행에 전역 RRT 우회나 자동 파지 탐색이 연결된 상태는 아니다.

충돌 사전검사의 계산 복사본에서는 후보 관절 자세와 운반 부품의 예상 위치를 계산한다. **실제로 실행하는 장면에는 부품 위치 강제 변경, weld 부착, 외력 보조를 넣지 않았다.** 움직이는 다섯 부품은 free body이고, 그리퍼 접촉·마찰·중력으로 움직인다. original gripper의 내부 연결 제약만 유지한다.

## 4. 명시한 가정과 검사 기준

값은 `config/framework_battery.example.json`에서 바꾼다. 실험실 측정값이 아니라 개발 가정이다.

- 작업대 1.0 × 0.8 m, 높이 0.75 m. UR3e 한 대와 원래 2F-85를 사용한다.
- FRAME 0을 250 × 110 mm 받침 지그에 고정한다. 지그 상면 높이는 0.795 m다. 지그가 셀 아래를 받치므로 성공하더라도 지그 위 배치를 검증한 것이다.
- 다섯 부품은 각각 40 mm 높이 공급 받침 위에서 시작한다. PCB 공급 받침은 좁게 만들었다.
- 셀 질량 50 g, PCB 20 g, 균일 상자 관성, 마찰계수 0.7과 접촉 물성은 가정이다. CAD 형상은 유지하되 셀은 단단한 강체로 취급한다.
- 첫 셀의 TCP는 부품 중심보다 6 mm 위, 파지 방향은 scripted 값이다. LLM이 고른 값이 아니다.
- 원래 그리퍼 패드가 얇은 셀보다 아래로 내려가므로 완전히 잡은 채 설치하면 프레임과 간섭했다. 두 번째 실험에서는 목표 14 mm 위에서 손을 열고 중력으로 안착하는 시도를 했다. **정밀 삽입 완료로 간주하지 않는다.**
- 설치 판정은 목표 위치 오차 ≤1.5 mm, 방향 오차 ≤2°, 손가락 해제, 목표 지지 접촉, 후퇴 후 2초간 선속도 <1 mm/s·각속도 <0.01 rad/s를 모두 요구한다.
- 경로와 실행에서 로봇·그리퍼·운반 부품·대기/설치 부품의 접촉을 검사한다. 허용 지지/파지 외 접촉은 중단하며, 실행 접촉 침투 한도는 0.5 mm다.
- 완성 CAD 위치에서 충돌 근사의 침투가 0.1 mm를 넘으면 물리 실행 전에 중단한다. 이 값도 시뮬레이션 검사를 위한 개발 기준이다.

전기 접속, 납땜, 접착, 파우치 변형, 충전·방전, 실제 배터리 취급은 구현하지 않았다.

## 5. 실제 실행 기록

아래 폴더명은 UTC 시간이며 모두 `runs/framework_battery/` 아래다. `result.json`, `resolved_scene.json`, 물리 trace와 그림을 각 회차에 따로 저장했다.

| 회차 | 기록 폴더 | 실제 결과 |
|---|---|---|
| 초기 장면 | 20261119T160154259697Z | 실제 CAD·UR3e·2F-85 로딩, 공급 받침 위 2초 물리 안정화 통과 |
| 첫 파지/설치 시도 | 20261119T160235842218Z | 양쪽 패드 파지·120 mm 들어올리기·운반 성공. 설치 하강 경로에서 패드-프레임 접촉을 사전 검출하고 실제 접촉 전에 중단 |
| 높은 위치에서 해제 | 20261119T160636868251Z | 파지·운반·14 mm 위 해제·후퇴 실행. 해제/지지/방향/안정성은 통과했으나 목표보다 약 4.3~4.4 mm 높아 위치 기준 실패 |
| 정밀 베이스 근사 검사 | 20261119T161800470187Z | 완성 위치의 가짜 침투 0.5122 mm 검출. 실행 전 중단, 새 파지 실행 없음 |

두 번째 회차의 마지막 관측: 위치 오차 4.315 mm, 방향 오차 약 0.943°, 패드 간격 85.12 mm, 선속도 약 0.0097 mm/s. 손을 뗀 뒤 안정해도 올바른 위치가 아니므로 **실패**다. 최종 판정은 마지막 한 샘플만 보지 않고 안정화 구간 전체로 한다.

원본 CAD 솔리드끼리는 0번/5번의 완성 위치 교집합 체적이 0이었다. 최초 충돌 근사는 같은 위치에서 약 0.729 mm³가 겹쳤고 최대 침투가 0.5132 mm였다. 베이스를 150개에서 752개 볼록 조각으로 세분화해도 최대 침투는 0.5122 mm였다. 따라서 현재 근사는 정확한 끼워 맞춤 검증에 충분하지 않다. 이 수치만으로 실물 부품끼리 간섭한다고 결론 내리지 않는다.

최초 전체 CoACD 계산은 약 56초였다. 전체 고정밀 계산은 300초 상한으로 시도했으며 전체 manifest를 완료하지 못했다. 완료된 베이스 형상만 별도 검사해 `collision_base_refined/manifest.json`에 연결했고 나머지 부품은 최초 근사를 유지했다. 부품 2의 고정밀 시도는 2,896조각까지 늘어나므로 모든 부품의 조각 수만 계속 늘리는 방식은 중단했다. 기록된 작은 체적 누락도 있으며 충돌 근사는 정확 CAD의 대체 증명이 아니다.

**4 → 1 → 3 → 2의 실제 로봇 설치는 실행하지 않았다.** 첫 부품 실패를 무시하고 진행하지 않았다. 특히 긴 PCB의 파지 방향과 이송 경로는 별도 구현·검사가 더 필요하다. `--all-parts`는 순서대로 성공 검증 후 진행하는 확장용 옵션이며 전체 성공이 검증된 명령이 아니다.

## 6. 실행 방법

Windows의 기존 두 Python 환경에서는 MuJoCo DLL이 Windows 응용 프로그램 제어 정책에 차단됐다. 정책을 끄지 않고 WSL의 별도 Linux 환경 `runtime/linux-venv`를 만들었다. WSL 배포판 이름은 `ASAP-Ubuntu`지만 분해 코드와 분해용 Python 환경을 수정하지 않았다. 아래는 이번에 실제 사용한 환경이다.

현재 복사된 자료를 검사하고 장면/실패 그림을 남기기:

```powershell
cd C:\dev\llm-robot-assembly
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly -- bash -lc 'MUJOCO_GL=egl runtime/linux-venv/bin/python scripts/run_framework_battery.py --snapshots --seconds 120'
```

**현재 기대 결과는 목표 형상 검사에서 FAILED다.** 창을 띄우지 않고 실제 MuJoCo로 검사하며 새 결과 폴더와 `failed.png`가 생긴다. 실패 기준을 끄거나 완화해서 실행하지 않는다.

형상 문제가 해결된 뒤 첫 부품의 물리 동작을 실행하는 명령:

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly -- bash -lc 'MUJOCO_GL=egl runtime/linux-venv/bin/python scripts/run_framework_battery.py --execute --snapshots --seconds 240'
```

현재 자료에서는 이 명령도 로봇 이동 전에 같은 형상 검사로 중단된다. 현재 실패를 재현하는 명령이며 조립 성공 명령이 아니다. wall-time 예산은 샘플링 지점에서 확인하므로 모델 컴파일·단일 IK 계산 중에는 즉시 종료되는 강제 제한이 아니다.

입력 검사:

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly -- runtime/linux-venv/bin/python -m unittest discover -s tests -p test_battery_transfer.py -v
```

새 환경 동기화에 실제 사용한 명령:

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly -- bash -lc 'UV_PROJECT_ENVIRONMENT=runtime/linux-venv UV_PYTHON_INSTALL_DIR=runtime/linux-python runtime/linux-bootstrap/bin/uv sync --locked --python 3.11 --extra cad'
```

`linux-bootstrap`의 uv는 이 컴퓨터에 준비된 도구다. 일반적인 다른 컴퓨터에서는 uv 설치 후 `uv sync --locked --extra cad`를 사용한다. 원본 자료/단위 출처 없이 형상을 자동 생성하지 않는다.

처음부터 새 묶음을 만드는 경우 아래처럼 실행한다. 기존 묶음은 덮어쓰지 않으므로 현재 폴더에는 재실행하지 않는다.

```powershell
runtime\luna-venv\Scripts\python.exe scripts\import_framework_battery.py --source-root C:\dev\disassembly_codex_ready_v2 --source-step "C:\Users\namyeon\OneDrive\문서\ChatGPT\졸업프로젝트\공개CAD후보\산업조립품_추가\production\fw-battery\source.step" --output runtime\products\framework_battery_v2
```

충돌 근사 재생성 도구는 `prepare_battery_collision.py --bundle <새묶음>`이다. 베이스만 계산하려면 `--output-name collision_fine --threshold 0.003 --parts 0`을 사용하고, `compose_battery_collision.py --bundle <새묶음>`으로 나머지 부품의 근사와 묶는다. `--parts`는 전체 고정밀 계산의 낭비를 줄이기 위해 추가한 선택 옵션이며 이번 완료된 베이스는 앞의 전체 계산 시도에서 나온 것이다. 이 조합 역시 현재 끼워 맞춤 기준을 통과하지 못한다.

## 7. 검사와 다음 작업

- 새 입력 검사 6개 통과: 연속 체인, 다른 분기 혼입 거부, 미완료 체인 거부, 추가 지지 거부, 실패 edge 거부, 단위/좌표 복원.
- 기존 관련 검사 9개 통과: `test_frame_scene.py` 4개, `test_planner_candidate_input.py` 3개, `test_triangle_planner_input.py` 2개. 기존 전체 시연을 새로 돌린 것은 아니다.
- 원본 CAD/근거 12개 SHA-256 동일. 기존 기둥·프레임 실행 코드 변경 없음. 기존 결과 삭제/교체 없음.
- 로컬 CAD/결과/이미지/환경은 runtime와 runs에만 두고 Git에서 제외했다. 추가 패키지는 선택 의존성 CoACD이고 버전은 uv.lock에 기록했다.

다음 순서는 **(1) 베이스와 셀의 국소 충돌 형상을 원본 표면과 대조해 정확도를 확보 → (2) 원래 2F-85 패드가 프레임을 치지 않는 파지/접근 또는 명시된 전용 지그를 설계 → (3) 5번의 정확한 설치·해제·안정성 재검증 → (4) 나머지 셀과 PCB의 실제 경로 검증 → (5) 검증된 실행기에 조립/복구 에이전트를 연결**이다. 모델 변경, 추가 실험 시간, 새 유료 호출은 별도 범위를 알리고 진행한다.

현재 실패는 분해 플래너의 순서가 틀렸다는 증거가 아니다. **부품 수준의 분해 가능성과, 이 그리퍼/지그를 사용하는 로봇 조립 가능성은 별도 검사**라는 점을 실제 기록으로 확인한 단계다.
