# routes_cedula_ocr.py — Verificación documental de cédulas (PDF/JPG/PNG) con OCR opcional
# Nora · 2025-10-11
import io, os, re
from datetime import datetime, date
from typing import Optional, Tuple, Dict, Any
from flask import Blueprint, request, jsonify, Response
from werkzeug.utils import secure_filename

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    from PIL import Image
except Exception:
    Image = None

_pdf_backends = {}
try:
    import PyPDF2
    _pdf_backends['pypdf2'] = True
except Exception:
    pass

try:
    import pdfplumber
    _pdf_backends['pdfplumber'] = True
except Exception:
    pass

bp_cedula_ocr = Blueprint("cedula_ocr", __name__)

def _corsify(resp: Response) -> Response:
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key"
    return resp

DATE_RX = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b|\b(\d{2}/\d{2}/\d{4})\b")
NUM_RX  = re.compile(r"\b([A-Z0-9][A-Z0-9\-/]{6,20})\b", re.IGNORECASE)

def _norm(s: Optional[str]) -> str:
    return (s or '').strip()

def _parse_dates(text: str) -> Dict[str, Optional[str]]:
    candidates = []
    for m in DATE_RX.finditer(text or ''):
        raw = m.group(1) or m.group(2)
        iso = None
        if m.group(1):
            iso = raw
        else:
            try:
                d, mth, y = raw.split('/')
                iso = f"{y}-{mth}-{d}"
            except Exception:
                pass
        if iso:
            start = max(0, m.start()-32); end = min(len(text), m.end()+32)
            ctx = text[start:end].lower()
            candidates.append((iso, ctx))

    issue, expiry = None, None
    for iso, ctx in candidates:
        if any(k in ctx for k in ['caduc', 'vencim', 'expir']):
            expiry = iso
        if any(k in ctx for k in ['emisi', 'expedi', 'emisión', 'expedición']):
            issue = iso
    if not issue and candidates:
        issue = sorted(candidates, key=lambda x: x[0])[0][0]
    if not expiry and candidates:
        expiry = sorted(candidates, key=lambda x: x[0])[-1][0]
    return {'issue_date': issue, 'expiry_date': expiry}

def _parse_number(text: str) -> Optional[str]:
    for m in NUM_RX.finditer(text or ''):
        cand = m.group(1).strip().strip('.')
        if len(cand) >= 6:
            return cand
    return None

def _status_from_dates(issue_iso: Optional[str], expiry_iso: Optional[str]) -> str:
    if expiry_iso:
        try:
            exp = datetime.fromisoformat(expiry_iso).date()
            return 'vigente' if exp >= date.today() else 'caducada'
        except Exception:
            pass
    return 'no_consta'

def _extract_text_from_pdf(data: bytes, max_pages: int = 2) -> Tuple[str, str]:
    if _pdf_backends.get('pdfplumber'):
        try:
            text = ''
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for i, page in enumerate(pdf.pages[:max_pages]):
                    text += (page.extract_text() or '') + '\n'
            if text.strip():
                return ('pdfplumber', text)
        except Exception:
            pass
    if _pdf_backends.get('pypdf2'):
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            text = ''
            for i, page in enumerate(reader.pages[:max_pages]):
                try:
                    text += (page.extract_text() or '') + '\n'
                except Exception:
                    pass
            if text.strip():
                return ('pypdf2', text)
        except Exception:
            pass
    return ('none', '')

def _extract_text_from_image(data: bytes) -> Tuple[str, str]:
    if not (pytesseract and Image):
        return ('none', '')
    try:
        img = Image.open(io.BytesIO(data)).convert('RGB')
        txt = pytesseract.image_to_string(img, lang='spa+cat')
        return ('ocr', txt or '')
    except Exception:
        return ('none', '')

def _detect_ext(filename: str) -> str:
    return (filename or '').lower().rsplit('.', 1)[-1]

@bp_cedula_ocr.route('/api/legal/cedula/ocr', methods=['POST', 'OPTIONS'])
def cedula_ocr():
    if request.method == 'OPTIONS':
        return _corsify(Response(status=204))

    f = request.files.get('file')
    if not f or not f.filename:
        return _corsify(jsonify(ok=False, error='no_file')), 400

    filename = secure_filename(f.filename)
    data = f.read()
    ext = _detect_ext(filename)

    text, method = '', 'none'
    if ext in ('pdf',):
        m, t = _extract_text_from_pdf(data)
        method = m; text = t
        if not text:
            m2, t2 = _extract_text_from_image(data)
            if t2:
                method = m2; text = t2
    elif ext in ('jpg','jpeg','png','bmp','tif','tiff'):
        m, t = _extract_text_from_image(data); method = m; text = t
    else:
        m, t = _extract_text_from_image(data); method = m; text = t

    text_clean = (text or '').strip()
    number = _parse_number(text_clean)
    dates = _parse_dates(text_clean)
    status = _status_from_dates(dates.get('issue_date'), dates.get('expiry_date'))
    has_doc = bool(number or dates.get('expiry_date'))

    result = {
        'ok': True,
        'status': status,
        'has_doc': has_doc,
        'method': method,
        'data': {
            'number': number,
            'issue_date': dates.get('issue_date'),
            'expiry_date': dates.get('expiry_date'),
        },
        'text_preview': text_clean[:2000] if text_clean else None
    }
    return _corsify(jsonify(result))

@bp_cedula_ocr.route('/api/legal/cedula/ocr/validate', methods=['POST','OPTIONS'])
def cedula_ocr_validate():
    if request.method == 'OPTIONS':
        return _corsify(Response(status=204))
    body = request.get_json(silent=True) or {}
    number = _norm(body.get('number'))
    issue  = _norm(body.get('issue_date'))
    expiry = _norm(body.get('expiry_date'))
    status = _status_from_dates(issue, expiry) if expiry else ('no_consta' if not number else 'vigente')
    return _corsify(jsonify(ok=True, status=status, data={'number': number or None, 'issue_date': issue or None, 'expiry_date': expiry or None}))
