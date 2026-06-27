let currentJob = null;
let models = [];
let jobHistory = [];

const PAPER_REFERENCE_BASELINES = [
  {
    cohort: "Sweden hold-out",
    population: "N=113,072",
    outcome: "Sudden cardiac death",
    baseline: "0.6% annual SCD rate",
    highRisk: "Top 2.2% ECG-model group: 7.0% annual SCD rate",
    avlFeature: "1st diff coefficient -0.069***; 2nd diff -0.038**",
    note: "Raw aVL_rs_diff median/IQR not published.",
  },
  {
    cohort: "Taiwan external validation",
    population: "Case-control registry",
    outcome: "Arrhythmic cardiac arrest / SCD validation",
    baseline: "Naive case-control base rate 1.6%",
    highRisk: "Top ECG-model decile reported as almost 2x the Swedish top decile",
    avlFeature: "1st diff coefficient -0.099***; 2nd diff -0.082***",
    note: "Case-control design; raw aVL_rs_diff median/IQR not published.",
  },
  {
    cohort: "Taiwan cardiac-cause arrest vs control",
    population: "Control comparison in Supplement Table V.G.1",
    outcome: "Cardiac-cause arrest vs control",
    baseline: "No population baseline reported",
    highRisk: "Not a model-risk threshold comparison",
    avlFeature: "1st diff coefficient -0.161***; 2nd diff -0.132**",
    note: "With ECG controls: 1st diff -0.073; 2nd diff -0.077.",
  },
];

const apiStatus = document.querySelector("#apiStatus");
const uploadForm = document.querySelector("#uploadForm");
const predictForm = document.querySelector("#predictForm");
const biomarkerForm = document.querySelector("#biomarkerForm");
const refreshRuns = document.querySelector("#refreshRuns");
const runsList = document.querySelector("#runsList");
const openBiomarkerInterpretation = document.querySelector("#openBiomarkerInterpretation");
const closeBiomarkerInterpretation = document.querySelector("#closeBiomarkerInterpretation");
const biomarkerDialog = document.querySelector("#biomarkerDialog");
const biomarkerInterpretationBody = document.querySelector("#biomarkerInterpretationBody");
const modelSelect = document.querySelector("#modelSelect");
const recordsTable = document.querySelector("#recordsTable");
const previewSelect = document.querySelector("#previewSelect");
const caveatsList = document.querySelector("#caveatsList");
const biomarkerHead = document.querySelector("#biomarkerHead");
const biomarkerTable = document.querySelector("#biomarkerTable");
const biomarkerLabel = document.querySelector("#biomarkerLabel");
const predictionHead = document.querySelector("#predictionHead");
const predictionTable = document.querySelector("#predictionTable");
const predictionLabel = document.querySelector("#predictionLabel");
const jobLabel = document.querySelector("#jobLabel");
const canvas = document.querySelector("#ecgCanvas");
const ctx = canvas.getContext("2d");

async function init() {
  try {
    await fetchJson("/api/health");
    apiStatus.textContent = "Backend ready";
    apiStatus.style.borderColor = "#9ed7cd";
    apiStatus.style.color = "#0f766e";
  } catch {
    apiStatus.textContent = "Backend unavailable";
    return;
  }
  models = await fetchJson("/api/models");
  renderModels();
  await loadJobHistory();
  const jobId = new URLSearchParams(window.location.search).get("job");
  if (jobId) {
    try {
      currentJob = await fetchJson(`/api/jobs/${encodeURIComponent(jobId)}`);
      renderJob(currentJob);
      await loadPreview(currentJob.records[0]?.study_id);
      return;
    } catch (error) {
      console.warn(error);
    }
  }
  renderEmptyState();
}

function renderModels() {
  modelSelect.innerHTML = "";
  if (!models.length) {
    modelSelect.append(new Option("No registry found", ""));
    return;
  }
  for (const model of models) {
    const label = `${model.label}${model.ready ? "" : " (missing artifacts)"}`;
    const option = new Option(label, model.id);
    option.disabled = !model.ready;
    modelSelect.append(option);
  }
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = document.querySelector("#fileInput").files;
  if (!files.length) {
    alert("Choose at least one ECG file.");
    return;
  }

  const form = new FormData();
  for (const file of files) form.append("files", file);
  form.append("sampling_rate_hz", document.querySelector("#samplingRate").value || "500");
  form.append("lead_order", document.querySelector("#leadOrder").value || "");

  setBusy(uploadForm, true);
  try {
    currentJob = await fetchJson("/api/jobs", { method: "POST", body: form });
    renderJob(currentJob);
    await loadJobHistory();
    setJobUrl(currentJob.job_id);
    await loadPreview(currentJob.records[0]?.study_id);
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(uploadForm, false);
  }
});

predictForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentJob) {
    alert("Upload ECG files first.");
    return;
  }
  const selectedModel = modelSelect.value;
  if (!selectedModel) {
    alert("Select a ready model artifact.");
    return;
  }

  const form = new FormData();
  form.append("model_id", selectedModel);
  form.append("allow_missing_leads", document.querySelector("#allowMissing").checked ? "true" : "false");

  setBusy(predictForm, true);
  try {
    currentJob = await fetchJson(`/api/jobs/${currentJob.job_id}/predict`, { method: "POST", body: form });
    renderJob(currentJob);
    await loadJobHistory();
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(predictForm, false);
  }
});

biomarkerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentJob) {
    alert("Upload ECG files first.");
    return;
  }

  setBusy(biomarkerForm, true);
  try {
    currentJob = await fetchJson(`/api/jobs/${currentJob.job_id}/biomarkers`, { method: "POST" });
    renderJob(currentJob);
    await loadJobHistory();
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(biomarkerForm, false);
  }
});

refreshRuns.addEventListener("click", () => {
  loadJobHistory();
});

openBiomarkerInterpretation.addEventListener("click", () => {
  renderBiomarkerInterpretation();
  if (typeof biomarkerDialog.showModal === "function") biomarkerDialog.showModal();
  else biomarkerDialog.setAttribute("open", "open");
});

closeBiomarkerInterpretation.addEventListener("click", () => {
  biomarkerDialog.close();
});

biomarkerDialog.addEventListener("click", (event) => {
  if (event.target === biomarkerDialog) biomarkerDialog.close();
});

previewSelect.addEventListener("change", () => {
  if (previewSelect.value) loadPreview(previewSelect.value);
});

runsList.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-job-id]");
  if (!button) return;
  const jobId = button.dataset.jobId;
  try {
    currentJob = await fetchJson(`/api/jobs/${encodeURIComponent(jobId)}`);
    renderJob(currentJob);
    setJobUrl(jobId);
    await loadPreview(currentJob.records[0]?.study_id);
  } catch (error) {
    alert(error.message);
  }
});

