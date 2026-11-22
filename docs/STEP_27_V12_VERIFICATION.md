# STEP 27 · v1.2 전체 연결 구현과 실제 검증

2026-11-23. 작업 브랜치: `codex/integrated-assembly-v12`. 시작 HEAD: `c2d8e026d49c4c2a064b809a351845e239f057c3`. 시작 시 현재 작업본과 지침을 확인했으며 과거 설계 기준 commit으로 되돌리지 않았다. 기존 기둥·프레임·Framework 배터리 경로 및 결과는 보존했다.

**새 통합 실행 경로를 구현했고, 새 CAD의 작은 조립체에서 실제 물리 조립·복구·경험 검색·영상을 확인했다. 실제 Astra 판단, 실제 DINO 특징 계산, 전체 10부품 프레임 완주는 미완료다. 따라서 설계 전체의 최종 완료로 판정하지 않는다.**

## 1. 이번 구현을 쉽게 설명하면

이전에는 제품마다 만들어 둔 Python 설정과 좌표에 기대는 부분이 있었다. 새 경로는 완성 목표가 포함된 제품을 받아, 지원 형상인지 확인하고, 실제 분해 성공 체인을 연결하고, 구멍·기둥·지지 관계와 초기 배치를 계산한다. 그 결과를 같은 MuJoCo 장면의 실행 루프로 넘긴다.

정책이 제안한 숫자가 곧 성공은 아니다. Python이 제안을 검사하고, 복사 장면에서 파지·삽입 간섭을 먼저 확인하고, 주 장면에서 실제 접촉으로 집고 이동·설치·놓기를 수행한다. 설치 위치·방향·속도·접촉·지지가 검증되어야 다음 부품으로 진행한다.

실패 기록은 같은 부품의 사건에 모은다. 파지만 성공해도 해결된 것으로 저장하지 않고, 원래 부품이 실제로 설치된 근거와 전체 run 종료 시 보존 근거를 확인한 뒤 해결지침 등록을 허용한다. 다음 run에서는 실패 조건을 먼저 걸러 과거 지침을 검색한다.

현재 규칙 정책(scripted)으로 이 연결을 검증했다. 규칙 정책은 자연어 지침을 읽고 판단하는 LLM이 아니므로, 이번 검색 성공으로 Astra의 개선 효과를 주장하지 않는다.

## 2. 문서 완료 기준별 상태

| HANDOFF 완료 기준 | 구현 및 검증 상태 | 미완료 또는 제한 |
|---|---|---|
| 1. 이동 가능한 실제 export, 단위·ID·목표·연속 체인 | 구현. 실제 새 CAD의 네이티브 export를 복사해 입력하고 원본 해시·잔여 부품·경로/지지 검증 연결을 확인 | 임의 STEP/일반 곡면 제품의 로봇 조립을 검증한 것은 아님 |
| 2. single/roles, memory off/on 공통 실행 | 네 조건 S0/S1/R0/R1 연결. 공통 설정·도구·시도 예산·공유 장부 검사 통과 | 네 조건의 실제 Astra 성능 비교 미실행 |
| 3. 초기 2장, 공유 이력, 최신 관측 | 실제 설치 SDK + 오프라인 HTTP 응답으로 요청/전체 output/불투명 reasoning/call ID 연결 검증 | 실제 API 키가 없어 실제 응답 왕복 미검증 |
| 4. 거부·복구·설치 사건과 출처 연결 | 실제 복사 파지 거부 및 실제 정렬 실패 → 복구 → 원래 설치 검증 확인 | 새 입력에서 운반 재계획까지 실험하지는 않음. 기존 재계획 실행기를 조건부 재사용 |
| 5. 적격 등록, 다음 run 검색·적용·재검증 | 개발용 사건 2개 등록. 다음 실제 MuJoCo run에서 metadata 검색과 복구·연속 설치 확인 | 지침은 명시된 scripted 개발용. 실제 Astra 지침 생성·활용과 DINO 검색 효과 미검증 |
| 6. 실제 상태 영상·실패 이미지 | 성공 run MP4/PNG와 전체 integration state 저장. 원래 영상 보존하며 별도 replay 검증 | 이전 run의 카메라 v1은 현재 v2와 시각 비교하지 않음 |
| 7. 수집/평가 manifest·집계·사용량 | split, 네 조건, 고정 메모리/입력/프롬프트/구현/의존성, 유료 batch 총량, JSON/CSV 구현·계약 검사 | 실제 연구 수집/40회 평가 미실행 |
| 8. 전체 프레임 통합 시연 | **미완료**. 새 10부품 CAD를 실제 네이티브 분해에 넣었으나 180초 상한에서 9개 중 2개 제거 후 PARTIAL | 완전한 export를 만들지 못했으므로 로봇 전체 조립 시작 안 함. 작은 셀 성공으로 대체하지 않음 |
| 9. 새 입력을 제품별 수동 코드/좌표 없이 조립 | **실제 통과**. 다른 원본 ID와 22mm 기둥/26mm 소켓/64mm 높이의 개발 CAD로 입력 → 실제 분해 → 자동 조건/배치 → 1개 설치 완료 | 지원 직교 수직 삽입 종류 안의 증거. 일반 CAD 일반화 성공을 뜻하지 않음 |

