"""
AutoInput Pro - Dual Role System
🔵 Cabang: Upload only (can't see submissions)
🔴 HR: Review & approve (can't upload)
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
app.config['SECRET_KEY'] = 'autopro-2024-secure'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ═══════════════════════════════════
# SIMPLE AUTH (for demo - use Azure AD in production)
# ═══════════════════════════════════
USERS = {
    'hr': {'password': 'hr123', 'role': 'hr', 'name': 'HR Officer'},
    'cabang1': {'password': 'cabang123', 'role': 'cabang', 'name': 'Cabang Jakarta South'},
    'cabang2': {'password': 'cabang123', 'role': 'cabang', 'name': 'Cabang Bandung'},
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def hr_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session or session.get('role') != 'hr':
            flash('⛔ Access denied. HR only.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def cabang_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session or session.get('role') != 'cabang':
            flash('⛔ Access denied.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ═══════════════════════════════════
# DATABASE
# ═══════════════════════════════════
def get_db():
    conn = sqlite3.connect('data.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        npk TEXT, kpm_id TEXT UNIQUE, nama_lengkap TEXT,
        loan_amount REAL, down_payment REAL, total_ar REAL,
        tanggal_mulai TEXT, tenure_months INTEGER,
        loan_type TEXT, interest_rate REAL, cabang TEXT,
        principal REAL, total_interest REAL,
        monthly_installment REAL, outstanding_balance REAL,
        status_approval TEXT DEFAULT 'Pending',
        submitted_by TEXT, approved_by TEXT,
        document_source TEXT, ocr_confidence REAL,
        submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_date DATETIME
    )''')
    conn.commit()
    conn.close()

init_db()

# ═══════════════════════════════════
# OCR + CALCULATIONS (same as before)
# ═══════════════════════════════════
COLUMN_PATTERNS = {
    'npk': [r'(?:NPK|No\.?\s*PK)[\s:]*([A-Za-z0-9\-]+)'],
    'kpm_id': [r'(?:KPM)[\s:\-]*(\d{10,})'],
    'nama_lengkap': [r'(?:Nama|Customer|Debitur)[\s:]*([A-Za-z\s\.]{3,50}?)(?:\n|$)'],
    'loan_amount': [r'(?:Loan|Pinjaman|AF|Plafon)[\s:]*[Rp\.\s]*([\d,\.]+)'],
    'down_payment': [r'(?:DP|Down\s*Payment|Uang\s*Muka)[\s:]*[Rp\.\s]*([\d,\.]+)'],
    'total_ar': [r'(?:Total\s*AR|AR|Piutang)[\s:]*[Rp\.\s]*([\d,\.]+)'],
    'tanggal_mulai': [r'(?:Tanggal|Tgl|Date)[\s:]*(\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4})'],
    'tenure_months': [r'(?:Tenure|Tenor|Jangka)[\s:]*(\d+)'],
    'loan_type': [r'(?:Type|Jenis|Produk)[\s:]*(Regular|Fleet|Siap\s*Dana|KINTO)'],
    'interest_rate': [r'(?:Interest|Bunga|Rate)[\s:]*([\d.,]+)\s*%?'],
    'cabang': [r'(?:Cabang|Branch|Kantor)[\s:]*([A-Za-z\s\-]{3,40}?)(?:\n|$)'],
}

def ocr_scan_image(image_path):
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path).convert('L')
        return pytesseract.image_to_string(img, lang='eng+ind').strip()
    except:
        return None

def smart_parse(text):
    result, conf = {}, {}
    for field, patterns in COLUMN_PATTERNS.items():
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE|re.MULTILINE)
            if m:
                v = m.group(1).strip()
                if field in ['loan_amount','down_payment','total_ar']:
                    v = float(re.sub(r'[^\d]','',v))
                elif field == 'tenure_months': v = int(re.sub(r'[^\d]','',v))
                elif field == 'interest_rate':
                    v = float(v.replace(',','.'))
                    if v > 1: v /= 100
                result[field] = v; conf[field] = 85; break
    return result, conf

def calculate(data):
    try:
        loan = float(data.get('loan_amount',0))
        dp = float(data.get('down_payment',0))
        tenor = int(data.get('tenure_months',12))
        rate = float(data.get('interest_rate',0.05))
        principal = loan - dp
        total_int = principal * rate * (tenor/12)
        monthly = (principal + total_int) / tenor if tenor > 0 else 0
        return {'principal':round(principal),'total_interest':round(total_int),
                'monthly_installment':round(monthly),'outstanding_balance':round(principal+total_int)}
    except: return {}

# ═══════════════════════════════════
# ROUTES
# ═══════════════════════════════════

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').lower()
        password = request.form.get('password','')
        
        if username in USERS and USERS[username]['password'] == password:
            session['user'] = username
            session['role'] = USERS[username]['role']
            session['name'] = USERS[username]['name']
            flash(f'Welcome, {USERS[username]["name"]}!', 'success')
            
            # Redirect based on role
            if USERS[username]['role'] == 'hr':
                return redirect(url_for('hr_dashboard'))
            else:
                return redirect(url_for('cabang_upload'))
        else:
            flash('Invalid credentials', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# 🔴 HR ROUTES
@app.route('/hr')
@hr_required
def hr_dashboard():
    conn = get_db()
    stats = {
        'pending': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Pending'").fetchone()[0],
        'approved': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Approved'").fetchone()[0],
        'total': conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
        'amount': conn.execute("SELECT COALESCE(SUM(loan_amount),0) FROM submissions").fetchone()[0],
    }
    recent = [dict(r) for r in conn.execute(
        "SELECT * FROM submissions ORDER BY submitted_date DESC LIMIT 50").fetchall()]
    conn.close()
    return render_template('hr_dashboard.html', stats=stats, recent=recent)

@app.route('/api/approve/<int:id>', methods=['POST'])
@hr_required
def approve(id):
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Approved', approved_by=?, approved_date=CURRENT_TIMESTAMP WHERE id=?",
                 (session.get('name','HR'), id))
    conn.commit()
    conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/reject/<int:id>', methods=['POST'])
@hr_required
def reject(id):
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Rejected' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'status':'ok'})

@app.route('/export-excel')
@hr_required
def export():
    conn = get_db()
    df = pd.read_sql_query("SELECT * FROM submissions ORDER BY submitted_date DESC", conn)
    conn.close()
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as w:
        df.to_excel(w, sheet_name='Submissions', index=False)
    output.seek(0)
    return send_file(output, download_name=f'HR_Report_{datetime.now().strftime("%Y%m%d")}.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# 🔵 CABANG ROUTES
@app.route('/cabang')
@cabang_required
def cabang_upload():
    return render_template('cabang_upload.html')

@app.route('/cabang/scan', methods=['GET','POST'])
@cabang_required
def cabang_scan():
    parsed, confidence, raw_text, calculations = None, None, None, None
    
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(filepath)
            raw_text = ocr_scan_image(filepath)
            if raw_text:
                parsed, confidence = smart_parse(raw_text)
                if parsed:
                    calculations = calculate(parsed)
                    flash('✅ Document scanned! Verify data below.', 'success')
                else:
                    flash('⚠️ Could not detect data patterns.', 'warning')
            else:
                flash('⚠️ OCR failed. Try a clearer image.', 'warning')
    
    return render_template('cabang_scan.html', parsed=parsed, confidence=confidence,
                         raw_text=raw_text, calculations=calculations)

@app.route('/cabang/form')
@cabang_required
def cabang_form():
    return render_template('cabang_form.html')

@app.route('/api/submit', methods=['POST'])
@cabang_required
def submit():
    try:
        data = request.json
        calculations = calculate(data)
        data.update(calculations)
        
        conn = get_db()
        conn.execute('''INSERT OR REPLACE INTO submissions 
            (npk, kpm_id, nama_lengkap, loan_amount, down_payment, total_ar,
             tanggal_mulai, tenure_months, loan_type, interest_rate, cabang,
             principal, total_interest, monthly_installment, outstanding_balance,
             submitted_by, document_source, ocr_confidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (data.get('npk'), data.get('kpm_id'), data.get('nama_lengkap'),
             data.get('loan_amount'), data.get('down_payment'), data.get('total_ar'),
             data.get('tanggal_mulai'), data.get('tenure_months'), data.get('loan_type'),
             data.get('interest_rate'), data.get('cabang'),
             data.get('principal'), data.get('total_interest'),
             data.get('monthly_installment'), data.get('outstanding_balance'),
             session.get('name','Cabang'), data.get('document_source','Manual'),
             data.get('ocr_confidence',100)))
        conn.commit()
        conn.close()
        return jsonify({'status':'success','calculations':calculations})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}),500

# Default redirect
@app.route('/')
def index():
    if 'user' in session:
        if session.get('role') == 'hr':
            return redirect(url_for('hr_dashboard'))
        else:
            return redirect(url_for('cabang_upload'))
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
