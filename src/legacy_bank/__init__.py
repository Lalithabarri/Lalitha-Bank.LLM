"""Legacy Bank Operations Console — the synthetic target application (D01).

Stand-alone Flask app with fake in-memory data. It is the thing the automation drives through
its UI; nothing in ``cua`` imports it, and it imports nothing from ``cua``.
"""

import os

from flask import Flask

from legacy_bank.data import Bank
from legacy_bank.faults import FaultMode


def create_app(fault_mode: FaultMode | str | None = None) -> Flask:
    """Build an app with its own fresh Bank state.

    ``fault_mode`` defaults to the ``LEGACY_BANK_FAULT_MODE`` environment variable; it is fixed
    for the lifetime of the app and not switchable through the UI.
    """
    if fault_mode is None:
        fault_mode = os.environ.get("LEGACY_BANK_FAULT_MODE") or None
    if isinstance(fault_mode, str):
        fault_mode = FaultMode(fault_mode)

    app = Flask(__name__)
    app.config["FAULT_MODE"] = fault_mode
    app.extensions["bank"] = Bank()

    from legacy_bank.routes import bp

    app.register_blueprint(bp)
    return app
