# 다부품 LangGraph 실행을 MuJoCo 창에서 보기

작성: 2026-11-01. `run_frame_assembly.py`에 선택 사항인 `--viewer`를 추가했다. 옵션을 생략하면 기존처럼 창 없이 물리 계산과 검증을 수행한다.

```powershell
cd C:\dev\llm-robot-assembly
# 먼저 작은 결합과 실패 복구를 보기
uv run --locked python scripts/run_frame_assembly.py --parts 2 --mode roles --scenario first_alignment_offset --viewer --snapshots
# 전체 10부품 보기
uv run --locked python scripts/run_frame_assembly.py --parts 10 --mode roles --viewer --snapshots
```

보기 창은 같은 물리 상태를 보여준다. 영상용으로 부품을 순간이동시키는 별도 연출이 아니다. 창을 닫으면 실행은 취소로 기록하며, 완료하면 창이 자동으로 닫힌다. 렌더링은 최대 약 30Hz로 갱신하고 물리 검사 주기는 유지한다. 컴퓨터 계산이 느리면 실제 시간보다 느리게 보일 수 있다.

창을 실제로 켠 2부품·오정렬 복구 실행이 success로 완료됐다. 기록: `runs/step23_frame/20261101T054939_272494Z_acab7551`. 바닥판 포함 2부품, 로봇이 설치한 부품 1개다. 역할은 assembly → recovery → assembly로 인계되고 전체 복구 예산 1회를 사용했다. API 호출은 0이다.

`graph_events.jsonl`을 줄별 JSON으로 다시 읽어 기록 형식도 확인했다. 이전 초기 개발 실행 중에는 잘못된 줄 구분이 있었으므로, 새 실행의 유효한 JSONL과 과거 기록을 구분한다.

현재 `roles`는 역할별 규칙 정책이다. 보기 창이 열린다고 실제 LLM 에이전트가 연결된 것은 아니다. 10부품 실행은 여러 번의 느린 접근·삽입·검사를 포함하므로, 기능을 먼저 확인하려면 위의 2부품 명령을 사용한다.
