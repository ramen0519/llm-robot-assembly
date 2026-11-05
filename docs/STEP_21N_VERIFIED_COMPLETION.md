# 21N · 물리 검증 직후 완료하는 전체 Luna 조립

2026-11-05. 현재 코드로 빈 바닥판에서 기둥 4개·슬리브 4개·덮개까지 **9/9개를 한 장면에서 설치**했다. 상태 재개 없이 Luna의 파지 제안·물리 검사·수정·실행을 처음부터 끝까지 확인했다. 최종 최대 위치 오차 0.173mm, 덮개 구멍 최소 여유 1.832mm다.

## 이번 단위에서 바꾼 것

21L 재개 시험은 슬리브 4의 삽입 후 모델 완료 응답을 기다리다 시간 초과로 멈췄고, 21M 새 시험은 둘째 기둥의 삽입 후 입력 토큰 계산 연결이 실패했다. 두 결과는 실패 기록으로 보존했다. 같은 시각에 통신 문제가 발생했으나 네트워크 또는 서버 중 어느 쪽이 원인인지는 확인하지 못했다. 이후 호스트에서 API 주소의 인증 없는 요청에 HTTP 401이 돌아와 연결 가능 상태만 확인했다.

전체 프레임의 실제 LLM 모드에서는 이제 다음 순서로 부품 하나를 완료한다.

1. Luna가 부품 기준 파지 위치 XYZ와 손가락 닫힘 각도를 제안한다.
2. MuJoCo 복사본에서 집기와 삽입 경로를 검사한다. 거절된 후보의 접촉·도달 결과를 Luna에 전달하며, 부품당 기본 후보 한도는 3개다.
3. 승인한 파지로 원래 장면에서 실제 접촉·마찰을 이용해 집고 들어 올린다.
4. 새 관측을 보고 Luna가 삽입 또는 중단을 선택한다.
5. 실제 삽입·해제·후퇴 후 현재 물리 상태로 결합·지지·안정성을 검사한다.
6. 검사가 통과하면 그래프가 성공을 기록하고 다음 부품으로 넘어간다. 추가 LLM finish 요청은 하지 않는다.

이 변경은 마지막 판단의 책임을 물리 검증기에 둔다. 한 번에 승인되는 부품은 생성 요청 3회에서 2회로 줄어든다. 파지 위치를 사람이 고정하거나 삽입 검사를 생략하지 않는다. 다른 기존 실행기의 기본 프로토콜은 유지한다. 결과에는 completion_source=physics_verifier를 기록한다.

API 실패를 포함해 정상적인 적분 상태가 남는 모든 종료에서 terminal_checkpoint를 저장한다. 이는 시뮬레이션 상태 보존이며, 실패한 물리 동작을 자동으로 재생하라는 뜻이 아니다. --resume-run은 기존처럼 실제 집기 전 후보 한도 중단만 허용하고, 통신 실패 재개는 아직 지원하지 않는다.

## 검증

수정 후 관련 검사 10개를 통과했다. test_post_sleeve_episode.py 5개(209.732초), test_frame_resume.py 3개(8.509초), test_frame_checkpoint.py 2개(0.008초)다.

통신 연결 실패를 주입해 실제 MuJoCo 상태가 정확히 복원되는지 검사한다. 별도의 실제 기둥 집기·삽입 검사에서는 모델 요청 2회만으로 검증 완료하고 다음 부품에서 공통 예산을 넘지 않는지 확인한다. 이 검사에 사용하는 HTTP 응답은 오프라인 테스트용이며, 실제 Luna 성능과 구분한다.

실제 전체 실행은 빈 고정 바닥판과 자유 부품 9개에서 새로 시작한다. 기둥 1~4, 슬리브 1~4, 덮개 순서와 초기 배치는 지정되어 있다. 이전 실행의 성공 수를 합산하지 않는다. 최종 결과와 API 로그, 물리 상태의 연속성, 승인 파지와 실제 명령, 전체 결합·접촉력을 별도로 대조한다.

## 실제 전체 실행 결과

- 실행: runs/step23_frame/20261105T052814_462220Z_48e7b620
- 고정 바닥판 1개와 자유 부품 9개, 설치물 0개에서 시작했다. --resume-run 없이 한 프로세스에서 9개를 설치했다.
- 생성 요청 27회, 입력 토큰 64,604, 출력 토큰 2,933. 설정 단가 기준 추정 비용 USD 0.009210985이며 청구서를 대조한 값은 아니다. 이 값은 다른 실행과 합산하지 않은 이번 독립 실행의 비용이다.
- 시뮬레이션 진행 시간 1013.562초, 측정 506,961행, 프로그램 기록 경과 308.17분. 이 경과에는 모델 응답·후보 검사·물리 실행·기록과 렌더링이 포함된다.
- 최종 위치 오차 최대 0.1734mm. 슬리브 구멍 최소 여유 1.7945mm, 덮개 구멍 최소 여유 1.8320mm.
- 덮개의 슬리브별 수직 접촉력(N): 슬리브 1: 0.2016, 2: 0.1954, 3: 0.1943, 4: 0.1935.
- 마지막 추가 LLM 완료 호출 없이 9개 모두 물리 검증 직후 성공 처리했다. terminal_checkpoint와 최종 검증 상태가 정확히 일치한다.
- 본 장면 집기·삽입의 실패 보고서 0개. 모션 계층 직접 경로 검사 거절 0건과 LLM 운반 재계획 0회는 구분한다. 아래의 파지 후보 거절도 별도 기록이다.

