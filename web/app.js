const state = {
  result: null,
  selected: null,
  currentView: "overview",
  publishedResults: [],
};

const byId = (id) => document.getElementById(id);

function shortId(value, size = 10) {
  if (!value) return "-";
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

function setText(id, value) {
  byId(id).textContent = value;
}

function showError(payload, status) {
  byId("loadingState").hidden = true;
  byId("overviewView").hidden = true;
  byId("qualityView").hidden = true;
  byId("runsView").hidden = true;
  byId("errorState").hidden = false;
  const missing = payload?.data_state === "not_found";
  setText("errorTitle", missing ? "아직 공개된 결과가 없습니다." : "결과를 읽지 못했습니다.");
  setText("errorMessage", payload?.message || `HTTP ${status}`);
  const badge = byId("resultBadge");
  badge.textContent = missing ? "데이터 없음" : "읽기 실패";
  badge.className = "status-badge is-error";
}

function renderOverview() {
  const result = state.result;
  setText("scopeText", `${result.source.sheet} · Excel ${result.source.selected_excel_rows.join("·")}행`);
  setText("dateBasisText", "원문 InvoiceDate의 날짜 부분");
  setText("runText", `${shortId(result.run.run_id)} · ${result.run.completed_at}`);
  const sqlEvidence = result.applied_transformation_sql
    ? `-- 원천 행 변환과 표시 규칙\n${result.applied_transformation_sql}\n\n-- 상품·날짜별 표본 집계\n${result.applied_sql}`
    : `-- 과거 결과: 원천 행 변환 SQL 식별은 기록되지 않음\n\n${result.applied_sql}`;
  setText("sqlText", sqlEvidence);
  renderProductRows();
  renderQuality();
}

function cancellationMarkerText(row) {
  if (!("source_cancellation_marker" in row)) {
    return "원천 취소 표시 미계산/미확인 (과거 결과)";
  }
  if (row.source_cancellation_marker === "true") {
    return "원천 취소 표시 있음 (true · InvoiceNo C/c 접두)";
  }
  if (row.source_cancellation_marker === "false") {
    return "원천 취소 표시 없음 (false · 판매 확정 아님)";
  }
  return "원천 취소 표시 unknown (InvoiceNo 없음·space/tab/CR/LF만 있음 또는 지원하지 않는 셀 타입)";
}

function renderProductRows() {
  const body = byId("productRows");
  body.replaceChildren();
  const query = byId("productFilter").value.trim().toUpperCase();
  const products = state.result.products.filter((item) => item.stock_code.toUpperCase().includes(query));
  byId("filterEmpty").hidden = products.length > 0;

  products.forEach((product) => {
    const row = document.createElement("tr");
    if (state.selected === product) row.classList.add("is-selected");
    const values = [
      product.observed_date,
      product.stock_code,
      product.sample_quantity_sum.toLocaleString("ko-KR"),
      product.observed_row_count.toLocaleString("ko-KR"),
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index >= 2) cell.className = "number";
      row.appendChild(cell);
    });
    const actionCell = document.createElement("td");
    const action = document.createElement("button");
    action.className = "row-button";
    action.textContent = "근거 보기";
    action.setAttribute("aria-label", `${product.stock_code} 계산 근거 보기`);
    action.addEventListener("click", () => selectProduct(product));
    actionCell.appendChild(action);
    row.appendChild(actionCell);
    body.appendChild(row);
  });
}

function selectProduct(product) {
  state.selected = product;
  renderProductRows();
  setText("detailTitle", product.stock_code);
  setText("detailLocation", product.observed_date);
  setText("detailQuantity", `${product.sample_quantity_sum.toLocaleString("ko-KR")}개`);
  setText("detailRowCount", `${product.observed_row_count.toLocaleString("ko-KR")}행`);
  byId("detailMetrics").hidden = false;
  byId("detailHelp").hidden = true;
  byId("detailBody").hidden = false;
  renderContributions(product);
  renderSource(product);
}

function renderContributions(product) {
  const panel = byId("rowsTab");
  panel.replaceChildren();
  product.contributing_rows.forEach((row) => {
    const item = document.createElement("article");
    item.className = "contribution";
    const head = document.createElement("div");
    head.className = "contribution-head";
    const title = document.createElement("strong");
    title.textContent = `${row.invoice_no ?? "InvoiceNo 없음"} · 수량 ${row.quantity}`;
    const location = document.createElement("span");
    location.textContent = `Excel ${row.source_row_number}행`;
    head.append(title, location);
    const description = document.createElement("p");
    description.textContent = `${row.description} · ${cancellationMarkerText(row)} · 원본에서 읽은 단가 ${row.unit_price_source_text} · ${row.invoice_timestamp}`;
    item.append(head, description);
    panel.appendChild(item);
  });
}

function renderSource(product) {
  const panel = byId("sourceTab");
  panel.replaceChildren();
  const list = document.createElement("dl");
  list.className = "source-grid";
  const entries = [
    ["데이터셋", state.result.source.dataset],
    ["파일 지문", state.result.source.sha256],
    ["시트", state.result.source.sheet],
    ["선택 행", product.contributing_rows.map((row) => row.source_row_number).join(", ")],
    ["표본 한계", state.result.source.scope_note],
  ];
  entries.forEach(([term, description]) => {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = description;
    list.append(dt, dd);
  });
  panel.appendChild(list);
}

