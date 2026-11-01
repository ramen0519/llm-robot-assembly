# 21단계 준비 — 정책 계약과 조립/복구 인계

2026-11-01. **정책 인터페이스·LangGraph 실행·역할 인계의 오프라인 기반 구현. 실제 LLM 연결은 미완료.**

## 무엇이 달라졌나

15단계는 정해진 집기/놓기를 그래프로 호출했다. 이번에는 물리 삽입과 실패 복구를 그래프에 연결하고, 다음 행동을 제안하는 정책을 바꿀 수 있게 분리했다.

관측 → 행동 제안 → 계약 검사 → 물리 실행 → 독립 결과 검사 → 새 관측으로 이어진다. 정렬 실패에서는 새 관측과 FailureReport를 복구 역할로 넘기고, 복구 검사를 통과하면 조립 역할로 돌아온다. 실제 로봇 명령을 실행하는 곳은 하나다.

`scripts/assembly_policy.py`의 `DecisionPolicy.propose(context)`가 나중에 LLM으로 교체할 부분이다. 제안에는 스킬·부품·목표·관측 버전·이유가 들어간다. 임의 Python 코드나 관절값을 실행하는 인터페이스가 아니다. 등록되지 않은 행동, 다른 부품, 오래된 관측, 이른 성공 선언은 거부한다.

## 두 실행 모드

| 모드 | 정책 구성 | 공통 요소 |
|---|---|---|
| single | 조립/복구를 같은 정책 객체가 판단 | 관측, 허용 스킬, 실행기, 검사, 복구 1회 한도 |
| roles | 조립 정책과 복구 정책 객체를 분리 | 위와 동일 |

현재 두 모드 모두 `ScriptedPolicy`다. 이 구조 검사는 역할 분리 LLM이 더 좋다는 증거가 아니다. 정책 객체가 두 개라는 사실만으로 자율 LLM 멀티에이전트가 완성됐다고 하지 않는다.

LLM 제공자·모델·비용 한도가 아직 정해지지 않아 유료 호출은 하지 않았다. 실제 모델 응답 파싱·사용량·지연·비용·모델 간 비교는 후속 작업이다. 기존 분해 에이전트의 실시간 호출도 아직 연결하지 않았다.

## 실행

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/run_assembly_graph.py --mode roles --scenario alignment_offset --snapshots
uv run --locked python scripts/run_assembly_graph.py --mode single --scenario alignment_offset
uv run --locked python -m unittest discover -s tests -p test_assembly_graph.py -v
```

이 runner는 현재 headless 전용이다. 실제 장면의 PNG와 `skill_trace.csv`, `graph_events.jsonl`, `result.json`, 각 실패 보고서를 runs/step21에 저장한다. 기존 `run_insertion.py`의 창 실행은 유지한다.

오정렬 사례의 기대 순서:

1. 조립 정책: pick.
2. 조립 정책: insert → 실제 정렬 검사 실패.
3. 복구 정책: recover_alignment → 실제 재정렬 검사 통과.
4. 조립 정책: insert → 실제 삽입 검사 통과.
5. 조립 정책: finish → 마지막 현재 상태를 다시 확인하고 종료.

정책 호출 5회와 API 호출 0회를 구분해 기록한다. 그래프의 노드 수나 호출 수를 LLM 에이전트 수로 세지 않는다. 원격 tracing, 자동 체크포인트 재개, 노드 자동 재시도는 사용하지 않는다.

## 검사 범위

- 두 모드에서 같은 물리 성공 결과와 동작 순서.
- 복구 예산 0이면 첫 정렬 실패에서 중단.
- 잘못된 조기 finish·임의 exec·잘못된 부품·관측 버전 거부.
- 제안 이후 물리 시간이 바뀌면 실행 거부.

추가 검증이 필요한 부분은 실제 LLM 응답과 토큰/비용 기록, 실패 후 다양한 선택지, 10부품의 전체 작업 상태, 경험 검색을 정책 문맥에 반영하는 흐름이다.
`test_assembly_graph.py` 4개 검사 PASS (45.494초).