완료 근거가 없는 항목은 위 표에 남겼다. 입력 계약의 fixture 검사는 실제 물리 성공으로 집계하지 않는다.

## 3. 실제 실행 결과

모든 경로는 `C:\dev\llm-robot-assembly` 기준이다. 아래 run은 모두 보존했다. `설치 수`에는 고정 베이스를 포함하지 않는다.

| run 폴더 | 실제 내용 | 결과 | API 생성 요청 |
|---|---|---|---|
| `runs/v12_new_cell_smoke02` | 변경된 새 CAD → 실제 ASAP/RedMax 연속 체인 → 자동 준비 → 단일 기둥 집기·설치 | 성공, 1/1 설치, S0 scripted, 제어 2회 | 0 |
| `runs/v12_post_sleeve_recovery02` | 첫 파지 z=-18mm를 명시적으로 주입 → 복사 접촉 거부 → 수정 → 같은 장면 기둥/슬리브 설치 | 성공, 2/2 설치, R0 scripted, 제어 5회, 해결 사건 1개 | 0 |
| `runs/v12_post_sleeve_memory_alignment` | 위 사건 검색 + 같은 파지 거부, 첫 기둥 6mm 정렬 오차 시나리오 → 정렬 복구 → 슬리브까지 설치 | 성공, 2/2 설치, R1 scripted, 제어 7회, 실패 행동 2회, 검색 2회 중 1회 반환 | 0 |
| `runs/v12_windows_entry_prepare` | 실제 Windows PowerShell 진입점으로 새 셀 export 재사용 | 기하/격자 준비 완료. 실제 접근/설치 완료를 뜻하지 않음 | 0 |
| `runs/v12_full_frame_prepare02` | 10부품 전체 프레임의 실제 네이티브 분해 | PARTIAL, 제거 2/9, `wall_time` 중단. 전체 조립 미시작 | 0 |
| `runs/v12_astra_key_readiness02` | `llm.astra-high-native-frame.toml`, Astra high 실제 실행 진입점 | 키 없음으로 요청 전 blocked. 정책 판단 0회, 물리 시간 0, 초기/최종 상태 동일 | 0 |

다음 실패도 지우지 않았다.

- `v12_new_cell_smoke`: 신뢰된 WSL Python 경로 검사 문제로 시작 실패. 실행 파일 경로 검사와 실제 호출 경로를 분리해 수정했다.
- `v12_post_sleeve_recovery`: 분해에서는 성공한 순서였지만 역순이 현재 슬리브 설치 전제조건에 맞지 않아 unsupported로 중단. 받은 성공 체인을 임의로 바꾸지 않았다. 다음 run부터 직접 CAD의 지원 형상 우선순위를 **분해 검증 전에** 주는 방식으로 연결했다.
- `v12_full_frame_prepare`: 기존 우선순위에서 분해 시간 제한. 이후 기하 우선순위를 적용한 `prepare02`도 시간 제한 안에 전체 체인을 얻지는 못했다.
- `v12_astra_key_readiness`: 첫 준비 검사. 초기 구현의 `policy_decision_evaluated` 표시 오류를 고쳤고, 최신 `readiness02`에서 실제 호출 0/판단 false를 확인했다. 과거 로그를 소급 변경하지 않았다.

