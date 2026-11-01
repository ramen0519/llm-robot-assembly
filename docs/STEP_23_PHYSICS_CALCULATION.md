# 다부품 계산 보완: 경로 후보·중력 보상·실제 물리 실행을 구분하기

작성: 2026-11-01. 23단계 다부품 개발 중 발견한 공통 계산 문제를 별도 단위로 수정했다.

## 무엇을 바꿨나

로봇을 움직일 때는 세 가지 계산이 필요하다.

1. 경로 후보: 아직 움직이지 않은 자세에서 부딪힐지 검사한다. 이 계산용 복사본에서는 위치·충돌만 계산하며, 실제로 발생한 힘이라고 기록하지 않는다.
2. 중력 보상: 팔이 무게 때문에 처지지 않도록 기존 위치 제어기에 더할 값을 구한다. 속도를 0으로 둔 별도 계산용 복사본에서 MuJoCo의 강체 역동역학 함수를 사용한다. 실제 로봇의 위치나 속도는 바꾸지 않는다.
3. 실제 실행: 모터 명령을 적용하고 MuJoCo 물리 시간을 진행한다. 여기에서는 접촉력과 움직임을 계산하고 기존의 파지·충돌·속도·부품 상태 검사를 수행한다.

기존에는 1번과 2번도 전체 `mj_forward`를 호출했다. 덮개 시험에서 반복된 `Linesearch objective is not convex` 경고의 호출 경로를 추적해 **중력 보상용 복사본의 접촉 해석**에서 발생했음을 확인했다. 경로 검사 변경만으로 경고가 없어지지는 않았다.

`workcell_motion.py`의 경로 후보는 `mj_fwdPosition`을 사용한다. `move_tcp.py`의 중력 계산은 `mj_kinematics → mj_comPos → mj_comVel → mj_rne`로 필요한 중력 항만 구한다. 실제 물리 실행의 `mj_step`·접촉 해석은 유지했다.

추가로 `simulation_warnings.py`는 실제 실행 중 발생하는 네이티브 경고를 수집해 `SIMULATION_INVALID`로 종료한다. 경고 문자열과 발생 연산을 FailureReport에 남긴다. 콜백 안에서 예외를 던지지 않고 네이티브 함수가 반환된 뒤 중단한다. 기존 콜백은 항상 복원한다. 이 콜백은 프로세스 공용이므로 현재처럼 하나의 직렬 실행기로 사용하며 동일 프로세스에서 여러 물리 스레드를 실행하지 않는다.

## 통과 기준과 검사

```powershell
uv run --locked python -m unittest discover -s tests -p test_gravity_bias.py -v
uv run --locked python -m unittest discover -s tests -p test_simulation_warnings.py -v
uv run --locked python -m unittest discover -s tests -p test_pick_place.py -k current_state -v
uv run --locked python -m unittest discover -s tests -p test_pick_place.py -k carried_part -v
uv run --locked python -m unittest discover -s tests -p test_insertion.py -v
uv run --locked python -m unittest discover -s tests -p test_insertion_recovery.py -v
```

중력 보상값의 기존 전체 계산과의 일치, 실제 상태 불변, 경고 중단과 콜백 복원, 경로 검사 시 현재 상태·운반 부품 보존을 확인한다. 실제 삽입과 리셋 없는 복구의 회귀 검사 결과도 아래에 기록한다.

## 한계

이 변경 자체가 덮개 조립 성공을 뜻하지 않는다. 덮개와 전체 제품의 실제 조립 시험은 별도 기록한다. 중단한 경고 진단 실행은 성공·복구 성공으로 집계하지 않으며 해당 실행 폴더에 중단 이유를 남겼다. 모델 구조나 접촉 허용 조건을 무시하여 계산을 통과시키는 변경은 아니다.

실행 결과: 중력 값 일치/상태 보존 1개 PASS(0.232초), 경고 처리 2개 PASS(0.235초), 현재 상태 경로 검사 1개 PASS(0.940초), 운반 부품 충돌 검사 1개 PASS(7.179초), 물리 삽입 5개 PASS(26.501초), 복구 3개 PASS(29.105초). 잘못 지정한 test_insertion_skill.py 패턴은 0개 검사였으므로 통과 근거로 사용하지 않았고 실제 test_insertion.py를 실행했다.
