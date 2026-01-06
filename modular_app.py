"""WSGI entrypoint for the experimental modular build."""

from lms_platform import create_app

app = create_app()
