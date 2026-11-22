# v1.2 제품 입력부터 조립 결과까지

이 문서는 새 기본 실행 경로를 설명한다. 설계 기준은 [MASTER_DESIGN](design_v12/ASSEMBLY_MASTER_DESIGN_v1.2.md)와 [HANDOFF](design_v12/ASSEMBLY_CODEX_HANDOFF_v1.2.md)다. **구현 내용과 실제 통과한 검증은 다르다.** 실제 실행 근거는 [검증 보고서](STEP_27_V12_VERIFICATION.md)에 따로 기록한다.

## 사용자 흐름

```text
완성 배치가 있는 CAD 또는 검증된 분해 export
  → 단위·부품 ID·목표 pose·원본 해시 확인
  → CAD인 경우: 기하 정규화 → 지원 형상의 탐색 우선순위 → 실제 ASAP/RedMax 검사
  → 성공한 하나의 연속 분해 체인만 역순 후보로 사용
  → 실제 메시에서 결합 조건·충돌 형상·초기 격자·작업대 생성
  → MuJoCo 장면 안정화
  → LangGraph: 관측 → 허용 행동/예산 → 실패 시 검색 → 정책 제안
  → Python 계약 검사 → 복사 사전검사 → 주 장면 동작 → 물리 검증
  → 다음 부품 / 지원 복구 / 중단
  → 원본 사건 완결 → 지침 생성·검사·등록 → 결과 HTML/JSON/CSV/영상
```

분해 모듈은 원래 저장소를 수정하지 않는다. 현재 머신의 연결 설정은 `config/disassembly_bridge.wsl.example.json`이며, 별도의 읽기 전용 checkout인 `runtime/integration_disassembly_source`를 실행한다. 직접 CAD를 받는 기본 연결은 **API 없는 기하 우선순위 + 네이티브 DFS/RedMax**다. 이를 Astra 분해 판단으로 부르지 않는다. 외부에서 실제 Astra로 만든 export도 같은 계약으로 검사할 수 있다.

## 입력과 지원 범위

- 검증된 `disassembly_export/v1` JSON, 그 JSON이 든 폴더, 또는 상대 경로를 보존한 ZIP.
- 완성 배치가 포함된 STEP/OBJ 제품을 upstream 입력기에 연결할 수 있다. 이번 물리 검증에서 실제 사용한 입력은 **OBJ + 제품 manifest**다. 임의 STEP 제품의 로봇 조립까지 검증했다는 뜻은 아니다.
- 낱개 메시만 있고 완성 목표가 없거나 단위가 확인되지 않으면 자동 추측하지 않는다. 완성 좌표에 놓인 OBJ 폴더에는 출처에 맞는 `--assembled-obj --unit mm` 등을 명시한다.
- 지금 지원하는 것은 축에 정렬된 폐곡면 강체의 사각 소켓 바닥판, 사각 기둥, 사각 관통 슬리브, 여러 관통 구멍과 일체형 파지 탭을 가진 덮개의 수직 삽입이다.
- 하나의 명시된 고정 베이스, UR3e 한 대, 2F-85, 기본 최대 10개 제품 부품이다. 외부 추가 지지가 필요한 후보는 거부한다.
- 원·곡면·모따기 등 일반 CAD, 임의 자세 조립, 나사·압입·스냅핏·재파지·급품은 이 템플릿의 지원 완료 범위가 아니다.

새 제품의 registry를 사람이 작성하는 대신 실제 표면의 직교 셀 분해, 닫힌 메시/부피 검사, 구멍·기둥 대응, 바닥/슬리브 지지 높이, 목표 좌표로 조건을 생성한다. 파일명 `post_1` 등을 의미 판정에 쓰지 않는다. 바운딩 박스는 배치 크기에 사용하지만, 그것만으로 결합 종류를 확정하지 않는다. 충돌 형상은 지원 직교 표면과 부피가 일치하는 상자 집합이며 구멍을 메우는 convex hull로 바꾸지 않는다.

분해에서 기둥을 슬리브보다 먼저 빼는 것이 가능해도, 역순 후보는 현재 로봇의 슬리브 삽입 전제조건과 다를 수 있다. 직접 CAD 경로에서는 지원 형상에서 얻은 우선순위를 **분해 탐색 전에** 전달하고 네이티브 검사를 다시 받는다. 이미 받은 export의 성공 체인을 임의로 재정렬하지 않는다.

## 자동 준비와 가정

공통 작업장 설정은 `config/assembly_workcell.v12.json` 하나다. 제품별 로봇 좌표표가 아니다. 고정 부품 ID와 자유 부품 목록, 목표 pose, 공차/구멍/지지/파지 기하, 안정화 기준을 CAD와 실행기 계약으로 만든다.

