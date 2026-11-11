# 22S · 세 기둥을 삼각형으로 배치한 새 제품

**2026-11-12. 외부 플래너가 새 제품의 CAD로 구한 순서를 받아 Astra high 조립·복구 에이전트가 빈 바닥판에 기둥 3개, 슬리브 3개와 덮개 1개를 한 장면에서 설치했다. 두 번의 간섭을 복구했고, 다른 제품에서 기록한 슬리브 경험을 검색한 2개 부품은 모두 첫 파지로 설치했다.**

## 실시간 조립 결과

| 조립 순서 | 부품 중심 기준 파지 높이 | 원래 장면의 결과 |
|---|---|---|
| 기둥3 | −4 → +8 mm | 오른쪽 패드와 바닥판 벽의 삽입 간섭을 복구. 예상 패드 여유 −7.30 → +4.68 mm. |
| 슬리브3 | +12 mm | 이전 제품의 슬리브1 경험을 검색하고 첫 파지로 설치. |
| 기둥1 | +8 mm | 첫 파지로 설치. |
| 슬리브1 | −5 → +12 mm | 집는 도중 패드가 작업대에 닿는 후보를 복구하고 설치. |
| 기둥2 | +10 mm | 첫 파지로 설치. |
| 슬리브2 | +12 mm | 이전 제품의 슬리브1 경험을 검색하고 첫 파지로 설치. |
| 덮개 | +12 mm | 세 기둥 위로 끼운 뒤 그리퍼를 열고 물러나 지지를 확인. |

높이는 부품 좌표계의 Z값이다. 이 회차에서 X·Y 오프셋과 턱 회전 제안값은 전부 0이었다. 두 실패 모두 복사된 상태의 사전검사에서 측정됐다. 복구 에이전트가 새 파지를 제안하고, 원래 장면의 집기가 검증된 뒤 조립 에이전트가 삽입을 이어받았다.

- **물리 검증.** 원래 장면의 397,753개 표본에서 부품별 집기·들기·삽입·해제·후퇴가 모두 이어졌다. 부품 간 6개 경계의 전체 물리 상태가 일치했다.
- **최종 결합.** 7개 부품의 최대 위치 오차 0.305 mm. 슬리브 구멍의 최소 측면 여유 1.753 mm, 덮개 구멍은 1.740 mm, 덮개 개구면을 지나 기둥이 뻗은 최소 길이는 5.926 mm였다. 슬리브 1·2·3이 덮개를 받치는 힘은 각각 0.175, 0.226, 0.384 N이었다.
- **호출과 시간.** 조립 16회(경험 검색 2회 포함), 복구 2회. 합계 입력 38,428토큰, 출력 7,381토큰. 사용량과 설정 단가를 곱한 추정 비용은 **$0.84161**이고 미정산 요청은 0회다. 실제 경과 31분 11초, 시뮬레이션 시간 795.226초.
- **결과 위치.** `runs/step23_frame/20261111T160843_513944Z_29d9f165`의 `triangle_live_audit.json`에 부품별 첫 후보·수정·검색·모델 호출 및 최종 측정이 있고 `triangle_assembly_stages.png`에 세 시점의 실제 장면이 있다.

기존 10부품 프레임과 새 8부품 배치에서 시연 가능한 시뮬레이션 프로토타입으로 기록한다. 새 제품의 실시간 성공은 1회다. 비교군을 둔 반복 성공률, 임의의 입력 CAD에서 결합 정보를 추론하는 기능, 실물 로봇 검증은 이번 결과의 범위 밖이다.

## 무엇을 바꿨나

160 mm 정사각 바닥판과 덮개에서 구멍을 네 귀퉁이에 두던 배치를 세 꼭짓점으로 바꿨다. 부품 구성은 바닥판 1개, 기둥 3개, 슬리브 3개, 덮개 1개다. 바닥판을 작업대에 고정하고 나머지 7개를 로봇이 설치한다. 구멍 중심 좌표는 바닥판 중심 기준 (−40, −40), (+40, −40), (0, +40) mm다. 기둥과 슬리브의 단면, 수직 끼움 방식 및 로봇은 이전 제품과 같다.

CAD의 부품 관계로부터 바닥판·덮개의 구멍, 무조코 충돌 형상, 덮개의 목표 관통 기둥과 지지 슬리브 목록을 생성한다. 에이전트는 현재 형상과 조립 목표를 읽는다. 사람이 선언한 결합 선행조건으로 외부 ASAP/RedMax의 제거 후보를 제한하고, 실제로 분해 경로와 잔여 부품의 안정성을 통과한 순서를 역전해 조립 후보를 얻는다. 이 후보는 같은 제품의 CAD 지문과 좌표계를 다시 대조한 뒤 사용한다.

## 판정 기준

- 독립된 새 CAD에 대해 네이티브 분해 계획이 고정 바닥판 하나만 남길 때까지 완결돼야 한다.
- 원래 무조코 에피소드가 설치물 0개로 시작하고, 각 집기·삽입이 물리 검증을 거쳐야 한다. 이어지는 부품 사이에서 로봇과 모든 부품의 상태가 연속이어야 한다.
- 완성 시점의 위치·방향과 해제·지지를 검사한다. 마지막 상태를 복원해 각 기둥이 해당 슬리브와 덮개의 위아래 개구면을 통과하는지, 세 슬리브가 각각 덮개를 받치는지 다시 측정한다.

