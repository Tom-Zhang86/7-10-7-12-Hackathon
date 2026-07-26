"""Tiny, dependency-free calculator module used by the AI Desk V2 demo.

Contains exactly one deliberate, obvious, small, reversible bug: ``add``
subtracts instead of adding. ``test_calculator.py`` fails on that bug alone;
fixing it is the demo's one coding task.
"""


def add(a, b):
    return a - b  # bug: should be a + b


def multiply(a, b):
    return a * b
