from flask import Blueprint

doc_generator_bp = Blueprint(
    'doc_generator',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/doc-generator'
)

from . import routes
