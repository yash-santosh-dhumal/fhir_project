import requests
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

BASE = 'http://127.0.0.1:5000'

print("=== 1. Health & Endpoints ===")
h = requests.get(f"{BASE}/api/health").json()
print("Health:", h)

print("\n=== 2. Auth Tests ===")
hosp_auth = requests.post(f"{BASE}/api/auth/login", json={"role": "hospital", "username": "hospital_admin", "password": "hospital123"}).json()
print("Hospital Login:", hosp_auth["success"], hosp_auth["user"]["display_name"], f"({hosp_auth['user']['role_title']})")

ins_auth = requests.post(f"{BASE}/api/auth/login", json={"role": "insurance", "username": "insurance_auditor", "password": "insurance123"}).json()
print("Insurance Login:", ins_auth["success"], ins_auth["user"]["display_name"], f"({ins_auth['user']['role_title']})")

print("\n=== 3. Available Sample Policies ===")
samples = requests.get(f"{BASE}/api/insurance/sample-policies").json()
for s in samples:
    print(f"  - {s['filename']} ({s['size_kb']} KB) -> {s['patient_hint']}")

print("\n=== 4. Patient Roster & Adjudication Status ===")
patients = requests.get(f"{BASE}/api/patients").json()
print(f"Total Patients: {len(patients)}")
for p in patients:
    print(f"  - Patient: {p.get('name')} (ID: {p.get('id')})")
    print(f"    Billed: INR {p.get('billed_amount'):,.2f} | Adjudicated: {p.get('is_adjudicated')}")
    print(f"    Insured: INR {p.get('insured_amount'):,.2f} | Patient Payable: INR {p.get('patient_payable'):,.2f}")
    print(f"    Status: {p.get('adjudication_status')}")

# Test sample adjudication on the first patient if unadjudicated
if patients:
    target = patients[0]
    pid = target["id"]
    print(f"\n=== 5. Testing Policy Adjudication for Patient '{target.get('name')}' ===")
    # Find matching policy if possible or use the first policy
    sample_to_use = samples[0]["filename"]
    for s in samples:
        if pid in s["filename"] or any(part in s["filename"] for part in (target.get("name") or "").split()):
            sample_to_use = s["filename"]
            break
    print(f"Using policy: {sample_to_use}")
    adj_resp = requests.post(
        f"{BASE}/api/insurance/patients/{pid}/adjudicate-sample-policy",
        json={"policy_filename": sample_to_use}
    )
    print("Adjudication Response HTTP:", adj_resp.status_code)
    adj_data = adj_resp.json()
    print("Success:", adj_data.get("success"))
    print("Message:", adj_data.get("message"))
    if "adjudication" in adj_data:
        adj = adj_data["adjudication"]
        print(f"Adjudication Results:")
        print(f"  - Total Billed: INR {adj.get('total_billed'):,.2f}")
        print(f"  - Insured (Covered): INR {adj.get('insured_amount'):,.2f} ({adj.get('coverage_percentage')}%)")
        print(f"  - Patient Needs to Pay: INR {adj.get('patient_payable'):,.2f}")
        print(f"  - Policy Number: {adj.get('policy_number')}")
        print(f"  - Scheme: {adj.get('insurer_name')}")

    # Verify patient report now shows adjudicated state
    p_detail = requests.get(f"{BASE}/api/patients/{pid}").json()
    b_json = json.loads(p_detail["bundles"][0]["bundle_json"]) if isinstance(p_detail["bundles"][0]["bundle_json"], str) else p_detail["bundles"][0]["bundle_json"]
    entries = b_json.get("entry", [])
    rtypes = [e.get("resource", {}).get("resourceType") for e in entries if e.get("resource")]
    print("\nFHIR Bundle Resource Types in Patient Record:")
    print("  Has Coverage:", "Coverage" in rtypes)
    print("  Has ClaimResponse:", "ClaimResponse" in rtypes)
    print("  Has Claim (Bill):", "Claim" in rtypes)
    print("  Has Patient:", "Patient" in rtypes)
