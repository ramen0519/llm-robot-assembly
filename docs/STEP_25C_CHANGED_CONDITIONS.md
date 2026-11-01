# 25C단계: 배치·장애물 변경 조건을 선언하고 비교하기

작성: 2026-11-01. 규칙 정책의 실제 MuJoCo 개발 시험이며 LLM 비교가 아니다.

## 바뀐 기능

`development_cases.py`에 초기 장면 조건을 이름으로 선언했다. 부품 트레이의 X 또는 Y를 10mm 옮기거나, 운반 장애물의 X를 10mm 옮긴다. 변환은 장면을 처음 만들 때 적용한다. 실행 중 부품을 순간이동시키거나 결합 위치를 성공한 곳으로 바꾸지 않는다.

`run_assembly_graph.py --scene-case ...`가 이 조건을 읽는다. 변경된 실제 배치와 장애물은 `resolved_config.json`에 들어가므로 명목 장면의 메모리 지문과 일치하지 않는다. `insertion_scene.py`의 기본값은 기존 명목 장면을 유지한다.

`benchmark_variations.py`는 실행 전에 조건·정책 구성·기대 결과·구현 지문을 저장한다. 각 조건에서 single과 roles에 같은 물리 조건·재계획 한도·복구 한도를 적용한다. 두 모드의 조건 지문이 같은지, 전체 실행 중 코드가 바뀌지 않았는지도 확인한다. 실패 실행을 버리거나 성공률에서 빼지 않는다.

## 실행과 실제 결과

```powershell
uv run --locked python scripts/benchmark_variations.py
uv run --locked python scripts/run_assembly_graph.py --mode single --scene-case tray_x_plus_10mm --snapshots
uv run --locked python scripts/run_assembly_graph.py --mode single --scene-case obstacle_x_plus_10mm --transfer-obstacle --transport-replan-budget 1 --scenario alignment_offset --snapshots
uv run --locked python -m unittest discover -s tests -p test_development_cases.py -v
```

전체 기록: `runs/step25_variations/20261101T095931_833301Z_c6fa9743`.

| 조건 | single | roles | 각 실행 sim 초 | 각 정책 호출 | 운반 재계획 / 정렬 복구 |
|---|---|---|---:|---:|---:|
| 트레이 X +10mm | 성공 | 성공 | 73.782 | 3 | 0 / 0 |
| 트레이 Y +10mm | 성공 | 성공 | 73.924 | 3 | 0 / 0 |
| 장애물 X +10mm, 재계획 허용 | 성공 | 성공 | 102.520 | 6 | 1 / 1 |
| 같은 장애물, 재계획 0회 | 운반 전 실패 종료 | 운반 전 실패 종료 | 31.494 | 2 | 0 / 0 |

- 총 8회, 실제 조립 성공 **6/8**. 예상 동작은 **8/8**. 예상대로 중단한 2회도 조립 실패에 포함한다.
- 성공은 실제 삽입 결과의 8개 물리 검사와 설치 수로 확인했다.
- 우회 허용 사례는 `PLANNED_CONTACT`와 `ALIGNMENT_OUT_OF_TOLERANCE`를 기록한 뒤 복구했다. 재계획 0회 사례는 첫 운반의 사전 검사에서 멈췄다.
- API 호출 0회, 메모리 비활성, 실행 중 구현 지문 유지. 각 모드 쌍의 조건 지문 일치.
- 장면·지문 단위 검사 **3개 PASS**, 최종 실행 1.244초. 초기 좌표가 실제 모델에 적용됨, 결합 목표 유지, 명목 메모리와 조건 분리, 장애물 옵션 누락 거부를 확인했다.

## 해석 범위

두 정책은 같은 고정 규칙을 하나 또는 역할별 객체로 실행한다. 그래서 결과가 같으며, 이것은 역할 인계 코드가 물리 동작을 바꾸지 않는다는 개발 기준선이다. 멀티에이전트의 추론 능력이나 비용 절감은 측정하지 않았다.

조건은 개발 중 공개한 사례이고 각 조건당 1회씩 실행했다. 임의 배치 일반화, 독립적인 비공개 평가셋, 통계적으로 충분한 성공률 실험이 아니다. 다른 시험과 CPU를 공유했으므로 wall time을 정책 속도 비교에 쓰지 않는다. 실제 LLM 비교에서는 모델·도구·관측·동결 지식·전체 예산을 맞추고 개발 시험과 별도 평가를 분리해야 한다.
