# STEP 58 · 배터리 6개 설치와 기존 접근 매개변수 재시도

2026-11-26. 실물 명령·유료 API 호출·E 드라이브 사용 없음. 기존 결과와 미커밋 변경을 보존한다.

## 실제 결과

`battery_continuation08-9af65d02`에서 기존 3, 4, 6번을 정확히 복원하고 연속 관찰을 다시 통과한 뒤, 10, 5, 9번을 같은 주 장면에서 실제로 설치했다. 새 3개 모두 해제·후퇴 후 1초/501회 검사를 통과했다. 고정 케이스를 제외한 설치는 6/12개다. 이 50%는 설치 수의 진행률이며, 전체 소프트웨어나 범용성의 완성률이 아니다.

새 3개 중 최대 축별 위치 오차 0.10738mm, 상대 회전 0.02677도, 최종 경질 관통 0.11216mm. 원래 CAD·물성·0.002초 timestep·완료 프로파일을 유지했다. 실제 Astra 호출은 0회이며 연구 경험 등록도 없다.

새 3개는 실행 중 코드 수정 없이 연속 성공했다. 7번은 복사 접근의 IK가 3회 실패해 미뤘다. 8번 복사 검사 중, 검증된 빈 손 상태에서 7번 재시험을 준비하려고 정상 취소했다. 회차 상태는 `cancelled`이며 `physical_success`가 아니다. 실행 3033.609초, 보존 묶음 audit 통과. 취소된 회차의 마지막 전체 관찰을 성공으로 바꾸지 않는다. 각 설치의 실제 501회 성공 근거와 터미널 상태를 다음 실행에서 다시 검증한다.

## 원인과 수정 범위

`source_reach_scan08`은 7번의 실제 실패 상태로 실시한 별도 IK 진단이다. 80mm 접근은 세 후보 모두 실패했고, 60mm에서 두 후보, 40mm에서 세 후보가 수렴했다. 실제 집는 자세와 명목 설치 목표 IK도 세 후보 모두 수렴했다. 이 진단 자체는 충돌이나 실제 조립을 검증하지 않았다.

기존 행동 계약은 이미 접근 거리를 40~120mm로 제공한다. 물리 실행기에 새 기능을 추가하거나 물리 기준을 낮출 사안이라는 증거가 아니다. API 없는 `Scripted` 정책이 기본 80mm만 사용하던 선택을 보완한다.

새 `scripts/assembly_v2/scripted_feedback.py`는 같은 부품·목표·관측에서 복사 접근 IK 실패가 발생했을 때만 이전 파지 pose를 80→60→40mm 접근으로 재검사한다. 이전 revision의 후보 ID는 재사용하지 않고 원래의 전체 파지 pose를 직접 제안한다. 실제 주 장면 실패, 움직인 부품, 다른 목표, 고정 정책 비교, 40mm 하한에서는 자동 재실행하지 않는다. 전체 사전검사·실제 집기·운반·설치·해제·안정성 검사는 유지한다.

Astra 정책·허용 행동 범위·물리 실행기·완료 기준은 변경하지 않았다. 이 개발용 정책의 성공을 Astra가 스스로 복구한 성과로 집계하지 않는다.

## 검증과 다음 실행

관련 코드 검사 190개 통과(5.803초). 실제 7번 접근 거리 수정 검증은 다음 회차에서 진행하며, 코드 검사 통과로 물리 성공을 선언하지 않는다.

```powershell
cd C:\dev\llm-robot-assembly
& .\runtime\luna-venv\Scripts\python.exe -B scripts\run_assembly.py continue-development `
  --from-run 'F:\dev\llm-robot-assembly-data\research\20261126_motion_primitives\battery_continuation08' `
  --output 'F:\dev\llm-robot-assembly-data\research\20261126_motion_primitives\battery_continuation09'
```

출력 경로는 아직 없는 새 이름을 사용해야 한다. 전체 시간 제한 없는 API-free 개발 이어가기이며, 유료 수집·평가에는 적용하지 않는다.

근거: `F:/dev/llm-robot-assembly-data/research/20261126_motion_primitives/battery_continuation08/`의 `result.json`, `events.jsonl`, `checkpoints/`, `audit.json`, `replay_bundle/`, `media/execution.mp4`. 변경 이력은 같은 상위 폴더의 `PROGRESS.md`와 `source_reach_scan08/result.json`.

전체 배터리 완료, 빈 바닥판부터 수정 없이 새 실행, 실제 Astra, 경험 재사용 효과, 별도 제품 일반화는 아직 확인되지 않았다. 현재 도구는 실험용 연장 손가락을 포함한 MuJoCo 모델이며 실물 검증과 구분한다.


## 이어가기 시험 중간 결과

`battery_continuation09-6f13d1b2`에서 6개 설치 상태를 정확히 복원하고 다시 연속 관찰을 통과했다. 그러나 이번 장면에서 선택된 7번 파지는 이전의 몸통 파지와 다른 위쪽 작은 돌출부 파지였다. 80→60→40mm 세 접근 모두 IK 미수렴이었다. 접근 거리를 조정하면 모든 파지가 해결된다고 주장하지 않는다.

`source_reach_scan09`에서는 이 현재 파지의 집는 끝점과 명목 설치 목표 IK는 수렴했지만, 40mm 이상 접근 IK는 수렴하지 않았다. 이전 `source_reach_scan08`의 몸통 파지 결과와 구분해야 한다. 후보 선택·공급 위치·이미 설치된 부품의 파지 간섭 영향을 추가 확인 중이다. 이 시점에 7번 성공은 없고, 8번 복사 검사로 진행 중이다. 유료 API 호출은 계속 0회다.