성공한 새 셀은 시뮬레이션 73.646초, 실행 구간 벽시계 79.820초, 영상 생성 91.636초였다. 복구+검색 셀은 시뮬레이션 171.414초, 실행 구간 208.210초, 영상 생성 310.797초였다. 초기 run의 `physical_wall_seconds`는 모델/검사를 포함한 실행 구간의 이름이었다. 최신 구현은 이를 `execution_wall_seconds` 별칭으로 명시하고 정책·복사 검사·주 도구·지침·렌더 시간을 따로 측정한다. 과거 값에서 측정하지 않은 세부 시간을 추정해 채우지 않았다. 이 개발 기록은 통제된 조건별 성능 비교 표본이 아니다.

## 4. 확인할 영상·이미지·로그

가장 많은 기능이 연결된 결과: [`v12_post_sleeve_memory_alignment/report.html`](../runs/v12_post_sleeve_memory_alignment/report.html).

- 연속 주 실행 영상: [`media/execution.mp4`](../runs/v12_post_sleeve_memory_alignment/media/execution.mp4)
- 실제 최종 장면: [`media/final.png`](../runs/v12_post_sleeve_memory_alignment/media/final.png)
- 정확한 검출 순간: `failures/failure_0001.{json,npy,png}`, `failure_0002.{json,npy,png}`. 각 JSON의 source/phase/action/check ID를 확인한다.
- 행동·역할·실행·설치 판정: `events.jsonl`, `observations.jsonl`, `final_verification.json`
- 검색: `retrieval.jsonl` — `metadata_only`, 반환 사례 ID, 일치한 실패 ID와 scope, snapshot ID.
- 사건·등록: `incidents.json`, `result.json`의 `registrations`
- 비교용 한 행: `metrics.csv`, `metrics.json`
- 재생 근거: `model.mjb`, `replay_states.npz`, `replay_frames.json`, `code_versions.json`

새 CAD 전체 입력 경로를 확인하려면 [`v12_new_cell_smoke02/report.html`](../runs/v12_new_cell_smoke02/report.html), `input/export_v1.json`, `input/native_input.json`, `input/input/geometry.json`, `prepared.json`, `disassembly/`를 본다. 실제 파일 목록은 export의 inventory를 기준으로 확인한다. 초기 성공 run 두 개의 HTML/CSV는 보존된 result/events에서 후처리로 만들었으며, `report_manifest.json`에 출처 해시를 남겼다. 물리 run이나 정책을 다시 실행한 것이 아니다.

별도 재생 확인: `runs/v12_new_cell_smoke02/media/replay.mp4`, `replay_media_result.json`. 1486개 저장 상태를 320×320으로 다시 렌더했다. 원래 `execution.mp4` 해시 `9c7869273acaae27c44a201692d3b6424785386775f591e797e67bfb0e0e7569`는 보존되었다.

영상과 런타임 메모리는 Git에서 제외한다. 위 링크는 현재 작업 컴퓨터의 로컬 결과를 가리킨다.

## 5. 검증 종류와 범위

