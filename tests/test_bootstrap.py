def test_project_imports():
    import app


def test_python_version():
    import sys

    assert sys.version_info >= (3, 12)
    assert sys.version_info < (3, 13)
