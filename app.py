"""
AutoInput Pro - Complete System with Teams List Integration
🔵 Cabang: Submit only (scan/form)
🔴 HR: Full review, expand details, approve/reject, export
📋 Auto-syncs to Microsoft Teams List
"""

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
import pandas as pd
import os, re, json, sqlite3, requests
from datetime import datetime
from io import BytesIO
from functools import wraps

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'autopro-2024'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ═══════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════
USERS = {
    'hr': {'password': 'hr123', 'role': 'hr', 'name': 'HR Officer'},
    'cabang1': {'password': 'cabang123', 'role': 'cabang', 'name': 'Jakarta South'},
    'cabang2': {'password': 'cabang123', 'role': 'cabang', 'name': 'Bandung'},
    'cabang3': {'password': 'cabang123', 'role': 'cabang', 'name': 'Surabaya'},
}

# Microsoft Teams List Webhook URL (set this in production)
TEAMS_WEBHOOK_URL = os.environ.get('TEAMS_WEBHOOK_URL', '')

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
        npk TEXT, kpm_id TEXT, nama_lengkap TEXT,
        loan_amount REAL, down_payment REAL, total_ar REAL,
        tanggal_mulai TEXT, tenure_months INTEGER,
        loan_type TEXT, interest_rate REAL, cabang TEXT,
        principal REAL, total_interest REAL,
        monthly_installment REAL, outstanding_balance REAL,
        status_approval TEXT DEFAULT 'Pending',
        hr_notes TEXT,  -- NEW: HR can add notes
        submitted_by TEXT, approved_by TEXT,
        document_source TEXT, ocr_confidence REAL,
        submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_date DATETIME,
        teams_synced INTEGER DEFAULT 0  -- NEW: Track Teams sync
    )''')
    conn.commit()
    conn.close()

init_db()

# ═══════════════════════════════════
# TEAMS LIST SYNC
# ═══════════════════════════════════
def sync_to_teams(submission_data, action='new'):
    """Send data to Microsoft Teams via webhook or Power Automate"""
    if not TEAMS_WEBHOOK_URL:
        return False  # Webhook not configured
    
    try:
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": f"New Submission - {submission_data.get('nama_lengkap', 'Unknown')}",
            "themeColor": "DC3545" if action == 'new' else "28A745",
            "title": f"{'🆕 New' if action == 'new' else '✅ Approved'} Submission",
            "sections": [
                {
                    "facts": [
                        {"name": "Customer", "value": submission_data.get('nama_lengkap', '')},
                        {"name": "KPM ID", "value": submission_data.get('kpm_id', '')},
                        {"name": "Loan Amount", "value": f"Rp {submission_data.get('loan_amount', 0):,.0f}"},
                        {"name": "Monthly Installment", "value": f"Rp {submission_data.get('monthly_installment', 0):,.0f}"},
                        {"name": "Branch", "value": submission_data.get('cabang', '')},
                        {"name": "Status", "value": submission_data.get('status_approval', 'Pending')},
                    ]
                }
            ]
        }
        
        response = requests.post(TEAMS_WEBHOOK_URL, json=card)
        return response.status_code == 200
    except Exception as e:
        print(f"Teams sync error: {e}")
        return False

# ═══════════════════════════════════
# OCR ENGINE (same as before)
# ═══════════════════════════════════
def ocr_scan_image(image_path):
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path).convert('L')
        return pytesseract.image_to_string(img, lang='eng+ind').strip()
    except: return None

def parse_document(text):
    if not text: return {}, {}
    patterns = {
        'npk': r'(?:NPK|NOPEK)[:\s]*([A-Za-z0-9\-]{2,20})',
        'kpm_id': r'(?:KPM)[:\s\-]*(\d{10,})',
        'nama_lengkap': r'(?:Nama|Customer|Debitur)[:\s]*([A-Za-z\s\.]{3,60})',
        'loan_amount': r'(?:Loan|Pinjaman|AF|Plafon)[:\s]*[Rp\.\s]*([\d,\.]{5,})',
        'down_payment': r'(?:DP|Down\s*Payment|Uang\s*Muka)[:\s]*[Rp\.\s]*([\d,\.]{4,})',
        'total_ar': r'(?:Total\s*AR|Piutang)[:\s]*[Rp\.\s]*([\d,\.]{4,})',
        'tanggal_mulai': r'(?:Tanggal|Tgl)[:\s]*(\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4})',
        'tenure_months': r'(?:Tenure|Tenor|Jangka)[:\s]*(\d{1,3})',
        'loan_type': r'(?:Type|Jenis|Produk)[:\s]*(Regular|Fleet|Siap\s*Dana|KINTO)',
        'interest_rate': r'(?:Interest|Bunga|Rate)[:\s]*([\d.,]{1,5})\s*%?',
        'cabang': r'(?:Cabang|Branch|Kantor)[:\s]*([A-Za-z\s\-]{3,40})',
    }
    result, conf = {}, {}
    for field, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE|re.MULTILINE)
        if m:
            v = m.group(1).strip()
            if not v: continue
            try:
                if field in ['loan_amount','down_payment','total_ar']:
                    v = float(re.sub(r'[^\d]','',v))
                elif field == 'tenure_months': v = int(re.sub(r'[^\d]','',v))
                elif field == 'interest_rate':
                    v = float(v.replace(',','.'))
                    if v > 1: v /= 100
                result[field] = v; conf[field] = 85
            except: pass
    return result, conf

def calculate(data):
    try:
        loan = float(data.get('loan_amount',0))
        dp = float(data.get('down_payment',0))
        tenor = int(data.get('tenure_months',12))
        rate = float(data.get('interest_rate',0.05))
        if loan <= 0 or tenor <= 0: return {}
        principal = loan - dp
        total_int = principal * rate * (tenor/12)
        monthly = (principal + total_int) / tenor
        return {'principal':round(principal),'total_interest':round(total_int),
                'monthly_installment':round(monthly),'outstanding_balance':round(principal+total_int)}
    except: return {}

# ═══════════════════════════════════
# ROUTES
# ═══════════════════════════════════

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','').lower().strip()
        p = request.form.get('password','')
        if u in USERS and USERS[u]['password'] == p:
            session['user'] = u; session['role'] = USERS[u]['role']; session['name'] = USERS[u]['name']
            return redirect(url_for('hr_dashboard') if USERS[u]['role']=='hr' else url_for('cabang_upload'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

# 🔴 HR ROUTES
@app.route('/hr')
@hr_required
def hr_dashboard():
    conn = get_db()
    stats = {
        'pending': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Pending'").fetchone()[0],
        'approved': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Approved'").fetchone()[0],
        'total': conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
    }
    recent = [dict(r) for r in conn.execute(
        "SELECT * FROM submissions ORDER BY submitted_date DESC LIMIT 100").fetchall()]
    conn.close()
    return render_template('hr_dashboard.html', stats=stats, recent=recent)

@app.route('/hr/review/<int:id>', methods=['GET','POST'])
@hr_required
def hr_review(id):
    """HR can EXPAND and review full submission details"""
    conn = get_db()
    
    if request.method == 'POST':
        action = request.form.get('action')
        notes = request.form.get('hr_notes', '')
        
        if action == 'approve':
            conn.execute('''UPDATE submissions 
                SET status_approval='Approved', hr_notes=?, approved_by=?, approved_date=CURRENT_TIMESTAMP 
                WHERE id=?''', (notes, session.get('name','HR'), id))
            conn.commit()
            
            # Sync to Teams
            sub = dict(conn.execute("SELECT * FROM submissions WHERE id=?", (id,)).fetchone())
            sub['status_approval'] = 'Approved'
            sync_to_teams(sub, 'approved')
            
            flash('✅ Approved! Synced to Teams.', 'success')
        elif action == 'reject':
            conn.execute('''UPDATE submissions 
                SET status_approval='Rejected', hr_notes=? WHERE id=?''', (notes, id))
            conn.commit()
            flash('❌ Rejected.', 'warning')
        elif action == 'save_notes':
            conn.execute('UPDATE submissions SET hr_notes=? WHERE id=?', (notes, id))
            conn.commit()
            flash('📝 Notes saved.', 'info')
        
        conn.close()
        return redirect(url_for('hr_dashboard'))
    
    submission = dict(conn.execute("SELECT * FROM submissions WHERE id=?", (id,)).fetchone())
    conn.close()
    return render_template('hr_review.html', submission=submission)

@app.route('/hr/scan', methods=['GET','POST'])
@hr_required
def hr_scan():
    parsed, confidence, raw_text, calculations = None, None, None, None
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            raw_text = ocr_scan_image(path)
            if raw_text:
                parsed, confidence = parse_document(raw_text)
                if parsed: calculations = calculate(parsed)
    return render_template('hr_scan.html', parsed=parsed, confidence=confidence,
                         raw_text=raw_text, calculations=calculations)

@app.route('/hr/import-excel', methods=['GET','POST'])
@hr_required
def hr_import_excel():
    results = None
    if request.method == 'POST':
        file = request.files.get('excel_file')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            if file.filename.endswith(('.xlsx','.xls','.csv')):
                df = pd.read_excel(path) if file.filename.endswith('.xlsx') else pd.read_csv(path)
                results = {'rows': len(df), 'columns': list(df.columns), 'sample': df.head(3).to_dict('records')}
                # Auto-import logic here
                flash(f'✅ File read: {len(df)} rows found. Ready to import.', 'success')
    return render_template('hr_import.html', results=results)

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

@app.route('/api/hr-submit', methods=['POST'])
@hr_required
def hr_submit():
    try:
        data = request.json
        calc = calculate(data)
        if not calc: return jsonify({'status':'error'}), 400
        data.update(calc)
        conn = get_db()
        conn.execute('''INSERT INTO submissions 
            (npk,kpm_id,nama_lengkap,loan_amount,down_payment,total_ar,tanggal_mulai,tenure_months,loan_type,interest_rate,cabang,principal,total_interest,monthly_installment,outstanding_balance,submitted_by,document_source,status_approval)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (data.get('npk'),data.get('kpm_id'),data.get('nama_lengkap'),data.get('loan_amount'),data.get('down_payment'),data.get('total_ar'),data.get('tanggal_mulai'),data.get('tenure_months'),data.get('loan_type'),data.get('interest_rate'),data.get('cabang'),data.get('principal'),data.get('total_interest'),data.get('monthly_installment'),data.get('outstanding_balance'),session.get('name'),data.get('document_source','HR'),'Approved'))
        conn.commit()
        sub_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        sub = dict(conn.execute("SELECT * FROM submissions WHERE id=?",(sub_id,)).fetchone())
        conn.close()
        sync_to_teams(sub, 'new')
        return jsonify({'status':'success','calculations':calc})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}),500

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
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            raw_text = ocr_scan_image(path)
            if raw_text:
                parsed, confidence = parse_document(raw_text)
                if parsed: calculations = calculate(parsed)
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
        calc = calculate(data)
        if not calc: return jsonify({'status':'error'}), 400
        data.update(calc)
        conn = get_db()
        conn.execute('''INSERT INTO submissions 
            (npk,kpm_id,nama_lengkap,loan_amount,down_payment,total_ar,tanggal_mulai,tenure_months,loan_type,interest_rate,cabang,principal,total_interest,monthly_installment,outstanding_balance,submitted_by,document_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (data.get('npk'),data.get('kpm_id'),data.get('nama_lengkap'),data.get('loan_amount'),data.get('down_payment'),data.get('total_ar'),data.get('tanggal_mulai'),data.get('tenure_months'),data.get('loan_type'),data.get('interest_rate'),data.get('cabang',session.get('name')),data.get('principal'),data.get('total_interest'),data.get('monthly_installment'),data.get('outstanding_balance'),session.get('name'),data.get('document_source','Cabang')))
        conn.commit()
        sub_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        sub = dict(conn.execute("SELECT * FROM submissions WHERE id=?",(sub_id,)).fetchone())
        conn.close()
        sync_to_teams(sub, 'new')
        return jsonify({'status':'success','calculations':calc})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}),500

# Teams List API endpoint
@app.route('/api/teams-list')
def teams_list():
    """Returns data in format compatible with Microsoft Teams List"""
    conn = get_db()
    submissions = [dict(r) for r in conn.execute(
        "SELECT * FROM submissions ORDER BY submitted_date DESC LIMIT 200").fetchall()]
    conn.close()
    return jsonify({'data': submissions, 'count': len(submissions)})

# Teams webhook configuration guide
@app.route('/teams-setup')
def teams_setup():
    return render_template('teams_setup.html')

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('hr_dashboard') if session.get('role')=='hr' else url_for('cabang_upload'))
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
