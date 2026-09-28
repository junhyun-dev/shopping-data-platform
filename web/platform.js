"use strict";

const app = { state: null, selectedProduct: null, requestVersion: 0, recovering: false };
const byId = (id) => document.getElementById(id);
const won = (value) => `${BigInt(String(value)).toLocaleString("ko-KR")}원`;
const number = (value) => Number(value).toLocaleString("ko-KR");
const shortRun = (value) => value ? value.slice(0, 8) : "—";

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function switchView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${name}View`));
  document.querySelectorAll(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  window.scrollTo({ top: 0, behavior: "instant" });
}

function showError(message) {
  const banner = byId("globalError");
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function clearError() { byId("globalError").classList.add("hidden"); }

function addMetric(container, label, value) {
  const wrapper = node("div");
  wrapper.append(node("dt", label), node("dd", value));
  container.append(wrapper);
}

function renderSummary(result) {
  const summary = byId("summary");
  summary.replaceChildren();
  addMetric(summary, "대상", `${result.run.target_date} · ${result.coverage.from}~${result.coverage.to}`);
  addMetric(summary, "주문", `${number(result.summary.order_count)}건`);
  addMetric(summary, "상품 수량", `${number(result.summary.product_quantity)}개`);
  addMetric(summary, "주문 품목", `${number(result.summary.order_line_count)}행`);
  addMetric(summary, "주문품목액", won(result.summary.line_amount_krw));
}

function productButton(product) {
  const button = node("button", "거래 근거", "row-button");
  button.type = "button";
  button.addEventListener("click", () => openProduct(product.product_id));
  return button;
}

function renderOverview() {
  const { state } = app;
  const result = state.displayed_result;
  const complete = result.result_state === "complete";
  byId("overviewKicker").textContent = complete ? "정상 게시 결과" : "자료가 덜 들어온 잠정 결과";
  byId("overviewDescription").textContent = complete
    ? `${state.target_date} ${state.coverage.from}~${state.coverage.to} 네 수집 구간을 모두 처리한 결과입니다.`
    : `${state.loaded_segment_count}/${state.expected_segment_count}개 기대 수집 구간만 반영했습니다. 정상 게시값이 아닙니다.`;
  byId("headerState").textContent = complete ? "정상 게시" : "부분 결과 · 확인 필요";
  byId("headerState").className = `status ${complete ? "" : "warn"}`;
  byId("overviewNext").textContent = complete ? "첫 상품 거래 보기" : "누락 구간 확인";
  byId("overviewNext").onclick = () => complete ? openProduct(result.products[0].product_id) : switchView("processing");
  byId("runLabel").textContent = `${complete ? "현재 정상" : "잠정 계산"} ${shortRun(result.run.run_id)}`;
  renderSummary(result);
  const rows = byId("productRows");
  rows.replaceChildren();
  result.products.forEach((product) => {
    const row = node("tr");
    const nameCell = node("td");
    nameCell.append(node("strong", product.product_name), node("div", product.product_id, "muted"));
    row.append(
      nameCell,
      node("td", product.product_quantity_integer_text, "number"),
      node("td", number(product.contributing_order_count), "number"),
      node("td", number(product.order_line_count), "number"),
      node("td", won(product.line_amount_krw), "number")
    );
    const action = node("td");
    action.append(productButton(product));
    row.append(action);
    rows.append(row);
  });
}

function openProduct(productId) {
  const result = app.state.displayed_result;
  const product = result.products.find((item) => item.product_id === productId);
  if (!product) {
    showError("현재 표시 결과에서 해당 상품을 찾을 수 없습니다.");
    return;
  }
  app.selectedProduct = productId;
  byId("productEmpty").classList.add("hidden");
  byId("productDetail").classList.remove("hidden");
  byId("productTitle").textContent = `${product.product_name} · ${product.product_id}`;
  byId("productContext").textContent = `${product.observed_date} · 실행 ${shortRun(result.run.run_id)} · ${result.result_state === "complete" ? "정상 게시" : "잠정 계산"}`;
  const summary = byId("productSummary");
  summary.replaceChildren();
  addMetric(summary, "상품 수량", `${product.product_quantity_integer_text}개`);
  addMetric(summary, "고유 주문", `${number(product.contributing_order_count)}건`);
  addMetric(summary, "품목 행", `${number(product.order_line_count)}행`);
  addMetric(summary, "주문품목액", won(product.line_amount_krw));
  const contributionRows = byId("contributionRows");
  contributionRows.replaceChildren();
  product.contributing_rows.forEach((item) => {
    const row = node("tr");
    const idCell = node("td");
    idCell.append(node("strong", item.order_id), node("div", item.line_id, "muted"));
    const location = node("td");
    location.append(node("div", `order[${item.source_order_index}] / line[${item.source_line_index}]`), node("div", item.source_file_id.slice(0, 19) + "…", "muted"));
    row.append(
      idCell,
      node("td", item.occurred_at),
      node("td", item.segment_id),
      node("td", item.quantity_integer_text, "number"),
      node("td", won(item.unit_price_krw), "number"),
      node("td", won(item.line_amount_krw), "number"),
      location
    );
    contributionRows.append(row);
  });
  byId("sqlPanel").textContent = result.sql.product_summary + "\n\n-- 기여 품목\n" + result.sql.contribution_rows;
  const sources = byId("sourcesPanel");
  sources.replaceChildren();
  result.sources.forEach((source) => {
    const card = node("article", undefined, "source-card");
    card.append(node("strong", `${source.segment_id} · ${source.sha256.slice(0, 12)}…`));
    card.append(node("div", `합성 예시 보관 시각 ${source.archive_arrived_at} · 원천 최초 등록 ${source.processed_at}`, "muted"));
    const path = node("code", source.stored_path);
    card.append(path);
    sources.append(card);
  });
  switchView("product");
}

function renderProcessing() {
  const rows = byId("segmentRows");
  rows.replaceChildren();
  app.state.segments.forEach((segment) => {
    const row = node("tr");
    const source = segment.source_available ? node("span", "보관 확인", "pill") : node("span", "원천 확인 실패", "pill fail");
    const loaded = segment.loaded ? node("span", "적재됨", "pill") : node("span", "빠짐", "pill warn");
    const sourceCell = node("td");
    sourceCell.append(source);
    if (!segment.source_available) {
      sourceCell.append(node("div", segment.source_error || "구체적인 실패 이유를 읽지 못했습니다.", "error-note"));
    }
    const loadedCell = node("td"); loadedCell.append(loaded);
    row.append(node("td", segment.segment_id), node("td", `${segment.from}~${segment.to}`), sourceCell, loadedCell, node("td", segment.archive_arrived_at || "—"));
    rows.append(row);
  });
}

function renderRecovery() {
  const plan = app.state.recovery_plan;
  const recoverySegment = app.state.segments.find((segment) => segment.segment_id === plan.segment_id);
  const sourceError = recoverySegment?.source_error || "구체적인 실패 이유를 읽지 못했습니다.";
  const hasCurrentNormal = Boolean(app.state.current_normal_result);
  const card = byId("recoveryPlan");
  card.replaceChildren();
  const copy = node("div");
  const title = !plan.source_available
    ? `${plan.segment_id} 복구 원천을 확인하지 못했습니다`
    : plan.already_loaded
      ? "누락 구간 복구 완료"
      : `${plan.segment_id} 원천을 확보했습니다`;
  copy.append(node("h3", title));
  copy.append(node("p", !plan.source_available
    ? `복구에 사용할 원천 확인 실패: ${sourceError} ${hasCurrentNormal ? "마지막 정상 결과는 유지하며," : "아직 정상 게시 결과는 없으며,"} 원인을 바로잡기 전에는 재처리를 실행하지 않습니다.`
    : plan.already_loaded
      ? "모든 기대 수집 구간을 실제 적재·검증한 정상 결과가 게시됐습니다."
      : "원천 보관자료의 지문과 관계를 확인한 뒤, 이 구간을 포함해 다시 계산합니다.",
  plan.source_available ? undefined : "error-note"));
  card.append(copy);
  const buttonLabel = !plan.source_available ? "원천 확인 필요" : plan.already_loaded ? "복구 완료" : app.recovering ? "처리 중…" : "SEG-1300 수집·재처리";
  const button = node("button", buttonLabel, "primary");
  button.disabled = !plan.can_run || app.recovering;
  button.addEventListener("click", recover);
  card.append(button);
  const comparison = byId("recoveryComparison");
  const before = app.state.previous_calculation_result;
  const after = app.state.current_normal_result;
  comparison.replaceChildren();
  if (before && after) {
    comparison.classList.remove("hidden");
    comparison.append(node("h3", "복구 전후 저장 결과"));
    comparison.append(node("p", `${shortRun(before.run.run_id)} 잠정 계산과 ${shortRun(after.run.run_id)} 정상 게시를 비교합니다.`, "muted"));
    const metrics = node("dl");
    [["주문", `${before.summary.order_count} → ${after.summary.order_count}건`], ["상품 수량", `${before.summary.product_quantity} → ${after.summary.product_quantity}개`], ["품목 행", `${before.summary.order_line_count} → ${after.summary.order_line_count}행`], ["주문품목액", `${won(before.summary.line_amount_krw)} → ${won(after.summary.line_amount_krw)}`]].forEach(([label, value]) => {
      const wrapper = node("div"); wrapper.append(node("dt", label), node("dd", value)); metrics.append(wrapper);
    });
    comparison.append(metrics);
  } else {
    comparison.classList.add("hidden");
  }
  byId("recoveryDescription").textContent = !plan.source_available
    ? hasCurrentNormal
      ? "마지막 정상 결과를 유지한 채 복구 원천을 다시 확인합니다."
      : "부분 결과를 정상으로 게시하지 않고 복구 원천을 다시 확인합니다."
    : plan.already_loaded
      ? "최신 정상 결과와 실제 처리 이력을 확인합니다."
      : "기대 구간과 도착·적재 상태가 어긋난 근거를 보고 복구합니다.";
  const rows = byId("runRows"); rows.replaceChildren();
  app.state.runs.forEach((run) => {
    const row = node("tr");
    const state = node("span", run.status === "succeeded" ? "성공" : run.status === "failed" ? "실패" : "실행 중", `pill ${run.status === "failed" ? "fail" : run.status === "running" ? "warn" : ""}`);
    const stateCell = node("td"); stateCell.append(state);
    row.append(node("td", shortRun(run.run_id)), node("td", run.run_kind === "initial" ? "첫 계산" : "복구"), stateCell, node("td", run.selected_segments.join(", ")), node("td", run.completed_at || "—"), node("td", run.error_message || "—", run.error_message ? "error-note" : ""));
    rows.append(row);
  });
}

function render() {
  renderOverview();
  renderProcessing();
  renderRecovery();
  if (app.selectedProduct) {
    const exists = app.state.displayed_result.products.some((item) => item.product_id === app.selectedProduct);
    if (!exists) app.selectedProduct = null;
  }
}

async function loadState({ preserveError = false } = {}) {
  const version = ++app.requestVersion;
  if (!preserveError) clearError();
  const response = await fetch("/api/platform/state", { cache: "no-store" });
  const payload = await response.json();
  if (version !== app.requestVersion) return;
  if (!response.ok) throw new Error(payload.message || "상태를 읽지 못했습니다.");
  app.state = payload;
  render();
}

async function recover() {
  if (app.recovering || !app.state.recovery_plan.can_run) return;
  app.recovering = true; renderRecovery(); clearError();
  try {
    const plan = app.state.recovery_plan;
    const response = await fetch("/api/platform/recover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_date: plan.target_date, segment_id: plan.segment_id }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "복구 실행이 실패했습니다.");
    await loadState();
    switchView("overview");
  } catch (error) {
    showError(error.message);
  } finally {
    app.recovering = false;
    if (app.state) renderRecovery();
  }
}

document.querySelectorAll(".nav-button").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
byId("backOverview").addEventListener("click", () => switchView("overview"));
byId("processingRefresh").addEventListener("click", () => loadState().catch((error) => showError(error.message)));
byId("recoveryRefresh").addEventListener("click", () => loadState().catch((error) => showError(error.message)));
document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  ["contributions", "sql", "sources"].forEach((name) => byId(`${name}Panel`).classList.toggle("hidden", name !== button.dataset.tab));
}));

loadState().catch((error) => showError(error.message));
