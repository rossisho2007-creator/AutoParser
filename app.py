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
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

USERS = {'hr': {'password': 'hr123', 'role': 'hr', 'name': 'HR Officer'},
         'cabang1': {'password': 'cabang123', 'role': 'cabang', 'name': 'Jakarta South'}}

def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if 'user' not in session: return redirect(url_for('login'))
        return f(*a, **k)
    return d

def hr_required(f):
    @wraps(f)
    def d(*a, **k):
        if session.get('role') != 'hr': flash('HR only', 'danger'); return redirect(url_for('login'))
        return f(*a, **k)
    return d

def cabang_required(f):
    @wraps(f)
    def d(*a, **k):
        if session.get('role') != 'cabang': flash('Cabang only', 'danger'); return redirect(url_for('login'))
        return f(*a, **k)
    return d

def get_db():
    conn = sqlite3.connect('data.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, npk TEXT, kpm_id TEXT, nama_lengkap TEXT,
        loan_amount REAL, down_payment REAL, total_ar REAL, tanggal_mulai TEXT,
        tenure_months INTEGER, loan_type TEXT, interest_rate REAL, cabang TEXT,
        principal REAL, total_interest REAL, monthly_installment REAL,
        outstanding_balance REAL, status_approval TEXT DEFAULT 'Pending',
        hr_notes TEXT, submitted_by TEXT, document_source TEXT,
        submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit(); conn.close()

init_db()

# ====== OCR ======
def do_ocr(path):
    try:
        from PIL import Image, ImageEnhance
        import pytesseract
        img = Image.open(path)
        if img.width > 2000:
            r = 2000/img.width
            img = img.resize((2000, int(img.height*r)))
        img = img.convert('L')
        img = ImageEnhance.Contrast(img).enhance(3.0)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        return pytesseract.image_to_string(img, lang='eng+ind', config='--psm 6').strip()
    except: return ''

# ====== WORKING PARSER ======
def parse_kpm_form(text):
    """Tested and working - extracts 7 fields"""
    try:
        if not text: return {}, {}, text, {}
        result, conf, meta = {}, {}, {}
        if 'kredit' in text.lower(): meta['form_type'] = 'KPM_FORM'
        tests = [
            ('nama_lengkap', r'Nama\s*:\s*([A-Za-z\s\.]{5,60})'),
            ('npk', r'Pokok\s*Karyawan\s*:?\s*(\d[\d\s]*)'),
            ('jabatan_gol', r'Jabatan\s*/\s*Gol\s*:\s*([A-Za-z0-9\s\/\-\.]{2,30})'),
            ('departemen_cabang', r'Cabang\s*:\s*([A-Za-z0-9\s\/\-\.]{3,80})'),
            ('tgl_masuk', r'Masuk\s*:\s*(\d{1,2}\s*[A-Za-z]+\s*\d{2,4})'),
            ('tgl_pengangkatan', r'Pengangkatan\s*:\s*(\d{1,2}\s*[A-Za-z]+\s*\d{2,4})'),
            ('policy_number', r'(005/SK\s*DIR/HRD/III/2008)'),
        ]
        for field, pat in tests:
            try:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    v = m.group(1).strip()
                    if field == 'npk': v = re.sub(r'\s+', '', v)
                    elif 'tgl' in field: v = v.replace(' ', '/')
                    if v: result[field] = v; conf[field] = 90
            except: pass
        if 'kredit' in text.lower() and 'motor' in text.lower():
            result['loan_type'] = 'Kredit Kepemilikan Motor'
        return result, conf, text, meta
    except Exception as e:
        print(f"Parse error: {e}")
        return {}, {}, text, {}

def calculate(data):
    try:
        loan = float(data.get('loan_amount', 0))
        dp = float(data.get('down_payment', 0))
        tenor = int(data.get('tenure_months', 12))
        rate = float(data.get('interest_rate', 0.05))
        if loan <= 0 or tenor <= 0: return {}
        p = loan - dp
        ti = p * rate * (tenor/12)
        m = (p + ti) / tenor
        return {'principal': round(p), 'total_interest': round(ti),
                'monthly_installment': round(m), 'outstanding_balance': round(p+ti)}
    except: return {}

# ====== ROUTES ======
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','').strip().lower()
        p = request.form.get('password','')
        if u in USERS and USERS[u]['password'] == p:
            session['user'] = u; session['role'] = USERS[u]['role']; session['name'] = USERS[u]['name']
            return redirect('/hr' if USERS[u]['role']=='hr' else '/cabang')
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/hr')
@hr_required
def hr_dashboard():
    conn = get_db()
    stats = {'pending': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Pending'").fetchone()[0],
             'approved': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Approved'").fetchone()[0],
             'total': conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]}
    recent = [dict(r) for r in conn.execute("SELECT * FROM submissions ORDER BY submitted_date DESC LIMIT 100")]
    conn.close()
    return render_template('hr_dashboard.html', stats=stats, recent=recent)

