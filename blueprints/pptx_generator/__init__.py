from flask import Blueprint

pptx_generator_bp = Blueprint(
    'pptx_generator',
    __name__,
    template_folder='templates',
    url_prefix='/pptx-generator'
)

from . import routes
