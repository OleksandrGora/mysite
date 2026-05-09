from flask import Blueprint

moodle_session_bp = Blueprint(
    'moodle_session',
    __name__,
    template_folder='templates',
    url_prefix='/moodle-session'
)

from . import routes
