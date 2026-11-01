# 21D단계: 키 없이 API 연결 경계를 검사하고 실행 준비하기

작성: 2026-11-01. 실제 API 요청 0회, 실물 명령 0회.

## 무엇을 준비했나

`check_sim_readiness.py`는 외부 통신 없이 다음을 확인한다.

- 프로젝트가 고정한 Python 패키지 버전과 실제 설치 버전.
- 실제 MuJoCo 첫 결합 장면 생성과 3초 안정화.
- 실행 데이터가 OneDrive 밖에 있는지.
- LLM 설정 형식, 모델·비용·토큰·호출 한도, 한 번의 최대 요청이 예산 안에 들어가는지.
- 지정한 환경변수에 키가 존재하는지. 키 값은 출력하거나 저장하지 않는다.

`offline_ready`는 시뮬레이션의 기본 실행 준비가 됐다는 뜻이다. 키 유효성, 계정/모델 접근, 현재 가격, 모델의 툴 지원과 판단 성능은 확인하지 않는다. 예시 설정은 비활성이며 실제 모델과 가격을 임의로 정하지 않았다.

## 실제 실행

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/check_sim_readiness.py
uv run --locked python scripts/preview_llm_request.py
uv run --locked python -m unittest discover -s tests -p test_api_readiness.py -v
```

- 진단: `runs/readiness/20261101T100023_535826Z_92abc684`, `offline_ready`. 키 없음·live 설정 미완료를 별도로 기록.
- 요청 미리보기: `runs/step21_llm_preview/20261101T100029_099588Z_44ee5da6`, 실제 3초 안정화 관측, 요청 3,743바이트, API·HTTP 요청 0회, 조립 0개.
- 신규 검사 2개 PASS, 25.526초. 메모리 안의 HTTP 응답을 사용하며 실제 서비스로 연결하지 않는다.

하나는 키 모양의 가짜 문자열을 넣어도 진단기가 네트워크 클라이언트를 만들거나 그 문자열을 결과에 기록하지 않는지 검사한다.

다른 하나는 고정 장애물과 정렬 오차가 있는 **실제 MuJoCo**에서 공식 SDK를 오프라인 응답에 연결한다. 허용된 행동을 고르는 테스트 응답으로 집기 → 삽입 실패 → 재계획 → 정렬 복구 → 삽입 → 완료가 이어지고, 물리 완료 검사들이 통과했다. 재계획 1회, 정렬 복구 1회, 정책 선택 6회다. 테스트 내부의 사용량 장부에 기록되는 6회는 가짜 응답 처리 횟수이며 실제 API 사용·LLM 성능 지표가 아니다.

스킬 절차 요약이 API 입력에 들어가더라도 허용 행동은 그대로 유지된다. 정책은 한 번에 기본 스킬 하나만 제안하며, 코드가 현재 관측·ID·한도·후조건을 확인한다. 공식 [Function calling 문서](https://developers.openai.com/api/docs/guides/function-calling)를 확인했고 strict 스키마와 단일 함수 선택을 유지했다.

## 집에서 할 일과 남은 검증

[실행 안내](START_HERE_NO_API.md)에 키를 마스킹 입력하는 방법, 설정 진단, 정상 결합 → 정렬 복구 → 운반 재계획 순서의 실제 API 명령을 정리했다.

첫 live 연결은 `run_assembly_graph.py`의 단일 LLM·첫 결합 범위다. `run_frame_assembly.py`의 전체 10부품 `single/roles`는 여전히 규칙 정책이다. 전체 프레임 live 정책과 실제 다중 LLM은 작은 live 시험 이후 연결한다. 오프라인 통과를 모델 판단 능력·토큰 절감·멀티에이전트 우위의 근거로 사용하지 않는다.
