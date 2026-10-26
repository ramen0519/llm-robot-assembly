"""Print the Python environment and MuJoCo versions without running a simulation."""

import sys

import mujoco

print(f"Python: {sys.version.split()[0]}")
print(f"Executable: {sys.executable}")
print(f"Virtual environment: {sys.prefix != sys.base_prefix}")
print(f"MuJoCo package: {mujoco.__version__}")
print(f"MuJoCo engine: {mujoco.mj_versionString()}")
print("MuJoCo import: OK")
