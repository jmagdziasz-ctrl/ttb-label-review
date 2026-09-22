const STATUS_ICON = {
  match: "✓", match_minor: "✓", needs_review: "⚠",
  mismatch: "✗", missing: "✗",
};
const STATUS_LABEL = {
  match: "Match", match_minor: "Match (minor diff)", needs_review: "Needs Review",
  mismatch: "Mismatch", missing: "Not Found",
};
const OVERALL_LABEL = {
  pass: "PASS — all fields match",
  needs_review: "NEEDS REVIEW — human check recommended",
  fail: "FAIL — discrepancy found",
};
const OVERALL_ICON = { pass: "✅", needs_review: "⚠️", fail: "❌" };
const FIELD_LABELS = {
  brand_name: "Brand Name", class_type: "Class / Type", alcohol_content: "Alcohol Content",
  net_contents: "Net Contents", government_warning: "Government Warning", country_of_origin: "Country of Origin",
  bottler_name_address: "Bottler / Importer Name & Address",
};
const METHOD_LABEL = {
  ocr: "Read automatically from the photo",
  vision: "Read using AI image analysis",
};

function formatSeconds(ms) {
  return `${(ms / 1000).toFixed(1)}s`;
}

function resultMetaLine(data) {
  const method = METHOD_LABEL[data.extraction_method] || "Read automatically from the photo";
  const confidence = data.extraction_confidence != null
    ? ` · about ${(data.extraction_confidence * 100).toFixed(0)}% sure we read it correctly`
    : "";
  return `${method}${confidence} · took ${formatSeconds(data.processing_time_ms)}`;
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => (toast.hidden = true), 6000);
}

// ---- Tabs ----
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---- Single review: image preview + drag/drop ----
const dropzone = document.getElementById("dropzone");
const imageInput = document.getElementById("image-input");
const dropzoneText = document.getElementById("dropzone-text");
const imagePreview = document.getElementById("image-preview");

function handleFileSelected(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    imagePreview.src = e.target.result;
    imagePreview.hidden = false;
    dropzoneText.textContent = file.name;
  };
  reader.readAsDataURL(file);
}
imageInput.addEventListener("change", () => handleFileSelected(imageInput.files[0]));
["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag-over"); })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) { imageInput.files = e.dataTransfer.files; handleFileSelected(file); }
});

// ---- Single review submit ----
const singleForm = document.getElementById("single-form");
const singleResult = document.getElementById("single-result");
const singleSubmit = document.getElementById("single-submit");

singleForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!imageInput.files[0]) { showToast("Please choose a label image first."); return; }

  const formData = new FormData(singleForm);
  formData.set("image", imageInput.files[0]);

  singleSubmit.disabled = true;
  singleSubmit.innerHTML = '<span class="spinner"></span>Reviewing…';
  singleResult.hidden = true;

  const started = performance.now();
  try {
    const res = await fetch("/api/review", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");
    renderSingleResult(data, performance.now() - started);
  } catch (err) {
    showToast(`Error: ${err.message}`);
  } finally {
    singleSubmit.disabled = false;
    singleSubmit.textContent = "Review Label";
  }
});

function fieldRowHtml(f) {
  const label = FIELD_LABELS[f.field] || f.field;
  return `
    <tr>
      <td>${label}</td>
      <td class="value-diff">${escapeHtml(f.submitted_value ?? "—")}</td>
      <td class="value-diff">${escapeHtml(f.extracted_value ?? "—")}</td>
      <td>
        <span class="status-pill ${f.status}">${STATUS_ICON[f.status]} ${STATUS_LABEL[f.status]}</span>
        ${f.note ? `<div class="note">${escapeHtml(f.note)}</div>` : ""}
      </td>
    </tr>`;
}

function renderSingleResult(data, clientMs) {
  const warningsHtml = data.warnings && data.warnings.length
    ? `<div class="warnings-box"><strong>Heads up:</strong><ul>${data.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul></div>`
    : "";

  singleResult.innerHTML = `
    <div class="overall-banner ${data.overall_status}">
      <span class="icon">${OVERALL_ICON[data.overall_status]}</span>
      <div>
        ${OVERALL_LABEL[data.overall_status]}
        <span class="meta-line">${resultMetaLine(data)}</span>
      </div>
    </div>
    ${warningsHtml}
    <table class="field-table">
      <thead><tr><th>Field</th><th>Application Says</th><th>Label Shows</th><th>Result</th></tr></thead>
      <tbody>${data.fields.map(fieldRowHtml).join("")}</tbody>
    </table>
  `;
  singleResult.hidden = false;
  singleResult.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- Quick Batch: no spreadsheet, paste text and/or upload text files ----
const quickBatchForm = document.getElementById("quick-batch-form");
const quickBatchResult = document.getElementById("quick-batch-result");
const quickBatchSubmit = document.getElementById("quick-batch-submit");

document.getElementById("quick-batch-example-link").addEventListener("click", (e) => {
  e.preventDefault();
  const box = document.getElementById("quick-batch-example");
  box.hidden = !box.hidden;
});

quickBatchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = document.getElementById("quick-batch-text").value;
  const appFiles = document.getElementById("quick-batch-files-input").files;
  const imageFiles = document.getElementById("quick-batch-images-input").files;

  if (!text.trim() && !appFiles.length) {
    showToast("Paste your applications, upload application files, or both.");
    return;
  }
  if (!imageFiles.length) {
    showToast("Please select at least one label photo.");
    return;
  }

  const formData = new FormData();
  if (text.trim()) formData.append("text", text);
  Array.from(appFiles).forEach((f) => formData.append("application_files", f));
  Array.from(imageFiles).forEach((f) => formData.append("images", f));

  quickBatchSubmit.disabled = true;
  quickBatchSubmit.innerHTML = '<span class="spinner"></span>Reviewing batch…';
  quickBatchResult.hidden = true;

  const started = performance.now();
  try {
    const res = await fetch("/api/review/batch-from-text", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");
    renderBatchResult(data, performance.now() - started, quickBatchResult);
  } catch (err) {
    showToast(`Error: ${err.message}`);
  } finally {
    quickBatchSubmit.disabled = false;
    quickBatchSubmit.textContent = "Review Batch";
  }
});

