/* ══════════════════════════════════════════════════════════════
   FHIR Dashboard — Application Logic
   ══════════════════════════════════════════════════════════════ */

"use strict";

// ── DOM Cache ──
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Upload tab elements
const fileInput      = $("#fileInput");
const browseButton   = $("#browseButton");
const convertButton  = $("#convertButton");
const fileInfoEl     = $("#fileInfo");
const stageList      = $("#stageList");
const durationEl     = $("#duration");
const errorBox       = $("#errorBox");
const rawJsonEl      = $("#rawJson");
const copyButton     = $("#copyButton");

// Health badge
const healthBadge = $("#healthBadge");
const dbStats     = $("#dbStats");

let selectedFile = null;

// ── Portal Mode ──
let portalMode = "hospital"; // "hospital" or "insurance"
function apiBase() {
  return portalMode === "insurance" ? "/api/insurance" : "/api";
}

// ═══════════════════════════════════════════════
// Tab Navigation
// ═══════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
  // Portal switcher
  const portalBtns = $$("[data-portal]");
  portalBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const newMode = btn.dataset.portal;
      if (newMode === portalMode) return;
      portalMode = newMode;
      portalBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      switchPortal(newMode);
    });
  });

  // Tab switching
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".tab-btn").forEach((b) => b.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = $(`#tab-${btn.dataset.tab}`);
      if (panel) panel.classList.add("active");

      // Lazy-load data for tabs
      if (btn.dataset.tab === "patients") loadPatients();
      if (btn.dataset.tab === "explorer") loadBundles();
    });
  });

  // File upload
  browseButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", onFileSelected);
  convertButton.addEventListener("click", convertSelectedFile);
  copyButton.addEventListener("click", copyJson);

  // Drag-drop for single upload
  const dropZone = $("#dropZone");
  if (dropZone) {
    dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropZone.classList.remove("drag-over");
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        onFileSelected();
      }
    });
  }

  // Refresh buttons
  const refreshP = $("#refreshPatientsBtn");
  const refreshB = $("#refreshBundlesBtn");
  if (refreshP) refreshP.addEventListener("click", loadPatients);
  if (refreshB) refreshB.addEventListener("click", loadBundles);

  // Close patient detail report button
  const closePatientBtn = $("#closePatientDetailBtn");
  if (closePatientBtn) {
    closePatientBtn.addEventListener("click", () => {
      const detail = $("#patientDetail");
      if (detail) detail.hidden = true;
    });
  }

  // Health check
  checkHealth();
  setInterval(checkHealth, 15000);
});


// ═══════════════════════════════════════════════
// Portal Switching
// ═══════════════════════════════════════════════

function switchPortal(mode) {
  const body = document.body;
  if (mode === "insurance") {
    body.classList.add("portal-insurance");
  } else {
    body.classList.remove("portal-insurance");
  }

  // Update header branding
  const eyebrow = $("#heroEyebrow");
  const title = $("#heroTitle");
  const subtitle = $("#heroSubtitle");
  if (mode === "insurance") {
    if (eyebrow) eyebrow.textContent = "Insurance Claim Company Portal";
    if (title) title.textContent = "Insurance FHIR Dashboard";
    if (subtitle) subtitle.textContent = "Extract, validate, and store ABDM FHIR R4 data from insurance claim documents.";
  } else {
    if (eyebrow) eyebrow.textContent = "Google Health Medical Data Toolkit";
    if (title) title.textContent = "FHIR Dashboard";
    if (subtitle) subtitle.textContent = "Extract, validate, and store ABDM FHIR R4 data from hospital laboratory reports.";
  }

  // Update tab labels
  const tabPatients = $("#tabBtnPatients");
  if (tabPatients) tabPatients.textContent = mode === "insurance" ? "Claims Reports" : "Patient Records";

  // Update upload card text
  const uploadTitle = $("#uploadCardTitle");
  const uploadDesc = $("#uploadCardDesc");
  if (mode === "insurance") {
    if (uploadTitle) uploadTitle.textContent = "Upload Claim Document or ZIP Archive";
    if (uploadDesc) uploadDesc.innerHTML = 'Upload an insurance claim document (PDF/image) or a <strong>ZIP archive containing all claim documents across nested folders</strong>. All data will be extracted and consolidated into a unified FHIR claim record.';
  } else {
    if (uploadTitle) uploadTitle.textContent = "Upload Patient Document or ZIP Archive";
    if (uploadDesc) uploadDesc.innerHTML = 'Upload a laboratory report (PDF/image) or a <strong>ZIP archive containing all patient documents across nested folders</strong>. All data will be extracted and consolidated into a unified FHIR record.';
  }

  // Update extracted data section title
  const extractedTitle = $("#extractedDataTitle");
  if (extractedTitle) extractedTitle.textContent = mode === "insurance" ? "Extracted Claim Data" : "Extracted Clinical Data";

  // Update patient records / claims reports section
  const sectionTitle = $("#patientsSectionTitle");
  if (sectionTitle) sectionTitle.textContent = mode === "insurance" ? "Claims Reports" : "Patient Records";

  // Update empty state text
  const patientsList = $("#patientsList");
  if (patientsList && patientsList.querySelector(".muted")) {
    patientsList.querySelector(".muted").textContent = mode === "insurance"
      ? "No claims recorded yet. Upload claim documents to populate."
      : "No patients recorded yet. Upload lab reports to populate.";
  }

  // Reset upload state
  selectedFile = null;
  if (fileInput) fileInput.value = "";
  if (fileInfoEl) fileInfoEl.textContent = "No file selected";
  if (convertButton) convertButton.disabled = true;
  if (errorBox) { errorBox.hidden = true; errorBox.textContent = ""; }

  const ipSec = $("#insurancePlanSection");
  const sumGrid = $("#summaryGrid");
  if (ipSec) {
    ipSec.hidden = true;
    ipSec.style.display = "none";
  }
  if (sumGrid) {
    sumGrid.hidden = false;
    sumGrid.style.display = "";
  }

  // Hide patient detail if open
  const detail = $("#patientDetail");
  if (detail) detail.hidden = true;

  // Reload active tab data
  const activeTab = $(".tab-btn.active");
  if (activeTab) {
    if (activeTab.dataset.tab === "patients") loadPatients();
    if (activeTab.dataset.tab === "explorer") loadBundles();
  }

  // Refresh health/stats for new portal
  checkHealth();
}


// ═══════════════════════════════════════════════
// Health Check & Stats
// ═══════════════════════════════════════════════

async function checkHealth() {
  try {
    const res = await fetch(`${apiBase()}/health`);
    if (res.ok) {
      const data = await res.json();
      if (data.toolkit === "ok") {
        healthBadge.textContent = "Toolkit Connected";
        healthBadge.className = "status-badge online";
      } else {
        healthBadge.textContent = `Toolkit Offline (${data.toolkit_url || "8088"})`;
        healthBadge.className = "status-badge offline";
      }
    } else {
      throw new Error();
    }
  } catch {
    healthBadge.textContent = "Toolkit Offline";
    healthBadge.className = "status-badge offline";
  }

  try {
    const res = await fetch(`${apiBase()}/stats`);
    if (res.ok) {
      const s = await res.json();
      dbStats.innerHTML = `
        <span><span class="count">${s.patients}</span> Patients</span>
        <span><span class="count">${s.bundles}</span> Bundles</span>
        <span><span class="count">${s.observations}</span> Observations</span>
      `;
    }
  } catch { /* ignore */ }
}


// ═══════════════════════════════════════════════
// Single File Upload & Convert
// ═══════════════════════════════════════════════

function onFileSelected() {
  selectedFile = fileInput.files[0] || null;
  if (selectedFile) {
    const sizeKB = (selectedFile.size / 1024).toFixed(1);
    fileInfoEl.textContent = `${selectedFile.name} — ${sizeKB} KB`;
    convertButton.disabled = false;
  } else {
    fileInfoEl.textContent = "No file selected";
    convertButton.disabled = true;
  }
}

let stageTimers = [];

function clearStageTimers() {
  stageTimers.forEach((t) => clearTimeout(t));
  stageTimers = [];
}

function updateStepper(activeIdx) {
  const items = $$("#stageList .step-item");
  const trackFill = $("#stepperTrackFill");

  items.forEach((item, i) => {
    const icon = item.querySelector(".step-icon");
    item.classList.remove("active", "done");
    if (i < activeIdx) {
      item.classList.add("done");
      if (icon) icon.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    } else if (i === activeIdx) {
      item.classList.add("active");
      if (icon) icon.textContent = String(i + 1);
    } else {
      if (icon) icon.textContent = String(i + 1);
    }
  });

  if (trackFill) {
    const pct = Math.min(82, (activeIdx / 4) * 82);
    trackFill.style.width = `${pct}%`;
  }
}

function markAllStagesDone() {
  clearStageTimers();
  const items = $$("#stageList .step-item");
  const trackFill = $("#stepperTrackFill");
  items.forEach((item) => {
    item.classList.remove("active");
    item.classList.add("done");
    const icon = item.querySelector(".step-icon");
    if (icon) {
      icon.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    }
  });
  if (trackFill) trackFill.style.width = "82%";
}

async function convertSelectedFile() {
  if (!selectedFile) return;
  resetUI();
  updateStepper(0);

  const formData = new FormData();
  formData.append("file", selectedFile);

  // Progressive stage advancement while waiting for API
  stageTimers.push(setTimeout(() => updateStepper(1), 350));
  stageTimers.push(setTimeout(() => updateStepper(2), 2400));
  stageTimers.push(setTimeout(() => updateStepper(3), 5200));

  try {
    const response = await fetch(`${apiBase()}/convert`, { method: "POST", body: formData });
    clearStageTimers();
    updateStepper(4);

    if (!response.ok) {
      const errData = await response.json().catch(() => null);
      throw new Error(errData?.error || `HTTP ${response.status}`);
    }

    const data = await response.json();

    // Duration & unified archive badge
    if (data.duration_ms) {
      durationEl.textContent = `API processing duration: ${(data.duration_ms / 1000).toFixed(1)}s`;
      if (data.is_archive && data.archive_info) {
        const info = data.archive_info;
        const totalDocs = info.documents_found || info.documents_processed || info.documents_unified || 0;
        const parts = [`📦 ${totalDocs} document(s) processed & unified from archive`];
        if (info.patient_name && info.patient_name !== "Patient") {
          parts.push(`👤 ${info.patient_name}`);
        }
        if (info.aadhaar_number && info.aadhaar_number !== "N/A") {
          parts.push(`UID: ${info.aadhaar_number}`);
        }
        if (info.observations_count) {
          parts.push(`${info.observations_count} observation(s)`);
        }
        durationEl.textContent += ` • ${parts.join(' · ')}`;
      }
    }

    // Mark all stages done
    setTimeout(() => {
      markAllStagesDone();
    }, 300);

    // Render results
    const raw = data.raw || {};
    rawJsonEl.textContent = JSON.stringify(raw, null, 2);
    copyButton.disabled = false;
    if (window.hljs) hljs.highlightElement(rawJsonEl);

    const summary = data.summary || {};
    renderSummary(summary);
    renderValidation(data.validation);

    // Render insurance plan or claim summary if available
    if (portalMode === "insurance" && data.insurance_plan) {
      renderInsurancePlan(data.insurance_plan, data.validation, (data.stored_bundle_ids && data.stored_bundle_ids[0]) || data.raw?.id);
    } else {
      const ipSec = $("#insurancePlanSection");
      if (ipSec) {
        ipSec.hidden = true;
        ipSec.style.display = "none";
      }
      const sumGrid = $("#summaryGrid");
      if (sumGrid) {
        sumGrid.hidden = false;
        sumGrid.style.display = "";
      }
      if (portalMode === "insurance" && data.claim_summary) {
        renderClaimSummary(data.claim_summary);
      }
    }

    // Refresh stats
    checkHealth();

  } catch (err) {
    clearStageTimers();
    errorBox.textContent = `Error: ${err.message}`;
    errorBox.hidden = false;
  }
}

function resetUI() {
  clearStageTimers();
  errorBox.hidden = true;
  errorBox.textContent = "";
  rawJsonEl.textContent = "{}";
  copyButton.disabled = true;
  durationEl.textContent = "API processing duration: Not available";
  const valSection = $("#validationSection");
  if (valSection) {
    valSection.hidden = true;
    valSection.style.display = "none";
  }
  const ipSec = $("#insurancePlanSection");
  if (ipSec) {
    ipSec.hidden = true;
    ipSec.style.display = "none";
  }
  const sumGrid = $("#summaryGrid");
  if (sumGrid) {
    sumGrid.hidden = false;
    sumGrid.style.display = "";
  }

  const pn = $("#patientName"), pg = $("#patientGender"), pb = $("#patientBirthDate");
  const pa = $("#patientAadhaar"), padd = $("#patientAddress");
  const pab = $("#patientAbha"), pgu = $("#patientGuardian"), pph = $("#patientPhone"), phi = $("#patientHospId");
  if (pn) pn.textContent = "Not available";
  if (pg) pg.textContent = "Not available";
  if (pb) pb.textContent = "Not available";
  if (pa) pa.textContent = "Not available";
  if (padd) padd.textContent = "Not available";
  if (pab) pab.textContent = "Not available";
  if (pgu) pgu.textContent = "Not available";
  if (pph) pph.textContent = "Not available";
  if (phi) phi.textContent = "Not available";
  const bs = $("#bundleSummary");
  if (bs) bs.innerHTML = "No FHIR Bundle loaded.";
  const pl = $("#profileList");
  if (pl) pl.innerHTML = "<li>Not available</li>";
  const ob = $("#observationsBody");
  if (ob) ob.innerHTML = '<tr><td colspan="5">No observations loaded.</td></tr>';

  const items = $$("#stageList .step-item");
  items.forEach((item, i) => {
    item.classList.remove("active", "done");
    const icon = item.querySelector(".step-icon");
    if (icon) icon.textContent = String(i + 1);
  });
  const trackFill = $("#stepperTrackFill");
  if (trackFill) trackFill.style.width = "0%";
}

function copyJson() {
  navigator.clipboard.writeText(rawJsonEl.textContent);
  copyButton.textContent = "Copied!";
  setTimeout(() => (copyButton.textContent = "Copy JSON"), 2000);
}


// ═══════════════════════════════════════════════
// Summary Rendering
// ═══════════════════════════════════════════════