function renderQuality() {
  const container = byId("qualityRows");
  container.replaceChildren();
  const rowCount = state.result.products.reduce((sum, item) => sum + item.observed_row_count, 0);
  const markerRecorded = state.result.products.every((item) =>
    item.contributing_rows.every((row) => "source_cancellation_marker" in row)
  );
  const checks = [
    ["확인됨", "원본 파일 지문", `manifest의 SHA-256과 일치한 파일만 공개됐습니다. ${shortId(state.result.source.sha256, 16)}`],
    ["확인됨", "선택한 원본 행", `Excel ${state.result.source.selected_excel_rows.join(", ")}행, 총 ${rowCount}행이 상품별 결과에 연결됐습니다.`],
    ["범위 제한", "완전성", "선택한 행 안에서만 완료된 결과입니다. 하루 전체나 파일 전체의 판매 결과가 아닙니다."],
    [
      markerRecorded ? "관찰 규칙" : "미계산",
      "원천 취소 표시",
      markerRecorded
        ? "InvoiceNo 원문과 셀 종류를 보존합니다. 숫자·문자 셀은 읽기용으로 앞뒤 space·tab·CR·LF를 제거한 첫 글자가 C/c인지 표시하고, 누락·지원 범위 공백만 있음·지원하지 않는 타입은 unknown입니다. false는 판매 확정이 아닙니다."
        : "이 과거 결과에는 원천 취소 표시가 없습니다. 미계산/미확인으로 읽습니다.",
    ],
    ["미확정", "업무 의미", "수량의 부호를 보존했지만 판매·취소·환불·순매출로 분류하지 않았습니다."],
  ];
  checks.forEach(([label, title, description]) => {
    const row = document.createElement("div");
    row.className = "check-row";
    const tag = document.createElement("span");
    tag.className = "check-label";
    tag.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = title;
    const copy = document.createElement("p");
    copy.textContent = description;
    row.append(tag, strong, copy);
    container.appendChild(row);
  });
}

function resultOptionLabel(result) {
  const rows = result.selected_excel_rows.join(",");
  return `${shortId(result.run_id, 10)} · Excel ${rows}행${result.is_current ? " · 현재" : ""}`;
}

function fillResultSelectors(results) {
  const baseSelect = byId("baseResult");
  const currentSelect = byId("currentResult");
  baseSelect.replaceChildren();
  currentSelect.replaceChildren();
  results.forEach((result) => {
    [baseSelect, currentSelect].forEach((select) => {
      const option = document.createElement("option");
      option.value = result.run_id;
      option.textContent = resultOptionLabel(result);
      select.appendChild(option);
    });
  });

  const current = results.find((result) => result.is_current) || results[0];
  const base = results.find((result) => result.run_id !== current?.run_id) || current;
  if (current) currentSelect.value = current.run_id;
  if (base) baseSelect.value = base.run_id;
  byId("compareButton").disabled = results.length < 2;
}

function comparisonBasisText(basis) {
  if (basis.code === "selection_scope_changed") {
    const added = basis.selected_rows_added.length
      ? `추가 Excel ${basis.selected_rows_added.join(", ")}행`
      : "추가 행 없음";
    const removed = basis.selected_rows_removed.length
      ? `빠진 Excel ${basis.selected_rows_removed.join(", ")}행`
      : "빠진 행 없음";
    return `선택 범위 변경 · ${added} · ${removed}`;
  }
  if (basis.code === "same_input_scope_and_rule") {
    return "같은 원본·선택 범위·집계 SQL·원천 해석 규칙입니다.";
  }
  const changed = [];
  if (basis.source_file_changed) changed.push("원본 파일 지문");
  if (basis.sheet_changed) changed.push("시트");
  if (basis.aggregation_rule_changed) changed.push("SQL 계산 기준");
  if (basis.transformation_rule_changed) {
    changed.push(
      basis.transformation_rule_unrecorded
        ? "원천 해석 규칙(과거 결과 ID 미기록)"
        : "원천 해석 규칙"
    );
  } else if (basis.transformation_rule_unrecorded) {
    changed.push("원천 해석 규칙 ID 미기록");
  }
  if (basis.selection_scope_changed) changed.push("선택 범위");
  return `달라졌거나 확인이 필요한 기준: ${changed.join(" · ")}. 늦은 입력으로 단정하지 않고 각 기준을 따로 확인합니다.`;
}

function metricText(stateName, quantity, rowCount) {
  if (stateName === "not_present") return "없음";
  return `${quantity.toLocaleString("ko-KR")} / ${rowCount.toLocaleString("ko-KR")}행`;
}

function locationText(prefix, locations) {
  return locations.map((location) => {
    const file = location.source_file_id === state.result?.source.source_file_id
      ? ""
      : ` (${shortId(location.source_file_id, 12)})`;
    return `${prefix} ${location.sheet} ${location.source_row_number}행${file}`;
  });
}