이번 제품은 기존 수직 끼움 제품군의 **새 개발 구성**이다. 입력에 CAD 및 결합 관계가 포함되며, 향후 성능 비교용으로 따로 보관할 미사용 제품과 구분한다.

## 재현 순서

저장소 루트에서 프로젝트 Python을 사용한다. 첫 명령은 기존 WSL의 ASAP/RedMax 설치를 사용하며 모델 API를 호출하지 않는다.

```powershell
$assemblyPy = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPy -B -u -X utf8 scripts/run_asap_native.py --layout triangle_three_post --parts 8 --timeout 600 --budget 30 --respect-robot-prerequisites
```

출력된 새 폴더 안의 `assembly_candidate.json` 경로를 `$assemblyPlan`에 지정한다. 아래 명령은 기본 파지와 로봇의 실제 접촉·마찰을 사용해 전체 동작을 시험한다. 부품을 집기 전후의 물리 상태를 남긴다.

```powershell
& $assemblyPy -B -u -X utf8 scripts/run_frame_assembly.py --layout triangle_three_post --parts 8 --planner-candidate $assemblyPlan --mode scripted --transport-strategy direct_then_rrt --empty-approach-strategy direct_then_rrt --snapshots
```

실시간 조립·복구 에이전트 모드에서는 같은 계획·장면에 `--mode roles`, 모델 설정 파일, 슬리브1의 경험 파일 및 검색 대상 `--sleeve-memory-targets sleeve_2 sleeve_3`를 지정한다. 이전 제품용 기둥 실패를 인위적으로 만드는 조건은 이 구성에서 사용하지 않는다.

```powershell
$assemblyPlan = 'runs/asap_native/20261111T155529_503873Z_023f6ae9/assembly_candidate.json'
$sleeveExperience = 'runtime/memory/simulation/sleeve_grasp_v1/719902ec44642017e2d48067db7d3f3a05f5a183736331aa3096d7dbc7302633.json'
& $assemblyPy -B -u -X utf8 scripts/run_frame_assembly.py --layout triangle_three_post --parts 8 --mode roles --scenario nominal --planner-candidate $assemblyPlan --sleeve-memory $sleeveExperience --sleeve-memory-targets sleeve_2 sleeve_3 --llm-config config/llm.astra-high-native-frame.toml --empty-approach-strategy direct_then_rrt --transport-strategy direct_then_rrt --snapshots
```

실시간 명령은 두 에이전트가 `OPENAI_API_KEY`와 회차의 합계 예산을 공유한다. 저장된 감사는 키 없이 실행할 수 있다.

```powershell
& $assemblyPy -B -X utf8 scripts/audit_variant_frame.py runs/step23_frame/20261111T160843_513944Z_29d9f165 --planner-candidate $assemblyPlan
& $assemblyPy -B -X utf8 scripts/audit_variant_frame.py runs/step23_frame/20261111T160429_197818Z_5e6a6ad0 --planner-candidate $assemblyPlan --physics-only
```

제품 장면, 덮개 판정, 네이티브 입력, 실제 에이전트 진입 경로 관련 테스트 25개가 통과했다. 기존 네 기둥의 분해 결과를 삼각 제품용으로 주면 움직이기 전에 거절한다. 같은 삼각 후보를 실행 후 강화된 입력기로 다시 읽어 원래 실행의 출처 정보·순서와 일치함을 확인했다.

## 새 분해 순서

ASAP/RedMax가 507.6초에 해를 찾았다. 원본 분해 결과의 `selected_edges` 7개를 확인했다. **덮개 → 슬리브2 → 기둥2 → 슬리브1 → 기둥1 → 슬리브3 → 기둥3**을 제거하면 고정 바닥판만 남는다. 조립 실행은 마지막에 제거한 뒤쪽 중앙 기둥3부터 시작한다. 입력·원본 보고서·역순 계획은 `runs/asap_native/20261111T155529_503873Z_023f6ae9`에 저장했다.

## 기본 파지로 확인한 기계적 실행

모델 호출 없이 동일한 새 순서를 빈 바닥판에서 끝까지 실행했다. 자유 부품 7개가 모두 설치됐고, 393,617개 추적 표본 및 6개 부품 간 경계에서 원래 장면의 연속성을 확인했다. 마지막 물리 상태를 복원해 잰 최대 위치 오차는 0.283 mm였다. 덮개 구멍의 최소 측면 여유는 1.771 mm, 슬리브 1·2·3이 받치는 힘은 각각 0.204, 0.193, 0.387 N이었다. 회차는 `runs/step23_frame/20261111T160429_197818Z_5e6a6ad0`이다.

## 등록된 기둥 스킬의 적용 범위

실시간 회차와 별개로 기존 스킬 저장소에 새 기둥의 실패 단계와 접촉 코드를 질의했다. 이전 제품의 기둥1 코드가 검색되지만, 등록된 부품·결합 목표와 장면 조건 해시가 새 제품의 기둥3과 달랐다. 기존 코드는 실행 범위를 그대로 유지했다. 이 회차의 기둥3 복구 방법은 복구 에이전트가 새 측정값을 보고 직접 다시 제안한 파지 높이다. 슬리브2·3에서 참고하는 과거 슬리브1 실패·복구 경험은 별도 기록이다.
