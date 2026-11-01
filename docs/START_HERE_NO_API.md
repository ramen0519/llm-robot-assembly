# 집에서 이어서 실행하기

작업 폴더: `C:\dev\llm-robot-assembly`. 실행 데이터는 `runs`, 경험·스킬은 `runtime`에 저장되며 Git에서 제외한다. 실물 로봇은 연결하지 않는다.

## API 없이 확인

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/check_sim_readiness.py
uv run --locked python scripts/preview_llm_request.py
```

첫 명령은 설치 패키지와 실제 MuJoCo 장면의 3초 안정화를 검사한다. 두 번째는 현재 관측으로 만들 요청을 저장한다. 둘 다 네트워크 요청이 없고, 물체를 조립하지 않는다. 예시 설정이 비활성이거나 키가 없다는 메시지는 지금 단계에서는 정상이다.

화면으로 작은 조립·실패 복구를 보려면 다음을 실행한다.

```powershell
uv run --locked python scripts/run_frame_assembly.py --parts 2 --mode single --scenario first_alignment_offset --viewer --snapshots
```

전체 10부품과 정책 재계획을 실행하는 명령은 다음과 같다. 실제 물리 시뮬레이션이므로 작은 시연보다 오래 걸린다. `--viewer`를 추가하면 화면이 열린다.

```powershell
uv run --locked python scripts/run_frame_assembly.py --parts 10 --mode single --scenario first_alignment_offset --transfer-obstacle --empty-approach-strategy direct_then_rrt --transport-replan-budget 9 --snapshots
```

`single`은 여기서 **고정 규칙 정책 한 개**다. LLM API가 실행된다는 뜻이 아니다. 바닥판은 미리 고정하며, 로봇 설치 목표는 나머지 9개다. 성공 여부는 `result.json`의 설치 수와 실제 위치·지지·안정 조건으로 판단한다.

## 키 준비 후 첫 LLM 시험

키만 준비하면 계정의 모델 접근 권한과 단가가 자동 결정되는 것은 아니다. 사용할 모델 ID, 확인한 입력·출력 가격, 비용·토큰·호출 한도도 정해야 한다. 임의 모델과 가격을 미리 채워두지 않았다.

1. `config/llm.example.toml`을 `config/local.llm.toml`로 복사한다. 이미 로컬 파일이 있다면 덮어쓰지 않는다.
2. 모델·가격·가격 확인 날짜/출처와 한도를 채우고 `enabled = true`로 변경한다. 처음에는 첫 결합만 시험한다.
3. 같은 PowerShell 창에서 아래처럼 키를 마스킹 입력한다. 키를 채팅이나 저장소 파일에 넣지 않는다.

```powershell
$assemblyApiKey = Read-Host 'OPENAI_API_KEY' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $assemblyApiKey).Password
Remove-Variable assemblyApiKey
uv run --locked python scripts/check_sim_readiness.py --llm-config config/local.llm.toml
```

진단기는 키의 **존재 여부만** 확인한다. 실제 키 유효성·모델 사용 권한·가격·툴 지원은 검증하지 않는다. 아직 과금 요청은 보내지 않는다.

아래부터는 실제 API를 사용하는 명령이다. 이번 API 없는 준비 작업에서는 실행하지 않았다.

```powershell
# 첫 실제 시험: 정상 집기·삽입
uv run --locked python scripts/run_assembly_graph.py --mode single --llm-config config/local.llm.toml --snapshots
# 통과 후: 정렬 실패를 보고 복구 선택
uv run --locked python scripts/run_assembly_graph.py --mode single --scenario alignment_offset --llm-config config/local.llm.toml --snapshots
# 그다음: 막힌 운반 경로 재계획 + 정렬 복구
uv run --locked python scripts/run_assembly_graph.py --mode single --scenario alignment_offset --transfer-obstacle --transport-replan-budget 1 --llm-config config/local.llm.toml --snapshots
```

이 연결은 현재 **첫 결합의 단일 LLM 정책** 범위다. 전체 프레임의 live LLM, 실제 다중 LLM 에이전트와 모델 비교는 이 작은 시험을 통과한 뒤 확장한다. 같은 `single` 옵션이라도 `--llm-config`가 없는 실행은 API 0회다.

LLM은 허용된 스킬 하나를 제안한다. 정확한 관절값·경로·물리 검사는 기존 실행 코드가 담당한다. 요청은 공식 [Function calling 문서](https://developers.openai.com/api/docs/guides/function-calling)의 strict 스키마와 단일 함수 선택을 사용하며, 반환된 제안도 실행기에서 다시 검사한다.

## 결과 읽는 순서

1. `result.json`: 성공/실패, 실제 설치 수, 정책 종류, 재계획·복구 횟수, API 사용량.
2. `graph_events.jsonl`: 어떤 관측에서 어떤 스킬을 골랐는지.
3. `failure_*.json`: 실패 코드·시점·목표·실제값·접촉 정보.
4. `skill_trace.csv`: 시간에 따른 실제 물리 측정값.
5. `resolved_config.json`: 해당 실행의 조건. 동결 지식을 사용하면 `frozen_knowledge.json`도 함께 저장한다.

API 오류나 사용량을 알 수 없는 요청은 무료 성공으로 처리하지 않는다. 이미 집은 부품의 동작을 무조건 재실행하지 않으며 실패 이후에는 현재 상태 검사와 허용된 복구 범위가 우선한다.

전체 최신 결과와 남은 범위는 [현재 상태](CURRENT_STATUS.md), 단계별 세부사항은 각 STEP 문서를 참고한다.