async function renderComparison() {
  const baseRunId = byId("baseResult").value;
  const currentRunId = byId("currentResult").value;
  if (!baseRunId || !currentRunId) {
    setText("comparisonState", "비교하려면 완료 결과가 두 개 이상 필요합니다.");
    byId("comparisonTable").hidden = true;
    return;
  }

  setText("comparisonState", "두 완료 결과의 계산값과 포함 행을 읽는 중입니다.");
  byId("comparisonTable").hidden = true;
  try {
    const query = new URLSearchParams({base_run_id: baseRunId, current_run_id: currentRunId});
    const response = await fetch(`/api/comparison?${query}`, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
    setText("comparisonState", comparisonBasisText(payload.change_basis));

    const body = byId("comparisonRows");
    body.replaceChildren();
    payload.groups.forEach((group) => {
      const row = document.createElement("tr");
      if (group.changed) row.classList.add("is-changed");
      const values = [
        group.observed_date,
        group.stock_code,
        metricText(group.base_state, group.base_sample_quantity_sum, group.base_observed_row_count),
        metricText(group.current_state, group.current_sample_quantity_sum, group.current_observed_row_count),
      ];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index >= 2) cell.className = "number";
        row.appendChild(cell);
      });
      const changeCell = document.createElement("td");
      const changes = [
        ...locationText("+", group.added_source_rows),
        ...locationText("−", group.removed_source_rows),
      ];
      changeCell.textContent = changes.length ? changes.join(" · ") : "변화 없음";
      row.appendChild(changeCell);
      body.appendChild(row);
    });
    byId("comparisonTable").hidden = false;
  } catch (error) {
    setText("comparisonState", `결과 비교를 읽지 못했습니다: ${error.message}`);
  }
}

async function renderRuns() {
  const body = byId("runRows");
  body.replaceChildren();
  try {
    const [runsResponse, resultsResponse] = await Promise.all([
      fetch("/api/runs", {cache: "no-store"}),
      fetch("/api/results", {cache: "no-store"}),
    ]);
    const runs = await runsResponse.json();
    const results = await resultsResponse.json();
    if (!runsResponse.ok || !Array.isArray(runs)) throw new Error(runs.message || `HTTP ${runsResponse.status}`);
    if (!resultsResponse.ok || !Array.isArray(results)) throw new Error(results.message || `HTTP ${resultsResponse.status}`);
    runs.forEach((run) => {
      const row = document.createElement("tr");
      const id = document.createElement("td");
      const scope = run.selected_excel_rows ? ` · ${run.selected_excel_rows.join(",")}행` : "";
      id.textContent = `${shortId(run.run_id, 12)}${scope}`;
      const status = document.createElement("td");
      status.textContent = run.recovered_at
        ? "중단 복구"
        : run.status === "succeeded"
          ? "완료"
          : run.status === "failed"
            ? "실패"
            : "처리 중";
      status.className = run.status === "succeeded" ? "run-success" : run.status === "failed" ? "run-failed" : "";
      const started = document.createElement("td");
      started.textContent = run.started_at;
      const completed = document.createElement("td");
      completed.textContent = run.recovered_at
        ? "게시 전 중단을 다음 실행에서 복구했습니다."
        : run.error_message || run.completed_at || "-";
      row.append(id, status, started, completed);
      body.appendChild(row);
    });
    state.publishedResults = results;
    fillResultSelectors(results);
    if (results.length >= 2) {
      await renderComparison();
    } else {
      setText("comparisonState", "비교하려면 완료 결과가 두 개 이상 필요합니다.");
      byId("comparisonTable").hidden = true;
    }
  } catch (error) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = `처리 이력을 읽지 못했습니다: ${error.message}`;
    row.appendChild(cell);
    body.appendChild(row);
    setText("comparisonState", `완료 결과를 읽지 못했습니다: ${error.message}`);
    byId("comparisonTable").hidden = true;
  }
}

function switchView(name) {
  state.currentView = name;
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === name);
  });
  byId("overviewView").hidden = name !== "overview";
  byId("qualityView").hidden = name !== "quality";
  byId("runsView").hidden = name !== "runs";
  if (name === "runs") renderRuns();
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  byId("rowsTab").hidden = name !== "rows";
  byId("sourceTab").hidden = name !== "source";
  byId("sqlTab").hidden = name !== "sql";
}

async function loadResult() {
  try {
    const response = await fetch("/api/result", {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) {
      showError(payload, response.status);
      return;
    }
    state.result = payload;
    byId("loadingState").hidden = true;
    byId("errorState").hidden = true;
    byId("overviewView").hidden = false;
    const badge = byId("resultBadge");
    badge.textContent = "선택 행 처리 완료";
    badge.className = "status-badge is-ready";
    renderOverview();
  } catch (error) {
    showError({data_state: "read_error", message: error.message}, 0);
  }
}

document.querySelectorAll(".nav-button").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});
byId("productFilter").addEventListener("input", renderProductRows);
byId("compareButton").addEventListener("click", renderComparison);

loadResult();
