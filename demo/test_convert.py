"""Direct conversion test using the toolkit's rest_server module."""
import os
import sys
import traceback

sys.path.insert(0, ".")

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass

from absl import flags

# Register the flag by importing rest_server (which defines --config_file)
from src import rest_server

config_path = "config.demo.yaml" if os.path.exists("config.demo.yaml") else "src/config.yaml"

if not flags.FLAGS.is_parsed():
    flags.FLAGS([sys.argv[0], f"--config_file={config_path}"])

print(f"Config: {config_path}")
print(f"API key set: {bool(os.environ.get('GEMINI_API_KEY'))}")

# Try to initialize the standardizer
try:
    standardizer = rest_server.get_composite_standardizer()
    print(f"Standardizer initialized: {standardizer is not None}")
except Exception as e:
    print(f"Standardizer init FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

# Try a test conversion
test_dir = os.path.join("demo", "sample")
test_images = [f for f in os.listdir(test_dir) if f.endswith((".jpg", ".jpeg", ".png"))]
if not test_images:
    print("No test images found")
    sys.exit(1)

test_file = os.path.join(test_dir, test_images[0])
print(f"Test file: {test_file}")

with open(test_file, "rb") as f:
    file_bytes = f.read()

print(f"File size: {len(file_bytes)} bytes")
print("Starting conversion...")

try:
    result = standardizer.standardize(file_bytes, mime_type="image/jpeg")
    print("SUCCESS!")
    result_dict = result.model_dump(mode="json")
    docs = result_dict.get("standardized_medical_documents", [])
    print(f"Documents: {len(docs)}")
    if docs:
        bundle = docs[0].get("fhir_bundle")
        if bundle:
            entries = bundle.get("entry", [])
            print(f"FHIR entries: {len(entries)}")
            for entry in entries[:3]:
                rt = entry.get("resource", {}).get("resourceType", "?")
                print(f"  - {rt}")
except Exception as e:
    print(f"CONVERSION FAILED: {e}")
    traceback.print_exc()
