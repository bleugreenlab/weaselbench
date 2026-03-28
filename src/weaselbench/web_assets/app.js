const state = {
  index: null,
  selectedTaskId: null,
  selectedRuns: {},
  panelTabs: {},
  selectedPath: null,
  pathQuery: "",
  search: "",
  statusFilter: "all",
  artifactCache: new Map(),
  artifactLoads: new Map(),
};

const reportTitle = document.querySelector("#report-title");
const reportSubtitle = document.querySelector("#report-subtitle");
const reportMeta = document.querySelector("#report-meta");
const modelStrip = document.querySelector("#model-strip");
const taskMatrix = document.querySelector("#task-matrix");
const taskDetail = document.querySelector("#task-detail");
const taskSearch = document.querySelector("#task-search");
const statusFilter = document.querySelector("#status-filter");
const taskCount = document.querySelector("#task-count");

document.addEventListener("DOMContentLoaded", () => {
  taskSearch.addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    render();
  });
  statusFilter.addEventListener("change", (event) => {
    state.statusFilter = event.target.value;
    render();
  });

  taskMatrix.addEventListener("click", (event) => {
    const row = event.target.closest("[data-task-id]");
    if (!row) {
      return;
    }
    selectTask(row.dataset.taskId);
  });

  taskDetail.addEventListener("change", (event) => {
    const selector = event.target.closest("[data-model-key][data-run-selector]");
    if (!selector) {
      const pathSelector = event.target.closest("[data-path-selector]");
      if (!pathSelector) {
        return;
      }
      state.selectedPath = pathSelector.value || null;
      renderTaskDetail();
      return;
    }
    state.selectedRuns[selector.dataset.modelKey] = selector.value;
    renderTaskDetail();
  });

  taskDetail.addEventListener("input", (event) => {
    const pathFilter = event.target.closest("[data-path-filter]");
    if (!pathFilter) {
      return;
    }
    state.pathQuery = pathFilter.value.trim().toLowerCase();
    renderTaskDetail({
      focusPathFilter: true,
      pathFilterSelectionStart: pathFilter.selectionStart,
      pathFilterSelectionEnd: pathFilter.selectionEnd,
    });
  });

  taskDetail.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab][data-model-key]");
    if (!tab) {
      return;
    }
    state.panelTabs[tab.dataset.modelKey] = tab.dataset.tab;
    renderTaskDetail();
  });

  loadIndex().catch((error) => {
    reportTitle.textContent = "Failed to load report index";
    reportSubtitle.textContent = error.message;
    taskDetail.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`;
  });
});

async function loadIndex() {
  const response = await fetch("report-index.json");
  if (!response.ok) {
    throw new Error(`Failed to load report-index.json (${response.status})`);
  }
  state.index = await response.json();
  state.selectedTaskId = parseHashTaskId() || state.index.tasks[0]?.task_id || null;
  render();
}

function render() {
  if (!state.index) {
    return;
  }
  renderHeader();
  renderModelStrip();
  renderTaskMatrix();

  const visibleTasks = getVisibleTasks();
  if (!visibleTasks.length) {
    taskDetail.innerHTML = '<div class="notice">No tasks match the current filter.</div>';
    return;
  }
  if (!visibleTasks.some((task) => task.task_id === state.selectedTaskId)) {
    selectTask(visibleTasks[0].task_id, { rerender: false });
  }
  renderTaskDetail();
}

function renderHeader() {
  const { evaluation } = state.index;
  reportTitle.textContent = `${evaluation.benchmark_name} · ${evaluation.evaluation_id}`;
  reportSubtitle.textContent = `${evaluation.task_set} | ${evaluation.task_count} tasks | ${evaluation.attempts} attempt${evaluation.attempts === 1 ? "" : "s"} per task`;
  reportMeta.innerHTML = [
    renderMetaCard("Benchmark", `${evaluation.benchmark_id} (${evaluation.benchmark_status})`),
    renderMetaCard("Manifest", evaluation.manifest_fingerprint.slice(0, 12)),
    renderMetaCard("Runtime", evaluation.runtime_image || evaluation.runtime),
    renderMetaCard(
      "Public Validity",
      evaluation.valid_for_public_leaderboard ? "valid" : "invalid",
    ),
  ].join("");
}

function renderModelStrip() {
  modelStrip.innerHTML = state.index.models
    .map(
      (item, index) => `
        <article class="model-card">
          <h3>#${index + 1} ${escapeHtml(item.model_key)}</h3>
          <div class="model-metrics">
            ${renderMetric("Pass@1", formatDecimal(item.task_pass_rate_at_1))}
            ${renderMetric("Mean", formatDecimal(item.mean_total_score))}
            ${renderMetric("Partial", formatDecimal(item.partial_rate))}
            ${renderMetric("Infra", formatDecimal(item.infra_error_rate))}
          </div>
          ${
            item.invalid_reasons.length
              ? `<p class="muted">${escapeHtml(item.invalid_reasons[0])}</p>`
              : ""
          }
        </article>
      `,
    )
    .join("");
}

function renderTaskMatrix() {
  const visibleTasks = getVisibleTasks();
  taskCount.textContent = `${visibleTasks.length} of ${state.index.tasks.length} task${state.index.tasks.length === 1 ? "" : "s"} shown`;
  const headers = state.index.models
    .map((item) => `<th>${escapeHtml(item.model_key)}</th>`)
    .join("");

  taskMatrix.innerHTML = `
    <table class="matrix-table">
      <thead>
        <tr>
          <th>Task</th>
          ${headers}
        </tr>
      </thead>
      <tbody>
        ${
          visibleTasks
            .map((task) => renderTaskRow(task, state.selectedTaskId === task.task_id))
            .join("")
        }
      </tbody>
    </table>
  `;
}

function renderTaskRow(task, isSelected) {
  const cells = state.index.models
    .map((model) => renderMatrixCell(getCanonicalRun(task, model.model_key)))
    .join("");
  return `
    <tr class="task-row ${isSelected ? "is-selected" : ""}" data-task-id="${escapeAttr(task.task_id)}">
      <td class="task-primary">
        <span class="task-title">${escapeHtml(task.title || task.task_id)}</span>
        <span class="task-id">${escapeHtml(task.task_id)}</span>
        ${
          task.summary
            ? `<p class="task-blurb">${escapeHtml(task.summary)}</p>`
            : ""
        }
        <div class="task-meta">
          ${task.labels?.task_family ? `<span class="pill">${escapeHtml(task.labels.task_family)}</span>` : ""}
          ${
            task.workflow
              ? `<span class="pill">${escapeHtml(task.workflow)}</span>`
              : ""
          }
        </div>
      </td>
      ${cells}
    </tr>
  `;
}

function renderMatrixCell(run) {
  if (!run) {
    return '<td class="matrix-cell"><span class="muted">No run</span></td>';
  }
  return `
    <td class="matrix-cell">
      <div class="cell-card">
        <div class="cell-head">
          <span class="status-chip ${escapeAttr(run.verdict)}">${escapeHtml(run.verdict)}</span>
          <span class="cell-score">${formatDecimal(run.total)}</span>
        </div>
        <span class="muted">${formatSeconds(run.wall_clock_seconds)} · ${run.changed_files} files</span>
      </div>
    </td>
  `;
}

function renderTaskDetail({
  focusPathFilter = false,
  pathFilterSelectionStart = null,
  pathFilterSelectionEnd = null,
} = {}) {
  const task = getSelectedTask();
  if (!task) {
    taskDetail.innerHTML = '<div class="notice">Select a task to compare runs.</div>';
    return;
  }

  syncRunSelections(task);
  const selectedRuns = getSelectedRuns(task);
  const unionPaths = getUnionPaths(selectedRuns);
  const visiblePaths = getVisiblePaths(unionPaths);
  if (!state.selectedPath || !unionPaths.includes(state.selectedPath)) {
    state.selectedPath = visiblePaths[0] || unionPaths[0] || null;
  } else if (visiblePaths.length && !visiblePaths.includes(state.selectedPath)) {
    state.selectedPath = visiblePaths[0];
  }

  taskDetail.classList.remove("empty-state");
  taskDetail.innerHTML = `
    <div class="task-header">
      <div class="task-header-top">
        <div>
          <h2>${escapeHtml(task.title || task.task_id)}</h2>
          <p class="task-summary">${escapeHtml(task.summary || "No task summary captured.")}</p>
        </div>
        <div class="task-actions">
          <span class="pill">${escapeHtml(task.task_id)}</span>
          ${
            task.labels?.task_family
              ? `<span class="pill">${escapeHtml(task.labels.task_family)}</span>`
              : ""
          }
          ${
            task.workflow
              ? `<span class="pill">${escapeHtml(task.workflow)}</span>`
              : ""
          }
        </div>
      </div>
      ${renderTaskSpec(task)}
      ${renderChangedSurfaceControls(unionPaths, visiblePaths, selectedRuns)}
    </div>
    <div class="panel-grid">
      ${selectedRuns.map((run) => renderRunPanel(task, run)).join("")}
    </div>
  `;

  if (focusPathFilter) {
    requestAnimationFrame(() => {
      const nextPathFilter = taskDetail.querySelector("[data-path-filter]");
      if (!nextPathFilter) {
        return;
      }
      nextPathFilter.focus();
      if (pathFilterSelectionStart == null || pathFilterSelectionEnd == null) {
        return;
      }
      nextPathFilter.setSelectionRange(
        pathFilterSelectionStart,
        pathFilterSelectionEnd,
      );
    });
  }
}

function renderTaskSpec(task) {
  const criteria = task.acceptance_criteria?.length
    ? `<ul class="criteria-list">${task.acceptance_criteria
        .map((item) => `<li>${escapeHtml(item)}</li>`)
        .join("")}</ul>`
    : '<p class="muted">No acceptance criteria captured.</p>';
  return `
    <section class="task-spec">
      <strong>Acceptance criteria</strong>
      ${criteria}
      <details>
        <summary>Prompt and scoring context</summary>
        ${
          task.prompt
            ? `<pre class="pre">${escapeHtml(task.prompt)}</pre>`
            : '<p class="muted">Prompt not captured for this task.</p>'
        }
        ${
          task.scoring?.axes?.length
            ? `<div class="axis-list">
                ${task.scoring.axes
                  .map(
                    (axis) => `
                      <div class="axis-row">
                        <span>${escapeHtml(axis.name)}</span>
                        <span>${formatDecimal(axis.weight)}</span>
                      </div>
                    `,
                  )
                  .join("")}
              </div>`
            : ""
        }
      </details>
    </section>
  `;
}

function renderChangedSurfaceControls(unionPaths, visiblePaths, selectedRuns) {
  if (!unionPaths.length) {
    return '<section class="changed-surface"><strong>Changed surface</strong><p class="muted">No changed files recorded for the selected runs.</p></section>';
  }
  return `
    <section class="changed-surface">
      <div class="changed-surface-top">
        <div>
          <strong>Changed surface</strong>
          <p class="muted">${unionPaths.length} unique file paths across the selected runs.</p>
        </div>
        <div class="task-actions">
          ${selectedRuns
            .map(
              (run) => `<span class="pill">${escapeHtml(run.model_key)}: ${run.changed_files}</span>`,
            )
            .join("")}
        </div>
      </div>
      <div class="file-focus-grid">
        <label class="field">
          <span>Filter paths</span>
          <input
            type="search"
            value="${escapeAttr(state.pathQuery)}"
            placeholder="cloud-monitoring, package.json, snapshot"
            data-path-filter="true"
          >
        </label>
        <label class="field">
          <span>Focus file</span>
          <select data-path-selector="true">
            ${
              visiblePaths.length
                ? visiblePaths
                    .map(
                      (path) => `
                        <option value="${escapeAttr(path)}" ${path === state.selectedPath ? "selected" : ""}>
                          ${escapeHtml(path)}
                        </option>
                      `,
                    )
                    .join("")
                : '<option value="">No paths match the current filter</option>'
            }
          </select>
        </label>
      </div>
      <p class="muted">Showing ${visiblePaths.length} of ${unionPaths.length} paths.</p>
    </section>
  `;
}

function renderRunPanel(task, run) {
  const modelRuns = getRunsForModel(task, run.model_key);
  const selectedId = getRunId(run);
  const activeTab = state.panelTabs[run.model_key] || "overview";
  const tabContent = renderRunTabContent(run, activeTab);
  return `
    <article class="run-panel">
      <div class="run-panel-header">
        <div>
          <h3>${escapeHtml(run.model_key)}</h3>
          <span class="status-chip ${escapeAttr(run.verdict)}">${escapeHtml(run.verdict)}</span>
        </div>
        <label class="field">
          <span>Run selection</span>
          <select data-model-key="${escapeAttr(run.model_key)}" data-run-selector="true">
            ${modelRuns
              .map(
                (item) => `
                  <option value="${escapeAttr(getRunId(item))}" ${getRunId(item) === selectedId ? "selected" : ""}>
                    ${escapeHtml(formatRunLabel(item))}
                  </option>
                `,
              )
              .join("")}
          </select>
        </label>
        <div class="tab-row">
          ${["overview", "checks", "transcript", "final-state"]
            .map(
              (tab) => `
                <button
                  class="tab ${activeTab === tab ? "is-active" : ""}"
                  type="button"
                  data-tab="${tab}"
                  data-model-key="${escapeAttr(run.model_key)}"
                >
                  ${escapeHtml(formatTabLabel(tab))}
                </button>
              `,
            )
            .join("")}
        </div>
      </div>
      ${tabContent}
    </article>
  `;
}

function renderRunTabContent(run, tab) {
  if (tab === "overview") {
    return renderOverviewTab(run);
  }

  const artifact = state.artifactCache.get(run.artifact_url);
  const loadError = artifact?.__error;
  if (!artifact && !loadError) {
    requestArtifact(run);
    return '<div class="notice">Loading artifact data...</div>';
  }
  if (loadError) {
    return `<div class="notice error">${escapeHtml(loadError)}</div>`;
  }

  if (tab === "checks") {
    return renderChecksTab(artifact);
  }
  if (tab === "transcript") {
    return renderTranscriptTab(artifact);
  }
  return renderFinalStateTab(run, artifact);
}

function renderOverviewTab(run) {
  const infraNote = run.infra_failure
    ? `<div class="notice">Infra failure: ${escapeHtml(run.infra_failure)}</div>`
    : "";
  return `
    ${infraNote}
    <div class="metrics-grid">
      ${renderMetricCard("Score", formatDecimal(run.total))}
      ${renderMetricCard("Wall clock", formatSeconds(run.wall_clock_seconds))}
      ${renderMetricCard("Changed files", String(run.changed_files))}
      ${renderMetricCard("Tool calls", String(run.tool_usage_entries))}
      ${renderMetricCard("Transcript", String(run.transcript_entries))}
      ${renderMetricCard("Termination", run.termination_reason || "unknown")}
    </div>
    <div class="axis-list">
      ${Object.entries(run.axis_scores)
        .map(
          ([name, value]) => `
            <div class="axis-row">
              <span>${escapeHtml(name)}</span>
              <span>${formatDecimal(value)}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderChecksTab(artifact) {
  const visible = artifact.check_results?.visible || [];
  const hidden = artifact.check_results?.hidden || [];
  return `
    <div class="check-list">
      ${visible.length ? `<strong>Visible checks</strong>` : ""}
      ${visible.map((item) => renderCheck(item.command, item.passed, `exit ${item.exit_code}`)).join("")}
      ${hidden.length ? `<strong>Hidden checks</strong>` : ""}
      ${hidden
        .map((item) => renderCheck(item.name, item.passed, item.message || item.axis || ""))
        .join("")}
      ${
        !visible.length && !hidden.length
          ? '<div class="notice">No check details captured in this artifact.</div>'
          : ""
      }
    </div>
  `;
}

function renderCheck(label, passed, detail) {
  return `
    <div class="check-item">
      <div class="check-head">
        <span>${escapeHtml(label)}</span>
        <span class="status-chip ${passed ? "pass" : "fail"}">${passed ? "pass" : "fail"}</span>
      </div>
      ${detail ? `<div class="check-message">${escapeHtml(detail)}</div>` : ""}
    </div>
  `;
}

function renderTranscriptTab(artifact) {
  const transcript = artifact.transcript || [];
  const toolUsage = artifact.tool_usage || [];
  return `
    <div class="transcript-list">
      ${transcript.length ? `<strong>Transcript</strong>` : ""}
      ${transcript
        .map(
          (item) => `
            <div class="transcript-item">
              <span class="transcript-role">${escapeHtml(item.role || "message")}</span>
              <div class="muted">${escapeHtml(item.timestamp || "")}</div>
              <pre class="pre">${escapeHtml(item.content || "")}</pre>
            </div>
          `,
        )
        .join("")}
    </div>
    <div class="tool-list">
      ${toolUsage.length ? `<strong>Tool usage</strong>` : ""}
      ${toolUsage
        .map(
          (item) => `
            <details class="tool-item">
              <summary>${escapeHtml(item.tool || "tool")} ${escapeHtml(toolSummary(item))}</summary>
              <pre class="pre">${escapeHtml(jsonPreview(item.args))}</pre>
              <pre class="pre">${escapeHtml(jsonPreview(item.result))}</pre>
            </details>
          `,
        )
        .join("")}
      ${
        !transcript.length && !toolUsage.length
          ? '<div class="notice">Transcript and tool details are not available in this artifact.</div>'
          : ""
      }
    </div>
  `;
}

function renderFinalStateTab(run, artifact) {
  if (!run.final_state_available || !artifact.final_state) {
    return '<div class="notice">Final-state capture is not present in this artifact. Legacy runs only expose shallow edit summaries.</div>';
  }
  if (!state.selectedPath) {
    return '<div class="notice">Use the changed-surface controls above to choose a file to compare.</div>';
  }

  const match = artifact.final_state.changed_files.find((item) => item.path === state.selectedPath);
  if (!match) {
    return `<div class="notice">${escapeHtml(run.model_key)} did not change ${escapeHtml(state.selectedPath)}.</div>`;
  }

  return `
    <div class="run-summary">
      <span class="label">Selected file</span>
      <span class="value">${escapeHtml(match.path)} · ${escapeHtml(match.change)}</span>
    </div>
    <div class="final-state-grid">
      ${renderFileCard("Before", match.before_hash, match.before_bytes, match.before_text, match.is_text, match.content_truncated, "File not present before run")}
      ${renderFileCard("After", match.after_hash, match.after_bytes, match.after_text, match.is_text, match.content_truncated, "File not present after run")}
    </div>
  `;
}

function renderFileCard(label, hash, bytes, text, isText, truncated, emptyMessage) {
  return `
    <section class="file-card">
      <h4>${escapeHtml(label)}</h4>
      <div class="file-meta">
        <span>Hash: ${escapeHtml(hash || "n/a")}</span>
        <span>Bytes: ${bytes == null ? "n/a" : escapeHtml(String(bytes))}</span>
        <span>Type: ${isText ? "utf-8 text" : "binary or uncaptured text"}</span>
      </div>
      ${
        text != null
          ? `<pre class="pre">${escapeHtml(text)}</pre>`
          : truncated && isText
            ? '<div class="notice">Text omitted because this file exceeded the inline capture limit.</div>'
            : `<div class="notice">${escapeHtml(emptyMessage)}</div>`
      }
    </section>
  `;
}

function requestArtifact(run) {
  if (state.artifactCache.has(run.artifact_url) || state.artifactLoads.has(run.artifact_url)) {
    return;
  }
  const load = fetch(run.artifact_url)
    .then((response) => {
      if (!response.ok) {
        throw new Error(`Failed to load artifact (${response.status})`);
      }
      return response.json();
    })
    .then((artifact) => {
      state.artifactCache.set(run.artifact_url, artifact);
      state.artifactLoads.delete(run.artifact_url);
      renderTaskDetail();
    })
    .catch((error) => {
      state.artifactCache.set(run.artifact_url, { __error: error.message });
      state.artifactLoads.delete(run.artifact_url);
      renderTaskDetail();
    });
  state.artifactLoads.set(run.artifact_url, load);
}

function selectTask(taskId, { rerender = true } = {}) {
  state.selectedTaskId = taskId;
  state.selectedRuns = {};
  state.panelTabs = {};
  state.selectedPath = null;
  state.pathQuery = "";
  window.location.hash = `task=${encodeURIComponent(taskId)}`;
  if (rerender) {
    render();
  }
}

function syncRunSelections(task) {
  const runsByModel = groupRunsByModel(task.runs);
  for (const [modelKey, runs] of Object.entries(runsByModel)) {
    const selectedRunId = state.selectedRuns[modelKey];
    if (!runs.some((run) => getRunId(run) === selectedRunId)) {
      const canonical = runs.find((run) => run.canonical_for_model) || runs[0];
      state.selectedRuns[modelKey] = getRunId(canonical);
    }
    if (!state.panelTabs[modelKey]) {
      state.panelTabs[modelKey] = "overview";
    }
  }
}

function getSelectedRuns(task) {
  return Object.entries(groupRunsByModel(task.runs)).map(([modelKey, runs]) => {
    const selectedRunId = state.selectedRuns[modelKey];
    return runs.find((run) => getRunId(run) === selectedRunId) || runs[0];
  });
}

function getRunsForModel(task, modelKey) {
  return (groupRunsByModel(task.runs)[modelKey] || []).slice();
}

function groupRunsByModel(runs) {
  return runs.reduce((groups, run) => {
    groups[run.model_key] ||= [];
    groups[run.model_key].push(run);
    groups[run.model_key].sort((left, right) => {
      if (left.attempt_index !== right.attempt_index) {
        return left.attempt_index - right.attempt_index;
      }
      return left.retry_index - right.retry_index;
    });
    return groups;
  }, {});
}

function getCanonicalRun(task, modelKey) {
  return getRunsForModel(task, modelKey).find((run) => run.canonical_for_model) || getRunsForModel(task, modelKey)[0] || null;
}

function getUnionPaths(runs) {
  return [...new Set(runs.flatMap((run) => run.changed_paths || []))].sort();
}

function getVisiblePaths(paths) {
  if (!state.pathQuery) {
    return paths;
  }
  return paths.filter((path) => path.toLowerCase().includes(state.pathQuery));
}

function getSelectedTask() {
  return state.index.tasks.find((task) => task.task_id === state.selectedTaskId) || null;
}

function getVisibleTasks() {
  return state.index.tasks.filter((task) => {
    const haystack = [
      task.task_id,
      task.title,
      task.summary,
      task.labels?.task_family,
      ...(task.labels?.temptation_types || []),
    ]
      .join(" ")
      .toLowerCase();
    const matchesSearch = !state.search || haystack.includes(state.search);
    const verdicts = new Set(
      state.index.models
        .map((model) => getCanonicalRun(task, model.model_key))
        .filter(Boolean)
        .map((run) => run.verdict),
    );
    const matchesStatus =
      state.statusFilter === "all" || verdicts.has(state.statusFilter);
    return matchesSearch && matchesStatus;
  });
}

function getRunId(run) {
  return `${run.model_key}::${run.attempt_index}::${run.retry_index}`;
}

function parseHashTaskId() {
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash) {
    return null;
  }
  const params = new URLSearchParams(hash);
  return params.get("task");
}

function renderMetaCard(label, value) {
  return `<div class="meta-card"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></div>`;
}

function renderMetric(label, value) {
  return `<div class="metric"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></div>`;
}

function renderMetricCard(label, value) {
  return `<div class="metric"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></div>`;
}

function formatRunLabel(run) {
  return `attempt ${run.attempt_index + 1} · retry ${run.retry_index} · ${run.verdict} · ${formatDecimal(run.total)}`;
}

function formatTabLabel(tab) {
  return {
    overview: "Overview",
    checks: "Checks",
    transcript: "Transcript/Tools",
    "final-state": "Final State",
  }[tab];
}

function formatSeconds(value) {
  if (value == null) {
    return "n/a";
  }
  if (value >= 3600) {
    return `${(value / 3600).toFixed(1)}h`;
  }
  if (value >= 60) {
    return `${(value / 60).toFixed(1)}m`;
  }
  return `${value.toFixed(1)}s`;
}

function formatDecimal(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return Number(value).toFixed(3);
}

function toolSummary(item) {
  const code = item?.result?.returncode;
  return code == null ? "" : `(exit ${code})`;
}

function jsonPreview(value) {
  if (value == null) {
    return "null";
  }
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return text.length > 5000 ? `${text.slice(0, 5000)}\n…truncated…` : text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}
