# STEP 48 · 공통 프로토타입 실제 검증

2026-11-26. 배터리 재실행, 실물 명령, 유료 API 호출 없이 수행했다. 실제 Astra는 이번 변경분에서 `not_run`이다. 코드 연결을 확인한 계약 검사와 실제 MuJoCo 동작을 구분한다. 기존 파일·CAD·이전 결과를 보존했다.

## 핵심 결과

| 실행 폴더 | 결과 | 실제 설치 | 핵심 내용 |
|---|---|---|---|
| `prototype_20261126_collect01` | `preparation_failed` | 0/1 | 예전 준비본의 환경 불일치 감지 |
| `prototype_20261126_collect02` | `failed` | 0/1 | 눕힌 부품의 설치 IK 실패 |
| `prototype_20261126_collect03` | `failed` | 0/1 | 설치 손 방향 힌트 후 접근 IK/간섭 한계 |
| `prototype_20261126_collect04` | `preparation_failed` | 0/0 | 기하 판정 저장 오류 감지 후 수정 |
| `prototype_20261126_collect05` | `physical_success` | 1/1 | 실패 후 실제 설치, 개발 지침 등록 1건 |
| `prototype_20261126_multibody01` | `reference_invalid` | 0/2 | 별도 받침 없는 육각 fixture의 기준 속도 실패 |
| `prototype_20261126_multibody02` | `partial` | 1/2 | 첫 기둥 설치, 다음 부품 놓기에서 이웃 접촉 |
| `prototype_20261126_multibody03` | `physical_success` | 2/2 | 동일 입력에 제한된 벌림·후퇴 복구 연결 |
| `prototype_20261126_reuse01` | `physical_success` | 1/1 | DINO 검색 후 현재 장면 재검증·설치 |

시뮬레이션을 시작한 실행의 `code_versions.json`과 `replay_bundle/`가 당시 실제 코드·자산을 식별한다. 준비 단계에서 종료된 실행은 `result.json`과 `exception.json`으로 구분한다. 기준 HEAD `cf8add83cb2704c61ab4dc928119fb5276f1d33f` 위 미커밋 변경이다. 실패한 시험도 지우거나 성공 행으로 바꾸지 않았다.

실제 최종 완료 창의 측정값(각 실행 501개 샘플):

| 실행 / 부품 | XYZ 축별 최대오차(mm) | 최대 상대각(도) | 최대 경질 관통(mm) |
|---|---|---|---|
| collect05 / 1 | 0.0335, 0.0653, 0.1078 | 0.00074 | 0.1078 |
| multibody03 / post | 0.0272, 0.0080, 0.1078 | 0.00064 | 0.1078 |
| multibody03 / taper_post | 0.0052, 0.0017, 0.1078 | 0.00069 | 0.1078 |
| reuse01 / 1 | 0.0335, 0.0653, 0.1078 | 0.00074 | 0.1078 |

`prototype_20261126_multibody03-9ed9bb80`은 같은 한 장면에서 직선 기둥과 테이퍼 기둥을 모두 설치했다(고정 바닥 포함 총 3부품). 두 번째 부품의 full-open 해제 중 이웃 기둥 접촉을 실제 복사 검사에서 감지하고, 제한된 벌림→후퇴→완전 개방으로 바꾼 계획을 다시 검사한 뒤 주 장면에서도 완료했다. 실제 소요 433.782초(약 7분 14초, 영상·임베딩 저장 포함)이며 별도 성능 벤치마크는 아니다.

해당 해결 지침 ID는 `e3d5afb50150bcd88b9cb3e000d5f1883b8e2a26ba834885fee73569f35bcf25`다. 이 지침은 실제 사용한 `limited_open_then_retreat` 동작 계획을 인용한다. 이번 격리된 개발 저장소에는 해결 사례가 총 2개 등록됐다. 첫 사례의 다음 실행 재사용은 확인했으며, 새 해제 사례를 이후 또 한 번 실행하는 검증은 아직 수행하지 않았다.

## 경험 생성 → 검색 → 재검증

