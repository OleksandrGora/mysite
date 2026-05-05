from flask import Blueprint

ocr_processor_bp = Blueprint(
    'ocr_processor',
    __name__,
    template_folder='templates',
    url_prefix='/ocr-processor'
)

from . import routes
