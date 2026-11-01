# 현재 진행 보고서 — 2026-11-01

추가 검증: [23E 네 기둥 연속 우회 조립](STEP_23E_FOUR_POST_DETOUR.md)에서 같은 장면의 로봇 설치 4/4를 확인했다. 두 기둥 운반과 한 번의 빈 그리퍼 접근에 RRT가 사용됐고 나머지는 검사된 직접 경로였다. 실제 LLM 호출은 0회다.

작업 위치: `C:\dev\llm-robot-assembly`
브랜치: `codex/simulation-progress`
사용자 요청에 따라 작은 구현 단위별로 검증·한국어 Markdown 보고서·로컬 커밋을 남기며 연속 작업했다. 이번 작업 범위는 시뮬레이션이다.

## 지금 되는 것

**10부품 개발용 프레임을 끝까지 물리 조립했다.** 고정 바닥판 1개를 제외한 기둥 4개·슬리브 4개·덮개 1개를 로봇이 실제 접촉으로 집어 설치했다. 첫 기둥에 6mm 정렬 오류를 넣고, 같은 상태에서 복구한 뒤 나머지를 이어서 완성했다. 로봇 설치 9/9, 전체 sim 1023.688초, 부품별 마지막 검사 구간 최대 위치 오차 0.1525mm다.

**LangGraph와 단일 LLM API 연결 코드는 준비됐지만 실제 모델 호출은 아직 검증하지 않았다.** 기본 single/roles는 같은 도구를 사용하는 단일/역할별 규칙 정책이다. 명시적 설정을 주는 단일 API 정책을 추가했으며, 모델·예산·키 준비 후 라이브 시험이 남아 있다. 실물 명령·API 호출·파인튜닝은 실행하지 않았다. CAD 구조·결합 정보·파지점·순서는 개발용으로 작성했다.

## 먼저 실행해 보기

```powershell
cd C:\dev\llm-robot-assembly
uv sync --locked
# 작은 조립과 실패→복구 인계를 창에서 확인
uv run --locked python scripts/run_frame_assembly.py --parts 2 --mode roles --scenario first_alignment_offset --viewer --snapshots
# 10부품 연속 조립: 창 없이 계산하고 결과 이미지 저장
uv run --locked python scripts/run_frame_assembly.py --parts 10 --mode roles --scenario first_alignment_offset --snapshots
# 빈 그리퍼가 장애물을 우회하는 별도 모션 시험
uv run --locked python scripts/run_motion_detour.py --snapshots
# 상자를 집은 뒤 장애물 우회 → 실제 놓기
uv run --locked python scripts/run_loaded_detour.py --snapshots
# 첫 기둥: 고정 장애물 우회 + 정렬 오류 복구 + 삽입
uv run --locked python scripts/run_assembly_graph.py --mode single --transfer-obstacle --transport-strategy direct_then_rrt --scenario alignment_offset --snapshots
# 두 기둥 연속 조립: 기존 조립물 유지 + 빈 그리퍼/운반 우회
uv run --locked python scripts/run_frame_assembly.py --parts 3 --mode single --transfer-obstacle --transport-strategy direct_then_rrt --snapshots
# 정상 / 복구 없는 실패 / 복구 후 성공, 단일·역할 정책 6회 기준선
uv run --locked python scripts/benchmark_assembly.py --repetitions 1
```

전체 조립은 느린 접근·삽입·검사를 여러 차례 수행하므로 즉시 끝나지 않는다. 해당 PC에서 기록된 전체 실행 시간은 약 34.0분이며 다른 물리 시험과 동시에 실행했다. viewer 창을 닫으면 취소로 기록한다.

## 단계별 상태와 보고서

