"""
Form Analyzer - Upload a form image and get coordinates of all text fields
Just run: python3 analyze_form.py
Then open http://localhost:5001 in your browser
"""

from flask import Flask, render_template_string, request, jsonify, send_file
import os
from PIL import Image, ImageDraw, ImageFont
import json

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs('uploads', exist_ok=True)

HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Form Analyzer - Click to Mark Fields</title>
    <style>
        body { font-family: Arial; margin: 20px; background: #f5f5f5; }
        h1 { color: #333; }
        #container { position: relative; display: inline-block; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        #formImage { max-width: 800px; cursor: crosshair; border: 2px solid #ddd; }
        .marker { position: absolute; border: 2px solid red; background: rgba(255,0,0,0.1); pointer-events: none; }
        .marker-label { position: absolute; background: red; color: white; font-size: 10px; padding: 2px 6px; border-radius: 3px; pointer-events: none; }
        #info { margin-top: 20px; padding: 15px; background: #fff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        #coordinates { background: #1a1a2e; color: #00ff00; padding: 15px; border-radius: 5px; font-family: monospace; font-size: 13px; max-height: 400px; overflow-y: auto; white-space: pre-wrap; }
        button { padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; }
        .btn-red { background: #dc3545; color: white; }
        .btn-blue { background: #007bff; color: white; }
        .btn-green { background: #28a745; color: white; }
        .field-list { margin: 10px 0; }
        .field-item { display: inline-block; background: #e9ecef; padding: 5px 10px; margin: 3px; border-radius: 5px; font-size: 12px; cursor: pointer; }
        .field-item.selected { background: #007bff; color: white; }
        input[type="text"] { padding: 8px; border: 1px solid #ddd; border-radius: 5px; width: 200px; margin: 5px; }
    </style>
</head>
<body>
    <h1>📸 Form Analyzer - Mark Fields</h1>
    
    {% if image %}
    <p><b>Instructions:</b> Select a field below, then click TWO corners of the field on the image (top-left then bottom-right)</p>
    
    <div class="field-list">
        <b>Select field to mark:</b><br>
        {% for field in fields %}
        <span class="field-item" onclick="selectField('{{ field }}')" id="field-{{ field }}">{{ field }}</span>
        {% endfor %}
        <br>
        <input type="text" id="customField" placeholder="Or type custom field name...">
        <button onclick="selectField(document.getElementById('customField').value)" class="btn-blue">Add</button>
    </div>
    
    <p>Selected: <b id="selectedField" style="color: red;">None</b> | 
       Clicks: <span id="clickCount">0</span>/2 |
       <button onclick="resetMarks()" class="btn-red">Reset All</button>
       <button onclick="copyCoordinates()" class="btn-green">📋 Copy Coordinates</button>
    </p>
    
    <div id="container">
        <img id="formImage" src="/image/{{ image }}" onclick="markPoint(event)">
        <div id="markers"></div>
    </div>
    
    <div id="info">
        <h3>📋 Field Coordinates</h3>
        <div id="coordinates">{}</div>
    </div>
    
    {% else %}
    <div style="text-align:center; padding:50px;">
        <h2>Upload a form image to analyze</h2>
        <form method="POST" enctype="multipart/form-data">
            <input type="file" name="form" accept="image/*" required style="padding:10px;">
            <button type="submit" class="btn-blue" style="font-size:16px;">📤 Upload & Analyze</button>
        </form>
        <p style="color:#666; margin-top:20px;">Upload a photo or scan of your form<br>Then click on the image to mark where each field is</p>
    </div>
    {% endif %}
    
    <script>
        let selectedField = null;
        let clicks = [];
        let allMarks = {};
        
        function selectField(field) {
            selectedField = field;
            clicks = [];
            document.getElementById('selectedField').textContent = field;
            document.getElementById('clickCount').textContent = '0';
            
            // Highlight selected
            document.querySelectorAll('.field-item').forEach(el => el.classList.remove('selected'));
            const el = document.getElementById('field-' + field);
            if (el) el.classList.add('selected');
        }
        
        function markPoint(event) {
            if (!selectedField) {
                alert('Please select a field first!');
                return;
            }
            
            const img = document.getElementById('formImage');
            const rect = img.getBoundingClientRect();
            const x = Math.round((event.clientX - rect.left) / rect.width * img.naturalWidth);
            const y = Math.round((event.clientY - rect.top) / rect.height * img.naturalHeight);
            
            clicks.push({x, y});
            
            if (clicks.length === 1) {
                // Show first point
                addMarker(x, y, x+5, y+5, 'blue');
            } else if (clicks.length === 2) {
                // Save the field
                const x1 = Math.min(clicks[0].x, clicks[1].x);
                const y1 = Math.min(clicks[0].y, clicks[1].y);
                const w = Math.abs(clicks[1].x - clicks[0].x);
                const h = Math.abs(clicks[1].y - clicks[0].y);
                
                allMarks[selectedField] = {x: x1, y: y1, w: w, h: h};
                
                // Show rectangle
                addMarker(x1, y1, w, h, 'red', selectedField);
                
                // Update coordinates display
                updateDisplay();
                
                // Reset
                clicks = [];
                selectedField = null;
                document.getElementById('selectedField').textContent = 'None';
            }
            
            document.getElementById('clickCount').textContent = clicks.length;
        }
        
        function addMarker(x, y, w, h, color, label) {
            const img = document.getElementById('formImage');
            const displayWidth = img.clientWidth;
            const scale = displayWidth / img.naturalWidth;
            
            const marker = document.createElement('div');
            marker.className = 'marker';
            marker.style.left = (x * scale) + 'px';
            marker.style.top = (y * scale) + 'px';
            marker.style.width = (w * scale) + 'px';
            marker.style.height = (h * scale) + 'px';
            marker.style.borderColor = color;
            marker.style.background = color === 'red' ? 'rgba(255,0,0,0.1)' : 'rgba(0,0,255,0.3)';
            
            if (label) {
                const labelEl = document.createElement('div');
                labelEl.className = 'marker-label';
                labelEl.textContent = label;
                labelEl.style.left = (x * scale) + 'px';
                labelEl.style.top = (y * scale - 20) + 'px';
                document.getElementById('markers').appendChild(labelEl);
            }
            
            document.getElementById('markers').appendChild(marker);
        }
        
        function updateDisplay() {
            document.getElementById('coordinates').textContent = JSON.stringify(allMarks, null, 2);
        }
        
        function resetMarks() {
            allMarks = {};
            clicks = [];
            selectedField = null;
            document.getElementById('markers').innerHTML = '';
            document.getElementById('coordinates').textContent = '{}';
            document.getElementById('selectedField').textContent = 'None';
        }
        
        function copyCoordinates() {
            const text = JSON.stringify(allMarks, null, 2);
            navigator.clipboard.writeText(text).then(() => {
                alert('✅ Coordinates copied to clipboard!');
            });
        }
    </script>
</body>
</html>
'''

FIELDS = [
    'NPK', 'KPM_ID', 'Nama_Lengkap', 'Loan_Amount', 'Down_Payment',
    'Total_AR', 'Tanggal_Mulai', 'Tenure', 'Loan_Type', 
    'Interest_Rate', 'Cabang', 'Remarks', 'Signature'
]

@app.route('/', methods=['GET', 'POST'])
def index():
    image = None
    if request.method == 'POST':
        file = request.files.get('form')
        if file:
            path = os.path.join('uploads', file.filename)
            file.save(path)
            image = file.filename
    
    # Find latest uploaded image if none specified
    if not image:
        files = [f for f in os.listdir('uploads') if f.endswith(('.png','.jpg','.jpeg'))]
        if files:
            image = sorted(files)[-1]
    
    return render_template_string(HTML, image=image, fields=FIELDS)

@app.route('/image/<filename>')
def serve_image(filename):
    return send_file(os.path.join('uploads', filename))

if __name__ == '__main__':
    print("\n" + "="*50)
    print("📸 FORM ANALYZER")
    print("="*50)
    print("\n1. Open http://localhost:5001 in your browser")
    print("2. Upload your form image")
    print("3. Click on fields to mark them")
    print("4. Copy the coordinates!\n")
    app.run(debug=True, host='0.0.0.0', port=5001)