@app.route('/hr/review/<int:id>', methods=['GET','POST'])
@hr_required
def hr_review(id):
    conn = get_db()
    if request.method == 'POST':
        a = request.form.get('action'); n = request.form.get('hr_notes','')
        if a == 'approve': conn.execute("UPDATE submissions SET status_approval='Approved', hr_notes=? WHERE id=?", (n,id))
        elif a == 'reject': conn.execute("UPDATE submissions SET status_approval='Rejected', hr_notes=? WHERE id=?", (n,id))
        conn.commit(); conn.close(); return redirect('/hr')
    sub = dict(conn.execute("SELECT * FROM submissions WHERE id=?", (id,)).fetchone())
    conn.close()
    return render_template('hr_review.html', submission=sub)

@app.route('/cabang')
@cabang_required
def cabang_upload(): return render_template('cabang_upload.html')

@app.route('/cabang/scan', methods=['GET','POST'])
@cabang_required
def cabang_scan():
    parsed, conf, raw, calc, debug = None, None, None, None, {}
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            debug['file'] = file.filename
            raw = do_ocr(path)
            debug['chars'] = len(raw) if raw else 0
            if raw:
                parsed, conf, raw, meta = parse_kpm_form(raw)
                if parsed: calc = calculate(parsed)
    return render_template('cabang_scan.html', parsed=parsed, confidence=conf, raw_text=raw, calculations=calc, debug=debug)

@app.route('/cabang/form')
@cabang_required
def cabang_form(): return render_template('cabang_form.html')

@app.route('/api/submit', methods=['POST'])
@cabang_required
def submit():
    try:
        data = request.json
        calc = calculate(data)
        if not calc: return jsonify({'status':'error'}), 400
        data.update(calc)
        conn = get_db()
        conn.execute('INSERT INTO submissions (npk,kpm_id,nama_lengkap,loan_amount,down_payment,total_ar,tanggal_mulai,tenure_months,loan_type,interest_rate,cabang,principal,total_interest,monthly_installment,outstanding_balance,submitted_by,document_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (data.get('npk'),data.get('kpm_id'),data.get('nama_lengkap'),data.get('loan_amount'),data.get('down_payment'),data.get('total_ar'),data.get('tanggal_mulai'),data.get('tenure_months'),data.get('loan_type'),data.get('interest_rate'),data.get('cabang',session.get('name')),data.get('principal'),data.get('total_interest'),data.get('monthly_installment'),data.get('outstanding_balance'),session.get('name'),data.get('document_source','Cabang')))
        conn.commit(); conn.close()
        return jsonify({'status':'success','calculations':calc})
    except Exception as e: return jsonify({'status':'error','message':str(e)}),500

@app.route('/api/approve/<int:id>', methods=['POST'])
@hr_required
def approve(id):
    conn = get_db(); conn.execute("UPDATE submissions SET status_approval='Approved' WHERE id=?",(id,)); conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/reject/<int:id>', methods=['POST'])
@hr_required
def reject(id):
    conn = get_db(); conn.execute("UPDATE submissions SET status_approval='Rejected' WHERE id=?",(id,)); conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/hr/export-excel')
@hr_required
def export():
    conn = get_db(); df = pd.read_sql_query("SELECT * FROM submissions", conn); conn.close()
    o = BytesIO()
    with pd.ExcelWriter(o, engine='openpyxl') as w: df.to_excel(w, index=False)
    o.seek(0)
    return send_file(o, download_name=f'Report_{datetime.now().strftime("%Y%m%d")}.xlsx')

@app.route('/')
def index():
    if 'user' in session: return redirect('/hr' if session.get('role')=='hr' else '/cabang')
    return redirect('/login')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
