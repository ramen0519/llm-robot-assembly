# 일반 메시 조립 v2 운영 안내

일반 메시 구현은 [STEP 29](STEP_29_GENERAL_MESH_V2.md), 이전 실제 Astra 실행은 [STEP 30](STEP_30_LIVE_ASTRA_V2.md), 최신 UI 시범과 시간 한도 중단은 [STEP 53](STEP_53_ASTRA_UI_TRIAL.md)에 기록한다. 이 문서의 실행 예제 자체는 성공 증거가 아니다. 새 제품의 순서만 뒤집어 성공했다고 판단하지 않는다.

## 1. 최초 공통 설정

현재 컴퓨터에서는 다음 환경을 사용한다.

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B scripts\run_assembly.py status
```

새 컴퓨터는 Python 3.11과 uv를 준비한 뒤 `uv sync --extra vision`으로 `uv.lock`의 의존성을 설치한다. 이미지 검색을 쓸 때 CPU PyTorch도 설치한다.

```powershell
uv sync --extra vision
uv pip install --python .venv\Scripts\python.exe --index-url https://download.pytorch.org/whl/cpu 'torch==2.5.1+cpu'
$assemblyPython = '.\.venv\Scripts\python.exe'
```

현재 PC의 기본 `.venv` 실행 파일은 Windows 앱 제어 정책에 차단되어 있어, 기존에 동작하는 `runtime/luna-venv`를 사용했다. OS 보안 설정을 변경하지 않았다. 렌더링과 실제 MuJoCo 물리는 이 환경에서 실행했다. 대화형 MuJoCo viewer의 DLL 차단은 별개이며 UI의 저장 영상 확인은 가능하다.

### 공통 작업장

현재 컴퓨터의 공통 데이터 루트는 `F:\dev\llm-robot-assembly-data`다. 설정 파일은 Git에서 제외한 `config/local.storage.v2.json`이며 내용은 `{"data_root":"F:/dev/llm-robot-assembly-data"}`다. 다른 컴퓨터에서는 폴더를 정한 뒤 동일 설정을 만들거나 `ASSEMBLY_DATA_ROOT` 환경변수를 사용한다. 기본 CLI 결과, UI 결과/업로드, 기본 메모리가 이 루트를 공유한다. `--output`, `--memory-root`를 명시하면 해당 값이 우선한다. 입력 CAD·기존 실행·소스 코드를 자동 이동하지 않는다. E:는 계속 거부한다.

`config/assembly_workcell.v2.json`에서 로봇 설치 위치·작업대 높이·전체 조립 위치/회전·공급 영역·물성·자원 한도를 설정한다. 이 값은 모든 제품에 공통이다. 현재 밀도 800 kg/m³와 마찰 등은 측정값이 아닌 가정값이다. 고정 베이스 하나를 지그로 지지하고 나머지는 자유 강체로 둔다.

`config/completion.v1.2.json`은 승인된 완료 기준이다. 실패를 통과시키려고 수치를 변경하지 않는다. 그리퍼 전체 자세와 실제 메시를 검사하며, 원 CAD의 부품 상대 위치/회전을 바꾸지 않는다.

### 분해 연결

`config/disassembly_bridge.wsl.example.json`은 현재 공통 연결 예제다. native ASAP/RedMax와 STEP importer가 설치된 Python 및 저장소 경로를 확인한다. 원 분해 프로젝트는 수정하지 않고 조립 저장소의 기존 연결용 사본을 호출한다.

```powershell
& .\runtime\luna-venv\Scripts\python.exe -B scripts\run_assembly.py check-bridge --output 'F:\dev\llm-robot-assembly-data\research\NEW_BRIDGE_CHECK' --seconds 30
```

이 명령이 `bridge_ready`를 반환해야 raw CAD→native 검증→export 연결을 실행할 수 있다. 보통 접속이 5초 안에 응답하지 않으면 설정에서 켠 동일 배포판의 systemd 환경 접속을 시도한다. systemd PID와 동일 native 파일 해시·RedMax import를 검사하며 Linux root 계정을 쓰는 명시적 공통 설정이다. [실제 검증과 한계](STEP_49_NATIVE_INPUT_BRIDGE.md)를 확인한다. 작업 중인 다른 WSL 환경을 임의 종료하거나 초기화하지 않는다. 이미 검증된 `export_v1.json`과 그 전체 증거 폴더를 입력하면 이 단계를 재실행하지 않는다. 다른 분기의 성공 기록은 섞지 않는다.

### DINO 가중치

로컬 `runtime/assets/dinov2-small`에 `config.json`, `preprocessor_config.json`, `model.safetensors`가 필요하다. 현재 사용하는 고정 원본은 `facebook/dinov2-small`, revision `ed25f3a31f01632728cabb09d1542f84ab7b0056`이다. 가중치 SHA-256은 `ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1`로 검사한다. 설치된 huggingface_hub에서 해당 revision의 세 파일을 내려받을 수 있다. 가중치 전체를 Git에 넣지 않는다.

```powershell
& $assemblyPython -c "from huggingface_hub import snapshot_download; snapshot_download('facebook/dinov2-small', revision='ed25f3a31f01632728cabb09d1542f84ab7b0056', allow_patterns=['config.json','preprocessor_config.json','model.safetensors'], local_dir='runtime/assets/dinov2-small')"
```

### 모델 설정과 키

`config/llm.astra-high.toml`: Astra high, 전역 생성 요청 최대 36회·요청당 입력 24,000/출력 2,048 토큰·회차 전체 361,728토큰·설정 비용 상한 $1.50. 이 설정은 새로운 지출 승인이 아니다. 단일 역할과 조립/복구 역할은 한 모델·공유 대화·도구·전역 장부를 사용한다. 제어 생성 `4N+4`는 전역 예산과 별도로 검사한다.

키가 실행 프로세스에 없으면 `not_run`으로 종료한다. 이미 등록한 키를 해당 셸에 전달하거나, 필요한 경우 값을 출력하지 않고 입력한다.

```powershell
$assemblyKey = Read-Host 'API 키' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $assemblyKey).Password
```

키는 CLI 인수·설정 파일·실행 로그에 기록하지 않는다. 유료 수집은 실제 승인된 잔여 총량이 명시된 별도 파일이 있어야 실행된다.

## 2. 입력 하나로 시작

```powershell
& $assemblyPython -B scripts\run_assembly.py run --input 'C:\path\product.step' --policy astra --condition R0 --seconds 600
```

`--input`은 완성 배치가 있는 STEP, 부품 목록과 목표 변환/단위가 있는 CAD 묶음, native 검증 export, 검증된 v2 준비 자료를 받는다. OBJ 자체는 단위가 없으므로 원 출처에서 확인한 단위를 `--unit mm` 등으로 제공한다. 완성 위치를 OBJ 정점에 이미 포함했다면 `--assembled-obj`로 선언한다. raw CAD zip과 native export zip을 구분한다. 개별 JSON 파일을 업로드할 경우 참조 OBJ들도 접근 가능한 위치에 있어야 하므로, 여러 파일은 폴더 경로나 전체 묶음을 사용한다.

```powershell
& $assemblyPython -B scripts\run_assembly.py prepare --input 'C:\path\product.step' --output runs\prepared_product --seconds 300
& $assemblyPython -B scripts\run_assembly.py run --input runs\prepared_product\prepared.json --policy astra --condition R0 --seconds 600
```

`prepare`는 형상·배치 준비다. 물리 조립 성공을 뜻하지 않는다. 이미 만든 준비 자료도 원본 형상·단위·목표·경로·충돌 자산을 재검사한다. 새 실행은 새 폴더를 사용한다.

## 3. API 없는 개발 확인

현재 PC에 보존된 native 검증 2부품 입력:

```powershell
& $assemblyPython -B scripts\run_assembly.py run --input runs\clean_start_validation\input\export_v1.json --policy scripted --condition R0 --seconds 600
& $assemblyPython -B scripts\run_assembly.py run --input runs\clean_start_validation\input\export_v1.json --policy scripted --condition R1 --first-grasp config\v2_development_rejected_grasp.json --seconds 600
```

두 번째는 **의도한 개발 교란**이다. 잘못된 첫 파지가 복사 검사에서 거절되면 주 장면을 유지한 채 다음 후보를 검사한다. scripted·개발 지침은 연구용 Astra 수집에 포함되지 않는다. 이 로컬 입력과 실행 결과는 Git에 포함되지 않는다.

일반 형상 진단은 별도다. native 체인을 만들어낸 것처럼 위장하지 않는다.

```powershell
& $assemblyPython -B scripts\check_v2_general.py --shape oblique --attempt 1 --output runs\oblique_check_new --seconds 300
& $assemblyPython -B scripts\check_v2_general.py --shape capacity --prepare-only --output runs\capacity_check_new --seconds 300
```

## 4. 로컬 UI

```powershell
& $assemblyPython -B scripts\run_assembly.py ui --port 8765
```

브라우저에서 http://127.0.0.1:8765 를 연다. 제품 입력 → 준비/실행 선택 → 현재 부품/단계 → 결과 링크 → 측정값·실패·실제 영상·등록/검색 기록 순서다. UI는 같은 CLI를 실행한다. 개발용 로컬 서버이며 외부 공개·실물 로봇 제어 기능은 없다.

현재 코드를 반영하려면 위 명령으로 UI를 시작한다. 서버가 항상 실행 중이라고 가정하지 않는다. 최근 실행 목록에는 현재 데이터 루트와 기존 C: `runs`의 CLI/UI 결과가 함께 표시된다. 작업용 임시 키는 상시 서버에 영구 저장하지 않는다. 새 유료 실행을 UI에서 시작하려면 키가 있는 셸에서 UI를 새로 시작해야 한다. 기존 결과/영상 열기에는 키가 필요 없다.

S0/S1은 단일 역할, R0/R1은 조립·복구 역할 분리다. 0은 검색 끔, 1은 검색 켬. UI는 development 실행이며 연구 수집/평가는 아래 동결 계획으로 실행한다.

## 5. 실제 수집 → 등록 → 동결 → 평가

`config/collection_inputs.v2.example.json`을 `config/local.collection_inputs.json`으로 복사하고 실제 준비 자료 경로와 원본 lineage를 적는다. 제품 좌표를 작성하는 파일이 아니라 **실험 대상 목록**이다. collection은 R0 또는 S0, 실제 Astra, 검색 OFF다.

```powershell
& $assemblyPython -B scripts\run_assembly.py plan-trials --manifest config\local.collection_inputs.json --output runs\collection_plan.json
& $assemblyPython -B scripts\run_assembly.py collect --manifest runs\collection_plan.json --output runs\pilot_new --max-runs 5 --paid-budget config\local.approved_budget.json
```

`local.approved_budget.json`에는 실제 기존 승인의 근거와 **잔여 총 실행/요청/토큰/시간/비용**만 입력한다. `config/aggregate_budget.v2.example.json`은 전부 0인 실행 불가 예제다. 기존 회차 $1.50를 새 5회 승인으로 확대하지 않는다. 승인 파일·키가 없거나 다음 실행을 예약할 예산이 없으면 0회 `not_run`/비정상 종료 코드를 남긴다.

시범을 포함한 한 묶음에서 5회 후 기록/저장에 문제가 없을 때 본 수집까지 이어가는 명령:

```powershell
& $assemblyPython -B scripts\run_assembly.py collect --manifest runs\collection_plan.json --output runs\pilot_and_main_new --max-runs 5 --continue-main --paid-budget config\local.approved_budget.json
```

위 두 collect 명령은 대안이다. 둘 다 실행하면 시범이 중복된다. 이미 수행한 별도 시범 뒤 새 batch를 실행한다면 잔여 대상과 실제 잔여 예산으로 새 계획을 작성한다. 기존 batch를 덮어써 장부를 초기화하지 않는다.

2026-11-25의 실제 시범은 `F:\dev\llm-robot-assembly-data\runs\v2_astra_pilot_01`에 있다. 당시 manifest는 같은 원본 입력 5회이며 모두 소비했다. 이를 새 제품의 본 수집 목록으로 간주하지 않는다. 이후 지침 형식 수정으로 코드 inventory가 달라졌으므로 다음 실험은 입력 manifest로 `plan-trials`를 다시 실행해야 한다. 이번 승인 총량과 실제 정산은 로컬 `runtime/session_spend_20261124.json`에 보존한다. 이 장부는 해당 세션 실행기의 기록이며 일반 CLI의 새 batch 예산을 자동 갱신하지 않는다. 새 승인 파일에는 실제 남은 한도만 적는다.

`batch_result.json`의 started/completed/physical_success/eligible/generated/registered/pending/duplicate, 개별 audit·영상·사용량을 확인한다. 물리 실패 자체를 삭제하지 않는다. 해결된 사건도 지침 생성 예산이 부족하면 pending이며, 일반 성공 로그만으로 해결 사례가 생기지 않는다.

등록된 연구 경험을 확인한 다음 동결한다.

```powershell
& $assemblyPython -B scripts\run_assembly.py freeze --root runtime\memory\assembly_v2 --output runs\memory_frozen.json
& $assemblyPython -B scripts\run_assembly.py plan-trials --manifest config\local.evaluation_inputs.json --snapshot runs\memory_frozen.json --output runs\evaluation_plan.json
& $assemblyPython -B scripts\run_assembly.py evaluate --manifest runs\evaluation_plan.json --output runs\evaluation_new --max-runs 4 --paid-budget config\local.evaluation_budget.json
```

평가 목록은 collection과 다른 원본 입력을 사용하고 split=evaluation으로 둔다. 한 입력마다 S0/S1/R0/R1 네 조건을 자동 배치한다. 동결 후 코드·환경·원본·자산·모델 설정 변경은 계획 재작성 사유다. 이름만 바꾼 같은 원본은 독립 평가로 인정하지 않는다. 같은 입력에서 다음 검색을 확인하는 기능 시험은 development로 별도 기록한다.

## 6. 증거 확인·재생

```powershell
& $assemblyPython -B scripts\run_assembly.py audit --run runs\RUN_NAME
& $assemblyPython -B scripts\run_assembly.py report --run runs\RUN_NAME
& $assemblyPython -B scripts\run_assembly.py replay --run runs\RUN_NAME
```

`report.html`, `media/execution.mp4`, `final_verification.json`, `events.jsonl`, `incidents.json`, `retrieval.jsonl`, `registrations.json`, `metrics.csv`를 확인한다. 실패 PNG와 정확한 MuJoCo 상태는 `failures/`에 있다. `replay_bundle/`에는 실제 모델·상태·입력·충돌 자산·코드 hash가 들어간다. 원 run 없이 이 묶음 경로를 `--run`에 주어 재생할 수 있다. 재생은 저장 상태의 영상화이며 새 물리 시험이 아니다.

현재 저장 상태는 20 Hz, 영상은 그중 실제 프레임을 선택한 5 fps다. 사이 프레임을 보간해 새 동작을 만들지 않는다. 기준 물리 검사는 2 ms 간격으로 별도 수행한다. 명시적 개발 실험은 기록된 4/10 ms 간격을 따른다. 주 실행 영상에는 copy/planner 상태가 섞이지 않는다.

## 7. 실제 Astra 복구·지침 재사용의 개발 시험

다음은 정상 입력에서 모델의 자연 실패율을 재는 명령이 아니다. 명시적으로 낮은 첫 파지를 복사 장면에서 검사해 간섭을 유발하고, 그 결과부터 실제 Astra가 복구/설치/지침 생성을 수행한다. 첫 검사 후보는 모델이 제안했다고 표시하지 않으며 주 장면에서는 실행할 수 없다. split=development에서만 허용하고, collection/evaluation에는 거절된다.

```powershell
& $assemblyPython -B scripts\run_assembly.py run --input runs\v2_memory_reuse_physics_01\prepared.json --policy astra --condition R0 --development-failure-probe config\v2_development_rejected_grasp.json --memory-root F:\dev\llm-robot-assembly-data\runtime\memory\assembly_v2_development_probe --seconds 600
& $assemblyPython -B scripts\run_assembly.py run --input runs\v2_memory_reuse_physics_01\prepared.json --policy astra --condition R1 --development-failure-probe config\v2_development_rejected_grasp.json --memory-root F:\dev\llm-robot-assembly-data\runtime\memory\assembly_v2_development_probe --seconds 600
```

첫 실행의 등록은 다음 snapshot부터 검색된다. R1의 `retrieval.jsonl` → `events.jsonl`의 실제 Astra 제안·재검증 → `final_verification.json`을 함께 확인한다. 이 개발 사례를 독립 평가 메모리에 복사하지 않는다. 새로운 실행은 유료이므로 기존 총 승인의 실제 잔액 및 회당 제한 안에서만 수행한다.


## 2026-11-26 API 없는 개발 경험 연결 검사

실제 물리 실패→해결→지침 등록의 연결만 검사하려면 `run`에 `--policy scripted --split development --development-learning --memory-root <별도 개발 폴더>`를 명시한다. 기본 공유 메모리·collection·evaluation에서는 이 개발 작성기를 거부한다. 다음 실행에서 같은 별도 폴더와 `--condition R1`을 사용하면 고정 snapshot으로 검색하고 다시 물리 검사한다. 지침은 원 실행에서 실제 사용해 설치까지 확인된 수치만 포함한다. 이것을 Astra 생성 경험이나 학습 효과 실험으로 집계하지 않는다.

UI는 설치 개수, 주 실행/복사 검사, 단계, 판단 출처와 등록 개수를 요약한다. `memory_use.json`과 `continuations.jsonl`, `checkpoints/`, `result.json`의 `experience_summary`와 `next_step`으로 실제 사용·중단·재개 근거를 확인한다. 배터리 재시험 전 공통 실행 명령과 실제 결과는 [STEP 48](STEP_48_PROTOTYPE_VALIDATION.md)에 있다.


### API 키를 파일에 저장하지 않고 UI 시작

```powershell
powershell -NoProfile -File .\scripts\start_assembly_ui.ps1 -Port 8765
```

숨김 입력으로 키를 받고 해당 UI 프로세스와 실행 자식에만 전달한다. 값은 명령줄·로그·디스크·전역 환경에 저장하지 않는다. API 없이 쓰려면 `-WithoutApi`를 붙인다. 이 명령은 UI만 열며 실행을 시작하거나 새 비용을 승인하지 않는다. 주소는 http://127.0.0.1:8765 이다.


STEP 변환은 native Python 3.8과 별도의 Python 3.10/OCP를 쓴다. 현재 조립용 사본의 `.runtime/ocp`는 F:의 조립 전용 라이브러리 복사본에 연결돼 있다. 코드 저장소 복제만으로 이 로컬 자산까지 설치되는 것은 아니다. 기존 라이브러리 출처·해시와 실제 단일 STEP 준비 결과는 [STEP 51](STEP_51_STEP_INPUT_READY.md)을 확인한다.


### 최신 실제 UI 시범의 한계

STEP 53의 805초 실행 한도는 두 번째 부품 사전검사 관찰 중 소진됐다. 주 장면 1/2와 API 없는 별도 복사 관찰 통과를 전체 성공으로 합치지 않는다. 안정성 관찰이 시간/취소/측정 오류로 중단되면 완료 미확정으로 남기고, 기록된 실제 기준 위반은 보존한다. 종료 후 영상·bundle 저장 시간은 실행 한도에 추가될 수 있어 보고서의 `pipeline_seconds`와 실행 한도를 구분한다. 다음 새 전체 시험 계획은 최대 $1.50·1,200초이며, 실행하기 전 기존 총 예산 잔액을 확인한다. 이미 열린 UI 서버는 종료하지 말고 유지한다.

## 물리 계산 간격 선택 · 개발 실험

`prepare`와 `run`에서 `--physics-timestep 0.002`, `0.004`, `0.01`을 지정한다. 새 원본 입력은 기본 0.002초이고 준비된 입력은 저장된 프로파일을 따른다. 이 옵션은 새 초기 장면을 위한 것이며 실행 중간 상태의 간격 변경/이어가기를 허용하지 않는다. 손가락 동작의 실제 초 단위 시간은 유지한다.

0.004초는 1초/251개, 0.01초는 1초/101개 관측이며 기존 허용 오차·접촉 기준을 그대로 적용한다. 별도 프로파일 해시와 `validation_regime`으로 결과·검색·audit를 구분한다. 더 큰 간격은 development 전용이며 collection/evaluation에서 거부한다. 현재 0.01초 새 장면 시작은 그리퍼 관절 검사에서 실패했다. 0.004초의 API 없는 2/2 성공과 정확한 실행 명령은 [STEP 54](STEP_54_TIMESTEP_VARIANTS.md)에 있다.

새 UI 선택 항목은 서버를 다음에 재시작할 때 표시된다. 기존 키가 들어 있는 서버는 이번 작업에서 종료하지 않았다. 현재 작업이 없을 때 `scripts/start_assembly_ui.ps1`로 재시작하면 숨김 키 입력부터 다시 진행한다. 이번에 저장한 결과·영상 열기는 새 API나 물리 실행을 발생시키지 않는다.


### 배터리 0.004초 Astra 실제 결과

수정 준비본은 `F:/dev/llm-robot-assembly-data/research/20261126_battery_4ms/support_fixed/prepared.json`이다. 기존 키가 들어 있는 UI에서 준비본 경로를 사용할 수 있다. 최신 결과는 첫 부품 두 파지안이 사전검사에서 실패한 0/12이며 전체 조립 성공이 아니다. 비용·남은 설정·실패 이미지·보고서 열기 절차는 [STEP 55](STEP_55_BATTERY_4MS_ASTRA.md)에 있다. 실행 중 쓰던 남은 회차 예산을 다음 시도의 신규 승인으로 간주하지 않는다.
