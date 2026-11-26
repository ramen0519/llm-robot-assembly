# STEP 51 · 누락된 STEP 변환 환경 복구와 단일 파일 입력 확인

2026-11-26. raw CAD 목록의 연결을 확인한 뒤 STEP 입력을 별도로 점검했다.

## 문제와 수정

ASAP/RedMax Python은 정상으로 연결됐지만, STEP 변환용 별도 Python 3.10에서 `ModuleNotFoundError: No module named 'OCP'`가 발생했다. 조립 저장소의 연결용 사본에 `.runtime/ocp`가 없던 문제다. 이 상태를 잘못된 CAD 형상이나 조립 불가능으로 해석하지 않았다.

원 분해 환경의 `F:/disassembly_research/design_v1_20261122/.runtime/ocp`에는 기존 검증본 `cadquery-ocp-novtk 7.9.3.1.1`이 있었다. 이를 조립 전용 `F:/dev/llm-robot-assembly-data/runtime/assets/ocp-7.9.3.1.1`로 복사했다. 396개 파일의 SHA-256이 모두 일치하며 원본·라이선스·배포 메타데이터를 보존했다. 새 다운로드, 기존 Python 패키지 교체, 원 분해 코드 수정은 하지 않았다.

조립용 사본의 `C:/dev/llm-robot-assembly/runtime/integration_disassembly_source/.runtime/ocp`만 해당 F: 복사본을 가리키는 junction으로 만들었다. 링크의 절대 경로와 대상이 각각 위 조립 작업본/조립 데이터 폴더에 속하는지 확인했다. 이 설정은 Git 코드가 아니라 이 컴퓨터의 공통 환경 설정이며 다른 PC에서 다시 준비해야 한다. C:에는 대용량 라이브러리를 복제하지 않았다.

Linux의 기존 Python 3.10.12에서 실제 import 성공을 확인했다. 실행 환경 점검 결과는 `research/20261126_bridge/step_dependency.json`(수정 전 실패)과 `step_dependency_after.json`(수정 후 성공), 파일별 출처는 `ocp_copy_manifest.json`에 보존했다.

## 단일 STEP 입력의 실제 시험

- 입력은 기존 분해 프로젝트의 소형 예제 `examples/desktop_demo/three_parts.step` 복사본이다. 새 독립 평가 자료나 사용자 배터리가 아니다.
- 입력 사본: `F:/dev/llm-robot-assembly-data/research/20261126_bridge/step_fixture/three_parts.step`.
- 원본/사본 SHA-256: `11ae456bba54d0fd2ad2db21e42f66cb54a46257d1ed3f9bad8708ec3245a159`.
- 실행 폴더: `F:/dev/llm-robot-assembly-data/runs/prototype_20261126_step01`.
- 사용자가 단위, 부품별 목표 좌표, 조립 순서, 제품별 코드를 추가하지 않았다. STEP 안의 부품 발생(occurrence)과 완성 배치를 읽었다.
- 파일의 길이 단위는 실제 `millimetre`였으며 3개 부품을 얻었다. 단위를 임의 가정하지 않았다.
- native 분해는 실제 `COMPLETE`, 필요한 제거 2/2, 최종 고정 베이스 `0` 잔여, API 0회.
- 이후 공통 MuJoCo 장면 준비 `prepared_geometry`, 총 **38.859초**.

**이 STEP 예제에서 로봇 파지·설치·최종 안정성은 아직 실행하지 않았다.** STEP 형식 처리와 실제 부품 수준 분해 검증·조립 준비를 확인한 결과다. 같은 날 CAD 목록에서 로봇 2/2 조립·검색을 확인한 STEP 50의 제품과 혼합하여 주장하지 않는다.

```powershell
cd C:\dev\llm-robot-assembly
& .\runtime\luna-venv\Scripts\python.exe -B scripts\run_assembly.py prepare --input 'F:\dev\llm-robot-assembly-data\research\20261126_bridge\step_fixture\three_parts.step' --output 'F:\dev\llm-robot-assembly-data\runs\NEW_STEP_PREPARE' --seconds 240
```

새 출력 폴더를 사용한다. 전달 자료·부품 대응·원본 단위/배치·native 체인은 각각 실행 폴더의 `disassembly_geometry/input/`, `disassembly/export_v1.json`, `prepared.json`에서 확인한다.

실제 Astra 호출은 아직 하지 않았다. 현재 키는 사용자 UI 프로세스의 메모리에서만 유지하는 방식이다. PowerShell 서버 창을 닫으면 해당 UI가 종료되므로 다시 열고 유지해야 한다. 기존 예산을 넘기지 않는 1회 최대 $1.50 / 실행 900초의 별도 Astra 시험을 준비했으며, 키나 서버가 없으면 `not_run`으로 끝내고 예약·호출을 하지 않는다.
