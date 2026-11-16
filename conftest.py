# Import the installed packages before pytest's importlib mode derives module names like
# "mock_backend.tests.conftest" from the repo layout, which would otherwise register the
# mock_backend/ project directory as a namespace package that shadows the real package.
import mock_backend.app  # noqa: F401
