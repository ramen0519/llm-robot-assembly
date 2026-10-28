# LLM Robot Assembly

MuJoCo에서 조립 작업을 계획·실행·검증하고, 성공과 실패 경험을 재사용하는 LLM 기반 로봇 조립 졸업프로젝트.

현재 단계는 **14단계: 설정 가능한 작업대에서 현재 자세 기반 TCP 이동, 상자 한 개의 물리적인 집기·운반·놓기와 결과 판정까지 완료**다. 단계별 성공/실패·접촉·관측을 저장한다. LangGraph 연결은 다음 15단계이며, 삽입·약 10부품 조립·실물 연결은 이후 확장한다.

개발 경로는 `C:\dev\llm-robot-assembly`이며, GitHub 저장소는 [ramen0519/llm-robot-assembly](https://github.com/ramen0519/llm-robot-assembly)다.

## 목표와 범위

- 1인 개발, 약 4주, 최종 실물 조립 시연.
- 약 10개 제품 부품을 조립하는 제한된 작업. 조립체 형상은 미확정.
- MuJoCo 시뮬레이션을 먼저 완성한 뒤 실제 로봇을 연결.
- LLM API와 LangGraph 단일 에이전트로 시작. 파인튜닝은 수행하지 않음.
- 검증된 기본 스킬의 조합과 실행 경험을 저장·검색.
- 이후 같은 인터페이스로 멀티에이전트를 추가해 비용과 성능을 비교.

연구실 발표자료에는 **UR3e + Robotiq 2F-85**가 명시되어 있으며, 현재 시뮬레이션에는 사용자가 선택한 UR3e 모델을 불러왔다. 실제 사용할 장비의 기종·그리퍼 세대·제어 환경은 실물 연동 전에 확인한다. RGB-D 카메라는 사용 가능하며, 첫 시뮬레이션에서는 시뮬레이터가 제공하는 상태를 사용한다.

## 시뮬레이션과 디지털트윈

첫 구현은 MuJoCo 조립 시뮬레이션이다. 이후 실제 관절·그리퍼·부품 상태를 가상환경에 반영하고, 계획의 사전 검증과 실물 실행 결과의 피드백을 연결해 디지털트윈 기반 시스템으로 확장한다. 아직 실물과 동기화되는 디지털트윈을 구현한 상태는 아니다.

시뮬레이션에서 검증한 스킬과 절차를 실물로 적용하는 부분은 Sim-to-Real 관점으로 평가할 수 있다. 프로젝트의 중심은 LLM의 작업 계획·실패 대응·경험 재사용이며, 시뮬레이션에서 신경망 제어 정책을 학습해 이전하는 과제는 초기 범위에 포함하지 않는다.

## 개발 순서

- [x] 준비: Python 전용 환경과 MuJoCo 설치·import 확인
- [x] 기초 장면: 공 낙하, 바닥 접촉, 안정 상태 검사와 뷰어 실행
- [x] M1 일부: UR3e 로봇팔 모델 로딩과 정지 자세 표시
- [x] M1 일부: 첫 관절의 완만한 목표값 변경과 실제 각도·속도 기반 도착 판정
- [x] M1 일부: Robotiq 2F-85 결합과 열린 상태의 초기 자세 유지
- [x] M1 일부: 그리퍼의 완만한 개폐와 실제 패드 간격·속도 기반 완료 판정
- [x] M2 일부: 명목 TCP 위치·방향 읽기, 좌표축 표시와 모델 내부 변환 검사
- [x] M2 일부: mink로 TCP 위쪽 30mm 목표의 관절 각도 계산·오차 및 제한 검사 (정지 자세, 모터 실행 전)
- [x] M2 일부: 중력 보상 위치 제어로 TCP 이동, 중간 자세·실제 이동 중 접촉 검사와 도착·안정 판정
- [x] M2 일부: FailureReport에 실패 코드·시점·목표/실제 상태·접촉을 저장하고 실패 시 중단
- [x] M1: 설정 파일로 작업대·UR3e·2F-85·시험 부품을 구성하고 자세 유지·상태 읽기·부품 안정화 검사
- [x] M2: 기존 Mink IK, 현재 자세 기반 중간 목표 이동, 로봇·그리퍼·운반 부품의 접촉·관절 한계 검사
- [x] M3 일부: 한 부품의 물리적인 집기·운반·놓기와 관측 기반 결과 판정 (12~14단계)
- [ ] 다음 15단계: 현재 observe/pick/place 스킬을 LangGraph 단일 흐름에 연결
- [ ] M3 나머지: 정렬·삽입과 결합 결과 판정
- [ ] M4: 스크립트 기준선으로 3→6→약 10부품 조립
- [ ] M5: LangGraph 단일 LLM의 계획·검증·실패 처리
- [ ] M6: 경험 저장·검색, API 사용량 기록, 시뮬레이션 v1 검증
- [ ] M7: 실물 연결·보정·기본 조작 검증과 전체 조립
- [ ] M8: 상태 변화·실패 대응·메모리 효과 평가와 시연

한 부품을 실제 시뮬레이션 접촉으로 집어 지정 위치에 놓고, 부품 위치와 안정 상태로 성공 여부를 확인하는 첫 실행 목표를 달성했다. 다음은 이 스킬을 LangGraph에서 호출하는 구조다. 최종 약 10부품 조립 목표와 실물 검증은 별도로 남아 있다.

## 시스템 경계

| 구성 | 역할 |
|---|---|
| 에이전트 | 목표와 현재 상태를 받아 허용된 스킬을 선택 |
| 스킬 실행기 | 집기·배치·정렬·삽입을 수행하고 상태를 반환 |
| 모션 기능 | IK, 이동 경로, 충돌 검사, 궤적 실행 |
| 환경 adapter | MuJoCo와 실제 장비의 관측·실행 연결 |
| 결과 판정 | 측정 가능한 조건으로 단계·전체 조립 성공 판정 |
| 메모리·로그 | 적용 조건과 결과, 실패, 시간, API 사용량 저장 |

MuJoCo 3.17.0과 mink 1.3.0으로 가까운 TCP 목표의 IK 계산과 모터를 통한 이동·도착을 확인했다. 현재는 단순 관절 보간과 샘플별 접촉 검사이며, 범용 경로 탐색이 필요해지면 OMPL 또는 기존 MoveIt 구성을 검토한다.

## 문서

- [1단계: Python 환경과 MuJoCo 설치 설명](docs/STEP_01_ENVIRONMENT.md)
- [2단계: 바닥과 공의 중력·충돌 확인](docs/STEP_02_FALLING_BALL.md)
- [3단계: UR3e 모델 불러오기](docs/STEP_03_UR3E_MODEL.md)
- [4단계: UR3e 관절 하나 움직이고 도착 확인하기](docs/STEP_04_ONE_JOINT.md)
- [5단계: Robotiq 2F-85 장착과 초기 자세 유지](docs/STEP_05_GRIPPER_MOUNT.md)
- [6단계: 그리퍼를 닫고 다시 열며 실제 상태 확인](docs/STEP_06_GRIPPER_CONTROL.md)
- [7단계: TCP 위치·방향과 좌표축 확인](docs/STEP_07_TCP_FRAME.md)
- [8단계: TCP 목표에서 관절 각도 계산하기 — IK](docs/STEP_08_TCP_IK.md)
- [9단계: IK 결과로 천천히 이동하고 실제 TCP 도착 확인](docs/STEP_09_TCP_MOTION.md)
- [10단계: 실패 코드·시점·목표/실제 상태·접촉을 JSON으로 기록](docs/STEP_10_FAILURE_REPORT.md)
- [11단계: 실측값으로 바꾸기 쉬운 작업대·부품 장면](docs/STEP_11_CONFIGURABLE_WORKCELL.md)
- [실패 상황과 Fallback 설계 — 자동 복구 미구현](docs/FAILURE_RECOVERY_DESIGN.md)
- [전체 개발 계획](docs/PROJECT_PLAN.md)
- [입문 설명서](docs/BEGINNER_GUIDE.md)
- [개발 작업 지침](AGENTS.md)

## 개발 원칙

- 정해진 순서의 스크립트로 동작을 검증한 뒤 LLM을 연결한다.
- 시뮬레이션 성공과 실물 성공을 따로 기록한다.
- 물체 순간이동이나 비물리적 부착을 사용하는 디버그 동작은 물리 조립 성능 평가에서 제외한다.
- 파지한 물체와 이미 조립된 부품을 충돌 검사에 포함한다.
- 부품 수, 로봇이 설치한 부품 수, 전체 성공률과 단계별 성공률을 구분한다.
- 실물 동작은 자동 재전송하지 않고 상태를 먼저 확인한다.
- API 키·개인 설정·실험 생성물·연구실 참고자료 원본은 Git에 추가하지 않는다.

이 저장소는 선배들의 구현을 확보·이전한 프로젝트가 아니다. 선행 연구는 참고 자료이며, 재사용할 코드는 출처·라이선스·동작 여부를 확인한 뒤 추가한다.

## 실행 환경

2026-10-26 현재 작업 PC에서 확인한 환경:

- Windows, Python 3.11.15, uv 0.11.32.
- MuJoCo 3.17.0. 직접 의존성은 `pyproject.toml`, 전체 해결된 패키지 버전은 `uv.lock`에 기록.
- Intel Iris Xe Graphics, RAM 약 16GB. 사용자가 별도로 언급한 RTX 4070 SUPER급 PC에서의 실행은 아직 확인하지 않았다.
- 프로젝트 전용 환경: `C:\dev\llm-robot-assembly\.venv` (Git 제외).

PowerShell에서 설치 상태를 다시 확인한다.

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/check_environment.py
```

확인된 핵심 출력:

```text
Python: 3.11.15
Virtual environment: True
MuJoCo package: 3.17.0
MuJoCo engine: 3.17.0
MuJoCo import: OK
```

설정 파일만 받은 다른 컴퓨터에서는 uv가 준비된 상태에서 `uv sync --locked`로 환경을 만든다. Python 3.11 계열을 사용하며 세부 패치 버전과 실행 경로는 컴퓨터에 따라 다를 수 있다. `.venv`를 복사해서 옮기지 않는다.

기본 환경의 import·내부 엔진 버전·의존성 검사에 더해, 아래의 작은 장면에서 물리 계산과 렌더링·뷰어 실행을 확인했다. UR3e 모델은 정지 표시, 제한된 한 관절 이동, 그리퍼 개폐와 아래의 가까운 TCP 목표 이동까지 확인했다. 환경 구성은 [1단계 설명서](docs/STEP_01_ENVIRONMENT.md)를 참고한다.

바닥과 공의 낙하 장면을 실행한다. 3초짜리 실험을 반복하며 창을 닫으면 종료된다.

```powershell
uv run --locked python scripts/drop_ball.py
```

창 없이 중력·접촉·안정 상태를 수치로 확인한다.

```powershell
uv run --locked python scripts/check_drop.py
```

공은 약 0.334초에 바닥과 접촉했고, 최종 중심 높이 0.049930m에서 안정됐다. 네 가지 수치 검사와 8초 제한 뷰어 실행을 확인했다. 접촉 모델은 작은 겹침을 허용하며, 정밀 조립 성능을 확인한 결과는 아니다. 설정·코드·판정 기준은 [2단계 설명서](docs/STEP_02_FALLING_BALL.md)를 참고한다.

UR3e의 저장된 초기 자세를 표시한다. 이번 뷰어는 물리 시간을 진행하지 않는다.

```powershell
uv run --locked python scripts/view_ur3e.py
```

창 없이 로딩과 관절 구조만 확인한다.

```powershell
uv run --locked python scripts/view_ur3e.py --check
```

커뮤니티 UR3e 모델의 회전 관절 6개·메시 20개 로딩, 정지 렌더링, 뷰어 정상 종료를 확인했다. 원본 라이선스와 고정 커밋은 [출처 기록](models/universal_robots_ur3e/PROVENANCE.md)에 보관한다. 이 팔 단독 예제는 범용 자세 유지 성능·충돌 형상 정확도·실물 보정을 검증하지 않는다. 그리퍼 결합·개폐는 아래의 별도 예제에서 확인한다. 설명은 [3단계 문서](docs/STEP_03_UR3E_MODEL.md)를 참고한다.

첫 관절을 약 -90도에서 -60도로 천천히 움직이고 도착을 확인한다. 초기 유지 1초, 목표 변경 4초, 안정화 2초 후 창이 닫힌다.

```powershell
uv run --locked python scripts/move_one_joint.py
```

창 없이 같은 물리 계산과 판정을 실행한다.

```powershell
uv run --locked python scripts/move_one_joint.py --headless
```

마지막 0.5초 동안 각도 오차 0.5도 이하와 속도 0.5도/초 미만을 확인한다. 실제 검증에서는 최종 -60.000016도, 최대 이동 속도 11.140780도/초였고 예상치 못한 접촉·관절 한계 초과·엔진 경고가 없었다. 다른 관절은 같은 목표를 유지하지만 중력과 동역학으로 최대 0.725444도 변했다. 측정 기록은 Git에서 제외된 `runs/step04/one_joint.csv`에 저장된다. 판정 기준과 설명은 [4단계 문서](docs/STEP_04_ONE_JOINT.md)를 참고한다.

UR3e에 2F-85를 장착하고 열린 상태로 5초간 자세를 유지한다.

```powershell
uv run --locked python scripts/view_ur3e_gripper.py
uv run --locked python scripts/view_ur3e_gripper.py --check
```

팔 6개와 그리퍼 8개의 물리 관절, 총 7개의 제어 입력을 확인했다. 그리퍼 무게를 포함한 5초 유지 검사에서 팔의 최대 각도 변화는 약 1.07도였고 예상치 못한 접촉이나 엔진 경고가 없었다. 이 예제는 열린 상태의 유지 검사다. 개폐는 아래 6단계에서 확인하고 물체 파지는 후속 단계다. 기존 팔 단독 예제는 그대로 유지한다. 결합 방법과 검사 기준은 [5단계 설명서](docs/STEP_05_GRIPPER_MOUNT.md)를 참고한다.

그리퍼를 4초 동안 천천히 닫고, 잠시 유지한 뒤 다시 연다. 총 13초 후 실제 상태를 판정한다.

```powershell
uv run --locked python scripts/move_gripper.py
uv run --locked python scripts/move_gripper.py --headless
```

`Gripper open/close: PASS`가 출력되면 두 패드 구간의 닫힘·재열림, 마지막 0.5초의 안정 상태와 경로 중 검사 기준을 통과한 것이다. 열린 간격은 약 85.12mm, 닫힘 유지 구간의 가장 큰 간격은 약 0.825mm였다. 예상한 손가락 패드끼리의 접촉만 허용한다. 이 빈 그리퍼 예제에는 물체 파지가 없으며, 물체 파지는 아래 13단계에서 확인한다. 측정값은 Git 제외 경로 `runs/step06/gripper_cycle.csv`에 매번 덮어쓴다. 설명과 실제 검증 범위는 [6단계 문서](docs/STEP_06_GRIPPER_CONTROL.md)를 참고한다.

닫히지 않는 동작·미완료 기록·바닥 충돌을 성공으로 오인하지 않는 회귀검사도 실행할 수 있다.

```powershell
uv run --locked python -m unittest discover -s tests -v
```

그리퍼의 집기 기준점(TCP)을 표시하고 월드 기준 위치와 X·Y·Z축 방향을 읽는다. 로봇을 정지시킨 좌표 확인용 뷰어다.

```powershell
uv run --locked python scripts/view_tcp.py
uv run --locked python scripts/view_tcp.py --check
```

노란 점이 TCP이고 X축은 빨강, Y축은 초록, Z축은 파랑이다. 창은 직접 닫으며 `--seconds 5`로 자동 종료할 수 있다. 모델 정의 검사 통과 시 `TCP frame definition: PASS`가 출력된다. 초기 TCP 위치는 월드 기준 약 `[0.130998, -0.297500, 0.147550]`m이고, 손목 장착 기준점과의 거리는 155.8mm다. 이는 모델의 고정 기준점이며 실물 TCP 보정은 아니다. 실행 코드와 좌표계 설명은 [7단계 문서](docs/STEP_07_TCP_FRAME.md)를 참고한다.

TCP를 월드 기준 위쪽 30mm로 옮기면서 방향을 유지할 관절 각도를 계산한다. 결과 자세는 정지 미리보기이며 실제 모터 이동은 수행하지 않는다.

```powershell
uv run --locked python scripts/solve_tcp_ik.py
uv run --locked python scripts/solve_tcp_ik.py --check
```

`TCP IK calculation: PASS`는 IK 목표 오차와 후보 자세의 검사 통과를 의미한다. 실제 확인값은 위치 오차 약 0.004801mm, 방향 오차 0.000023도였다. 손가락 관절을 고정하고 팔의 관절 범위를 지키며, 검사한 후보에서 검출된 접촉은 없었다. 연속 이동 경로·모터 제어·실물 정밀도를 검증한 결과는 아니다. IK/FK의 차이와 실행 흐름은 [8단계 문서](docs/STEP_08_TCP_IK.md)를 참고한다.

IK로 계산한 목표를 모터에 전달해 TCP를 위로 30mm 이동한다. 1초 유지, 4초 이동, 2초 안정화 후 실제 TCP와 속도로 성공을 판정한다.

```powershell
uv run --locked python scripts/move_tcp.py
uv run --locked python scripts/move_tcp.py --headless
```

중력 보상은 원래 힘 제한이 있는 위치 구동기를 통해 적용하며 실제 상태는 `mj_step()`으로 갱신한다. 사전 중간 자세 101개와 물리 실행 중 2ms마다 접촉·상태를 검사했다. 마지막 0.5초의 최대 위치 오차는 약 0.00491mm, 방향 오차는 약 0.0000254도였고 `TCP physical motion: PASS`를 확인했다. 현재 CLI는 10단계의 실행별 `runs/step10/<run_id>/tcp_motion.csv`에 기록한다. 현재 장면의 샘플별 검사이며 연속 충돌 방지 보장이나 물체 파지는 아니다. 중력 보상의 이유, 코드 흐름과 실패 검사는 [9단계 문서](docs/STEP_09_TCP_MOTION.md)를 참고한다.

실패하면 같은 실행 폴더에 `failure_report.json`을 저장한다. 실패 코드, 단계·시각, 목표와 실제 TCP·관절 상태, 접촉 쌍·거리, 마지막 정상 관측을 확인할 수 있다. 정상 실행에는 실패 파일을 만들지 않고 `result.json`의 상태를 `success`로 남긴다. 새 실행은 새 폴더를 사용한다.

```powershell
uv run --locked python -m unittest discover -s tests -p test_failure_report.py -v
```

실제 MuJoCo 접촉(2초 검출, 마지막 정상 관측 1.998초), 목표 미도달, IK 예산 부족, 후보 접촉, 중단·불완전 기록 및 저장 오류를 검사했다. 전체 회귀검사는 20개가 통과했다. 자동 복구는 추가하지 않았으며, 10단계의 파일 저장 연결 범위는 `move_tcp.py`이며, 12~14단계 실행기에도 실패 기록을 연결했다. JSON 읽는 법과 코드 흐름은 [10단계 문서](docs/STEP_10_FAILURE_REPORT.md)를 참고한다.

작업대·로봇 설치 위치·시험 부품·목표 영역을 설정 파일로 구성하고 3초간 물리 안정화를 검사한다. 기본값은 실측값이 아닌 예시다.

```powershell
uv run --locked python scripts/view_workcell.py --check
uv run --locked python scripts/view_workcell.py
```

개인 설정은 `config/workcell.example.toml`을 Git 제외 파일 `config/local.workcell.toml`로 복사해 수정한 뒤 `--config config/local.workcell.toml`로 지정한다. 이 PC에는 미측정 예시를 복사해 준비했다. 작업대 크기·높이·위치·회전, 로봇 설치 위치·회전·받침 높이, 부품 크기·질량·배치, 목표 위치를 변경할 수 있다. 로봇 베이스와 부품 높이는 작업대 기준으로 계산된다.

주황색은 시험 부품, 초록색은 시각적 목표이며 흰 원점과 빨강/초록/파랑 축은 작업대 좌표계다. 검사 후 뷰어는 정지 화면으로 남고 창을 닫으면 종료된다. 기본 및 위치·높이·회전·받침을 바꾼 설정의 안정화, 잘못된 배치·UR3 모델 혼동 거부를 확인했다. 기존 20개와 새 6개 검사가 통과했다. 11단계 단독 예제는 안정화까지만 확인한다. 기본 배치의 이동·집기·놓기는 아래 12~14단계에서 검증했다. 설정을 바꾸면 재검증이 필요하며 삽입은 후속 단계다. 실행별 설정 사본과 결과는 `runs/step11/`에 저장한다. 설정 항목과 측정 기준은 [11단계 설명서](docs/STEP_11_CONFIGURABLE_WORKCELL.md)에 정리했다.


## 12~14단계: 실제로 집어 옮겨 놓기

현재 작업대에서 주황색 부품을 집어 100 mm 들어 올리고, 초록색 목표에 놓은 뒤 손을 뗀 상태를 판정한다.

```powershell
uv run --locked python scripts/run_pick_place.py --config config/local.workcell.toml
uv run --locked python scripts/run_pick_place.py --check --snapshots --config config/local.workcell.toml
```

새 설치에서 개인 설정이 없으면 `--config`를 생략한다. `--through 12`는 접근·하강까지만, `--through 13`은 집기·상승까지 실행한다. 기본값은 14다. GUI는 완료 후 정지 화면으로 남고 창을 닫으면 종료한다.

`Step 14: SUCCESS`는 실제 접촉으로 파지·운반하고, 놓은 뒤 마지막 0.5초 동안 위치 오차 3 mm 이하·방향 오차 3도 이하·낮은 속도·작업대 지지·손 해제를 확인했다는 뜻이다. 기본 실행에서 들림 간격은 약 99.4 mm, 최종 위치 오차는 약 0.183 mm였다. 실제 부품 위치를 덮어쓰거나 인위적 부착을 사용하지 않는다.

이동·스킬·검증·기록을 별도 파일로 나눴다. 현재 상태에서 Mink IK를 이어서 풀고, 운반 부품을 포함해 사전 경로와 실제 접촉을 검사한다. 막힌 경로를 우회하는 범용 탐색기, 자동 복구, 삽입, 카메라 인식은 아직 없다. 결과·실패 기록·이미지는 `runs/step12_14/<run_id>/`에 저장하며 Git에서 제외한다. 원리·판정 기준·검사 범위는 [12~14단계 설명서](docs/STEP_12_14_PICK_PLACE.md)를 참고한다.

2026-10-28 기준 전체 회귀검사 33개가 통과했다. headless 물리 실행과 offscreen 렌더를 확인했으며, 이번 단계의 대화형 viewer 조작 검사는 수행하지 않았다.
