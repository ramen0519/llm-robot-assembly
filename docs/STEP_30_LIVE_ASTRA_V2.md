# STEP 30 — 실제 Astra 조립·경험 재사용·F 드라이브 검증

작성: 2026-11-25(KST). 이전 구현 보고서: [STEP 29](STEP_29_GENERAL_MESH_V2.md).

## 1. 이번에 실제로 확인한 것

**실제 Astra high로 2부품 조립, 5회 시범 수집, 개발용 실패의 복구·지침 생성/등록·다음 실행 검색/재검증, UI 시작부터 영상 확인까지 수행했다. 일반 형상 포함 10부품 이상 전체 조립과 배터리 조립은 아직 검증하지 못했으므로 최종 프로토타입 전체 완료라고 보고하지 않는다.**

- 로봇 환경: 실제 MuJoCo 계산, UR3e 한 대, Robotiq 2F-85. 실물 명령 없음.
- 실제 API 모델: `gpt-6-astra`, `high`. 다른 모델로 대체하지 않았다.
- 입력: 기존 native 분해 검증이 있는 고정 베이스 0 + 기둥 1. `C:\dev\llm-robot-assembly\runs\clean_start_validation\input\export_v1.json`과 이미 생성한 v2 준비 자료를 재검사해서 사용했다.
- 시범: **같은 입력 5회, 5회 성공, 재시도 각 0회**. 서로 다른 5개 제품의 성공률이 아니다.
- 실패 복구: 명시적인 개발용 복사 검사에서 패드/작업대 간섭을 재현했다. 첫 후보는 Astra가 제안한 것으로 표시하지 않았고 주 장면에서는 실행하지 않았다. 그 뒤 실제 Astra가 복구 파지를 선택하고 설치·해제했다.
- 해결지침: Astra가 생성한 지침 2건이 개발 전용 메모리에 등록됐다. 다음 실행에서 앞선 지침을 실제 DINO로 검색하고 현재 장면의 물리 검사를 다시 통과했다.
- 연구용 메모리: 0건. 정상 성공만 있었던 시범에서 실패 해결 사례를 지어내지 않았고, 개발 교란 사례를 연구 데이터에 섞지 않았다.
- 코드 검사: 최종 코드의 **99개 unittest 통과**. 실제 API/실제 물리/코드 검사를 각각 기록했다.

## 2. 버전과 주요 변경

시작 브랜치 `codex/general-mesh-v2`, 시작 HEAD `ff7161b516d426508cc83a18f2a0bfff4157326b`, 시작 작업 트리 깨끗함. 기존 코드·CAD·실험·경험은 보존했고 과거 commit으로 reset하지 않았다.

| 적용 commit | 변경 이유 |
|---|---|
| `6a34eaf` | 실제 두 번째 API 요청에서 발생한 SDK 직렬화 오류 수정. 후보 전달의 중복 감소. C: 코드와 F: 데이터 분리 |
| `65c076a8abcb706aff85a4418c742496a02ff596` | 명시적 개발 실패 검사, 실제 Astra 지침의 사건 ID 계약, CLI 결과의 UI 조회 및 회귀 검사 |

주요 파일:

- `scripts/assembly_v2/policy.py`, `scripts/assembly_v12/responses.py`: SDK 객체를 API의 필드명으로 직렬화한다. `async_`가 두 번째 요청에 들어가 거절되던 실제 오류를 수정했다. 미사용 nullable 필드도 제외한다.
- `scripts/assembly_v2/packets.py`: 18개 후보와 검사 상태를 열 이름이 공유되는 표로 전달한다. 표시 좌표의 6자리 유효숫자는 명시하며, 후보 ID는 원래 정밀한 값으로 실행한다. 원 관측·접촉·실패 자료는 그대로 보존한다. 대화 이력을 중간에 잘라내지 않는다.
- `scripts/assembly_v2/memory.py`: 지침의 인용 ID는 실제 사건 ID 목록에서만 선택한다. 검사/행동 ID가 사건 ID 필드에 섞여 등록이 거절된 실제 오류를 수정했다. 실제 성공 파라미터·단위·설치 증거 검증을 유지했다.
- `scripts/assembly_v2/execution.py`, `artifacts.py`: 개발 실패 검사와 실제 Astra 결정을 사건별로 구분한다. 개발 검사가 주 장면을 움직이면 audit에서 거절한다. 연구 수집/평가에서는 이 옵션을 사용할 수 없다.
- `scripts/assembly_v2/storage.py`, `entry.py`, `ui.py`: 공통 데이터 루트 설정과 F: 경로를 지원한다. UI는 현재 데이터 루트와 기존 C:의 실제 결과를 조회한다.
- `tests/test_v2_live_protocol.py`: SDK 응답의 다음 요청 재사용, 후보 보존, 지침 근거, 외부 저장소, 개발 시험 격리, UI 결과 접근을 검사한다.

