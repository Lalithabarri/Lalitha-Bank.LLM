"""StdinInterventionHandler — the V1 operator interface is the terminal (D18).

Prints the request and blocks until the human types ``done`` or ``abort``. ``done`` does not
mean "it worked" and ``abort`` does not mean "nothing happened": both are hand-back signals
after which the engine re-observes the same browser session and verifies deterministically.
The handler sees only the ``InterventionRequest``; it has no surface, gate, page or engine and
therefore cannot perform the UI action itself.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from cua.hitl.intervention import HandBack, HandBackKind, InterventionRequest

PROMPT = (
    "\n=== HUMAN INTERVENTION REQUIRED ===\n"
    "run {run_id}  session {session_id}  artifact {artifact_id}\n"
    "step {step_id} (#{step_index}): {reason} ({risk})\n"
    "requested action: {requested_action_summary}\n"
    "will be verified as: {verification_requirement}\n"
    "\n"
    "Perform the action yourself in the open browser window, then type\n"
    "  done   - re-observe the browser and verify (automation continues only if verified)\n"
    "  abort  - re-observe and verify, but do not continue automation afterwards\n"
)

_COMMANDS = {"done": HandBackKind.DONE, "abort": HandBackKind.ABORT}


class StdinInterventionHandler:
    def __init__(
        self,
        *,
        input: Callable[[], str] | None = None,  # noqa: A002 - mirrors builtins.input
        output: TextIO | None = None,
    ) -> None:
        self._input = input or (lambda: sys.stdin.readline())
        self._output = output or sys.stdout

    def intervene(self, request: InterventionRequest) -> HandBack:
        self._output.write(PROMPT.format(**request.model_dump()))
        self._output.flush()
        while True:
            self._output.write("> ")
            self._output.flush()
            line = self._input()
            if line == "":  # EOF: nobody is there; treat as abort, never as done
                self._output.write("stdin closed; treating as abort\n")
                return HandBack(kind=HandBackKind.ABORT, note="stdin closed")
            command, _, note = line.strip().partition(" ")
            kind = _COMMANDS.get(command.lower())
            if kind is None:
                self._output.write("type 'done' or 'abort'\n")
                continue
            return HandBack(kind=kind, note=note.strip())
