"""Server-rendered screens of the Legacy Bank Operations Console."""

from decimal import Decimal

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from legacy_bank.data import (
    ACCOUNT_LABELS,
    CHECKING,
    SAVINGS,
    Bank,
    Member,
    TransferError,
    format_money,
    parse_amount,
    validate_transfer,
)
from legacy_bank.faults import FaultMode

bp = Blueprint("bank", __name__)

# Balance of the phantom second "Savings" row rendered under AMBIGUOUS_SAVINGS.
_AMBIGUOUS_SAVINGS_BALANCE = Decimal("250.00")


def _bank() -> Bank:
    return current_app.extensions["bank"]


def _fault_mode() -> FaultMode | None:
    return current_app.config.get("FAULT_MODE")


def _get_member_or_404(member_id: str) -> Member:
    member = _bank().find_member(member_id)
    if member is None:
        abort(404)
    return member


def _account_rows(member: Member, *, apply_faults: bool) -> list[tuple[str, str]]:
    rows = [
        (ACCOUNT_LABELS[CHECKING], format_money(member.balances[CHECKING])),
        (ACCOUNT_LABELS[SAVINGS], format_money(member.balances[SAVINGS])),
    ]
    if apply_faults and _fault_mode() is FaultMode.AMBIGUOUS_SAVINGS:
        rows.append((ACCOUNT_LABELS[SAVINGS], format_money(_AMBIGUOUS_SAVINGS_BALANCE)))
    return rows


@bp.get("/")
def home():
    return redirect(url_for("bank.search"))


@bp.route("/members/search", methods=["GET", "POST"])
def search():
    if request.method == "GET":
        return render_template("search.html")
    member_id = request.form.get("member_id", "").strip()
    if not member_id:
        return render_template("search.html", error="Please enter a Member ID.")
    member = _bank().find_member(member_id)
    if member is None:
        # A legitimate business outcome, not an error page: the console stays on the search
        # screen and tells the operator which id was not found.
        return render_template(
            "search.html", member_id=member_id, error=f"No member found for {member_id}."
        )
    return redirect(url_for("bank.member_detail", member_id=member.member_id))


@bp.get("/members/<member_id>")
def member_detail(member_id: str):
    member = _bank().find_member(member_id)
    if member is None:
        return render_template("member_not_found.html", member_id=member_id), 404
    return render_template(
        "member_detail.html",
        member=member,
        account_rows=_account_rows(member, apply_faults=True),
    )


@bp.route("/members/<member_id>/transfer", methods=["GET", "POST"])
def transfer_form(member_id: str):
    member = _get_member_or_404(member_id)
    account_options = list(ACCOUNT_LABELS.items())
    form = {"from_account": CHECKING, "to_account": SAVINGS, "amount": ""}
    if request.method == "GET":
        return _render_transfer_form(member, form, account_options)

    form = {
        "from_account": request.form.get("from_account", ""),
        "to_account": request.form.get("to_account", ""),
        "amount": request.form.get("amount", ""),
    }
    try:
        amount = parse_amount(form["amount"])
        validate_transfer(member, form["from_account"], form["to_account"], amount)
    except TransferError as exc:
        return _render_transfer_form(member, form, account_options, error=str(exc))

    return render_template(
        "transfer_review.html",
        member=member,
        from_account=form["from_account"],
        to_account=form["to_account"],
        amount=f"{amount:.2f}",
        from_label=ACCOUNT_LABELS[form["from_account"]],
        to_label=ACCOUNT_LABELS[form["to_account"]],
        amount_display=format_money(amount),
    )


def _render_transfer_form(member, form, account_options, error=None):
    return render_template(
        "transfer.html",
        member=member,
        form=form,
        account_options=account_options,
        account_rows=_account_rows(member, apply_faults=False),
        error=error,
    )


@bp.post("/members/<member_id>/transfer/confirm")
def transfer_confirm(member_id: str):
    member = _get_member_or_404(member_id)
    account_options = list(ACCOUNT_LABELS.items())
    form = {
        "from_account": request.form.get("from_account", ""),
        "to_account": request.form.get("to_account", ""),
        "amount": request.form.get("amount", ""),
    }
    # Re-validate everything carried in hidden fields; the review page is not trusted.
    try:
        amount = parse_amount(form["amount"])
        transfer = _bank().transfer(
            member.member_id, form["from_account"], form["to_account"], amount
        )
    except TransferError as exc:
        return _render_transfer_form(member, form, account_options, error=str(exc))
    return redirect(
        url_for("bank.transfer_complete", member_id=member.member_id, ref=transfer.reference)
    )


@bp.get("/members/<member_id>/transfer/complete")
def transfer_complete(member_id: str):
    member = _get_member_or_404(member_id)
    ref = request.args.get("ref", "")
    transfer = next(
        (t for t in _bank().transfers if t.reference == ref and t.member_id == member.member_id),
        None,
    )
    if transfer is None:
        abort(404)
    return render_template(
        "transfer_complete.html",
        member=member,
        transfer=transfer,
        from_label=ACCOUNT_LABELS[transfer.from_account],
        to_label=ACCOUNT_LABELS[transfer.to_account],
        amount_display=format_money(transfer.amount),
        account_rows=_account_rows(member, apply_faults=False),
    )


@bp.post("/__admin/reset")
def admin_reset():
    """Test-harness control: restore seed data. Not linked from any screen."""
    _bank().reset()
    return "", 204