각 실행의 정확한 파일 hash는 `code_versions.json`과 `replay_bundle/bundle.json`에 있다. 5회 시범 뒤 지침 형식을 추가 수정했으므로 다음 연구 실행은 최신 코드로 계획을 다시 동결해야 한다. 이 문서만을 위해 기존 실행 결과를 덮어쓰지 않았다. 이번 변경은 로컬 커밋이며 새 GitHub 푸시는 수행하지 않았다.

## 3. 실제 실행 결과

별도 표기가 없으면 아래 폴더는 `F:\dev\llm-robot-assembly-data\runs` 아래다.

| 실행 | 실제 결과 | 생성 호출 / 추산 비용 |
|---|---|---|
| C: `runs/v2_astra_live_01`, `…-ae35dec7` | Astra 파지·실제 집기 후 다음 API 입력 오류로 실패. 설치 성공으로 세지 않음 | 1 / $0.1196125 |
| `v2_astra_live_02`, `…-2e30e983` | Astra 파지 선택 → 실제 집기·설치·해제·연속 검사·영상·audit 통과 | 2 / $0.2057125 |
| `v2_astra_pilot_01/000_R0` ~ `004_R0` | 5회 모두 실제 성공, 영상·audit·보관 오류 없음, 지침 등록 0 | 합계 10 / $0.536358 |
| `v2_astra_recovery_probe_01`, `…-7a54332d` | 개발 실패 → 실제 Astra 복구·설치 성공. 지침의 잘못된 인용 계약 때문에 등록 보류 | 3 / $0.27084 |
| `v2_astra_recovery_probe_02`, `…-acff55dc` | 개발 실패 → 실제 Astra 복구·설치 → 지침 생성·등록, DINO 추출 통과 | 3 / $0.2706525 |
| `v2_astra_memory_reuse_01`, `…-66274a5e` | 새 snapshot의 검색 지침을 참고한 Astra 판단 → 실제 재검증·설치 성공 | 3 / $0.267515 |
| `v2_astra_ui_01/jobs/0b143e567d6348139c41629539950f9e` | UI 시작 요청 → 기존 CLI → 실제 Astra 조립 → 진행/결과/영상 조회 통과 | 2 / $0.1073395 |

시범의 전체 run ID는 `v2_astra_pilot_01/batch_result.json`에 있다. 시범 소요 시간은 약 584.47초다. UI run ID는 `0b143e567d6348139c41629539950f9e-e168e920`이며, `v2_astra_ui_01/ui_flow_check.json`에 HTTP 시작/진행/결과/영상 검증을 남겼다. 유료 UI 시험용 임시 서버는 종료했고, 결과 조회 서버에는 키를 영구 저장하지 않았다.

### 공통 완료 프로파일

첨부 v1.2의 논리 hash `c2a925ce7e111ae5c807bfeec155bcc4dc56f971fa016012c95d1e8b9d03c37c`를 유지했다. XYZ 각 축 5mm, 상대 회전각 3°, 최종 경질 관통 0.5mm, 실제 해제/비접촉/5mm 이격, 속도 기준, 정착 1초 후 연속 1초 **501개 샘플**을 검사했다. 삽입 깊이가 정의된 기둥은 1mm 깊이 및 사전 정렬 기준도 검사했다.

대표적인 실제 메모리 재사용 실행의 최종 창 최대/최소값:

| 항목 | 측정값 |
|---|---|
| XYZ 각 축 최대 오차 | 약 0.02349 / 0.03140 / 0.10775 mm |
| 최대 상대 회전각 | 약 0.000427° |
| 최대 경질 관통 | 약 0.10776 mm |
| 최소 그리퍼 이격 | 약 64.540 mm |
| 삽입 깊이 오차 | 약 0.10775 mm |
| 최종 창 | 501개 모두 통과, source=live_simulation |

목표 직접 배치·순간이동·인위적 부착으로 성공시키지 않았다. 예시 밀도/마찰 등의 공통 가정은 이전 보고서와 같다.

## 4. 경험과 검색의 증거

개발 전용 메모리: `F:\dev\llm-robot-assembly-data\runtime\memory\assembly_v2_development_probe`.

