"""``legacy-bank`` — run the synthetic Legacy Bank Operations Console locally."""

import argparse

from legacy_bank import create_app
from legacy_bank.faults import FaultMode


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="legacy-bank", description="Run the synthetic Legacy Bank Operations Console."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--fault-mode",
        choices=[m.value for m in FaultMode],
        default=None,
        help="deterministic fault to inject for the lifetime of the process "
        "(also LEGACY_BANK_FAULT_MODE env var)",
    )
    args = parser.parse_args(argv)

    app = create_app(fault_mode=args.fault_mode)
    mode = app.config["FAULT_MODE"]
    print(f"Legacy Bank Operations Console on http://{args.host}:{args.port}")
    print(f"fault mode: {mode.value if mode else 'none'}")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
