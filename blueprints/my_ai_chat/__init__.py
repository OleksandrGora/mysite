from flask import Blueprint

my_ai_chat_bp = Blueprint(
    'my_ai_chat',
    __name__,
    template_folder='templates',
    url_prefix='/ai-chat'
)

from . import routes