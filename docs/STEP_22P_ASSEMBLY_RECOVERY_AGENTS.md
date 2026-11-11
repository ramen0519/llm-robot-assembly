# 22P · 조립 에이전트와 복구 에이전트의 인계

**구현 및 무과금 검증.** 22O에서 Astra high가 내린 결정을 재생해 새 무조코 장면에서 첫 기둥 복구부터 덮개까지 9/9개를 설치했다. 이번 회차의 실제 API 호출은 0회, 비용은 $0였다. 두 역할이 각각 새 모델 응답을 받아 완주하는 시험은 별도 실행으로 남아 있다.

같은 프레임, 같은 외부 조립 순서, 같은 Astra high 설정과 무조코 실행기를 유지하고 판단을 두 에이전트로 분리했다. 조립 에이전트는 현재 부품의 파지와 삽입을 제안한다. 복구 에이전트는 관측된 실패와 인계 기록을 받아 스킬을 검색하거나, 수치 파지를 수정하거나, 작업을 중단할 수 있다. 두 에이전트의 모델 호출과 비용은 하나의 실행 예산에서 차감한다.

## 첫 기둥의 제어권은 언제 넘어가나

1. 개발 시나리오에서 첫 기둥의 파지 높이를 중심 기준 +1 mm로 준다. 무조코 사전검사에서 실제로 패드–소켓 벽 간섭이 검출된 경우에만 해당 스킬을 검색할 수 있다.
2. LangGraph가 원래 장면을 다시 관측하고 **조립 → 복구**를 기록한다. 실패 부품, 실패 단계, 거절 파지, 현재 관측 번호와 물리 상태의 식별값이 함께 전달된다.
3. 복구 에이전트가 검색어를 작성하고, 찾은 문서에서 읽을 자료와 적용할 스킬을 선택한다. 스킬 코드는 이번 실패의 예상 여유를 사용해 새 파지값을 계산한다. 검증된 스킬을 찾지 못해 보류하면 이유와 함께 멈춘다.
4. 새 파지의 사전검사를 통과한 뒤, 같은 원래 장면에서 집어 들어 올리고 양쪽 손가락의 접촉과 유지 상태를 검사한다. 이 검사가 통과해야 **복구 → 조립**으로 인계한다.
5. 조립 에이전트는 현재 관측과 인계 증거를 받아 기둥 삽입을 결정한다. 이후 부품도 같은 장면을 이어받는다. 다른 부품에서 수정 가능한 수치 파지 실패가 발생하면 복구 에이전트가 현재 부품의 접촉 정보로 새 좌표를 제안할 수 있다.

## 실행과 증거

무과금 회차는 `runs/offline_role_rehearsal/20261111T090200_749801Z_8e8f4b04`다. 시뮬레이터의 집기·운반·삽입·검사 전체를 다시 실행했다. 에이전트 판단 입력에 대해서는 원래 부품·목표·관측 번호·허용 동작·전체 작업대 관측과의 일치를 확인한 뒤 저장된 결정을 사용했다. 복구 역할은 이전 검색어와 문서 선택으로 동결된 저장소를 다시 조회했다.

| 물리 시점 | 실제로 확인한 인계 |
|---|---|
| 3.000초 / 첫 기둥 | 예상 패드–소켓 벽 간섭을 새로 검출하고 조립 → 복구. 등록 스킬이 새 관측으로 파지 높이 +4.37 mm를 계산했다. |
| 31.518초 / 첫 기둥 | 원래 장면에서 기둥 바닥이 작업대에서 99.474 mm 들린 사실을 검증하고 복구 → 조립. 조립 역할이 현재 관측과 인계 기록을 받아 삽입했다. |
| 747.420초 / 슬리브3 | 패드–작업대 접촉으로 초기 −7 mm 후보가 거절돼 조립 → 복구. 저장된 수치 수정 결정으로 +10 mm 후보를 재검사했다. |
| 807.872초 / 슬리브3 | 원래 장면에서 집기가 확인된 후 복구 → 조립. 이후 슬리브3 삽입과 덮개 설치까지 이어졌다. |

최종 상태를 복원한 독립 검사에서 부품 간 8회 연속성, 최종 최대 위치 오차 **0.227 mm**, 덮개 구멍 최소 측면 여유 **1.823 mm**를 확인했다. 슬리브 1~4가 덮개를 받치는 수직 하중은 각각 0.209, 0.181, 0.207, 0.196 N이었다. 물리 상태는 같은 결정을 사용한 22O의 최종 상태와 일치했다. 실제 경과 약 29분 51초. 새 역할 판단의 성능·비용은 이번 숫자로 추정할 수 없다.

관련 자동 검사 65개가 통과했다. 검사에는 제어권이 없는 정책의 동작 거부, 새 관측·집기 증거가 없는 복귀 차단, 두 역할의 공통 한도 소진, 혼동된 모델 응답 ID·역할별 정산 검출이 포함된다. 유료 회차의 검증기는 역할별 원본 모델 응답·비용을 그래프와 대조하며 오프라인 재생 기록을 라이브 증거로 인정하지 않는다.

```powershell
cd C:\dev\llm-robot-assembly
$python = '.\runtime\luna-venv\Scripts\python.exe'
$candidate = 'runs/asap_native/20261101T101241_037230Z_6897a2be/assembly_candidate.json'
$rehearsal = 'runs/offline_role_rehearsal/20261111T090200_749801Z_8e8f4b04'
# 저장된 무료 회차의 최종 물리 상태와 두 차례의 왕복 인계를 재측정
& $python -B -X utf8 "$rehearsal/verify_rehearsal.py" $rehearsal
# 보관된 결정으로 같은 시험을 새 장면에서 다시 실행 (API 호출 0회)
& $python -B -X utf8 "$rehearsal/rehearse_roles.py" (Get-Location).Path
# OPENAI_API_KEY가 프로젝트 계정으로 제공된 환경에서 새 유료 회차 실행
& $python -B -u -X utf8 scripts/run_frame_assembly.py --parts 10 --mode roles --planner-candidate $candidate --scenario first_post_grasp_interference --grasp-skill-library runtime/skills/simulation/post_recovery_v1 --llm-config config/llm.astra-high-native-frame.toml --transport-strategy direct_then_rrt --empty-approach-strategy direct_then_rrt --snapshots
# API를 호출하지 않는 역할 경계 검사
& $python -B -X utf8 -m unittest discover -s tests -p test_role_transfer.py
& $python -B -X utf8 -m unittest discover -s tests -p test_live_agent_roles.py
```

역할별 지시·도구와 모델 요청은 [모델 정책](../scripts/openai_policy.py), 제어권과 물리 검증 순서는 [조립 그래프](../scripts/assembly_graph.py), 이전과 동일한 공유 한도는 [실행 설정](../config/llm.astra-high-native-frame.toml)에 있다. `graph_events.jsonl`에는 인계 시점과 물리 증거를, `llm_events.jsonl`에는 요청을 보낸 역할을, `result.json`에는 각 역할의 호출·토큰·비용과 전체 조립 결과를 저장한다.
