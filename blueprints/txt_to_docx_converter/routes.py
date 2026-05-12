from flask import render_template, request, redirect, url_for, session, send_file, flash
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from google.auth.exceptions import GoogleAuthError
import subprocess
import tempfile
import os
import logging
import traceback
import re
import socket

from . import converter_bp

logger = logging.getLogger(__name__)

# Custom exception for re-authorization
class ReauthorizationNeeded(Exception):
    """Raised when token refresh fails and re-authorization is needed."""
    pass

PANDOC = '/usr/bin/pandoc'

REPLACEMENTS = [
    ('\\tg', '\\tan'),
    ('\\ctg', '\\cot'),
    ('\\tgh', '\\tanh'),
    ('\\arctg', '\\arctan'),
    ('\\approx', '='),
]


def _make_credentials():
    """Storjuje Credentials z danij sesiyi."""
    t = session['token']
    return Credentials(
        token=t.get('access_token'),
        refresh_token=t.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        scopes=t.get('scope', '').split()
    )


def is_token_valid():
    """
    Перевіряє токен. Якщо протік — пробує оновити через refresh_token.
    Повертає True якщо токен валідний.
    Викидає ReauthorizationNeeded якщо потрібен повторний логін.
    """
    t = session.get('token')
    if not t:
        logger.warning("[TOKEN_VALID] No token in session")
        raise ReauthorizationNeeded("No token in session")
    
    if not t.get('refresh_token'):
        logger.warning("[TOKEN_VALID] No refresh_token in session token")
        raise ReauthorizationNeeded("No refresh_token in session")
    
    if not t.get('access_token'):
        logger.warning("[TOKEN_VALID] No access_token in session token")
        raise ReauthorizationNeeded("No access_token in session")

    # Check if scope includes drive
    scope = t.get('scope', '')
    if isinstance(scope, list):
        scope = ' '.join(scope)
    
    if 'drive' not in scope.lower():
        logger.warning(f"[TOKEN_VALID] Token missing Drive scope. Scope: {scope}")
        raise ReauthorizationNeeded(f"Token missing Drive scope")

    creds = _make_credentials()

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                logger.info("[TOKEN_VALID] Attempting to refresh token...")
                creds.refresh(GoogleRequest())
                session['token']['access_token'] = creds.token
                session.modified = True
                logger.info("[TOKEN_VALID] Token refreshed successfully")
            except (GoogleAuthError, socket.error, ConnectionError, Exception) as e:
                logger.error("[TOKEN_VALID] Token refresh failed: %s", e)
                # Clear invalid token from session
                session.pop('token', None)
                raise ReauthorizationNeeded(f"Token refresh failed: {e}")
        else:
            logger.warning("[TOKEN_VALID] Token not valid and cannot refresh")
            raise ReauthorizationNeeded("Token not valid and cannot refresh")

    return True

## 4. Google Drive API клієнт
def get_drive_service():
    t = session.get('token')
    if not t:
        logger.error("[DRIVE_SERVICE] No token in session!")
        raise ReauthorizationNeeded("No token in session")
    
    # Normalize scope to list
    scope = t.get('scope', '')
    if isinstance(scope, str):
        scopes = scope.split()
    elif isinstance(scope, list):
        scopes = scope
    else:
        scopes = []
    
    # Ensure Drive scope is included
    drive_scope = 'https://www.googleapis.com/auth/drive'
    if drive_scope not in scopes:
        scopes.append(drive_scope)
    
    creds = Credentials(
        token=t.get('access_token'),
        refresh_token=t.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        scopes=scopes
    )
    
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
                session['token']['access_token'] = creds.token
                session.modified = True
            except (GoogleAuthError, socket.error, ConnectionError, Exception) as e:
                logger.error("[DRIVE_SERVICE] Failed to refresh token: %s", e)
                session.pop('token', None)
                raise ReauthorizationNeeded(f"Token refresh failed: {e}")
        else:
            logger.error("[DRIVE_SERVICE] Credentials not valid and cannot refresh")
            session.pop('token', None)
            raise ReauthorizationNeeded("Credentials not valid and cannot refresh")
    
    return build('drive', 'v3', credentials=creds)

