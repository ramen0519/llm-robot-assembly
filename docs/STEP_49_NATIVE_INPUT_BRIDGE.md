# STEP 49 · 완성 CAD에서 실제 분해 검증과 조립 준비까지

2026-11-26. 최신 `codex/general-mesh-v2`의 기존 미커밋 변경을 보존했다. 기준 HEAD는 `cf8add83cb2704c61ab4dc928119fb5276f1d33f`이며 과거 상태로 되돌리지 않았다.

## 문제와 해결

기존 입력 연결은 `wsl -d ASAP-Ubuntu`가 응답하지 않으면 원본 CAD 처리 전에 멈췄다. Linux 자체는 켜져 있었고 별도의 Python 계산도 진행 중이었다. 따라서 WSL 전체 종료나 배포판 재시작은 하지 않았다.

같은 배포판의 시스템 접속을 통해 이미 실행 중인 systemd 환경에 들어가면 기존 `/opt/asap-research/envs/asap/bin/python`과 RedMax를 사용할 수 있음을 실제로 확인했다. 새 `assembly_v2/native_bridge.py`는 먼저 보통 접속을 최대 5초 검사하고, 설정에서 명시적으로 켠 경우에만 이 대체 접속을 사용한다. 해당 PID가 systemd인지, 기존 native 실행 파일이 존재하는지, Windows와 Linux에서 읽은 파일의 SHA-256이 같은지 검사한다. 새 엔진이나 모의 결과로 대체하지 않는다.

이 경로는 Linux root 계정으로 기존 환경에 접속한다. 서비스 설정·설치·다른 작업의 상태를 변경하지 않으며, 이번에 검증한 연구실 `ASAP-Ubuntu` 환경을 위한 공통 설정이다. 다른 컴퓨터에서는 자신의 배포판과 환경을 확인해야 한다. 일반 사용자 접속 자체의 원인은 해결되지 않았고, 프로그램의 입력 처리 경로를 복구했다.

명령별 제한과 전체 제한을 함께 지킨다. 취소/시간 초과 시 자신이 시작한 Windows 호출만 종료하며, native 계산에도 별도 Linux timeout을 둔다. 연결 검사 성공은 분해 물리 검증이나 로봇 조립 성공이 아니다.

## 이번 실제 확인

- `research/20261126_bridge/health01/bridge_health.json`: 보통 접속 5초 timeout, 동일 환경 대체 접속과 RedMax import 성공. API 0회.
- `runs/prototype_20261126_raw_cad01`: 순서 hint가 아닌 `socket_fixture/cad/assembly.json`을 직접 입력했다. 부품 메시·단위 mm·완성 배치만 있는 자료다.
- 새 native 실행 ID: `disassembly-b0c1e749190b40af`. 실제 RedMax 경로 검사 12회, 안정성 검사 2회, 분해 연속 체인 `2 → 1`, 최종 잔여 `0`, 추가 지지 각 0개, `COMPLETE`.
- 기하 기준으로 고정 베이스 `0`과 시험할 후보 순위를 만들었으며 실제 채택은 native 물리 검사 결과를 따랐다. 분해 선택은 Astra가 아니라 `fixture_order`/원래 terminal base 정책이다.
- 완료 체인의 역순으로 `1 → 2`를 조립 후보로 전달했고, 공통 MuJoCo 입력 준비가 `prepared_geometry`로 끝났다. 준비까지 61.453초. API 0회, 비용 $0.
- native JSON의 탐색 중 다른 분기가 아니라 최종 `chain`의 `parent_uid → child_uid`, before/after와 최종 잔여 부품이 이어지는지 확인했다. 조립 입력 검사에서도 같은 계약을 다시 검사한다.
- 회귀 검사 125개 통과(7.016초). PowerShell UI 시작 파일의 구문 오류 0개. 이 검사 수에 실제 Astra 호출은 포함하지 않는다.

이 제품은 앞서 만든 개발용 3부품 fixture다. 새로운 독립 평가 제품이나 배터리 완주 증거가 아니다. 준비만 실행한 위 run에서 로봇은 아직 조립하지 않았다. 전체 실행의 별도 후속 결과는 STEP 50에 기록한다.

## 코드와 실행

- `scripts/assembly_v2/native_bridge.py`: 접속 선택, 실제 환경 확인, 출처 해시, 제한·중단.
- `scripts/assembly_v2/entry.py`: 기존 raw CAD 경로가 새 연결기를 사용하고 `check-bridge` 제공.
- `config/disassembly_bridge.*.json`: 이 컴퓨터에서 실제 확인한 배포판·명시적 대체 접속 설정.
- `scripts/start_assembly_ui.ps1`: 키를 숨김 입력으로 받아 UI 자식 프로세스에만 전달. 디스크·전역 환경·로그에 저장하지 않고 종료 시 부모 환경 복원. `-WithoutApi`로 키 없이 시작 가능.
- `tests/test_v2_native_bridge.py`: 시간 초과·취소·설정 꺼짐·출처 불일치·물리 미검증 구분.

원본 분해 저장소와 기존 CAD/결과는 수정하지 않았다. 수정 전 조립 파일은 `F:/dev/llm-robot-assembly-data/research/20261126_bridge/before/`에 보존했다. E 드라이브와 실물 로봇은 사용하지 않았다.

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'

# 새 출력 폴더를 사용한다. 연결만 검사하며 API/물리 실행은 없다.
& $assemblyPython -B scripts\run_assembly.py check-bridge --output 'F:\dev\llm-robot-assembly-data\research\NEW_BRIDGE_CHECK' --seconds 30

# 제품별 Python이나 조립 순서 파일 없이 원본 CAD 목록에서 준비한다.
& $assemblyPython -B scripts\run_assembly.py prepare --input 'F:\dev\llm-robot-assembly-data\research\20261126_prototype\socket_fixture\cad\assembly.json' --output 'F:\dev\llm-robot-assembly-data\runs\NEW_CAD_PREPARE' --seconds 360

# 키를 저장하지 않고 UI 시작. 접속 주소 http://127.0.0.1:8765
powershell -NoProfile -File .\scripts\start_assembly_ui.ps1 -Port 8765
```

실제 Astra 시험을 위한 API 키는 이번 프로세스·사용자 환경·기존 실행 중 UI에 없었다. 키 연결을 요청했으며 키가 없다는 이유로 scripted 결과를 Astra 결과로 표시하지 않는다. 위 UI 시작 명령 자체는 유료 호출을 하지 않는다. 유료 실행에는 기존 승인 잔액과 실행별 한도를 별도로 지켜야 한다.
