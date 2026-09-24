import requests
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
BASE = "http://127.0.0.1:5000"

print("=================================================================")
print("STEP 1: HOSPITAL USER LOGIN")
print("=================================================================")
hosp_login = requests.post(f"{BASE}/api/auth/login", json={
    "role": "hospital",
    "username": "hospital_admin",
    "password": "hospital123"
}).json()
assert hosp_login["success"] is True
print(f"✓ Logged in as: {hosp_login['user']['display_name']} ({hosp_login['user']['role_title']})")
print(f"  Portal: {hosp_login['user']['portal_label']}")

print("\n=================================================================")
print("STEP 2: UPLOAD HOSPITAL ARCHIVE (WITHOUT INSURANCE POLICY)")
print("=================================================================")
zip_path = Path("demo/sample/hospital data/AP12363098.zip")
assert zip_path.exists(), f"File {zip_path} not found"
print(f"Uploading hospital archive: {zip_path.name} ({zip_path.stat().st_size / 1024:.1f} KB)...")

with open(zip_path, "rb") as f:
    upload_resp = requests.post(
        f"{BASE}/api/convert",
        files={"file": (zip_path.name, f, "application/zip")}
    )

assert upload_resp.status_code == 200, f"Upload failed: {upload_resp.text}"
upload_data = upload_resp.json()
patient_id = upload_data.get("patient_id")
patient_name = upload_data.get("patient_name") or upload_data.get("archive_info", {}).get("patient_name")
print(f"✓ Converted successfully into FHIR format!")
print(f"  Patient ID: {patient_id}")
print(f"  Patient Name: {patient_name}")
print(f"  Stored Bundles: {upload_data.get('stored_bundle_ids')}")

print("\n=================================================================")
print("STEP 3: VERIFY UNADJUDICATED STATE IN HOSPITAL PORTAL")
print("=================================================================")
p_info = requests.get(f"{BASE}/api/patients/{patient_id}").json()
bundle = json.loads(p_info["bundles"][0]["bundle_json"]) if isinstance(p_info["bundles"][0]["bundle_json"], str) else p_info["bundles"][0]["bundle_json"]
rtypes = [e.get("resource", {}).get("resourceType") for e in bundle.get("entry", []) if e.get("resource")]
print(f"Resource types in bundle: {set(rtypes)}")

# Check that Claims (hospital bills) exist, but Coverage and ClaimResponse do NOT exist yet
assert "Claim" in rtypes, "Hospital Claim bill should be in bundle"
assert "Coverage" not in rtypes, "Coverage should NOT be in hospital bundle before insurance upload"
assert "ClaimResponse" not in rtypes, "ClaimResponse should NOT be in hospital bundle before insurance upload"

all_patients = requests.get(f"{BASE}/api/patients").json()
patient_record = next(p for p in all_patients if p["id"] == patient_id)
print(f"✓ Patient record listed:")
print(f"  Billed Amount: INR {patient_record['billed_amount']:,.2f}")
print(f"  Is Adjudicated: {patient_record['is_adjudicated']}")
print(f"  Status: {patient_record['adjudication_status']}")
assert patient_record["is_adjudicated"] is False
assert patient_record["billed_amount"] > 0

print("\n=================================================================")
print("STEP 4: INSURANCE USER LOGIN")
print("=================================================================")
ins_login = requests.post(f"{BASE}/api/auth/login", json={
    "role": "insurance",
    "username": "insurance_auditor",
    "password": "insurance123"
}).json()
assert ins_login["success"] is True
print(f"✓ Logged in as: {ins_login['user']['display_name']} ({ins_login['user']['role_title']})")
print(f"  Portal: {ins_login['user']['portal_label']}")

print("\n=================================================================")
print("STEP 5: INSURANCE PORTAL VIEWS ALL PATIENT REPORTS")
print("=================================================================")
ins_patients = requests.get(f"{BASE}/api/insurance/patients").json()
print(f"✓ Found {len(ins_patients)} patient files transmitted from hospital.")
target_patient = next(p for p in ins_patients if p["id"] == patient_id)
print(f"  Selected Claimant: {target_patient['name']}")
print(f"  Hospital Bill: INR {target_patient['billed_amount']:,.2f}")
print(f"  Adjudication Status: {target_patient['adjudication_status']}")

