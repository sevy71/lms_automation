"""
Onboarding Blueprint — public organiser self-service flow.

GET  /get-started              → organiser creation form
POST /create-organiser         → create organiser workspace, redirect to dashboard
GET  /organiser/<slug>/dashboard → organiser dashboard
GET  /organiser/<slug>/qr.png  → invite QR code PNG (for dashboard embed)
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
    url_for,
)

from lms_automation.extensions import db
from lms_automation.models import Organiser, Round

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
    """Return *base* if it is unused, otherwise append an incrementing counter."""
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@onboarding_bp.route("/get-started")
def get_started():
    return render_template("get_started.html")


@onboarding_bp.route("/create-organiser", methods=["POST"])
def create_organiser():
    name = (request.form.get("name") or "").strip()

    if not name:
        return render_template("get_started.html", error="Organisation name is required.")

    base_slug = _make_slug(name)
    if not base_slug:
        return render_template(
            "get_started.html",
            error="Could not generate a valid slug from that name. Please use letters or numbers.",
        )

    slug = _unique_slug(base_slug)

    new_org = Organiser(name=name, slug=slug, status="active")
    db.session.add(new_org)
    db.session.commit()

    return redirect(url_for("onboarding.organiser_dashboard", slug=slug))


@onboarding_bp.route("/organiser/<slug>/dashboard")
def organiser_dashboard(slug: str):
    org = Organiser.query.filter_by(slug=slug).first_or_404()

    total_players = org.players.count()
    active_players = org.players.filter_by(status="active").count()

    # Show the active round, or the next pending one if none is active
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