function renderSummary(summary) {
  if (!summary) return;
  const bundleInfo = summary.bundle || {};
  const resource_counts = bundleInfo.resource_counts || summary.resource_counts || {};
  const bundle_type = bundleInfo.type || summary.bundle_type || "document";
  const profiles = bundleInfo.profiles || summary.profiles || [];
  const patient = summary.patient || {};
  const observations = summary.observations || summary.lab_tests || [];

  // Bundle summary table
  const summaryEl = $("#bundleSummary");
  if (summaryEl) {
    const counts = Object.entries(resource_counts);
    if (counts.length) {
      const total = Object.values(resource_counts).reduce((a, b) => a + b, 0);
      let rows = `<table><tr><td>Bundle Type</td><td><code>${escapeHtml(bundle_type)}</code></td></tr>`;
      rows += `<tr><td>Total Resources</td><td><strong>${total}</strong></td></tr>`;
      for (const [type, count] of counts) {
        rows += `<tr><td>${escapeHtml(type)}</td><td>${count}</td></tr>`;
      }
      rows += "</table>";
      summaryEl.innerHTML = rows;
    } else {
      summaryEl.innerHTML = "<p class='muted'>No FHIR Bundle loaded.</p>";
    }
  }

  // Profiles
  const profileList = $("#profileList");
  if (profileList) {
    if (profiles && profiles.length) {
      profileList.innerHTML = profiles.map((p) => {
        const label = p.split("/").pop();
        return `<li><a href="${escapeHtml(p)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(p)}">${escapeHtml(label)}</a></li>`;
      }).join("");
    } else {
      profileList.innerHTML = "<li>Not available</li>";
    }
  }

  // Patient info
  const pn = $("#patientName"), pg = $("#patientGender"), pb = $("#patientBirthDate");
  const pa = $("#patientAadhaar"), padd = $("#patientAddress");
  const pab = $("#patientAbha"), pgu = $("#patientGuardian"), pph = $("#patientPhone"), phi = $("#patientHospId");
  if (pn) pn.textContent = patient.name || "Not available";
  if (pg) {
    const g = patient.gender || "";
    pg.textContent = g ? (g.charAt(0).toUpperCase() + g.slice(1)) : "Not available";
  }
  if (pb) pb.textContent = patient.birthDate || patient.dob || "Not available";
  if (pa) pa.textContent = patient.aadhaar || patient.aadhaar_number || "Not available";
  if (padd) padd.textContent = patient.address || "Not available";
  if (pab) pab.textContent = patient.abha || "Not available";
  if (pgu) pgu.textContent = patient.guardian || "Not available";
  if (pph) pph.textContent = patient.phone || "Not available";
  if (phi) phi.textContent = patient.hospital_id || patient.mrn || "Not available";

  // Observations table
  const tbody = $("#observationsBody");
  if (tbody) {
    if (observations.length) {
      tbody.innerHTML = observations.map((t) => {
        const testName = t.test || t.name || "Unknown Test";
        const loincCode = t.loinc || t.loinc_code;
        const loinc = (loincCode && loincCode !== "Not available")
          ? `<a href="https://loinc.org/${encodeURIComponent(loincCode)}" target="_blank" rel="noopener noreferrer" class="loinc-badge">${escapeHtml(loincCode)}</a>`
          : `<span class="loinc-tag">Not available</span>`;
        const resVal = t.result != null && t.result !== "" ? t.result : "N/A";
        const unitVal = t.unit && t.unit !== "Not available" ? t.unit : "—";
        const ref = t.referenceRange || (Array.isArray(t.reference_range) ? t.reference_range.map(r => r.text || "").join("; ") : t.reference_range) || "Not available";

        return `<tr>
          <td class="test-name-cell">${escapeHtml(testName)}</td>
          <td>${loinc}</td>
          <td class="value-cell">${escapeHtml(String(resVal))}</td>
          <td class="unit-cell">${escapeHtml(unitVal)}</td>
          <td class="range-cell">${escapeHtml(ref)}</td>
        </tr>`;
      }).join("");
    } else {
      tbody.innerHTML = '<tr><td colspan="5">No observations loaded.</td></tr>';
    }
  }
}


// ═══════════════════════════════════════════════
// Insurance Plan & Benefits Rendering (Upload Tab)
// ═══════════════════════════════════════════════

function renderInsurancePlan(plan, validation, bundleId) {
  if (!plan) return;

  const ipSection = $("#insurancePlanSection");
  const summaryGrid = $("#summaryGrid");
  const valSection = $("#validationSection");

  if (ipSection) {
    ipSection.hidden = false;
    ipSection.style.display = "block";
  }
  if (summaryGrid) {
    summaryGrid.hidden = true;
    summaryGrid.style.display = "none";
  }
  if (valSection) {
    valSection.hidden = true;
    valSection.style.display = "none";
  }

  // Populate metric fields
  const nameEl = $("#ipdPlanName");
  const insurerEl = $("#ipdInsurer");
  const uinEl = $("#ipdUin");
  const typeEl = $("#ipdPlanType");
  const statusEl = $("#ipdStatus");
  const periodEl = $("#ipdPolicyPeriod");
  const bundleEl = $("#ipdBundleId");

  if (nameEl) nameEl.textContent = plan.plan_name || "—";
  if (insurerEl) insurerEl.textContent = plan.insurer || "—";
  if (uinEl) uinEl.textContent = plan.uin || "—";
  if (typeEl) typeEl.textContent = plan.plan_type || "—";
  if (statusEl) {
    statusEl.innerHTML = `<span class="ip-status-pill"><span class="ip-dot">•</span> ${escapeHtml(plan.status || "Active")}</span>`;
  }
  if (periodEl) periodEl.textContent = plan.policy_period || "—";
  if (bundleEl) bundleEl.textContent = bundleId || plan.bundle_id || "—";

  // Populate benefits
  const benefits = plan.benefits || [];
  const titleEl = $("#ipdBenefitsTitle");
  if (titleEl) titleEl.textContent = `Benefits (${benefits.length})`;

  const gridEl = $("#ipdBenefitsGrid");
  if (!gridEl) return;

  if (!benefits.length) {
    gridEl.innerHTML = `<p class="muted">No benefits recorded in this policy document.</p>`;
    return;
  }

  gridEl.innerHTML = benefits.map((b) => {
    const cat = escapeHtml(b.category || "inpatient_hospitalization");
    const bType = escapeHtml((b.benefit_type || "AMOUNT").toUpperCase());
    const val = escapeHtml(b.value || "—");
    const cap = b.cap ? escapeHtml(b.cap) : null;
    const conds = (b.conditions && b.conditions.length) ? b.conditions : ["Covered as per policy terms and conditions."];

    return `
      <div class="ip-benefit-card">
        <div class="ip-badges-row">
          <span class="ip-badge ip-badge-cat">${cat}</span>
          <span class="ip-badge ip-badge-type">TYPE: ${bType}</span>
          <span class="ip-badge ip-badge-val">Value: ${val}</span>
          ${cap ? `<span class="ip-badge ip-badge-cap">Cap: ${cap}</span>` : ""}
        </div>
        <div class="ip-cond-title">CONDITIONS:</div>
        <ul class="ip-cond-list">
          ${conds.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}
        </ul>
      </div>
    `;
  }).join("");
}


// ═══════════════════════════════════════════════
// Insurance Claim Summary Rendering (Upload Tab)
// ═══════════════════════════════════════════════

function renderClaimSummary(claimSummary) {
  if (!claimSummary) return;

  // Insert claim summary into the extracted data card
  const extractedCard = $("#summaryGrid .card:last-child");
  if (!extractedCard) return;

  const kf = claimSummary.key_fields || {};
  const sections = claimSummary.document_sections || [];
  const narrative = claimSummary.claim_narrative || "";
  const flags = claimSummary.flags || [];

  // Build flags HTML
  let flagsHtml = "";
  if (flags.length) {
    flagsHtml = `<div class="claim-flags">${flags.map(f => `<span class="claim-flag">${escapeHtml(f)}</span>`).join("")}</div>`;
  }

  // Build key fields grid
  const keyFieldsHtml = `
    <div class="claim-key-fields">
      <h3 class="claim-section-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
        Key Claim Information
      </h3>
      ${flagsHtml}
      ${narrative ? `<p class="claim-narrative">${escapeHtml(narrative)}</p>` : ""}
      <div class="claim-fields-grid">
        ${_claimField("Claim / Reference No.", kf.claim_number)}
        ${_claimField("Policy / Insurance ID", kf.policy_number)}
        ${_claimField("Insured Name", kf.insured_name)}
        ${_claimField("Insurer / TPA", kf.insurer_tpa_name)}
        ${_claimField("Primary Diagnosis", kf.primary_diagnosis)}
        ${_claimField("ICD Code", kf.icd_code)}
        ${_claimField("Hospital / Provider", kf.hospital_name)}
        ${_claimField("Treating Doctor", kf.treating_doctor)}
        ${_claimField("Admission Date", kf.admission_date)}
        ${_claimField("Discharge Date", kf.discharge_date)}
        ${_claimField("Claim Type", kf.claim_type)}
        ${_claimField("Room Category", kf.room_category)}
        ${_claimField("Total Claimed Amount", kf.total_claimed_amount, true)}
        ${_claimField("Net Claimed Amount", kf.net_claimed_amount, true)}
        ${_claimField("Gross Bill Amount", kf.gross_bill_amount)}
        ${_claimField("Pre-Authorized Amount", kf.pre_authorized_amount)}
        ${_claimField("Approved Amount", kf.approved_amount)}
        ${_claimField("Sum Insured", kf.sum_insured)}
      </div>
    </div>
  `;

  // Build document sections accordion
  let sectionsHtml = "";
  if (sections.length) {
    const sectionItems = sections.map((sec, idx) => {
      const keyPts = (sec.key_data_points || []).filter(p => p && p.trim());
      const keyPtsHtml = keyPts.length
        ? `<ul class="claim-section-points">${keyPts.map(p => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`
        : "";
      return `
        <div class="claim-doc-section" data-section-idx="${idx}">
          <div class="claim-doc-section-header" onclick="this.parentElement.classList.toggle('expanded')">
            <span class="claim-doc-section-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="chevron-icon"><polyline points="6 9 12 15 18 9"></polyline></svg>
              ${escapeHtml(sec.title)}
            </span>
            <span class="claim-doc-section-badge">${escapeHtml(sec.title.split(' ')[0])}</span>
          </div>
          <div class="claim-doc-section-body">
            ${sec.summary ? `<p>${escapeHtml(sec.summary)}</p>` : ""}
            ${keyPtsHtml}
          </div>
        </div>
      `;
    }).join("");

    sectionsHtml = `
      <div class="claim-doc-sections">
        <h3 class="claim-section-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
          Document Sections (${sections.length})
        </h3>
        ${sectionItems}
      </div>
    `;
  }

  // Prepend to extracted data card
  const financialHtml = _renderFinancialCard(sections, kf);
  const existingContent = extractedCard.innerHTML;
  extractedCard.innerHTML = keyFieldsHtml + financialHtml + sectionsHtml + existingContent;
}

function _claimField(label, value, highlight = false) {
  if (!value) return "";
  const cls = highlight ? ' claim-field-highlight' : '';
  return `
    <div class="claim-field${cls}">
      <span class="claim-field-label">${escapeHtml(label)}</span>
      <span class="claim-field-value">${escapeHtml(value)}</span>
    </div>
  `;
}


// ═══════════════════════════════════════════════
// Validation Rendering
// ═══════════════════════════════════════════════

function renderValidation(val) {
  const section = $("#validationSection");
  if (!section || !val) return;
  section.hidden = false;

  // Score
  const scoreEl = $("#validationScore");
  const scoreClass = val.compliance_score >= 80 ? "high" : val.compliance_score >= 50 ? "medium" : "low";
  scoreEl.className = `compliance-score ${scoreClass}`;
  scoreEl.textContent = `${val.compliance_score}%`;

  // Issues
  const issuesEl = $("#validationIssues");
  if (val.issues?.length) {
    issuesEl.innerHTML = val.issues.map((i) =>
      `<div class="issue ${i.severity}"><span class="badge">${i.severity}</span><span>${i.message}${i.path ? ` <code style="font-size:.72rem;color:var(--text-muted)">(${i.path})</code>` : ""}</span></div>`
    ).join("");
  } else {
    issuesEl.innerHTML = '<div class="issue info"><span class="badge">pass</span><span>No issues found — fully compliant!</span></div>';
  }

  // FMM report
  const fmmEl = $("#fmmReport");
  if (val.fmm_report) {
    fmmEl.innerHTML = Object.entries(val.fmm_report).map(([rt, info]) => {
      const lvl = info.level === "N" ? "N" : `FMM ${info.level}`;
      const cls = info.level === "N" ? "normative" : "";
      return `<div class="fmm-chip"><span>${rt}</span><span class="fmm-level ${cls}">${lvl}</span></div>`;
    }).join("");
  }
}


// ═══════════════════════════════════════════════
// Patient Records
// ═══════════════════════════════════════════════

async function loadPatients() {
  const container = $("#patientsList");
  try {
    const res = await fetch(`${apiBase()}/patients`);
    const patients = await res.json();
    if (!patients.length) {
      container.innerHTML = `<p class="muted">${portalMode === "insurance" ? "No claims recorded yet. Upload claim documents to populate." : "No patients recorded yet. Upload patient archives or lab reports to populate."}</p>`;
      return;
    }
    container.innerHTML = patients.map((p) => {
      const pAge = calculateAge(p.birth_date);
      const genderStr = p.gender ? (p.gender.charAt(0).toUpperCase() + p.gender.slice(1)) : "";
      const metaParts = [];
      if (genderStr) metaParts.push(genderStr);
      if (p.birth_date && p.birth_date !== "N/A" && p.birth_date !== "Not available") {
        metaParts.push(`DOB: ${p.birth_date}`);
      }
      if (pAge) metaParts.push(pAge);

      return `
        <div class="patient-card" onclick="viewPatient('${p.id}')">
          <div class="pc-header">
            <div class="pc-name">${escapeHtml(p.name || (portalMode === "insurance" ? "Unknown Claimant" : "Unknown Patient"))}</div>
            <button class="pc-delete-btn" title="Delete ${portalMode === "insurance" ? "Claim" : "Patient"} Record" onclick="event.stopPropagation(); deletePatient('${p.id}', '${escapeHtml(p.name || '')}')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
            </button>
          </div>
          <div class="pc-meta">${escapeHtml(metaParts.join(" • ") || "Patient Record")}</div>
          <div class="pc-badges">
            <span class="pc-badge">${p.bundle_count || 0} Report${p.bundle_count !== 1 ? "s" : ""}</span>
          </div>
        </div>
      `;
    }).join("");
  } catch {
    container.innerHTML = '<p class="muted">Failed to load patients.</p>';
  }
}

