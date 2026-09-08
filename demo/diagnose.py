"""Diagnostic script for the FHIR toolkit backend."""
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

def main():
    print("=== FHIR Toolkit Diagnostic ===\n")

    # 1. Check config file
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    config_file = "config.demo.yaml" if os.path.exists(
        os.path.join(repo_root, "config.demo.yaml")
    ) else "src/config.yaml"
    config_path = os.path.join(repo_root, config_file)
    print(f"Config file: {config_file} (exists={os.path.exists(config_path)})")

    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # 2. Check LOINC KB paths
    print("\n--- LOINC KB Paths ---")
    for key in [
        "loinc_analaytes_index_csv_path",
        "loinc_system_kb_csv_path",
        "loinc_property_kb_csv_path",
    ]:
        path = config.get(key, "")
        exists = os.path.exists(path)
        print(f"  {key}: {path} -> exists={exists}")

    # 3. Check models
    print("\n--- Model Config ---")
    clf_model = config.get("classifier_llm_client", {}).get("parameters", {}).get("model", "N/A")
    ext_model = config.get("extractor_llm_client", {}).get("parameters", {}).get("model", "N/A")
    print(f"  Classifier model: {clf_model}")
    print(f"  Extractor model:  {ext_model}")

    # 4. Check API key
    api_key = os.environ.get("GEMINI_API_KEY", "")
    print(f"\n--- API Key ---")
    print(f"  GEMINI_API_KEY set: {bool(api_key)} (length={len(api_key)})")

    # 5. Try creating clients
    print("\n--- Client Init ---")
    from src.document_to_fhir.common import model_client
    try:
        client = model_client.GeminiClient(
            api_key=api_key,
            model=clf_model,
        )
        print(f"  Classifier client created OK")
    except Exception as e:
        print(f"  Classifier client ERROR: {e}")
        traceback.print_exc()

    # 6. Try a real conversion with a test image
    print("\n--- Conversion Test ---")
    test_images = [
        f for f in os.listdir(os.path.join(repo_root, "demo", "sample"))
        if f.endswith((".jpg", ".jpeg", ".png"))
    ]
    if not test_images:
        print("  No test images found in demo/sample/")
        return

    test_file = os.path.join(repo_root, "demo", "sample", test_images[0])
    print(f"  Using test file: {test_images[0]}")

    with open(test_file, "rb") as f:
        file_bytes = f.read()
    print(f"  File size: {len(file_bytes)} bytes")

    try:
        from absl import flags
        if not flags.FLAGS.is_parsed():
            flags.FLAGS([sys.argv[0], f"--config_file={config_path}"])

        from src import rest_server
        standardizer = rest_server.get_composite_standardizer()
        if standardizer is None:
            print("  ERROR: Standardizer is None")
            return
        print("  Standardizer initialized OK")

        result = standardizer.standardize(file_bytes, mime_type="image/jpeg")
        print("  CONVERSION SUCCESS!")
        print(f"  Result type: {type(result).__name__}")
        docs = getattr(result, "standardized_medical_documents", [])
        print(f"  Documents returned: {len(docs) if docs else 0}")
    except Exception as e:
        print(f"  CONVERSION ERROR: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
