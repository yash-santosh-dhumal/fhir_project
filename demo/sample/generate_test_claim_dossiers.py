"""Generate 4 realistic multi-page insurance claim dossiers for testing.

Each dossier is a 3-page A4 PDF matching the exact structure and layout of
sample_insurance_claim_dossier_multipage.pdf:
  - Page 1: Pre-Authorization / Cashless Approval Letter
  - Page 2: Final Hospital Invoice & Itemized Expense Breakdown
  - Page 3: Policy Schedule & Beneficiary Entitlement
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

SAMPLE_DIR = Path(__file__).resolve().parent

class NumberedCanvas(canvas.Canvas):
    """Canvas that computes total page count dynamically for the footer."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            super().showPage()
        super().save()

    def draw_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        text = f"Page {self._pageNumber} of {page_count} - Official Claim Dossier - Confidential"
        self.drawCentredString(A4[0] / 2.0, 24, text)
        self.restoreState()


def build_dossier_pdf(filename: str, claim_data: dict) -> Path:
    out_path = SAMPLE_DIR / filename
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=42,
    )

    styles = getSampleStyleSheet()
    
    # Custom styles
    company_style = ParagraphStyle(
        'CompanyHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        alignment=1, # Center
        textColor=colors.HexColor('#0f172a'),
    )
    sub_company_style = ParagraphStyle(
        'SubCompany',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        alignment=1,
        textColor=colors.HexColor('#475569'),
    )
    sec_title_style = ParagraphStyle(
        'SecTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#007a87'),
    )
    h2_style = ParagraphStyle(
        'H2Style',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1e293b'),
    )
    meta_bold = ParagraphStyle(
        'MetaBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#1e293b'),
    )
    meta_val = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#334155'),
    )
    tbl_cell = ParagraphStyle(
        'TblCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor('#1e293b'),
    )
    tbl_hdr = ParagraphStyle(
        'TblHdr',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#0f172a'),
    )
    tbl_num = ParagraphStyle(
        'TblNum',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        alignment=2, # Right
        textColor=colors.HexColor('#0f172a'),
    )
    legal_style = ParagraphStyle(
        'LegalStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=7,
        leading=9.5,
        textColor=colors.HexColor('#64748b'),
    )

    story = []

    def add_letterhead(doc_title: str):
        story.append(Paragraph(claim_data['insurer_name'].upper(), company_style))
        story.append(Spacer(1, 2))
        sub_text = (
            f"IRDAI Reg. No. {claim_data['irdai_reg']} | CIN: {claim_data['cin']} | Website: {claim_data['website']}<br/>"
            f"Toll Free: {claim_data['toll_free']} | Email: {claim_data['email']} | Cashless TPA: {claim_data['tpa_name']}"
        )
        story.append(Paragraph(sub_text, sub_company_style))
        story.append(Spacer(1, 6))
        story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor('#94a3b8'), spaceAfter=10))
        story.append(Paragraph(doc_title, sec_title_style))
        story.append(Spacer(1, 8))

    # ══════════════════════════════════════════════════════════
    # PAGE 1: PRE-AUTHORIZATION / CASHLESS APPROVAL LETTER
    # ══════════════════════════════════════════════════════════
    add_letterhead("PRE-AUTHORIZATION / CASHLESS APPROVAL LETTER")

    # Header meta table
    hdr_data = [
        [
            Paragraph(f"<b>Pre-Auth Approval No:</b> {claim_data['pre_auth_no']}", meta_val),
            Paragraph(f"<b>Date of Issuance:</b> {claim_data['pre_auth_date']}", meta_val),
        ],
        [
            Paragraph(f"<b>Policy Number:</b> {claim_data['policy_number']}", meta_val),
            Paragraph(f"<b>Plan Name:</b> {claim_data['plan_name']}", meta_val),
        ]
    ]
    t_hdr = Table(hdr_data, colWidths=[260, 260])
    t_hdr.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_hdr)
    story.append(Spacer(1, 14))

    # Section 1: Patient particulars
    story.append(Paragraph("1. PATIENT & SUBSCRIBER PARTICULARS", h2_style))
    story.append(Spacer(1, 4))
    pat_data = [
        [
            Paragraph(f"<b>Patient Name:</b> {claim_data['patient_name']}", meta_val),
            Paragraph(f"<b>Age / Gender:</b> {claim_data['age_gender']}", meta_val),
        ],
        [
            Paragraph(f"<b>Aadhaar UID:</b> {claim_data['aadhaar_number']}", meta_val),
            Paragraph(f"<b>ABHA ID:</b> {claim_data['abha_id']}", meta_val),
        ],
        [
            Paragraph(f"<b>Contact Phone:</b> {claim_data['phone']}", meta_val),
            Paragraph(f"<b>Address:</b> {claim_data['address']}", meta_val),
        ]
    ]
    t_pat = Table(pat_data, colWidths=[260, 260])
    t_pat.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_pat)
    story.append(Spacer(1, 14))

    # Section 2: Hospitalization details
    story.append(Paragraph("2. HOSPITALIZATION DETAILS", h2_style))
    story.append(Spacer(1, 4))
    hosp_data = [
        [
            Paragraph(f"<b>Hospital Name:</b> {claim_data['hospital_name']}", meta_val),
            Paragraph(f"<b>Hospital MRN / IP:</b> {claim_data['claim_number']}", meta_val),
        ],
        [
            Paragraph(f"<b>Admitting Diagnosis:</b> {claim_data['diagnosis']}", meta_val),
            Paragraph(f"<b>Proposed Procedure:</b> {claim_data['procedure']}", meta_val),
        ],
        [
            Paragraph(f"<b>Admission Date:</b> {claim_data['admission_date']}", meta_val),
            Paragraph(f"<b>Expected Discharge:</b> {claim_data['discharge_date']}", meta_val),
        ],
        [
            Paragraph(f"<b>Treating Doctor:</b> {claim_data['treating_doctor']}", meta_val),
            Paragraph(f"<b>Specialty:</b> {claim_data['specialty']}", meta_val),
        ]
    ]
    t_hosp = Table(hosp_data, colWidths=[260, 260])
    t_hosp.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_hosp)
    story.append(Spacer(1, 14))

    # Section 3: Authorization Sanction
    story.append(Paragraph("3. AUTHORIZATION SANCTION", h2_style))
    story.append(Spacer(1, 4))
    auth_data = [
        [
            Paragraph(f"<b>Initial Pre-Auth Requested:</b> Rs. {claim_data['requested_amount']}", meta_val),
            Paragraph(f"<b>Initial Amount Approved:</b> Rs. {claim_data['approved_amount']}", meta_val),
        ],
        [
            Paragraph(f"<b>Co-payment Applicable:</b> {claim_data['copay']}", meta_val),
            Paragraph(f"<b>Deductible:</b> {claim_data['deductible']}", meta_val),
        ],
        [
            Paragraph(f"<b>Room Category Sanctioned:</b> {claim_data['room_category']}", meta_val),
            Paragraph(f"<b>ICU Category:</b> {claim_data['icu_category']}", meta_val),
        ]
    ]
    t_auth = Table(auth_data, colWidths=[260, 260])
    t_auth.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_auth)

    # ══════════════════════════════════════════════════════════
    # PAGE 2: FINAL HOSPITAL INVOICE & BREAKDOWN
    # ══════════════════════════════════════════════════════════
    story.append(PageBreak())
    add_letterhead("FINAL HOSPITAL INVOICE & BREAKDOWN")

    inv_hdr = [
        [
            Paragraph(f"<b>Bill No:</b> {claim_data['bill_number']}", meta_val),
            Paragraph(f"<b>Bill Date:</b> {claim_data['bill_date']}", meta_val),
        ]
    ]
    t_inv_hdr = Table(inv_hdr, colWidths=[260, 260])
    t_inv_hdr.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_inv_hdr)
    story.append(Spacer(1, 10))

    # Items table
    tbl_rows = [
        [
            Paragraph("<b>Sl No.</b>", tbl_hdr),
            Paragraph("<b>Description of Service / Expense</b>", tbl_hdr),
            Paragraph("<b>Rate / Unit</b>", tbl_hdr),
            Paragraph("<b>Total Amount (Rs.)</b>", tbl_hdr),
        ]
    ]

    for idx, item in enumerate(claim_data['bill_items'], 1):
        tbl_rows.append([
            Paragraph(str(idx), tbl_cell),
            Paragraph(item[0], tbl_cell),
            Paragraph(item[1], tbl_cell),
            Paragraph(f"{item[2]:,.2f}", tbl_num),
        ])

    # Totals
    tbl_rows.append([
        Paragraph("", tbl_cell),
        Paragraph("<b>Gross Hospital Bill Amount:</b>", tbl_hdr),
        Paragraph("", tbl_cell),
        Paragraph(f"<b>Rs. {claim_data['gross_bill_amount']}</b>", tbl_num),
    ])
    tbl_rows.append([
        Paragraph("", tbl_cell),
        Paragraph(f"Less: Non-Medical & Disallowed Expenses:", tbl_cell),
        Paragraph("", tbl_cell),
        Paragraph(f"Rs. {claim_data['disallowed_amount']}", tbl_num),
    ])
    tbl_rows.append([
        Paragraph("", tbl_cell),
        Paragraph("<b>Net Claimed Amount to Insurer:</b>", tbl_hdr),
        Paragraph("", tbl_cell),
        Paragraph(f"<b>Rs. {claim_data['net_claimed_amount']}</b>", tbl_num),
    ])

    t_items = Table(tbl_rows, colWidths=[36, 260, 114, 112])
    t_items.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor('#0f172a')),
        ('INNERGRID', (0,0), (-1,-4), 0.5, colors.HexColor('#94a3b8')),
        ('LINEBELOW', (0,0), (-1,0), 1, colors.HexColor('#0f172a')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f8fafc')),
        ('LINEABOVE', (0,-3), (-1,-3), 0.75, colors.HexColor('#0f172a')),
        ('LINEABOVE', (0,-1), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0,-3), (-1,-1), colors.HexColor('#f8fafc')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t_items)

    # ══════════════════════════════════════════════════════════
    # PAGE 3: POLICY SCHEDULE & BENEFICIARY ENTITLEMENT
    # ══════════════════════════════════════════════════════════
    story.append(PageBreak())
    add_letterhead("POLICY SCHEDULE & BENEFICIARY ENTITLEMENT")

    pol_meta = [
        [
            Paragraph(f"<b>Total Sum Insured:</b> Rs. {claim_data['sum_insured_words']}", meta_val),
            Paragraph(f"<b>Cumulative Bonus (NCB):</b> Rs. {claim_data['cumulative_bonus']}", meta_val),
        ],
        [
            Paragraph(f"<b>Effective Sum Insured:</b> Rs. {claim_data['effective_sum_insured']}", meta_val),
            Paragraph(f"<b>Policy Status:</b> {claim_data['policy_status']}", meta_val),
        ],
        [
            Paragraph(f"<b>Policy Term:</b> {claim_data['policy_term']}", meta_val),
            Paragraph(f"<b>Pre-Existing Waiting Period:</b> {claim_data['waiting_period']}", meta_val),
        ]
    ]
    t_pol = Table(pol_meta, colWidths=[260, 260])
    t_pol.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_pol)
    story.append(Spacer(1, 14))

    story.append(Paragraph("COVERED MEMBERS & LIVES INSURED", h2_style))
    story.append(Spacer(1, 5))

    mem_rows = [
        [
            Paragraph("<b>S.No</b>", tbl_hdr),
            Paragraph("<b>Member Full Name</b>", tbl_hdr),
            Paragraph("<b>Relationship</b>", tbl_hdr),
            Paragraph("<b>Gender</b>", tbl_hdr),
            Paragraph("<b>Date of Birth</b>", tbl_hdr),
            Paragraph("<b>Sum Insured (Rs.)</b>", tbl_hdr),
        ]
    ]
    for idx, mem in enumerate(claim_data['members'], 1):
        mem_rows.append([
            Paragraph(str(idx), tbl_cell),
            Paragraph(mem['name'], tbl_cell),
            Paragraph(mem['relation'], tbl_cell),
            Paragraph(mem['gender'], tbl_cell),
            Paragraph(mem['dob'], tbl_cell),
            Paragraph(mem['sum_insured'], tbl_cell),
        ])

    t_mem = Table(mem_rows, colWidths=[30, 160, 80, 60, 80, 112])
    t_mem.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor('#0f172a')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#94a3b8')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f8fafc')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t_mem)
    story.append(Spacer(1, 16))

    decl_text = (
        "Declaration: This document is issued as an authorized electronic document under the Information Technology Act, 2000. "
        f"All coverage terms are subject to standard {claim_data['insurer_name']} policy wordings registered with IRDAI."
    )
    story.append(Paragraph(decl_text, legal_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    return out_path


# ── Define the 4 Test Dossiers ────────────────────────────────

DOSSIERS = [
    {
        "filename": "sample_insurance_claim_dossier_apollo_cardiology.pdf",
        "insurer_name": "Star Health and Allied Insurance Co. Ltd.",
        "irdai_reg": "129",
        "cin": "L66010TN2005PLC056649",
        "website": "www.starhealth.in",
        "toll_free": "1800-425-2255",
        "email": "claims@starhealth.in",
        "tpa_name": "In-house Claims Management Dept",
        "pre_auth_no": "SHAI-HYD-2026-99214",
        "pre_auth_date": "18-Jan-2026",
        "policy_number": "SH/2025/1102/084920",
        "plan_name": "Family Health Optima Insurance Plan",
        "patient_name": "Rajeshwari Venkataraman",
        "age_gender": "58 Years / Female",
        "aadhaar_number": "6541 8920 3314",
        "abha_id": "91-4412-8871-3329",
        "phone": "+91 94401 22987",
        "address": "Flat 302, Sri Krishna Residency, Jubilee Hills, Hyderabad - 500033",
        "hospital_name": "Apollo Hospitals, Jubilee Hills",
        "claim_number": "APL-HYD-2026-44102",
        "diagnosis": "Acute Coronary Syndrome (NSTEMI) with Triple Vessel Disease",
        "procedure": "Percutaneous Transluminal Coronary Angioplasty (PTCA) with Drug Eluting Stent",
        "admission_date": "18-Jan-2026",
        "discharge_date": "21-Jan-2026",
        "treating_doctor": "Dr. K. S. Somasekhar",
        "specialty": "Interventional Cardiology",
        "requested_amount": "4,10,000",
        "approved_amount": "3,40,000",
        "copay": "0% (Nil)",
        "deductible": "Nil",
        "room_category": "Single Executive Room (Sanctioned)",
        "icu_category": "Coronary Care Unit (CCU) - Full Coverage",
        "bill_number": "APL/INV/2026/01/9812",
        "bill_date": "21-Jan-2026",
        "bill_items": [
            ("CCU Bed Charges (2 Days)", "15,000 / day", 30000.00),
            ("Executive Room Rent (1 Day)", "7,500 / day", 7500.00),
            ("Cath Lab & Angioplasty Suite Charges", "Lumpsum", 55000.00),
            ("Drug-Eluting Stents (2 Xience Sierra DES)", "65,000 / unit", 130000.00),
            ("Primary Interventional Cardiologist Fees", "Consultant", 65000.00),
            ("Cardiac Anesthetist & Perfusionist Charges", "Consultant", 22000.00),
            ("Cardiac Medications & Anti-platelet Infusions", "Pharmacy", 34200.00),
            ("Coronary Angiography (CAG) Pre-procedure", "Investigation", 18500.00),
            ("Cardiac Biomarkers (Troponin-I, CK-MB, Lipid, Echo)", "Diagnostic Lab", 14300.00),
            ("Biomedical Waste & Administrative Handling", "Hospital MIS", 8500.00),
        ],
        "gross_bill_amount": "3,85,000.00",
        "disallowed_amount": "12,500.00",
        "net_claimed_amount": "3,72,500.00",
        "sum_insured_words": "15,00,000 (Fifteen Lakhs)",
        "cumulative_bonus": "3,50,000",
        "effective_sum_insured": "18,50,000",
        "policy_status": "ACTIVE / IN FORCE",
        "policy_term": "01-Apr-2025 to 31-Mar-2026",
        "waiting_period": "36 Months (Completed)",
        "members": [
            {"name": "Rajeshwari Venkataraman", "relation": "Self (Primary)", "gender": "Female", "dob": "1968-04-12", "sum_insured": "15,00,000 (Floater)"},
            {"name": "S. Venkataraman", "relation": "Spouse", "gender": "Male", "dob": "1964-09-28", "sum_insured": "15,00,000 (Floater)"},
        ]
    },
    {
        "filename": "sample_insurance_claim_dossier_fortis_orthopedics.pdf",
        "insurer_name": "ICICI Lombard General Insurance Company Ltd.",
        "irdai_reg": "115",
        "cin": "L67200MH2000PLC129408",
        "website": "www.icicilombard.com",
        "toll_free": "1800-2666",
        "email": "ihealthcare@icicilombard.com",
        "tpa_name": "ICICI Lombard Health Care TPA",
        "pre_auth_no": "IL-GGN-2026-55102",
        "pre_auth_date": "05-Feb-2026",
        "policy_number": "4015/IL/0091823/00/000",
        "plan_name": "Complete Health Insurance - Elevate Plan",
        "patient_name": "Harish Chandra Mehra",
        "age_gender": "67 Years / Male",
        "aadhaar_number": "7712 9044 1823",
        "abha_id": "91-1192-3344-7788",
        "phone": "+91 98110 44556",
        "address": "House 142, Sector 45, Gurugram, Haryana - 122003",
        "hospital_name": "Fortis Memorial Research Institute",
        "claim_number": "FMRI-IP-2026-08144",
        "diagnosis": "Severe Tricompartmental Osteoarthritis Right Knee (Grade IV)",
        "procedure": "Unilateral Total Knee Replacement (TKR Right) with Computer Navigation",
        "admission_date": "05-Feb-2026",
        "discharge_date": "09-Feb-2026",
        "treating_doctor": "Dr. Ashok Rajgopal",
        "specialty": "Orthopedics & Joint Reconstruction",
        "requested_amount": "3,10,000",
        "approved_amount": "2,60,000",
        "copay": "0% (Nil)",
        "deductible": "Nil",
        "room_category": "Twin Sharing Private (Sanctioned)",
        "icu_category": "Post-op Stepdown ICU (1 Day)",
        "bill_number": "FMRI/BILL/2026/02/1129",
        "bill_date": "09-Feb-2026",
        "bill_items": [
            ("Room Rent - Twin Sharing (3 Days)", "5,000 / day", 15000.00),
            ("Step Down Surgical ICU (1 Day)", "10,000 / day", 10000.00),
            ("Operation Theatre & Robotic Navigation Suite", "Lumpsum", 48000.00),
            ("Stryker Triathlon Knee Implant (High Flex)", "Implant Unit", 95000.00),
            ("Chief Joint Replacement Surgeon Professional Fee", "Consultant", 50000.00),
            ("Anesthetist Charges (Regional Spinal/Epidural)", "Consultant", 16000.00),
            ("Physiotherapy & CPM Machine Rehabilitation", "Per Session", 9500.00),
            ("Pharmacy, Bone Cement & Surgical Consumables", "Itemized list", 28500.00),
            ("Pre-operative Digital X-Rays, MRI & Blood Work", "Diagnostics", 14800.00),
            ("Dietary & General Administrative Overhead", "Hospital Fee", 8200.00),
        ],
        "gross_bill_amount": "2,95,000.00",
        "disallowed_amount": "8,200.00",
        "net_claimed_amount": "2,86,800.00",
        "sum_insured_words": "10,00,000 (Ten Lakhs)",
        "cumulative_bonus": "2,00,000",
        "effective_sum_insured": "12,00,000",
        "policy_status": "ACTIVE / IN FORCE",
        "policy_term": "15-May-2025 to 14-May-2026",
        "waiting_period": "36 Months (Completed)",
        "members": [
            {"name": "Harish Chandra Mehra", "relation": "Self (Primary)", "gender": "Male", "dob": "1959-02-14", "sum_insured": "10,00,000 (Floater)"},
            {"name": "Kamla Mehra", "relation": "Spouse", "gender": "Female", "dob": "1963-08-19", "sum_insured": "10,00,000 (Floater)"},
        ]
    },
    {
        "filename": "sample_insurance_claim_dossier_max_nephrology.pdf",
        "insurer_name": "Care Health Insurance Limited",
        "irdai_reg": "148",
        "cin": "U66000DL2007PLC161503",
        "website": "www.careinsurance.com",
        "toll_free": "1800-102-4488",
        "email": "customerfirst@careinsurance.com",
        "tpa_name": "Care Health In-House TPA",
        "pre_auth_no": "CARE-DEL-2026-10944",
        "pre_auth_date": "14-Mar-2026",
        "policy_number": "14088219/2025/01",
        "plan_name": "Care Advantage - High Sum Health Plan",
        "patient_name": "Pooja Sundaram",
        "age_gender": "36 Years / Female",
        "aadhaar_number": "5512 8840 9912",
        "abha_id": "91-7788-9900-1122",
        "phone": "+91 99100 88231",
        "address": "B-404, Pinnacle Heights, Saket, New Delhi - 110017",
        "hospital_name": "Max Super Speciality Hospital, Saket",
        "claim_number": "MSSH-SKT-2026-03918",
        "diagnosis": "Right Upper Ureteric Calculus (8.5mm) with Acute Hydronephrosis & Urosepsis",
        "procedure": "Right Ureteroscopy (URS) + Holmium Laser Lithotripsy + Double-J (DJ) Stenting",
        "admission_date": "14-Mar-2026",
        "discharge_date": "16-Mar-2026",
        "treating_doctor": "Dr. Dinesh Khullar",
        "specialty": "Urology & Renal Sciences",
        "requested_amount": "1,25,000",
        "approved_amount": "1,05,000",
        "copay": "0% (Nil)",
        "deductible": "Nil",
        "room_category": "Single Private Room (Eligible)",
        "icu_category": "Nil (Daycare / Short Stay Ward)",
        "bill_number": "MAX/SAK/2026/03/7718",
        "bill_date": "16-Mar-2026",
        "bill_items": [
            ("Room Rent - Single Private (2 Days)", "6,000 / day", 12000.00),
            ("Endourology Operating Theatre Charges", "Lumpsum", 28500.00),
            ("Holmium Laser Equipment & Fiber Charges", "Per Case", 15000.00),
            ("Consultant Urologist & Laser Surgeon Fees", "Surgeon", 28000.00),
            ("Anesthetist Professional Fee", "Consultant", 9500.00),
            ("Cook Medical DJ Stent & Guidewire Consumables", "Implant Item", 8500.00),
            ("IV Antibiotics, Fluids & Pain Management", "Pharmacy", 6800.00),
            ("NCCT KUB & Renal Ultrasonography", "Radiology", 6200.00),
            ("Urine C/S, Serum Creatinine, CBC, Electrolytes", "Lab Medicine", 4000.00),
        ],
        "gross_bill_amount": "1,18,500.00",
        "disallowed_amount": "4,300.00",
        "net_claimed_amount": "1,14,200.00",
        "sum_insured_words": "7,50,000 (Seven Lakhs Fifty Thousand)",
        "cumulative_bonus": "1,50,000",
        "effective_sum_insured": "9,00,000",
        "policy_status": "ACTIVE / IN FORCE",
        "policy_term": "01-Oct-2025 to 30-Sep-2026",
        "waiting_period": "24 Months (Completed)",
        "members": [
            {"name": "Pooja Sundaram", "relation": "Self (Primary)", "gender": "Female", "dob": "1989-11-04", "sum_insured": "7,50,000 (Individual)"},
        ]
    },
    {
        "filename": "sample_insurance_claim_dossier_manipal_infectious.pdf",
        "insurer_name": "Niva Bupa Health Insurance Company Limited",
        "irdai_reg": "145",
        "cin": "U66000DL2008PLC182918",
        "website": "www.nivabupa.com",
        "toll_free": "1860-500-8888",
        "email": "customercare@nivabupa.com",
        "tpa_name": "Niva Bupa Health Claims Division",
        "pre_auth_no": "NB-BLR-2026-88190",
        "pre_auth_date": "22-Apr-2026",
        "policy_number": "31889920/NIVA/2025/08",
        "plan_name": "ReAssure 2.0 - Comprehensive Health Plan",
        "patient_name": "Devendra Mohan Joshi",
        "age_gender": "29 Years / Male",
        "aadhaar_number": "4491 2231 7765",
        "abha_id": "91-3322-1144-5566",
        "phone": "+91 98450 77112",
        "address": "Flat 502, Prestige Palms, Indiranagar, Bengaluru - 560038",
        "hospital_name": "Manipal Hospital, Old Airport Road",
        "claim_number": "MH-BLR-2026-11928",
        "diagnosis": "Dengue Hemorrhagic Fever (Grade II) with Severe Thrombocytopenia (Platelets 18,000) & Plasma Leakage",
        "procedure": "Medical Intensive Care, Continuous Hemodynamic Monitoring & Single Donor Apheresis Platelet (SDAP) Transfusion",
        "admission_date": "22-Apr-2026",
        "discharge_date": "26-Apr-2026",
        "treating_doctor": "Dr. Neha Gupta",
        "specialty": "Internal Medicine & Critical Care",
        "requested_amount": "1,85,000",
        "approved_amount": "1,45,000",
        "copay": "0% (Nil)",
        "deductible": "Nil",
        "room_category": "Semi-Private Ward (Sanctioned)",
        "icu_category": "Medical ICU (MICU) - 2 Days Sanctioned",
        "bill_number": "MH/INV/2026/04/5529",
        "bill_date": "26-Apr-2026",
        "bill_items": [
            ("MICU Bed & Multi-para Monitor Charges (2 Days)", "18,000 / day", 36000.00),
            ("Semi-Private Room Rent (2 Days)", "4,500 / day", 9000.00),
            ("Intensivist & Critical Care Physician Charges", "Per Day", 24000.00),
            ("Single Donor Apheresis Platelet (SDAP Unit x 2)", "14,500 / unit", 29000.00),
            ("Blood Bank Processing, Cross-matching & Transfusion", "Service", 8500.00),
            ("IV Fluids (Colloids & Crystalloids), Anti-pyretics & Antibiotics", "Pharmacy", 22400.00),
            ("Serial Complete Blood Count (CBC x 8 runs)", "Diagnostic Lab", 7200.00),
            ("Dengue NS1, IgM, LFT, KUB Ultrasound, Coagulation Profile", "Special Lab", 18500.00),
            ("Nursing & Continuous Critical Care Surveillance", "Nursing Care", 7800.00),
            ("Hospital Administrative & Disallowed Surcharges", "Admin Fees", 5600.00),
        ],
        "gross_bill_amount": "1,68,000.00",
        "disallowed_amount": "5,600.00",
        "net_claimed_amount": "1,62,400.00",
        "sum_insured_words": "10,00,000 (Ten Lakhs)",
        "cumulative_bonus": "2,50,000",
        "effective_sum_insured": "12,50,000",
        "policy_status": "ACTIVE / IN FORCE",
        "policy_term": "01-Jul-2025 to 30-Jun-2026",
        "waiting_period": "36 Months (Completed)",
        "members": [
            {"name": "Devendra Mohan Joshi", "relation": "Self (Primary)", "gender": "Male", "dob": "1997-06-21", "sum_insured": "10,00,000 (Individual)"},
        ]
    }
]


def main():
    print(f"Generating 4 multi-page insurance claim dossiers in {SAMPLE_DIR}...")
    for dossier in DOSSIERS:
        fname = dossier['filename']
        path = build_dossier_pdf(fname, dossier)
        size_kb = os.path.getsize(path) / 1024
        print(f"  \u2713 Created: {fname} ({size_kb:.1f} KB)")
    print("All 4 test dossiers created successfully!")


if __name__ == "__main__":
    main()
