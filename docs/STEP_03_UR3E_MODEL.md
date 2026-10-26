# 3단계: UR3e 모델 불러오기

## 이번 단계의 목표

UR3e 로봇팔 모델을 MuJoCo에서 읽고, 저장된 초기 자세를 3D 창에 표시한다. 이번 단계에서는 물리 시간을 진행하지 않는다. 따라서 관절 제어, 중력 아래의 자세 유지, 그리퍼 결합, IK, 조립 동작을 구현하거나 검증한 것은 아니다.

## 실행 방법

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/view_ur3e.py
```

체크무늬 바닥 위에 UR3e가 정지된 자세로 표시된다. 창을 닫으면 종료된다. 수치 확인만 하려면 다음 명령을 사용한다.

```powershell
uv run --locked python scripts/view_ur3e.py --check
```

자동으로 창을 닫는 실행도 확인했다.

```powershell
uv run --locked python scripts/view_ur3e.py --seconds 8
```

여기서 8초는 컴퓨터의 실제 경과 시간이다. 창을 8초 열어 두더라도 시뮬레이션 시간은 0초로 유지된다.

## 모델은 어디서 가져왔나

[SouthColumn76/universal_robots_ur3e](https://github.com/SouthColumn76/universal_robots_ur3e)의 커뮤니티 MJCF 모델을 사용했다. 원본 README는 ROS Industrial의 공개 URDF에서 변환했다고 설명한다. 이 모델을 제조사가 검증한 MuJoCo 모델이나 공식 MuJoCo Menagerie 모델로 표현하지 않는다.

- 가져온 커밋: `5f042ffca6b5885fd18f5448e17b71ab46274fa3`.
- 라이선스: BSD-3-Clause. 원본 [LICENSE.txt](../models/universal_robots_ur3e/LICENSE.txt)를 함께 보관한다.
- 원본 그대로 복사: 로봇 XML, README, 라이선스, OBJ 형상 파일 20개.
- 복사한 원본 23개 파일은 SHA-256 비교로 다운로드한 원본과 내용이 같음을 확인했다.
- 출처와 수정 범위: [PROVENANCE.md](../models/universal_robots_ur3e/PROVENANCE.md).

형상 파일 용량은 합계 약 32.25MiB다. 필요한 파일을 저장소에 포함했으므로, 이 저장소를 받은 뒤 별도로 모델을 다시 다운로드할 필요가 없다. 모델 폴더 안에 또 다른 Git 저장소를 넣지는 않았다.

원본 제작자는 구동기·감쇠 등의 값이 충분히 튜닝되지 않았으며 다른 모델과 결합하면 불안정할 수 있다고 명시한다. 현재는 로딩과 정지 화면만 확인했다. 실제 장비의 기종·치수·보정값과 모델의 일치 여부는 별도 단계에서 확인한다.

## 세 부분의 역할

| 파일/폴더 | 역할 |
|---|---|
| [models/universal_robots_ur3e/ur3e.xml](../models/universal_robots_ur3e/ur3e.xml) | 로봇 링크·관절·물리 속성·구동기·저장 자세 정의 |
| `models/universal_robots_ur3e/assets/` | 로봇 외형을 이루는 20개 OBJ 메시 파일 |
| [scenes/ur3e_preview.xml](../scenes/ur3e_preview.xml) | 로봇 모델을 포함하고 바닥·조명·메시 경로 설정 |
| [scripts/view_ur3e.py](../scripts/view_ur3e.py) | 로딩, 초기 자세 설정, 구조 확인, 정지 뷰어 실행 |

**메시(mesh)**는 작은 면들을 이어 만든 3D 형상이다. **링크(link)**는 로봇의 몸체 부분이고, **관절(joint)**은 링크 사이에서 회전 또는 이동을 허용하는 연결이다. 외형 메시 개수와 관절 개수는 같지 않다. 이번 모델은 외형 파일 20개로 6관절 로봇을 표현한다.

원본 모델은 유지하고 우리 장면 파일에서 연결한다.

```xml
<include file="../models/universal_robots_ur3e/ur3e.xml"/>
<compiler meshdir="../models/universal_robots_ur3e/assets/"/>
```

`include`는 로봇 정의를 장면에 포함한다. `meshdir`는 형상 파일을 찾을 폴더를 지정한다. 첫 로딩에서는 원본과 장면의 폴더가 달라 형상 파일 경로 오류가 났고, 우리 장면에서 경로를 명시해 해결했다.

`.gitattributes`에는 복사한 원본 파일의 줄바꿈을 바꾸지 않는 규칙을 추가했다. 큰 OBJ 파일은 Git의 텍스트 줄 단위 차이 표시를 생략하도록 설정했다. Python 의존성은 추가하지 않았다.

## 초기 자세는 어떻게 표시하나

핵심 코드는 다음 순서다.

```python
model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
data = mujoco.MjData(model)
mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
mujoco.mj_forward(model, data)
```

1. XML과 연결된 형상 파일을 읽어 `model`을 만든다.
2. 관절 각도와 속도 등 현재 상태를 저장할 `data`를 만든다.
3. 원본의 `home` keyframe을 읽어 초기 상태로 설정한다.
4. `mj_forward()`로 그 자세에서 링크 위치 등 필요한 값을 계산한다.

**Keyframe**은 미리 저장한 상태다. 여기의 `home`은 모델 제작자가 저장한 자세의 이름이며, 실물 로봇의 원점 보정이나 홈 복귀 동작을 실행하는 명령이 아니다.

관절 각도는 내부에서 라디안 단위를 사용한다. 사용자가 읽는 출력에서는 `math.degrees()`로 도 단위로 바꿨다. 약 1.5708라디안이 90도다.

| 관절 | 표시한 초기 각도 |
|---|---|
| shoulder_pan_joint | -90도 |
| shoulder_lift_joint | -90도 |
| elbow_joint | +90도 |
| wrist_1_joint | -90도 |
| wrist_2_joint | -90도 |
| wrist_3_joint | 0도 |

뷰어 반복문은 화면을 동기화하는 `viewer.sync()`만 호출한다. 앞 단계의 낙하 코드와 달리 `mj_step()`을 호출하지 않으므로 중력으로 자세가 변하거나 구동기가 로봇을 움직이지 않는다. 이 화면을 자세 유지 제어에 성공한 결과로 해석하면 안 된다.

## 확인 결과

2026-10-26, MuJoCo 3.17.0에서 다음 결과를 확인했다.

```text
Joints: 6, degrees of freedom: 6
Meshes: 20, actuators: 6 (not driven)
UR3e load: OK
Static preview: physics time does not advance.
Preview closed. Simulation time: 0.000 s
```

- XML과 외형 파일 로딩 성공.
- 예상한 이름과 순서의 회전 관절 6개, 위치 좌표 6개, 속도 자유도 6개 확인.
- 초기 각도가 유한한 수인지 확인.
- 정지 렌더링 전후 관절 각도가 같고 시뮬레이션 시간이 0인지 확인.
- 실제 뷰어 창을 8초 제한으로 실행하고 정상 종료 확인.

`actuators: 6`은 원본에 구동기 정의가 있다는 뜻이다. 이번에는 물리 시간을 진행하지 않아 그 제어 성능을 검사하지 않았다. 원본의 `attachment_site`도 그리퍼를 붙이기 위한 기준점이며, 실제 그리퍼의 보정된 TCP로 취급하지 않는다.

실행 확인 이미지는 Git에서 제외된 로컬 `runs/step03/ur3e_preview.png`에 저장했다. 현재 M1 전체가 끝난 것은 아니며, 그중 로봇팔 모델 표시만 완료한 상태다.

## 참고 문서

- [사용한 모델의 고정 버전](https://github.com/SouthColumn76/universal_robots_ur3e/tree/5f042ffca6b5885fd18f5448e17b71ab46274fa3)
- [MuJoCo Python API와 뷰어](https://mujoco.readthedocs.io/en/stable/python.html)
- [MuJoCo XML 모델 형식](https://mujoco.readthedocs.io/en/stable/XMLreference.html)
