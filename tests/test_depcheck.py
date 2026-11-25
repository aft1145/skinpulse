from skinpulse.depcheck import deps_outdated

LOCK = '''version = 1
[[package]]
name = "skinpulse"
version = "0.1.0"
[[package]]
name = "httpx"
version = "0.28.1"
[[package]]
name = "Typing_Extensions"
version = "4.12.2"
[[package]]
name = "colorama"
version = "0.4.6"
'''
PROJ = '''[project]
name = "skinpulse"
dependencies = ["httpx>=0.27", "uvicorn[standard]>=0.30"]
'''


def _root(tmp_path, lock=LOCK, proj=PROJ):
    (tmp_path / "uv.lock").write_text(lock)
    (tmp_path / "pyproject.toml").write_text(proj)
    return tmp_path


def test_in_sync(tmp_path):
    have = {"httpx": "0.28.1", "typing-extensions": "4.12.2", "uvicorn": "0.32.0"}
    assert deps_outdated(_root(tmp_path), have) is False        # colorama 没装：别的平台才要，不管


def test_locked_version_differs(tmp_path):
    have = {"httpx": "0.27.0", "typing-extensions": "4.12.2", "uvicorn": "0.32.0"}
    assert deps_outdated(_root(tmp_path), have) is True


def test_new_direct_dependency_not_installed(tmp_path):
    have = {"httpx": "0.28.1", "typing-extensions": "4.12.2"}
    assert deps_outdated(_root(tmp_path), have) is True


def test_unreadable_files_do_not_block(tmp_path):
    assert deps_outdated(tmp_path, {}) is False
    assert deps_outdated(_root(tmp_path, lock="not = [toml"), {}) is False


def test_real_checkout_is_in_sync():
    assert deps_outdated() is False