1. `v2_astra_recovery_probe_02` event1: `development_failure_probe`, `PLANNED_CONTACT`, `approach_grasp/preflight`, `planner_candidate`, `executed=false`. 패드/작업대 간섭을 검사했고 주 상태가 유지됐다.
2. event2: 실제 Astra recovery가 최신 후보 `1:r2:g166` 선택. copy의 파지와 해당 파지의 설치 가능성을 각각 확인하고 주 장면에서 집었다.
3. event3: 실제 Astra assembly가 insert를 제안. 실제 설치·해제·후퇴·완료 창을 통과했다.
4. 실제 Astra guidance 호출: 실패 event1, 성공 pick event2, 설치 event3와 실제 파지값을 인용한 지침 생성. 실제 근거 검사 후 사례 `2731012d90ce71cc6773bac38a5536d79825e8f6455a9fa3215006e9a2ac552d` 등록, 실제 DINO 384차원 임베딩 1개 생성.
5. 다음 실행 `v2_astra_memory_reuse_01`의 snapshot `22096ef1eff468d9a48bf7b8a5903901f3c49c5c53a438cd39e38f59f91b1f40`에 위 사례가 들어갔다. `retrieval.jsonl`에서 조건 필터 후 `DINO_after_condition_filter`, score=1.0, raw_images_sent_to_llm=false를 확인했다.
6. 다음 실행의 event2에는 위 record ID와 실제 Astra 제안이 연결돼 있다. 모델은 검색 사례를 참고했다고 짧은 이유에 명시했고, 실제 적용한 위치/회전/개방 폭은 지침의 값과 일치했다. 실제 파지/설치를 다시 검사한 뒤 최종 성공했다.

재사용 실행에서도 새로 해결된 사건의 지침이 생성되어 사례 `8ae56ac430004cdbf0a65750f5f0fc929912983776dea77c5282027a1ddcabdb`를 등록했다. 두 보관 묶음의 hash, 원 이벤트, 완료 증거, 지침 인용, DINO 차원을 다시 검사했다.

이것은 **동일 입력·의도한 개발 실패의 기능 검증**이다. 자연 실패율 감소, 새로운 형상의 검색 성능, 메모리의 인과적 개선 효과는 측정하지 않았다. 두 개발 사례는 연구용 `runtime/memory/assembly_v2`의 수집/평가에 포함되지 않는다.

## 5. 요구별 적용 상태

일반 형상 자산·전체 pose·원 CAD 목표·역경로 변환·파지/설치 분리 구현은 STEP 29의 상태를 유지했다. 이번에는 실제 API와 지침·검색·UI 경로를 추가 검증했다.

| 항목 | 현재 상태 |
|---|---|
| 입력·자동 준비·native 연결 | 검증된 export 경로 실제 통과. 새 raw CAD는 WSL 무응답으로 미실행 |
| 일반 메시/충돌/물성/초기 배치 | 구현 및 원통/관통 구멍/비대칭/13부품 준비 확인 유지. 복잡한 배터리 충돌 자산 미완성 |
| 전체 파지 pose/설치 경로/검증 | 일반 경로 유지. 실제 Astra 2부품 집기·삽입 통과. 경사 파지의 실제 물리 증거는 기존 scripted 시험 |
| 경험·지침·검색 | 실제 Astra 생성/등록/다음 DINO 검색/물리 재검증 통과. 연구 메모리 0건 |
| 영상·결과·UI | 실제 상태 영상, audit, UI 실제 Astra 시작→결과/영상 조회 통과 |
| 수집·평가 | 실제 5회 시범 완료. 추가 제품 본 수집과 독립 네 조건 평가는 미실행 |

