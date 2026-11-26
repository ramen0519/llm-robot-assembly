# LLM Robot Assembly · 일반 메시 v2

## 2026-11-26 공통 프로토타입

[구현 변경](docs/STEP_47_PROTOTYPE_FLOW.md) · [실제 시험과 실행 명령](docs/STEP_48_PROTOTYPE_VALIDATION.md) · [완성 CAD 입력 연결 복구](docs/STEP_49_NATIVE_INPUT_BRIDGE.md) · [CAD부터 실제 조립과 경험 재사용](docs/STEP_50_RAW_CAD_ASSEMBLY.md)

배터리 재시험 전에 초기 배치, 제한된 동작 이어가기, 놓기·후퇴 복구, 중단 상태 저장, 실패 경험의 생성·다음 검색, 쉬운 UI 상태를 연결했다. 실제 MuJoCo scripted 시험과 실제 Astra 실행을 구분한다. API 없는 개발 지침은 격리한 메모리에서만 생성한다. 최신 실제 Astra high 시험은 첫 부품의 주 장면 설치까지 통과한 **1/2**, 두 번째 사전검사의 관찰 중 시간 한도로 종료됐다. 후속 API0 복사 관찰은 통과했지만 전체 성공으로 합산하지 않는다. 비용 추정 $0.258, 중단 분류 수정과 136개 회귀 검사는 [STEP 53](docs/STEP_53_ASTRA_UI_TRIAL.md)에 기록했다. 배터리 전체 조립은 아직 미완료다. WSL 보통 접속 문제는 동일 환경의 명시적 대체 접속으로 보완했고, 순서가 없는 3부품 CAD 목록→실제 native 분해 검증→MuJoCo 준비를 확인했다. 별도 소형 STEP 단일 파일도 단위·3부품·완성 배치 추출, 실제 분해 검증, MuJoCo 준비까지 확인했다([STEP 51](docs/STEP_51_STEP_INPUT_READY.md)). 전달 export/CAD 순서 hint 경로도 유지한다.


### 분해 순서를 추천으로 받아 조립하기

[사용 예와 검증 결과](docs/STEP_31_DISASSEMBLY_ORDER_HINT.md)

`assembly_order_hint/v1`의 `assembly_hint.json`을 UI의 입력 경로 또는 `python scripts/run_assembly.py prepare --input <assembly_hint.json>`에 넣는다. 같은 입력을 `run`에도 사용할 수 있다. 파일은 CAD 목록·단위·목표 배치와 일치하는 부품 순서를 지정한다. 첫 부품을 지그 후보로 두고, 나머지 순서는 조립의 출발점으로 사용한다. 손이 비어 있고 아직 주 장면을 움직이지 않았으면 Astra가 부품을 미룰 수 있다. 다른 부품을 설치한 뒤 다시 고려한다.

조립기는 CAD에서 직접 충돌 형상을 만들고 MuJoCo에서 완성 목표의 안정성, 파지, 이동 경로, 놓은 뒤의 위치 유지를 검사한다. `--policy astra --condition R0 --split collection`은 새 복구 경험을 수집한다. 기존 `disassembly_export/v1`은 분해 물리 경로까지 전달할 때 사용하는 별도 입력이다.

MuJoCo · UR3e 한 대 · Robotiq 2F-85로 완성 CAD 목표의 강체 조립을 시도한다. 최신 요구는 [구현 작업서 v2.0](docs/design_v2/3-ASSEMBLY_CODEX_HANDOFF_v2.0.md)과 [완료 프로파일 v1.2](docs/design_v2/2-ASSEMBLY_COMPLETION_PROFILE_v1.2.md)다. 이전 사각 형상·top-down·수직 삽입·10부품 제한은 현재 지원 범위의 정의가 아니다.