| 부품 | 승인 X / Y / Z (mm), 닫힘 각도 (도) | 후보 수 | 생성 요청 | 설치 직후 최대 위치 오차 (mm) |
|---|---|---:|---:|---:|
| post_1 | 0 / 0 / 12, 0 | 2 | 3 | 0.1244 |
| post_2 | 0 / 0 / 6, 0 | 2 | 3 | 0.1137 |
| post_3 | 0 / 0 / 8, 0 | 2 | 3 | 0.1456 |
| post_4 | 0 / 0 / 10, 0 | 2 | 3 | 0.1296 |
| sleeve_1 | 0 / 0 / 6, 0 | 2 | 3 | 0.0911 |
| sleeve_2 | 0 / 0 / 8, 0 | 2 | 3 | 0.0681 |
| sleeve_3 | 0 / 0 / 8, 0 | 2 | 3 | 0.0882 |
| sleeve_4 | 0 / 0 / 8, 0 | 2 | 3 | 0.0266 |
| cover | 0 / 0 / 18.5, 0 | 2 | 3 | 0.1294 |

### 거절된 후보

- post_1: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → insert_lower/preflight, PLANNED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, assembly_base_wall_0_1
- post_2: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → insert_lower/preflight, PLANNED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, assembly_base_wall_2_1
- post_3: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → insert_lower/preflight, PLANNED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, assembly_base_wall_0_3
- post_4: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → insert_lower/preflight, PLANNED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, assembly_base_wall_2_3
- sleeve_1: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → pick_close, UNEXPECTED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, workcell_tabletop
- sleeve_2: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → pick_close, UNEXPECTED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, workcell_tabletop
- sleeve_3: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → pick_close, UNEXPECTED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, workcell_tabletop
- sleeve_4: {'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'jaw_yaw_deg': 0} → pick_close, UNEXPECTED_CONTACT: Forbidden compound-part contact: gripper_right_pad1, workcell_tabletop
- cover: {'x_mm': 0, 'y_mm': 0, 'z_mm': 36, 'jaw_yaw_deg': 0} → grasp_bounds, INPUT_OR_PRECONDITION_INVALID: z_mm outside [-19, 19].

### 독립 대조

independent_audit.txt에 실제 API 응답·승인된 파지·실행 명령의 일치, 전체 API 한도 준수, 여덟 부품 경계의 물리 상태 및 예산 연속성, 매 스텝 trace 시간과 행 수, 각 부품의 집기·들기·삽입·해제·후퇴, 최종 결합·지지와 상태 복원 검사를 보관했다. 실행 코드 21개의 지문도 실제 실행 시작 때의 값과 일치한다.

before_assembly.png와 after_부품 사진 9장, skill_trace.csv, graph_events.jsonl, llm_events.jsonl, resolved_config.json, result.json을 로컬 실행 폴더에 보관했다. 생성된 이미지·실험 로그는 Git에 넣지 않는다. 수정 후 관련 검사 10개를 통과했으며, 전체 실행 코드 버전은 f70e392다. 마지막 상태를 복원한 별도 검사에서도 덮개 관통·지지와 네 슬리브 관통을 재측정했다. 이 개발 배치 한 번의 성공을 새로운 제품의 일반화 성능이나 반복 성공률로 해석하지 않는다.

## 전체 조립 개발 시험 이력

| 시험 | 기록된 설치 / 9 | 생성 요청 | 알려진 추정 비용 (USD) | 종료 사유 |
|---|---:|---:|---:|---|
| 11월 4일 중단 | 8 | 33 | 0.011170625 | 최종 결과 없이 프로세스 종료 |
| 21K | 3 | 14 | 0.004542250 | 네 번째 기둥의 파지 후보 한도 |
| 21L (21K 재개) | 7 | 32 | 0.010577625 | 슬리브 4 삽입 이후 완료 응답 시간 초과 |
| 21M | 1 | 7 | 0.002269875 | 기둥 2 삽입 이후 완료 요청 전 입력 계산 연결 실패 |
| 21N (이번 새 실행) | 9 | 27 | 0.009210985 | 전체 물리 검증 완료 |

21L의 요청·비용은 21K를 포함한다. 두 행을 중복 합산하지 않는다. 21L의 시간 초과 요청 1회는 사용량을 모르므로 별도 $0.002524 예약액을 유지한다. 21L과 21M에서는 현재 부품의 pick/insert 검증 뒤 추가 완료 응답을 받지 못해 공식 설치 목록에 추가되지 않았다. 이를 이번 실행의 설치 개수에 합산하지 않는다. 위 표는 개발 중 서로 다른 코드와 프로토콜을 사용한 시험 이력이며 동일 조건 반복 성공률 비교가 아니다.

### 경과 시간 해석

- 도구 응답 대기 170.78분 동안 전후 관측한 본 장면 물리 시간은 796.796 → 803.952초였다. 같은 프로세스와 장면이 이어졌으며 원인은 확인하지 못했다. 기기 절전이었다고 단정하지 않는다.

장시간 대기 공백을 포함한 이번 실행의 경과 시간은 중단 없는 처리 성능 벤치마크로 사용하지 않는다. 원본 관측은 execution_gaps.jsonl에 보관했다.

## 실행 방법

```powershell
cd C:\dev\llm-robot-assembly
.\runtime\luna-venv\Scripts\python.exe scripts/run_frame_assembly.py --parts 10 --mode single --llm-config runs/step21n-budget.toml --transport-strategy direct_then_rrt --snapshots
```

키는 기존 프로세스 환경 또는 로컬 암호화 보관에서 읽고 파일·로그에 출력하지 않는다. 이 로컬 설정은 gpt-6-luna, 생성 50회, 총 300,000토큰, 추정 비용 $0.15 상한, 요청 제한 시간 60초다. 단가는 기존 2026-11-03 설정을 사용하며 청구서 확인값은 아니다. SDK 자동 재시도는 꺼져 있다. 앞선 시간 초과 요청의 사용량은 불명확하므로 $0.002524 예약액을 지우거나 0원으로 처리하지 않는다. 모든 실험을 합쳐 사용자가 승인한 $1 안에서 진행한다.

다른 컴퓨터에서는 config/llm.example.toml로 활성화한 모델·단가·예산 설정을 준비한다. --viewer로 새 실행하면 모델을 다시 호출한다. 저장된 사진과 기록을 읽는 데는 추가 호출이 없다.

## 현재 범위

정해진 CAD·결합 치수·초기 배치와 시뮬레이터 정답 상태에서의 프로토타입이다. 파지는 위에서 접근하는 조건 내 XYZ와 닫힘 각도를 제안한다. 완전한 6자유도 파지 생성, 자유 배치 비전 인식, 처음 보는 임의 조립품, 새로운 조립 순서 발견, 외부 분해 플래너의 전체 실시간 연결, 다중 LLM 비교, 실물 동작의 완성을 의미하지 않는다.

기본 동작은 Mink IK, 관절 제어와 기존 RRT-Connect가 실행한다. direct_then_rrt의 우회는 모션 계층 처리이며 LLM의 작업 재계획과 구분한다. 이번 전체 실행에 과거 성공 파지의 자동 스킬 생성·검색 또는 동결 지식 묶음은 넣지 않았다. 같은 부품에서 거절된 후보 이력은 수정 요청에 전달한다.

통신 오류의 공식 설명: [OpenAI API 오류 안내](https://developers.openai.com/api/docs/guides/error-codes). 이 안내만으로 이번 통신 장애의 원인을 확정하지 않는다.

## 저장한 실행 다시 검사하기

최종 result.json이 생성된 뒤 다음 명령으로 저장 기록을 대조한다. API 호출이나 새로운 물리 조립을 수행하지 않으며, 마지막 상태를 별도 MuJoCo 데이터에 복원해 결합을 재측정한다. 현재 실행 소스 지문과 일치하는 체크아웃에서 사용한다. 스냅샷을 포함한 빈 장면부터의 9개 연속 실행을 대상으로 한다.

```powershell
.\runtime\luna-venv\Scripts\python.exe scripts/audit_live_frame.py runs/step23_frame/20261105T052814_462220Z_48e7b620
```

통신 오류로 종료된 21M 기록에는 종료 코드 1을 반환하는 것을 확인했다. 이번 21N 전체 실제 실행에는 종료 코드 0과 independent_audit_passed를 반환했다. UTF-8 로그를 명시적으로 읽어 Windows 기본 인코딩의 차이를 처리한다. Python의 -O 옵션으로 검사를 끄는 실행은 거부한다.

저장된 네 기둥 상태를 별도 데이터에 복원하는 부분 검사도 통과했다. 실제 조립 종료 시에는 선언된 결합 접촉을 허용하는 검사 모드가 켜져 있으므로, 기록 검사기에서도 이 종료 모드를 명시적으로 복원한다. 초기 검사기는 조립 전 모드로 읽어 정상 지지 접촉을 거절했으며, 이 검사기 설정을 수정한 뒤 상태 배열·부품 경계 3곳·물리 결합과 지지의 재검사를 통과했다. 원래 조립 실행 코드는 변경하지 않았고 시뮬레이션 스텝을 추가하지 않았다.
