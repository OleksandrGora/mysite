from flask import Blueprint

converter_bp = Blueprint(
    'txt-to-docx-converter',
    __name__,
)

from . import routes
