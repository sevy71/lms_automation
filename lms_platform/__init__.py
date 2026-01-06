from flask import Flask

from .config import Config
from .extensions import db, migrate


def create_app(config_class: type[Config] = Config) -> Flask:
    """Application factory for the modular LMS experiment."""
    app = Flask(
        __name__,
        template_folder='../lms_automation/templates',
        static_folder='../lms_automation/static'
    )
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)

    # Defer blueprint imports until runtime to avoid circular dependencies
    from .blueprints.public.routes import bp as public_bp
    from .blueprints.admin.routes import bp as admin_bp
    from .blueprints.api.routes import bp as api_bp
    from .blueprints.auth.routes import bp as auth_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    # Legacy aliases so templates that call historic endpoint names keep working
    app.add_url_rule('/', endpoint='index', view_func=app.view_functions['public.index'])
    app.add_url_rule('/register', endpoint='player_registration', view_func=app.view_functions['public.player_registration'])
    app.add_url_rule('/rules', endpoint='rules', view_func=app.view_functions['public.rules'])
    app.add_url_rule('/admin/dashboard', endpoint='admin_dashboard', view_func=app.view_functions['admin.dashboard'])

    return app


__all__ = ["create_app"]