- 신규 계약 검사 **39개 통과**: export의 잘못된 체인/다른 분기 섞기/단위/중복 변환/해시/ZIP 탈출, 지원 기하, 공유 이력과 revision, 해결 사건 등록 근거, 정확한 실패 이미지 연결, 네 조건/split/고정 자료/유료 총량 등을 검사했다.
- SDK 검사: 설치된 OpenAI SDK에 오프라인 HTTP transport를 연결했다. 실제 모델 추론이 아니다.
- 실제 MuJoCo 검사: 위 세 성공 run 및 실패 run. 임의 부착/실행 중 부품 순간이동으로 성공시키지 않았다. 목표 그림은 별도 scratch 장면의 렌더 전용이며 성공 근거에서 제외했다.
- 기존 회귀 검사 **15개 통과**: frame scene/verification, live role 계약, 명시적인 legacy resume, 덮개 실제 구멍/지지 검사, OpenAI 사용량/timeout 처리. 전체 기존 suite를 수행했다는 뜻은 아니다.
- 결과 감사: 새 셀 13항목 및 복구/검색 셀 16항목 통과. 연속 시각, 실제 설치 근거, 실패 이미지/state 해시, model/state/video 대응을 확인했다. 감사는 새 물리 실험이 아니다.
- Windows 명령 실제 준비 통과. 처음 발견한 WSL 경로 전달 문제는 `--exec` 인자 전달로 수정했다.
- 로컬 DINO: 공개 가중치 다운로드와 고정 SHA 검사는 통과. CPU PyTorch 설치는 300초 상한에서 완료되지 않았다. C 드라이브 잔여 약 300MB도 확인했다. torch/transformers/Pillow를 갖춘 환경에서의 실제 특징 계산은 **미실행**. 기존 의존성 `pip check`는 정상이다.
- 실제 Astra 요청 **0회**, 실제 유료 API 비용 **$0**. 키 부재를 로그에 남겼으며 키 값은 기록하지 않았다.

카메라/인덱스 계약을 검사한 384차원 테스트 벡터는 명시된 합성 fixture다. 실제 DINO 출력이나 성능으로 보고하지 않는다.

## 6. 주요 변경 파일과 결정

| 파일 | 역할 / 결정 |
|---|---|
| `scripts/run_assembly.py`, `run_assembly.ps1` | 제품 입력부터 결과까지 단일 진입점, Windows→WSL 경로/키 환경 전달 |
| `assembly_v12/contracts.py` | 이동 가능한 export와 하나의 연속 성공 체인, 원본 형상·단위·좌표·증거 검사 |
| `assembly_v12/preparation.py`, `scene.py` | 실제 지원 기하에서 작업 계약/초기 격자/작업대/충돌 상자 집합/동적 registry 생성. 기존 프레임 검사 우회 없음 |
| `assembly_v12/context.py`, `execution.py` | 전체 공유 이력, 동일 모델·도구·예산, 순차 역할 전환, 직렬 LangGraph·물리 실행·복구 |
| `assembly_v12/memory.py` | 객관 사건, 자연어 지침 검사, 원본 설치/종료 보존 근거, 고정 snapshot, 조건 필터/DINO 순위/metadata 대체 |
| `assembly_v12/media.py`, `artifacts.py`, `metrics.py` | integration state, 검출 순간, 별도 replay, HTML, 버전·사용량·시간·결과 표 |
| `assembly_v12/entry.py` | 분해 모듈 호출, 준비/실행 구분, split/동결 메모리/네 조건 batch, 승인 총량 보존 |
| `setup_v12_vision.py`, `verify_v12_vision.py` | 선택적 고정 DINO 자산 다운로드 및 실제 특징 계산 확인 도구 |
| `validate_v12_run.py` | 이미 실행된 물리 기록과 영상의 근거 감사 |
| `make_v12_development_inputs.py` | 개발용 CAD 생성. 분해/조립 성공 결과를 생성하는 도구가 아님 |
| `import_v12_development_case.py` | 이전 개발 사건을 원본 events/최종 설치 증거와 대조해 새 archive로 옮기는 제한된 도구 |
| `frame_assembly_skills.py` | 기존 `base` 기본값을 유지하면서 새 고정 부품 ID 지원 |
| `openai_policy.py`, `llm_runtime.py` | 새 경로에서 encrypted output replay 지원 및 single/guidance 역할의 공통 장부 |
| `run_frame_assembly.py`, `test_frame_resume.py` | 추가 후보 legacy resume을 명시적 opt-in으로 제한, 새 비교군과 분리 |
| `config/assembly_workcell.v12.json` 등 | 공통 환경, 실제 분해 연결, 문서 원문 프롬프트, 고정 시험 설정 예시 |
| `tests/test*v12*.py`, README, PROJECT_PLAN, 이 보고서 | 변경 계약 검증 및 최신/역사적 경로 구분 |

