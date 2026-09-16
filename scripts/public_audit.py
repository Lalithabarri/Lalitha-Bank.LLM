"""Public-artifact audit: nothing in the committed evidence, capabilities or docs may leak what the
architecture forbids. Runs offline in ``scripts/verify.sh``; exit 1 on any finding.

Checks (E10 and the I1/I6 persistence rules, applied to the files a reviewer downloads):
* no credential shapes in tracked text files outside ``tests/`` (``sk-…``, ``Bearer``,
  ``OPENAI_API_KEY=…``; the tests carry sentinel keys precisely to prove they never persist);
* no ``.env`` file tracked;
* no absolute user paths or the local hostname in evidence / capabilities / docs;
* evidence: no runtime member id, no ``"ref"`` key or ref-shaped token, every line redacted,
  every screenshot referenced by an event exists and matches its sha256, no stray binaries;
* capabilities: no member id, no ref-shaped value, no selector syntax, every file validates.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMBER_IDS = ("M1001", "M1002", "M404")
_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")
_KEY = re.compile(
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]{8,}|OPENAI_API_KEY\s*=\s*\S{8,}"
)
_ABS_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[A-Za-z0-9_.-]+")
_SELECTOR = re.compile(r'"(?:css=|xpath=|text=|//)')
TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".py", ".txt", ".toml", ".sh", ".html"}


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / line for line in out.stdout.splitlines() if line]


def main() -> int:
    findings: list[str] = []
    files = tracked_files()
    hostname = socket.gethostname()

    if any(
        p.name == ".env" or p.name.startswith(".env.") and p.name != ".env.example" for p in files
    ):
        findings.append("a .env file is tracked")

    for path in files:
        if path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        # tests/ carries deliberate sentinel keys whose whole purpose is to prove they never persist
        if not rel.startswith("tests/") and _KEY.search(text):
            findings.append(f"{rel}: credential-shaped text")
        if rel.startswith(("evidence/", "capabilities/")) or path.suffix == ".md":
            if _ABS_PATH.search(text):
                findings.append(f"{rel}: absolute user path")
            if hostname and hostname in text:
                findings.append(f"{rel}: local hostname")

    for path in (ROOT / "evidence").rglob("events.jsonl"):
        rel = path.relative_to(ROOT).as_posix()
        raw = path.read_text(encoding="utf-8")
        for member in MEMBER_IDS:
            if member in raw:
                findings.append(f"{rel}: runtime member id {member} persisted")
        if '"ref"' in raw or _REF_TOKEN.search(raw):
            findings.append(f"{rel}: transient ref persisted")
        for number, line in enumerate(raw.splitlines(), start=1):
            event = json.loads(line)
            if event.get("redaction_applied") is not True:
                findings.append(f"{rel}:{number}: line persisted without redaction")
            payload = event.get("payload", {})
            for key in ("pre_screenshot", "post_screenshot"):
                ref = payload.get(key)
                if ref:
                    target = path.parent / ref["path"]
                    if ref["path"].startswith("/") or ".." in ref["path"].split("/"):
                        findings.append(f"{rel}:{number}: unsafe artifact path {ref['path']}")
                    elif not target.exists():
                        findings.append(f"{rel}:{number}: missing artifact {ref['path']}")
                    elif hashlib.sha256(target.read_bytes()).hexdigest() != ref["sha256"]:
                        findings.append(f"{rel}:{number}: sha256 mismatch for {ref['path']}")
        for binary in path.parent.rglob("*"):
            if binary.is_file() and binary.suffix not in (".jsonl", ".png"):
                findings.append(f"{binary.relative_to(ROOT)}: unexpected file in a run directory")

    sys.path.insert(0, str(ROOT / "src"))
    from cua.artifact import ArtifactStore  # noqa: E402

    for path in (ROOT / "capabilities").rglob("*@*.json"):
        rel = path.relative_to(ROOT).as_posix()
        if "compile_reports" in rel:
            continue
        text = path.read_text(encoding="utf-8")
        for member in MEMBER_IDS:
            if member in text:
                findings.append(f"{rel}: member id {member} in an artifact")
        if re.search(r'"(?:f\d+)?e\d+"', text) or _SELECTOR.search(text):
            findings.append(f"{rel}: ref or selector in an artifact")
        try:
            ArtifactStore(path.parent).load_id(path.stem)
        except Exception as exc:  # noqa: BLE001 - report, do not hide
            findings.append(f"{rel}: does not validate: {exc}")

    for finding in findings:
        print(f"FAIL  {finding}")
    print(f"public audit: {len(files)} tracked files, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
