# 16B단계: 현재 10부품 CAD를 분해 플래너 입력으로 준비하기

작성: 2026-11-01. 외부 프로젝트의 파일은 변경하지 않았으며 분해 탐색이나 API 호출은 실행하지 않았다.

## 구현

`export_planner_input.py`는 현재 넓은 바닥판·중심 파지 덮개 형상의 10부품 CAD를 새 실행 폴더에 내보낸다. 기존 ASAP의 실제 `assets/load.py` 입력 계약을 읽고, 숫자 ID의 OBJ 10개와 각 부품의 완성 위치를 담은 `config.json`을 생성했다.

OBJ 정점과 이동 값의 길이 단위는 모두 미터다. 각 부품을 개별적으로 정규화하지 않는다. 원래 부품 ID와 숫자 ID의 일대일 연결, 파일 지문, 바닥판 ID를 manifest에 남긴다. 분해 순서를 임의로 만들어 성공 결과로 넣지 않는다.

## 좌표 연결

CAD의 로컬 원점과 MuJoCo 물체 중심은 같지 않을 수 있다. 특히 덮개의 손잡이 때문에 전체 경계 상자의 중심이 달라진다. 두 좌표계를 같은 것으로 쓰면 로봇이 잘못된 높이에 놓게 된다.

`world_from_CAD × CAD_from_robot_body = world_from_robot_body` 관계를 부품별로 검사한다. 로봇 물체 중심의 결과 위치가 실제 결합 목표와 일치해야 내보내기가 통과한다.

`binding_template.json`은 단위·ID·좌표 연결의 초안이다. 아직 분해 보고서가 없으므로 `source_report_sha256`은 null이다. 나중에 실제 보고서와 입력 지문을 확인한 뒤 연결해야 하며, 이 초안을 현재 어댑터에 그대로 넣으면 승인된 좌표 연결로 통과하지 않는다.

## 실행과 검증

```powershell
uv run --locked python scripts/export_planner_input.py
uv run --locked python -m unittest discover -s tests -p test_planner_export.py -v
```

실제 출력: `runs/planner_inputs/20261101T054017_590834Z_53056bf4`.

- 10개 부품, 미터 단위, 160mm 바닥판 폭, 각 좌표 변환 일치: 자동 검사 PASS.
- 외부 ASAP의 실제 읽기 함수로 OBJ 10개와 `config.json`을 읽었다.
- 로더가 적용한 정점과 우리가 계산한 완성 좌표가 일치했다. 10개 메시 모두 닫힌 형상이고 양의 부피를 가진다.
- 결과: `external_loader_validation.json`. 검증에 사용한 외부 로더 파일의 SHA-256도 기록했다.
- 로더 검증은 임시 `trimesh` 의존 환경에서 수행했다. 외부 저장소와 기존 Python 환경은 수정하지 않았다.

## 아직 남은 것

입력 형식과 배치가 맞는다는 것은 물리 분해가 가능하다는 뜻이 아니다. ASAP의 길이에 의존하는 물리 설정과 지지 가정을 이 미터 단위 제품에 맞게 확인하고, 실제 완전한 분해 결과를 받아야 한다. 그 역순도 조립 후보이며 로봇 파지·접근·삽입 검사는 별도로 필요하다. 현재 10부품 실행의 순서는 기존 설정을 사용하고 있다.
