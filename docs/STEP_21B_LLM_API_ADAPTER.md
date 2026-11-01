# 21B단계: 단일 LLM 정책의 API 연결부와 사용량 기록

작성: 2026-11-01. **연결 코드를 구현하고 오프라인 계약 검사·관련 물리 회귀 검사를 통과했다. 실제 OpenAI 서버에 모델 요청은 보내지 않았다.** 모델·과금 한도 선택과 프로젝트 API 키가 준비되면 같은 실행기에서 라이브 시험을 할 수 있는 상태다. 라이브 서버의 모델 지원·계정 접근·출력 품질은 아직 미검증이다.

## 이번에 무엇이 달라졌나

기존 `ScriptedPolicy`는 허용 목록의 첫 동작을 코드로 골랐다. 새 `OpenAIAssemblyPolicy`는 같은 입출력 자리에 연결되지만, 다음 스킬 제안을 Responses API에서 받는다. IK·경로 계산·모터 제어·성공 판정은 기존 코드가 그대로 담당한다.

예를 들어 LLM의 출력은 아래와 같은 **작업 제안 데이터**다. 새로운 Python 실행 코드를 받지 않는다.

```json
{
  "skill": "recover_alignment",
  "part_id": "post_1",
  "target_id": "socket_1",
  "observation_revision": 3,
  "reason": "정렬 실패가 측정됐고 현재 복구 예산이 남아 있다."
}
```

이 예시는 설명용이다. 실제 모델이 이 내용을 출력했다는 뜻은 아니다. `observation_revision`은 몇 번째 관측에 근거한 제안인지 나타내며, 현재 관측과 다르면 실행하지 않는다.

실행 흐름은 다음과 같다.

1. MuJoCo에서 현재 부품·TCP·목표·실패 측정값을 읽는다.
2. 현재 허용 스킬·검증된 작업 이력·경험 요약과 함께 요청을 만든다.
3. 공식 입력 토큰 계산 endpoint로 요청 크기를 확인한다.
4. 총 호출·토큰·비용 예산을 확인하고 모델에게 함수 제안 1개를 요청한다.
5. 응답 완료 여부·사용량·함수 이름·JSON 필드·ID·관측 revision·허용 스킬을 검사한다.
6. LangGraph가 물리 실행 직전 관측을 다시 대조하고 기존 스킬을 실행한다.
7. 기존 물리 검증기가 성공·실패를 판정하고 다음 관측으로 돌아간다.

첫 라이브 연결은 단일 정책만 지원한다. `roles`는 계속 규칙 정책 시험용이며 실제 멀티에이전트 LLM 실행은 후속이다. 현재는 동기식 호출이므로 API를 기다리는 동안 시뮬레이션 물리 시간이 진행되지 않는다. 실물 제어 방식으로 검증한 구조는 아니다.

## 요청에 무엇을 넣나

현재 부품과 목표의 좌표·방향, TCP, 결합 정의, 검증된 동작 이력, 실패 코드와 측정된 위반 조건, 남은 복구 예산, 최대 3개 경험 요약을 넣는다. 마지막 검증 결과도 함께 전달한다. 원본 관절 배열·전체 접촉 로그·중복된 과거 관측·로컬 경로를 매번 붙이지 않는다.

입력은 명시적으로 선택한 필드로 구성하며, 32KiB를 넘으면 조용히 잘라내지 않고 중단한다. 첫 실제 관측에서 생성한 전체 요청 파일은 약 3.5KiB였다. 바이트 크기와 API 토큰 수는 다른 단위다. 실제 토큰 수는 라이브 시험의 count endpoint 결과로 기록한다.

