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

// Batch tab elements
const batchFileInput  = $("#batchFileInput");
const batchBrowseBtn  = $("#batchBrowseBtn");
const batchStartBtn   = $("#batchStartBtn");
const batchFileList   = $("#batchFileList");
const batchProgress   = $("#batchProgress");

// Health badge
const healthBadge = $("#healthBadge");
const dbStats     = $("#dbStats");

let selectedFile = null;
let batchFiles = [];

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

  // Batch upload
  batchBrowseBtn.addEventListener("click", () => batchFileInput.click());
  batchFileInput.addEventListener("change", onBatchFilesSelected);
  batchStartBtn.addEventListener("click", startBatchUpload);

  // Batch drag-drop
  const batchDZ = $("#batchDropZone");
  if (batchDZ) {
    batchDZ.addEventListener("dragover", (e) => { e.preventDefault(); batchDZ.classList.add("drag-over"); });
    batchDZ.addEventListener("dragleave", () => batchDZ.classList.remove("drag-over"));
    batchDZ.addEventListener("drop", (e) => {
      e.preventDefault();
      batchDZ.classList.remove("drag-over");
      if (e.dataTransfer.files.length) {
        batchFileInput.files = e.dataTransfer.files;
        onBatchFilesSelected();
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

    // Duration
    if (data.duration_ms) {
      durationEl.textContent = `API processing duration: ${data.duration_ms.toFixed(2)} ms`;
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
  if (pn) pn.textContent = "Not available";
  if (pg) pg.textContent = "Not available";
  if (pb) pb.textContent = "Not available";
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
  if (pn) pn.textContent = patient.name || "Not available";
  if (pg) {
    const g = patient.gender || "";
    pg.textContent = g ? (g.charAt(0).toUpperCase() + g.slice(1)) : "Not available";
  }
  if (pb) pb.textContent = patient.birthDate || patient.dob || "Not available";

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
// Batch Processing
// ═══════════════════════════════════════════════

function onBatchFilesSelected() {
  batchFiles = Array.from(batchFileInput.files);
  batchFileList.innerHTML = batchFiles.map((f) => {
    const sizeKB = (f.size / 1024).toFixed(1);
    return `<div class="batch-file-item">${f.name} — ${sizeKB} KB</div>`;
  }).join("");
  batchStartBtn.disabled = batchFiles.length === 0;
}

async function startBatchUpload() {
  if (!batchFiles.length) return;
  batchStartBtn.disabled = true;
  batchProgress.hidden = false;
  const statusText = $("#batchStatusText");
  const progressBar = batchProgress.querySelector(".progress-bar");
  const resultsEl = $("#batchFileResults");
  statusText.textContent = "Uploading files...";
  resultsEl.innerHTML = "";

  const formData = new FormData();
  batchFiles.forEach((f) => formData.append("files", f));

  try {
    const res = await fetch("/api/batch-upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");

    const batchId = data.batch_id;
    statusText.textContent = `Batch ${batchId.slice(0, 8)}... — Processing ${data.file_count} files`;

    // Poll status
    const poll = setInterval(async () => {
      try {
        const sr = await fetch(`/api/batch-status/${batchId}`);
        const sd = await sr.json();
        const pct = sd.total > 0 ? Math.round((sd.done / sd.total) * 100) : 0;
        progressBar.style.width = `${pct}%`;
        statusText.textContent = `${sd.done} / ${sd.total} files processed (${pct}%)`;

        resultsEl.innerHTML = sd.files.map((f) =>
          `<div class="batch-result-item"><span>${f.filename}</span><span class="batch-status ${f.status}">${f.status}${f.duration_ms ? ` (${f.duration_ms.toFixed(0)}ms)` : ""}</span></div>`
        ).join("");

        if (sd.done >= sd.total) {
          clearInterval(poll);
          statusText.textContent = `Batch complete — ${sd.done} files processed.`;
          batchStartBtn.disabled = false;
          checkHealth();
        }
      } catch { /* retry */ }
    }, 2000);

  } catch (err) {
    statusText.textContent = `Error: ${err.message}`;
    batchStartBtn.disabled = false;
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
      container.innerHTML = '<p class="muted">No patients recorded yet. Upload lab reports to populate.</p>';
      return;
    }
    container.innerHTML = patients.map((p) => `
      <div class="patient-card" onclick="viewPatient('${p.id}')">
        <div class="pc-header">
          <div class="pc-name">${escapeHtml(p.name || "Unknown")}</div>
          <button class="pc-delete-btn" title="Delete Patient Record" onclick="event.stopPropagation(); deletePatient('${p.id}', '${escapeHtml(p.name || '')}')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            </svg>
          </button>
        </div>
        <div class="pc-meta">${escapeHtml(p.gender || "")} • DOB: ${escapeHtml(p.birth_date || "N/A")}</div>
        <div class="pc-badges">
          <span class="pc-badge">${p.bundle_count || 0} Report${p.bundle_count !== 1 ? "s" : ""}</span>
        </div>
      </div>
    `).join("");
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

    // Patient Demographics
    const pName = patient.name || "Unknown Patient";
    const pGender = patient.gender ? (patient.gender.charAt(0).toUpperCase() + patient.gender.slice(1)) : "Not specified";
    const pDob = patient.birth_date || "Not specified";
    const pAge = calculateAge(patient.birth_date);

    // Extract identifiers (ABHA, MRN, etc.)
    let pIdent = "ABDM Registered";
    if (patientResource.identifier && patientResource.identifier.length) {
      const idObj = patientResource.identifier[0];
      pIdent = idObj.value || "Not available";
      if (idObj.type?.coding?.[0]?.display) {
        pIdent += ` (${idObj.type.coding[0].display})`;
      }
    } else if (patient.id) {
      pIdent = patient.id.slice(0, 16) + "...";
    }

    // If no bundles, show clean placeholder
    if (!bundles.length) {
      content.innerHTML = `
        <div class="report-frame">
          <div class="report-patient-grid">
            <div class="report-cell"><span class="report-cell-label">Patient Name</span><span class="report-cell-value highlight">${escapeHtml(pName)}</span></div>
            <div class="report-cell"><span class="report-cell-label">Gender</span><span class="report-cell-value">${escapeHtml(pGender)}</span></div>
            <div class="report-cell"><span class="report-cell-label">Birth Date</span><span class="report-cell-value">${escapeHtml(pDob)}${pAge}</span></div>
            <div class="report-cell"><span class="report-cell-label">Patient UUID</span><span class="report-cell-value mono">${escapeHtml(patient.id || "")}</span></div>
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

      const sourceFilename = selectedBundle.source_filename || "N/A";
      const practitionerName = practitioner.name?.[0]?.text || practitioner.name?.[0]?.family || "Attending Pathologist / Medical Officer";

      // Gather observations for this bundle
      let bundleObs = resources.filter((r) => r.resourceType === "Observation");
      if (!bundleObs.length) {
        // Fallback to SQLite indexed observations
        bundleObs = observations.filter((o) => !o.bundle_id || o.bundle_id === selectedBundle.id);
      }

      // Format observation rows
      let obsRowsHtml = "";
      if (bundleObs.length) {
        obsRowsHtml = bundleObs.map((obs) => {
          let testName = "";
          let loincCode = "";
          let val = "N/A";
          let unit = "—";
          let refRangeStr = "Not specified";

          if (obs.resourceType === "Observation") {
            testName = obs.code?.text || obs.code?.coding?.[0]?.display || obs.code?.coding?.[0]?.code || "Test";
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
          } else {
            testName = obs.code_text || "Test";
            loincCode = obs.loinc_code || "";
            val = obs.value != null ? obs.value : "N/A";
            unit = obs.unit || "—";
            refRangeStr = obs.reference_range || "Not specified";
          }

          const interp = getInterpretation(val, refRangeStr, obs);
          const loincHtml = loincCode
            ? `<a href="https://loinc.org/${encodeURIComponent(loincCode)}" target="_blank" rel="noopener noreferrer" class="loinc-badge" title="View LOINC Definition">${escapeHtml(loincCode)}</a>`
            : `<span class="loinc-tag">Not available</span>`;

          return `
            <tr>
              <td class="test-name-cell">${escapeHtml(testName)}</td>
              <td>${loincHtml}</td>
              <td class="value-cell">${escapeHtml(String(val))}</td>
              <td class="unit-cell">${escapeHtml(unit)}</td>
              <td class="range-cell">${escapeHtml(refRangeStr)}</td>
              <td><span class="flag-badge ${interp.cls}">${interp.text}</span></td>
            </tr>
          `;
        }).join("");
      } else {
        obsRowsHtml = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No diagnostic observations found for this report.</td></tr>';
      }

      // Conclusion box
      let conclusionHtml = "";
      const conclusionText = diagReport.conclusion && diagReport.conclusion !== "NA" ? diagReport.conclusion : "";
      if (conclusionText) {
        conclusionHtml = `
          <div class="conclusion-box">
            <h4>Diagnostic Impression &amp; Clinical Notes</h4>
            <p>${escapeHtml(conclusionText)}</p>
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
                <p>HOSPITAL LABORATORY INFORMATION SYSTEM • DIAGNOSTIC REPORT RECORD (HL7® FHIR® R4)</p>
              </div>
            </div>
            <div class="report-status-box">
              <span class="report-status-pill">${escapeHtml(docStatus)}</span>
              <span class="report-meta-tag">Report Date: <strong>${escapeHtml(formattedDate)}</strong></span>
              <span class="report-meta-tag">Source: <code>${escapeHtml(sourceFilename)}</code></span>
            </div>
          </div>

          <!-- Patient Demographics Grid -->
          <div class="report-patient-grid">
            <div class="report-cell">
              <span class="report-cell-label">Patient Full Name</span>
              <span class="report-cell-value highlight">${escapeHtml(pName)}</span>
            </div>
            <div class="report-cell">
              <span class="report-cell-label">Gender / Sex</span>
              <span class="report-cell-value">${escapeHtml(pGender)}</span>
            </div>
            <div class="report-cell">
              <span class="report-cell-label">Date of Birth / Age</span>
              <span class="report-cell-value">${escapeHtml(pDob)}${pAge}</span>
            </div>
            <div class="report-cell">
              <span class="report-cell-label">Patient UUID</span>
              <span class="report-cell-value mono" title="${escapeHtml(patient.id || '')}">${escapeHtml((patient.id || '').slice(0, 16))}...</span>
            </div>
            <div class="report-cell">
              <span class="report-cell-label">Identifier (ABHA / MRN)</span>
              <span class="report-cell-value mono">${escapeHtml(pIdent)}</span>
            </div>
            <div class="report-cell">
              <span class="report-cell-label">Attending / Pathologist</span>
              <span class="report-cell-value">${escapeHtml(practitionerName)}</span>
            </div>
          </div>

          <!-- FHIR Standard Profile Strip -->
          <div class="report-profile-strip">
            <span><strong>Profile:</strong> DiagnosticReportRecord (NRCeS / ABDM)</span>
            <span><strong>Category:</strong> Laboratory (SNOMED: 4241000179101)</span>
            <span><strong>FHIR Level:</strong> FMM 3 (Trial Use)</span>
            <span><strong>Total Analytes:</strong> ${bundleObs.length}</span>
          </div>

          <!-- Clinical Observations Table -->
          <div class="table-wrap">
            <table class="report-table">
              <thead>
                <tr>
                  <th>Test / Analyte</th>
                  <th>LOINC Code</th>
                  <th>Observed Value</th>
                  <th>Unit</th>
                  <th>Biological Reference Interval</th>
                  <th>Flag</th>
                </tr>
              </thead>
              <tbody>
                ${obsRowsHtml}
              </tbody>
            </table>
          </div>

          <!-- Conclusion / Notes -->
          ${conclusionHtml}

          <!-- Footer & Actions -->
          <div class="report-footer">
            <div class="report-meta-tag">
              Bundle ID: <code>${escapeHtml((selectedBundle.id || '').slice(0, 18))}...</code> • ABDM FHIR R4 Compliant
            </div>
            <div class="report-actions">
              <button class="secondary small" id="printReportBtn" type="button">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
                Print Report
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

      // Wire up print button
      const printBtn = $("#printReportBtn");
      if (printBtn) {
        printBtn.addEventListener("click", () => window.print());
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