`prototype_20261126_collect05`는 의도적으로 낮은 첫 파지를 넣은 개발 시험이다. 테이블 간섭과 후속 후보의 설치 간섭을 거쳐 실제 주 장면에서 설치까지 완료했다. 1개 사건과 근거 기반 지침 1개가 등록됐으며 실패 이미지 2장의 로컬 DINO 임베딩이 저장됐다.

등록 사례 ID: `27a88671840de77db53820b03cfc6c255624b54a33ff4fbf79f47b3f78533fb9`.

`prototype_20261126_reuse01`은 새 장면·새 실행이다. 동일한 의도적 첫 실패 후 조건 필터를 통과한 기록을 DINO 유사도로 검색했다. `retrieval.jsonl`에서 `DINO_after_condition_filter`, `raw_images_sent_to_llm=false`, `encoder_error=null`을 확인했다. 파지값과 제한된 동작 계획은 다시 복사 검사와 주 실행을 통과했다. `memory_use.json`은 `executed_and_installation_preserved`다.

첫 실행은 후보 3회/재시도 2회, 검색 사용 실행은 후보 2회/재시도 1회였다. 같은 개발 입력에서의 기능 확인 1쌍이며 일반화 성능·통계적 개선·Astra 판단 능력의 증거가 아니다. 개발 지침은 `scripted_evidence_extractor`로 표시되고 연구용 Astra 수집과 분리된다.

메모리 위치: `F:/dev/llm-robot-assembly-data/runtime/memory/prototype_20261126_development`.

## 단계별 문제와 수정

| 관측한 문제 | 조치 및 판단 |
|---|---|
| 예전 prepared가 현재 공통 환경과 불일치 | 원본 입력에서 새로 준비. 검사 우회하지 않음 |
| 안정 자세의 발생 확률만 우선해 기둥을 눕힘 | 메시 받침면·무게중심·전도각을 검사한 후 목표와 회전 차이가 작은 후보 선택 |
| 현재 위에서 잡기만 고려하여 설치 때 손이 옆으로 향함 | 설치 때 TCP 방향을 후보 근거로 추가; 개발 정책의 고정 top 제한 제거 |
| 낮은 파지가 테이블 또는 설치 받침과 간섭 | 실제 사전검사로 거절하고 다른 파지 시험. 기존 기준 유지 |
| 여러 부품에서 손을 완전히 벌리다가 앞서 설치한 기둥과 접촉 | 해당 복사 장면의 release 실패에 한해 제한된 벌림→후퇴 계획을 새로 검증. Astra도 같은 선택지를 사용 가능 |
| 시간 제한 시 이미 놓은 부품을 다시 집을 위험 | 인증된 해제 상태를 별도 저장하고 finish_release로 남은 동작만 수행 |
| 중단·예외에서 경험 정리가 생략될 가능성 | 종료 정리에 사건·지침 상태·마지막 상태 저장을 연결 |
| 다음 복구 행동으로 검색해 실패 행동과 조건이 안 맞음 | 실패한 행동을 검색 조건으로 사용 |
| 기하 판정의 NumPy bool 저장 오류 | JSON 기본 bool로 변환, 회귀 검사 추가 |
| 원본 STEP 자동 전달에 필요한 WSL 응답 없음 | 10초 점검 타임아웃을 기록. 다른 작업을 중단시키는 WSL 재시작은 수행하지 않음 |

별도의 받침 없는 육각 기둥 fixture는 완성 기준 장면에서 속도 기준을 초과했다. 초기 1–2초 구간 최대 이동속도 약 1.269mm/s, 회전속도 약 0.03964rad/s다. 위치·각도가 작다는 이유로 이 실패를 통과시키지 않았다. 이후 홈으로 지지하는 fixture는 별도 입력·해시·출처로 생성했으며 같은 제품의 성공으로 바꿔 보고하지 않는다.

## 중단 후 이어가기와 UI

`research/20261126_prototype/native_continuation01/result.json`: 실제 MuJoCo 이동 중 시뮬레이션 시각 3.042초에서 명시적으로 시간 구간 만료를 한 번 주입했다. 상태를 되돌리지 않고 다시 관측·경로 검사 후 10mm TCP 이동을 마쳤다. 최종 TCP 위치 오차 약 0.0281mm. 전체 예산은 그대로였고 timestep은 0.002초다. 빈 그리퍼 이동 시험이며 전체 조립 또는 자연 발생 시간 초과 시험으로 주장하지 않는다.