// ---- Batch review (existing manifest CSV) ----
const batchForm = document.getElementById("batch-form");
const batchResult = document.getElementById("batch-result");
const batchSubmit = document.getElementById("batch-submit");

batchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const manifestFile = document.getElementById("manifest-input").files[0];
  const imageFiles = document.getElementById("images-input").files;
  if (!manifestFile || !imageFiles.length) { showToast("Please select a manifest CSV and at least one image."); return; }

  const formData = new FormData();
  formData.append("manifest", manifestFile);
  Array.from(imageFiles).forEach((f) => formData.append("images", f));

  batchSubmit.disabled = true;
  batchSubmit.innerHTML = '<span class="spinner"></span>Reviewing batch…';
  batchResult.hidden = true;

  const started = performance.now();
  try {
    const res = await fetch("/api/review/batch", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");
    renderBatchResult(data, performance.now() - started);
  } catch (err) {
    showToast(`Error: ${err.message}`);
  } finally {
    batchSubmit.disabled = false;
    batchSubmit.textContent = "Review Batch";
  }
});

function renderBatchResult(data, clientMs, container = batchResult) {
  const s = data.summary;
  const summaryHtml = `
    <div class="summary-row">
      <div class="summary-chip total">${s.total} total</div>
      <div class="summary-chip pass">${s.pass} pass</div>
      <div class="summary-chip needs_review">${s.needs_review} needs review</div>
      <div class="summary-chip fail">${s.fail} fail</div>
      ${s.error ? `<div class="summary-chip error">${s.error} errored</div>` : ""}
      <div class="summary-chip total">${(clientMs / 1000).toFixed(1)}s total</div>
    </div>`;

  const unmatchedHtml = data.unmatched_uploaded_images.length
    ? `<div class="warnings-box"><strong>${data.parsed_applications ? "Photos uploaded but no matching application" : "Uploaded but not in manifest"}:</strong> ${data.unmatched_uploaded_images.map(escapeHtml).join(", ")}</div>`
    : "";

  const parsedHtml = data.parsed_applications ? `
    <details class="parsed-apps-box">
      <summary>We found ${data.parsed_applications.length} application${data.parsed_applications.length === 1 ? "" : "s"} in what you pasted/uploaded — click to double-check</summary>
      <table class="parsed-apps-table">
        <thead><tr><th>Match this to photo</th><th>Brand Name</th><th>Class / Type</th><th></th></tr></thead>
        <tbody>${data.parsed_applications.map((p) => `
          <tr>
            <td><code>${escapeHtml(p.id)}.*</code></td>
            <td>${p.brand_name ? escapeHtml(p.brand_name) : "—"}</td>
            <td>${p.class_type ? escapeHtml(p.class_type) : "—"}</td>
            <td>${p.error ? `<span class="status-pill mismatch">✗ ${escapeHtml(p.error)}</span>` : (p.warnings && p.warnings.length ? `<span class="status-pill needs_review">⚠ ${escapeHtml(p.warnings.join(" "))}</span>` : `<span class="status-pill match">✓ parsed</span>`)}</td>
          </tr>`).join("")}
        </tbody>
      </table>
    </details>` : "";

  const rowsHtml = data.results.map((r, i) => {
    const detailId = `${container.id}-detail-${i}`;
    if (r.error) {
      return `<tr class="error-row"><td>${escapeHtml(r.filename)}</td><td colspan="4">${escapeHtml(r.error)}</td></tr>`;
    }
    const fieldSummary = r.fields.map((f) => `${FIELD_LABELS[f.field] || f.field}: ${STATUS_ICON[f.status]}`).join("  ");
    return `
      <tr class="expandable" data-detail="${detailId}">
        <td>${escapeHtml(r.filename)}</td>
        <td><span class="status-pill ${r.overall_status === "pass" ? "match" : r.overall_status}">${OVERALL_ICON[r.overall_status]} ${r.overall_status.replace("_", " ")}</span></td>
        <td>${escapeHtml(fieldSummary)}</td>
        <td>${formatSeconds(r.processing_time_ms)}</td>
        <td>▾ details</td>
      </tr>
      <tr id="${detailId}" class="detail-row" hidden>
        <td colspan="5">
          <table class="field-table">
            <thead><tr><th>Field</th><th>Application Says</th><th>Label Shows</th><th>Result</th></tr></thead>
            <tbody>${r.fields.map(fieldRowHtml).join("")}</tbody>
          </table>
          ${r.warnings && r.warnings.length ? `<div class="warnings-box"><ul>${r.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul></div>` : ""}
        </td>
      </tr>`;
  }).join("");

  container.innerHTML = `
    ${summaryHtml}
    ${parsedHtml}
    ${unmatchedHtml}
    <table class="batch-table">
      <thead><tr><th>${data.parsed_applications ? "Application" : "Filename"}</th><th>Overall</th><th>Field Summary</th><th>Time</th><th></th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  container.hidden = false;

  container.querySelectorAll("tr.expandable").forEach((row) => {
    row.addEventListener("click", () => {
      const detail = document.getElementById(row.dataset.detail);
      detail.hidden = !detail.hidden;
    });
  });
  container.scrollIntoView({ behavior: "smooth", block: "start" });
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