모델에는 `propose_assembly_action`이라는 함수 하나만 제공한다. 함수 스키마에 현재 스킬·부품 ID·목표 ID·revision을 제한하고, 응답 뒤에도 로컬 검증을 수행한다. 공식 [함수 호출 문서](https://developers.openai.com/api/docs/guides/function-calling)의 `strict`, 함수 지정, 병렬 호출 비활성화 형식을 사용했다. 함수 제안 수신이 로봇 명령 실행을 뜻하지 않는다.

## 실패와 비용을 어떻게 기록하나

- SDK 자동 재시도는 0회다. 네트워크 오류 뒤 같은 요청이나 물리 동작을 자동 재전송하지 않는다. 명시적 timeout을 설정한다. [공식 Python SDK 문서](https://developers.openai.com/api/reference/python)
- 모델이 거부하거나 출력이 잘리거나 잘못된 행동을 제안해도 응답의 사용량은 집계한다. 유효한 제안이 없으면 로봇 동작으로 넘어가지 않는다.
- timeout 또는 사용량 누락은 `null`로 기록하고, 해당 요청에 미리 잡아둔 예산을 유지한다. 0토큰·0원으로 처리하지 않는다.
- `api_calls`는 모델 생성 요청 수, `http_requests`는 입력 토큰 계산 요청까지 포함한 수다. 정책 함수 호출 수와도 구분한다.
- `output_tokens`에 포함된 추론 토큰을 다시 더하지 않는다. 캐시 입력·추론 토큰은 별도 보조 지표로 남긴다. [공식 토큰 계산 문서](https://developers.openai.com/api/docs/guides/token-counting)
- 비용은 설정에 기록한 **비할인 입력 단가와 출력 단가의 보수적 추정값**이다. 캐시 할인은 적용하지 않으며 실제 청구 금액이라고 표시하지 않는다. 별도 캐시 쓰기 비용처럼 지원하지 않는 사용량이 오면 미확인으로 중단한다.
- 한 episode 안에서 호출·총 토큰·추정 비용 한도를 공유한다. 요청 전 입력·출력 최대치 전체가 남은 예산에 들어갈 때만 진행한다. 가격 정보가 정확하다는 전제가 필요하며 계정 전체의 다른 작업 비용까지 통제하는 기능은 아니다.
- 재개 시 새 예산으로 같은 journal을 덮어쓰지 않는다. 중단된 API 요청의 자동 재개·정산 기능은 아직 없다.

새 실패 코드: `POLICY_REQUEST_FAILED`, `POLICY_RESPONSE_INVALID`, `POLICY_USAGE_UNKNOWN`, `POLICY_BUDGET_EXCEEDED`. 같은 `FailureReport`로 현재 물리 상태와 함께 기록한다.

## 파일과 실행 방법

| 파일 | 역할 |
|---|---|
| `scripts/llm_runtime.py` | 설정 검사, SDK 생성, 호출·사용량·예산 기록 |
| `scripts/openai_policy.py` | 관측 정리, 함수 요청 생성, 응답을 기존 행동 계약으로 변환 |
| `scripts/run_assembly_graph.py` | 기존 규칙 실행 또는 명시적으로 선택한 단일 API 정책 실행 |
| `scripts/preview_llm_request.py` | 실제 초기 관측에서 전송 전 요청 파일 생성; API 호출 없음 |
| `config/llm.example.toml` | 비활성 예시 설정. 모델·가격·예산은 미정 |
| `tests/test_openai_policy.py` | 메모리 안의 HTTP 응답으로 공식 SDK와 실행 경계를 검사 |

공식 OpenAI Python SDK `3.42.0`을 추가하고 버전을 잠갔다. 다른 제공자를 쓰려면 같은 `DecisionPolicy.propose(context)` 계약의 어댑터를 추가할 수 있다.

먼저 API 없이 요청을 확인한다.

```powershell
cd C:\dev\llm-robot-assembly
uv sync --locked
uv run --locked python scripts/preview_llm_request.py
uv run --locked python -m unittest discover -s tests -p test_openai_policy.py -v
```

라이브 실행은 아래 준비가 된 뒤 사용한다.

1. `config/llm.example.toml`을 Git 제외 파일 `config/local.llm.toml`로 복사한다.
2. 사용할 모델, 확인한 가격과 확인일, 명시적 총비용·호출·토큰 한도를 채운다. 모델이 지원하는 추론 설정을 확인하고 `enabled = true`로 바꾼다.
3. 현재 실행 환경에 `OPENAI_API_KEY`를 설정한다. 키 값은 설정 TOML·Git·채팅·로그에 넣지 않는다. 이 코드는 다른 프로젝트의 키 파일을 자동 탐색하지 않는다.
4. 다음 명령으로 작은 첫 결합부터 검증한다.

```powershell
uv run --locked python scripts/run_assembly_graph.py --mode single --scenario alignment_offset --llm-config config/local.llm.toml --snapshots
```

이 명령은 준비 완료 후의 실행 방법이며, 이번 단계에서는 라이브 성공을 확인한 명령이 아니다. `--llm-config`를 생략하면 기존 규칙 정책으로 동작한다. 비활성 예시 파일을 직접 넣으면 API·물리 실행 전에 거부한다.

출력은 `runs/step21/<run_id>` 아래에 있다. `llm_events.jsonl`은 요청 원문과 지문, API 요청/응답 ID, 사용량, 검증한 제안, 오류 종류를 기록한다. 인증 헤더·키·SDK 오류 원문은 기록하지 않는다. `result.json`의 `llm_usage`가 최종 집계이며 기존 `graph_events.jsonl`, 물리 CSV, FailureReport도 유지한다.

## 실제 검증 결과

- 새 연결부 검사 **17개 PASS**, 18.360초. 공식 SDK는 실제로 실행하되 HTTP는 메모리 안의 가짜 응답으로 대체해 외부 통신을 차단했다.
- 기존 그래프·경험·문맥 전달·지표·실패 보고·절차 등록 관련 검사 **26개 PASS**, 46.187초.
- 요청 미리보기: `runs/step21_llm_preview/20261101T065028_937506Z_3e45f209`. 실제 MuJoCo 3초 안정화 관측, 모델 응답 없음, 조립 0개, API 0회.
- 비활성 설정의 실제 실행기 거부: `runs/step21_llm_setup_check/20261101T065413_471914Z_82083242`. 물리 장면 실행 전 중단, API 0회.
- 기존 복구 절차의 현재 코드 재검증: `runs/step22_recipes/20261101T065300_766292Z_e998536d`, 실제 MuJoCo 복구·삽입 success. 현재 호환 등록본 1개.
- 설치 패키지 53개 호환성 검사 PASS.

새 검사에는 가짜 API가 정상 제안을 돌려줄 때 **실제 MuJoCo**의 복구·삽입까지 연결되는 경우와, 잘못된 제안이면 실제 집기 전에 멈추는 경우가 포함된다. 이는 연결 코드와 실행 경계의 검사이며 LLM 판단 성능·라이브 API 조립 성공으로 집계하지 않는다. 기존 10부품 전체 실행을 이 변경 뒤 다시 돌린 것은 아니다.

## 다음 완료 기준

사용자가 선택한 제공자·모델·비용 한도와 키로 실제 응답을 받아 첫 결합을 실행하고, 요청·관측·사용량·물리 결과를 하나의 기록에서 확인한다. 그다음 검증된 대체 파지·접근·복구 후보를 늘려 선택 성능을 비교한다. 현재 정상 단계는 선택지가 대부분 하나이므로, API 연결만으로 LLM의 유용성이나 멀티에이전트 우위를 입증하지 않는다.