자유 부품 ID를 정렬해 열 수를 `ceil(sqrt(N))`로 잡고, 가장 큰 XY 외곽에 공통 여백을 더한 격자를 만든다. 로봇과 고정 베이스 공간은 건너뛰며, 초기 Z는 각 메시의 실제 최저점으로 계산한다. 작업대 XY는 전체 배치에 맞춰 확장한다. 로봇 설치 위치와 완성 목표를 이동시켜 도달 실패를 숨기지 않는다.

기본 질량·마찰·작업대 치수·고정 지그는 **시뮬레이션 가정값**이다. `prepared.json`에 공통 profile과 출처를 남긴다. `input_valid`, `task_supported`, `stable_layout_ready`, `preparation_ready`, 최종 설치 성공을 구분한다. 각 부품의 현재 파지/설치 복사 검사를 모두 통과해야 전체 접근 준비를 확인한 것으로 기록한다. 아직 검사하지 않은 부품의 접근 가능성을 선행 선언하지 않는다.

## 정책·실행·복구

`scripts/assembly_v12/execution.py`가 실제 MuJoCo 장면을 한 번 만들고, 부품별 LangGraph 루프가 같은 장면을 이어받는다. LLM은 숫자 파지 후보와 현재 허용된 행동 하나를 제안한다. IK·경로·궤적·접촉/설치 검증은 기존 로봇 소프트웨어가 수행한다.

| 조건 | 역할 | 과거 해결 사례 검색 |
|---|---|---|
| S0 | single | 끔 |
| S1 | single | 켬 |
| R0 | assembly / recovery, 공유 이력 | 끔 |
| R1 | assembly / recovery, 공유 이력 | 켬 |

같은 모델 설정, 초기 두 목표 이미지, 도구, 장면, 메모리 snapshot, 장부를 사용한다. 두 역할은 병렬로 로봇을 움직이지 않는다. `store=False`와 전체 수동 replay를 사용하고, 모델의 전체 output 항목과 encrypted reasoning, `call_id`에 연결된 실행 결과를 보존한다. 컨텍스트 한도를 넘으면 중단하며 과거 대화를 몰래 자르지 않는다.

행동은 `pick`, `insert`, `recover_alignment`, `replan_insert`, `stop`이다. `insert`에는 운반·정렬·삽입·해제·후퇴·설치 검사가 포함된다. 위치는 정책 입력에서 world mm, 회전은 xyzw이며 파지 오프셋은 부품 로컬 mm/deg다.

- 파지 후보: 부품당 누적 최대 3개. 복사 검사가 통과해도 주 장면에서 실제 파지·들림을 다시 확인한다.
- 정렬 복구: 전체 run 최대 1회. 지정 단계의 실제 정렬 실패와 파지 유지, 기존 오차/여유/속도 조건을 만족해야 한다. 복구 후 `insert`가 다시 필요하다.
- 운반 재계획: 전체 run 최대 1회, 부품별 최대 1회. 미실행 `insert_transfer/preflight` 충돌과 동일 상태의 거부 근거가 있어야 한다. 성공하면 설치까지 완료하므로 insert를 중복 실행하지 않는다.
- 제어 생성 상한: `4N+4`와 기존 LLM 설정 상한 중 작은 값. 역할 변경으로 상한을 늘리지 않는다.
- 새 기본 실행기는 중단된 행동을 자동 재개하지 않는다. `replay`는 영상 재생이다. 기존 추가 3회 재개 실험은 `run_frame_assembly.py --legacy-extended-resume`로 명시해야 하며 새 비교군에서 제외된다.

## 경험과 지침

사건은 같은 run·부품·목표의 첫 실패에서 열리고 **원래 부품의 실제 설치 검증**에서 `solved_local`이 된다. 코드 스킬을 새로 생성/실행하지 않는다. 객관적인 시도·검사·실행·검증 기록과 짧은 자연어 지침을 보관한다.

지침은 `applies_when`, `strategy`, `checks`, `evidence_refs` 네 필드다. 원본 실패와 설치 검증 참조, 기록된 수치/단위, 지원 행동을 검사한다. 부족하면 null 보류 또는 등록 보류로 남긴다. 이 검사는 원인의 과학적 입증이나 새 제품에서의 성공 보장은 아니다.

등록 시 원본 events, 최종 보존 검사, 준비·설정 자료와 사건을 대조하고 복사/해시를 남긴다. 현재 첫 구현은 **전체 성공 run의 해결 사건만** 등록한다. 전체 실패 run에서 이미 해결한 일부 부품을 살리는 별도 국소 보존 어댑터는 미구현이며 그런 후보는 보류한다. 일반적인 무실패 성공은 문제 해결 사례 수에 넣지 않는다.

