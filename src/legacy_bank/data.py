"""Synthetic in-memory bank state. All members, names, and balances are fake.

M1001 and M1002 are valid members with deliberately different balances; M404 does not exist.
"""

from dataclasses import dataclass, field
from decimal import Decimal

CHECKING = "checking"
SAVINGS = "savings"
ACCOUNT_LABELS = {CHECKING: "Checking", SAVINGS: "Savings"}


@dataclass
class Member:
    member_id: str
    full_name: str
    status: str
    balances: dict[str, Decimal]


@dataclass(frozen=True)
class Transfer:
    reference: str
    member_id: str
    from_account: str
    to_account: str
    amount: Decimal


class TransferError(ValueError):
    """A business validation failure; the message is safe to show to the operator."""


def _seed_members() -> dict[str, Member]:
    return {
        "M1001": Member(
            member_id="M1001",
            full_name="Alice Morgan",
            status="Active",
            balances={CHECKING: Decimal("2340.50"), SAVINGS: Decimal("15275.00")},
        ),
        "M1002": Member(
            member_id="M1002",
            full_name="Rahul Iyer",
            status="Active",
            balances={CHECKING: Decimal("980.00"), SAVINGS: Decimal("4120.75")},
        ),
    }


@dataclass
class Bank:
    members: dict[str, Member] = field(default_factory=_seed_members)
    transfers: list[Transfer] = field(default_factory=list)
    _txn_counter: int = 0

    def reset(self) -> None:
        self.members = _seed_members()
        self.transfers = []
        self._txn_counter = 0

    def find_member(self, member_id: str) -> Member | None:
        return self.members.get(member_id.strip().upper())

    def transfer(
        self, member_id: str, from_account: str, to_account: str, amount: Decimal
    ) -> Transfer:
        member = self.find_member(member_id)
        if member is None:
            raise TransferError(f"No member found for {member_id}.")
        validate_transfer(member, from_account, to_account, amount)
        member.balances[from_account] -= amount
        member.balances[to_account] += amount
        self._txn_counter += 1
        transfer = Transfer(
            reference=f"TXN-{self._txn_counter:06d}",
            member_id=member.member_id,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
        )
        self.transfers.append(transfer)
        return transfer


def validate_transfer(member: Member, from_account: str, to_account: str, amount: Decimal) -> None:
    if from_account not in ACCOUNT_LABELS or to_account not in ACCOUNT_LABELS:
        raise TransferError("Please choose valid accounts.")
    if from_account == to_account:
        raise TransferError("From and To accounts must be different.")
    if amount <= 0:
        raise TransferError("Amount must be greater than zero.")
    if member.balances[from_account] < amount:
        raise TransferError(
            f"Insufficient funds in {ACCOUNT_LABELS[from_account]}: "
            f"available balance is {format_money(member.balances[from_account])}."
        )


def parse_amount(raw: str) -> Decimal:
    """Parse an operator-entered amount: digits with at most two decimal places."""
    text = raw.strip().replace(",", "")
    try:
        amount = Decimal(text)
    except Exception:
        raise TransferError("Amount must be a number, e.g. 125.00.") from None
    if not amount.is_finite():
        raise TransferError("Amount must be a number, e.g. 125.00.")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise TransferError("Amount may have at most two decimal places.")
    return amount.quantize(Decimal("0.01"))


def format_money(value: Decimal) -> str:
    return f"${value:,.2f}"
