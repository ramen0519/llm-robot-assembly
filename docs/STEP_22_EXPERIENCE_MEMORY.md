# 22a/b — 실행 경험 저장·검색과 정책 문맥 전달

2026-11-01. **닫힌 JSON 경험 저장, 조건 일치 검색, 그래프 문맥 전달 구현.** 학습된 새 스킬 자동 생성·승격과 메모리 성능 개선 입증은 아직 아니다.

## 스킬과 메모리는 무엇이 다른가

- 스킬: `pick/v1`, `insert_post/v1`, `recover_alignment/v1`처럼 실행 가능한 코드와 전제조건·결과 검사.
- 메모리: “이 설정에서 정렬 오차가 났고, 재관측·재정렬 스킬 후 삽입 검사를 통과했다”라는 과거 사건 기록.

한 번 성공한 자연어 문장을 실행 코드로 등록하지 않는다. 메모리는 후보 판단에 참고하는 데이터이며 현재 관측/접촉 검사/복구 한도를 우회하지 않는다.

## 저장하는 내용

실제 run의 결과와 설정을 읽어 작업 ID, 시뮬레이션 구분, 조건 지문, 성공/실패, 실패 코드, 검증된 스킬 결과, 복구 스킬, 원본 파일 경로·SHA-256을 저장한다. SHA-256은 파일 내용의 지문이며 파일이 달라졌는지 식별한다.

성공 기록은 첫 삽입에 필요한 8개 검사와 위치/깊이 수치가 있어야 저장된다. 이는 저장된 검증 기록의 계약 검사이며 재시뮬레이션이나 외부 데이터의 진실성 보증은 아니다.

기본 폴더는 `C:\dev\llm-robot-assembly\runtime\memory\simulation`이다. OneDrive 밖이고 Git에서 제외된다. 살아 있는 SQLite DB 대신 완성된 JSON 파일을 원자적으로 교체한다. 같은 경험의 재저장은 중복 생성하지 않는다.

모든 현재 경험은 development 구분이다. 향후 성능 평가는 별도 시험셋을 고정해야 하며, 시험 결과를 기억에 넣은 뒤 같은 시험을 반복해 일반화 성능이라고 하면 안 된다.

## 검색과 실행

현재는 임베딩 검색보다 명시적인 조건 일치를 먼저 사용한다. 작업대/로봇/부품/결합 정보/시뮬레이터 버전의 지문이 같은 기록만 가져오고, 실패 코드로 좁힌다. 성공한 복구를 먼저 보여주되 실패 사례도 남긴다.

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/experience_memory.py save --run-dir runs/step21/20261101T044217_632132Z_0f502680
uv run --locked python scripts/experience_memory.py save --run-dir runs/step18/20261101T043637_317870Z_01380fe9
uv run --locked python scripts/experience_memory.py search --run-dir runs/step21/20261101T044217_632132Z_0f502680 --failure-code ALIGNMENT_OUT_OF_TOLERANCE
uv run --locked python scripts/run_assembly_graph.py --mode roles --scenario alignment_offset --memory
```

그래프는 새 관측 때 최대 3개 기록의 짧은 요약을 정책 문맥에 넣는다. `graph_events.jsonl`의 `experience_ids`로 실제 검색/전달된 기록을 확인한다. 현재 규칙 정책은 이 정보를 사용해 다른 행동을 선택하지 않으므로, 메모리 유무에 따른 개선이 있었다고 주장하지 않는다.

## 검증

- 저장/중복 방지/검색/다른 조건 배제/잘못된 성공 및 수정된 기록 거부 검사 5개 PASS.
- 메모리가 정책 문맥과 이벤트로 전달되되 움직임을 자동 허가하지 않는 검사 1개 PASS.
- 실제 복구 성공 기록과 실패 기록을 저장하고 동일 조건 검색에서 둘 다 확인.

현재 기록에는 당시 코드 커밋 ID가 없으므로 `source_implementation_revision: null`로 표시한다. 코드 버전까지 일치하는 검증이나 완전한 재현성 보증으로 표현하지 않는다.

## 남은 것

후속 [22C단계](STEP_22C_SKILL_RECIPES.md)에서 기록된 복구 순서로 JSON 절차 후보를 만들고, 새 MuJoCo 실행으로 검증한 뒤 조건을 제한해 등록했다. 실제 LLM이 경험을 읽고 선택을 바꾸는 실험, 의미 기반 유사도 검색, 새로운 복구 전략 생성, 실제 API 사용량 기록은 아직 후속 작업이다. 이미 검증된 코드와 아직 검증하지 않은 제안을 구분하는 원칙은 유지한다.
메모리 연결 실제 물리 실행도 success: `runs/step21/20261101T044438_478143Z_c7edee85`.
