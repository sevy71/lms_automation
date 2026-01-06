# Modular LMS Experiment

This directory (`LMS2_modular`) is a safe copy of the original LMS2 project that reshapes the app into smaller, easier-to-manage pieces. The legacy `lms_automation` package has been left untouched for reference. New code lives under `lms_platform/` and demonstrates how to split the system into blueprints, services, and reusable helpers.

## Layout

```
lms_platform/
  __init__.py          # App factory that wires config, extensions, and blueprints
  config.py            # Centralised configuration (DB, secrets, defaults)
  extensions.py        # Shared SQLAlchemy + migration instances
  blueprints/
    public/            # Player-facing pages (currently just the index)
    auth/              # Admin login/logout routes
    admin/             # Admin dashboard + picks grid
    api/               # JSON endpoints (picks-grid data for now)
  services/
    teams.py           # Canonical team display names
    picks.py           # Picks grid data assembly
    admin.py           # Admin dashboard orchestration helpers
  auth/
    decorators.py      # `admin_required` decorator reused across blueprints
modular_app.py         # WSGI entrypoint calling `create_app()`
```

The blueprints reuse the existing templates and static files from `lms_automation/` so you can compare UI results directly against the legacy app.

## Running the experimental build

```bash
cd /Users/antoniosirignanonew/Projects/LMS2_modular
source venv/bin/activate            # optional: reuse the copied virtualenv
export FLASK_APP=modular_app:app
flask run
```

Admin login continues to use `ADMIN_PASSWORD` from environment (default `admin123`).

## What changed compared to the legacy app?

- **App factory & blueprints:** Instead of a 2,800-line `app.py`, routes are grouped by concern (public, auth, admin, api) and registered from `create_app`. This makes it clearer where new pages belong and simplifies testing.
- **Service layer:** Core business logic (team labels, picks-grid data, admin dashboard context) lives in `lms_platform/services`. Views call these helpers, keeping templates thin and behaviour reusable (e.g., API + CSV export can share the same data builder).
- **Auth utilities:** The admin guard is now a reusable decorator in `lms_platform/auth`, so future protected routes only need `@admin_required` without copy/paste.

## Next ideas before franchising/licensing

1. **Continue migrating** remaining routes out of `lms_automation/app.py` into new blueprints/services. Work feature-by-feature so behaviour stays identical.
2. **Introduce tenant-aware settings** (per competition branding, rules, notification targets) by extending `Config` or adding a tenant table the services consult.
3. **Add automated tests** targeting the service layer and blueprint responses—easier now that logic lives outside the view functions.
4. **Package notifications & scheduled jobs** into their own modules (e.g., `lms_platform/services/notifications.py`, `lms_platform/tasks/cron.py`) so Railway/worker processes can import them without the web UI.
5. **Document onboarding** (environment variables, migrations, deploy steps) next to the modular code to help future franchisees.

Because this lives in a separate directory, you can iterate freely without risking the production project. When you are happy with the modular approach, you can gradually port the same structure back into `LMS2` or treat this copy as the baseline for the franchised build.
