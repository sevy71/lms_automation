"""
Onboarding Blueprint — public organiser self-service flow.

GET  /get-started               → organiser creation form
POST /create-organiser          → create organiser + admin account, auto-login, redirect
GET  /organiser/<slug>/dashboard → organiser dashboard (requires organiser_admin session)
GET  /organiser/<slug>/qr.png   → invite QR code PNG
"""
from __future__ import annotations

import io
import os
import re

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from lms_automation.extensions import db
from lms_automation.models import AdminUser, Organiser, Round

onboarding_bp = Blueprint("onboarding", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slug(name: str) -> str:
    """Convert an organiser name to a URL-safe slug."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def _unique_slug(base: str) -> str:
    """Return *base* if unused, otherwise append an incrementing counter."""
    slug = base
    counter = 2
    while Organiser.query.filter_by(slug=slug).first():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _registration_url(organiser_slug: str) -> str:
    """Build the absolute player registration URL for an organiser."""
    base = os.environ.get("BASE_URL", "").rstrip("/")
    if not base:
        base = request.url_root.rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = base.replace("http://", "https://")
    return f"{base}/register?organiser={organiser_slug}"


def _set_organiser_session(admin_user: AdminUser) -> None:
    """Populate the Flask session for a newly authenticated organiser_admin.

    Mirrors the structure of _set_admin_session() in app.py so that the same
    session keys are used throughout the codebase.
    """
    session.clear()
    session["admin_logged_in"] = True
    session.permanent = True
    session["admin_user_id"] = admin_user.id
    session["admin_role"] = admin_user.role           # 'organiser_admin'
    session["organiser_id"] = admin_user.organiser_id
    session["organiser_slug"] = admin_user.organiser.slug if admin_user.organiser else ""
    session["organiser_name"] = admin_user.organiser.name if admin_user.organiser else ""


def _require_organiser_auth(slug: str) -> Organiser:
    """Verify the session is an organiser_admin who owns *slug*.

    Returns the Organiser on success.
    Redirects to login if unauthenticated; aborts 403 if wrong role or org.
    """
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login", next=request.url))  # type: ignore[return-value]
    if session.get("admin_role") != "organiser_admin":
        abort(403)
    org = Organiser.query.filter_by(slug=slug).first_or_404()
    if session.get("organiser_id") != org.id:
        abort(403)
    return org


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@onboarding_bp.route("/get-started")
def get_started():
    return render_template("get_started.html")


@onboarding_bp.route("/create-organiser", methods=["POST"])
def create_organiser():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    # ── Validate input ──────────────────────────────────────────────────────
    if not name:
        return render_template("get_started.html", error="Competition name is required.")

    base_slug = _make_slug(name)
    if not base_slug:
        return render_template(
            "get_started.html",
            error="Could not generate a valid slug from that name. Please use letters or numbers.",
        )

    if not email or "@" not in email:
        return render_template("get_started.html", error="A valid email address is required.")

    if len(password) < 12:
        return render_template("get_started.html", error="Password must be at least 12 characters.")

    # Check email (used as username) is not already registered
    if AdminUser.query.filter_by(username=email).first():
        return render_template(
            "get_started.html",
            error="An account with that email already exists. Please log in instead.",
        )

    # ── Create organiser ────────────────────────────────────────────────────
    slug = _unique_slug(base_slug)
    new_org = Organiser(name=name, slug=slug, status="active")
    db.session.add(new_org)
    db.session.flush()  # get new_org.id without committing yet

    # ── Create organiser admin account ──────────────────────────────────────
    admin_user = AdminUser(
        username=email,
        organiser_id=new_org.id,
        role="organiser_admin",
        is_active=True,
    )
    admin_user.set_password(password)
    db.session.add(admin_user)
    db.session.commit()

    # ── Auto-login ──────────────────────────────────────────────────────────
    _set_organiser_session(admin_user)

    return redirect(url_for("onboarding.organiser_dashboard", slug=slug))


@onboarding_bp.route("/organiser/<slug>/dashboard")
def organiser_dashboard(slug: str):
    result = _require_organiser_auth(slug)
    # _require_organiser_auth returns a redirect response when unauthenticated
    if not isinstance(result, Organiser):
        return result
    org = result

    total_players = org.players.count()
    active_players = org.players.filter_by(status="active").count()

    current_round = (
        org.rounds.filter_by(status="active")
        .order_by(Round.round_number.desc())
        .first()
    )
    if not current_round:
        current_round = (
            org.rounds.filter_by(status="pending")
            .order_by(Round.round_number.asc())
            .first()
        )

    reg_url = _registration_url(slug)
    qr_url = url_for("onboarding.organiser_qr_png", slug=slug)

    return render_template(
        "organiser_dashboard.html",
        org=org,
        total_players=total_players,
        active_players=active_players,
        current_round=current_round,
        reg_url=reg_url,
        qr_url=qr_url,
    )


@onboarding_bp.route("/organiser/<slug>/qr.png")
def organiser_qr_png(slug: str):
    """Return a QR code PNG for the organiser's player invite link."""
    org = Organiser.query.filter_by(slug=slug).first_or_404()
    reg_url = _registration_url(org.slug)

    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(reg_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        from flask import send_file
        return send_file(buf, mimetype="image/png")
    except ImportError:
        abort(503, description="QR code library not available.")