Python이 실패 후 검색한다. domain/source/phase/failure code, 동일 실행기·형상과 관측된 기하 조건, 현재 허용되는 첫 대응으로 제한한다. 부품 ID 전체 일치를 요구하지 않지만 현재 기하 조건은 보수적으로 비교한다. 첫 파지 전 검색은 기본 경로에서 하지 않는다.

- 같은 규격의 정확한 실패 이미지와 DINOv2-small 특징이 있으면 cosine 순으로 최대 3개.
- 영상 비교가 불가능해도 필수 metadata 조건이 일치하면 최대 1개. 가짜 유사도 점수를 붙이지 않는다.
- 필요한 조건을 확인할 수 없으면 0개.

사건 안에서도 필터에 일치한 **그 실패의 이미지**만 비교한다. DINO 가중치/전처리/구현/패키지/카메라 version을 기록하고 서로 다른 version을 섞지 않는다. 현재 카메라는 `v2_active_orange`이며 기존 v1 이미지를 v2로 소급 표시하지 않는다. 로컬 가중치의 다운로드/해시 검사는 완료했지만 조립 Python 환경의 torch/transformers/Pillow가 준비되지 않아 실제 DINO 추론은 미실행이다. 설치 시 C 드라이브 잔여 약 300MB와 300초 설치 상한을 확인했다. `--encoder <local-model-folder>`가 없는 기본 실행은 metadata 경로다. 과거 이미지·벡터·전체 trace는 Astra에 보내지 않고 짧은 지침 카드만 보낸다.

충분한 디스크 공간을 확보한 뒤 선택 vision 환경을 준비하는 명령(WSL 프로젝트 디렉토리 기준):

```bash
runtime/linux-venv/bin/python -m ensurepip --upgrade
runtime/linux-venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1+cpu
runtime/linux-venv/bin/python -m pip install -r config/requirements-v12-vision.txt
runtime/linux-venv/bin/python -B scripts/setup_v12_vision.py runtime/assets/dinov2-small
runtime/linux-venv/bin/python -B scripts/verify_v12_vision.py runtime/assets/dinov2-small runs/v12_post_sleeve_memory_alignment/failures/failure_0001.png --output runs/dino_smoke.json
```

마지막 명령은 실제 특징 계산만 검증한다. 오래된 이미지를 새 카메라 version의 검색 인덱스에 넣거나 로봇 성능을 검증하는 명령이 아니다. 연구 수집은 설치가 끝난 고정 환경과 동일 카메라로 새로 수행한다.

메모리는 `runtime/memory/assembly_v12_verified`에 둔다. OneDrive DB를 사용하지 않는다. 현재 run 시작 시 snapshot을 고정하며 새 사건은 다음 run부터 검색된다. `freeze-memory`는 collection split만 고정하므로 개발용 scripted 지침이 주 평가에 들어가지 않는다.

## Windows에서 실행

현재 머신 기준 WSL 배포판은 `ASAP-Ubuntu`, Python은 `runtime/linux-venv/bin/python`이다. PowerShell:

```powershell
cd C:\dev\llm-robot-assembly
.\scripts\run_assembly.ps1 -InputPath 'C:\path\to\product.json' -Condition R0 -Policy scripted -Seconds 360
```

출력이 가리키는 `report.html`을 열면 목표/초기/실패/최종 이미지, 연속 영상, 행동 표, 원본 로그 링크가 나온다. 실행마다 새 디렉토리를 사용한다.

개발용 CAD를 새로 생성해 입력부터 확인하려면:

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly -- runtime/linux-venv/bin/python -B scripts/make_v12_development_inputs.py runtime/v12_inputs/my_new_cell --variant new_cell
.\scripts\run_assembly.ps1 -InputPath 'C:\dev\llm-robot-assembly\runtime\v12_inputs\my_new_cell\product.json' -Policy scripted -Condition S0
```

이 생성기는 선언된 **개발용 CAD**를 만들 뿐 분해 성공 기록을 만들지 않는다. 실제 분해 검증은 다음 명령에서 수행된다.

API 키가 준비되고 해당 설정의 실행이 승인된 경우:

```powershell
.\scripts\run_assembly.ps1 -InputPath 'C:\path\to\export_v1.json' -Policy astra -Condition R1 -LlmConfig '.\config\llm.astra-high-native-frame.toml' -Seconds 360
```

키는 설정된 환경변수로만 전달하며 코드/명령 인자/로그에 값을 넣지 않는다. 기존 모델·토큰·금액 상한은 그대로 적용된다. 키가 없는 현재 환경에서는 이 경로가 API 요청 전에 blocked로 종료된다. 긴 공유 이력의 토큰 검사에서 상한을 넘으면 같은 방식으로 중단한다.

정렬 복구와 metadata 검색의 오프라인 재현 명령(WSL 안에서, 새 output 이름 사용):

```bash
MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py run \
  runs/v12_post_sleeve_recovery02/input/export_v1.json \
  --policy scripted --condition R1 --seconds 360 --family post_sleeve \
  --first-grasp config/v12_development_rejected_grasp.json \
  --scenario alignment_offset --output runs/my_new_recovery_run
