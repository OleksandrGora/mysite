from flask import Blueprint

converter_bp = Blueprint(
    'txt-to-docx-converter',
    __name__,
    template_folder='templates'
)

from . import routes
