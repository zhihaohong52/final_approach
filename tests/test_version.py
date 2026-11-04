from backend.wave.version import ENGINE_VERSION


def test_version():
    assert ENGINE_VERSION == "v4-airlines"
