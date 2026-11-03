# 현재 진행 보고서 — 2026-11-03

작업 위치: `C:\dev\llm-robot-assembly` · 브랜치: `codex/simulation-progress`.
사용자 요청에 따라 API 키 없이 시뮬레이션을 개발·검증하고 단계별 한국어 보고서를 남겼다. **집에서 시작할 순서는 [실행 안내](START_HERE_NO_API.md)**를 따른다.

## 지금 되는 것

**슬리브 한 개**에서 실제 Luna의 수치 파지 제안, 작업대 간섭 후 수정, 이미 설치된 자유 기둥에 끼우기와 해제 후 관통 검사를 연결했다. [21G 실행 보고서](STEP_21G_PROPOSED_SLEEVE_GRASP.md)

**첫 기둥에서 Luna가 파지 위치와 닫힘 방향을 숫자로 제안한다.** MuJoCo가 복사본에서 삽입까지 검사하고 접촉·손가락 높이를 돌려주면 Luna가 제안을 바꿀 수 있다. 기본 배치에서 설치했고, 트레이를 10mm 옮긴 시험에서는 소켓 벽과의 간섭을 보고 Luna가 파지 높이를 12mm 올려 설치했다. 두 실행 모두 해제 후 여덟 조건을 통과했다. [21F 실행 보고서](STEP_21F_PROPOSED_POST_GRASP.md)

**첫 기둥의 실제 Luna 단독 정상 조립을 확인했다.** 첫 시도는 캐시 사용량 정산에서 멈췄고, 수정 후 새 시도에서 집기·삽입·완료 확인에 성공했다. 모델 요청은 두 실행 합계 5회, 추정 비용은 0.00073255달러다. [21E 보고서](STEP_21E_FIRST_LIVE_LUNA.md)

**10부품 프레임에서 고정 바닥판을 제외한 9개를 로봇이 모두 설치했다.** 막힌 운반 경로를 정책에 전달해 재계획을 6회 선택했고, 첫 정렬 실패도 1회 복구했다. 같은 장면에서 접촉·마찰로 집기·운반·삽입·해제·후퇴하며 먼저 설치한 부품도 검사했다. 시뮬레이션 시간 957.474초, 물리 측정 478,930행, 최대 최종 위치 오차 약 0.1994mm다. [23I 보고서](STEP_23I_FULL_POLICY_REPLAN.md)에 기록과 소스 버전의 범위를 남겼다.

**기존 외부 ASAP/RedMax도 연결했다.** API 없는 네이티브 DFS로 3부품 계획을 생성하고 두 기둥 실제 로봇 설치 2/2까지 연결했다. 로봇의 알려진 선행조건을 추가한 10부품 분해 9단계도 생성했다. 다만 그 새 10부품 순서는 입력 검사만 통과했으며 전체 로봇 실행은 아직 하지 않았다. 위 9/9 성공은 기존 개발용 순서의 결과다.

기본 `single/roles` 및 11월 1일의 시험은 규칙 정책이다. 11월 3일 첫 결합에서 `--llm-config`를 사용해 실제 Luna 판단을 확인했다. live LLM은 현재 첫 기둥과 첫 슬리브의 별도 시뮬레이션에서 단일 정책으로 동작하며 전체 프레임과 실제 다중 LLM은 후속이다. CAD·결합·로봇 선행조건은 개발자가 지정하고 관측은 시뮬레이터 실제 상태를 사용한다. 10부품 규칙 비교군은 파지도 지정한다. 첫 기둥의 21F 모드에서는 모델이 파지 좌표와 닫힘 방향을 직접 제안한다. 비전·실물은 미구현이다.

## 이번 준비 작업의 결과

