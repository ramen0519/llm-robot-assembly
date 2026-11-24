# 현재 v1.2 실행 안내

2026-11-24 초기화 이후의 사용법이다. 구버전 runner·경험 변환기·생성 코드 스킬을 호출하지 않는다. 설계 원문은 [MASTER](design_v12/ASSEMBLY_MASTER_DESIGN_v1.2.md), 정리 및 실제 검증 결과는 [STEP 28](STEP_28_CLEAN_START.md)을 본다.

## 입력부터 결과까지

`scripts/run_assembly.py`는 완성 목표가 있는 CAD/제품 manifest 또는 이동 가능한 `disassembly_export/v1`을 받는다. 직접 CAD는 `config/disassembly_bridge.wsl.example.json`에 연결한 외부 ASAP/RedMax를 실행하고, 완전한 하나의 실제 성공 체인을 확인한다. 원본 해시·단위·목표·지지 조건을 검사한 뒤 조립 역순 후보로 전달한다. 탐색의 서로 다른 성공 분기를 섞거나 분해 방향을 TCP 명령으로 쓰지 않는다.

분해 소스는 `runtime/integration_disassembly_source`의 별도 checkout이다. 원래 분해 프로젝트는 수정하지 않는다. 현재 기본 CAD 연결의 후보 우선순위는 기하 규칙이고 경로·지지는 실제 네이티브 물리 검사다. 이것을 Astra 분해 판단으로 표현하지 않는다.

현재 지원하는 것은 축에 정렬된 닫힌 강체 표면의 사각 소켓 바닥판·기둥·관통 슬리브·여러 구멍과 파지 탭이 있는 덮개의 수직 삽입이다. 기본 최대 10부품, 고정 베이스 하나, 추가 외부 지지 없음, UR3e 한 대와 2F-85다. 목표나 단위가 불명확하거나 지원 밖인 제품은 사유를 남기고 중단한다.

실제 형상에서 구멍·기둥·지지·삽입 깊이·파지 범위를 계산하고 동적 registry를 만든다. 초기 격자·작업대 XY도 공통 환경 profile에서 생성한다. 새 제품마다 좌표표나 Python registry를 작성하는 것이 정상 절차가 아니다. 공급 공간을 넓혀도 로봇 도달 범위가 늘었다고 보지 않는다. 미제공 질량·마찰·지그 등은 가정값과 출처를 기록한다.

## 실행 명령

현재 머신은 WSL `ASAP-Ubuntu`, Python `runtime/linux-venv/bin/python`이다. PowerShell:

```powershell
cd C:\dev\llm-robot-assembly
.\scripts\run_assembly.ps1 -InputPath 'C:\path\to\product.json' -Policy scripted -Condition S0 -Seconds 300
```

`scripted`는 개발용 규칙 정책이며 실제 Astra 판단이 아니다. 먼저 입력 준비만 하려면 `-Action prepare`를 사용한다. 기하/배치 준비, 실제 안정화·접근 준비, 최종 조립 성공을 별도로 기록한다.

새 개발용 CAD에서 시작하려면(같은 입력 폴더를 덮어쓰지 않는다):

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B scripts/make_v12_development_inputs.py runtime/v12_inputs/my_fresh_cell --variant new_cell
.\scripts\run_assembly.ps1 -InputPath 'C:\dev\llm-robot-assembly\runtime\v12_inputs\my_fresh_cell\product.json' -Policy scripted -Condition S0 -Seconds 300
```

이 생성기는 CAD만 만든다. 실제 분해 결과·조립 결과·경험을 미리 넣지 않는다. 과거 run을 지웠으므로 예전 run의 export 경로를 기본 예제로 사용하지 않는다.

키와 이번 실행 범위가 승인된 경우의 Astra 명령:

```powershell
.\scripts\run_assembly.ps1 -InputPath 'C:\path\to\product.json' -Policy astra -Condition R1 -LlmConfig 'C:\dev\llm-robot-assembly\config\llm.astra-high.toml' -Seconds 300
```

키는 지정된 환경변수로 전달한다. 명령 인자·파일·로그에 키 값을 넣지 않는다. 모델·입력/출력 토큰·호출/비용/시간 한도는 기존 설정을 유지했다. 이 설정 파일 자체가 새 유료 실험의 승인이라는 뜻은 아니다.

## 정책과 실행

S0(single/off), S1(single/on), R0(순차 조립·복구/off), R1(순차 조립·복구/on)을 같은 관측·도구·예산에서 실행한다. 역할을 나누어도 하나의 공유 이력과 장부를 쓰며 모터 명령은 직렬이다. `store=False`, 전체 output/불투명 reasoning과 call ID 결과를 수동 replay한다. 문맥이 한도를 넘으면 몰래 자르지 않고 중단한다.

허용 행동은 `pick`, `insert`, `recover_alignment`, `replan_insert`, `stop`이다. 파지 후보는 부품당 누적 3개, 정렬 복구와 재계획은 각각 run당 1회다. 실제 상태·출처·단계·잔여 예산이 조건에 맞아야 복구를 노출한다. 재계획은 설치까지 검증되면 해당 부품을 종료한다. 파지·정렬 복구만 성공한 것을 설치 성공으로 세지 않는다.

정책은 수치 후보 하나를 제안하고 Python이 revision/ID/좌표/범위부터 확인한다. 복사 검사 통과 뒤에도 주 장면에서 실제 접촉·들림·이동·삽입·해제·안정성을 확인한다. 현재 실행기를 유지하기 위해 `frame_assembly_skills.py`, `insertion_skills.py` 등 공통 물리 코드는 남겼다. 옛 그래프와 `run_frame_assembly.py`, 코드 스킬 VM은 제거했다.

## 비어 있는 메모리에서 시작

기본 경로는 **`runtime/memory/assembly_v12` 하나**다. 다음 명령은 등록 사례·지침·split별 수를 표시한다.

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B scripts/run_assembly.py memory-status
```

