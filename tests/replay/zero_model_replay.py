"""Zero-model replay in a fresh interpreter.

Run as ``python -m tests.replay.zero_model_replay --member M1001 [--live]``. Before anything
from ``cua`` is imported, an import guard is installed that makes importing ``google*``,
``cua.llm`` or ``cua.discovery`` raise. The replay then runs to completion — against the
scripted surface, or (``--live``) a real Chromium and an in-process Legacy Bank — and prints one
JSON line. Because the interpreter is fresh, no previously imported module can make the guard
vacuous.
"""

import argparse
import importlib.abc
import json
import sys
import threading
from decimal import Decimal

FORBIDDEN_PREFIXES = ("google", "cua.llm", "cua.discovery")


class _Guard(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(FORBIDDEN_PREFIXES):
            self.attempts.append(fullname)
            raise ImportError(f"zero-model guard: {fullname} must never be imported by replay")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--member", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    guard = _Guard()
    sys.meta_path.insert(0, guard)
    assert not any(m.startswith(FORBIDDEN_PREFIXES) for m in sys.modules)

    from cua.replay import MonotonicClock, TerminalStatus  # noqa: E402 - after the guard
    from tests.replay.support import engine_for, flagship  # noqa: E402

    if args.live:
        from werkzeug.serving import make_server

        from cua.surface.playwright_surface import PlaywrightSurface
        from legacy_bank import create_app

        server = make_server("127.0.0.1", 0, create_app(fault_mode=None), threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        surface = PlaywrightSurface(headless=True).open()
        try:
            result = engine_for(surface, MonotonicClock(), base_url=base_url).run(
                flagship(), {"member_id": args.member}
            )
        finally:
            surface.close()
            server.shutdown()
    else:
        from tests.replay.scripted_surface import ScriptedSurface

        surface = ScriptedSurface(base_url="http://fake.test")
        result = engine_for(surface, MonotonicClock(), base_url="http://fake.test").run(
            flagship(), {"member_id": args.member}
        )

    loaded = sorted(m for m in sys.modules if m.startswith(FORBIDDEN_PREFIXES))
    print(
        json.dumps(
            {
                "status": result.status.value,
                "outputs": {k: str(v) for k, v in result.outputs.items()},
                "output_types": {k: type(v).__name__ for k, v in result.outputs.items()},
                "forbidden_loaded": loaded,
                "forbidden_attempted": guard.attempts,
                "success_is_decimal": all(isinstance(v, Decimal) for v in result.outputs.values())
                and result.status is TerminalStatus.SUCCESS,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
