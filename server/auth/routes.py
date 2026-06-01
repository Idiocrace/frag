"""Flask blueprint exposing /login, /register, /verify, /account.

Use `create_auth_blueprint` to build a blueprint wired to concrete
implementations of AccountStore, MFAStore, Mailer, and AccountTemplate.
"""

import logging

from flask import Blueprint, render_template, redirect, request, session, url_for

from server.auth.accounts import AccountStore
from server.auth.mailer import Mailer
from server.auth.mfa import MFAStore, user_needs_mfa
from server.auth.passwords import hash_password, verify_password
from server.auth.templates import AccountTemplate


log = logging.getLogger(__name__)


def create_auth_blueprint(
    *,
    accounts: AccountStore,
    mfa: MFAStore,
    mailer: Mailer,
    template: AccountTemplate,
    url_prefix: str | None = None,
) -> Blueprint:
    bp = Blueprint("auth", __name__, url_prefix=url_prefix)

    @bp.route("/login", methods=["GET", "POST"])
    def login():
        if request.method != "POST":
            return render_template("login.html")

        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user_data = accounts.get(username)
        log.info("login attempt: %s", username)

        if not user_data:
            return render_template("login.html", error="Invalid credentials")

        valid, needs_upgrade = verify_password(password, user_data.get("password", ""))
        if not valid:
            return render_template("login.html", error="Invalid credentials")

        if needs_upgrade:
            user_data["password"] = hash_password(password)
            accounts.set(username, user_data)
            log.info("upgraded legacy password for user: %s", username)

        if user_needs_mfa(user_data):
            code = mfa.issue(username)
            mailer.send_mfa_code(user_data.get("email", ""), code, _ttl_for(mfa))
            session["pending_user"] = username
            return redirect(url_for("auth.verify"))

        session.permanent = True
        session["user"] = username
        return redirect(url_for("auth.account"))

    @bp.route("/register", methods=["GET", "POST"])
    def register():
        if request.method != "POST":
            return render_template("register.html")

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip()

        if not username or not password or not email:
            return render_template("register.html", error="All fields are required")

        if accounts.get(username):
            return render_template("register.html", error="Username already exists")

        new_acct = template.new_account(
            username=username,
            email=email,
            password_hash=hash_password(password),
        )
        accounts.set(username, new_acct)

        session["user"] = username
        return redirect(url_for("auth.account"))

    @bp.route("/verify", methods=["GET", "POST"])
    def verify():
        pending = session.get("pending_user")
        if not pending:
            return redirect(url_for("auth.login"))

        if request.method != "POST":
            return render_template("verify.html")

        code_input = request.form.get("code", "")
        if mfa.verify(pending, code_input):
            session.permanent = True
            session["user"] = pending
            session.pop("pending_user", None)
            return redirect(url_for("auth.account"))

        return render_template("verify.html", error="Invalid code")

    @bp.route("/account", methods=["GET", "POST"])
    def account():
        if "user" not in session:
            return redirect(url_for("auth.login"))

        if request.method == "POST" and "logout" in request.form:
            session.clear()
            return redirect(url_for("auth.login"))

        user_data = accounts.get(session["user"])
        return render_template("account.html", u_data=user_data)

    return bp


def _ttl_for(mfa: MFAStore) -> tuple[int, str]:
    """Pull TTL metadata from MFA stores that expose it; fall back to (10, 'minutes')."""
    ttl_human = getattr(mfa, "ttl_human", None)
    if callable(ttl_human):
        return ttl_human()
    return (10, "minutes")