**구현 범위와 실제 검증은 다르다.** 실제 Astra high의 2부품 조립과 같은 입력의 5회 시범 수집(5/5)을 확인했다. 별도 개발 시험에서는 실제 Astra 복구·지침 생성/등록과 다음 실행의 DINO 검색·물리 재검증을 확인했다. 일반 형상 포함 10부품 이상 전체 완주는 아직 미검증이다. [최신 실제 결과와 미완료 사항](docs/STEP_30_LIVE_ASTRA_V2.md), [일반 메시 구현 범위](docs/STEP_29_GENERAL_MESH_V2.md)를 확인한다.

## 실행 흐름

완성 목표 CAD → 실제 native 분해 검증/선택된 연속 체인 → 메시·충돌 형상·초기 배치 → 별도 완성 기준 장면 검사 → Python 표면 파지 후보 / Astra 전체 pose 선택·수정 → 독립 복사 장면의 파지·설치 검사 → 주 장면의 접촉·마찰 집기와 이동·설치·해제 → 공통 완료 검사 → 경험·해결지침 등록 검토 → 다음 실행의 조건 필터와 이미지 유사도 검색.

완료는 원 CAD 기준 고정 베이스 좌표계 XYZ **각각 5 mm**, 상대 회전각 3°, 최종 경질 관통 0.5 mm 이하, 로봇 비접촉/그리퍼 5 mm 이격, 속도 기준, 정착 1초 뒤 **연속 1초 501개 경계 샘플(기준 0.002초)**로 판정한다. 실제로 정의된 직선 삽입은 깊이·사전 정렬 검사도 필수다. 기준 장면·계획 후보·복사 검사 통과를 주 실행 성공으로 바꾸지 않는다.

