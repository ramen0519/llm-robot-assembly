# 16단계: 분해 결과를 조립 후보로 가져오기

2026-11-01 구현·검증. 기존 분해 프로젝트는 읽기만 했으며 코드 변경, 새 플래너 실행, LLM/API 호출은 없다.

## 목적과 구현

`scripts/disassembly_adapter.py`는 기존 `explicit_action_v1` 계열의 `planner_report.json`을 읽는다. 성공 표지만 신뢰하지 않고 각 단계의 남은 부품, 제거한 부품, 지지 부품, 자세, 실제 경로 검증 근거와 마지막 부품을 다시 검사한다.

완전한 PASS 계획의 제거 순서를 뒤집어 조립 후보를 만들고, 원본 보고서의 SHA-256 및 각 단계의 근거 ID를 보존한다. UNKNOWN/부분 계획, 기한을 넘긴 성공, 중복/불연속 단계, 잘못된 지지·자세는 거부한다.

분해 힘의 방향은 원본 정보로 보존하며 로봇 TCP 이동 명령으로 바꾸지 않는다. 분해에서 바꾼 제품 자세나 가정한 지지는 실제 조립에서 지그·재파지로 가능한지 별도 검증해야 한다.

## 실제 기록 확인

기존 프로젝트의 2026-10-22 저장된 07009 성공 보고서를 읽었다. 원본 분해 순서는 4 → 1 → 2 → 3, 마지막 부품은 0이다. 출력 조립 후보는 **0을 시작 부품으로 두고 3 → 2 → 1 → 4를 추가**하는 순서다.

이는 과거 분해 성공 기록의 읽기·변환 성공이다. 새 분해 계획 생성, 현재 로봇의 조립 성공, 10부품 조립 성공이 아니다.

저장 보고서에는 우리 작업대에 적용할 길이 단위·좌표 연결이 충분하지 않다. 이를 임의로 미터나 밀리미터로 정하지 않고 `source.length_scale_m=null`, `coordinate_binding_validated=false`로 보존했다.

## 실행

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/disassembly_adapter.py --report "C:\dev\disassembly_codex_ready_v2\runs\separation_final_live_20261022\live_cycle\next_episode\planner\planner_report.json"
```

해당 경로는 이 컴퓨터의 비공개 로컬 참고 기록이다. 원본을 Git에 복사하지 않았다. 다른 컴퓨터에서는 자신의 유효한 report 경로를 지정해야 한다.

실제 실행 출력:

```text
CANDIDATE_IMPORTED: seed=0, additions=['3', '2', '1', '4']
Robot execution NOT validated
```

결과: `runs/step16/20261101T042730_447322Z_24ed03e9/`의 `assembly_candidate.json`, `result.json`. 생성물은 Git에서 제외한다.

## 좌표 연결 계약

선택 사항인 `--binding <JSON>`에는 다음 정보를 명시해야 한다.

- `schema_version: 1`과 해당 원본 보고서의 `source_report_sha256`.
- `source_length_scale_m`: 원본 길이 한 단위가 몇 미터인지.
- `source_frame`, `world_from_source_m`: 원본 좌표계 이름과 월드로 변환하는 4×4 강체 변환. 이 변환의 이동 항은 미터다.
- `part_map`: 원본 부품 ID 전체와 우리 부품 ID의 일대일 연결.
- `assembled_part_poses_world_m`: 연결된 각 부품의 완성 상태 목표 자세.

이 계약은 단위/좌표 연결의 형식과 일관성을 검사한다. 원본 CAD의 실제 단위를 자동으로 알아내거나 보정의 물리적 정확도를 입증하지 않는다. 통과해도 `robot_execution_validated`는 **false**다.

## 검사

```powershell
uv run --locked python -m unittest discover -s tests -p test_disassembly_adapter.py -v
```

10개 검사 PASS. 순서 반전·상태 연속성·부분 결과 거부·중복/잘못된 지지·잘못된 자세·단위/ID 연결·이동 항 단위 변환·중복 JSON 키와 비유한 수치 거부를 확인했다. 테스트의 작은 보고서는 합성 계약 fixture이며 물리 성공 근거로 사용하지 않는다.

## 다음

17단계에서 약 10부품 개발용 조립체 후보와 첫 결합 장면을 만든다. 기존 07009를 우리 제품으로 확정한 것이 아니며, 실제 플래너 호출 연동은 후속 검증으로 남긴다.
