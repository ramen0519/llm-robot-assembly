# 1단계: 프로젝트 전용 Python 환경과 MuJoCo

## 목표와 완료 범위

프로젝트의 `.venv`에서 Python을 실행해 MuJoCo를 불러오고, 패키지와 내부 엔진 버전을 출력한다. 3D 화면이나 물체의 움직임은 다음 단계다. 이번 결과로 그래픽 드라이버, 로봇 모델, 접촉 동작까지 검증된 것은 아니다.

## 도구와 파일의 역할

**Python**은 우리가 작성한 프로그램을 실행한다. **MuJoCo**는 이후 그 프로그램 안에서 물체의 움직임과 접촉을 계산할 라이브러리다. 공식 Python 패키지에 엔진도 함께 들어 있다. **uv**는 사용할 Python을 선택하고 라이브러리를 설치하며 그 환경에서 프로그램을 실행하는 도구다.

`.venv`는 이 프로젝트에서 사용할 Python 실행 파일과 라이브러리가 들어 있는 전용 폴더다. 컴퓨터 전체의 패키지와 분리하므로, 다른 프로젝트에서 다른 MuJoCo 버전을 쓰더라도 따로 관리할 수 있다.

이미 설치된 Python 3.11.15를 활용했다. 이번 프로젝트의 시작 기준은 3.11 계열이다. 이것은 설치되어 있는 Python 3.14에서 MuJoCo가 작동하지 않는다는 뜻이 아니다. 향후 IK 등 새 라이브러리를 추가할 때도 호환성을 확인한다.

| 파일 또는 폴더 | 역할 | Git에 저장 |
|---|---|---|
| `.python-version` | uv가 선택할 Python 계열 `3.11` | 예 |
| `pyproject.toml` | 프로젝트 정보, Python 범위, 직접 필요한 패키지 | 예 |
| `uv.lock` | uv가 계산한 전체 의존성의 정확한 버전과 배포 파일 정보 | 예 |
| `.venv/` | 이 컴퓨터에서 실제 실행하는 전용 환경 | 아니요 |
| `scripts/check_environment.py` | 환경과 MuJoCo 버전을 출력하는 확인 코드 | 예 |

`pyproject.toml`의 주요 설정:

```toml
requires-python = ">=3.11,<3.12"
dependencies = [
    "mujoco==3.17.0",
]
```

`requires-python`은 Python 3.11 계열을 사용한다는 범위다. `==3.17.0`은 MuJoCo를 해당 버전으로 설치한다는 뜻이다. `version = "0.1.0"`은 우리 프로젝트의 초기 버전 표시이며 MuJoCo 버전과는 별개다.

`[tool.uv]`의 `package = false`는 현재 프로젝트 자체를 배포용 Python 패키지로 설치하지 않고 필요한 라이브러리와 스크립트를 실행하는 형태로 시작한다는 설정이다.

`uv.lock`은 uv가 관리하므로 직접 수정하지 않는다. 나중에 라이브러리가 추가되면 `pyproject.toml`과 `uv.lock`을 함께 관리한다. `.venv`를 다른 컴퓨터로 복사하는 대신 설정 파일로 환경을 다시 만든다.

## 설치할 때 실행한 명령

```powershell
cd C:\dev\llm-robot-assembly
uv sync --no-python-downloads
```

`cd`는 작업 폴더를 바꾼다. `uv sync`는 프로젝트 설정을 읽고 `.venv`를 만든 뒤 필요한 라이브러리를 설치한다. Python이 이미 있으므로 이번에는 `--no-python-downloads`를 붙여 새 Python을 다운로드하지 않도록 했다.

MuJoCo와 함께 NumPy, GLFW 같은 보조 패키지도 설치됐다. 이런 패키지를 **의존성**이라고 한다. MuJoCo가 필요로 해서 함께 설치된 것이며, 이번에 별도 기능을 구현한 것은 아니다.

저장소를 받은 다른 컴퓨터에서는 uv가 준비된 상태에서 다음 명령으로 저장된 버전을 기준으로 환경을 만들 수 있다.

```powershell
uv sync --locked
```

`--locked`는 설정과 잠금 파일이 맞지 않으면 임의로 잠금 파일을 바꾸지 않고 오류로 알리도록 한다. Python 3.11이 없으면 uv가 Python도 다운로드할 수 있다. 세부 패치 버전은 설치 환경에 따라 달라질 수 있다.

## 다시 확인하는 방법

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/check_environment.py
```

`uv run`은 프로젝트 전용 환경에서 뒤의 명령을 실행한다. 여기서 `python`은 `.venv`에 있는 Python이다. 별도의 가상환경 활성화 명령은 필요하지 않다.

2026-10-26 실제 확인한 출력:

```text
Python: 3.11.15
Executable: C:\dev\llm-robot-assembly\.venv\Scripts\python.exe
Virtual environment: True
MuJoCo package: 3.17.0
MuJoCo engine: 3.17.0
MuJoCo import: OK
```

실행 경로가 프로젝트의 `.venv`를 가리키고 `Virtual environment: True`가 나오는지 확인한다. 다른 컴퓨터에서는 경로와 Python 패치 버전이 달라질 수 있다.

## 확인 코드 읽기

[확인 코드](../scripts/check_environment.py)는 위에서 아래로 실행되는 짧은 스크립트다.

- `import sys`: 실행 중인 Python의 경로와 버전 등을 확인하는 기본 도구를 불러온다.
- `import mujoco`: 설치된 MuJoCo를 현재 프로그램에서 사용할 수 있도록 불러온다. 실패하면 뒤의 성공 메시지까지 실행되지 않는다.
- `sys.version.split()[0]`: Python 버전 문자열의 첫 항목인 버전 번호를 가져온다.
- `sys.executable`: 지금 실행 중인 Python 파일의 경로다.
- `sys.prefix != sys.base_prefix`: 현재 환경과 원래 Python 설치 위치가 다른지 비교한다. 이번 환경에서는 `True`이므로 가상환경을 사용하고 있다.
- `mujoco.__version__`: Python에서 사용하는 MuJoCo 패키지 버전이다.
- `mujoco.mj_versionString()`: 실제 불러온 내부 MuJoCo 엔진에 버전을 물어본다.
- `print(...)`: 값을 화면에 출력한다. `f"..."` 안의 `{...}`는 계산한 값을 문자열에 넣는 문법이다.

이 스크립트는 환경 정보를 출력하는 도구다. 잘못된 모든 환경을 자동 판정하는 테스트 프로그램은 아니므로 출력된 경로와 버전도 확인한다.

## 추가로 확인한 내용

```powershell
uv pip check --python .venv\Scripts\python.exe
```

설치된 패키지 10개의 의존성 검사가 통과했다. 각 패키지가 요구하는 다른 패키지의 버전 조건이 맞는지 확인한 것이다. 실제 로봇 동작의 성공 여부를 확인하는 검사는 아니다.

기존 `.gitignore`의 `.venv/` 규칙이 적용되는 것도 확인했다. Git에는 설정·잠금 파일·확인 코드·설명서를 저장한다.

## 다음 단계

바닥과 물체 하나로 이루어진 작은 장면을 만들고 MuJoCo 뷰어에서 중력과 충돌을 확인한다. 이번 단계에서는 해당 장면을 구현하거나 실행하지 않았다.

## 참고 문서

- [MuJoCo 공식 Python 설치 안내](https://mujoco.readthedocs.io/en/stable/python.html#installation)
- [MuJoCo 공식 배포 패키지](https://pypi.org/project/mujoco/)
- [uv 프로젝트 관리 안내](https://docs.astral.sh/uv/guides/projects/)
