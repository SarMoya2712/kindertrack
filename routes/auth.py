"""HTTP handlers; credential checks and session lifecycle live in admin_auth."""
import functools
from flask import Blueprint, request, redirect, url_for, render_template, flash, session
from services.admin_auth import authenticate, current_admin, logout_admin
from services.localization import t, LANGUAGES

bp = Blueprint('auth', __name__)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_admin() is None:
            if request.path.startswith('/api/'):
                return {'message': t('session_expired'), 'status':'error'}, 401
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapped


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_admin() is not None:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        outcome = authenticate(request.form.get('email', ''), request.form.get('password', ''), request.remote_addr)
        if outcome == 'success':
            return redirect(url_for('main.dashboard'))
        flash(t(outcome), 'error')
        if outcome == 'rate_limit':
            return render_template('login.html'), 429
    return render_template('login.html')


@bp.post('/logout')
@login_required
def logout():
    logout_admin()
    return redirect(url_for('auth.login'))


@bp.post('/language/<lang>')
def language(lang):
    session['lang'] = lang if lang in LANGUAGES else 'ru'
    target = request.form.get('next', '/')
    return redirect(target if target.startswith('/') and not target.startswith('//') and '\\' not in target else '/')