| 단계 | 실제 확인 범위 | 문서 |
|---|---|---|
| 12B 추가 | Mink 목표 IK → 기존 RRT-Connect → 물리 제어로 장애물 우회. 빈 그리퍼 별도 시험 | [12B](STEP_12B_RRT_CONNECT.md) |
| 12C 추가 | 상자의 물리 파지 → 직접 경로 충돌 예측 → 재관측·RRT 우회 → 놓기. 전체 조립 자동 연결은 후속 | [12C](STEP_12C_LOADED_DETOUR.md) |
| 15 | 물리 pick/place를 LangGraph에 연결 | [15](STEP_15_LANGGRAPH.md) |
| 16 | 저장된 실제 분해 결과에서 조립 후보 변환 | [16](STEP_16_DISASSEMBLY_ADAPTER.md) |
| 16B | 우리 CAD 10개를 외부 ASAP 로더 형식으로 내보내고 실제 로더로 확인 | [16B](STEP_16B_PLANNER_INPUT.md) |
| 17–18 | 10부품 후보 CAD, 첫 기둥 삽입·해제·후퇴 | [17](STEP_17_PRODUCT_SCENE.md), [18](STEP_18_PHYSICAL_INSERTION.md) |
| 18B 추가 | 첫 기둥의 선택형 RRT 운반→삽입과 정렬 복구 연결. 하강 중 우회 금지 | [18B](STEP_18B_TRANSPORT_INSERTION.md) |
| 19–20 | 정렬·깊이·접촉 실패 판정, 초기화 없는 오정렬 복구 1종 | [19](STEP_19_INSERTION_FAILURES.md), [20](STEP_20_ALIGNMENT_RECOVERY.md) |
| 21 준비 | 정책 계약·역할 인계·단일 비교 기반 | [21](STEP_21_POLICY_GRAPH_FOUNDATION.md) |
| 21B | 단일 LLM API 연결부·토큰/예산/오류 기록. 실제 API 호출은 미검증 | [21B](STEP_21B_LLM_API_ADAPTER.md) |
| 22 | 경험 저장·조건 검색·정책 문맥 전달 | [22](STEP_22_EXPERIENCE_MEMORY.md) |
| 22C | 기록된 복구 순서를 JSON 후보로 추출→새 물리 실행→조건 제한 등록 | [22C](STEP_22C_SKILL_RECIPES.md) |
| 23A/B/C | 4기둥, 슬리브/덮개 개별 검증, 10부품 연속 조립 | [23A](STEP_23A_MULTIPLE_POSTS.md), [23B](STEP_23B_COMPOUND_COMPONENTS.md), [23C](STEP_23C_FULL_FRAME.md) |
| 23D | 기둥 2개 연속 설치, 기존 조립물 보존·후보 충돌 검사, 빈 그리퍼 접근과 기둥 운반 우회 | [23D](STEP_23D_RETAINED_PART_DETOUR.md) |
| 23 보완 | 계산용 상태와 물리 계산 구분, 기록 최적화, 실제 viewer 실행 | [계산](STEP_23_PHYSICS_CALCULATION.md), [기록](STEP_23_RECORDING_OPTIMIZATION.md), [보기](STEP_23_VIEWER.md) |
| 25A | 6회 규칙 정책 기준선과 지표 집계. 실제 LLM 비교는 후속 | [25A](STEP_25A_OFFLINE_BASELINE.md) |

## 검증 근거

- 전체 10부품 + 첫 정렬 복구: `runs/step23_frame/20261101T052809_562769Z_f77121fb`. 전체 1회 성공이며 일반 성공률로 해석하지 않는다.
- TCP 목표 → IK → RRT 우회 → 실제 도착: `runs/motion_detour/20261101T055133_403030Z_459afbc4`. 최종 TCP 오차 약 0.0465mm.
- 부품을 잡은 우회·놓기: `runs/loaded_detour/20261101T073056_625658Z_e730854e`. 물리 시간 93.544초, 최대 운반 미끄러짐 약 1.219mm, 최종 위치 오차 약 0.178mm. 고정 장면·seed 3 시험이며 조립 성공으로 집계하지 않는다.
- 첫 기둥의 RRT 운반·삽입: `runs/step21/20261101T074325_472434Z_c9249f9f`, success. 정렬 오류 복구까지 포함한 `runs/step21/20261101T074547_926168Z_2e400be5`도 success. 같은 장애물에서 직접 경로만 사용한 반례는 운반 전에 중단했다.
- 두 기둥 연속 우회·삽입: `runs/step23_frame/20261101T075500_510987Z_3709bbf8`, success, 설치 2/2. 빈 그리퍼 접근 1회·기둥 운반 2회 RRT 우회. 두 번째 작업 중 첫 기둥의 목표 위치 대비 최대 오차 0.112mm.
- viewer를 켠 작은 그래프 실행: `runs/step23_frame/20261101T054939_272494Z_acab7551`, success.
- 현재 코드 범위의 복구 절차 재검증: `runs/step22_recipes/20261101T075705_164808Z_7888e0d8`, success. 호환 등록본 1개.
- 외부 로더 입력 확인: `runs/planner_inputs/20261101T054017_590834Z_53056bf4`. 외부 플래너의 계획 탐색 실행은 아님.
- 규칙 평가: `runs/step25_baseline/20261101T053132_441032Z_b741f3a0`. 예상 행동 6/6, 실제 조립 성공 4/6(복구를 끈 실패 2회 포함). LLM 성능 비교가 아님.
- API 연결부 추가 전 전체 회귀 검사 **104개 PASS**(482.449초). 진행 중 나중에 추가한 TCP 목표 연결은 별도 4개 검사와 실제 우회 실행으로 다시 검증했다. 설치 패키지 51개 호환성 검사도 통과했다.