| F 항목 | 적용 및 실제 확인 |
|---|---|
| F01 정상 접촉 대칭 | 기존 회귀 유지, 이번 실제 집기/삽입에도 적용 |
| F02 최신 파지 변환 | 재사용. 실제 성공 파지 이후 현재 변환으로 설치 |
| F03 실제 실행 여부 | 개발 실패 false / Astra pick·insert true 구분, audit 통과 |
| F04 시간 제한 | 600초 실행/180초 후처리의 기존 유한 제한 유지, 신규 예산 확대 없음 |
| F05 수집/평가 격리 | 시범 R0 검색 OFF, 개발 시험 분리, 연구 메모리에 개발 사례 0 |
| F06 동결 | 실행 파일/설정/자산/가중치 hash 보존. 다음 실험은 최신 코드로 새 계획 필요 |
| F07 성공/audit | 실제 final 창과 원 목표·원 이벤트·모델/실패 자료 대조 통과 |
| F08 예산 | 실제 24회 사용량 정산, 제어/지침 공통 모델/장부, 총 $15 이하 |
| F09 수집 절차 | 5회 시작/완료와 오류 없음 확인. 추가 제품 manifest 부재로 본 수집 not_run |
| F10 검색 조건 | 문제 조건 필터 뒤 실제 DINO 검색. 같은 실패 재현 시험임을 명시 |
| F11 지침 근거 | 실제 거절 사례 발견 후 사건 ID를 제한. 실제 생성·검사·등록 통과 |
| F12 단위/원본 대응 | 기존 source/native 재검사 후 실행. 원 CAD 목표 변경 없음 |
| F13 배치/부품 수 | 일반 N 기반 구조 및 13부품 준비 회귀 유지. 이번 실제 조립은 2부품 |
| F14 제안 출처 | Python 후보/Astra 선택/개발 첫 검사 명확히 구분. 실제 Astra가 주 동작 판단 |
| F15 지표 | 시범 각 후보1/재시도0. 개발 시험 후보2/재시도1을 별도 기록 |
| F16 증거 보관 | 실제 모델·상태·원 이벤트·소스 묶음 hash 확인, 새 개발 보관 사례 2개 재검사 |

V01(10부품 프레임 전체 실행)은 부분 확인, V02 일반 자산·물리 진입과 V03 scripted 경사 파지 증거는 유지, **V04 일반 형상 10+ Astra 완주는 not_run**, V05 13부품 준비 유지, V06 실제 Astra 개발 복구/지침/재사용 통과, V07 반례 검사 통과, V08 실제 UI 흐름 통과다.

## 6. 사용량·저장 위치·확인 자료

- 사용자 승인: 이번 세션 합계 최대 **$15**.
- 생성 24회, 입력 **127,257** 토큰, 출력 **8,453** 토큰. 출력에는 reasoning **4,144** 토큰이 포함돼 있다. 캐시 입력 **19,760** 토큰은 입력에 포함된다.
- 설정 요금과 실제 응답 사용량으로 계산한 합계 **$1.7780300**, 잔여 **$13.2219700**. 불명확 사용량/미정산 예약 0. 실제 청구서 대조는 미실행이다.
- 키 접근/입력 개수 진단은 생성 호출이 아니다. 키는 파일/인수/Git/실험 로그에 저장하지 않았고, 생성 작업 종료 후 도구 메모리에서도 제거했다. 공급자 측 키 폐기는 사용자가 수행한다.
- 세션 누적 장부: `C:\dev\llm-robot-assembly\runtime\session_spend_20261124.json`.
- 새 영상/실험/메모리: **`F:\dev\llm-robot-assembly-data`**. 사용자 승인 후 전환했다. 기존 C: 결과는 삭제/이동하지 않고 Windows 무손실 압축으로 약 0.84GB를 확보했다.
- 전체 결과표: `F:\dev\llm-robot-assembly-data\runs\v2_session_report_20261125\summary.json`, `runs.csv`, `not_run.json`, `ui_links.json`.
- 검사 로그: `F:\dev\llm-robot-assembly-data\runs\v2_final_checks_20261125\unittest_after_guidance_fix.log` — 99개 통과.
- 대표 영상: `F:\dev\llm-robot-assembly-data\runs\v2_astra_memory_reuse_01\media\execution.mp4`.
- 검색/지침/원 이벤트: 같은 run의 `retrieval.jsonl`, `guidance_candidates.json`, `events.jsonl`, `registrations.json`, `llm_events.jsonl`.

## 7. 처음 설정과 실행

공통 환경/설치 명령은 [운영 안내](ASSEMBLY_V2_OPERATIONS.md)를 따른다. 현재 PC에는 Python 환경·MuJoCo·로봇/그리퍼·DINO가 준비돼 있다. F: 설정은 Git 제외 파일 `config/local.storage.v2.json`에 저장했다.

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B scripts\run_assembly.py status
$assemblyKey = Read-Host '새 API 키' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $assemblyKey).Password

# 현재 검증된 입력 하나로 실제 실행. 새 기본 결과는 F:에 생성된다.
& $assemblyPython -B scripts\run_assembly.py run --input runs\v2_memory_reuse_physics_01\prepared.json --policy astra --condition R0 --seconds 600

# 공통 native 환경 복구 후 새 완성 CAD 입력
& $assemblyPython -B scripts\run_assembly.py run --input 'C:\path\product.step' --policy astra --condition R0 --seconds 600

