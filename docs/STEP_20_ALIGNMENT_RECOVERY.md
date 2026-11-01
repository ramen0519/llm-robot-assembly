# 20단계 — 리셋 없는 첫 실패 복구

2026-11-01. **구멍 위 오정렬 한 종류에 대한 결정론적 복구 완료.** 임의 실패를 해결하는 범용 복구나 LLM 복구는 아니다.

## 무엇을 복구하나

기둥을 잘 잡고 있고, 구멍 위에서 XY 정렬만 틀린 경우다. 현재 상태에서 다시 측정해 다음 조건을 만족해야 움직인다.

- 원래 실패가 ALIGNMENT_OUT_OF_TOLERANCE이고 하강 전 정렬 검사에서 멈췄음.
- 현재 관측이 유효하고 양쪽 접촉과 상대 자세로 파지가 확인됨.
- 위치 오차 20 mm 이내, 방향 오차 1도 이내.
- 부품 바닥이 구멍 입구보다 20 mm 이상 높고, 다른 곳의 지지를 받지 않음.
- 부품 속도 1 mm/s 이하.

실패 보고서만 믿고 재전송하지 않는다. 이 조건을 **복구 직전의 실제 시뮬레이션 상태**에서 다시 검사한다.

## 작은 동작으로 나눈 절차

1. 0.25초 자세를 유지하며 파지 확인.
2. 현재 TCP에서 수직으로 10 mm 들어 올리기.
3. 현재 손-기둥 관계를 다시 측정해 정상 구멍 위치 위로 재정렬.
4. 정렬 검사 통과.
5. 새 상태에서 새 경로를 계산해 삽입·해제·후퇴·최종 판정.

`recover_alignment/v1` 성공은 정렬 복구 성공이다. 별도로 삽입 성공 검사를 통과해야 전체 작업이 성공한다. 실행기는 복구를 최대 1번만 허용하며, 부품 놓침/시뮬레이션 이상/다른 충돌은 자동 복구하지 않는다.

## 실행

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/run_insertion.py --headless --snapshots --scenario alignment_offset --recover
uv run --locked python -m unittest discover -s tests -p test_insertion_recovery.py -v
```

`--recover`가 없으면 기존처럼 첫 실패에서 멈춘다.

`scripts/insertion_recovery.py`는 복구 동작이고, `scripts/run_insertion.py`는 1회 예산·실패 이력·최종 결과를 관리한다. 첫 실패는 `failure_001.json`, 복구 전후 관측은 `recovery_001.json`에 보존한다. 최종 success와 최초 실패 기록이 함께 있는 것은 실패를 숨긴 것이 아니라 복구된 episode임을 나타낸다.

## 실제 검증

`test_insertion_recovery.py` 3개 PASS, 27.172초.

- 같은 episode에서 오정렬 실패 → 복구 → 실제 삽입 성공.
- 물리 스텝 사이 live qpos/qvel 강제 변경 없음, 시간 연속 증가.
- 부품 놓침 등 지원하지 않는 실패는 추가 물리 스텝 없이 거부.
- 이전 실패 이후 파지 정보가 실제 상태와 달라졌으면 복구 거부.

이는 한 설정의 기능 검증이다. 반복 성공률·복구 정책 우위·LLM 효과를 입증하는 평가 결과는 아니다. 다음 단계에서 공통 관측과 검사를 사용하는 정책 인터페이스 및 LangGraph 인계를 연결한다.
실제 CLI 실행도 success: `runs/step18/20261101T043858_731712Z_846593f5`. 최초 실패와 복구 관측, 최종 이미지가 보존되어 있다.
