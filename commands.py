"""Administrative commands; no public registration or default credentials."""
import click
from flask import current_app
from services.admin_auth import create_admin


def init_app(app):

    @app.cli.command('enroll-photos')
    def enroll_photos_command():
        """Generate missing embeddings without replacing existing photo files."""
        enrolled, rejected = current_app.extensions['face'].enroll_existing()
        click.echo(f'Enrolled: {enrolled}; rejected: {len(rejected)}')
        for photo_id, reason in rejected:
            click.echo(f'Photo {photo_id}: {reason}')
        if rejected:
            raise click.ClickException('Rejected photos were preserved; replace them with valid portraits.')

    @app.cli.command('create-admin')
    @click.option('--email', prompt=True)
    @click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin_command(email, password):
        """Create the single administrator (password is prompted without echo)."""
        try:
            create_admin(email, password)
        except ValueError as error:
            raise click.ClickException(str(error)) from None
        click.echo('Administrator created.')

    @app.cli.command('check-attendance')
    def check_attendance_command():
        """Report recognized scans without attendance; never backfill or change data."""
        from database import db
        from services.attendance_review import missing_attendance_statement
        rows = db.session.execute(missing_attendance_statement().order_by('id')).all()
        click.echo(f'Recognized scans without attendance: {len(rows)}')
        for row in rows:
            click.echo(f'Scan {row.id}; child {row.child_id}; local date {row.local_date}; status {row.status}')
        click.echo('Read-only check. No attendance or historical amounts were created.')
