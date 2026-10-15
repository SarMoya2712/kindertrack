"""Request security shared by all admin modules."""
import hmac
import secrets
from flask import abort, request, session


def csrf_token():
    if not isinstance(session.get('csrf'), str) or not session['csrf']:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def init_app(app):
    @app.before_request
    def protect_requests():
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            supplied = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token', '')
            expected = session.get('csrf')
            # Bytes support arbitrary Unicode; malformed input must yield 400, not TypeError/500.
            if (not isinstance(expected, str) or not supplied or len(supplied) > 128
                or not hmac.compare_digest(expected.encode('utf-8'), supplied.encode('utf-8'))):
                abort(400, description='csrf_error')

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(self), microphone=()'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
            "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store, private'
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response
