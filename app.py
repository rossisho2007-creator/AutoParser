"""
AutoInput Pro - Template-Based Document Scanner
Upload your form template → System knows exactly where to read
"""

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
import pandas as pd
import os, re, json, sqlite3
from datetime import datetime
from io import BytesIO
from functools import wraps

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'autopro-2024'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['TEMPLATE_FOLDER'] = 'templates_forms'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMPLATE_FOLDER'], exist_ok=True)

USERS = {
    'hr': {'password': 'hr123', 'role': 'hr', 'name': 'HR Officer'},
    'cabang1': {'password': 'cabang123', 'role': 'cabang', 'name': 'Jakarta South'},
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def hr_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'hr': flash('HR only', 'danger'); return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def cabang_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'cabang': flash('Cabang only', 'danger'); return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_db():
    conn = sqlite3.connect('data.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        npk TEXT, kpm_id TEXT, nama_lengkap TEXT,
        loan_amount REAL, down_payment REAL, total_ar REAL,
        tanggal_mulai TEXT, tenure_months INTEGER,
        loan_type TEXT, interest_rate REAL, cabang TEXT,
        principal REAL, total_interest REAL,
        monthly_installment REAL, outstanding_balance REAL,
        status_approval TEXT DEFAULT 'Pending',
        hr_notes TEXT, submitted_by TEXT, approved_by TEXT,
        document_source TEXT, ocr_confidence REAL,
        submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Template storage
    conn.execute('''CREATE TABLE IF NOT EXISTS form_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        image_path TEXT,
        fields_json TEXT,
        created_date DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    conn.close()

init_db()

# ═══════════════════════════════════
# OCR ENGINE
# ═══════════════════════════════════

def enhance_image(image_path):
    """Enhance image for better OCR"""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        
        img = Image.open(image_path)
        
        # Resize if too small
        if img.width < 1000:
            ratio = 2000 / img.width
            img = img.resize((2000, int(img.height * ratio)), Image.LANCZOS)
        
        # Convert to grayscale
        img = img.convert('L')
        
        # Enhance
        img = ImageEnhance.Contrast(img).enhance(2.5)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        
        # Threshold
        img = img.point(lambda x: 0 if x < 150 else 255)
        
        return img
    except Exception as e:
        print(f"Enhance error: {e}")
        return None

def ocr_with_template(image_path, template_fields=None):
    """
    If template is provided, reads specific regions.
    Otherwise, does full-page OCR with fuzzy matching.
    """
    try:
        import pytesseract
        from PIL import Image
        
        img = enhance_image(image_path)
        if img is None:
            return '', {}
        
        # If we have template fields, read specific regions
        if template_fields:
            results = {}
            for field_name, region in template_fields.items():
                # Crop the specific region
                x, y, w, h = region['x'], region['y'], region['w'], region['h']
                cropped = img.crop((x, y, x + w, y + h))
                
                # OCR just this region
                text = pytesseract.image_to_string(cropped, lang='eng+ind', config='--psm 7')
                results[field_name] = text.strip()
            return json.dumps(results), results
        
        # Full page OCR
        text = pytesseract.image_to_string(img, lang='eng+ind', config='--psm 6')
        return text, None
        
    except Exception as e:
        print(f"OCR error: {e}")
        return '', None

def fuzzy_parse(text):
    """Fuzzy parsing with common OCR error correction"""
    if not text: return {}, {}
    
    # Fix common OCR errors
    corrections = {
        'KPI': 'KPM', 'Mula': 'Mulai', 'Santo': 'Santoso',
        'NPKOO': 'NPK00', '0O': '00', 'O1': '01'
    }
    for wrong, correct in corrections.items():
        text = text.replace(wrong, correct)
    
    result, conf = {}, {}
    lines = text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # NPK
        if 'npk' not in result:
            m = re.search(r'(?:NPK|NOPEK)[:\s]*([A-Za-z0-9\-]{3,20})', line, re.I)
            if m: result['npk'] = m.group(1); conf['npk'] = 80; continue
        
        # KPM ID
        if 'kpm_id' not in result:
            m = re.search(r'(?:KPM|KPI)[:\s\-]*(\d{10,})', line, re.I)
            if not m: m = re.search(r'\b(\d{14})\b', line)
            if m: result['kpm_id'] = m.group(1); conf['kpm_id'] = 85; continue
        
        # Name
        if 'nama_lengkap' not in result:
            m = re.search(r'(?:Nama|Name)[:\s]*([A-Za-z\s\.]{3,50})$', line, re.I)
            if m: result['nama_lengkap'] = m.group(1).strip(); conf['nama_lengkap'] = 80; continue
        
        # Loan Amount
        if 'loan_amount' not in result:
            m = re.search(r'(?:Loan|Pinjaman|AF|Plafon)[:\s]*[Rp\.\s]*([\d,\.]{6,})', line, re.I)
            if not m: m = re.search(r'Rp\.?\s*([\d,\.]{6,})', line)
            if m:
                try:
                    result['loan_amount'] = float(re.sub(r'[^\d]', '', m.group(1)))
                    conf['loan_amount'] = 75
                except: pass
                continue
        
        # Down Payment
        if 'down_payment' not in result:
            m = re.search(r'(?:DP|Down|Uang\s*Muka)[:\s]*[Rp\.\s]*([\d,\.]{5,})', line, re.I)
            if m:
                try:
                    result['down_payment'] = float(re.sub(r'[^\d]', '', m.group(1)))
                    conf['down_payment'] = 75
                except: pass
                continue
        
        # Tenure
        if 'tenure_months' not in result:
            m = re.search(r'(?:Tenure|Tenor|Jangka)[:\s]*(\d{1,3})', line, re.I)
            if m: result['tenure_months'] = int(m.group(1)); conf['tenure_months'] = 85; continue
        
        # Interest Rate
        if 'interest_rate' not in result:
            m = re.search(r'(?:Interest|Bunga|Rate)[:\s]*([\d.,]{1,5})\s*%?', line, re.I)
            if m:
                try:
                    v = float(m.group(1).replace(',', '.'))
                    if v > 1: v /= 100
                    result['interest_rate'] = v; conf['interest_rate'] = 80
                except: pass
                continue
        
        # Date
        if 'tanggal_mulai' not in result:
            m = re.search(r'(?:Tanggal|Tgl|Date)[:\s]*(\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4})', line, re.I)
            if m: result['tanggal_mulai'] = m.group(1); conf['tanggal_mulai'] = 85; continue
        
        # Branch
        if 'cabang' not in result:
            m = re.search(r'(?:Cabang|Branch|Kantor)[:\s]*([A-Za-z\s\-]{3,40})$', line, re.I)
            if m: result['cabang'] = m.group(1).strip(); conf['cabang'] = 80; continue
        
        # Loan Type
        if 'loan_type' not in result:
            m = re.search(r'(?:Type|Jenis|Produk)[:\s]*(Regular|Fleet|Siap\s*Dana|KINTO)', line, re.I)
            if m: result['loan_type'] = m.group(1); conf['loan_type'] = 90; continue
        
        # Total AR
        if 'total_ar' not in result:
            m = re.search(r'(?:Total\s*AR|AR|Piutang)[:\s]*[Rp\.\s]*([\d,\.]{5,})', line, re.I)
            if m:
                try:
                    result['total_ar'] = float(re.sub(r'[^\d]', '', m.group(1)))
                    conf['total_ar'] = 70
                except: pass
                continue
    
    return result, conf

def calculate(data):
    try:
        loan = float(data.get('loan_amount', 0))
        dp = float(data.get('down_payment', 0))
        tenor = int(data.get('tenure_months', 12))
        rate = float(data.get('interest_rate', 0.05))
        if loan <= 0 or tenor <= 0: return {}
        principal = loan - dp
        total_int = principal * rate * (tenor / 12)
        monthly = (principal + total_int) / tenor
        return {
            'principal': round(principal),
            'total_interest': round(total_int),
            'monthly_installment': round(monthly),
            'outstanding_balance': round(principal + total_int)
        }
    except: return {}

# ═══════════════════════════════════
# ROUTES
# ═══════════════════════════════════

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username', '').lower().strip()
        p = request.form.get('password', '')
        if u in USERS and USERS[u]['password'] == p:
            session['user'] = u; session['role'] = USERS[u]['role']; session['name'] = USERS[u]['name']
            return redirect(url_for('hr_dashboard') if USERS[u]['role'] == 'hr' else url_for('cabang_upload'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/hr')
@hr_required
def hr_dashboard():
    conn = get_db()
    stats = {
        'pending': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Pending'").fetchone()[0],
        'approved': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Approved'").fetchone()[0],
        'total': conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
    }
    recent = [dict(r) for r in conn.execute("SELECT * FROM submissions ORDER BY submitted_date DESC LIMIT 100").fetchall()]
    conn.close()
    return render_template('hr_dashboard.html', stats=stats, recent=recent)

@app.route('/hr/review/<int:id>', methods=['GET', 'POST'])
@hr_required
def hr_review(id):
    conn = get_db()
    if request.method == 'POST':
        action = request.form.get('action')
        notes = request.form.get('hr_notes', '')
        if action == 'approve':
            conn.execute("UPDATE submissions SET status_approval='Approved', hr_notes=?, approved_by=? WHERE id=?",
                         (notes, session.get('name', 'HR'), id))
        elif action == 'reject':
            conn.execute("UPDATE submissions SET status_approval='Rejected', hr_notes=? WHERE id=?", (notes, id))
        conn.commit(); conn.close()
        return redirect(url_for('hr_dashboard'))
    sub = dict(conn.execute("SELECT * FROM submissions WHERE id=?", (id,)).fetchone())
    conn.close()
    return render_template('hr_review.html', submission=sub)

# TEMPLATE UPLOAD - Upload your form here
@app.route('/hr/upload-template', methods=['GET', 'POST'])
@hr_required
def upload_template():
    if request.method == 'POST':
        file = request.files.get('template')
        name = request.form.get('name', 'Default Template')
        
        if file and file.filename:
            path = os.path.join(app.config['TEMPLATE_FOLDER'], secure_filename(file.filename))
            file.save(path)
            
            # Store in database
            conn = get_db()
            conn.execute("INSERT INTO form_templates (name, description, image_path) VALUES (?, ?, ?)",
                        (name, 'Custom form template', path))
            conn.commit()
            
            template_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()
            
            flash(f'✅ Template "{name}" uploaded! Now you can define fields.', 'success')
            return redirect(url_for('define_fields', template_id=template_id))
    
    templates = []
    conn = get_db()
    for r in conn.execute("SELECT * FROM form_templates ORDER BY created_date DESC").fetchall():
        templates.append(dict(r))
    conn.close()
    
    return render_template('upload_template.html', templates=templates)

# DEFINE FIELDS - Click on the template image to set where fields are
@app.route('/hr/define-fields/<int:template_id>')
@hr_required
def define_fields(template_id):
    conn = get_db()
    template = dict(conn.execute("SELECT * FROM form_templates WHERE id=?", (template_id,)).fetchone())
    conn.close()
    return render_template('define_fields.html', template=template)

@app.route('/cabang')
@cabang_required
def cabang_upload(): return render_template('cabang_upload.html')

@app.route('/cabang/scan', methods=['GET', 'POST'])
@cabang_required
def cabang_scan():
    parsed, confidence, raw_text, calculations = None, None, None, None
    debug = {}
    
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            debug['file'] = file.filename
            
            # OCR with enhancement
            raw_text, _ = ocr_with_template(path)
            debug['chars'] = len(raw_text) if raw_text else 0
            
            if raw_text:
                parsed, confidence = fuzzy_parse(raw_text)
                if parsed:
                    calculations = calculate(parsed)
                    flash(f'✅ Found {len(parsed)} fields!', 'success')
                else:
                    flash('⚠️ No patterns found. Is this the right form?', 'warning')
            else:
                flash('❌ No text detected. Ensure good lighting.', 'danger')
    
    return render_template('cabang_scan.html', parsed=parsed, confidence=confidence,
                         raw_text=raw_text, calculations=calculations, debug=debug)

@app.route('/cabang/form')
@cabang_required
def cabang_form(): return render_template('cabang_form.html')

@app.route('/api/submit', methods=['POST'])
@cabang_required
def submit():
    try:
        data = request.json
        calc = calculate(data)
        if not calc: return jsonify({'status': 'error'}), 400
        data.update(calc)
        conn = get_db()
        conn.execute('''INSERT INTO submissions 
            (npk,kpm_id,nama_lengkap,loan_amount,down_payment,total_ar,
             tanggal_mulai,tenure_months,loan_type,interest_rate,cabang,
             principal,total_interest,monthly_installment,outstanding_balance,
             submitted_by,document_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (data.get('npk'), data.get('kpm_id'), data.get('nama_lengkap'),
             data.get('loan_amount'), data.get('down_payment'), data.get('total_ar'),
             data.get('tanggal_mulai'), data.get('tenure_months'), data.get('loan_type'),
             data.get('interest_rate'), data.get('cabang', session.get('name')),
             data.get('principal'), data.get('total_interest'),
             data.get('monthly_installment'), data.get('outstanding_balance'),
             session.get('name'), data.get('document_source', 'Cabang')))
        conn.commit(); conn.close()
        return jsonify({'status': 'success', 'calculations': calc})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/approve/<int:id>', methods=['POST'])
@hr_required
def approve(id):
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Approved' WHERE id=?", (id,))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/reject/<int:id>', methods=['POST'])
@hr_required
def reject(id):
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Rejected' WHERE id=?", (id,))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/hr/export-excel')
@hr_required
def hr_export():
    conn = get_db()
    df = pd.read_sql_query("SELECT * FROM submissions ORDER BY submitted_date DESC", conn)
    conn.close()
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as w:
        df.to_excel(w, sheet_name='Submissions', index=False)
    output.seek(0)
    return send_file(output, download_name=f'HR_Report_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx')

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('hr_dashboard') if session.get('role') == 'hr' else url_for('cabang_upload'))
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