| 단위 | 확인한 내용 | 보고서 |
|---|---|---|
| 23I | 네 기둥 4/4 → 전체 설치 9/9, 전체 재계획 한도 공유·정렬 복구·설치물 보존 | [23I](STEP_23I_FULL_POLICY_REPLAN.md) |
| 25C | 부품 배치·장애물 변경의 single/roles 8회. 조립 성공 6회, 한도 0에서 의도한 중단 2회 | [25C](STEP_25C_CHANGED_CONDITIONS.md) |
| 16C | 실제 외부 네이티브 플래너 실행, 단위·좌표·소스·실패와 시간 제한 기록 | [16C](STEP_16C_NATIVE_ASAP.md) |
| 16D | 명시적 선행조건 아래 10부품 분해 후보 생성. 새 역순은 아직 전체 로봇 실행 전 | [16D](STEP_16D_CONSTRAINED_NATIVE_PLAN.md) |
| 16E | 외부 후보의 지문·CAD·좌표 재검사 후 두 기둥 물리 설치 2/2 | [16E](STEP_16E_PLANNER_TO_ROBOT.md) |
| 22D | 개발 경험 2개·현재 물리 검증 절차 1개를 실행 전 고정. 조건 불일치 거부, 전달 구분·실행 확인 | [22D](STEP_22D_FROZEN_KNOWLEDGE.md) |
| 21D | 로컬 준비 진단·요청 미리보기·가짜 HTTP 응답의 복구 흐름 통합 검사 | [21D](STEP_21D_OFFLINE_READINESS.md) |

25C의 기대 동작 충족은 8/8이지만 조립 성공은 6/8이다. 두 의도적 중단을 성공 조립으로 세지 않는다. 선언한 개발 조건의 검사이며 새 평가셋의 일반화나 멀티 LLM 성능 비교가 아니다.

메모리는 과거 성공·실패 기록이고, 현재 스킬 후보는 `recover_alignment → insert`처럼 기존 동작을 조합한 JSON 절차다. 생성된 새 Python 동작 코드가 아니다. 별도 물리 재검증 뒤 제한된 조건에서 등록했다. 동결 묶음은 실행 중 바뀌지 않는 지식 사본이며 정책에 참고 정보로 전달한다. 규칙 정책 시험만으로 지식을 활용한 LLM의 성능 향상을 주장하지 않는다.

## 실행 명령

```powershell
cd C:\dev\llm-robot-assembly
# 실제 3초 안정화와 설치 환경 확인. 네트워크 없음.
uv run --locked python scripts/check_sim_readiness.py
# 요청 형태만 저장. 네트워크 없음.
uv run --locked python scripts/preview_llm_request.py
# 작은 조립 + 정렬 복구를 화면에서 확인
uv run --locked python scripts/run_frame_assembly.py --parts 2 --mode single --scenario first_alignment_offset --viewer --snapshots
# 전체 10부품 + 정책 운반 재계획 + 정렬 복구
uv run --locked python scripts/run_frame_assembly.py --parts 10 --mode single --scenario first_alignment_offset --transfer-obstacle --empty-approach-strategy direct_then_rrt --transport-replan-budget 9 --snapshots
```

전체 실행에 `--viewer`를 추가하면 창으로 볼 수 있다. 이번 실제 경과는 약 37분이었으며 다른 검사가 CPU를 공유했으므로 속도 비교 수치가 아니다. 그래프의 정책 호출 수와 실제 API 요청 수는 다르다.

## 최신 실행 근거

| 기록 | 결과 |
|---|---|
| `runs/step23_frame/20261101T095103_233879Z_a70f6b06` | 전체 설치 9/9, 운반 재계획 6·정렬 복구 1, API 0. 별도 trace 감사 통과 |
| `runs/step23_frame/20261101T094505_612205Z_eb62769c` | 네 기둥 4/4, 재계획 2·정렬 복구 1 |
| `runs/step25_variations/20261101T095931_833301Z_c6fa9743` | 변경 조건 8회, 기대 동작 8/8·실제 조립 6/8 |
| `runs/asap_native/20261101T095751_655043Z_006a36d4` | 실제 3부품 외부 분해 후보 생성 |
| `runs/asap_native/20261101T101241_037230Z_6897a2be` | 선행조건을 추가한 전체 분해 9단계와 로봇 입력 검사. 로봇 설치 0개 |
| `runs/step23_frame/20261101T101741_839942Z_afe3e4d5` | 외부 3부품 후보 → 두 기둥 설치 2/2. 기록 소스 지문 13개가 최종 코드와 일치 |
| `runs/step22_recipes/20261101T101848_881054Z_26e0be0f` | 최종 코드로 기존 복구 절차 재검증 성공, 현재 호환 등록본 1개 |
| `runs/step22_frozen_checks/20261101T101935_471571Z_d1eb177a` | memory/recipes 전달 분리 검사와 both 실제 첫 기둥 복구·설치 1/1 |
| `runs/readiness/20261101T100023_535826Z_92abc684` | offline_ready, 키·모델 미설정과 비활성 예시 설정을 정확히 표시 |
| `runs/step21_llm_preview/20261101T100029_099588Z_44ee5da6` | 실제 3초 관측으로 요청 미리보기 생성, API 0·조립 0 |