초기화 직후 모두 0이다. 첫 실행은 과거 지침 없이 판단한다. 같은 부품·목표의 실패를 사건으로 모으고 실제 원래 설치에 성공한 뒤 run 종료 보존 검사와 지침 근거 검사를 통과해야 등록된다. 해결하지 못한 로그와 일반 무실패 성공은 해결 사례 수에 넣지 않는다. 등록한 사례는 현재 run이 아닌 다음 run부터 사용할 수 있다.

경험은 객관적인 시도·검사·동작·결과 기록이다. 지침은 `applies_when`, `strategy`, `checks`, `evidence_refs`의 자연어 카드다. 스킬 함수는 이미 검증된 로봇 실행 코드이고, LLM이 새 실행 코드를 만들어 등록하는 기능은 없다.

실패 후 Python이 source/phase/code·실행기·형상 조건·허용 첫 대응을 먼저 필터한다. 같은 카메라/encoder version의 정확한 실패 이미지가 있으면 DINO cosine 순 최대 3개, 직접 적용 조건만 확인되면 metadata 최대 1개, 조건을 확인할 수 없으면 0개다. 과거 사진·벡터·전체 로그는 Astra에 보내지 않고 지침만 전달한다. 현재 DINO 실제 추론은 아직 미검증이며 기본 동작은 metadata 검색이다.

`--scripted-guidance`는 명시적인 development 연결 검사 전용이다. 기본 실행에서는 켜지 않으며, 개발용 카드를 연구용 collection으로 계산하지 않는다. `freeze-memory`는 collection 사례만 고정한다. 이전 DB·JSON·스킬·동결 메모리는 자동 이관하지 않는다.

## 영상과 결과

각 새 run은 `runs/` 아래 독립 폴더에 결과를 남긴다. `report.html`에는 실제 결과·초기/최종/실패 이미지·영상·로그 링크가 있다. `result.json`, `metrics.csv`, `events.jsonl`, `incidents.json`, `retrieval.jsonl`에서 판단 출처와 물리 판정을 확인한다.

MP4는 주 실행의 `mjSTATE_INTEGRATION`을 재생한다. 실패 장면은 검출 순간의 복사/계획 후보/주 실행 source와 state hash를 보존한다. 목표 그림은 렌더 전용이며 실제 성공 장면이 아니다. 재생은 로봇 명령을 다시 실행하지 않는다.

```bash
runtime/linux-venv/bin/python -B scripts/validate_v12_run.py runs/NEW_RUN
MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py replay runs/NEW_RUN
runtime/linux-venv/bin/python -B scripts/run_assembly.py report runs/NEW_RUN
```

`execution.mp4`와 별도 `replay.mp4`를 구분한다. 모델/상태 hash나 simulator version이 다르면 재생을 거부한다. 준비·모델·복사 검사·주 도구·지침·검색·렌더 시간과 실제 simulation time을 구분한다. 주 도구 시간에는 내부 계획·검사 시간이 포함된다. 비용 불명인 실패를 0원으로 덮어쓰지 않는다.

## 새 경험 수집과 평가

`config/assembly_trials.example.json`에서 독립적인 collection/evaluation 입력의 준비 결과와 lineage를 지정한다. 제품별 로봇 코드를 만드는 작업은 아니다. collection은 memory-off S0/R0, evaluation은 고정된 같은 메모리의 S0/S1/R0/R1이다.

```bash
runtime/linux-venv/bin/python -B scripts/run_assembly.py freeze-memory runtime/memory/assembly_v12 runtime/memory/frozen_collection.json
runtime/linux-venv/bin/python -B scripts/run_assembly.py plan-trials config/local.trials.json runtime/trials_frozen.json --snapshot runtime/memory/frozen_collection.json
MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py collect runtime/trials_frozen.json runs/my_collection --max-runs 1
MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py evaluate runtime/trials_frozen.json runs/my_evaluation --max-runs 1
```

준비 결과·코드/의존성·프롬프트·모델 설정·선택 encoder·메모리를 고정한다. 코드를 바꾸면 새 manifest를 만든다. 실제 유료 batch는 명시적인 `--paid-budget`에 승인 근거와 run/호출/토큰/비용/시간 상한이 필요하다. 40회 평가 예시나 과거에 쓴 승인 잔액을 이번 자동 승인의 근거로 쓰지 않는다.

## 선택 이미지 검색 환경

로컬 가중치 `runtime/assets/dinov2-small`와 출처는 보존했다. Python의 torch/transformers/Pillow 등 선택 의존성은 준비 상태를 확인한 뒤 설치한다. 현재 조립 경험이 0개이므로 설치만으로 검색 사례가 생기지 않는다.

```bash
runtime/linux-venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1+cpu
runtime/linux-venv/bin/python -m pip install -r config/requirements-v12-vision.txt
runtime/linux-venv/bin/python -B scripts/setup_v12_vision.py runtime/assets/dinov2-small
runtime/linux-venv/bin/python -B scripts/verify_v12_vision.py runtime/assets/dinov2-small runs/NEW_RUN/failures/failure_0001.png --output runs/dino_check.json
```

이 검사는 특징 계산 검사이며 로봇 성공률 검증이 아니다. 카메라 `v2_active_orange`와 encoder version이 다른 자료를 같은 이미지 조건으로 섞지 않는다.