# 키가 있는 셸에서 시작하면 새 UI 실행도 가능하다.
& $assemblyPython -B scripts\run_assembly.py ui --port 8765
```

현재 결과 조회용 UI: **http://127.0.0.1:8767**. 실제 UI 실행 결과:

http://127.0.0.1:8767/results/8d93ad39bff5c8567702c2dfe266d567/report.html

다음 명령의 입력 목록/예산은 실제 새 대상과 잔여 한도로 준비해야 한다. 이미 소비한 시범을 본 수집으로 이름만 바꾸지 않는다.

```powershell
# 입력 목록 → 최신 코드로 계획 동결 → 시범 5회, 이상 없으면 계획의 나머지 본 수집
& $assemblyPython -B scripts\run_assembly.py plan-trials --manifest config\local.collection_inputs.json --output F:\dev\llm-robot-assembly-data\runs\next_collection_plan.json
& $assemblyPython -B scripts\run_assembly.py collect --manifest F:\dev\llm-robot-assembly-data\runs\next_collection_plan.json --output F:\dev\llm-robot-assembly-data\runs\next_collection --max-runs 5 --continue-main --paid-budget config\local.approved_budget.json

# 연구 경험 등록 후 동결 → 다른 원본 입력으로 네 조건 평가
& $assemblyPython -B scripts\run_assembly.py freeze --root F:\dev\llm-robot-assembly-data\runtime\memory\assembly_v2 --output F:\dev\llm-robot-assembly-data\runs\research_memory_frozen.json
& $assemblyPython -B scripts\run_assembly.py plan-trials --manifest config\local.evaluation_inputs.json --snapshot F:\dev\llm-robot-assembly-data\runs\research_memory_frozen.json --output F:\dev\llm-robot-assembly-data\runs\evaluation_plan.json
& $assemblyPython -B scripts\run_assembly.py evaluate --manifest F:\dev\llm-robot-assembly-data\runs\evaluation_plan.json --output F:\dev\llm-robot-assembly-data\runs\evaluation_new --max-runs 4 --paid-budget config\local.evaluation_budget.json

# 실제 보존 증거 확인/영상 재생. 재생은 새 로봇 실행이 아니다.
& $assemblyPython -B scripts\run_assembly.py audit --run F:\dev\llm-robot-assembly-data\runs\v2_astra_memory_reuse_01
& $assemblyPython -B scripts\run_assembly.py replay --run F:\dev\llm-robot-assembly-data\runs\v2_astra_memory_reuse_01
```

## 8. 남은 제한과 다음에 필요한 것

1. **native 환경:** WSL 자체 버전 조회는 응답하지만 ASAP-Ubuntu의 `/bin/echo`도 10초/45초 내 응답하지 않았다. 다른 분해 실험을 중단할 수 있어 재시작 여부를 사용자에게 물었고, 답변 없이 종료/재시작하지 않았다. 근거는 `runs/v2_native_environment/status.json`에 있다.
2. **Framework 배터리:** 실제 원본과 선택 체인은 이전에 확인했으나 현재 strict 입력에 필요한 경로/검증 묶음과 적합한 충돌 자산이 부족하다. 원본·분해 저장소를 수정하거나 거친 충돌 형상으로 통과시키지 않았다. 배터리 첫 부품 로봇 실행도 not_run이다.
3. **일반 형상 10부품 이상 전체 시연:** 검증된 새 native 입력과 적합한 자산부터 필요하다. 작은 2부품 성공이나 기존 13부품 준비로 대신하지 않는다.
4. **본 수집/성능 평가:** 기존 manifest는 5회 반복만 포함했고 모두 실행했다. 추가 유효 제품 목록과 분리된 평가 입력이 아직 없다. 연구 메모리 0건이고 개발용 2건을 연구 근거로 바꾸지 않았다. 전체 공유 이력의 입력 상한 8,000토큰도 계속 적용되므로 더 긴 작업에서는 별도의 문맥/자원 한도를 검토해야 한다.
5. **지원 동작:** 강체·마찰 기반 집기와 놓기/정의된 삽입이다. 나사·클립·접착·유연체·전기적 완료·실물 배터리 취급은 이번 검증에 포함되지 않는다.

**검증된 입력으로 실제 Astra 경험 수집을 시작할 실행 경로는 확인됐다.** 새 제품을 대상으로 본 수집과 일반화 실험을 시작하려면 native 환경, 유효 입력/충돌 자산, 분리된 수집·평가 목록이 추가로 필요하다.
