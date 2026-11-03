import os
import pytest
from backend import datastore
from backend.datastore.files import FileStore

def test_suite_runs_on_files():
    assert os.environ.get("ATM_STORE") == "files"
    assert "DATABASE_URL_POOLED" not in os.environ and "DATABASE_URL" not in os.environ
    assert isinstance(datastore.get_store(), FileStore)

def test_no_url_means_files(monkeypatch):
    monkeypatch.delenv("ATM_STORE", raising=False)
    datastore.set_store(None)
    assert isinstance(datastore.get_store(), FileStore)
    datastore.set_store(None)

def test_files_status_is_not_persistent():
    s = FileStore().status()
    assert (s.backend, s.degraded, s.persistent) == ("files", False, False)
