"""Record native MuJoCo warnings around a serialized physics operation."""
from contextlib import contextmanager
import mujoco
from failure_report import FailureCode, FailureEvent


@contextmanager
def checked_physics(phase):
    # MuJoCo's callback is process-wide. The simulation executor is single-threaded;
    # do not use this context around concurrent simulator calls in the same process.
    previous = mujoco.get_mju_user_warning()
    messages = []
    count = 0
    def record(message):
        nonlocal count
        count += 1
        if len(messages) < 8:
            messages.append(str(message))
        # Never throw through the native warning callback. Check after returning.
    mujoco.set_mju_user_warning(record)
    try:
        yield
    finally:
        mujoco.set_mju_user_warning(previous)
    if count:
        raise FailureEvent(FailureCode.SIMULATION_INVALID, 'Native simulator warning during ' + phase,
                           kinematics_fresh=False,
                           violations=[{'warning_count': count, 'messages': messages, 'operation': phase}])
