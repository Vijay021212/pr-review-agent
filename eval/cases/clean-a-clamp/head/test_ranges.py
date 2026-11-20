import pytest

from ranges import clamp


def test_clamp_keeps_values_inside() -> None:
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10


def test_clamp_needs_a_valid_range() -> None:
    with pytest.raises(ValueError):
        clamp(1, 10, 0)
