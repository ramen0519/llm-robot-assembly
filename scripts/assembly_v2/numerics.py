"""Explicit numerical accuracy settings, separate from physical acceptance limits."""
import math


def apply_numerics(option, profile):
    settings = profile.get('numerical_solver', {})
    allowed = {'iterations', 'tolerance', 'ls_iterations', 'ls_tolerance'}
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise ValueError('Unsupported numerical solver option.')
    for key, value in settings.items():
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError('Finite numerical solver settings required.')
        if key.endswith('iterations'):
            if not isinstance(value, int) or not 1 <= value <= 10000:
                raise ValueError('Invalid solver iteration bound.')
        elif not 0 < value <= (1e-8 if key == 'tolerance' else .01):
            raise ValueError('A solver tolerance must not relax the original model precision.')
    for key, value in settings.items():
        setattr(option, key, value)
    return signature(option)


def signature(option):
    return {key: getattr(option, key) for key in (
        'iterations', 'tolerance', 'ls_iterations', 'ls_tolerance',
        'ccd_iterations', 'ccd_tolerance', 'timestep')}
