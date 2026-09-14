"""Teste les fonctions pures de auth.py, sans appel réseau ni session Starlette.

Les emails autorisés/admin de test viennent de tests/conftest.py :
ami@example.com (allowed), admin@example.com (admin).
"""

from __future__ import annotations

from app.auth import evaluate_login, is_admin, is_allowed


def test_is_allowed_case_insensitive():
    assert is_allowed("AMI@EXAMPLE.COM")
    assert is_allowed("ami@example.com")


def test_is_allowed_admin_counts_as_allowed():
    assert is_allowed("admin@example.com")


def test_is_allowed_unknown_email_denied():
    assert not is_allowed("inconnu@example.com")


def test_is_allowed_empty_lists_denies_everyone():
    assert not is_allowed("quiconque@example.com", allowed=frozenset(), admin=frozenset())


def test_is_admin_only_for_admin_emails():
    assert is_admin("admin@example.com")
    assert not is_admin("ami@example.com")


def test_evaluate_login_rejects_unverified_email():
    ok, email = evaluate_login({"email": "ami@example.com", "email_verified": False})
    assert not ok
    assert email == "ami@example.com"


def test_evaluate_login_accepts_verified_allowed_email_and_normalizes_case():
    ok, email = evaluate_login({"email": "AMI@Example.com", "email_verified": True})
    assert ok
    assert email == "ami@example.com"


def test_evaluate_login_rejects_unknown_email():
    ok, _ = evaluate_login({"email": "inconnu@example.com", "email_verified": True})
    assert not ok


def test_evaluate_login_rejects_missing_email():
    ok, email = evaluate_login({"email_verified": True})
    assert not ok
    assert email == ""