## 현재 컴퓨터에서 시작

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B scripts\run_assembly.py status
& $assemblyPython -B scripts\run_assembly.py ui --port 8765
```

UI: **http://127.0.0.1:8765**. 입력 선택, 환경 상태, 현재 단계, 목표 이미지, 실제 결과·영상·실패·등록·검색 기록을 보여준다. UI도 같은 Python 실행기를 호출한다. 실물 로봇 명령은 없다.

현재 컴퓨터에서는 사용자 승인에 따라 새 결과와 메모리를 `F:\dev\llm-robot-assembly-data`에 저장한다. 코드는 C:에 유지한다. `config/local.storage.v2.json`의 `data_root` 또는 `ASSEMBLY_DATA_ROOT`로 공통 저장 위치를 바꿀 수 있다. 기존 기록은 이동/삭제하지 않았다. UI를 위 명령으로 시작한 뒤 최근 실행 목록에서 CLI 결과도 볼 수 있다. 서버 실행 여부는 별도로 확인한다.

완성 CAD 자료 한 개로 시작:

```powershell
& $assemblyPython -B scripts\run_assembly.py run --input 'C:\path\to\product.step' --policy astra --condition R0 --seconds 600
```

원본 STEP/OBJ는 공통 [분해 연결 설정](config/disassembly_bridge.wsl.example.json)의 환경이 필요하다. 현재 환경의 연결 여부는 완료 보고서에 기록했다. OBJ에는 단위와 완성 배치 정보가 필요하며, 파일에 이미 배치가 반영됐다면 `--assembled-obj --unit mm`처럼 선언한다. 알 수 없는 단위나 부족한 목표를 추측하지 않는다. 검증 export를 입력하면 기존 native 증거를 재사용한다.

API 없는 통합 동작 확인:

```powershell
& $assemblyPython -B scripts\run_assembly.py run --input 'runs\clean_start_validation\input\export_v1.json' --policy scripted --condition R0 --seconds 600
```

이 명령의 입력은 **로컬에 유지된 검증 자료**이며 Git clone에 포함되지 않는다. 새 컴퓨터에서는 원본 CAD로 분해 연결을 실행하거나 별도로 보관한 검증 export를 전달해야 한다. scripted 성공은 Astra 성공이 아니다.

## 새 컴퓨터의 공통 환경

```powershell
uv sync --extra vision
uv pip install --python .venv\Scripts\python.exe --index-url https://download.pytorch.org/whl/cpu 'torch==2.5.1+cpu'
```

- 로봇·그리퍼 XML/메시와 라이선스는 `models/`, 모션 플래너는 `third_party/`에 유지한다.
- native ASAP/RedMax 및 CAD importer는 공통 bridge 설정에서 한 번 연결한다. WSL을 임의로 초기화하지 않는다.
- DINO 가중치는 `runtime/assets/dinov2-small`에 별도 준비한다. 코드가 지정한 가중치 SHA-256과 모델/전처리 파일을 검사한다. 가중치가 없으면 이미지 순위 계산을 미실행으로 남긴다.
- Astra high 설정은 `config/llm.astra-high.toml`, 키는 `OPENAI_API_KEY` 환경변수다. 키 값은 인수·코드·로그에 쓰지 않는다. 설정 상한은 유료 실행의 새 승인이 아니다.
- 공통 로봇/작업대/물성/자원 설정은 `config/assembly_workcell.v2.json`이다. 제품별 좌표 registry나 실행 코드를 추가하지 않는다.

## 코드와 보존 범위

- `scripts/assembly_v2/`: 현재 입력·메시·파지·실행·완료·경험·UI·수집 경로.
- `scripts/run_assembly.py`, `scripts/run_assembly.ps1`: 현재 진입점.
- `scripts/assembly_v12/`, `scripts/run_assembly_v12.py`: 이전 사각 프레임의 명시적 회귀 경로. 완료 기준·연구 수집에는 v2를 사용한다.
- 공통 Mink IK, RRT-Connect, 관절 보간·서보 제어는 기존 코드를 재사용한다. Python과 MuJoCo가 수치/물리 검사를 수행하며 Astra가 모터 값을 직접 계산하지 않는다.
- 기본 경험은 데이터 루트의 `runtime/memory/assembly_v2`. 이번 작업에서 이전 경험·실행 자료를 삭제하거나 성공 사례로 복원하지 않았다. 의도한 실패의 실제 Astra 지침은 `runtime/memory/assembly_v2_development_probe`에 격리하고 연구 수집에서 제외한다.

수집·동결·평가·재생 명령은 [운영 안내](docs/ASSEMBLY_V2_OPERATIONS.md), 요구별 완료/미완료는 [STEP 29](docs/STEP_29_GENERAL_MESH_V2.md), 다음 작업은 [프로젝트 계획](docs/PROJECT_PLAN.md)에 있다.

```powershell
& $assemblyPython -B -m unittest discover -s tests -p 'test_v2*.py' -v
& $assemblyPython -B scripts\run_assembly.py audit --run 'runs\RUN_NAME'
& $assemblyPython -B scripts\run_assembly.py replay --run 'runs\RUN_NAME'
```

재생은 기록된 실제 상태의 영상화이며 새 물리 시험이 아니다. 나사 체결·클립 잠김·접착·전기적 연결·실물 배터리 취급을 구현했다고 주장하지 않는다.

연구실 배터리팩으로 확인한 준비·접촉 검사·파지·설치 접근 보완과 실제 제한은 [STEP 34](docs/STEP_34_BATTERY_AUTONOMY.md)에 정리했습니다. 현재 배터리 전체 조립 성공을 의미하지 않습니다.
- [배터리팩 실제 Astra 재검증: 실패 피드백과 파지 수정](docs/STEP_35_BATTERY_ASTRA_RETRY.md)
- [배터리팩 대체 파지·손가락·공구 비교](docs/STEP_36_BATTERY_TOOL_OPTIONS.md)

- [배터리팩 실제 Astra 파지·복구 시험](docs/STEP_37_BATTERY_ASTRA_TOOL_TRIAL.md)

- [배터리팩 검사 시간 단축과 동일 판정 검증](docs/STEP_38_BATTERY_VERIFICATION_SPEED.md)

- [API 없이 파지·설치 충돌 수정과 실제 한 부품 후속 검증](docs/STEP_40_OFFLINE_CLOSURE_FEEDBACK.md)

- [LLM 조립 관련 연구 14편과 현재 실행 문제 비교](docs/LLM_ASSEMBLY_RESEARCH_REVIEW_20261125.md)
- [실패 단계 분리와 실제 후속 검증](docs/STEP_42_RESEARCH_GUIDED_EXECUTION.md)

## 짧은 동작 계획과 배터리 최신 개발 검증

`run --motion-plan-mode bounded`(기본값)는 Astra가 파지뿐 아니라 접근 거리, 들기 높이, 최대 두 운반 중간 위치, 설치 접근 여유/방향, 기존 해제 방식을 제한된 범위에서 제안하도록 한다. `fixed`는 공통 기본 계획을 사용한다. Python과 MuJoCo의 충돌·힘·해제 후 완료 기준은 동일하다.

현재 연구실 배터리의 검증된 scripted 후속 분기는 **6/12 설치**다. 전체 Astra 연속 조립은 완료하지 않았다. 별도의 실제 Astra high 단일 들기 비교에서는 두 조건 모두 성공했고, 생성 2회의 비용 추정은 약 $0.096이었다. 한 번의 비교를 전체 속도나 비용 우위로 해석하지 않는다. 새 계획에 대한 경험 수집/일반화 평가는 아직 수행하지 않았다.

- [실행기 안정화·6/12 근거](docs/STEP_43_EXECUTION_STABILITY.md)
- [제한된 동작 계획 계약·실행·경험 연결](docs/STEP_44_BOUNDED_MOTION_PLAN.md)
- [실제 Astra 비교·비용·영상·재현 명령](docs/STEP_45_ASTRA_MOTION_COMPARISON.md)

- [물리 계산 간격 0.002초/0.004초 실제 비교와 제한](docs/STEP_46_TIMESTEP_SENSITIVITY.md)

## 2026-11-26 물리 계산 간격 개발 옵션

기본값은 0.002초다. `run --physics-timestep 0.004`로 개발 실험을 선택할 수 있다. 위치·각도·접촉·속도 한도와 관찰 1초는 유지하며 관측 횟수는 251개로 별도 기록한다. 0.01초 새 장면은 그리퍼 관절 검사에서 실패했고, 0.004초는 작은 조립체 2/2 설치를 197.843초에 확인했다(API 없는 scripted 실행). 새 Astra 또는 배터리 전체 성공을 뜻하지 않는다. [변경·실측·실행 명령](docs/STEP_54_TIMESTEP_VARIANTS.md).

최신 배터리 0.004초 Astra 시험은 초기 정착 통과 후 첫 부품의 해제/설치 자세 간섭으로 주 장면 0/12에서 종료했다. 실제 생성 3회·추정 $1.1528055이며 초기 받침과 후보 전달의 중복을 수정했다. [실패 근거·수정·실행 자료](docs/STEP_55_BATTERY_4MS_ASTRA.md).


공통 집기·설치·해제 함수의 최근 검증은 [STEP 56](docs/STEP_56_MEASURED_MOTION_PRIMITIVES.md)을 참고한다. API 없는 소켓 2/2 회귀와 배터리 3/12 설치를 구분하며, 배터리 전체 완료나 실제 Astra 검증으로 표시하지 않는다.

설치된 상태를 검증하고 API 없이 계속하는 명령은 [STEP 57](docs/STEP_57_RELEASED_CONTINUATION.md)에 있다. 기존 설치 증거와 저장 상태를 검사하고 새 연속 관찰을 통과해야 남은 작업을 시작한다. 전체 개발 시간 제한만 선택적으로 없앴으며 물리 판정은 유지한다.

- [STEP 58: 배터리 6개 실제 설치와 접근 거리 재시도](docs/STEP_58_BATTERY_HALF_AND_APPROACH_FEEDBACK.md)