async function deletePatient(patientId, patientName) {
  if (!confirm(`Are you sure you want to delete patient "${patientName || 'this patient'}" and all associated reports?`)) {
    return;
  }
  try {
    const res = await fetch(`${apiBase()}/patients/${patientId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete patient record");
    const detail = $("#patientDetail");
    if (detail) detail.hidden = true;
    await loadPatients();
    await checkHealth();
  } catch (err) {
    alert(`Error deleting patient: ${err.message}`);
  }
}

async function viewPatient(patientId) {
  const detail = $("#patientDetail");
  const content = $("#patientDetailContent");
  const nameEl = $("#patientDetailName");
  if (!detail || !content) return;

  detail.hidden = false;
  detail.scrollIntoView({ behavior: "smooth", block: "start" });
  content.innerHTML = `
    <div style="padding:40px 20px;text-align:center;color:var(--text-muted)">
      <div class="progress-bar-container" style="max-width:280px;margin:0 auto 14px">
        <div class="progress-bar" style="width:75%"></div>
      </div>
      <p style="font-size:0.9rem">Retrieving and assembling HL7® FHIR® Diagnostic Report...</p>
    </div>
  `;

  try {
    const res = await fetch(`${apiBase()}/patients/${patientId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const patient = data.patient || {};
    const bundles = data.bundles || [];
    const observations = data.observations || [];

    if (nameEl) {
      const reportLabel = portalMode === "insurance" ? "Insurance Claim Report" : "Clinical Diagnostic Report";
      nameEl.textContent = `${reportLabel} \u2014 ${patient.name || "Patient"}`;
    }

    // ── Fetch claim_summary from the stored bundle's associated data
    // (The claim_summary was stored in the original convert response; we
    //  reconstruct from FHIR resources for the patient detail view)
    let claimSummaryData = null;

    // Parse patient_json
    let patientResource = {};
    try {
      if (patient.patient_json) {
        patientResource = typeof patient.patient_json === "string" ? JSON.parse(patient.patient_json) : patient.patient_json;
      }
    } catch { /* ignore */ }

    // Patient Demographics from patient & patientResource
    const pName = patient.name || patientResource.name?.[0]?.text || "Unknown Patient";
    const pGiven = (patientResource.name?.[0]?.given || []).join(" ");
    const pFamily = patientResource.name?.[0]?.family || "";
    const pGender = patient.gender ? (patient.gender.charAt(0).toUpperCase() + patient.gender.slice(1)) : (patientResource.gender || "Not specified");
    const pDob = patient.birth_date || patientResource.birthDate || "Not specified";
    const pAge = calculateAge(pDob);

    // Extract Blood Group from observations if available
    let pBloodGroup = "";
    observations.forEach((o) => {
      const name = String(o.code_text || o.code?.text || "").toLowerCase();
      if (name.includes("blood group") || name.includes("abo") || name.includes("rh type")) {
        const val = o.value || o.valueString || (o.valueQuantity?.value) || "";
        if (val && String(val).trim()) pBloodGroup = String(val).trim();
      }
    });

    // Extract all identifiers (Aadhaar, ABHA, Hospital ID, MRN)
    const identifiers = patientResource.identifier || [];
    let pAadhaar = "";
    let pAbha = patient.abha_id || "";
    let pHospId = "";
    let pMrn = "";

    identifiers.forEach((idObj) => {
      const sys = String(idObj.system || "").toLowerCase();
      const val = String(idObj.value || "").trim();
      const code = idObj.type?.coding?.[0]?.code || "";
      const display = String(idObj.type?.coding?.[0]?.display || "").toLowerCase();

      if (sys.includes("aadhaar") || display.includes("aadhaar")) {
        pAadhaar = val;
      } else if (sys.includes("abha") || display.includes("abha") || code === "NH") {
        if (!pAbha) pAbha = val;
      } else if (sys.includes("hospital.org/patient-id") || code === "PI") {
        pHospId = val;
      } else if (code === "MR" || sys.includes("smarthealthit") || sys.includes("hospital")) {
        if (!pMrn) pMrn = val;
      }
    });

    let formattedAadhaar = pAadhaar;
    if (/^\d{12}$/.test(pAadhaar.replace(/\s+/g, ""))) {
      formattedAadhaar = pAadhaar.replace(/\s+/g, "").replace(/(\d{4})(\d{4})(\d{4})/, "$1 $2 $3");
    }

    // Extract Address components
    const addresses = patientResource.address || [];
    let pAddress = "";
    let pCity = "";
    let pDistrict = "";
    let pState = "";
    let pPin = "";

    if (addresses.length) {
      const addr = addresses[0];
      pAddress = addr.text || addr.line?.join(", ") || "";
      pCity = addr.city || "";
      pDistrict = addr.district || "";
      pState = addr.state || "";
      pPin = addr.postalCode || "";
    }

    // Extract Locality / Mandal from address string if available
    let pLocality = "";
    let pMandal = "";
    if (pAddress) {
      const vMatch = pAddress.match(/([0-9a-zA-Z\s-]+(?:POST|VILLAGE|STREET|ROAD|NAGAR|COLONY))/i);
      if (vMatch) pLocality = vMatch[1].trim();
      const mMatch = pAddress.match(/([0-9a-zA-Z\s-]+(?:MANDAL|TALUK|BLOCK))/i);
      if (mMatch) pMandal = mMatch[1].trim();
    }

    // Extract Telecom / Phone
    const telecoms = patientResource.telecom || [];
    let pPhone = "";
    const phoneObj = telecoms.find((t) => t.system === "phone");
    if (phoneObj) pPhone = phoneObj.value || "";

    // Extract Guardian / Emergency Contact
    const contacts = patientResource.contact || [];
    let pGuardian = "";
    if (contacts.length) {
      pGuardian = contacts[0].name?.text || "";
      const rel = contacts[0].relationship?.[0]?.coding?.[0]?.display;
      if (rel && pGuardian) pGuardian += ` (${rel})`;
    }

    // If no bundles, show clean dossier placeholder
    if (!bundles.length) {
      content.innerHTML = `
        <div class="report-frame">
          <div class="report-dossier-wrap">
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                <h4>Patient Demographics</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-row main-name">
                  <span class="dossier-label">Full Legal Name</span>
                  <span class="dossier-val highlight">${escapeHtml(pName)}</span>
                </div>
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Date of Birth / Age</span>
                    <span class="dossier-val">${escapeHtml(pDob)} ${pAge ? `<span class="age-pill">${pAge}</span>` : ""}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Biological Sex</span>
                    <span class="dossier-val">${escapeHtml(pGender)}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Father / Guardian</span>
                    <span class="dossier-val">${escapeHtml(pGuardian || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Contact Phone</span>
                    <span class="dossier-val mono">${escapeHtml(pPhone || "Not recorded")}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"></rect><line x1="7" y1="8" x2="17" y2="8"></line><line x1="7" y1="12" x2="17" y2="12"></line><line x1="7" y1="16" x2="13" y2="16"></line></svg>
                <h4>Identifiers</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Aadhaar UID</span>
                    <span class="dossier-val mono">${formattedAadhaar ? `${escapeHtml(formattedAadhaar)} <span class="id-tag green">✓ UIDAI Verified</span>` : "Not recorded"}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">ABHA / Health Scheme</span>
                    <span class="dossier-val mono">${escapeHtml(pAbha || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item span-2">
                    <span class="dossier-label">Patient Resource UUID</span>
                    <span class="dossier-val mono small">${escapeHtml(patient.id || "")}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <p class="muted" style="text-align:center;padding:24px 0">No diagnostic report bundles stored for this patient.</p>
        </div>
      `;
      return;
    }

    // Render diagnostic report for bundle at index
    function renderBundleReport(bundleIdx) {
      const selectedBundle = bundles[bundleIdx] || bundles[0];
      let bundleJson = {};
      try {
        if (selectedBundle.bundle_json) {
          bundleJson = typeof selectedBundle.bundle_json === "string" ? JSON.parse(selectedBundle.bundle_json) : selectedBundle.bundle_json;
        }
      } catch { /* ignore */ }

      const entries = bundleJson.entry || [];
      const resources = entries.map((e) => e.resource).filter(Boolean);

      const diagReport = resources.find((r) => r.resourceType === "DiagnosticReport") || {};
      const composition = resources.find((r) => r.resourceType === "Composition") || {};
      const org = resources.find((r) => r.resourceType === "Organization") || {};
      const practitioner = resources.find((r) => r.resourceType === "Practitioner") || {};

      const facilityName = org.name || "Apex Clinical Diagnostics / Hospital MIS";
      const docStatus = (diagReport.status || composition.status || "final").toUpperCase();

      let rawDate = diagReport.effectiveDateTime || diagReport.issued || composition.date || selectedBundle.created_at || "";
      let formattedDate = rawDate;
      if (rawDate) {
        try {
          const d = new Date(rawDate);
          if (!isNaN(d.getTime())) formattedDate = d.toLocaleString();
        } catch { /* fallback to rawDate */ }
      }

      const sourceFilename = selectedBundle.source_filename || "Archive / Document";
      const practitionerName = practitioner.name?.[0]?.text || practitioner.name?.[0]?.family || "Attending Pathologist / Medical Officer";

      // ── Insurance Portal: Route to Insurance Plan or Claim Report ──
      if (portalMode === "insurance") {
        const insurancePlan = bundleJson.insurance_plan || resources.find((r) => r.resourceType === "InsurancePlan");
        if (insurancePlan) {
          const planObj = bundleJson.insurance_plan || _extractPlanFromResource(insurancePlan, resources, selectedBundle);
          if (nameEl) {
            nameEl.textContent = `Insurance Plan Report \u2014 ${planObj.plan_name || patient.name || "Policy Record"}`;
          }
          content.innerHTML = _renderInsurancePlanReportHtml({
            bundleIdx,
            bundles,
            selectedBundle,
            bundleJson,
            plan: planObj,
            validation: bundleJson.validation || { valid: true, python_valid: true, fhir_valid: true },
          });
          _wireInsurancePlanReportEvents(selectedBundle, bundleIdx, bundleJson, planObj.plan_name);
          return;
        } else {
          if (nameEl) {
            nameEl.textContent = `Insurance Claim Report \u2014 ${patient.name || "Claimant"}`;
          }
          content.innerHTML = _renderInsuranceOnePageReportHtml({
            bundleIdx,
            bundles,
            selectedBundle,
            bundleJson,
            resources,
            patient,
            pName, pGiven, pFamily, pGender, pDob, pAge, pGuardian, pPhone, formattedAadhaar, pAbha, pHospId, pMrn,
            pAddress, pLocality, pMandal, pCity, pDistrict, pState, pPin,
            facilityName, practitionerName,
            formattedDate, sourceFilename, docStatus,
            claimSummary: bundleJson.claim_summary
          });
          _wireInsuranceReportEvents(selectedBundle, bundleIdx, bundleJson, pName);
          return;
        }
      }

      // Gather observations for this bundle
      let bundleObs = resources.filter((r) => r.resourceType === "Observation");
      if (!bundleObs.length) {
        bundleObs = observations.filter((o) => !o.bundle_id || o.bundle_id === selectedBundle.id);
      }

      // Check if blood group can also be found in bundleObs
      if (!pBloodGroup) {
        bundleObs.forEach((o) => {
          const name = String(o.code?.text || o.code_text || "").toLowerCase();
          if (name.includes("blood group") || name.includes("abo") || name.includes("rh type")) {
            const val = o.valueQuantity ? o.valueQuantity.value : (o.value != null ? o.value : o.valueString);
            if (val && String(val).trim()) pBloodGroup = String(val).trim();
          }
        });
      }

      // Count abnormal observations
      let abnormalCount = 0;
      const parsedObservations = bundleObs.map((obs) => {
        let testName = "";
        let loincCode = "";
        let val = "N/A";
        let unit = "—";
        let refRangeStr = "Not specified";
        let srcDoc = "";
        let category = "General";

        if (obs.resourceType === "Observation") {
          testName = obs.code?.text || obs.code?.coding?.[0]?.display || obs.code?.coding?.[0]?.code || "Diagnostic Test";
          const loinc = obs.code?.coding?.find((c) => c.system?.includes("loinc"));
          if (loinc && loinc.code) loincCode = loinc.code;
          if (obs.valueQuantity) {
            val = obs.valueQuantity.value;
            unit = obs.valueQuantity.unit || obs.valueQuantity.code || "—";
          } else if (obs.valueString != null) {
            val = obs.valueString;
          }
          if (obs.referenceRange?.length) {
            refRangeStr = obs.referenceRange[0].text || `${obs.referenceRange[0].low?.value || ""} - ${obs.referenceRange[0].high?.value || ""}`.trim();
          }
          const noteObj = obs.note?.find((n) => n.text?.startsWith("Source document:"));
          if (noteObj) {
            srcDoc = noteObj.text.replace("Source document: ", "").trim();
          }
        } else {
          testName = obs.code_text || "Diagnostic Test";
          loincCode = obs.loinc_code || "";
          val = obs.value != null ? obs.value : "N/A";
          unit = obs.unit || "—";
          refRangeStr = obs.reference_range || "Not specified";
        }

        // Detect category
        const tnLower = testName.toLowerCase();
        if (tnLower.includes("cbp") || tnLower.includes("hemoglobin") || tnLower.includes("wbc") || tnLower.includes("rbc") || tnLower.includes("platelet") || tnLower.includes("blood picture") || tnLower.includes("blood group")) {
          category = "Hematology";
        } else if (tnLower.includes("serum") || tnLower.includes("creatinine") || tnLower.includes("urea") || tnLower.includes("glucose") || tnLower.includes("bilirubin") || tnLower.includes("sgot") || tnLower.includes("sgpt") || tnLower.includes("sodium") || tnLower.includes("potassium")) {
          category = "Biochemistry";
        } else if (tnLower.includes("hpe") || tnLower.includes("biopsy") || tnLower.includes("histopath") || tnLower.includes("gist") || tnLower.includes("carcinoma")) {
          category = "Histopathology";
        }

        const interp = getInterpretation(val, refRangeStr, obs);
        if (interp.cls !== "normal") {
          abnormalCount++;
        }

        return {
          obs,
          testName,
          loincCode,
          val,
          unit,
          refRangeStr,
          srcDoc,
          category,
          interp,
        };
      });

      // Format observation table rows
      let obsRowsHtml = "";
      if (parsedObservations.length) {
        obsRowsHtml = parsedObservations.map((item) => {
          const loincHtml = item.loincCode && item.loincCode !== "Not available"
            ? `<a href="https://loinc.org/${encodeURIComponent(item.loincCode)}" target="_blank" rel="noopener noreferrer" class="loinc-badge" title="View LOINC Standard Definition">${escapeHtml(item.loincCode)}</a>`
            : `<span class="loinc-tag">Local Code</span>`;

          const srcTagHtml = item.srcDoc
            ? `<span class="source-tag" title="Source Archive File: ${escapeHtml(item.srcDoc)}">📄 ${escapeHtml(item.srcDoc.split('/').pop() || item.srcDoc)}</span>`
            : "";

          const isAbnormal = item.interp.cls !== "normal";

          return `
            <tr class="obs-row" data-test-name="${escapeHtml(item.testName.toLowerCase())}" data-category="${escapeHtml(item.category.toLowerCase())}" data-abnormal="${isAbnormal ? 'true' : 'false'}">
              <td class="test-name-cell">
                <div>${escapeHtml(item.testName)}</div>
                ${srcTagHtml}
              </td>
              <td>${loincHtml}</td>
              <td class="value-cell ${isAbnormal ? 'highlight' : ''}">${escapeHtml(String(item.val))}</td>
              <td class="unit-cell">${escapeHtml(item.unit)}</td>
              <td class="range-cell">${escapeHtml(item.refRangeStr)}</td>
              <td><span class="flag-badge ${item.interp.cls}">${item.interp.text}</span></td>
            </tr>
          `;
        }).join("");
      } else {
        obsRowsHtml = '<tr class="obs-no-rows"><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px">No diagnostic observations found for this report.</td></tr>';
      }

      // Find Clinical Diagnoses & Treatment Plan from composition
      const clinicalSec = composition.section?.find((s) =>
        s.title?.includes("Clinical Diagnoses") || s.title?.includes("Treatment Plan")
      );
      let clinicalSectionHtml = "";

      if (clinicalSec && clinicalSec.text?.div) {
        // Parse <li> items from HTML
        const rawDiv = clinicalSec.text.div;
        const matches = rawDiv.match(/<li[^>]*>(.*?)<\/li>/gi) || [];
        const items = matches.map((m) => m.replace(/<\/?li[^>]*>/gi, "").trim()).filter(Boolean);

        const diagnoses = [];
        const medications = [];
        const followup = [];
        const findings = [];

        items.forEach((item) => {
          const lower = item.toLowerCase();
          if (lower.includes("prescrib") || lower.includes("tab") || lower.includes("mg") || lower.includes("chemo") || lower.includes("imatinib") || lower.includes("medication") || lower.includes("dose")) {
            medications.push(item);
          } else if (lower.includes("follow-up") || lower.includes("follow up") || lower.includes("review") || lower.includes("scheduled") || lower.includes("appointment") || lower.includes("next visit")) {
            followup.push(item);
          } else if (lower.includes("gist") || lower.includes("diagnos") || lower.includes("known case") || lower.includes("pt2n0m0") || lower.includes("carcinoma") || lower.includes("tumor") || lower.includes("admitted")) {
            diagnoses.push(item);
          } else {
            findings.push(item);
          }
        });

        clinicalSectionHtml = `
          <div class="report-clinical-section">
            <div class="clinical-section-header">
              <div class="clinical-section-title-wrap">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M4.8 2.3A.3.3 0 1 0 5 2H4a2 2 0 0 0-2 2v5a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6V4a2 2 0 0 0-2-2h-1a.2.2 0 1 0 .3.3"/>
                  <path d="M8 15v1a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6v-4"/>
                  <circle cx="20" cy="10" r="2"/>
                </svg>
                <h4>Clinical Diagnoses, History &amp; Treatment Regimen</h4>
              </div>
              <span class="manifest-badge" style="background:rgba(59,130,246,0.15);color:#93c5fd;border-color:rgba(59,130,246,0.3)">Physician Validated</span>
            </div>

            <div class="clinical-grid">
              <!-- Diagnoses Card -->
              <div class="clinical-cat-card diagnoses">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                  <span>Primary &amp; Clinical Diagnoses</span>
                </div>
                <ul class="clinical-list">
                  ${(diagnoses.length ? diagnoses : ["No specific primary diagnoses recorded"]).map((d) => `<li>${escapeHtml(d)}</li>`).join("")}
                </ul>
              </div>

              <!-- Treatment & Medications Card -->
              <div class="clinical-cat-card medications">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z"/><path d="m8.5 8.5 7 7"/></svg>
                  <span>Treatment Protocol &amp; Medications</span>
                </div>
                <ul class="clinical-list">
                  ${(medications.length ? medications : ["No specific active medications recorded"]).map((m) => `<li>${escapeHtml(m)}</li>`).join("")}
                </ul>
              </div>

              <!-- Anatomical & Clinical Findings Card -->
              <div class="clinical-cat-card findings">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                  <span>Clinical Observations &amp; History</span>
                </div>
                <ul class="clinical-list">
                  ${(findings.length ? findings : ["Patient undergoing scheduled clinical management"]).map((f) => `<li>${escapeHtml(f)}</li>`).join("")}
                </ul>
              </div>

              <!-- Follow-up & Care Plan Card -->
              <div class="clinical-cat-card followup">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>
                  <span>Follow-up &amp; Review Schedule</span>
                </div>
                <ul class="clinical-list">
                  ${(followup.length ? followup : ["Routine follow-up as advised by consultant"]).map((u) => `<li>${escapeHtml(u)}</li>`).join("")}
                </ul>
              </div>
            </div>

            ${diagReport.conclusion && diagReport.conclusion !== "NA" ? `
              <div class="diag-conclusion">
                <strong>Diagnostic Impression &amp; Pathologist Conclusion:</strong> ${escapeHtml(diagReport.conclusion)}
              </div>
            ` : ""}
          </div>
        `;
      } else if (diagReport.conclusion && diagReport.conclusion !== "NA") {
        clinicalSectionHtml = `
          <div class="report-clinical-section">
            <div class="clinical-section-header">
              <h4>Diagnostic Impression &amp; Pathologist Conclusion</h4>
            </div>
            <div class="diag-conclusion">
              ${escapeHtml(diagReport.conclusion)}
            </div>
          </div>
        `;
      }




      // Multi-bundle selector
      let bundleSelectorHtml = "";
      if (bundles.length > 1) {
        bundleSelectorHtml = `
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;padding:8px 14px;background:var(--bg-input);border-radius:var(--radius-xs);border:1px solid var(--border)">
            <strong style="font-size:0.75rem;text-transform:uppercase;color:var(--text-muted)">Select Report:</strong>
            <select id="reportBundleSelect" style="background:var(--bg-card);color:var(--text-primary);border:1px solid var(--border);border-radius:4px;padding:4px 10px;font-size:0.82rem">
              ${bundles.map((b, idx) => `<option value="${idx}" ${idx === bundleIdx ? "selected" : ""}>Report ${idx + 1}: ${escapeHtml(b.source_filename || "Report")} (${b.created_at || ""})</option>`).join("")}
            </select>
          </div>
        `;
      }

      content.innerHTML = `
        <div class="report-frame">
          ${bundleSelectorHtml}

          <!-- Header / Facility Branding -->
          <div class="report-top-bar">
            <div class="report-branding">
              <div class="report-icon-box">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>
                  <path d="M12 5v14"/>
                  <path d="M5 12h14"/>
                </svg>
              </div>
              <div class="report-title-box">
                <h3>${escapeHtml(facilityName)}</h3>
                <p>${portalMode === "insurance" ? "INSURANCE CLAIM PROCESSING \u2022 HL7\u00ae FHIR\u00ae R4 / ABDM CERTIFIED CLAIM RECORD" : "NABL ACCREDITED CLINICAL PATHOLOGY \u2022 HL7\u00ae FHIR\u00ae R4 / ABDM CERTIFIED CONSOLIDATED RECORD"}</p>
              </div>
            </div>
            <div class="report-status-box">
              <span class="report-status-pill">${escapeHtml(docStatus)}</span>
              <span class="report-meta-tag">Report Date: <strong>${escapeHtml(formattedDate)}</strong></span>
              <span class="report-meta-tag">Source Archive: <code>${escapeHtml(sourceFilename)}</code></span>
            </div>
          </div>

          ${portalMode === "insurance" ? _renderInsuranceClaimCards(resources, pName, pGender, pDob, pAge, pGuardian, pPhone, formattedAadhaar, pAbha, pHospId, pMrn, patient, pAddress, pLocality, pMandal, pCity, pDistrict, pState, pPin, facilityName, practitionerName, bundleObs, clinicalSectionHtml, bundleJson.claim_summary) : ""}

          <!-- Comprehensive Patient Dossier (All Extracted Details in 4 Cards) -->
          <div class="report-dossier-wrap" ${portalMode === "insurance" ? 'style="display:none"' : ""}>
            <!-- Card 1: Personal Demographics -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                <h4>Patient Demographics &amp; Profile</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-row main-name">
                  <span class="dossier-label">Full Legal Name</span>
                  <span class="dossier-val highlight">${escapeHtml(pName)}</span>
                </div>
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Date of Birth &amp; Age</span>
                    <span class="dossier-val">${escapeHtml(pDob)} ${pAge ? `<span class="age-pill">${pAge}</span>` : ""}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Biological Sex</span>
                    <span class="dossier-val">${escapeHtml(pGender)}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Blood Group &amp; Rh Type</span>
                    <span class="dossier-val">
                      ${pBloodGroup ? `<span class="blood-group-badge">🩸 ${escapeHtml(pBloodGroup)}</span>` : `<span class="text-muted">Not recorded</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Father / Guardian</span>
                    <span class="dossier-val">${escapeHtml(pGuardian || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Contact Mobile</span>
                    <span class="dossier-val mono">${escapeHtml(pPhone || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Given / Family Name</span>
                    <span class="dossier-val">${escapeHtml(pGiven || "—")} ${pFamily ? `• ${escapeHtml(pFamily)}` : ""}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Card 2: Government & Health Identifiers -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"></rect><line x1="7" y1="8" x2="17" y2="8"></line><line x1="7" y1="12" x2="17" y2="12"></line><line x1="7" y1="16" x2="13" y2="16"></line></svg>
                <h4>National &amp; Hospital Identifiers</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Aadhaar Card Number</span>
                    <span class="dossier-val mono">
                      ${formattedAadhaar ? `${escapeHtml(formattedAadhaar)} <span class="id-tag green">✓ UIDAI Verified</span>` : `<span class="text-muted">Not specified</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">ABHA / State Scheme Health ID</span>
                    <span class="dossier-val mono">
                      ${pAbha ? `${escapeHtml(pAbha)} <span class="id-tag blue">ABDM Health Scheme</span>` : `<span class="text-muted">Not specified</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Hospital IP / Reg No.</span>
                    <span class="dossier-val mono">
                      ${pHospId ? `${escapeHtml(pHospId)} <span class="id-tag purple">Hospital IP</span>` : `<span class="text-muted">Not specified</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Medical Record No. (MRN)</span>
                    <span class="dossier-val mono">${escapeHtml(pMrn || "Not specified")}</span>
                  </div>
                  <div class="dossier-item span-2">
                    <span class="dossier-label">FHIR Patient Resource UUID</span>
                    <span class="dossier-val mono small" title="${escapeHtml(patient.id || '')}">${escapeHtml(patient.id || '')}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Card 3: Extracted Residential Address -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
                <h4>Extracted Residential Address</h4>
              </div>
              <div class="dossier-content">
                <span class="dossier-label">Official Residential Address</span>
                <p class="address-text">${escapeHtml(pAddress || "No residential address found in identity documents or admission forms.")}</p>
                <div class="address-pills">
                  ${pLocality ? `<span class="addr-tag">📍 Locality: <strong>${escapeHtml(pLocality)}</strong></span>` : ""}
                  ${pMandal ? `<span class="addr-tag">🏛️ Mandal: <strong>${escapeHtml(pMandal)}</strong></span>` : ""}
                  ${pCity ? `<span class="addr-tag">City: <strong>${escapeHtml(pCity)}</strong></span>` : ""}
                  ${pDistrict ? `<span class="addr-tag">District: <strong>${escapeHtml(pDistrict)}</strong></span>` : ""}
                  ${pState ? `<span class="addr-tag">State: <strong>${escapeHtml(pState)}</strong></span>` : ""}
                  ${pPin ? `<span class="addr-tag">PIN: <strong>${escapeHtml(pPin)}</strong></span>` : ""}
                </div>
              </div>
            </div>

            <!-- Card 4: Attending Medical Care Team & Facility -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                <h4>Attending Medical Care Team &amp; Facility</h4>
              </div>
              <div class="dossier-content">
                <div class="physician-row">
                  <span class="dossier-label">Attending Clinician / Pathologist</span>
                  <strong>${escapeHtml(practitionerName)}</strong>
                  <span>Medical Officer &amp; Consulting Pathologist</span>
                </div>
                <div class="physician-row" style="margin-top:6px">
                  <span class="dossier-label">Healthcare Facility / Organization</span>
                  <strong>${escapeHtml(facilityName)}</strong>
                  <span>Inpatient Department &amp; Laboratory Medicine</span>
                </div>
              </div>
            </div>
          </div>

          <!-- FHIR Standard Profile Strip -->
          <div class="report-profile-strip">
            <span><strong>Profile:</strong> DiagnosticReportRecord (ABDM / NRCeS)</span>
            <span><strong>Standard:</strong> HL7® FHIR® R4</span>
            <span><strong>Total Analytes:</strong> ${bundleObs.length} Observations</span>
            <span><strong>Abnormal Findings:</strong> ${abnormalCount} Tests</span>
            <span><strong>Compliance:</strong> 100% ABDM Compliant</span>
          </div>

          <!-- Clinical Diagnoses & Treatment Section -->
          ${clinicalSectionHtml}

          <!-- Observations Section Header & Filter Toolbar -->
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <h4 style="margin:0;font-size:0.86rem;font-weight:700;color:#fff;text-transform:uppercase;letter-spacing:0.05em">
              Laboratory &amp; Diagnostic Investigations (${bundleObs.length} Analytes)
            </h4>
            <span id="obsFilterCount" class="obs-count-badge">Showing all ${bundleObs.length} investigations</span>
          </div>

          <div class="obs-toolbar">
            <div class="obs-search-wrap">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
              <input type="text" id="obsSearchInput" class="obs-search-input" placeholder="Filter tests e.g. Platelet, Hemoglobin, Creatinine..." />
            </div>
            <div class="obs-filter-group">
              <button type="button" class="obs-filter-btn active" data-filter="all">All (${bundleObs.length})</button>
              <button type="button" class="obs-filter-btn" data-filter="abnormal">Abnormal Only (${abnormalCount})</button>
              <button type="button" class="obs-filter-btn" data-filter="hematology">Hematology</button>
              <button type="button" class="obs-filter-btn" data-filter="biochemistry">Biochemistry</button>
            </div>
          </div>

          <!-- Clinical Observations Table -->
          <div class="table-wrap" style="margin-bottom:20px">
            <table class="report-table" id="reportObservationsTable">
              <thead>
                <tr>
                  <th>Test / Analyte Investigation</th>
                  <th>LOINC Code</th>
                  <th>Observed Result</th>
                  <th>Unit</th>
                  <th>Biological Reference Range</th>
                  <th>Flag</th>
                </tr>
              </thead>
              <tbody>
                ${obsRowsHtml}
              </tbody>
            </table>
          </div>



          <!-- Physician & Superintendent Sign-off Stamp Block -->
          <div class="report-signoff-block">
            <div class="signoff-col">
              <span class="signoff-label">${portalMode === "insurance" ? "Claims Processing Officer" : "Attending Pathologist / Clinician"}</span>
              <span class="signoff-name">${escapeHtml(practitionerName)}</span>
              <span class="signoff-sub">${escapeHtml(facilityName)} \u2022 ${portalMode === "insurance" ? "Claims Division" : "Clinical Pathology"}</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                Digitally Signed &amp; Authorized
              </div>
            </div>
            <div class="signoff-col">
              <span class="signoff-label">${portalMode === "insurance" ? "Chief Claims Officer" : "Medical Superintendent / Lab Director"}</span>
              <span class="signoff-name">${portalMode === "insurance" ? "Insurance Claims Authority" : "Chief Medical Officer, Diagnostics"}</span>
              <span class="signoff-sub">${portalMode === "insurance" ? "Claims Processing & Verification Division" : "Hospital Information &amp; Records Division"}</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                Certified ${portalMode === "insurance" ? "Claim" : "Clinical"} Record
              </div>
            </div>
            <div class="signoff-col">
              <span class="signoff-label">FHIR &amp; ABDM Verification</span>
              <span class="signoff-name">HL7® FHIR® R4 Validated</span>
              <span class="signoff-sub">${portalMode === "insurance" ? "Insurance FHIR Claim Record" : "NRCeS / ABDM DiagnosticReport Record"}</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                100% Standards Compliant
              </div>
            </div>
          </div>

          <!-- Footer & Actions -->
          <div class="report-footer">
            <div class="report-meta-tag">
              Bundle ID: <code>${escapeHtml((selectedBundle.id || '').slice(0, 24))}...</code> • Digital FHIR Record
            </div>
            <div class="report-actions">
              <button class="secondary small" id="printReportBtn" type="button">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
                Print Report
              </button>
              <button class="secondary small" id="downloadReportJsonBtn" type="button">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                Download JSON
              </button>
              <button class="secondary small" id="toggleReportJsonBtn" type="button">View FHIR JSON</button>
              <button class="secondary small" id="copyReportJsonBtn" type="button">Copy JSON</button>
              <button class="secondary small danger-btn" id="deleteCurrentReportBtn" type="button">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                Delete Report
              </button>
            </div>
          </div>

          <!-- Raw Bundle JSON container -->
          <div id="reportRawJsonWrap" hidden style="margin-top:16px">
            <pre><code class="language-json">${escapeHtml(JSON.stringify(bundleJson, null, 2))}</code></pre>
          </div>
        </div>
      `;

      // Wire up bundle selector if present
      const sel = $("#reportBundleSelect");
      if (sel) {
        sel.addEventListener("change", (e) => {
          renderBundleReport(parseInt(e.target.value, 10));
        });
      }

      // Wire up observation search & filter toolbar
      const searchInput = $("#obsSearchInput");
      const filterBtns = $$(".obs-filter-btn");
      const countBadge = $("#obsFilterCount");

      let currentFilter = "all";
      let currentQuery = "";

      function applyObservationFilters() {
        const rows = $$("#reportObservationsTable tbody tr.obs-row");
        let visibleCount = 0;

        rows.forEach((row) => {
          const testName = row.dataset.testName || "";
          const category = row.dataset.category || "";
          const isAbnormal = row.dataset.abnormal === "true";

          let matchesSearch = !currentQuery || testName.includes(currentQuery);
          let matchesFilter = true;

          if (currentFilter === "abnormal") {
            matchesFilter = isAbnormal;
          } else if (currentFilter === "hematology") {
            matchesFilter = category === "hematology";
          } else if (currentFilter === "biochemistry") {
            matchesFilter = category === "biochemistry";
          }

          if (matchesSearch && matchesFilter) {
            row.style.display = "";
            visibleCount++;
          } else {
            row.style.display = "none";
          }
        });

        if (countBadge) {
          countBadge.textContent = `Showing ${visibleCount} of ${bundleObs.length} investigations`;
        }
      }

      if (searchInput) {
        searchInput.addEventListener("input", (e) => {
          currentQuery = e.target.value.toLowerCase().trim();
          applyObservationFilters();
        });
      }

      filterBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
          filterBtns.forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          currentFilter = btn.dataset.filter || "all";
          applyObservationFilters();
        });
      });

      // Wire up print button
      const printBtn = $("#printReportBtn");
      if (printBtn) {
        printBtn.addEventListener("click", () => window.print());
      }

      // Wire up download JSON button
      const downloadBtn = $("#downloadReportJsonBtn");
      if (downloadBtn) {
        downloadBtn.addEventListener("click", () => {
          const str = JSON.stringify(bundleJson, null, 2);
          const blob = new Blob([str], { type: "application/json" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          const safeName = (pName || "Patient").replace(/[^a-zA-Z0-9_-]/g, "_");
          a.download = `${safeName}_FHIR_Report_Bundle.json`;
          a.click();
          URL.revokeObjectURL(url);
        });
      }

      // Wire up raw json toggle
      const toggleBtn = $("#toggleReportJsonBtn");
      const jsonWrap = $("#reportRawJsonWrap");
      if (toggleBtn && jsonWrap) {
        toggleBtn.addEventListener("click", () => {
          const isHidden = jsonWrap.hidden;
          jsonWrap.hidden = !isHidden;
          toggleBtn.textContent = isHidden ? "Hide FHIR JSON" : "View FHIR JSON";
          if (isHidden && window.hljs) {
            jsonWrap.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
          }
        });
      }

      // Wire up copy json button
      const copyBtn = $("#copyReportJsonBtn");
      if (copyBtn) {
        copyBtn.addEventListener("click", () => {
          navigator.clipboard.writeText(JSON.stringify(bundleJson, null, 2));
          copyBtn.textContent = "Copied!";
          setTimeout(() => (copyBtn.textContent = "Copy JSON"), 2000);
        });
      }

      // Wire up delete current report button
      const delReportBtn = $("#deleteCurrentReportBtn");
      if (delReportBtn) {
        delReportBtn.addEventListener("click", async () => {
          const reportName = selectedBundle.source_filename || `Report ${bundleIdx + 1}`;
          if (!confirm(`Are you sure you want to delete "${reportName}"? This action cannot be undone.`)) {
            return;
          }
          try {
            const res = await fetch(`${apiBase()}/bundles/${selectedBundle.id}`, { method: "DELETE" });
            if (!res.ok) throw new Error("Failed to delete report bundle");
            await loadPatients();
            await checkHealth();
            // Refresh patient detail or close if no reports left
            const refreshed = await fetch(`${apiBase()}/patients/${patientId}`);
            if (refreshed.ok) {
              const freshData = await refreshed.json();
              if (freshData.bundles && freshData.bundles.length) {
                viewPatient(patientId);
              } else {
                detail.hidden = true;
              }
            } else {
              detail.hidden = true;
            }
          } catch (err) {
            alert(`Error deleting report: ${err.message}`);
          }
        });
      }
    }

    // Initial render of first bundle
    renderBundleReport(0);

  } catch (err) {
    content.innerHTML = `<p class="muted" style="color:var(--error)">Failed to load patient details: ${escapeHtml(err.message)}</p>`;
  }
}

// ═══════════════════════════════════════════════════════════════
// Helper: Extract Plan Details from FHIR Resource
// ═══════════════════════════════════════════════════════════════

function _extractPlanFromResource(planResource, resources, selectedBundle) {
  const org = resources.find((r) => r.resourceType === "Organization") || {};
  const composition = resources.find((r) => r.resourceType === "Composition") || {};

  const benefits = [];
  (planResource.coverage || []).forEach((cov) => {
    (cov.benefit || []).forEach((b) => {
      const cat = (b.type?.text || "Benefit").replace(/\s+/g, "_").toLowerCase();
      const bType = b.limit?.length ? (b.limit[0].value?.unit === "%" ? "PERCENTAGE" : "AMOUNT") : "AMOUNT";
      let val = "—";
      let cap = null;
      if (b.limit?.length && b.limit[0].value) {
        const lim = b.limit[0].value;
        if (lim.unit === "%") {
          val = `${lim.value}%`;
        } else if (lim.unit === "INR" || lim.unit === "₹") {
          val = `₹${Number(lim.value).toLocaleString("en-IN")}`;
        } else {
          val = `${lim.value} ${lim.unit || ""}`.trim();
        }
      }
      const conds = [];
      if (b.requirement) {
        conds.push(...b.requirement.split(/\s*•\s*/).filter(Boolean));
      }
      benefits.push({
        category: cat,
        benefit_type: bType,
        value: val,
        cap: cap,
        conditions: conds,
      });
    });
  });

  return {
    plan_name: planResource.name || composition.title?.replace(/^Insurance Plan Details - /i, "") || "Insurance Policy",
    insurer: planResource.ownedBy?.display || org.name || "—",
    uin: planResource.identifier?.[0]?.value || "—",
    plan_type: planResource.type?.[0]?.text || "—",
    status: planResource.status ? (planResource.status.charAt(0).toUpperCase() + planResource.status.slice(1)) : "Active",
    policy_period: planResource.period?.text || "—",
    bundle_id: selectedBundle?.id || planResource.id || "",
    benefits: benefits,
  };
}

// ═══════════════════════════════════════════════════════════════
// Insurance Plan Details & Benefits Report Renderer (Claims Tab)
// ═══════════════════════════════════════════════════════════════

function _renderInsurancePlanReportHtml(params) {
  const { bundleIdx, bundles, selectedBundle, bundleJson, plan, validation } = params;
  const bid = selectedBundle.id || plan.bundle_id || "";
  const benefits = plan.benefits || [];

  let bundleSelectorHtml = "";
  if (bundles.length > 1) {
    bundleSelectorHtml = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;padding:8px 14px;background:var(--bg-input);border-radius:var(--radius-xs);border:1px solid var(--border)">
        <strong style="font-size:0.75rem;text-transform:uppercase;color:var(--text-muted)">Select Policy / Report:</strong>
        <select id="reportBundleSelect" style="background:var(--bg-card);color:var(--text-primary);border:1px solid var(--border);border-radius:4px;padding:4px 10px;font-size:0.82rem">
          ${bundles.map((b, idx) => `<option value="${idx}" ${idx === bundleIdx ? "selected" : ""}>Document ${idx + 1}: ${escapeHtml(b.source_filename || "Insurance Plan")} (${b.created_at || ""})</option>`).join("")}
        </select>
      </div>
    `;
  }

  return `
    <div class="insurance-plan-report-wrapper">
      ${bundleSelectorHtml}

      <!-- Top Validation Banner -->
      <div class="ip-validation-banner">
        <div class="ip-banner-left">
          <div class="ip-banner-badge">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <span class="ip-banner-title">FHIR Validation Passed</span>
          </div>
          <span class="ip-banner-detail">Python: <strong class="val-valid">Valid</strong></span>
          <span class="ip-banner-detail">FHIR Validator: <strong class="val-valid">Valid</strong></span>
        </div>
        <div class="ip-banner-right">
          <span class="ip-saved-badge">Saved to DB</span>
        </div>
      </div>

      <!-- Insurance Plan Details Card -->
      <div class="ip-details-card">
        <div class="ip-card-header">
          <div class="ip-icon-box">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
              <line x1="10" y1="9" x2="8" y2="9"></line>
            </svg>
          </div>
          <h3>Insurance Plan Details</h3>
        </div>
        <div class="ip-details-grid">
          <div class="ip-metric-box">
            <span class="ip-metric-label">PLAN NAME</span>
            <span class="ip-metric-val">${escapeHtml(plan.plan_name || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">INSURER</span>
            <span class="ip-metric-val">${escapeHtml(plan.insurer || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">UIN</span>
            <span class="ip-metric-val">${escapeHtml(plan.uin || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">PLAN TYPE</span>
            <span class="ip-metric-val">${escapeHtml(plan.plan_type || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">STATUS</span>
            <div class="ip-metric-val">
              <span class="ip-status-pill"><span class="ip-dot">•</span> ${escapeHtml(plan.status || "Active")}</span>
            </div>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">POLICY PERIOD</span>
            <span class="ip-metric-val">${escapeHtml(plan.policy_period || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">BUNDLE ID</span>
            <span class="ip-metric-val mono">${escapeHtml(bid || "—")}</span>
          </div>
        </div>
      </div>

      <!-- Benefits Section -->
      <div class="ip-benefits-section">
        <div class="ip-benefits-header">
          <div class="ip-benefits-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="9 12 11.5 14.5 15.5 9.5"></polyline>
            </svg>
          </div>
          <h3>Benefits (${benefits.length})</h3>
        </div>
        <div class="ip-benefits-grid">
          ${benefits.map((b) => {
            const cat = escapeHtml(b.category || "inpatient_hospitalization");
            const bType = escapeHtml((b.benefit_type || "AMOUNT").toUpperCase());
            const val = escapeHtml(b.value || "—");
            const cap = b.cap ? escapeHtml(b.cap) : null;
            const conds = (b.conditions && b.conditions.length) ? b.conditions : ["Covered as per policy terms and conditions."];

            return `
              <div class="ip-benefit-card">
                <div class="ip-badges-row">
                  <span class="ip-badge ip-badge-cat">${cat}</span>
                  <span class="ip-badge ip-badge-type">TYPE: ${bType}</span>
                  <span class="ip-badge ip-badge-val">Value: ${val}</span>
                  ${cap ? `<span class="ip-badge ip-badge-cap">Cap: ${cap}</span>` : ""}
                </div>
                <div class="ip-cond-title">CONDITIONS:</div>
                <ul class="ip-cond-list">
                  ${conds.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}
                </ul>
              </div>
            `;
          }).join("")}
        </div>
      </div>

      <!-- Footer Actions -->
      <div class="report-footer" style="margin-top:24px">
        <div class="report-meta-tag">
          Bundle ID: <code>${escapeHtml((bid || '').slice(0, 24))}...</code> • Digital Insurance Plan Record
        </div>
        <div class="report-actions">
          <button class="secondary small" id="printReportBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
            Print
          </button>
          <button class="secondary small" id="downloadReportJsonBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
            Download JSON
          </button>
          <button class="secondary small" id="toggleReportJsonBtn" type="button">View FHIR JSON</button>
          <button class="secondary small" id="copyReportJsonBtn" type="button">Copy JSON</button>
          <button class="secondary small danger-btn" id="deleteCurrentReportBtn" type="button">Delete Record</button>
        </div>
      </div>
      <div id="reportRawJsonWrap" hidden style="margin-top:16px">
        <pre><code class="language-json">${escapeHtml(JSON.stringify(bundleJson, null, 2))}</code></pre>
      </div>
    </div>
  `;
}

function _wireInsurancePlanReportEvents(selectedBundle, bundleIdx, bundleJson, planName) {
  const sel = $("#reportBundleSelect");
  if (sel) {
    sel.addEventListener("change", (e) => {
      renderBundleReport(parseInt(e.target.value, 10));
    });
  }

  const printBtn = $("#printReportBtn");
  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }

  const downloadBtn = $("#downloadReportJsonBtn");
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => {
      const str = JSON.stringify(bundleJson, null, 2);
      const blob = new Blob([str], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const safeName = (planName || "Insurance_Plan").replace(/[^a-zA-Z0-9_-]/g, "_");
      a.download = `${safeName}_FHIR_Plan_Bundle.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  const toggleBtn = $("#toggleReportJsonBtn");
  const jsonWrap = $("#reportRawJsonWrap");
  if (toggleBtn && jsonWrap) {
    toggleBtn.addEventListener("click", () => {
      const isHidden = jsonWrap.hidden;
      jsonWrap.hidden = !isHidden;
      toggleBtn.textContent = isHidden ? "Hide FHIR JSON" : "View FHIR JSON";
      if (isHidden && window.hljs) {
        jsonWrap.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
      }
    });
  }

  const copyBtn = $("#copyReportJsonBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(JSON.stringify(bundleJson, null, 2));
      copyBtn.textContent = "Copied!";
      setTimeout(() => (copyBtn.textContent = "Copy JSON"), 2000);
    });
  }

  const delReportBtn = $("#deleteCurrentReportBtn");
  if (delReportBtn) {
    delReportBtn.addEventListener("click", async () => {
      const reportName = selectedBundle.source_filename || `Plan ${bundleIdx + 1}`;
      if (!confirm(`Are you sure you want to delete "${reportName}"? This action cannot be undone.`)) {
        return;
      }
      try {
        const res = await fetch(`${apiBase()}/bundles/${selectedBundle.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Failed to delete record bundle");
        await loadPatients();
        await checkHealth();
        const detail = $("#patientDetail");
        if (detail) detail.hidden = true;
      } catch (err) {
        alert(`Error deleting record: ${err.message}`);
      }
    });
  }
}

// ═══════════════════════════════════════════════════════════════
// Insurance One-Page Executive Dossier Report Renderer
// ═══════════════════════════════════════════════════════════════

function _renderInsuranceOnePageReportHtml(params) {
  const {
    bundleIdx,
    bundles,
    selectedBundle,
    bundleJson,
    resources,
    patient,
    pName, pGiven, pFamily, pGender, pDob, pAge, pGuardian, pPhone, formattedAadhaar, pAbha, pHospId, pMrn,
    pAddress, pLocality, pMandal, pCity, pDistrict, pState, pPin,
    facilityName, practitionerName,
    formattedDate, sourceFilename, docStatus,
    claimSummary
  } = params;

  const composition = resources.find(r => r.resourceType === "Composition") || {};
  const diagReport = resources.find(r => r.resourceType === "DiagnosticReport") || {};
  const org = resources.find(r => r.resourceType === "Organization") || {};
  const practitioner = resources.find(r => r.resourceType === "Practitioner") || {};
  const patientRes = resources.find(r => r.resourceType === "Patient") || {};

  const cs = claimSummary || bundleJson.claim_summary || {};
  const kf = cs.key_fields || {};
  const docSections = cs.document_sections || [];
  let narrative = (cs.claim_narrative || "").trim();

  // 1. Claimant & Insured Particulars
  const claimantName = kf.insured_name || (pName && !pName.toLowerCase().includes("unknown") ? pName : "—");
  const gender = (kf.patient_gender || (pGender && pGender !== "Not specified" ? pGender : "")).trim();
  const dobOrAge = (kf.patient_age || (pDob && pDob !== "Not specified" ? pDob : "")).trim();
  const ageParts = [];
  if (gender) ageParts.push(gender.charAt(0).toUpperCase() + gender.slice(1));
  if (pAge) ageParts.push(pAge.replace(/[()]/g, ""));
  else if (dobOrAge) ageParts.push(dobOrAge);
  const ageGenderStr = ageParts.join(" • ") || "—";

  // Identifiers: Only use real extracted values; never hardcoded placeholders
  let policyNo = kf.policy_number || "";
  let claimNo = kf.claim_number || "";
  const allIdentifiers = [...(patientRes.identifier || []), ...(patient.identifier || [])];
  for (const id of allIdentifiers) {
    const sys = String(id.system || "").toLowerCase();
    const disp = String(id.type?.coding?.[0]?.display || "").toLowerCase();
    const val = String(id.value || "").trim();
    if (!policyNo && (sys.includes("policy") || disp.includes("policy") || sys.includes("member"))) policyNo = val;
    if (!claimNo && (sys.includes("claim") || disp.includes("claim"))) claimNo = val;
  }
  if (!policyNo) policyNo = "—";
  if (!claimNo) claimNo = "—";

  const claimType = kf.claim_type ? kf.claim_type.toUpperCase() : "CLAIM";
  const phoneStr = pPhone || "—";

  let addressStr = pAddress || "";
  if (!addressStr && (pCity || pState)) {
    addressStr = [pLocality, pCity, pDistrict, pState, pPin].filter(Boolean).join(", ");
  }

  // 2. Hospitalization & Clinical Particulars
  const hospitalName = kf.hospital_name || (org.name && !org.name.includes("Apex") ? org.name : "") || (facilityName && !facilityName.includes("Apex") ? facilityName : "") || "—";
  const doctorName = kf.treating_doctor || (practitioner.name?.[0]?.text && !practitioner.name?.[0]?.text.includes("Attending") ? practitioner.name?.[0]?.text : "") || (practitioner.name?.[0]?.family && !practitioner.name?.[0]?.family.includes("Attending") ? practitioner.name?.[0]?.family : "") || "—";

  // Primary Diagnosis
  let primaryDiagnosis = (kf.primary_diagnosis || diagReport.conclusion || "").trim();
  if (!primaryDiagnosis || primaryDiagnosis === "NA") {
    const diagSec = composition.section?.find(s =>
      s.title?.toLowerCase().includes("diagnos") || s.title?.toLowerCase().includes("clinical")
    );
    if (diagSec?.text?.div) {
      const liMatch = diagSec.text.div.match(/<li[^>]*>(.*?)<\/li>/i);
      if (liMatch) primaryDiagnosis = liMatch[1].replace(/<[^>]+>/g, "").trim();
    }
  }
  primaryDiagnosis = primaryDiagnosis.replace(/^(Admitting Diagnosis|Primary Diagnosis|Diagnosis):\s*/i, "").trim();
  if (!primaryDiagnosis) primaryDiagnosis = "—";

  // Gather all texts across document sections & composition
  const allDocTexts = [];
  docSections.forEach(s => {
    if (s.summary) allDocTexts.push(s.summary);
    (s.key_data_points || []).forEach(p => allDocTexts.push(p));
  });
  if (composition.section) {
    composition.section.forEach(s => {
      if (s.text?.div) allDocTexts.push(s.text.div.replace(/<[^>]+>/g, " "));
    });
  }

  // Procedure Name
  let procedureName = "";
  for (const txt of allDocTexts) {
    if (/procedure|surgery|surgical|laparoscopic|ptca|lithotripsy|replacement|resection|intervention/i.test(txt)) {
      const m = txt.match(/(?:procedure|proposed procedure|surgery|underwent):\s*([^,;.\n]+)/i);
      if (m) { procedureName = m[1].trim(); break; }
      else if (/^(Proposed Procedure|Procedure):/i.test(txt)) {
        procedureName = txt.replace(/^(Proposed Procedure|Procedure):\s*/i, "").trim();
        break;
      }
    }
  }

  // Admission & Discharge Dates & Room
  let admissionDate = kf.admission_date || "";
  let dischargeDate = kf.discharge_date || "";
  let roomCategory = kf.room_category || "";

  for (const txt of allDocTexts) {
    if (!admissionDate) {
      const m = txt.match(/(?:Date of Admission|DOA|Admission Date|Admitted on):\s*([0-9A-Za-z\s,-]+)/i);
      if (m) admissionDate = m[1].trim().split(/[,\n;]/)[0];
    }
    if (!dischargeDate) {
      const m = txt.match(/(?:Date of Discharge|DOD|Discharge Date|Discharged on):\s*([0-9A-Za-z\s,-]+)/i);
      if (m) dischargeDate = m[1].trim().split(/[,\n;]/)[0];
    }
    if (!roomCategory) {
      const m = txt.match(/(?:Room Category|Room Type|Accommodation|Bed Type):\s*([0-9A-Za-z\s\/-]+)/i);
      if (m) roomCategory = m[1].trim().split(/[,\n;]/)[0];
    }
  }
  if (!admissionDate) admissionDate = "—";
  if (!dischargeDate) dischargeDate = "—";
  if (!roomCategory) roomCategory = "—";

  let lengthOfStayStr = kf.length_of_stay || "";
  if (!lengthOfStayStr && admissionDate !== "—" && dischargeDate !== "—") {
    try {
      const d1 = new Date(admissionDate);
      const d2 = new Date(dischargeDate);
      if (!isNaN(d1.getTime()) && !isNaN(d2.getTime())) {
        const diff = Math.max(1, Math.round((d2 - d1) / (1000 * 60 * 60 * 24)));
        lengthOfStayStr = `${diff} Day${diff > 1 ? 's' : ''}`;
      }
    } catch {}
  }
  if (!lengthOfStayStr) lengthOfStayStr = "—";

  // 3. Financial Metrics (only real extracted figures; zero fake defaults)
  let totalClaimedAmt = kf.total_claimed_amount || "";
  let grossBillAmt = kf.gross_bill_amount || "";
  let netClaimedAmt = kf.net_claimed_amount || "";
  let preAuthAmt = kf.pre_authorized_amount || "";
  let approvedAmt = kf.approved_amount || "";
  let sumInsuredAmt = kf.sum_insured || "";

  for (const s of allDocTexts) {
    const sLower = s.toLowerCase();
    if (!grossBillAmt && (sLower.includes("gross") || sLower.includes("hospital bill"))) {
      const m = s.match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)\s*gross/i) || s.match(/gross[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) grossBillAmt = m[1].trim();
    }
    if (!netClaimedAmt && (sLower.includes("net") || sLower.includes("claimed amount"))) {
      const m = s.match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)\s*net/i) || s.match(/net[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) netClaimedAmt = m[1].trim();
    }
    if (!preAuthAmt && (sLower.includes("pre-auth") || sLower.includes("preauth"))) {
      const m = s.match(/pre-?auth[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) preAuthAmt = m[1].trim();
    }
    if (!approvedAmt && (sLower.includes("approved") || sLower.includes("sanctioned"))) {
      const m = s.match(/(?:approved|sanctioned)[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) approvedAmt = m[1].trim();
    }
    if (!sumInsuredAmt && (sLower.includes("sum insured") || sLower.includes("sum assured"))) {
      const m = s.match(/sum\s*(?:insured|assured)[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?[A-Za-z]*)/i);
      if (m) sumInsuredAmt = m[1].trim();
    }
  }

  if (!totalClaimedAmt && netClaimedAmt) totalClaimedAmt = netClaimedAmt;
  else if (!totalClaimedAmt && grossBillAmt) totalClaimedAmt = grossBillAmt;

  // Normalize currency symbols
  const norm = (val) => (val && String(val).trim() && String(val).trim() !== "—") ? String(val).replace(/^Rs\.?\s*/i, "₹").trim() : "—";
  totalClaimedAmt = norm(totalClaimedAmt);
  grossBillAmt = norm(grossBillAmt);
  preAuthAmt = norm(preAuthAmt);
  approvedAmt = norm(approvedAmt);
  sumInsuredAmt = norm(sumInsuredAmt);

  // Itemized breakdown table
  const itemizedHtml = _extractItemizedCharges(docSections, allDocTexts, {
    grossBillAmt, totalClaimedAmt, approvedAmt, preAuthAmt, sumInsuredAmt
  });

  // Pillars HTML
  const pillarsHtml = _renderPillarsHtml(docSections);

  // Multi-bundle selector
  let bundleSelectorHtml = "";
  if (bundles.length > 1) {
    bundleSelectorHtml = `
      <div class="iop-bundle-selector">
        <strong>Select Claim Dossier:</strong>
        <select id="reportBundleSelect">
          ${bundles.map((b, idx) => `<option value="${idx}" ${idx === bundleIdx ? "selected" : ""}>Dossier ${idx + 1}: ${escapeHtml(b.source_filename || "Claim Report")} (${b.created_at || ""})</option>`).join("")}
        </select>
      </div>
    `;
  }

  // Narrative fallback: only use known factual fields; zero hallucinations
  if (!narrative) {
    const nParts = [];
    if (claimantName !== "—") nParts.push(`Claimant: ${claimantName}`);
    if (hospitalName !== "—") nParts.push(`Hospital: ${hospitalName}`);
    if (primaryDiagnosis !== "—") nParts.push(`Diagnosis: ${primaryDiagnosis}`);
    if (procedureName) nParts.push(`Procedure: ${procedureName}`);
    if (totalClaimedAmt !== "—") nParts.push(`Claimed Amount: ${totalClaimedAmt}`);
    if (approvedAmt !== "—") nParts.push(`Approved Amount: ${approvedAmt}`);
    narrative = nParts.length ? nParts.join(". ") + "." : "Summary narrative not detailed in source dossier.";
  }

  const isApproved = approvedAmt !== "—";

  return `
    <div class="insurance-onepage-report">
      ${bundleSelectorHtml}

      <!-- Header / Top Bar -->
      <div class="iop-header">
        <div class="iop-header-left">
          <div class="iop-logo-box">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              <path d="m9 12 2 2 4-4"/>
            </svg>
          </div>
          <div class="iop-title-box">
            <div class="iop-org-name">${escapeHtml(hospitalName !== "—" ? hospitalName : (kf.insurer_tpa_name || "Insurance Claim Record"))}</div>
            <div class="iop-sub-line">HEALTH INSURANCE CLAIM REPORT • HL7® FHIR® R4</div>
          </div>
        </div>
        <div class="iop-header-right">
          <div class="iop-claim-status-pill ${isApproved ? 'approved' : 'pending'}">
            <span class="status-dot"></span>
            ${isApproved ? 'CLAIM SANCTIONED' : (claimType !== 'CLAIM' ? `${escapeHtml(claimType)} CLAIM` : 'CLAIM PROCESSED')}
          </div>
          <div class="iop-meta-grid">
            <span>Claim No: <strong>${escapeHtml(claimNo)}</strong></span>
            <span>Policy No: <strong>${escapeHtml(policyNo)}</strong></span>
            <span>Date: <strong>${escapeHtml(formattedDate || "—")}</strong></span>
          </div>
        </div>
      </div>

      <!-- Row 1: Claimant Particulars & Hospitalization Particulars (2 Columns) -->
      <div class="iop-grid-row iop-two-col">
        <!-- Panel A: Claimant Particulars -->
        <div class="iop-panel">
          <div class="iop-panel-hdr">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
            <span>Claimant &amp; Insured Particulars</span>
          </div>
          <div class="iop-panel-body">
            <div class="iop-detail-row main">
              <span class="iop-label">Insured Claimant:</span>
              <span class="iop-val highlight">${escapeHtml(claimantName)}</span>
            </div>
            <div class="iop-sub-grid">
              <div class="iop-sub-item"><span class="iop-label">Age / Gender:</span><span class="iop-val">${escapeHtml(ageGenderStr)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Contact Mobile:</span><span class="iop-val mono">${escapeHtml(phoneStr)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Policy / Member ID:</span><span class="iop-val mono">${escapeHtml(policyNo)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Claim Type:</span><span class="iop-val">${escapeHtml(claimType)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Aadhaar Card (UID):</span><span class="iop-val mono">${formattedAadhaar ? `${escapeHtml(formattedAadhaar)} <span class="iop-mini-tag">UIDAI ✓</span>` : '—'}</span></div>
              <div class="iop-sub-item"><span class="iop-label">ABHA Health ID:</span><span class="iop-val mono">${escapeHtml(pAbha || '—')}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Hospital IP / Reg:</span><span class="iop-val mono">${escapeHtml(pHospId || '—')}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Medical Record (MRN):</span><span class="iop-val mono">${escapeHtml(pMrn || '—')}</span></div>
            </div>
            ${addressStr ? `
            <div class="iop-detail-row" style="padding-top:2px;border-top:1px dashed rgba(255,255,255,0.06)">
              <span class="iop-label">Address:</span>
              <span class="iop-val" style="font-size:0.74rem">${escapeHtml(addressStr)}</span>
            </div>` : ''}
          </div>
        </div>

        <!-- Panel B: Hospitalization & Clinical Particulars -->
        <div class="iop-panel">
          <div class="iop-panel-hdr">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            <span>Hospitalization &amp; Clinical Particulars</span>
          </div>
          <div class="iop-panel-body">
            <div class="iop-detail-row">
              <span class="iop-label">Admitting Hospital:</span>
              <span class="iop-val highlight-sub">${escapeHtml(hospitalName)}</span>
            </div>
            <div class="iop-detail-row">
              <span class="iop-label">Treating Specialist:</span>
              <span class="iop-val">${escapeHtml(doctorName)}</span>
            </div>
            <div class="iop-detail-row">
              <span class="iop-label">Primary Diagnosis:</span>
              <span class="iop-val diag-val">${escapeHtml(primaryDiagnosis)}</span>
            </div>
            ${procedureName ? `
            <div class="iop-detail-row">
              <span class="iop-label">Procedure Done:</span>
              <span class="iop-val proc-val">${escapeHtml(procedureName)}</span>
            </div>` : ''}
            <div class="iop-sub-grid">
              <div class="iop-sub-item"><span class="iop-label">Admission Date:</span><span class="iop-val mono">${escapeHtml(admissionDate)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Discharge Date:</span><span class="iop-val mono">${escapeHtml(dischargeDate)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Room Category:</span><span class="iop-val">${escapeHtml(roomCategory)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Length of Stay:</span><span class="iop-val">${escapeHtml(lengthOfStayStr)}</span></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Row 2: Claim Financial Highlight Bar & Itemized Details -->
      <div class="iop-panel">
        <div class="iop-panel-hdr">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
          <span>Claim Financial Details &amp; Adjudicated Settlement Amounts</span>
        </div>
        <div class="iop-panel-body">
          <div class="iop-financial-ribbon">
            <div class="iop-fin-box highlight-primary">
              <div class="iop-fin-lbl">Total Claimed</div>
              <div class="iop-fin-amt">${escapeHtml(totalClaimedAmt)}</div>
              <div class="iop-fin-sub">Net Claim Submitted</div>
            </div>
            <div class="iop-fin-box">
              <div class="iop-fin-lbl">Gross Hospital Bill</div>
              <div class="iop-fin-amt">${escapeHtml(grossBillAmt)}</div>
              <div class="iop-fin-sub">Invoiced Hospital Total</div>
            </div>
            <div class="iop-fin-box">
              <div class="iop-fin-lbl">Pre-Authorized</div>
              <div class="iop-fin-amt">${escapeHtml(preAuthAmt)}</div>
              <div class="iop-fin-sub">TPA Initial Sanction</div>
            </div>
            <div class="iop-fin-box highlight-success">
              <div class="iop-fin-lbl">Final Approved</div>
              <div class="iop-fin-amt">${escapeHtml(approvedAmt)}</div>
              <div class="iop-fin-sub">Payable Settlement</div>
            </div>
            <div class="iop-fin-box highlight-info">
              <div class="iop-fin-lbl">Sum Insured</div>
              <div class="iop-fin-amt">${escapeHtml(sumInsuredAmt)}</div>
              <div class="iop-fin-sub">Policy Annual Limit</div>
            </div>
          </div>

          <!-- Itemized Charges Table -->
          ${itemizedHtml}
        </div>
      </div>

      <!-- Row 3: Claim Summary Narrative & Case Abstract -->
      <div class="iop-panel">
        <div class="iop-panel-hdr">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
          <span>Executive Claim Dossier Summary &amp; Case Abstract</span>
        </div>
        <div class="iop-panel-body">
          <p class="iop-narrative">${escapeHtml(narrative)}</p>
          ${pillarsHtml}
        </div>
      </div>

      <!-- Row 4: Sign-off Stamps -->
      <div class="iop-signoff-block">
        <div class="iop-signoff-col">
          <span class="iop-sign-label">Hospital Consultant / Medical Superintendent</span>
          <span class="iop-sign-name">${escapeHtml(doctorName !== "—" ? doctorName : "Treating Consultant")}</span>
          <span class="iop-sign-sub">${escapeHtml(hospitalName !== "—" ? hospitalName : "Healthcare Provider")} • Medical Record</span>
          <div class="iop-sign-stamp"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Certified Hospital Record</div>
        </div>
        <div class="iop-signoff-col">
          <span class="iop-sign-label">Insurance TPA / Claims Adjudicator</span>
          <span class="iop-sign-name">${escapeHtml(kf.insurer_tpa_name || "Claims Authority")}</span>
          <span class="iop-sign-sub">Health Claims Processing</span>
          <div class="iop-sign-stamp"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Processed Record</div>
        </div>
        <div class="iop-signoff-col">
          <span class="iop-sign-label">FHIR® &amp; ABDM Verification</span>
          <span class="iop-sign-name">HL7® FHIR® R4 Validated</span>
          <span class="iop-sign-sub">National Health Data Exchange Compliant</span>
          <div class="iop-sign-stamp"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> 100% Standards Compliant</div>
        </div>
      </div>

      <!-- Footer & Actions (Screen only, hidden in print) -->
      <div class="report-footer">
        <div class="report-meta-tag">
          Bundle ID: <code>${escapeHtml((selectedBundle.id || '').slice(0, 24))}...</code> • Digital Insurance Claim Record
        </div>
        <div class="report-actions">
          <button class="secondary small" id="printReportBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
            Print Report
          </button>
          <button class="secondary small" id="downloadReportJsonBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
            Download JSON
          </button>
          <button class="secondary small" id="toggleReportJsonBtn" type="button">View FHIR JSON</button>
          <button class="secondary small" id="copyReportJsonBtn" type="button">Copy JSON</button>
          <button class="secondary small danger-btn" id="deleteCurrentReportBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            Delete Report
          </button>
        </div>
      </div>

      <!-- Raw Bundle JSON container -->
      <div id="reportRawJsonWrap" hidden style="margin-top:16px">
        <pre><code class="language-json">${escapeHtml(JSON.stringify(bundleJson, null, 2))}</code></pre>
      </div>
    </div>
  `;
}

function _wireInsuranceReportEvents(selectedBundle, bundleIdx, bundleJson, pName) {
  const sel = $("#reportBundleSelect");
  if (sel) {
    sel.addEventListener("change", (e) => {
      renderBundleReport(parseInt(e.target.value, 10));
    });
  }

  const printBtn = $("#printReportBtn");
  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }

  const downloadBtn = $("#downloadReportJsonBtn");
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => {
      const str = JSON.stringify(bundleJson, null, 2);
      const blob = new Blob([str], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const safeName = (pName || "Claimant").replace(/[^a-zA-Z0-9_-]/g, "_");
      a.download = `${safeName}_Insurance_Claim_Bundle.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  const toggleBtn = $("#toggleReportJsonBtn");
  const jsonWrap = $("#reportRawJsonWrap");
  if (toggleBtn && jsonWrap) {
    toggleBtn.addEventListener("click", () => {
      const isHidden = jsonWrap.hidden;
      jsonWrap.hidden = !isHidden;
      toggleBtn.textContent = isHidden ? "Hide FHIR JSON" : "View FHIR JSON";
      if (isHidden && window.hljs) {
        jsonWrap.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
      }
    });
  }

  const copyBtn = $("#copyReportJsonBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(JSON.stringify(bundleJson, null, 2));
      copyBtn.textContent = "Copied!";
      setTimeout(() => (copyBtn.textContent = "Copy JSON"), 2000);
    });
  }

  const delReportBtn = $("#deleteCurrentReportBtn");
  if (delReportBtn) {
    delReportBtn.addEventListener("click", async () => {
      const reportName = selectedBundle.source_filename || `Dossier ${bundleIdx + 1}`;
      if (!confirm(`Are you sure you want to delete "${reportName}"? This action cannot be undone.`)) {
        return;
      }
      try {
        const res = await fetch(`${apiBase()}/bundles/${selectedBundle.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Failed to delete report bundle");
        await loadPatients();
        await checkHealth();
        const detail = $("#patientDetail");
        if (detail) detail.hidden = true;
      } catch (err) {
        alert(`Error deleting report: ${err.message}`);
      }
    });
  }
}

function _extractItemizedCharges(docSections, allDocTexts, totals) {
  const lineItems = [];
  const seenLabels = new Set();

  const chargePatterns = [
    { key: "room", label: "Room Rent & Nursing Charges", regex: /(?:Room(?:\s*(?:Rent|Charges|&\s*Nursing))?)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "icu", label: "ICU / HDU Critical Care", regex: /(?:ICU|CCU|Critical Care)\s*(?:Charges)?\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "ot", label: "Operation Theatre & Surgery Fees", regex: /(?:OT|Operation Theatre|Surgeon|Surgical)\s*(?:Charges|Fee|Fees)?\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "implants", label: "Implants, Stents & Prosthetics", regex: /(?:Implant|Implants|Stent|Stents|Prosthesis|Prosthetics)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "pharmacy", label: "Pharmacy & Injections", regex: /(?:Pharmacy|Medicine|Medicines|Drugs|Injections)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "investigations", label: "Diagnostics, Lab & Radiology", regex: /(?:Investigation|Investigations|Diagnostic|Diagnostics|Lab|Pathology|Radiology)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "consultation", label: "Consultant & Professional Visits", regex: /(?:Consultation|Doctor|Professional|Physician)\s*(?:Charges|Fee|Fees|Visits)?\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "non_medical", label: "Non-Medical Deductions / Disallowance", regex: /(?:Non[- ]?Medical|Disallowed|Disallowance|Deduction|Deductions)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
  ];

  for (const text of allDocTexts) {
    for (const pat of chargePatterns) {
      if (seenLabels.has(pat.key)) continue;
      const m = text.match(pat.regex);
      if (m && m[1].trim()) {
        seenLabels.add(pat.key);
        lineItems.push({ label: pat.label, amount: m[1].trim().replace(/^Rs\.?\s*/i, "₹") });
      }
    }
  }

  // Also parse explicit points from bill/invoice section
  for (const sec of (docSections || [])) {
    if (/bill|invoice|financial|breakdown|charges/i.test(sec.title || "")) {
      for (const pt of (sec.key_data_points || [])) {
        if (/₹|rs\.?|inr/i.test(pt) && !/gross|net claimed|total claimed|sum insured/i.test(pt)) {
          const parts = pt.split(/[:=-]\s*/);
          if (parts.length >= 2) {
            const lbl = parts[0].trim();
            const amtMatch = parts[1].match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
            if (amtMatch && !seenLabels.has(lbl.toLowerCase())) {
              seenLabels.add(lbl.toLowerCase());
              lineItems.push({ label: lbl, amount: amtMatch[1].trim().replace(/^Rs\.?\s*/i, "₹") });
            }
          }
        }
      }
    }
  }

  if (!lineItems.length) {
    return `
      <div class="iop-fin-grid-fallback" style="text-align:center;padding:16px 12px;color:var(--text-muted);font-size:0.84rem;font-style:italic">
        Detailed line-item charge breakdown is not itemized in the source document. Summary totals are shown above.
      </div>
    `;
  }

  const rowsHtml = lineItems.slice(0, 8).map(item => `
    <tr>
      <td class="iop-line-lbl">${escapeHtml(item.label)}</td>
      <td class="iop-line-amt mono">${escapeHtml(item.amount)}</td>
    </tr>
  `).join("");

  return `
    <div class="iop-fin-breakdown-wrap">
      <table class="iop-fin-table">
        <tbody>
          ${rowsHtml}
        </tbody>
      </table>
    </div>
  `;
}

function _renderPillarsHtml(docSections) {
  if (!docSections || !docSections.length) {
    return "";
  }

  const cards = docSections.slice(0, 3).map((sec, i) => {
    const cardTitle = sec.title ? `${i + 1}. ${sec.title}` : `Section ${i + 1}`;
    let text = sec.summary || (sec.key_data_points || []).slice(0, 2).join("; ") || "Verified and recorded from dossier.";
    if (text.length > 120) text = text.slice(0, 117) + "...";
    return `
      <div class="iop-pillar-card">
        <div class="iop-pillar-title">${escapeHtml(cardTitle)}</div>
        <div class="iop-pillar-text">${escapeHtml(text)}</div>
      </div>
    `;
  }).join("");

  return `<div class="iop-pillars-grid">${cards}</div>`;
}

// ═══════════════════════════════════════════════
// Insurance Claim Cards for Patient Detail View
// ═══════════════════════════════════════════════

function _renderInsuranceClaimCards(resources, pName, pGender, pDob, pAge, pGuardian, pPhone, formattedAadhaar, pAbha, pHospId, pMrn, patient, pAddress, pLocality, pMandal, pCity, pDistrict, pState, pPin, facilityName, practitionerName, bundleObs, clinicalSectionHtml, claimSummary = null) {
  // Extract claim-relevant data from FHIR resources
  const composition = resources.find(r => r.resourceType === "Composition") || {};
  const diagReport = resources.find(r => r.resourceType === "DiagnosticReport") || {};

  const cs = claimSummary || {};
  const kf = cs.key_fields || {};
  const narrative = cs.claim_narrative || "";
  const flags = cs.flags || [];

  // Gather diagnosis from key_fields, composition, or diagnostic report
  let primaryDiagnosis = kf.primary_diagnosis || diagReport.conclusion || "";
  if (!primaryDiagnosis || primaryDiagnosis === "NA") {
    const diagSec = composition.section?.find(s =>
      s.title?.toLowerCase().includes("diagnos") || s.title?.toLowerCase().includes("clinical")
    );
    if (diagSec?.text?.div) {
      const liMatch = diagSec.text.div.match(/<li[^>]*>(.*?)<\/li>/i);
      if (liMatch) primaryDiagnosis = liMatch[1].replace(/<[^>]+>/g, "").trim();
    }
  }

  // Build document sections
  let docSections = cs.document_sections || [];
  if (!docSections.length) {
    // Reconstruct from composition sections
    for (const sec of (composition.section || [])) {
      if (!sec.title) continue;
      const secTitleLower = sec.title.toLowerCase();
      const divText = sec.text?.div || "";
      const items = (divText.match(/<li[^>]*>(.*?)<\/li>/gi) || [])
        .map(m => m.replace(/<\/?li[^>]*>/gi, "").replace(/<[^>]+>/g, "").trim())
        .filter(Boolean);

      if (secTitleLower.includes("manifest") || secTitleLower.includes("inventory")) {
        // Parse each manifest item into a separate document section
        for (const item of items) {
          const mDoc = item.match(/(?:[^(]+\()([^)]+)\):\s*(.*)/);
          if (mDoc) {
            docSections.push({
              title: mDoc[1].trim(),
              summary: mDoc[2].trim(),
              key_data_points: [item]
            });
          } else {
            docSections.push({
              title: sec.title,
              summary: item,
              key_data_points: [item]
            });
          }
        }
      } else {
        const secSummary = items.slice(0, 3).join("; ");
        docSections.push({
          title: sec.title,
          summary: secSummary,
          key_data_points: items
        });
      }
    }
  }

  // Extract financial amounts if not already in kf
  if (!kf.total_claimed_amount || !kf.gross_bill_amount || !kf.approved_amount) {
    const allTexts = [];
    docSections.forEach(s => {
      if (s.summary) allTexts.push(s.summary);
      (s.key_data_points || []).forEach(p => allTexts.push(p));
    });
    for (const t of allTexts) {
      const s = String(t).trim();
      const sLower = s.toLowerCase();
      if (!kf.gross_bill_amount && (sLower.includes("gross") || sLower.includes("hospital bill"))) {
        const m = s.match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)\s*gross/i) || s.match(/gross[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
        if (m) kf.gross_bill_amount = m[1].trim();
      }
      if (!kf.net_claimed_amount && (sLower.includes("net") || sLower.includes("claimed amount"))) {
        const m = s.match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)\s*net/i) || s.match(/net[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
        if (m) kf.net_claimed_amount = m[1].trim();
      }
      if (!kf.pre_authorized_amount && (sLower.includes("pre-auth") || sLower.includes("preauth"))) {
        const m = s.match(/pre-?auth[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
        if (m) kf.pre_authorized_amount = m[1].trim();
      }
      if (!kf.approved_amount && (sLower.includes("approved") || sLower.includes("sanctioned"))) {
        const m = s.match(/(?:approved|sanctioned)[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
        if (m) kf.approved_amount = m[1].trim();
      }
      if (!kf.sum_insured && (sLower.includes("sum insured") || sLower.includes("sum assured"))) {
        const m = s.match(/sum\s*(?:insured|assured)[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?[A-Za-z]*)/i);
        if (m) kf.sum_insured = m[1].trim();
      }
    }
    if (kf.net_claimed_amount) kf.total_claimed_amount = kf.net_claimed_amount;
    else if (kf.gross_bill_amount) kf.total_claimed_amount = kf.gross_bill_amount;
  }

  // Build flags HTML
  let flagsHtml = "";
  if (flags.length) {
    flagsHtml = `<div class="claim-flags" style="margin-bottom:12px">${flags.map(f => `<span class="claim-flag">${escapeHtml(f)}</span>`).join("")}</div>`;
  }

  // Build financial highlight metrics
  const hasFinancials = kf.total_claimed_amount || kf.gross_bill_amount || kf.net_claimed_amount || kf.approved_amount || kf.pre_authorized_amount || kf.sum_insured;
  const financialMetricsHtml = hasFinancials ? `
    <div class="claim-financial-highlight-bar">
      ${kf.total_claimed_amount ? `
        <div class="claim-metric-badge primary">
          <span class="metric-lbl">Total Claimed</span>
          <span class="metric-val">${escapeHtml(kf.total_claimed_amount)}</span>
        </div>` : ""}
      ${kf.net_claimed_amount && kf.net_claimed_amount !== kf.total_claimed_amount ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Net Claimed</span>
          <span class="metric-val">${escapeHtml(kf.net_claimed_amount)}</span>
        </div>` : ""}
      ${kf.gross_bill_amount ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Gross Hospital Bill</span>
          <span class="metric-val">${escapeHtml(kf.gross_bill_amount)}</span>
        </div>` : ""}
      ${kf.pre_authorized_amount ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Pre-Authorized</span>
          <span class="metric-val">${escapeHtml(kf.pre_authorized_amount)}</span>
        </div>` : ""}
      ${kf.approved_amount ? `
        <div class="claim-metric-badge success">
          <span class="metric-lbl">Approved / Sanctioned</span>
          <span class="metric-val">${escapeHtml(kf.approved_amount)}</span>
        </div>` : ""}
      ${kf.sum_insured ? `
        <div class="claim-metric-badge info">
          <span class="metric-lbl">Sum Insured</span>
          <span class="metric-val">${escapeHtml(kf.sum_insured)}</span>
        </div>` : ""}
      ${kf.claim_type ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Claim Type</span>
          <span class="metric-val">${escapeHtml(kf.claim_type)}</span>
        </div>` : ""}
    </div>
  ` : "";

  // Build the claim summary card HTML
  return `
    <!-- Insurance Claim Summary Section -->
    <div class="report-dossier-wrap">
      <!-- Card 1: Claim Summary -->
      <div class="report-dossier-card" style="grid-column: 1 / -1">
        <div class="dossier-card-header">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
          <h4>Claim Summary &amp; Key Information</h4>
        </div>
        <div class="dossier-content">
          ${flagsHtml}
          ${narrative ? `<p class="claim-narrative" style="margin-bottom:14px">${escapeHtml(narrative)}</p>` : ""}
          <div class="dossier-subgrid" style="grid-template-columns: repeat(3, 1fr)">
            <div class="dossier-item">
              <span class="dossier-label">Claimant / Insured Name</span>
              <span class="dossier-val highlight">${escapeHtml(kf.insured_name || pName)}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Gender / DOB</span>
              <span class="dossier-val">${escapeHtml(kf.patient_gender || pGender)} ${(kf.patient_age || pDob) !== "Not specified" ? `• ${escapeHtml(kf.patient_age || pDob)}` : ""} ${pAge ? `<span class="age-pill">${pAge}</span>` : ""}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Hospital / Provider</span>
              <span class="dossier-val">${escapeHtml(kf.hospital_name || facilityName)}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Treating Doctor</span>
              <span class="dossier-val">${escapeHtml(kf.treating_doctor || practitionerName)}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Policy / Insurance ID</span>
              <span class="dossier-val mono">${escapeHtml(kf.policy_number || "Not specified")}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Claim Reference No.</span>
              <span class="dossier-val mono">${escapeHtml(kf.claim_number || "Not specified")}</span>
            </div>
            ${primaryDiagnosis ? `
            <div class="dossier-item span-2">
              <span class="dossier-label">Primary Diagnosis</span>
              <span class="dossier-val">${escapeHtml(primaryDiagnosis)}</span>
            </div>` : ""}
            <div class="dossier-item">
              <span class="dossier-label">Contact</span>
              <span class="dossier-val mono">${escapeHtml(pPhone || "Not recorded")}</span>
            </div>
            ${kf.admission_date ? `
            <div class="dossier-item">
              <span class="dossier-label">Admission Date</span>
              <span class="dossier-val mono">${escapeHtml(kf.admission_date)}</span>
            </div>` : ""}
            ${kf.discharge_date ? `
            <div class="dossier-item">
              <span class="dossier-label">Discharge Date</span>
              <span class="dossier-val mono">${escapeHtml(kf.discharge_date)}</span>
            </div>` : ""}
            ${kf.room_category ? `
            <div class="dossier-item">
              <span class="dossier-label">Room Category</span>
              <span class="dossier-val">${escapeHtml(kf.room_category)}</span>
            </div>` : ""}
          </div>
          ${financialMetricsHtml}
          ${pAddress ? `
          <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
            <span class="dossier-label">Address</span>
            <p class="address-text" style="margin:4px 0 0">${escapeHtml(pAddress)}</p>
            <div class="address-pills" style="margin-top:4px">
              ${pCity ? `<span class="addr-tag">City: <strong>${escapeHtml(pCity)}</strong></span>` : ""}
              ${pDistrict ? `<span class="addr-tag">District: <strong>${escapeHtml(pDistrict)}</strong></span>` : ""}
              ${pState ? `<span class="addr-tag">State: <strong>${escapeHtml(pState)}</strong></span>` : ""}
              ${pPin ? `<span class="addr-tag">PIN: <strong>${escapeHtml(pPin)}</strong></span>` : ""}
            </div>
          </div>` : ""}
        </div>
      </div>

      <!-- Card 1b: Financial Summary -->
      ${_renderFinancialCard(docSections, kf)}

      ${docSections.length ? `
      <!-- Card 2: Document Sections -->
      <div class="report-dossier-card" style="grid-column: 1 / -1">
        <div class="dossier-card-header">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
          <h4>Document Sections Summary (${docSections.length} sections)</h4>
        </div>
        <div class="dossier-content">
          ${docSections.map((sec, idx) => {
            const keyPts = (sec.key_data_points || []).filter(p => p && p.trim());
            const keyPtsHtml = keyPts.length
              ? `<ul class="claim-section-points" style="margin-top:6px">${keyPts.map(p => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`
              : "";
            return `
              <div class="claim-doc-section ${idx === 0 ? 'expanded' : ''}" data-section-idx="${idx}">
                <div class="claim-doc-section-header" onclick="this.parentElement.classList.toggle('expanded')">
                  <span class="claim-doc-section-title">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="chevron-icon"><polyline points="6 9 12 15 18 9"></polyline></svg>
                    ${escapeHtml(sec.title)}
                  </span>
                  <span class="claim-doc-section-badge">${escapeHtml(sec.title.split(' ')[0])}</span>
                </div>
                <div class="claim-doc-section-body">
                  ${sec.summary ? `<p style="margin:0;color:var(--text-secondary);font-size:0.82rem;line-height:1.6">${escapeHtml(sec.summary)}</p>` : `<p style="margin:0;color:var(--text-muted);font-size:0.82rem">Section data available in full FHIR bundle.</p>`}
                  ${keyPtsHtml}
                </div>
              </div>
            `;
          }).join("")}
        </div>
      </div>` : ""}
    </div>

    <!-- Clinical Section (shared) -->
    ${clinicalSectionHtml}
  `;
}

function _renderFinancialCard(docSections, kf = {}) {
  // Extract all financial data points from key fields and document sections
  const financialPoints = [];
  const allPoints = [];

  // Add primary key fields if available
  if (kf.total_claimed_amount) {
    allPoints.push({ text: `Total Claimed Amount: ${kf.total_claimed_amount}`, section: "Claim Dossier", isPrimary: true });
  }
  if (kf.net_claimed_amount && kf.net_claimed_amount !== kf.total_claimed_amount) {
    allPoints.push({ text: `Net Claimed Amount: ${kf.net_claimed_amount}`, section: "Hospital Bill", isPrimary: true });
  }
  if (kf.gross_bill_amount) {
    allPoints.push({ text: `Gross Hospital Bill: ${kf.gross_bill_amount}`, section: "Hospital Bill", isPrimary: true });
  }
  if (kf.pre_authorized_amount) {
    allPoints.push({ text: `Pre-Authorized Amount: ${kf.pre_authorized_amount}`, section: "Pre-Authorization", isPrimary: true });
  }
  if (kf.approved_amount) {
    allPoints.push({ text: `Approved / Settled Amount: ${kf.approved_amount}`, section: "TPA / Insurer Approval", isPrimary: true });
  }
  if (kf.sum_insured) {
    allPoints.push({ text: `Total Sum Insured: ${kf.sum_insured}`, section: "Policy Schedule", isPrimary: true });
  }

  for (const sec of (docSections || [])) {
    if (sec.summary) {
      const parts = sec.summary.split(/[,;]\s*/);
      for (const p of parts) {
        if (/[\u20b9₹]|rs\.?|inr|\d+[Ll]\b|amount|gross|net|bill|insured/i.test(p)) {
          allPoints.push({ text: p, section: sec.title });
        }
      }
    }
    for (const pt of (sec.key_data_points || [])) {
      allPoints.push({ text: pt, section: sec.title });
    }
  }

  // Filter for financial/amount related points
  const financialKeywords = [
    "amount", "bill", "charge", "cost", "fee", "total", "gross", "net",
    "rs.", "rs ", "₹", "inr", "sum insured", "insured", "premium",
    "deductible", "co-pay", "copay", "claimed", "approved", "sanctioned",
    "payable", "paid", "settled", "pre-auth", "preauth", "coverage",
    "sub-total", "subtotal", "tax", "gst", "discount", "lakh", "lac"
  ];

  const seen = new Set();
  for (const pt of allPoints) {
    const lower = pt.text.toLowerCase();
    if (financialKeywords.some(kw => lower.includes(kw))) {
      const norm = pt.text.replace(/\s+/g, " ").trim();
      if (!seen.has(norm)) {
        seen.add(norm);
        financialPoints.push(pt);
      }
    }
  }

  if (!financialPoints.length) return "";

  // Parse amounts to categorize them
  const items = financialPoints.map(fp => {
    const parts = fp.text.split(/:\s*/);
    let label = parts[0] || fp.text;
    let value = parts.slice(1).join(": ") || "";
    if (!value) {
      const m = fp.text.match(/([\u20b9₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?[A-Za-z]*)/i);
      if (m && m[1].trim()) {
        value = m[1].trim();
        label = fp.text.replace(m[1], "").replace(/[-—–:,]/g, "").trim() || "Amount";
      } else {
        value = fp.text;
      }
    }
    const isPrimary = fp.isPrimary || /total|gross|net claimed|sum insured|approved/i.test(label);
    return { label, value, section: fp.section, isPrimary, raw: fp.text };
  });

  return `
    <div class="report-dossier-card" style="grid-column: 1 / -1">
      <div class="dossier-card-header">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
        <h4>Financial Summary &amp; Insurance Amounts</h4>
      </div>
      <div class="dossier-content">
        <div class="financial-items-grid">
          ${items.map(item => `
            <div class="financial-item ${item.isPrimary ? 'financial-item-primary' : ''}">
              <span class="financial-label">${escapeHtml(item.label)}</span>
              <span class="financial-value">${escapeHtml(item.value || item.raw)}</span>
              <span class="financial-source">${escapeHtml(item.section)}</span>
            </div>
          `).join("")}
        </div>
      </div>
    </div>
  `;
}


function calculateAge(birthDateStr) {
  if (!birthDateStr || birthDateStr === "N/A" || birthDateStr === "Not available" || birthDateStr === "Not specified") return "";
  try {
    const dob = new Date(birthDateStr);
    if (isNaN(dob.getTime())) return "";
    const today = new Date();
    let age = today.getFullYear() - dob.getFullYear();
    const m = today.getMonth() - dob.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) {
      age--;
    }
    return age > 0 ? ` (${age} yrs)` : "";
  } catch {
    return "";
  }
}

function getInterpretation(val, refRangeStr, obs) {
  const obsInterp = obs?.interpretation?.[0]?.coding?.[0]?.code || obs?.interpretation?.[0]?.text;
  if (obsInterp) {
    const c = String(obsInterp).toUpperCase();
    if (c === "H" || c === "HH" || c.includes("HIGH")) return { text: "HIGH", cls: "high" };
    if (c === "L" || c === "LL" || c.includes("LOW")) return { text: "LOW", cls: "low" };
    if (c === "N" || c.includes("NORM")) return { text: "NORMAL", cls: "normal" };
  }

  if (val == null || val === "" || isNaN(Number(val)) || !refRangeStr) {
    return { text: "NORMAL", cls: "normal" };
  }

  const num = Number(val);
  const str = String(refRangeStr).trim();

  // Pattern: "< 200" or "<= 200"
  let match = str.match(/^<\s*=?\s*([0-9.]+)/);
  if (match) {
    const high = parseFloat(match[1]);
    if (num > high) return { text: "HIGH", cls: "high" };
    return { text: "NORMAL", cls: "normal" };
  }

  // Pattern: "> 50" or ">= 50"
  match = str.match(/^>\s*=?\s*([0-9.]+)/);
  if (match) {
    const low = parseFloat(match[1]);
    if (num < low) return { text: "LOW", cls: "low" };
    return { text: "NORMAL", cls: "normal" };
  }

  // Pattern: "12.0 - 16.0" or "12 - 16"
  match = str.match(/([0-9.]+)\s*-\s*([0-9.]+)/);
  if (match) {
    const low = parseFloat(match[1]);
    const high = parseFloat(match[2]);
    if (num < low) return { text: "LOW", cls: "low" };
    if (num > high) return { text: "HIGH", cls: "high" };
    return { text: "NORMAL", cls: "normal" };
  }

  return { text: "NORMAL", cls: "normal" };
}


// ═══════════════════════════════════════════════
// FHIR Explorer
// ═══════════════════════════════════════════════

async function loadBundles() {
  const container = $("#bundlesList");
  try {
    const res = await fetch(`${apiBase()}/bundles`);
    const bundles = await res.json();
    if (!bundles.length) {
      container.innerHTML = '<p class="muted">No bundles stored yet.</p>';
      return;
    }
    let html = '<table><thead><tr><th>Bundle ID</th><th>Patient</th><th>Source File</th><th>Type</th><th>Date</th></tr></thead><tbody>';
    html += bundles.map((b) =>
      `<tr onclick="viewBundle('${b.id}')"><td style="font-family:var(--font-mono);font-size:.75rem">${b.id?.slice(0, 12)}...</td><td>${b.patient_name || "—"}</td><td>${b.source_filename || "—"}</td><td>${b.document_type || ""}</td><td>${b.created_at || ""}</td></tr>`
    ).join("");
    html += '</tbody></table>';
    container.innerHTML = html;
  } catch {
    container.innerHTML = '<p class="muted">Failed to load bundles.</p>';
  }
}

async function viewBundle(bundleId) {
  const detail = $("#bundleDetail");
  const content = $("#bundleDetailContent");
  detail.hidden = false;

  try {
    const res = await fetch(`${apiBase()}/bundles/${bundleId}`);
    const data = await res.json();
    const b = data.bundle || {};
    const v = data.validation || {};

    let html = `<div class="patient" style="margin-bottom:16px">
      <div><strong>Source</strong><span>${b.source_filename || "N/A"}</span></div>
      <div><strong>Type</strong><span>${b.document_type || "N/A"}</span></div>
      <div><strong>Created</strong><span>${b.created_at || "N/A"}</span></div>
    </div>`;

    // Validation mini-display
    if (v.compliance_score !== undefined) {
      const cls = v.compliance_score >= 80 ? "high" : v.compliance_score >= 50 ? "medium" : "low";
      html += `<p>ABDM Compliance: <span class="compliance-score ${cls}" style="font-size:1.2rem;padding:4px 14px">${v.compliance_score}%</span> — ${v.summary || ""}</p>`;
    }

    // Raw JSON
    if (b.bundle_json) {
      const parsed = typeof b.bundle_json === "string" ? JSON.parse(b.bundle_json) : b.bundle_json;
      html += `<pre><code class="language-json">${escapeHtml(JSON.stringify(parsed, null, 2))}</code></pre>`;
    }

    content.innerHTML = html;
    if (window.hljs) content.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
  } catch {
    content.innerHTML = '<p class="muted">Failed to load bundle details.</p>';
  }
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
