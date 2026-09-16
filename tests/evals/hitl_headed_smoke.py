"""Headed-browser + stdin smoke (Milestone 8, infrastructure validation — not an eval).

Run as ``uv run python -m tests.evals.hitl_headed_smoke [--wait-s N]``.

Proves, before any handoff code exists, that the same ``PlaywrightSurface`` can be visibly driven
by a human while the Python process is blocked on stdin, and can observe afterwards:

1. Chromium launches headed (``headless=False``) and a window is visible.
2. The synthetic bank loads (one automated NAVIGATE, then nothing).
3. Python blocks waiting for stdin.
4. The browser stays responsive while Python is blocked.
5. The human interacts with the page directly (e.g. searches a member).
6. After Enter the SAME surface/page observes successfully (URL + title printed).
7. The browser shuts down cleanly.

No ``Surface.act`` impersonates the human. When stdin is not a terminal the script waits
``--wait-s`` seconds instead of Enter (unattended mode: proves 1, 2, 4, 6, 7 only).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

from werkzeug.serving import make_server

from cua.domain import ActionType
from cua.surface import SurfaceAction
from cua.surface.playwright_surface import PlaywrightSurface
from legacy_bank import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-s", type=float, default=15.0)
    args = parser.parse_args(argv)

    server = make_server("127.0.0.1", 0, create_app(fault_mode=None), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    surface = PlaywrightSurface(headless=False).open()
    session_id = surface.session_id
    print(f"[1] headed Chromium launched; session_id={session_id}")
    surface.act(SurfaceAction(action_type=ActionType.NAVIGATE, url=f"{base_url}/members/search"))
    before = surface.observe()
    print(f"[2] bank loaded: url={before.url} title={before.page_title!r}")

    interactive = sys.stdin.isatty()
    if interactive:
        print("[3] Python is now blocked on stdin. Interact with the page in the window")
        print("    (e.g. search a member), then press Enter here.")
        sys.stdin.readline()
        print("[5] Enter received.")
    else:
        print(f"[3] stdin is not a terminal: unattended mode, waiting {args.wait_s:.0f}s")
        time.sleep(args.wait_s)

    after = surface.observe()
    print(
        f"[4/6] same surface observed after the wait: session_id={surface.session_id} "
        f"url={after.url} title={after.page_title!r} elements={len(after.elements)}"
    )
    same_session = surface.session_id == session_id
    surface.close()
    server.shutdown()
    print(f"[7] browser closed cleanly; same_session={same_session}")
    return 0 if same_session else 1


if __name__ == "__main__":
    sys.exit(main())
