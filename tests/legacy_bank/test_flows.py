"""Behavioral tests for the synthetic Legacy Bank Operations Console (Flask test client)."""

import re

import pytest

from legacy_bank import create_app


def html(response) -> str:
    return response.get_data(as_text=True)


def savings_rows(page: str) -> list[str]:
    return re.findall(r'<th scope="row">Savings</th><td>([^<]+)</td>', page)


def checking_rows(page: str) -> list[str]:
    return re.findall(r'<th scope="row">Checking</th><td>([^<]+)</td>', page)


def alert_text(page: str) -> str | None:
    match = re.search(r'<p role="alert"><b>([^<]+)</b></p>', page)
    return match.group(1) if match else None


# --- search -> detail -> balances --------------------------------------------------------------


def test_root_redirects_to_search(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/members/search")


def test_search_page_has_labelled_input_and_button(client):
    page = html(client.get("/members/search"))
    assert '<label for="member_id">Member ID</label>' in page
    assert 'id="member_id" name="member_id"' in page
    assert '<button type="submit">Search</button>' in page


@pytest.mark.parametrize(
    "member_id, name, checking, savings",
    [
        ("M1001", "Alice Morgan", "$2,340.50", "$15,275.00"),
        ("M1002", "Rahul Iyer", "$980.00", "$4,120.75"),
    ],
)
def test_search_valid_member_shows_detail_and_balances(client, member_id, name, checking, savings):
    response = client.post("/members/search", data={"member_id": member_id})
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/members/{member_id}")

    page = html(client.get(f"/members/{member_id}"))
    assert f"<h1>Member {member_id} &mdash; {name}</h1>" in page
    assert checking_rows(page) == [checking]
    assert savings_rows(page) == [savings]


def test_search_absent_member_is_business_outcome_on_search_screen(client):
    response = client.post("/members/search", data={"member_id": "M404"})
    assert response.status_code == 200
    page = html(response)
    assert alert_text(page) == "No member found for M404."
    assert "<h1>Member Search</h1>" in page


def test_direct_navigation_to_absent_member_is_404_with_same_text(client):
    response = client.get("/members/M404")
    assert response.status_code == 404
    assert alert_text(html(response)) == "No member found for M404."


def test_search_empty_member_id_prompts(client):
    response = client.post("/members/search", data={"member_id": "   "})
    assert response.status_code == 200
    assert alert_text(html(response)) == "Please enter a Member ID."


# --- detail -> transfer -> review -> confirm -> complete ---------------------------------------


def test_transfer_form_has_labelled_controls(client):
    page = html(client.get("/members/M1001/transfer"))
    assert '<label for="from_account">From account</label>' in page
    assert '<label for="to_account">To account</label>' in page
    assert '<label for="amount">Amount</label>' in page
    assert '<button type="submit">Review transfer</button>' in page


def test_transfer_happy_path(client):
    review = client.post(
        "/members/M1001/transfer",
        data={"from_account": "checking", "to_account": "savings", "amount": "500.00"},
    )
    assert review.status_code == 200
    page = html(review)
    assert "<h1>Review Transfer &mdash; Member M1001</h1>" in page
    assert '<input type="hidden" name="from_account" value="checking">' in page
    assert '<input type="hidden" name="to_account" value="savings">' in page
    assert '<input type="hidden" name="amount" value="500.00">' in page
    assert '<button type="submit">Confirm transfer</button>' in page

    # Review alone must not move money.
    detail = html(client.get("/members/M1001"))
    assert checking_rows(detail) == ["$2,340.50"]

    confirm = client.post(
        "/members/M1001/transfer/confirm",
        data={"from_account": "checking", "to_account": "savings", "amount": "500.00"},
    )
    assert confirm.status_code == 302
    location = confirm.headers["Location"]
    assert "/members/M1001/transfer/complete?ref=TXN-000001" in location

    complete = client.get(location)
    assert complete.status_code == 200
    page = html(complete)
    assert "<h1>Transfer Complete</h1>" in page
    assert "TXN-000001" in page
    assert checking_rows(page) == ["$1,840.50"]
    assert savings_rows(page) == ["$15,775.00"]

    detail = html(client.get("/members/M1001"))
    assert checking_rows(detail) == ["$1,840.50"]
    assert savings_rows(detail) == ["$15,775.00"]


def test_second_transfer_increments_reference(client):
    data = {"from_account": "savings", "to_account": "checking", "amount": "1"}
    client.post("/members/M1001/transfer/confirm", data=data)
    second = client.post("/members/M1001/transfer/confirm", data=data)
    assert "ref=TXN-000002" in second.headers["Location"]


@pytest.mark.parametrize(
    "data, expected",
    [
        (
            {"from_account": "checking", "to_account": "savings", "amount": "5000.00"},
            "Insufficient funds in Checking: available balance is $2,340.50.",
        ),
        (
            {"from_account": "checking", "to_account": "checking", "amount": "10"},
            "From and To accounts must be different.",
        ),
        (
            {"from_account": "checking", "to_account": "savings", "amount": "abc"},
            "Amount must be a number, e.g. 125.00.",
        ),
        (
            {"from_account": "checking", "to_account": "savings", "amount": "-5"},
            "Amount must be greater than zero.",
        ),
        (
            {"from_account": "checking", "to_account": "savings", "amount": "0"},
            "Amount must be greater than zero.",
        ),
        (
            {"from_account": "checking", "to_account": "savings", "amount": "1.005"},
            "Amount may have at most two decimal places.",
        ),
        (
            {"from_account": "checking", "to_account": "bitcoin", "amount": "10"},
            "Please choose valid accounts.",
        ),
    ],
)
def test_transfer_validation_errors_leave_state_unchanged(client, data, expected):
    response = client.post("/members/M1001/transfer", data=data)
    assert response.status_code == 200
    page = html(response)
    assert alert_text(page) == expected
    assert "<h1>Transfer Funds &mdash; Member M1001</h1>" in page

    detail = html(client.get("/members/M1001"))
    assert checking_rows(detail) == ["$2,340.50"]
    assert savings_rows(detail) == ["$15,275.00"]


def test_confirm_revalidates_tampered_hidden_fields(client):
    response = client.post(
        "/members/M1001/transfer/confirm",
        data={"from_account": "checking", "to_account": "savings", "amount": "999999.00"},
    )
    assert response.status_code == 200
    assert alert_text(html(response)).startswith("Insufficient funds in Checking")
    detail = html(client.get("/members/M1001"))
    assert checking_rows(detail) == ["$2,340.50"]


def test_transfer_routes_for_absent_member_are_404(client):
    assert client.get("/members/M404/transfer").status_code == 404
    assert client.post("/members/M404/transfer/confirm", data={}).status_code == 404


def test_complete_page_with_unknown_reference_is_404(client):
    assert client.get("/members/M1001/transfer/complete?ref=TXN-999999").status_code == 404


# --- harness controls and isolation ------------------------------------------------------------


def test_admin_reset_restores_seed_and_counter(client):
    data = {"from_account": "checking", "to_account": "savings", "amount": "100"}
    client.post("/members/M1001/transfer/confirm", data=data)
    assert checking_rows(html(client.get("/members/M1001"))) == ["$2,240.50"]

    assert client.post("/__admin/reset").status_code == 204

    assert checking_rows(html(client.get("/members/M1001"))) == ["$2,340.50"]
    again = client.post("/members/M1001/transfer/confirm", data=data)
    assert "ref=TXN-000001" in again.headers["Location"]


def test_app_instances_do_not_share_state():
    a, b = create_app().test_client(), create_app().test_client()
    a.post(
        "/members/M1001/transfer/confirm",
        data={"from_account": "checking", "to_account": "savings", "amount": "100"},
    )
    assert checking_rows(html(a.get("/members/M1001"))) == ["$2,240.50"]
    assert checking_rows(html(b.get("/members/M1001"))) == ["$2,340.50"]


# --- deterministic fault mode ------------------------------------------------------------------


def test_default_mode_has_exactly_one_savings_row(client):
    assert len(savings_rows(html(client.get("/members/M1001")))) == 1


def test_ambiguous_savings_mode_renders_two_savings_rows(client_ambiguous):
    page = html(client_ambiguous.get("/members/M1001"))
    rows = savings_rows(page)
    assert len(rows) == 2
    assert rows[0] == "$15,275.00"
    assert rows[0] != rows[1]
    assert page.count("Savings") >= 2
    # Checking remains unique so the fault is targeted, not global.
    assert len(checking_rows(page)) == 1


def test_fault_mode_from_env(monkeypatch):
    monkeypatch.setenv("LEGACY_BANK_FAULT_MODE", "ambiguous_savings")
    app = create_app()
    assert len(savings_rows(html(app.test_client().get("/members/M1002")))) == 2


def test_unknown_fault_mode_is_rejected():
    with pytest.raises(ValueError):
        create_app(fault_mode="not_a_mode")