분해 최신 checkout은 `runtime/integration_disassembly_source`, HEAD `8110fdcd5e919f3b3a1beb8c974b56a81069e7b4`. 원래 분해 프로젝트를 수정하지 않는 별도 소스다. 현재 CAD 직접 경로의 후보 우선순위는 고정 기하 규칙이며, 실제 경로·지지는 ASAP/RedMax로 검사했다. 이를 Astra 분해 실행으로 부르지 않는다.

## 7. 정확한 실행 절차

자세한 사용법: [INTEGRATED_ASSEMBLY_V12.md](INTEGRATED_ASSEMBLY_V12.md).

현재 결과를 먼저 열기:

```powershell
cd C:\dev\llm-robot-assembly
Start-Process .\runs\v12_post_sleeve_memory_alignment\report.html
```

기존 검증 export로 새 실제 조립(새 출력 폴더 사용, API 없음):

```powershell
.\scripts\run_assembly.ps1 -InputPath 'C:\dev\llm-robot-assembly\runs\v12_new_cell_smoke02\input\export_v1.json' -Policy scripted -Condition S0 -Seconds 300 -OutputPath 'C:\dev\llm-robot-assembly\runs\my_new_cell_run'
```

계약 검사:

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B -m unittest discover -s tests -p 'test*v12*.py' -v
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B scripts/validate_v12_run.py runs/v12_post_sleeve_memory_alignment
```

영상 재생은 물리 동작 재실행이 아니다:

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec env MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py replay runs/v12_new_cell_smoke02
```

## 8. 수집을 시작해도 되는가

**개발용 객관 로그 수집과 작은 scripted 물리 검사는 가능하다. Astra가 실제 해결지침을 생성·재사용하는 연구용 자동 수집은 아직 준비 검증이 끝나지 않았다.**

현재 검증 라이브러리 `runtime/memory/assembly_v12_verified`에는 개발용 해결 사건 2개가 있다. 모두 `split=development`, `guidance_source=scripted_development_fixture`다. 연구용 `collection` 사례로 세지 않으며, `freeze-memory` 기본 경로에서도 제외된다. 무실패 성공 run은 해결 사례 수를 늘리지 않는다.

필요한 다음 조건은 다음과 같다.

1. 현재 실행 환경의 지정 API 키 및 승인된 exact Astra high 설정으로 작은 실제 모델+물리 smoke 확인. 현재는 요청 전 차단된다.
2. C 드라이브에 충분한 작업 공간 확보 후 선택 vision 의존성 설치와 실제 DINO 특징 검사. 카메라 v2에서 새로 수집한 이미지로 시각 검색을 확인한다. 카메라 v1 개발 사진에 v2 표지를 소급 붙이지 않는다.
3. 전체 프레임: 추가 검증 범위를 별도로 요청한 상태다. 제안한 상한은 API $0, 분해 최대 15분, MuJoCo 최대 15분, 영상 최대 10분이다. 승인 응답 없이 확장하지 않았다. 그 전에는 이 항목을 미완료로 유지한다.
4. 독립 collection/evaluation 제품·조건·lineage, 동일 예산, frozen manifest 확정. 코드를 수정하거나 의존성이 바뀌면 기존 manifest 실행을 거부하고 다시 동결한다.
5. 실제 연구 수집 후 collection 메모리만 고정하고 네 조건 평가를 실행한다. 유료 batch는 별도의 승인 총량 JSON이 있어야 하며 설계의 40회 예시만으로 실행하지 않는다.

지원 범위는 직교 강체의 수직 삽입이다. 재파지·자동 급품·나사·압입·스냅핏·일반 곡면 형상의 결합 추론·실물 로봇은 구현 완료 범위가 아니다. 새로운 실행 코드 스킬 생성은 기본 경로에서 제외하고, 기존 검증된 함수와 자연어 해결지침을 사용한다. 전체 실패 run에서 앞서 해결한 국소 사건의 별도 보존 어댑터는 미구현이므로 등록을 보류한다.

E 드라이브 조사, 실물 로봇 명령, 원래 분해 자료 수정, API 키 기록, 충돌/설치 판정 완화는 수행하지 않았다.
