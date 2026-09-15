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

// ═══════════════════════════════════════════════
// Tab Navigation
// ═══════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
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
// Health Check & Stats
// ═══════════════════════════════════════════════

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
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
    const res = await fetch("/api/stats");
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
    const response = await fetch("/api/convert", { method: "POST", body: formData });
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
  if (valSection) valSection.hidden = true;

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
    const res = await fetch("/api/patients");
    const patients = await res.json();
    if (!patients.length) {
      container.innerHTML = '<p class="muted">No patients recorded yet. Upload patient archives or lab reports to populate.</p>';
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
            <div class="pc-name">${escapeHtml(p.name || "Unknown Patient")}</div>
            <button class="pc-delete-btn" title="Delete Patient Record" onclick="event.stopPropagation(); deletePatient('${p.id}', '${escapeHtml(p.name || '')}')">
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
    const res = await fetch(`/api/patients/${patientId}`, { method: "DELETE" });
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
    const res = await fetch(`/api/patients/${patientId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const patient = data.patient || {};
    const bundles = data.bundles || [];
    const observations = data.observations || [];

    if (nameEl) {
      nameEl.textContent = `Clinical Diagnostic Report — ${patient.name || "Patient"}`;
    }

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
                <p>NABL ACCREDITED CLINICAL PATHOLOGY • HL7® FHIR® R4 / ABDM CERTIFIED CONSOLIDATED RECORD</p>
              </div>
            </div>
            <div class="report-status-box">
              <span class="report-status-pill">${escapeHtml(docStatus)}</span>
              <span class="report-meta-tag">Report Date: <strong>${escapeHtml(formattedDate)}</strong></span>
              <span class="report-meta-tag">Source Archive: <code>${escapeHtml(sourceFilename)}</code></span>
            </div>
          </div>

          <!-- Comprehensive Patient Dossier (All Extracted Details in 4 Cards) -->
          <div class="report-dossier-wrap">
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
              <span class="signoff-label">Attending Pathologist / Clinician</span>
              <span class="signoff-name">${escapeHtml(practitionerName)}</span>
              <span class="signoff-sub">${escapeHtml(facilityName)} • Clinical Pathology</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                Digitally Signed &amp; Authorized
              </div>
            </div>
            <div class="signoff-col">
              <span class="signoff-label">Medical Superintendent / Lab Director</span>
              <span class="signoff-name">Chief Medical Officer, Diagnostics</span>
              <span class="signoff-sub">Hospital Information &amp; Records Division</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                Certified Clinical Record
              </div>
            </div>
            <div class="signoff-col">
              <span class="signoff-label">FHIR &amp; ABDM Verification</span>
              <span class="signoff-name">HL7® FHIR® R4 Validated</span>
              <span class="signoff-sub">NRCeS / ABDM DiagnosticReport Record</span>
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
            const res = await fetch(`/api/bundles/${selectedBundle.id}`, { method: "DELETE" });
            if (!res.ok) throw new Error("Failed to delete report bundle");
            await loadPatients();
            await checkHealth();
            // Refresh patient detail or close if no reports left
            const refreshed = await fetch(`/api/patients/${patientId}`);
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
    const res = await fetch("/api/bundles");
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
    const res = await fetch(`/api/bundles/${bundleId}`);
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
