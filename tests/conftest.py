import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("GNANI_API_KEY", "unit-test-gnani-key-not-real")
os.environ.setdefault("GNANI_BOT_ID", "test-bot-id")
os.environ.setdefault("GNANI_MAX_RETRIES", "2")
os.environ.setdefault("GNANI_RETRY_BACKOFF_SECONDS", "0")
os.environ.setdefault("GNANI_OUTCOME_POLL_MAX_ATTEMPTS", "3")
os.environ.setdefault("GNANI_OUTCOME_POLL_INTERVAL_SECONDS", "0")
os.environ.setdefault("GNANI_CASE_STATUS_TIMEOUT_SECONDS", "3")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.pop("GNANI_DTMF_URL", None)
os.environ.pop("GNANI_CASE_STATUS_URL", None)
os.environ.pop("GNANI_ALLOWED_NUMBERS", None)

import logging

import pytest

from tests.fixtures import FakeSender


@pytest.fixture
def sender(monkeypatch):
    fake = FakeSender()
    from src import gnani_tools

    monkeypatch.setattr(gnani_tools, "provider_sender", fake)
    return fake


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setenv("GNANI_MAX_RETRIES", "2")
    monkeypatch.setenv("GNANI_RETRY_BACKOFF_SECONDS", "0")


@pytest.fixture
def no_dtmf_url(monkeypatch):
    monkeypatch.delenv("GNANI_DTMF_URL", raising=False)


@pytest.fixture
def raahi_logs(caplog):
    logger = logging.getLogger("raahi.gnani")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous_level)