print("\n=================================================================")
print("STEP 6: INSURANCE PORTAL UPLOADS POLICY DOCUMENT FOR PATIENT")
print("=================================================================")
policy_file = Path("demo/sample/insurance policies/Insurance_Policy_Document_AP12363098 1.pdf")
assert policy_file.exists(), f"Policy file {policy_file} not found"
print(f"Uploading policy document: {policy_file.name} ({policy_file.stat().st_size / 1024:.1f} KB)...")

with open(policy_file, "rb") as f:
    adj_resp = requests.post(
        f"{BASE}/api/insurance/patients/{patient_id}/adjudicate-policy",
        files={"file": (policy_file.name, f, "application/pdf")}
    )

assert adj_resp.status_code == 200, f"Adjudication failed: {adj_resp.text}"
adj_data = adj_resp.json()
print("✓ Claim Adjudication Successful!")
print("  Server Message:", adj_data.get("message"))
adj = adj_data["adjudication"]
print(f"\n=================================================================")
print("STEP 7: INSURANCE SETTLEMENT REPORT GENERATED")
print("=================================================================")
print(f"  Total Hospital Bill:    INR {adj['total_billed']:,.2f}")
print(f"  Amount Covered (TPA):  INR {adj['insured_amount']:,.2f} ({adj['coverage_percentage']}%)")
print(f"  Patient Needs to Pay:   INR {adj['patient_payable']:,.2f}")
print(f"  Policy Number:          {adj['policy_number']}")
print(f"  Insurance Scheme:       {adj.get('insurer_name') or adj.get('scheme_or_insurer')}")
print(f"  Pre-Auth Reference:     {adj.get('pre_auth_ref')}")
print(f"  Disposition:            {adj.get('disposition')}")

assert adj["total_billed"] > 0
assert adj["insured_amount"] > 0
assert adj["patient_payable"] >= 0
assert round(adj["total_billed"], 2) == round(adj["insured_amount"] + adj["patient_payable"], 2)

print("\n=================================================================")
print("STEP 8: VERIFY INSURANCE REPORT IS VISIBLE IN HOSPITAL PORTAL")
print("=================================================================")
hosp_view = requests.get(f"{BASE}/api/patients/{patient_id}").json()
hosp_bundle = json.loads(hosp_view["bundles"][0]["bundle_json"]) if isinstance(hosp_view["bundles"][0]["bundle_json"], str) else hosp_view["bundles"][0]["bundle_json"]
hosp_rtypes = [e.get("resource", {}).get("resourceType") for e in hosp_bundle.get("entry", []) if e.get("resource")]

print(f"Hospital FHIR bundle resources now include:")
print(f"  ✓ Coverage present: {'Coverage' in hosp_rtypes}")
print(f"  ✓ ClaimResponse present: {'ClaimResponse' in hosp_rtypes}")
assert "Coverage" in hosp_rtypes
assert "ClaimResponse" in hosp_rtypes

# Check enriched patient record in hospital list
hosp_patients = requests.get(f"{BASE}/api/patients").json()
updated_p = next(p for p in hosp_patients if p["id"] == patient_id)
print(f"\nHospital Portal Patient List Record:")
print(f"  Name: {updated_p['name']}")
print(f"  Billed Amount: INR {updated_p['billed_amount']:,.2f}")
print(f"  Amount Covered in Insurance: INR {updated_p['insured_amount']:,.2f}")
print(f"  Amount Patient Needs to Pay: INR {updated_p['patient_payable']:,.2f}")
print(f"  Adjudication Status: {updated_p['adjudication_status']}")

assert updated_p["is_adjudicated"] is True
assert updated_p["insured_amount"] == adj["insured_amount"]
assert updated_p["patient_payable"] == adj["patient_payable"]

print("\n=================================================================")
print("ALL PRODUCTION-GRADE VERIFICATION STEPS PASSED SUCCESSFULLY!")
print("=================================================================")
