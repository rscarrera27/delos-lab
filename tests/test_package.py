import delos_lab


def test_package_exposes_version() -> None:
    assert delos_lab.__version__ == "0.1.0"