## 5. Створення або отримання папки 'Openclaw' в Drive
def get_or_create_folder(drive, folder_name):
    try:
        results = drive.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id)'
        ).execute()
        files = results.get('files', [])
        if files:
            return files[0]['id']
        folder = drive.files().create(
            body={'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'},
            fields='id'
        ).execute()
        return folder['id']
    except Exception as e:
        error_str = str(e)
        if "invalid_grant" in error_str or "unauthorized_client" in error_str or "TOKEN_EXPIRED" in error_str:
            logger.warning("Token expired in get_or_create_folder, raising ReauthorizationNeeded")
            raise ReauthorizationNeeded(f"Token expired in Drive API call: {e}")
        raise

## 1. Очищення тексту, заміна LaTeX-команд
def preprocess(text):
    # Нормалізуємо закінчення рядків
    text = text.replace('\r\n', '\n')
    text = text.replace('\r', '\n')

    for old, new in REPLACEMENTS:
        text = text.replace(old, new)

    # Прибираємо вкладені \text всередині \text
    text = re.sub(r'\\text\{([^}]*?)\\cdot\\text\{([^}]*?)\}\}', r'\\text{\1·\2}', text)

    # Прибираємо жирний текст всередині таблиць
    text = re.sub(r'(\|[^|\n]*)\*\*([^*|\n]*)\*\*([^|\n]*\|)', r'\1\2\3', text)

    # Екрануємо зірочки-виноски після цифр
    text = re.sub(r'(\d)\*\*', r'\1', text)
    text = re.sub(r'(\d)\*', r'\1', text)

    # Заміна тире на мінус (після обробки таблиць)
    text = text.replace('\u2012', '-')
    text = text.replace('\u2013', '-')
    text = text.replace('\u2014', '-')
    text = text.replace('\u2015', '-')
    text = text.replace('\u2212', '-')

    # Виправляємо вкладені \text{\cdot\text{}}
    text = text.replace('\\cdot\\text{м}', '·м')
    text = text.replace('\\cdot\\text{с}', '·с')
    text = text.replace('\\cdot\\text{кг}', '·кг')

    text = re.sub(r'^\s*---+\s*$', '\n\n\u00A0\n\n', text, flags=re.MULTILINE)

    # Додаємо порожній рядок перед таблицями
    text = re.sub(r'([^\n|])\n(\|)', r'\1\n\n\2', text)

    text = re.sub(r'([^\n])\n(- )', r'\1\n\n\2', text)
    text = re.sub(r'([^\n])\n(\* )', r'\1\n\n\2', text)
    text = re.sub(r'([^\n])\n(\d+\. )', r'\1\n\n\2', text)
    text = re.sub(r'^\*\s+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'([^\n])\n(\$)', r'\1\n\n\2', text)
    text = re.sub(r'(\*\*[^*]+\*\*\.?)\n(\$)', r'\1\n\n\2', text)

    text = text.replace('\u2248', '=')
    text = text.replace('\u2245', '=')
    text = text.replace('\u224A', '=')

    text = re.sub(r'\[cite_start\]', '', text)
    text = re.sub(r'\[cite_end\]', '', text)
    text = re.sub(r'\[cite:\s*\d+(?:\s*,\s*\d+)*\]', '', text)

    text = re.sub(r'\\mathbf\{([^}]+)\}', r'\1', text)

    before_replace = text
    text = re.sub(r'(\d),(\d+)(?!\d)', r'\1.\2', text)
    after_replace = text
    if before_replace != after_replace:
        logger.info("Comma replacement applied. Changes made.")
    else:
        unmatched = re.findall(r'\d,\d+', text)
        if unmatched:
            logger.warning("Found unmatched decimal commas: %s", unmatched[:10])

    text = re.sub(r'\\\[', '$$', text)
    text = re.sub(r'\\\]', '$$', text)

    return text


def parse_pandoc_warnings(stderr_text):
    warnings = []
    for line in stderr_text.splitlines():
        line = line.strip()
        if line:
            warnings.append(line)
    return warnings

## 2. Виклик pandoc для конвертації MD → DOCX
def run_pandoc(src, dst):
    try:
        result = subprocess.run(
            [PANDOC, src,
             '-f', 'markdown+tex_math_single_backslash+tex_math_dollars',
             '-t', 'docx',
             f'--reference-doc={os.path.dirname(__file__)}/reference.docx',
             '-o', dst],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return False, [], f"Pandoc не знайдено за шляхом: {PANDOC}"
    except subprocess.TimeoutExpired:
        return False, [], "Pandoc перевищив ліміт часу (120 с)"
    except Exception as e:
        return False, [], f"Помилка запуску Pandoc: {e}"

    warnings = parse_pandoc_warnings(result.stderr) if result.stderr else []

    if result.returncode != 0:
        return False, warnings, result.stderr or "Невідома помилка Pandoc"

    if not os.path.exists(dst):
        return False, warnings, "Pandoc завершився без помилок, але файл не створено"

    return True, warnings, None

## 3. Пост-обробка стилів, таблиць
def postprocess_docx(path):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING

    doc = Document(path)

    for para in doc.paragraphs:
        for run in para.runs:
            run.bold = False
            run.italic = False

    body = doc.element.body
    for bm in body.findall('.//' + qn('w:bookmarkStart')):
        bm.getparent().remove(bm)
    for bm in body.findall('.//' + qn('w:bookmarkEnd')):
        bm.getparent().remove(bm)

    section = doc.sections[0]
    section.left_margin = Cm(2)
    section.right_margin = Cm(1)

    def set_table_width(table):
        tbl = table._tbl
        tblPr = tbl.find(qn('w:tblPr'))
        if tblPr is None:
            tblPr = OxmlElement('w:tblPr')
            tbl.insert(0, tblPr)

        for old in tblPr.findall(qn('w:tblW')):
            tblPr.remove(old)
        for old in tblPr.findall(qn('w:tblLayout')):
            tblPr.remove(old)

        tblW = OxmlElement('w:tblW')
        tblW.set(qn('w:w'), '5000')
        tblW.set(qn('w:type'), 'pct')
        tblPr.append(tblW)

        tblLayout = OxmlElement('w:tblLayout')
        tblLayout.set(qn('w:type'), 'autofit')
        tblPr.append(tblLayout)

        for tblGrid in tbl.findall(qn('w:tblGrid')):
            tbl.remove(tblGrid)

        for tc in tbl.findall('.//' + qn('w:tc')):
            tcPr = tc.find(qn('w:tcPr'))
            if tcPr is not None:
                for tcW in tcPr.findall(qn('w:tcW')):
                    tcW.set(qn('w:w'), '0')
                    tcW.set(qn('w:type'), 'auto')

    def set_cell_border(cell):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcBorders = OxmlElement('w:tcBorders')
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:val'), 'single')
            el.set(qn('w:sz'), '4')
            el.set(qn('w:color'), '000000')
            tcBorders.append(el)
        tcPr.append(tcBorders)

    def set_cell_margin(cell):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcMar = OxmlElement('w:tcMar')
        for side in ('top', 'left', 'bottom', 'right'):
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:w'), '11')
            el.set(qn('w:type'), 'dxa')
            tcMar.append(el)
        tcPr.append(tcMar)

    for table in doc.tables:
        set_table_width(table)
        for row in table.rows:
            for cell in row.cells:
                set_cell_border(cell)
                set_cell_margin(cell)
                for para in cell.paragraphs:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    pf = para.paragraph_format
                    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
                    pf.first_line_indent = Pt(0)
                    pf.space_before = Pt(0)
                    pf.space_after = Pt(0)
                    for run in para.runs:
                        run.font.name = 'Arial'
                        run.font.size = Pt(12)
                        run.font.color.rgb = RGBColor(0, 0, 0)
                        run.bold = False
                        run.italic = False

    doc.save(path)


@converter_bp.route('/converter', methods=['GET', 'POST'])
def converter():
    if 'email' not in session:
        return redirect(url_for('login'))

    try:
        is_token_valid()
    except ReauthorizationNeeded as e:
        logger.warning(f"[CONVERTER] Reauthorization needed: {e}")
        session.clear()
        # Automatically redirect to OAuth flow
        return redirect(url_for('login'))

    if request.method == 'POST':
        text_input = request.form.get('text_input', '').strip()
        f = request.files.get('file')

        if not text_input and not f:
            return render_template('txt_to_docx_converter/index-converter.html', error='Please enter text or select a file', text_input=text_input)

        if text_input and f and f.filename:
            return render_template(
                'txt_to_docx_converter/index-converter.html',
                error='Both text and file provided. Text input will be used for conversion.',
                warnings=[],
                filename=f.filename,
                text_input=text_input
            )

        if text_input:
            content = text_input
            filename = 'text_input.md'
        else:
            try:
                content = f.read().decode('utf-8')
            except UnicodeDecodeError:
                try:
                    f.seek(0)
                    content = f.read().decode('cp1251')
                except Exception as e:
                    return render_template('txt_to_docx_converter/index-converter.html', error=f'Failed to read file: {e}')
            filename = f.filename

        content = preprocess(content)
        logger.error("PREPROCESSED (first 500): %s", repr(content[:500]))

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'input.md')
            dst = os.path.join(tmp, 'output.docx')

            with open(src, 'w', encoding='utf-8') as out:
                out.write(content)

            success, warnings, error_text = run_pandoc(src, dst)

            if not success:
                logger.error("Pandoc failed: %s", error_text)
                return render_template(
                    'txt_to_docx_converter/index-converter.html',
                    error=error_text,
                    warnings=warnings,
                    filename=filename,
                    text_input=text_input
                )

            postprocess_docx(dst)

            if request.form.get('action') == 'download':
                from io import BytesIO
                output_filename = filename.rsplit('.', 1)[0] + '.docx'
                with open(dst, 'rb') as docx_file:
                    file_data = docx_file.read()
                return send_file(
                    BytesIO(file_data),
                    mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    as_attachment=True,
                    download_name=output_filename
                )

            try:
                logger.info("Starting Google Drive upload...")
                drive = get_drive_service()
                logger.info("Drive service created successfully")
                folder_id = get_or_create_folder(drive, 'Openclaw')
                logger.info("Folder created/retrieved: %s", folder_id)
                file_meta = {
                    'name': filename.rsplit('.', 1)[0],
                    'mimeType': 'application/vnd.google-apps.document',
                    'parents': [folder_id],
                }
                media = MediaFileUpload(
                    dst,
                    mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
                uploaded = drive.files().create(
                    body=file_meta,
                    media_body=media,
                    fields='id,webViewLink'
                ).execute()
                logger.info("File uploaded successfully: %s", uploaded.get('webViewLink'))

            except ReauthorizationNeeded as e:
                logger.warning(f"[CONVERTER] Reauthorization needed during Drive upload: {e}")
                session.clear()
                # Automatically redirect to OAuth flow for re-authorization
                return redirect(url_for('login'))
            except Exception as e:
                error_msg = str(e)
                logger.error("Google Drive upload failed: %s", traceback.format_exc())
                logger.error("Error type: %s", type(e).__name__)
                logger.error("Error details: %s", error_msg)

                # Check if it's a token-related error
                if any(x in error_msg for x in ["TOKEN_EXPIRED", "invalid_grant", "unauthorized_client", "oauth2.googleapis.com"]):
                    logger.warning("Token expired or unauthorized, clearing session and redirecting to login")
                    session.clear()
                    return redirect(url_for('login'))
                
                return render_template(
                    'txt_to_docx_converter/index-converter.html',
                    error=f'Файл конвертовано, але помилка завантаження на Drive: {error_msg}',
                    warnings=warnings,
                    filename=filename,
                    text_input=text_input
                )

            return render_template(
                'txt_to_docx_converter/index-converter.html',
                link=uploaded.get('webViewLink'),
                warnings=warnings,
                filename=filename,
                text_input=text_input
            )

    return render_template('txt_to_docx_converter/index-converter.html')
