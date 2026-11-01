# 15단계: 기존 물리 동작을 LangGraph에 연결

이번 단계는 **LLM 없는 결정론적 실행 흐름**이다. 기존 UR3e + 2F-85의 한 상자 집기·놓기를 LangGraph가 순서대로 호출한다.

## 흐름

```text
시작 → 안정화 → 관측 → 동작 선택 → 실행 → 측정 결과 확인
                  ↑                         │
                  └──────── 다음 동작 ───────┘

집기와 놓기가 모두 검증됨 → 성공 종료
어느 단계에서든 실패/중단 → FailureReport 저장 → 실패/중단 종료
```

동작 선택은 이번에는 집기 → 놓기 순서를 정하는 Python 코드다. LLM이나 멀티에이전트가 구현된 것이 아니다. 이후 판단 정책을 추가해도 실행과 검증 경계는 유지한다.

## 파일과 역할

- `scripts/pick_place_graph.py`: 작업 상태, 6개 노드, 성공/실패 분기.
- `scripts/run_pick_place.py`: 기존 실행기. `--through 15`일 때 새 그래프를 사용한다.
- `scripts/assembly_skills.py`: 기존 접근·집기·놓기 코드. 이번에 변경하지 않았다.
- `scripts/workcell_verification.py`: 기존 수치 검사. 이번에 변경하지 않았다.
- `tests/test_pick_place_graph.py`: 실제 물리 실행과 잘못된 성공/재실행 방지 검사.

LangGraph는 1.2.19로 고정하고 `uv.lock`에 의존성을 기록했다. Python 3.11, MuJoCo 3.17.0, Mink 1.3.0은 유지한다.

## 상태는 작업 기록장

`PickPlaceState`에 부품·목표 ID, 관측과 관측 번호, 선택 동작과 동작 ID, 마지막 실행·검사 결과, 검증 완료 목록, 실패 보고서를 저장한다. JSON으로 저장할 수 있는 관측·결과 사본이다.

실제 MuJoCo 모델, 물리 상태, 제어기는 그래프 상태에 넣지 않는다. 한 실행이 소유한 `AssemblySkills` 객체를 통해 접근한다.

실행 직전에 현재 상태를 다시 읽고 선택 당시 관측과 같은지 확인한다. 물리 시간이 진행되거나 관절·부품 상태가 달라졌으면 오래된 명령을 실행하지 않고 종료한다. 자동 재관측·재계획은 후속 단계다.

## 노드에서 하는 일

1. `settle`: 기존 코드로 부품을 안정화한다.
2. `observe`: 관절, TCP, 부품, 접촉을 읽고 관측 번호를 증가시킨다.
3. `select_action`: 완료 목록에 따라 집기, 놓기 또는 종료를 선택한다.
4. `execute`: 관측과 허용 동작을 확인한 뒤 기존 스킬을 한 번 호출한다.
5. `verify`: 스킬 결과의 대상과 현재 물리 상태를 검사한다. 통과한 동작만 완료 목록에 추가한다.
6. `finish`: 집기와 놓기가 모두 검증된 경우에만 성공으로 종료한다.

집기 후에는 실제 부품 상승, 양쪽 손가락 접촉과 파지 유지를 확인한다. 놓기 후에는 기존 스킬의 안정 구간 검사에 더해 현재 위치·방향·지지·그리퍼 해제를 확인한다. `"success"` 문자열만으로 다음 작업에 넘어가지 않는다.

실패하면 기존 `FailureReport`를 파일에 저장하고 그래프 상태에도 보존한다. 이후에는 다음 스킬을 실행하거나 물리 시간을 진행하지 않는다. 앞서 성공한 동작은 완료 목록에 남고 실패한 동작은 추가하지 않는다.

## 실행

```powershell
cd C:\dev\llm-robot-assembly
uv sync --locked
uv run --locked python scripts/run_pick_place.py --through 15 --check
```

`--check`는 화면 없이 실제 물리 계산과 검사를 실행한다. 이미지를 함께 저장하려면:

```powershell
uv run --locked python scripts/run_pick_place.py --through 15 --check --snapshots
```

움직이는 MuJoCo 창을 보려면 `--check`를 뺀다. 성공 후 최종 장면을 표시하며 창을 닫으면 종료한다. 이번 검증은 headless와 실제 렌더 이미지 기준이고, 새 창 모드는 별도로 실행 검증하지 않았다.

```powershell
uv run --locked python scripts/run_pick_place.py --through 15
```

정상 실행의 마지막 출력은 `Step 15: SUCCESS`다. 실패/중단은 종료 코드 1이며 `FAILED` 또는 `CANCELLED`로 끝난다.

## 기록

기본 경로는 Git에서 제외된 `runs/step15/<run_id>/`다.

- `resolved_config.json`: 설정과 시뮬레이터·LangGraph 버전.
- `skill_trace.csv`: 기존 물리 실행 측정값.
- `graph_events.jsonl`: 각 노드의 완료 시점, 상태, 선택 동작, 관측 번호.
- `graph_state.json`: 마지막 그래프 상태. 실패 시 보고서도 포함한다.
- `result.json`: 실행 전체 결과와 기록 파일 위치.
- `failure_report.json`: 실패/중단 시에만 저장하는 보고서.
- `*.png`: `--snapshots` 사용 시 실제 물리 상태의 렌더.

설정 로딩 등 그래프 진입 전 실패는 `graph_state.json`에 `phase: runner`를 기록한다. 이 경우 그래프 이벤트 파일이 없을 수 있다.

API 호출과 원격 추적 전송은 없다. LangSmith 추적은 이 호출 범위에서 비활성화한다. 체크포인트·자동 재개·노드 재시도도 사용하지 않는다. 새 실행은 새로운 시험이며 이전 실패의 복구 성공으로 세지 않는다.

## 검증

```powershell
uv run --locked python -m unittest discover -s tests -p test_pick_place_graph.py -v
uv run --locked python -m unittest discover -s tests -v
```

추가 검사는 정상 물리 집기·놓기, 그리퍼 닫힘 고장, 운반 중 파지 손실, 오래된 관측, 물리 동작 없이 반환한 성공의 거부, 사용자 중단, 설정 로딩 실패를 확인한다.

잘못된 성공 응답을 반환하는 대체 코드는 거부 검사에만 사용한다. 정상 성공 검사는 실제 MuJoCo 접촉·마찰과 기존 구동기 제어를 사용한다.

검증 결과: 2026-11-01에 기존 14단계 재확인, 새 그래프의 실제 물리 실행과 렌더, 전체 40개 검사 통과를 확인했다. 기록 `20261101T041802_386993Z_b06e6f42`의 시뮬레이션 시간은 71.976초이고, 놓기 후 안정 구간 최대 위치 오차는 0.1832mm다. 이는 한 상자·기본 설정의 검증이며 전체 조립 성공률이나 실물 정밀도가 아니다.

## 한계와 다음 작업

한 상자와 알려진 위치, `sim_ground_truth` 관측을 사용하는 단계다. LLM 판단, 분해 결과 연동, 삽입, 자동 복구, 장기 경험, 다중 부품 조립, MoveIt 및 실물 연결은 아직 구현하지 않았다.

다음 로드맵 단위는 16단계의 분해 결과 어댑터다. 수요일 시연을 위해 LLM 연결을 앞당기는 경우에도 별도 작은 구현 단위로 진행한다.
