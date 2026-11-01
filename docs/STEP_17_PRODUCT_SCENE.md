# 17단계 — 10부품 개발 후보와 첫 결합 장면

2026-11-01. **제품 후보·CAD 생성·첫 결합 장면 검사 완료.** 최종 제품 확정이나 10부품 로봇 조립 완료라는 뜻은 아니다.

## 이번에 만든 것

사각 기둥 4개를 받침대에 꽂고, 각 기둥에 슬리브를 끼운 뒤 위 덮개를 얹는 프레임이다. 받침대 1 + 기둥 4 + 슬리브 4 + 덮개 1 = 제품 부품 10개다. 받침대는 미리 고정하므로 로봇이 설치해야 할 부품은 9개다. 덮개의 손잡이는 덮개에 포함된 형상이다.

지금 실행 장면에는 고정 받침대와 자유롭게 움직이는 첫 기둥만 들어 있다. 받침대의 고정은 조립 중 부품을 인위적으로 붙이는 기능이 아니라 처음부터 선언한 고정 치구 조건이다. 실제 치구 제작/강성/설치 가능성은 아직 검증하지 않았다.

| 치수 | 예시 값 |
|---|---:|
| 받침대 | 120 × 120 × 20 mm |
| 기둥 | 20 × 20 × 60 mm |
| 사각 구멍 폭 | 24 mm |
| 구멍 바닥 두께 / 삽입 깊이 | 4 mm / 16 mm |
| 한쪽 여유 간격 | 2 mm |
| 슬리브 | 바깥 36 mm, 안쪽 24 mm, 높이 30 mm |

2 mm 여유는 처음 물리 동작을 확인하기 위한 넉넉한 개발 공차다. 정밀 압입·나사 체결을 구현했다고 표현하면 안 된다. 최종 공차와 난이도는 실패/복구를 측정하며 조절한다.

## 파일과 역할

- `scripts/frame_product.py`: 치수와 부품 구성, 결합 선후 조건, 10개 OBJ 및 manifest 생성. OBJ 자체는 단위를 보장하지 않으므로 manifest에 metre와 각 부품의 완성 자세를 명시한다.
- `scripts/insertion_scene.py`: 같은 치수로 MuJoCo 첫 결합 장면 생성.
- `scripts/workcell_scene.py`: 기존 장면 생성 중 편집 가능한 spec 생성과 compile을 분리. 기존 한 부품 장면 동작은 유지.
- `scripts/view_insertion_scene.py`: 3초 안정화 후 이미지·관측·설정 보고서 생성.
- `tests/test_insertion_scene.py`: CAD와 실제 물리 접촉 검사.

구멍이 있는 OBJ 전체를 충돌용 convex mesh 하나로 쓰면 구멍이 막힐 수 있다. 여기서는 여러 개의 서로 겹치지 않는 상자로 받침대 벽과 바닥을 구성한다. 같은 상자를 합쳐 CAD를 생성하므로 물리 모델과 CAD의 명목 형상이 일치한다.

CAD 연산은 공개 라이브러리 [Manifold](https://github.com/elalish/manifold) / [manifold3d](https://pypi.org/project/manifold3d/) 3.3.2를 사용한다. 라이브러리 라이선스는 Apache-2.0이며 upstream 소스를 복사/변경하지 않았다. 프로젝트의 형상 생성 코드는 별도로 작성했다.

## 실행

```powershell
cd C:\dev\llm-robot-assembly
uv sync --locked
uv run --locked python scripts/frame_product.py --output runs/step17/my_cad
uv run --locked python scripts/view_insertion_scene.py
uv run --locked python -m unittest discover -s tests -p test_insertion_scene.py -v
```

CAD 출력 폴더는 비어 있어야 한다. 기존 결과를 덮어쓰지 않는다. 생성 OBJ·이미지·실험 기록은 Git에서 제외된 runs에 보관한다.

## 실제 확인 결과

- 새 검사 5개 PASS, 기존 작업대 회귀 검사 6개 PASS.
- 10개 CAD가 각각 연결된 유효한 고체이며 완성 자세에서 서로 체적 간섭하지 않음.
- 중앙에서 낙하시킨 기둥은 구멍 바닥까지 내려감.
- 구멍을 벗어난 기둥은 받침대 윗면에서 막힘.
- 장면 3초 안정화 및 이미지 생성: `runs/step17/20261101T043300_642043Z_dd0752ef`.
- CAD export: `runs/step17/cad_v2`.

낙하 검사는 독립적인 초기 조건에서 구멍의 물리를 검사한 것으로, 로봇이 조립한 성공 횟수에 넣지 않는다.

## 남은 것

다음 18단계는 로봇이 실제로 집고 운반해서 이 구멍에 넣는 동작이다. 슬리브·덮개의 파지와 다중 결합은 아직 검증하지 않았다. 이 CAD를 외부 분해 플래너에 실제 입력한 것도 아니다. MoveIt/OMPL 전역 우회 경로 연동은 미구현이며 현재 Mink 기반 지역 경로 검사 범위는 유지한다.
