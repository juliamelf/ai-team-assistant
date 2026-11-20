# tests/test_smoke.py


def test_smoke_one_plus_one():
    # подготовка
    left = 1
    right = 1
    # действие
    result = left + right
    # проверка
    assert result == 2
