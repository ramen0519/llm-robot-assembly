# LLM Robot Assembly · 일반 메시 v2

MuJoCo · UR3e 한 대 · Robotiq 2F-85로 완성 CAD 목표의 강체 조립을 시도한다. 최신 요구는 [구현 작업서 v2.0](docs/design_v2/3-ASSEMBLY_CODEX_HANDOFF_v2.0.md)과 [완료 프로파일 v1.2](docs/design_v2/2-ASSEMBLY_COMPLETION_PROFILE_v1.2.md)다. 이전 사각 형상·top-down·수직 삽입·10부품 제한은 현재 지원 범위의 정의가 아니다.

**구현 범위와 실제 검증은 다르다.** 일반 메시 경로, 전체 파지 pose, 연속 완료 검사, 기록·검색·UI 연결을 추가했다. 실제 Astra 및 새 CAD의 전체 native→로봇 연결은 키·분해 실행 환경·자산 준비가 갖춰져야 검증된다. [실제 수행 결과와 미완료 사항](docs/STEP_29_GENERAL_MESH_V2.md)을 먼저 확인한다.

## 실행 흐름

완성 목표 CAD → 실제 native 분해 검증/선택된 연속 체인 → 메시·충돌 형상·초기 배치 → 별도 완성 기준 장면 검사 → Python 표면 파지 후보 / Astra 전체 pose 선택·수정 → 독립 복사 장면의 파지·설치 검사 → 주 장면의 접촉·마찰 집기와 이동·설치·해제 → 공통 완료 검사 → 경험·해결지침 등록 검토 → 다음 실행의 조건 필터와 이미지 유사도 검색.

완료는 원 CAD 기준 고정 베이스 좌표계 XYZ **각각 5 mm**, 상대 회전각 3°, 최종 경질 관통 0.5 mm 이하, 로봇 비접촉/그리퍼 5 mm 이격, 속도 기준, 정착 1초 뒤 **연속 1초 501개 경계 샘플**로 판정한다. 실제로 정의된 직선 삽입은 깊이·사전 정렬 검사도 필수다. 기준 장면·계획 후보·복사 검사 통과를 주 실행 성공으로 바꾸지 않는다.

## 현재 컴퓨터에서 시작

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B scripts\run_assembly.py status
& $assemblyPython -B scripts\run_assembly.py ui --port 8765
```

UI: **http://127.0.0.1:8765**. 입력 선택, 환경 상태, 현재 단계, 목표 이미지, 실제 결과·영상·실패·등록·검색 기록을 보여준다. UI도 같은 Python 실행기를 호출한다. 실물 로봇 명령은 없다.

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
- 새 기본 경험은 `runtime/memory/assembly_v2`. 이번 작업에서 이전 경험·실행 자료를 삭제하거나 성공 사례로 복원하지 않았다. 개발 검사용 메모리는 별도 run 폴더에 격리한다.

수집·동결·평가·재생 명령은 [운영 안내](docs/ASSEMBLY_V2_OPERATIONS.md), 요구별 완료/미완료는 [STEP 29](docs/STEP_29_GENERAL_MESH_V2.md), 다음 작업은 [프로젝트 계획](docs/PROJECT_PLAN.md)에 있다.

```powershell
& $assemblyPython -B -m unittest discover -s tests -p 'test_v2*.py' -v
& $assemblyPython -B scripts\run_assembly.py audit --run 'runs\RUN_NAME'
& $assemblyPython -B scripts\run_assembly.py replay --run 'runs\RUN_NAME'
```

재생은 기록된 실제 상태의 영상화이며 새 물리 시험이 아니다. 나사 체결·클립 잠김·접착·전기적 연결·실물 배터리 취급을 구현했다고 주장하지 않는다.
