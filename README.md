# LLM Robot Assembly

MuJoCo · UR3e 한 대 · Robotiq 2F-85를 사용하는 CAD 입력 기반 조립 에이전트다. 실행 기준은 [v1.2 설계](docs/design_v12/ASSEMBLY_MASTER_DESIGN_v1.2.md)다.

**2026-11-24 초기화:** 구버전 제품 전용 실행기, 경험/교훈/생성 코드 스킬, 과거 run·영상·동결 평가 자료를 정리했다. 새 경험 저장소는 `runtime/memory/assembly_v12`이며 이전 사례를 자동으로 가져오지 않는다. [삭제 범위와 검증](docs/STEP_28_CLEAN_START.md).

## 현재 흐름

```text
완성 목표가 포함된 CAD / 검증된 분해 export
→ 입력·단위·원본·연속 성공 체인 검사
→ 지원 형상의 결합 조건·초기 배치·작업대 생성
→ 단일 역할 또는 조립/복구 역할의 공유 이력 LangGraph
→ 파지 제안 → 복사 검사 → 실제 MuJoCo 동작·설치 검증
→ 실패 후 과거 해결지침 검색 / 허용 복구 / 중단
→ 결과·영상 → 실제 해결 사건의 등록 검토
```

지원 범위는 사각 소켓·기둥·관통 슬리브·파지 탭이 있는 덮개의 수직 삽입이다. 임의 CAD, 나사·스냅핏·재파지·급품·실물 로봇은 지원 완료 범위가 아니다. 실제 분해 성공은 로봇 조립 성공과 별도로 검사한다.

## 시작하기

이 컴퓨터에서는 `ASAP-Ubuntu` WSL과 `runtime/linux-venv/bin/python`을 사용한다. 로봇 모델·환경·분해 모듈 연결·원본 CAD는 정리 과정에서 유지했다. 다른 컴퓨터는 `pyproject.toml`, `uv.lock`의 환경과 `config/disassembly_bridge.wsl.example.json`의 경로를 먼저 준비한다.

```powershell
cd C:\dev\llm-robot-assembly
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B scripts/run_assembly.py memory-status
```

새 제품 실행(API 없이 규칙 정책으로 물리 연결 확인):

```powershell
.\scripts\run_assembly.ps1 -InputPath 'C:\path\to\product.json' -Policy scripted -Condition S0 -Seconds 300
```

실제 Astra high는 승인된 범위와 키가 준비된 경우 `-Policy astra -LlmConfig 'C:\dev\llm-robot-assembly\config\llm.astra-high.toml'`을 사용한다. 키는 지정된 환경변수로 전달하고 파일이나 로그에 값을 기록하지 않는다. 설정 파일의 상한은 새 유료 실행의 승인을 대신하지 않는다.

## 남긴 코드

- `scripts/assembly_v12/`: 현재 입력·자동 준비·공유 정책·경험·미디어·수집/평가.
- `scripts/run_assembly.py`, `run_assembly.ps1`: 현재 사용자 진입점.
- `scripts/*motion*`, `solve_tcp_ik.py`, `joint_path_planner.py`, `*verification.py`, `frame_assembly_skills.py`, `insertion_skills.py` 등: 현재 실행기가 사용하는 공통 물리 코드.
- `frame_product.py`, `make_v12_development_inputs.py`: 명시된 개발용 CAD 생성기. 입력 제품을 대신 등록하거나 경험을 미리 넣지 않는다.
- `models/`, `third_party/`: 로봇·그리퍼·모션 플래너 원본과 라이선스.

옛 파일명을 가진 공통 물리 함수도 현재 의존성을 확인해 남겼다. 구버전 LangGraph, 제품 전용 runner, 생성 Python 스킬 VM과 이전 경험 검색기는 제거했다. 과거 소스 이력은 Git의 `3959448`에서 확인할 수 있다. 삭제한 로컬 영상·실행 로그는 Git에 저장되어 있지 않았다.

## 문서와 검사

- [실행 방법·메모리·수집과 평가](docs/INTEGRATED_ASSEMBLY_V12.md)
- [현재 개발 계획](docs/PROJECT_PLAN.md)
- [초기화 범위와 실제 검증](docs/STEP_28_CLEAN_START.md)
- [설계 원문](docs/design_v12/ASSEMBLY_MASTER_DESIGN_v1.2.md) · [구현 작업서 원문](docs/design_v12/ASSEMBLY_CODEX_HANDOFF_v1.2.md)

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B -m unittest discover -s tests -v
```

검사 중의 합성 경험은 임시 디렉토리에서만 사용한다. 검증용 scripted run과 실제 Astra 경험 수집은 출처·split을 구분하며 성공·실패를 물리 판정과 분리해 기록한다.
