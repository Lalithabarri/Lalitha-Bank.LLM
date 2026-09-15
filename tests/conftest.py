import pytest

from legacy_bank import create_app
from legacy_bank.faults import FaultMode


@pytest.fixture
def bank_app():
    return create_app(fault_mode=None)


@pytest.fixture
def bank_app_ambiguous():
    return create_app(fault_mode=FaultMode.AMBIGUOUS_SAVINGS)


@pytest.fixture
def client(bank_app):
    return bank_app.test_client()


@pytest.fixture
def client_ambiguous(bank_app_ambiguous):
    return bank_app_ambiguous.test_client()