```

현재 저장된 지침은 개발 연결 검사용이다. 규칙 정책은 지침의 자연어를 해석하지 않으므로 **검색 연결 확인과 LLM 지침 활용 성능을 구분**한다. `--scripted-guidance`는 development에서만 명시적으로 허용된다.

## 수집과 평가

`config/assembly_trials.example.json`의 경로·독립 lineage·split·공통 설정을 실제 준비 결과로 채운다. 제품별 registry나 Python 코드를 작성하는 작업은 아니다. 동일 원본 실행을 복사해 collection/evaluation으로 나누지 않는다. 현재 드라이버는 보수적으로 동일 분해 source run의 split 간 중복도 거부한다.

```bash
runtime/linux-venv/bin/python -B scripts/run_assembly.py freeze-memory runtime/memory/assembly_v12_verified runtime/memory/frozen_collection.json
runtime/linux-venv/bin/python -B scripts/run_assembly.py plan-trials config/local.trials.json runtime/trials_frozen.json --snapshot runtime/memory/frozen_collection.json
MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py collect runtime/trials_frozen.json runs/my_collection --max-runs 1
MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py evaluate runtime/trials_frozen.json runs/my_evaluation --max-runs 1
```

collection은 memory-off S0/R0, evaluation은 S0/S1/R0/R1을 사용한다. 준비 자료·모델 설정·프롬프트·메모리 및 실행 코드/의존성/선택 encoder version을 고정하고 결과를 본 뒤 조건을 바꾸지 않는다. 유료 batch에는 명시적으로 승인된 `--paid-budget` JSON이 별도로 필요하다. 필드는 `approval_reference`, `max_runs`, `max_model_requests`, `max_total_tokens`, `max_cost_usd`, `max_wall_seconds`다. 남은 총량에 완전한 동일 run 상한이 들어가지 않으면 다음 시행을 시작하지 않는다. 이 파일이나 40회 예시는 자동 실행 승인이 아니다.

metrics에는 성공/설치 수, 후보/거부/재시도, 정렬/재계획, 검색/시간, 생성·입력 count 요청, 토큰·캐시·추론·추정 비용, 물리/영상 상태를 나눈다. 각 run의 `llm_events.jsonl`과 `agent_usage`로 제어와 후처리 지침 비용을 구분한다. scripted 결과는 Astra 성능표와 합산하지 않는다. 아직 주 평가 batch는 수행하지 않았다.

## 영상과 원본 확인

주 실행의 `mjSTATE_INTEGRATION`을 20 fps와 경계 시점에 저장한다. 실패는 검출 순간 상태를 따로 저장한다. 검색용 480×480 상면 정사영에는 결과 글자를 넣지 않는다. 기본 연속 MP4는 CPU 렌더 비용을 고려해 320×320이며 물리 시각과 프레임 대응은 `replay_frames.json`에 있다. 초기 검증 run 중 일부는 이전 480×480 출력으로 보존했다.

```bash
runtime/linux-venv/bin/python -B scripts/validate_v12_run.py runs/my_new_recovery_run
MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py replay runs/my_new_recovery_run
```

replay는 `media/replay.mp4`를 별도로 만들며 원래 실행 영상을 덮어쓰지 않는다. 모델/버전/상태 hash가 바뀌었으면 거부한다. 보간·로봇 재실행으로 영상을 만들지 않는다. 물리 실행 상한과 별도로 영상 후처리에 최대 600초를 두며, 실패해도 상태 파일을 보존한다. 영상 완료가 물리 성공을 의미하지 않는다.

기존 기둥·프레임·배터리 코드와 기록, 기존 메모리/코드 스킬 라이브러리는 남아 있다. 새 기본 경로는 `scripts/run_assembly.py`이며 `planner_candidate_input.py`의 기존 제품 검사를 우회하지 않는다. 실물 로봇 명령은 이 경로에 없다.