`research/20261126_prototype/ui_check/result.json`: Flask UI 요청이 실제 공통 CLI 자식 프로세스로 준비를 완료했다. 기록 보고서·실제 최종 PNG·MP4를 HTTP 206으로 가져왔다. 브라우저 외형의 시각 검사는 수행하지 않았다. UI 서버를 계속 켜 둔 것으로 보고하지 않는다.

계약/회귀 검사: 120개 passed(5.859초). 취소·전체 시간 초과는 이어가기 대상이 아님, 원 CAD 목표 변경 거부, 출처/등록 제한, 모델 형식 수정 1회 한도, UI 상태 구분 등을 확인했다. 계약 검사의 모델 응답 대역은 실제 Astra 호출로 집계하지 않는다.

## 지금 실행하기

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B scripts\run_assembly.py ui --port 8765
```

주소: http://127.0.0.1:8765. 최근 실행 목록에서 `prototype_20261126_...`를 선택하면 결과와 영상을 확인한다. UI와 CLI는 동일 실행기를 사용한다.

제품 입력 하나로 API 없이 자동 준비부터 실행:

```powershell
& $assemblyPython -B scripts\run_assembly.py run --input 'F:\dev\llm-robot-assembly-data\research\20261126_prototype\socket_fixture\assembly_hint.json' --policy scripted --condition R0 --seconds 600
```

모델을 쓰려면 키를 안전하게 전달한 셸에서 `--policy astra`를 사용한다. 이번 작업에서 이 명령으로 유료 실행을 수행하지 않았고 현재 설정 상한이 추가 지출 승인은 아니다. raw STEP 경로는 먼저 공통 분해 환경의 응답 문제를 해결해야 한다. 기존 검증 export 또는 CAD/단위/목표/순서 hint 묶음은 WSL 없이 입력 가능하다.

별도 개발 메모리의 생성 연결을 재현하려면 새 출력 폴더와 `--policy scripted --split development --development-learning --memory-root <별도 개발 메모리>`를 사용한다. 검색은 다음 실행에서 `--condition R1`로 켠다. 이번 시험의 잘못된 첫 파지는 `research/20261126_prototype/failed_grasp.json`이며 `--first-grasp`에 전달한다. 일반 제품 실행에는 이 개발 교란을 붙이지 않는다.

```powershell
& $assemblyPython -B scripts\run_assembly.py audit --run 'F:\dev\llm-robot-assembly-data\runs\prototype_20261126_reuse01'
& $assemblyPython -B scripts\run_assembly.py replay --run 'F:\dev\llm-robot-assembly-data\runs\prototype_20261126_reuse01'
& $assemblyPython -B -m unittest discover -s tests -p 'test_v2*.py'
```

재생은 실제 저장 상태의 영상화이며 새 물리 시험이 아니다. 기존 수집·동결·평가 명령은 [운영 안내](ASSEMBLY_V2_OPERATIONS.md)에 있다. 이번 격리된 개발 메모리를 연구 평가에 넣지 않는다.

## 준비 상태와 남은 한계

입력 묶음→자동 준비→파지/설치 검사→주 실행→실패/해결 기록→다음 검색과 재검증→결과/영상의 공통 프로토타입을 구현했다. 1대 UR3e와 2F-85, 명시된 고정 베이스/공급 지그만 사용했다. 동작 중 인위적 부착·순간이동·물리 판정 완화는 없다.

현재 변경분의 실제 Astra 판단, 배터리 전체 조립, 일반 형상 10개 이상 전체 완주, 새 제품에서의 독립 성능 평가는 **미실행**이다. raw CAD 자동 분해 연결은 WSL 환경 응답에 차단돼 있다. 임의 재파지·도구 교체·나사 체결·전기 연결·실물 하드웨어는 구현 완료로 포함하지 않는다. 임의의 CAD가 반드시 조립된다는 프로그램은 아니다.

실제 Astra 수집을 재개하기 전 현재 코드/입력/모델/예산으로 계획을 새로 동결하고 작은 시범을 확인해야 한다. 기존 결과를 새 코드의 Astra 검증으로 대신하지 않는다. 이번 API 생성 0회, 비용 $0.
