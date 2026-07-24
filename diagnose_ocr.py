"""
Isolated OCR diagnostic — no Flask, no browser, no timeout handling.
Tests EasyOCR directly so we can see exactly where this actually breaks.
Run with: python3 diagnose_ocr.py [optional path to an image]
"""
import sys
import time
import os

print("=" * 60)
print("STEP 1: Can we even import easyocr?")
print("=" * 60)
t0 = time.time()
try:
    import easyocr
    print(f"OK — imported in {time.time()-t0:.1f}s")
except Exception as e:
    print(f"FAILED to import easyocr: {e}")
    print("\n>>> This is the problem. easyocr itself isn't installed correctly")
    print(">>> in this environment. Run: pip list | grep -i easyocr")
    sys.exit(1)

print()
print("=" * 60)
print("STEP 2: Find a test image")
print("=" * 60)
test_image = sys.argv[1] if len(sys.argv) > 1 else None
if not test_image:
    candidates = []
    for folder in ['uploads', '.']:
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    candidates.append(os.path.join(folder, f))
    if candidates:
        test_image = candidates[0]
        print(f"Auto-found: {test_image}")
        if len(candidates) > 1:
            print(f"(also found {len(candidates)-1} other image(s), using the first)")
    else:
        print("FAILED — no image found in uploads/ or current folder.")
        print(">>> Take a photo with your phone, save it here, and re-run:")
        print(">>> python3 diagnose_ocr.py path/to/photo.jpg")
        sys.exit(1)
else:
    print(f"Using provided path: {test_image}")

if not os.path.isfile(test_image):
    print(f"FAILED — '{test_image}' does not exist.")
    sys.exit(1)
print(f"File size: {os.path.getsize(test_image)/1024:.1f} KB")

print()
print("=" * 60)
print("STEP 3: Initialize the OCR reader (downloads model weights on first run)")
print("=" * 60)
t0 = time.time()
try:
    reader = easyocr.Reader(['id', 'en'])
    print(f"OK — reader ready in {time.time()-t0:.1f}s")
except Exception as e:
    print(f"FAILED after {time.time()-t0:.1f}s: {type(e).__name__}: {e}")
    print("\n>>> This is the problem. The model itself failed to load.")
    print(">>> Common cause: partial/corrupted model download. Try:")
    print(">>> rm -rf ~/.EasyOCR  (then re-run this script to force a clean re-download)")
    sys.exit(1)

print()
print("=" * 60)
print("STEP 4: Run OCR on the actual image")
print("=" * 60)
t0 = time.time()
try:
    results = reader.readtext(test_image)
    elapsed = time.time() - t0
    print(f"OK — completed in {elapsed:.1f}s, found {len(results)} text region(s)")
    if not results:
        print("\n>>> OCR ran successfully but found ZERO text in this image.")
        print(">>> This means the image itself is the problem (blurry, too dark,")
        print(">>> wrong file, or genuinely no readable text) — not the code.")
    else:
        print("\nExtracted text (first 15 regions):")
        for i, (bbox, text, conf) in enumerate(results[:15]):
            print(f"  [{i+1}] '{text}'  (confidence: {conf:.2f})")
        full_text = ' '.join([r[1] for r in results])
        print(f"\nFull concatenated text ({len(full_text)} chars):")
        print(f"  {full_text[:300]}")
except Exception as e:
    print(f"FAILED after {time.time()-t0:.1f}s: {type(e).__name__}: {e}")
    print("\n>>> This is the problem. OCR itself crashes on this specific image.")
    sys.exit(1)

print()
print("=" * 60)
print("DONE — if you see extracted text above, EasyOCR itself works fine")
print("and the bug is in smart_parse() or the Flask route, not OCR itself.")
print("=" * 60)
