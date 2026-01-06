from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

bp = Blueprint('auth', __name__, url_prefix='/admin')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        expected = current_app.config.get('ADMIN_PASSWORD')
        if password == expected:
            session['admin_authenticated'] = True
            flash('Welcome back!', 'success')
            next_url = request.args.get('next') or url_for('admin.dashboard')
            return redirect(next_url)
        flash('Incorrect password. Please try again.', 'danger')
    return render_template('admin_login.html')


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    session.pop('admin_authenticated', None)
    flash('You are now logged out.', 'info')
    return redirect(url_for('auth.login'))