21G 코드로 다시 검증한 고정 파지용 동결 파일은 `runtime/knowledge/simulation/a9d4c8644ff89ffd7de953834771bb61c6b710b0c6a34e9b6eeb0480bdbfdeef.json`이다. 코드·조건이 바뀌면 다시 검증·동결해야 한다. 이전 묶음을 최신 등록본으로 취급하지 않는다.

외부 전체 후보 순서는 `post_1 → sleeve_1 → post_2 → sleeve_2 → post_3 → post_4 → sleeve_4 → sleeve_3 → cover`다. 네 기둥을 먼저 놓는 기존 전체 실행 순서와 다르다. 네이티브 기하 정규화와 MuJoCo 로봇 물리는 구분하며 네이티브 성공을 로봇 조립 성공으로 복사하지 않는다.

## 검사와 재현 범위

`uv run --locked python -m unittest discover -s tests -v`로 **169개 PASS**, 1673.965초를 확인했다. 그 뒤 추가한 외부 선행조건·후보 연결은 네이티브 계약 5개, 후보 입력 3개, 기존 두 기둥의 전체 재계획·설치물 보존 물리 검사 1개를 따로 실행해 모두 통과했다. 일부 중복된 검사이므로 합산해 신규 검사 수로 쓰지 않는다. 마지막 수정 뒤 전체 169개를 다시 실행했다고 주장하지 않는다.

장기 10부품 실행이 시작된 뒤 디스크의 실행기·관측 연결 일부가 갱신됐다. 실행은 시작 당시 모듈과 기록된 지문을 사용했으며 최신 파일로 전체를 다시 돌린 결과는 아니다. 최종 기본 동작은 두 기둥 물리 회귀로, 새 외부 입력은 두 기둥 통합 실행으로, 현재 경험·절차는 별도 재검증으로 확인했다. 상세 버전 차이는 23I 보고서에 남겼다.

이전 기반은 [기존 모델](STEP_03_UR3E_MODEL.md), [TCP](STEP_07_TCP_FRAME.md), [IK 이동](STEP_09_TCP_MOTION.md), [pick/place](STEP_12_14_PICK_PLACE.md), [RRT 경로](STEP_12B_RRT_CONNECT.md), [LangGraph](STEP_15_LANGGRAPH.md), [경험](STEP_22_EXPERIENCE_MEMORY.md), [절차](STEP_22C_SKILL_RECIPES.md)를 참고한다.

## 다음 작업

1. 첫 기둥의 수치 파지 제안은 [21F 기록](STEP_21F_PROPOSED_POST_GRASP.md)에서 확인한다. 첫 슬리브의 관통 검증은 [21G 기록](STEP_21G_PROPOSED_SLEEVE_GRASP.md)에 있다. 후속은 기둥·슬리브의 연속 실행과 파지 후 오정렬에 대한 실제 모델의 복구 판단이다.
2. 실제 모델 시험을 통과하면 전체 프레임과 실제 다중 LLM 정책으로 확장한다. 지금은 역할별 규칙 객체가 있는 상태다.
3. 새 외부 10부품 순서의 전체 물리 실행을 검증한다. 현재는 후보 생성·입력 검사와 작은 후보의 실제 로봇 연결까지 완료했다.
4. 대체 파지·접근·작업 분해·더 다양한 실패를 늘리고 개발 기록과 분리한 평가셋에서 모델·단일/다중·스킬/메모리 효과를 비교한다.

위 준비는 임의 OBJ의 결합법 자동 발견이나 범용 자율 공장 완성을 뜻하지 않는다. 24단계 실물은 현재 범위에서 제외한다. 전체 방향은 [개발 계획](ASSEMBLY_EXECUTION_ROADMAP.md), 이번 준비 항목은 [API 없는 작업표](API_FREE_WORK_PLAN.md)에 있다.

## 저장과 Git

실험 결과·이미지·CAD 내보내기·로컬 데이터·지식 묶음·키는 Git에서 제외했다. 실행 데이터는 `C:\dev\llm-robot-assembly\runs`, 경험·등록 절차는 `runtime`으로 OneDrive 밖이다. 코드와 보고서는 `codex/simulation-progress`에 로컬 커밋했다. 원격 push와 실물 명령은 수행하지 않았다.
