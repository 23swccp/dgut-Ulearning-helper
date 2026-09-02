"""All offline tests import the service against an isolated, empty data directory."""

import os
import tempfile

_original_data_dir = os.environ.get("YXY_DATA_DIR")
_test_data = tempfile.TemporaryDirectory(prefix="dgut-offline-tests-")
os.environ["YXY_DATA_DIR"] = _test_data.name


def pytest_unconfigure(config):
    if _original_data_dir is None:
        os.environ.pop("YXY_DATA_DIR", None)
    else:
        os.environ["YXY_DATA_DIR"] = _original_data_dir
    _test_data.cleanup()