단일 API 연결부 추가 후 새 검사 17개와 관련 회귀 검사 26개를 통과했다. SDK의 HTTP는 테스트 응답으로 대체했으며 실제 모델 호출 성능 검사는 아니다. 현재 설치 패키지 53개도 호환성 검사를 통과했다. 이후 운반 우회 모듈의 신규 검사 5개와 관련 검사 5개도 각각 통과했다. 신규 검사 1개의 종료 후 로그 접근 오류를 수정해 해당 항목을 재실행했으며 상세 실행 이력은 [12C](STEP_12C_LOADED_DETOUR.md)에 기록했다. 첫 기둥 우회 연결 후에는 신규 6개와 관련 회귀 10개를 각각 통과했다. 조건 비교 테스트의 JSON 변환 오류 1개를 수정해 재검사한 이력까지 [18B](STEP_18B_TRANSPORT_INSERTION.md)에 기록했다. 이후 두 기둥 우회 연결의 신규 4개·관련 회귀 6개도 통과했다. [23D](STEP_23D_RETAINED_PART_DETOUR.md)에 초기 중단 사례와 성공 장면을 구분해 남겼다.

```powershell
uv run --locked python -m unittest discover -s tests -v
uv pip check
```

검사는 계약·오류 처리 검사와 실제 MuJoCo 동작 검사를 포함한다. 검사 수를 독립적인 조립 성공 횟수로 세지 않는다. 의도적으로 실패하는 반례도 포함된다.

## API 요청을 과금 없이 확인하기

```powershell
uv run --locked python scripts/preview_llm_request.py
```

실제 초기 관측으로 `request.json`을 만들며 모델 서버에는 보내지 않는다. 라이브 설정·명령·검증 한계는 [21B 보고서](STEP_21B_LLM_API_ADAPTER.md)에 있다.

## 남은 핵심 작업

1. 제공자·모델·API 예산과 키를 준비해 구현된 단일 LLM 정책의 실제 호출·첫 조립을 검증한다. 먼저 단일 정책 호출을 검증한 뒤 같은 입력·도구·전체 예산으로 역할 분리와 비교한다.
2. LLM이 선택할 유효 대안을 늘린다. 현재 정상 행동은 대부분 하나로 정해져 있으므로 API를 붙이는 것만으로 판단 효과가 입증되지는 않는다. 검증된 대체 파지·접근·복구 후보와 변화 조건이 필요하다.
3. 우리 CAD를 기존 분해 플래너에서 실제 계획하고 좌표·ID·지지 조건을 검증해 실행 후보로 연결한다. 현재는 결과 파일 어댑터와 입력 로더까지만 검증했다.
4. RRT 우회를 전체 조립의 허용된 이동 구간과 연결하고 각 제품 부품·파지·주변 조립물을 실제 검증한다. 상자 운반·놓기, 첫 기둥과 두 기둥 연속 조립의 선택형 우회 연결은 완료했다. 두 기둥에서는 기존 조립물 보존과 빈 그리퍼 접근 우회도 확인했다. 접촉 삽입 중에는 임의 우회를 사용하지 않는다.
5. 스킬·메모리를 고정한 별도 평가 사례에서 성공·복구·비용 효과를 비교한다. 지금 절차 등록은 첫 기둥의 특정 오정렬 사례에만 유효하다.

현재 API 제공자·모델·비용 한도는 답변 대기 중이다. 다른 프로젝트의 키나 과금 승인을 가져오지 않았다. 실물 단계(24)는 사용자 요청대로 제외한다.

## 저장과 Git

실험 로그·PNG·OBJ·메모리·절차 등록본은 Git에서 제외한다. 로컬 실행 데이터는 `C:\dev\llm-robot-assembly\runs`, 메모리·절차는 `runtime` 아래에 있어 OneDrive 동기화 폴더 밖이다. 코드·환경 잠금 파일·단계 보고서는 현재 브랜치의 로컬 커밋에 남겼다. `main` 변경이나 원격 push는 하지 않았다.