function renderJob(job) {
  jobLabel.textContent = `Job ${job.job_id}`;
  recordsTable.innerHTML = "";
  previewSelect.innerHTML = "";

  const caveats = new Set();
  for (const record of job.records) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(record.study_id)}</td>
      <td>${record.present_leads.length}/12</td>
      <td>${escapeHtml(record.missing_leads.join(", ") || "None")}</td>
      <td>${escapeHtml(record.derived_leads.join(", ") || "None")}</td>
      <td>${record.seconds.toFixed(1)} s</td>
    `;
    recordsTable.append(row);
    previewSelect.append(new Option(record.study_id, record.study_id));
    record.caveats.forEach((item) => caveats.add(item));
  }

  for (const item of job.prediction_caveats || []) caveats.add(item);
  for (const item of job.biomarker_caveats || []) caveats.add(item);
  renderCaveats([...caveats]);
  renderBiomarkers(job.biomarkers || []);
  renderPredictions(job.predictions || []);
  highlightCurrentRun();
}

async function loadJobHistory() {
  try {
    jobHistory = await fetchJson("/api/jobs");
    renderJobHistory();
  } catch (error) {
    runsList.innerHTML = `<div class="muted-text">${escapeHtml(error.message)}</div>`;
  }
}

function renderJobHistory() {
  runsList.innerHTML = "";
  if (!jobHistory.length) {
    runsList.innerHTML = '<div class="muted-text">No previous runs.</div>';
    return;
  }
  for (const run of jobHistory) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "run-item";
    button.dataset.jobId = run.job_id;
    const badges = [
      run.has_biomarkers ? "biomarker" : null,
      run.has_predictions ? "prediction" : null,
    ].filter(Boolean);
    button.innerHTML = `
      <span class="run-title">${escapeHtml(run.filenames.join(", ") || run.job_id)}</span>
      <span class="run-meta">${escapeHtml(formatDate(run.created_at))} · ${run.record_count} record${run.record_count === 1 ? "" : "s"}</span>
      <span class="run-meta">${escapeHtml(run.job_id)}${badges.length ? ` · ${escapeHtml(badges.join(", "))}` : ""}</span>
    `;
    runsList.append(button);
  }
  highlightCurrentRun();
}

function highlightCurrentRun() {
  for (const button of runsList.querySelectorAll(".run-item")) {
    button.classList.toggle("active-run", currentJob?.job_id === button.dataset.jobId);
  }
}

function renderCaveats(items) {
  caveatsList.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = "No caveats reported.";
    caveatsList.append(li);
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    caveatsList.append(li);
  }
}

function renderPredictions(rows) {
  predictionHead.innerHTML = "";
  predictionTable.innerHTML = "";
  predictionLabel.textContent = rows.length ? `${rows.length} row${rows.length === 1 ? "" : "s"}` : "Not run";
  if (!rows.length) return;

  const columns = Object.keys(rows[0]);
  const headRow = document.createElement("tr");
  for (const column of columns) {
    const th = document.createElement("th");
    th.textContent = column;
    headRow.append(th);
  }
  predictionHead.append(headRow);

  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const column of columns) {
      const td = document.createElement("td");
      const value = row[column];
      td.textContent = typeof value === "number" ? value.toFixed(4) : value;
      tr.append(td);
    }
    predictionTable.append(tr);
  }
}

function renderBiomarkers(rows) {
  biomarkerHead.innerHTML = "";
  biomarkerTable.innerHTML = "";
  biomarkerLabel.textContent = rows.length ? `${rows.length} row${rows.length === 1 ? "" : "s"}` : "Not run";
  openBiomarkerInterpretation.disabled = !rows.length;
  if (!rows.length) return;

  const columns = ["studyId", "aVL_rs_diff", "aVL_rs_diff_2", "first_percentile", "second_percentile", "qrs_axis_estimate", "r_peaks_used", "biomarker_available"];
  const firstPercentiles = biomarkerPercentiles(rows, "aVL_rs_diff");
  const secondPercentiles = biomarkerPercentiles(rows, "aVL_rs_diff_2");
  const headRow = document.createElement("tr");
  for (const column of columns) {
    const th = document.createElement("th");
    th.textContent = column;
    headRow.append(th);
  }
  biomarkerHead.append(headRow);

  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const column of columns) {
      const td = document.createElement("td");
      let value = row[column];
      if (column === "first_percentile") value = firstPercentiles.get(row.studyId);
      if (column === "second_percentile") value = secondPercentiles.get(row.studyId);
      if (typeof value === "number") td.textContent = value.toFixed(5);
      else if (value === null || value === undefined) td.textContent = "";
      else td.textContent = value;
      tr.append(td);
    }
    biomarkerTable.append(tr);
  }
}

function renderBiomarkerInterpretation() {
  const rows = currentJob?.biomarkers || [];
  const availableRows = rows.filter((row) =>
    row.biomarker_available &&
    typeof row.aVL_rs_diff === "number" &&
    typeof row.aVL_rs_diff_2 === "number"
  );
  if (!rows.length) {
    biomarkerInterpretationBody.innerHTML = `
      <div class="empty-panel">Compute the aVL biomarker first, then return here for cohort-relative interpretation.</div>
    `;
    return;
  }

  const selectedStudyId = previewSelect.value || rows[0]?.studyId;
  const selected = rows.find((row) => row.studyId === selectedStudyId) || rows[0];
  const firstPercentiles = biomarkerPercentiles(rows, "aVL_rs_diff");
  const secondPercentiles = biomarkerPercentiles(rows, "aVL_rs_diff_2");
  const firstStats = summarizeValues(availableRows.map((row) => row.aVL_rs_diff));
  const secondStats = summarizeValues(availableRows.map((row) => row.aVL_rs_diff_2));
  const quality = biomarkerQualityForStudy(selected?.studyId);
  const firstPercentile = firstPercentiles.get(selected?.studyId);
  const secondPercentile = secondPercentiles.get(selected?.studyId);
  const readiness = biomarkerReadiness(rows, availableRows);

  biomarkerInterpretationBody.innerHTML = `
    <section class="interpretation-summary">
      <div class="metric-card">
        <span class="metric-label">Selected ECG</span>
        <strong>${escapeHtml(selected?.studyId || "No record")}</strong>
        <span>${escapeHtml(readiness.selectedLabel)}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">First Difference</span>
        <strong>${formatMetric(selected?.aVL_rs_diff, 5)}</strong>
        <span>${firstPercentile == null ? "No cohort position" : `${firstPercentile.toFixed(1)} uploaded-cohort percentile`}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Second Difference</span>
        <strong>${formatMetric(selected?.aVL_rs_diff_2, 5)}</strong>
        <span>${secondPercentile == null ? "No cohort position" : `${secondPercentile.toFixed(1)} uploaded-cohort percentile`}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Signal basis</span>
        <strong>${quality.nativeAvl ? "Native aVL" : quality.derivedAvl ? "Derived aVL" : "aVL unavailable"}</strong>
        <span>${selected?.r_peaks_used ?? 0} R-peaks used</span>
      </div>
    </section>

    <section>
      <h3>Workflow Checks</h3>
      ${renderWorkflowChecks(readiness, quality, selected)}
    </section>

    <section>
      <h3>Two-Feature Reference Map</h3>
      ${renderReferenceMapPanel(availableRows, selected?.studyId, firstStats, secondStats)}
    </section>

    <section>
      <h3>Waveform Validation</h3>
      ${renderWaveformValidationPanel(selected)}
    </section>

    <section>
      <h3>Paper Reference Baselines</h3>
      <p class="reference-note">The paper does not publish raw <code>aVL_rs_diff</code> distribution cutoffs for the Swedish or Taiwanese cohorts. These rows provide published cohort/model context and the aVL feature-association coefficients from the supplementary analysis, so the MVP output can be sanity-checked without implying a clinical threshold.</p>
      ${renderPaperReferenceTable(firstStats, secondStats, availableRows.length)}
    </section>
  `;
}

function biomarkerReadiness(rows, availableRows) {
  const unavailable = rows.length - availableRows.length;
  const derivedCount = currentJob?.records?.filter((record) => record.derived_leads?.includes("aVL")).length || 0;
  return {
    availableCount: availableRows.length,
    totalCount: rows.length,
    unavailableCount: unavailable,
    derivedCount,
    selectedLabel: availableRows.length ? "Feature extraction available" : "Feature extraction unavailable",
  };
}

function renderWorkflowChecks(readiness, quality, selected) {
  const checks = [
    {
      label: "Input normalization",
      value: `${readiness.totalCount} ECG${readiness.totalCount === 1 ? "" : "s"} uploaded`,
      detail: "Stored as 12-lead, 500 Hz, 10-second arrays before biomarker extraction.",
      status: readiness.totalCount > 0 ? "ok" : "warn",
    },
    {
      label: "aVL source",
      value: quality.nativeAvl ? "Native aVL" : quality.derivedAvl ? "Derived aVL" : "Unavailable",
      detail: quality.derivedAvl
        ? "Derived aVL can support workflow testing, but native aVL is preferred for reproduction."
        : "Native aVL provides the closest match to the paper feature path.",
      status: quality.nativeAvl ? "ok" : quality.derivedAvl ? "warn" : "fail",
    },
    {
      label: "Feature extraction",
      value: `${readiness.availableCount}/${readiness.totalCount} available`,
      detail: readiness.unavailableCount
        ? `${readiness.unavailableCount} record${readiness.unavailableCount === 1 ? "" : "s"} need per-record caveat review.`
        : "Both aVL derivative features were produced for every uploaded ECG.",
      status: readiness.unavailableCount ? "warn" : "ok",
    },
    {
      label: "Method trace",
      value: selected?.debug?.detectionStatus || "not recorded",
      detail: selected?.debug
        ? `R-to-QRS-end interval ${selected.debug.intervalDurationMs ?? "unknown"} ms, version ${selected.debug.methodVersion}.`
        : "No waveform trace is available for this record.",
      status: selected?.debug?.detectionStatus === "ok" ? "ok" : "warn",
    },
  ];

  return `
    <div class="workflow-checks">
      ${checks.map((check) => `
        <article class="workflow-card ${check.status}">
          <span class="workflow-status" aria-hidden="true"></span>
          <div>
            <span class="metric-label">${escapeHtml(check.label)}</span>
            <strong>${escapeHtml(check.value)}</strong>
            <p>${escapeHtml(check.detail)}</p>
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

function renderReferenceMapPanel(rows, selectedStudyId, firstStats, secondStats) {
  if (!rows.length) return '<div class="empty-panel">No ECGs have both aVL derivative features available.</div>';
  return `
    <div class="reference-map-layout">
      <div class="reference-map-main">
        ${renderBivariateMapSvg(rows, selectedStudyId, firstStats, secondStats)}
      </div>
      <div class="reference-map-side">
        ${renderMarginalStrip(rows, selectedStudyId, "aVL_rs_diff", "First-difference feature")}
        ${renderMarginalStrip(rows, selectedStudyId, "aVL_rs_diff_2", "Second-difference feature")}
        <div class="distribution-caption">
          Points and bands come from the uploaded run. Paper cohort shading is not drawn unless raw or summarized feature ranges are available.
        </div>
      </div>
    </div>
  `;
}

function renderBivariateMapSvg(rows, selectedStudyId, firstStats, secondStats) {
  const width = 820;
  const height = 520;
  const pad = { left: 72, right: 32, top: 34, bottom: 66 };
  const xValues = rows.map((row) => row.aVL_rs_diff);
  const yValues = rows.map((row) => row.aVL_rs_diff_2);
  const xDomain = valueDomain(xValues);
  const yDomain = valueDomain(yValues);
  const xScale = (value) => pad.left + ((value - xDomain.min) / xDomain.span) * (width - pad.left - pad.right);
  const yScale = (value) => height - pad.bottom - ((value - yDomain.min) / yDomain.span) * (height - pad.top - pad.bottom);
  const selected = rows.find((row) => row.studyId === selectedStudyId);
  const medianX = firstStats.median == null ? null : xScale(firstStats.median);
  const medianY = secondStats.median == null ? null : yScale(secondStats.median);
  const hasIqr = firstStats.q1 != null && firstStats.q3 != null && secondStats.q1 != null && secondStats.q3 != null && rows.length >= 4;
  const iqr = hasIqr
    ? {
        x: xScale(firstStats.q1),
        y: yScale(secondStats.q3),
        width: Math.max(1, xScale(firstStats.q3) - xScale(firstStats.q1)),
        height: Math.max(1, yScale(secondStats.q1) - yScale(secondStats.q3)),
      }
    : null;

  return `
    <svg class="reference-map" viewBox="0 0 ${width} ${height}" role="img" aria-label="Two-feature aVL biomarker map">
      <rect x="${pad.left}" y="${pad.top}" width="${width - pad.left - pad.right}" height="${height - pad.top - pad.bottom}" class="map-plot-bg"></rect>
      ${iqr ? `<rect x="${iqr.x.toFixed(2)}" y="${iqr.y.toFixed(2)}" width="${iqr.width.toFixed(2)}" height="${iqr.height.toFixed(2)}" class="map-iqr"></rect>` : ""}
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis-line"></line>
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="axis-line"></line>
      ${medianX == null ? "" : `<line x1="${medianX.toFixed(2)}" y1="${pad.top}" x2="${medianX.toFixed(2)}" y2="${height - pad.bottom}" class="map-median"></line>`}
      ${medianY == null ? "" : `<line x1="${pad.left}" y1="${medianY.toFixed(2)}" x2="${width - pad.right}" y2="${medianY.toFixed(2)}" class="map-median"></line>`}
      ${rows.map((row) => {
        const selectedClass = row.studyId === selectedStudyId ? " selected-map-point" : "";
        return `
          <circle
            cx="${xScale(row.aVL_rs_diff).toFixed(2)}"
            cy="${yScale(row.aVL_rs_diff_2).toFixed(2)}"
            r="${row.studyId === selectedStudyId ? 7 : 4}"
            class="map-point${selectedClass}"
          >
            <title>${escapeHtml(row.studyId)}: ${formatMetric(row.aVL_rs_diff, 5)}, ${formatMetric(row.aVL_rs_diff_2, 5)}</title>
          </circle>
        `;
      }).join("")}
      ${selected ? `
        <line x1="${xScale(selected.aVL_rs_diff).toFixed(2)}" y1="${pad.top}" x2="${xScale(selected.aVL_rs_diff).toFixed(2)}" y2="${height - pad.bottom}" class="selected-guide"></line>
        <line x1="${pad.left}" y1="${yScale(selected.aVL_rs_diff_2).toFixed(2)}" x2="${width - pad.right}" y2="${yScale(selected.aVL_rs_diff_2).toFixed(2)}" class="selected-guide"></line>
      ` : ""}
      <text x="${pad.left}" y="${height - 22}" class="axis-label">aVL_rs_diff: mean absolute first difference from R peak to QRS end</text>
      <text x="18" y="${height - pad.bottom}" class="axis-label axis-label-vertical" transform="rotate(-90 18 ${height - pad.bottom})">aVL_rs_diff_2: mean absolute second difference</text>
      <text x="${pad.left}" y="${height - 44}" class="axis-tick">${formatMetric(xDomain.min, 5)}</text>
      <text x="${width - pad.right - 80}" y="${height - 44}" class="axis-tick">${formatMetric(xDomain.max, 5)}</text>
      <text x="${pad.left - 58}" y="${height - pad.bottom + 4}" class="axis-tick">${formatMetric(yDomain.min, 5)}</text>
      <text x="${pad.left - 58}" y="${pad.top + 4}" class="axis-tick">${formatMetric(yDomain.max, 5)}</text>
    </svg>
  `;
}

function renderMarginalStrip(rows, selectedStudyId, field, label) {
  const values = rows.map((row) => row[field]).filter((value) => Number.isFinite(value));
  if (!values.length) return "";
  const stats = summarizeValues(values);
  const domain = valueDomain(values);
  const selected = rows.find((row) => row.studyId === selectedStudyId);
  const position = (value) => ((value - domain.min) / domain.span) * 100;
  const q1 = stats.q1 == null ? 0 : position(stats.q1);
  const q3 = stats.q3 == null ? 100 : position(stats.q3);
  const median = stats.median == null ? null : position(stats.median);
  const selectedPosition = typeof selected?.[field] === "number" ? position(selected[field]) : null;

  return `
    <div class="marginal-card">
      <div class="marginal-head">
        <span class="metric-label">${escapeHtml(label)}</span>
        <strong>${formatMetric(selected?.[field], 5)}</strong>
      </div>
      <div class="marginal-strip">
        <span class="marginal-iqr" style="left: ${q1}%; width: ${Math.max(1, q3 - q1)}%;"></span>
        ${median == null ? "" : `<span class="marginal-median" style="left: ${median}%;"></span>`}
        ${selectedPosition == null ? "" : `<span class="marginal-selected" style="left: ${selectedPosition}%;"></span>`}
      </div>
      <div class="distribution-labels">
        <span>${formatMetric(domain.min, 5)}</span>
        <span>${formatMetric(domain.max, 5)}</span>
      </div>
    </div>
  `;
}

function renderWaveformValidationPanel(selected) {
  const debug = selected?.debug;
  if (!debug || !Array.isArray(debug.normalizedAvl) || debug.detectionStatus !== "ok") {
    return '<div class="empty-panel">No complete waveform debug trace is available for the selected ECG.</div>';
  }
  return `
    <div class="waveform-layout">
      <div>
        <div class="panel-subhead">
          <span class="metric-label">Median aVL Beat</span>
          <span>R ${debug.rPeakIndex}, QRS end ${debug.sEndIndex}</span>
        </div>
        ${renderWaveformSvg(debug)}
      </div>
      <div class="waveform-stack">
        ${renderCumulativeDiffSvg(debug, "cumulativeFirstDiff", "Cumulative first difference")}
        ${renderCumulativeDiffSvg(debug, "cumulativeSecondDiff", "Cumulative second difference")}
      </div>
    </div>
  `;
}

function renderWaveformSvg(debug) {
  const values = debug.normalizedAvl;
  const width = 760;
  const height = 240;
  const pad = { left: 34, right: 20, top: 18, bottom: 34 };
  const xScale = (index) => pad.left + (index / Math.max(1, values.length - 1)) * (width - pad.left - pad.right);
  const yScale = (value) => height - pad.bottom - clamp(value, 0, 1) * (height - pad.top - pad.bottom);
  const points = values.map((value, index) => `${xScale(index).toFixed(2)},${yScale(value).toFixed(2)}`).join(" ");
  const startX = xScale(debug.intervalStartIndex);
  const endX = xScale(debug.intervalEndIndex);
  const rX = xScale(debug.rPeakIndex);
  const sX = xScale(debug.sEndIndex);

  return `
    <svg class="waveform-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Normalized median aVL beat">
      <rect x="${startX.toFixed(2)}" y="${pad.top}" width="${Math.max(1, endX - startX).toFixed(2)}" height="${height - pad.top - pad.bottom}" class="interval-shade"></rect>
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis-line"></line>
      <polyline points="${points}" class="waveform-line"></polyline>
      <line x1="${rX.toFixed(2)}" y1="${pad.top}" x2="${rX.toFixed(2)}" y2="${height - pad.bottom}" class="r-marker"></line>
      <line x1="${sX.toFixed(2)}" y1="${pad.top}" x2="${sX.toFixed(2)}" y2="${height - pad.bottom}" class="s-marker"></line>
      <text x="${rX + 5}" y="${pad.top + 14}" class="axis-tick">R</text>
      <text x="${sX + 5}" y="${pad.top + 30}" class="axis-tick">QRS end</text>
      <text x="${pad.left}" y="${height - 10}" class="axis-tick">normalized aVL median beat</text>
    </svg>
  `;
}

function renderCumulativeDiffSvg(debug, field, label) {
  const values = debug[field] || [];
  if (!values.length) return '<div class="empty-panel">No cumulative trace available.</div>';
  const width = 360;
  const height = 140;
  const pad = { left: 34, right: 18, top: 18, bottom: 30 };
  const max = Math.max(...values, 1e-6);
  const xScale = (index) => pad.left + (index / Math.max(1, values.length - 1)) * (width - pad.left - pad.right);
  const yScale = (value) => height - pad.bottom - (value / max) * (height - pad.top - pad.bottom);
  const points = values.map((value, index) => `${xScale(index).toFixed(2)},${yScale(value).toFixed(2)}`).join(" ");
  const finalValue = values[values.length - 1];
  return `
    <div>
      <div class="panel-subhead tight">
        <span class="metric-label">${escapeHtml(label)}</span>
        <span>${formatMetric(finalValue, 5)}</span>
      </div>
      <svg class="diff-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(label)}">
        <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis-line"></line>
        <polyline points="${points}" class="diff-line"></polyline>
        <circle cx="${xScale(values.length - 1).toFixed(2)}" cy="${yScale(finalValue).toFixed(2)}" r="4" class="selected-svg-dot"></circle>
      </svg>
    </div>
  `;
}

function biomarkerPercentiles(rows, field) {
  const values = rows
    .filter((row) => typeof row[field] === "number" && Number.isFinite(row[field]))
    .map((row) => ({ studyId: row.studyId, value: row[field] }))
    .sort((a, b) => a.value - b.value);
  const out = new Map();
  if (!values.length) return out;
  if (values.length === 1) {
    out.set(values[0].studyId, 50);
    return out;
  }
  values.forEach((item, index) => {
    out.set(item.studyId, (index / (values.length - 1)) * 100);
  });
  return out;
}

function summarizeValues(values) {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  return {
    q1: quantile(sorted, 0.25),
    median: quantile(sorted, 0.5),
    q3: quantile(sorted, 0.75),
  };
}

function quantile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  if (sorted[base + 1] === undefined) return sorted[base];
  return sorted[base] + rest * (sorted[base + 1] - sorted[base]);
}

function biomarkerQualityForStudy(studyId) {
  const record = currentJob?.records?.find((item) => item.study_id === studyId);
  return {
    nativeAvl: Boolean(record?.present_leads?.includes("aVL") && !record?.derived_leads?.includes("aVL")),
    derivedAvl: Boolean(record?.derived_leads?.includes("aVL")),
  };
}

function renderPaperReferenceTable(firstStats, secondStats, availableCount) {
  return `
    <div class="reference-layout">
      <div class="reference-user-card">
        <h4>This run</h4>
        <dl>
          <div><dt>Available ECGs</dt><dd>${availableCount}</dd></div>
          <div><dt>First-diff median</dt><dd>${formatMetric(firstStats.median, 5)}</dd></div>
          <div><dt>First-diff IQR</dt><dd>${formatMetric(firstStats.q1, 5)} to ${formatMetric(firstStats.q3, 5)}</dd></div>
          <div><dt>Second-diff median</dt><dd>${formatMetric(secondStats.median, 5)}</dd></div>
          <div><dt>Second-diff IQR</dt><dd>${formatMetric(secondStats.q1, 5)} to ${formatMetric(secondStats.q3, 5)}</dd></div>
          <div><dt>Reference interval</dt><dd>Uploaded-cohort only</dd></div>
        </dl>
      </div>
      <div class="table-wrap">
        <table class="reference-table">
          <thead>
            <tr>
              <th>Paper cohort</th>
              <th>Outcome context</th>
              <th>Published baseline</th>
              <th>aVL feature association</th>
            </tr>
          </thead>
          <tbody>
            ${PAPER_REFERENCE_BASELINES.map((row) => `
              <tr>
                <td><strong>${escapeHtml(row.cohort)}</strong><br><span class="muted-text">${escapeHtml(row.population)}</span></td>
                <td>${escapeHtml(row.outcome)}</td>
                <td>${escapeHtml(row.baseline)}<br><span class="muted-text">${escapeHtml(row.highRisk)}</span></td>
                <td>${escapeHtml(row.avlFeature)}<br><span class="muted-text">${escapeHtml(row.note)}</span></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
    <div class="reference-footnote">
      Coefficients are from Supplementary Table V.G.1, regression of SCD-related outcomes on mean absolute first and second differences from R-peak to QRS-end in lead aVL. Stars denote paper-reported significance levels. They are not interchangeable with the raw feature value shown for this run.
    </div>
  `;
}

function valueDomain(values) {
  const finiteValues = values.filter((value) => Number.isFinite(value));
  const min = Math.min(...finiteValues);
  const max = Math.max(...finiteValues);
  const span = max - min;
  if (!Number.isFinite(span) || span === 0) {
    const base = Math.max(Math.abs(min), Math.abs(max), 1);
    return { min: min - base * 0.1, max: max + base * 0.1, span: base * 0.2 };
  }
  return { min: min - span * 0.08, max: max + span * 0.08, span: span * 1.16 };
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function formatMetric(value, digits) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Unavailable";
  return value.toFixed(digits);
}

async function loadPreview(studyId) {
  if (!currentJob || !studyId) {
    drawBlank();
    return;
  }
  const payload = await fetchJson(`/api/jobs/${currentJob.job_id}/records/${encodeURIComponent(studyId)}/preview`);
  drawEcg(payload);
}

function drawEcg(payload) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#fbfcfc";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#e7ecef";
  ctx.lineWidth = 1;
  for (let x = 0; x < canvas.width; x += 40) drawLine(x, 0, x, canvas.height);
  for (let y = 0; y < canvas.height; y += 32) drawLine(0, y, canvas.width, y);

  const leads = Object.keys(payload.leads);
  const rows = 6;
  const cols = 2;
  const cellW = canvas.width / cols;
  const cellH = canvas.height / rows;
  const colors = ["#0f766e", "#234b6d", "#8b3a62", "#6f5b13"];

  leads.forEach((lead, index) => {
    const col = index >= rows ? 1 : 0;
    const row = index % rows;
    const left = col * cellW + 42;
    const top = row * cellH + 12;
    const values = payload.leads[lead];
    const maxAbs = Math.max(0.2, ...values.map((value) => Math.abs(value)));
    ctx.fillStyle = "#34414a";
    ctx.font = "13px sans-serif";
    ctx.fillText(lead, col * cellW + 12, top + 18);
    ctx.strokeStyle = colors[index % colors.length];
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    values.forEach((value, pointIndex) => {
      const x = left + (pointIndex / Math.max(1, values.length - 1)) * (cellW - 58);
      const y = top + cellH / 2 - (value / maxAbs) * (cellH * 0.36);
      if (pointIndex === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function drawBlank() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#68737d";
  ctx.font = "16px sans-serif";
  ctx.fillText("Upload an ECG to preview waveform traces.", 28, 48);
}

function drawLine(x1, y1, x2, y2) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}

function renderEmptyState() {
  recordsTable.innerHTML = '<tr><td colspan="5">No ECG records uploaded.</td></tr>';
  previewSelect.innerHTML = "";
  renderCaveats(["This exploratory tool is not cleared or validated for clinical decision-making."]);
  renderBiomarkers([]);
  renderPredictions([]);
  drawBlank();
  highlightCurrentRun();
}

function setJobUrl(jobId) {
  const url = new URL(window.location.href);
  url.searchParams.set("job", jobId);
  window.history.replaceState({}, "", url);
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "Unknown time";
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // Keep default message.
    }
    throw new Error(message);
  }
  return response.json();
}

function setBusy(form, busy) {
  for (const element of form.querySelectorAll("button, input, select")) {
    element.disabled = busy;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

init();
