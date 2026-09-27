const state = {
  result: null,
  currentResult: null,
  selected: null,
  selectedMarker: null,
  currentView: "overview",
  publishedResults: [],
  resultContext: null,
  resultRequestVersion: 0,
  runsRequestVersion: 0,
  comparisonRequestVersion: 0,
  comparisonRowRequestVersion: 0,
  comparisonRowFocus: null,
  comparisonStableState: "비교할 완료 결과를 읽는 중입니다.",
  comparisonStableRowChanges: null,
  comparisonSelectionNotice: "",
  comparisonPayload: null,
  currentLookupStableState: "현재 공개 결과를 조회하기 전입니다.",
  viewDateFilter: {start: "", end: "", error: null},
  viewProductQueryError: null,
  viewFocus: null,
  displayedResultOrigin: null,
  pendingComparedResultRequest: false,
};

const VIEW_QUERY_KEYS = new Set([
  "run_id",
  "product_query",
  "date_start",
  "date_end",
  "sort",
  "focus_code",
  "focus_date",
]);
const VIEW_SORTS = new Set(["default", "quantity-desc", "quantity-asc"]);
const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const VIEW_TEXT_MAX_LENGTH = 128;

const byId = (id) => document.getElementById(id);

function shortId(value, size = 10) {
  if (!value) return "-";
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

function setText(id, value) {
  byId(id).textContent = value;
}

function isIsoCalendarDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1];
}

function readViewDateFilter() {
  const startInput = byId("observedDateStart");
  const endInput = byId("observedDateEnd");
  const start = startInput.value.trim();
  const end = endInput.value.trim();
  let error = null;
  if (startInput.validity.badInput || (start && !isIsoCalendarDate(start))) {
    error = "시작 원문 날짜를 YYYY-MM-DD 형식의 실제 날짜로 입력하세요.";
  } else if (endInput.validity.badInput || (end && !isIsoCalendarDate(end))) {
    error = "종료 원문 날짜를 YYYY-MM-DD 형식의 실제 날짜로 입력하세요.";
  } else if (start && end && start > end) {
    error = "시작 원문 날짜는 종료 원문 날짜보다 늦을 수 없습니다.";
  }
  state.viewDateFilter = {start, end, error};
  return state.viewDateFilter;
}

function viewDateFilterSummary(filter = state.viewDateFilter) {
  if (filter.start && filter.end) {
    return filter.start === filter.end
      ? `원문 날짜 ${filter.start} 하루 (양끝 포함)`
      : `원문 날짜 ${filter.start}~${filter.end} (양끝 포함)`;
  }
  if (filter.start) return `원문 날짜 ${filter.start}부터 (시작 포함)`;
  if (filter.end) return `원문 날짜 ${filter.end}까지 (종료 포함)`;
  return "전체 원문 날짜";
}

function observedDateMatchesView(observedDate, filter = state.viewDateFilter) {
  if (filter.error) return false;
  const value = String(observedDate);
  if (filter.start && value < filter.start) return false;
  if (filter.end && value > filter.end) return false;
  return true;
}

function validViewText(value) {
  return value.length <= VIEW_TEXT_MAX_LENGTH && !/[\u0000-\u001f\u007f]/.test(value);
}

function productQueryValidationError(value) {
  if (value.length > VIEW_TEXT_MAX_LENGTH) {
    return `상품 검색어는 ${VIEW_TEXT_MAX_LENGTH}자 이하여야 합니다.`;
  }
  if (/[\u0000-\u001f\u007f]/.test(value)) {
    return "상품 검색어에는 제어 문자를 사용할 수 없습니다.";
  }
  return null;
}

function parseInitialViewQuery() {
  const params = new URLSearchParams(window.location.search);
  const keys = [...params.keys()];
  if (!keys.length) return {kind: "current"};

  for (const key of keys) {
    if (!VIEW_QUERY_KEYS.has(key)) {
      throw new Error(`지원하지 않는 보기 조건입니다: ${key}`);
    }
    if (params.getAll(key).length !== 1) {
      throw new Error(`보기 조건은 한 번만 지정할 수 있습니다: ${key}`);
    }
  }

  const runId = params.get("run_id") || "";
  if (!runId) throw new Error("저장 실행을 다시 열려면 run_id가 필요합니다.");
  if (!RUN_ID_PATTERN.test(runId)) throw new Error("run_id 형식이 올바르지 않습니다.");

  const productQuerySource = params.get("product_query") || "";
  const productQuery = productQuerySource.trim();
  const dateStart = params.get("date_start") || "";
  const dateEnd = params.get("date_end") || "";
  const sort = params.get("sort") || "default";
  const focusCodeValue = params.get("focus_code");
  const focusDateValue = params.get("focus_date");
  const hasFocusCode = focusCodeValue !== null;
  const hasFocusDate = focusDateValue !== null;

  const productQueryError = productQueryValidationError(productQuerySource);
  if (productQueryError) throw new Error(productQueryError);
  if (dateStart && !isIsoCalendarDate(dateStart)) {
    throw new Error("date_start는 YYYY-MM-DD 형식의 실제 날짜여야 합니다.");
  }
  if (dateEnd && !isIsoCalendarDate(dateEnd)) {
    throw new Error("date_end는 YYYY-MM-DD 형식의 실제 날짜여야 합니다.");
  }
  if (dateStart && dateEnd && dateStart > dateEnd) {
    throw new Error("date_start는 date_end보다 늦을 수 없습니다.");
  }
  if (!VIEW_SORTS.has(sort)) throw new Error("지원하지 않는 표 보기 순서입니다.");
  if (hasFocusCode !== hasFocusDate) {
    throw new Error("focus_code와 focus_date는 함께 지정해야 합니다.");
  }

  let focus = null;
  if (hasFocusCode && hasFocusDate) {
    const stockCode = focusCodeValue.trim();
    const observedDate = focusDateValue;
    if (!stockCode || !validViewText(focusCodeValue)) {
      throw new Error("focus_code가 비었거나 지원하지 않는 문자를 포함합니다.");
    }
    if (!isIsoCalendarDate(observedDate)) {
      throw new Error("focus_date는 YYYY-MM-DD 형식의 실제 날짜여야 합니다.");
    }
    if (productQuery && !stockCode.toUpperCase().includes(productQuery.toUpperCase())) {
      throw new Error("focus_code가 현재 상품 검색 조건에 포함되지 않습니다.");
    }
    if ((dateStart && observedDate < dateStart) || (dateEnd && observedDate > dateEnd)) {
      throw new Error("focus_date가 현재 원문 날짜 범위 밖입니다.");
    }
    focus = {stockCode, observedDate};
  }

  return {
    kind: "pinned",
    runId,
    productQuery,
    dateStart,
    dateEnd,
    sort,
    focus,
  };
}

function focusMatchesCurrentView(focus) {
  if (!focus || state.viewDateFilter.error || state.viewProductQueryError) return false;
  const query = byId("productFilter").value.trim().toUpperCase();
  return focus.stockCode.toUpperCase().includes(query)
    && observedDateMatchesView(focus.observedDate);
}

function syncViewUrl() {
  if (!state.result || state.resultContext || state.viewDateFilter.error || state.viewProductQueryError) return;
  const params = new URLSearchParams({run_id: state.result.run.run_id});
  const productQuery = byId("productFilter").value.trim();
  const sort = byId("productSort").value;
  if (productQuery) params.set("product_query", productQuery);
  if (state.viewDateFilter.start) params.set("date_start", state.viewDateFilter.start);
  if (state.viewDateFilter.end) params.set("date_end", state.viewDateFilter.end);
  if (sort !== "default") params.set("sort", sort);
  if (state.viewFocus && focusMatchesCurrentView(state.viewFocus)) {
    params.set("focus_code", state.viewFocus.stockCode);
    params.set("focus_date", state.viewFocus.observedDate);
  }
  window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
}

function revealPinnedFocusOnSingleColumn() {
  const columns = getComputedStyle(document.querySelector(".workspace-grid"))
    .gridTemplateColumns.trim().split(/\s+/);
  if (columns.length !== 1) return;
  byId("detailPanel").scrollIntoView({behavior: "auto", block: "start"});
}

function applyPinnedViewInputs(view) {
  byId("productFilter").value = view.productQuery;
  byId("productSort").value = view.sort;
  byId("observedDateStart").value = view.dateStart;
  byId("observedDateEnd").value = view.dateEnd;
  state.viewDateFilter = {start: view.dateStart, end: view.dateEnd, error: null};
  state.viewProductQueryError = null;
  state.viewFocus = view.focus ? {...view.focus} : null;
}

function renderProductQueryState() {
  const input = byId("productFilter");
  const status = byId("productFilterState");
  input.setAttribute("aria-invalid", String(Boolean(state.viewProductQueryError)));
  status.hidden = !state.viewProductQueryError;
  status.textContent = state.viewProductQueryError
    ? `상품 검색 조건 오류 · ${state.viewProductQueryError} 현재 조건은 결과 없음이나 값 0으로 처리하지 않았으며, 주소에도 반영하지 않았습니다.`
    : "";
}

function renderViewDateFilterState() {
  const bar = byId("dateFilterBar");
  bar.hidden = !state.result;
  if (!state.result) return;

  const locked = Boolean(state.resultContext);
  const startInput = byId("observedDateStart");
  const endInput = byId("observedDateEnd");
  const clearButton = byId("clearObservedDateFilter");
  [startInput, endInput, clearButton].forEach((control) => {
    control.disabled = locked;
  });
  startInput.setAttribute("aria-invalid", String(Boolean(state.viewDateFilter.error)));
  endInput.setAttribute("aria-invalid", String(Boolean(state.viewDateFilter.error)));

  const status = byId("dateFilterState");
  status.classList.toggle("is-error", Boolean(state.viewDateFilter.error) && !locked);
  if (locked) {
    status.textContent = `${state.resultContext.focus.observedDate} · ${state.resultContext.focus.stockCode}의 저장 근거를 고정 표시합니다. 비교로 돌아가면 ${viewDateFilterSummary()} 조건을 다시 적용합니다.`;
  } else if (state.viewDateFilter.error) {
    status.textContent = `날짜 보기 조건 오류 · ${state.viewDateFilter.error} 정상적인 빈 결과나 값 0으로 처리하지 않았으며, 현재 잘못된 조건은 주소에 반영하지 않았습니다.`;
  } else {
    status.textContent = `${viewDateFilterSummary()} 보기 · 상품 코드 조건과 함께 적용하며 저장 결과를 재계산하지 않습니다.`;
  }
}

function selectedRowRanges(rows) {
  const values = [...new Set(rows.map((row) => Number(row)))].sort((left, right) => left - right);
  if (!values.length || values.some((row) => !Number.isInteger(row))) return [];

  const ranges = [];
  let start = values[0];
  let end = start;
  values.slice(1).forEach((row) => {
    if (row === end + 1) {
      end = row;
      return;
    }
    ranges.push([start, end]);
    start = row;
    end = row;
  });
  ranges.push([start, end]);
  return ranges;
}

function selectedRowsSummary(rows) {
  const ranges = selectedRowRanges(rows);
  if (!ranges.length) return rows.length ? `Excel ${rows.join(", ")}행` : "Excel 선택 행 없음";

  const visibleRanges = ranges.slice(0, 4).map(([start, end]) =>
    start === end ? String(start) : `${start}~${end}`
  );
  const omittedRangeCount = ranges.length - visibleRanges.length;
  const omitted = omittedRangeCount ? ` · 외 ${omittedRangeCount}개 구간` : "";
  return `Excel ${visibleRanges.join(", ")}행${omitted} · ${rows.length.toLocaleString("ko-KR")}행 선택`;
}

function shouldDiscloseSelectedRows(rows) {
  return rows.length > 12 || selectedRowRanges(rows).length > 4;
}

function renderSelectedRowsScope(sheet, rows) {
  const container = byId("scopeText");
  container.replaceChildren();
  const summary = `${sheet} · ${selectedRowsSummary(rows)}`;
  if (!shouldDiscloseSelectedRows(rows)) {
    container.textContent = summary;
    return;
  }

  const details = document.createElement("details");
  details.className = "scope-disclosure";
  const heading = document.createElement("summary");
  heading.textContent = `${summary} · 전체 행 펼치기`;
  const fullRows = document.createElement("span");
  fullRows.className = "scope-full-rows";
  fullRows.textContent = `전체 선택 행: ${rows.join(", ")}`;
  details.append(heading, fullRows);
  container.appendChild(details);
}

function changedRowsSummary(label, rows) {
  if (!rows.length) return `${label} 행 없음`;
  const summary = selectedRowsSummary(rows).replace(/행 선택$/, "행");
  return `${label} ${summary}`;
}

function renderComparisonState(value, rowChanges = null) {
  const container = byId("comparisonState");
  container.replaceChildren();
  const message = document.createElement("p");
  message.className = "comparison-state-copy";
  message.textContent = value;
  container.appendChild(message);

  if (!rowChanges) return;
  const disclosures = document.createElement("div");
  disclosures.className = "comparison-scope-disclosures";
  [
    ["추가", rowChanges.added],
    ["빠진", rowChanges.removed],
  ].forEach(([label, rows]) => {
    if (!shouldDiscloseSelectedRows(rows)) return;
    const details = document.createElement("details");
    details.className = "scope-disclosure comparison-scope-disclosure";
    const heading = document.createElement("summary");
    heading.textContent = `${label} ${rows.length.toLocaleString("ko-KR")}행 전체 펼치기`;
    const fullRows = document.createElement("span");
    fullRows.className = "scope-full-rows";
    fullRows.textContent = `${label} 전체 행: Excel ${rows.join(", ")}`;
    details.append(heading, fullRows);
    disclosures.appendChild(details);
  });
  if (disclosures.childElementCount) container.appendChild(disclosures);
}

function setStableComparisonState(value, rowChanges = null) {
  state.comparisonStableState = value;
  state.comparisonStableRowChanges = rowChanges;
  renderComparisonState(value, rowChanges);
}

function setCurrentLookupState(value) {
  state.currentLookupStableState = value;
  setText("currentResultState", value);
}

function finishCurrentResultRequest() {
  const button = byId("refreshCurrentResult");
  button.disabled = false;
  button.textContent = "최신 결과 다시 읽기";
  setText("currentResultState", state.currentLookupStableState);
}

function invalidatePendingResultRequest() {
  state.resultRequestVersion += 1;
  state.pendingComparedResultRequest = false;
  finishCurrentResultRequest();
}

function leavePendingResultContext() {
  invalidatePendingResultRequest();
  state.runsRequestVersion += 1;
  state.comparisonRowRequestVersion += 1;
  renderComparisonState(
    state.comparisonStableState,
    state.comparisonStableRowChanges
  );
}

function changeComparisonSelection() {
  invalidatePendingResultRequest();
  state.comparisonRequestVersion += 1;
  state.comparisonRowRequestVersion += 1;
  state.comparisonSelectionNotice = "";
  state.comparisonPayload = null;
  byId("comparisonFilterEmpty").hidden = true;
  setStableComparisonState(
    "비교 조건이 바뀌었습니다. 변화 확인을 눌러 두 결과를 다시 읽으세요."
  );
  if (state.comparisonRowFocus) {
    showComparisonRowTrace(
      state.comparisonRowFocus,
      "두 결과 선택이 바뀌었습니다. 변화 확인을 누르면 이 행의 기여 여부도 새 선택으로 다시 읽습니다."
    );
  }
  byId("comparisonTable").hidden = true;
}

function sourceLocationText(focus) {
  return `${focus.sheet} Excel ${focus.sourceRowNumber}행 · 파일 ${focus.sourceFileId}`;
}

function showComparisonRowTrace(focus, message) {
  const panel = byId("comparisonRowTrace");
  panel.hidden = false;
  setText("comparisonRowTraceTitle", `${focus.stockCode} · ${focus.observedDate}`);
  setText("comparisonRowTraceIdentity", sourceLocationText(focus));
  setText("comparisonRowTraceState", message);
  byId("comparisonRowTraceResults").replaceChildren();
}

function hideComparisonRowTrace() {
  byId("comparisonRowTrace").hidden = true;
  byId("comparisonRowTraceResults").replaceChildren();
}

function renderDisplayedResultState() {
  const result = state.result;
  if (!result) {
    setText("displayedResultState", "표시 중인 결과가 없습니다.");
    return;
  }

  let description = "저장 결과";
  if (state.resultContext) {
    const side = state.resultContext.side === "base" ? "비교의 이전 저장 결과" : "비교의 이후 저장 결과";
    const current = state.resultContext.isCurrent ? " · 조회 당시 현재 공개 결과" : "";
    description = `${side}${current}`;
  } else if (state.displayedResultOrigin?.kind === "pinned") {
    description = "주소로 고정해 다시 연 저장 결과";
  } else if (state.currentResult?.run?.run_id === result.run.run_id) {
    description = "마지막으로 읽은 현재 공개 결과";
  }
  setText(
    "displayedResultState",
    `표시 중 run ${shortId(result.run.run_id)} · ${description}`
  );
}

function renderDisplayedResultBadge() {
  const badge = byId("resultBadge");
  if (state.resultContext) {
    badge.textContent = state.resultContext.isCurrent
      ? "비교의 현재 공개 결과"
      : "지난 실행 읽는 중";
    badge.className = "status-badge";
    return;
  }
  if (state.displayedResultOrigin?.kind === "pinned") {
    badge.textContent = "주소로 고정한 저장 결과";
    badge.className = "status-badge";
    return;
  }
  badge.textContent = "조회 당시 최신 공개 결과";
  badge.className = "status-badge is-ready";
}

function renderResultContext() {
  const panel = byId("resultContext");
  const context = state.resultContext;
  panel.hidden = !context;
  if (!context) return;

  const side = context.side === "base" ? "비교의 이전 결과" : "비교의 이후 결과";
  const current = context.isCurrent ? " · 현재 공개 결과" : "";
  setText(
    "resultContextTitle",
    context.isCurrent ? `${side}${current}` : `지난 실행 읽는 중 · ${side}`
  );
  setText(
    "resultContextText",
    `run ${state.result.run.run_id} · 완료 ${state.result.run.completed_at} · ${state.result.source.sheet} · ${selectedRowsSummary(state.result.source.selected_excel_rows)}`
  );
}

function clearProductDetail(
  message = "왼쪽 표의 상품을 누르면 어떤 Excel 행이 숫자에 들어갔는지 확인할 수 있습니다.",
  {preserveViewFocus = false} = {}
) {
  state.selected = null;
  state.selectedMarker = null;
  if (!preserveViewFocus && !state.resultContext) state.viewFocus = null;
  setText("detailTitle", "상품을 선택하세요");
  setText("detailLocation", "-");
  byId("detailMetrics").hidden = true;
  setText("detailHelp", message);
  byId("detailHelp").hidden = false;
  byId("detailBody").hidden = true;
}

function showMissingComparedProduct(focus) {
  state.selected = null;
  state.selectedMarker = null;
  renderProductRows();
  setText("detailTitle", focus.stockCode);
  setText("detailLocation", focus.observedDate);
  byId("detailMetrics").hidden = true;
  setText(
    "detailHelp",
    "이 저장 결과에는 이 상품·날짜 결과가 없습니다. 0으로 계산한 것이 아니며 다른 상품을 대신 보여 주지 않습니다."
  );
  byId("detailHelp").hidden = false;
  byId("detailBody").hidden = false;
  const rowsPanel = byId("rowsTab");
  rowsPanel.replaceChildren();
  const missing = document.createElement("p");
  missing.className = "marker-breakdown-unavailable";
  missing.textContent = "이 실행에는 이 상품·날짜에 기여한 거래가 없습니다. 관찰 행 0을 만든 것이 아닙니다.";
  rowsPanel.appendChild(missing);
  renderSourceDetails(
    `실행 전체 범위: ${selectedRowsSummary(state.result.source.selected_excel_rows)}`
  );
}

function showError(payload, status) {
  byId("loadingState").hidden = true;
  byId("overviewView").hidden = true;
  byId("qualityView").hidden = true;
  byId("runsView").hidden = true;
  byId("errorState").hidden = false;
  const missing = payload?.data_state === "not_found";
  const invalidView = payload?.data_state === "invalid_view";
  setText(
    "errorTitle",
    invalidView
      ? "보기 주소를 열 수 없습니다."
      : missing
        ? "저장 실행을 찾을 수 없습니다."
        : "결과를 읽지 못했습니다."
  );
  setText("errorMessage", payload?.message || `HTTP ${status}`);
  const badge = byId("resultBadge");
  badge.textContent = missing ? "데이터 없음" : "읽기 실패";
  badge.className = "status-badge is-error";
}

function renderOverview() {
  const result = state.result;
  renderResultContext();
  renderDisplayedResultState();
  renderDisplayedResultBadge();
  renderViewDateFilterState();
  renderProductQueryState();
  renderSelectedRowsScope(result.source.sheet, result.source.selected_excel_rows);
  setText("dateBasisText", "원문 InvoiceDate의 날짜 부분");
  setText("runText", `${shortId(result.run.run_id)} · ${result.run.completed_at}`);
  const transformationEvidence = result.applied_transformation_sql
    ? `-- 원천 행 변환과 표시 규칙\n${result.applied_transformation_sql}`
    : "-- 과거 결과: 원천 행 변환 SQL 식별은 기록되지 않음";
  const breakdownEvidence = result.applied_source_cancellation_marker_breakdown_sql
    ? `-- 원천 취소 표시별 구성\n${result.applied_source_cancellation_marker_breakdown_sql}`
    : "-- 과거 결과: 원천 취소 표시별 구성 SQL은 기록되지 않음";
  const sqlEvidence = `${transformationEvidence}\n\n-- 상품·날짜별 표본 집계\n${result.applied_sql}\n\n${breakdownEvidence}`;
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

function exactIntegerText(value) {
  const raw = String(value);
  if (!/^-?\d+$/.test(raw)) return "정밀값 미기록/확인 불가";
  try {
    return BigInt(raw).toLocaleString("ko-KR");
  } catch (_) {
    return "정밀값 미기록/확인 불가";
  }
}

function integerText(exactText, legacyValue) {
  if (exactText !== undefined && exactText !== null) {
    return exactIntegerText(exactText);
  }
  if (typeof legacyValue === "number" && Number.isSafeInteger(legacyValue)) {
    return legacyValue.toLocaleString("ko-KR");
  }
  return "정밀값 미기록/확인 불가";
}

function markerLabel(marker) {
  if (marker === "true") return "표시 있음";
  if (marker === "false") return "표시 없음";
  return "미확인";
}

function productQuantityText(product) {
  return integerText(product.sample_quantity_sum_text, product.sample_quantity_sum);
}

function productQuantityWithUnit(product) {
  const quantity = productQuantityText(product);
  return quantity === "정밀값 미기록/확인 불가" ? quantity : `${quantity}개`;
}

function sortableProductQuantity(product) {
  if (product.sample_quantity_sum_text !== undefined
      && product.sample_quantity_sum_text !== null) {
    const raw = String(product.sample_quantity_sum_text);
    if (!/^-?\d+$/.test(raw)) return null;
    try {
      return BigInt(raw);
    } catch (_) {
      return null;
    }
  }
  if (typeof product.sample_quantity_sum === "number"
      && Number.isSafeInteger(product.sample_quantity_sum)) {
    return BigInt(product.sample_quantity_sum);
  }
  return null;
}

function compareProductIdentity(left, right) {
  const leftDate = String(left.observed_date);
  const rightDate = String(right.observed_date);
  const dateComparison = leftDate === rightDate ? 0 : (leftDate < rightDate ? -1 : 1);
  if (dateComparison) return dateComparison;
  const leftCode = String(left.stock_code);
  const rightCode = String(right.stock_code);
  return leftCode === rightCode ? 0 : (leftCode < rightCode ? -1 : 1);
}

function compareProductQuantity(left, right, direction) {
  const leftQuantity = sortableProductQuantity(left);
  const rightQuantity = sortableProductQuantity(right);
  if (leftQuantity === null && rightQuantity !== null) return 1;
  if (leftQuantity !== null && rightQuantity === null) return -1;
  if (leftQuantity !== null && rightQuantity !== null && leftQuantity !== rightQuantity) {
    const comparison = leftQuantity < rightQuantity ? -1 : 1;
    return direction === "quantity-desc" ? -comparison : comparison;
  }
  return compareProductIdentity(left, right);
}

function renderProductRows() {
  const body = byId("productRows");
  body.replaceChildren();
  const query = byId("productFilter").value.trim().toUpperCase();
  const sort = byId("productSort").value;
  const focus = state.resultContext?.focus;
  const products = state.result.products.filter((item) => {
    if (focus) {
      return item.observed_date === focus.observedDate
        && item.stock_code === focus.stockCode;
    }
    if (state.viewDateFilter.error || state.viewProductQueryError) return false;
    return item.stock_code.toUpperCase().includes(query)
      && observedDateMatchesView(item.observed_date);
  });
  if (sort !== "default") {
    products.sort((left, right) => compareProductQuantity(left, right, sort));
  }
  const unknownQuantityCount = sort === "default"
    ? 0
    : products.filter((product) => sortableProductQuantity(product) === null).length;
  const sortNotice = byId("productSortNotice");
  sortNotice.hidden = unknownQuantityCount === 0
    || Boolean((state.viewDateFilter.error || state.viewProductQueryError) && !focus);
  sortNotice.textContent = unknownQuantityCount
    ? `정밀 수량을 확인할 수 없는 ${unknownQuantityCount.toLocaleString("ko-KR")}개 결과는 순서를 확정하지 않고, 정확한 값이 있는 결과 뒤에 날짜·상품 코드 순으로 표시합니다.`
    : "";
  const filterEmpty = byId("filterEmpty");
  const dateFiltered = state.viewDateFilter.start || state.viewDateFilter.end;
  filterEmpty.textContent = focus
    ? `이 저장 결과에는 ${focus.observedDate} · ${focus.stockCode} 상품 결과가 없습니다. 0으로 계산한 것이 아닙니다.`
    : dateFiltered
      ? `현재 원문 날짜 보기 조건${query ? "과 상품 코드" : ""}에 맞는 결과가 없습니다. 0으로 계산한 것이 아닙니다.`
      : "이 표본에 해당 상품이 없습니다. 0으로 계산한 것이 아닙니다.";
  const viewFilterError = Boolean(
    (state.viewDateFilter.error || state.viewProductQueryError) && !focus
  );
  byId("productTable").hidden = viewFilterError;
  filterEmpty.hidden = viewFilterError || products.length > 0;

  products.forEach((product) => {
    const row = document.createElement("tr");
    if (state.selected === product) row.classList.add("is-selected");
    const values = [
      product.observed_date,
      product.stock_code,
      productQuantityText(product),
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
  state.selectedMarker = null;
  if (!state.resultContext) {
    state.viewFocus = {
      stockCode: product.stock_code,
      observedDate: product.observed_date,
    };
  }
  renderProductRows();
  setText("detailTitle", product.stock_code);
  setText("detailLocation", product.observed_date);
  setText("detailQuantity", productQuantityWithUnit(product));
  setText("detailRowCount", `${product.observed_row_count.toLocaleString("ko-KR")}행`);
  byId("detailMetrics").hidden = false;
  byId("detailHelp").hidden = true;
  byId("detailBody").hidden = false;
  renderContributions(product);
  renderSource(product);
  if (!state.resultContext) syncViewUrl();
}

function renderContributions(product) {
  const panel = byId("rowsTab");
  panel.replaceChildren();
  const breakdown = product.source_cancellation_marker_breakdown;
  const groups = breakdown?.data_state === "available" && Array.isArray(breakdown.groups)
    ? breakdown.groups
    : null;

  if (!groups) {
    const unavailable = document.createElement("p");
    unavailable.className = "marker-breakdown-unavailable";
    unavailable.textContent = "이 과거 결과에는 원천 취소 표시별 구성이 없습니다. 미계산/미확인으로 두고, 아래 원래 거래는 그대로 보여 줍니다.";
    panel.appendChild(unavailable);
    state.selectedMarker = null;
  } else {
    const section = document.createElement("section");
    section.className = "marker-breakdown";
    const heading = document.createElement("h3");
    heading.textContent = `표본 수량 ${productQuantityText(product)}의 원천 표시별 구성`;
    const explanation = document.createElement("p");
    explanation.textContent = "표시 없음은 판매 확정이 아니며, 표시 있음도 원거래 연결이나 환불 완료를 뜻하지 않습니다.";
    const controls = document.createElement("div");
    controls.className = "marker-filter-list";

    const addFilter = ({marker, label, detail, disabled = false}) => {
      const button = document.createElement("button");
      const active = marker === state.selectedMarker;
      button.type = "button";
      button.className = `marker-filter${active ? " is-active" : ""}`;
      button.disabled = disabled;
      button.setAttribute("aria-pressed", String(active));
      const strong = document.createElement("strong");
      strong.textContent = label;
      const span = document.createElement("span");
      span.textContent = detail;
      button.append(strong, span);
      if (!disabled) {
        button.addEventListener("click", () => {
          state.selectedMarker = marker;
          renderContributions(product);
        });
      }
      controls.appendChild(button);
    };

    addFilter({
      marker: null,
      label: "전체 기여 행",
      detail: `수량 합계 ${productQuantityText(product)} · ${product.observed_row_count.toLocaleString("ko-KR")}행`,
    });
    ["true", "false", "unknown"].forEach((marker) => {
      const group = groups.find((item) => item.source_cancellation_marker === marker);
      addFilter({
        marker,
        label: markerLabel(marker),
        detail: group
          ? `부호 있는 수량 ${exactIntegerText(group.signed_quantity_sum_text)} · ${group.observed_row_count.toLocaleString("ko-KR")}행`
          : "해당 행 없음",
        disabled: !group,
      });
    });
    section.append(heading, explanation, controls);
    panel.appendChild(section);
  }

  let rows = product.contributing_rows;
  if (groups && state.selectedMarker !== null) {
    const selectedGroup = groups.find(
      (item) => item.source_cancellation_marker === state.selectedMarker
    );
    const sourceRows = new Set(selectedGroup?.contributing_excel_rows || []);
    rows = product.contributing_rows.filter((row) => sourceRows.has(row.source_row_number));
    const selection = document.createElement("p");
    selection.className = "marker-selection";
    selection.textContent = `${markerLabel(state.selectedMarker)} · ${rows.map((row) => `Excel ${row.source_row_number}행`).join(" · ")}`;
    panel.appendChild(selection);
  }

  rows.forEach((row) => {
    const item = document.createElement("article");
    item.className = "contribution";
    const head = document.createElement("div");
    head.className = "contribution-head";
    const title = document.createElement("strong");
    const quantity = integerText(row.quantity_integer_text, row.quantity);
    title.textContent = `${row.invoice_no ?? "InvoiceNo 없음"} · 수량 ${quantity}`;
    const location = document.createElement("span");
    location.textContent = `Excel ${row.source_row_number}행`;
    head.append(title, location);
    const description = document.createElement("p");
    description.textContent = `${row.description} · ${cancellationMarkerText(row)} · 원본에서 읽은 단가 ${row.unit_price_source_text} · ${row.invoice_timestamp}`;
    const compareAction = document.createElement("button");
    compareAction.type = "button";
    compareAction.className = "contribution-compare-button";
    compareAction.textContent = "선택한 두 결과에서 이 행 보기";
    compareAction.setAttribute(
      "aria-label",
      `${row.sheet} Excel ${row.source_row_number}행이 선택한 두 결과에 기여했는지 보기`
    );
    compareAction.addEventListener("click", () => openContributionComparison(product, row));
    item.append(head, description, compareAction);
    panel.appendChild(item);
  });
}

function openContributionComparison(product, row) {
  state.comparisonRowFocus = {
    sourceFileId: state.result.source.source_file_id,
    sheet: row.sheet,
    sourceRowNumber: Number(row.source_row_number),
    stockCode: product.stock_code,
    observedDate: product.observed_date,
  };
  showComparisonRowTrace(
    state.comparisonRowFocus,
    "처리 이력에서 선택한 두 저장 결과를 읽어 이 행의 기여 여부를 확인합니다."
  );
  if (state.resultContext) restoreResultContextParent();
  switchView("runs", false);
  const hasSelectedPair = byId("baseResult").value && byId("currentResult").value;
  if (state.publishedResults.length >= 2 && hasSelectedPair) {
    renderComparison();
  } else {
    renderRuns();
  }
}

function renderSource(product) {
  renderSourceDetails(
    product.contributing_rows.map((row) => row.source_row_number).join(", ")
  );
}

function renderSourceDetails(selectedRows) {
  const panel = byId("sourceTab");
  panel.replaceChildren();
  const list = document.createElement("dl");
  list.className = "source-grid";
  const entries = [
    ["데이터셋", state.result.source.dataset],
    ["파일 지문", state.result.source.sha256],
    ["시트", state.result.source.sheet],
    ["선택 행", selectedRows],
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
    ["확인됨", "선택한 원본 행", `${selectedRowsSummary(state.result.source.selected_excel_rows)}, 총 ${rowCount.toLocaleString("ko-KR")}행이 상품별 결과에 연결됐습니다.`],
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
  return `${shortId(result.run_id, 10)} · ${selectedRowsSummary(result.selected_excel_rows)}${result.is_current ? " · 현재" : ""}`;
}

function fillResultSelectors(results) {
  const baseSelect = byId("baseResult");
  const currentSelect = byId("currentResult");
  const preferredBaseRunId = baseSelect.value;
  const preferredCurrentRunId = currentSelect.value;
  const availableRunIds = new Set(results.map((result) => result.run_id));
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

  const defaultCurrent = results.find((result) => result.is_current) || results[0];
  let baseRunId = availableRunIds.has(preferredBaseRunId)
    ? preferredBaseRunId
    : null;
  let currentRunId = availableRunIds.has(preferredCurrentRunId)
    ? preferredCurrentRunId
    : null;
  if (!currentRunId) {
    currentRunId = [defaultCurrent, ...results].find(
      (result) => result && result.run_id !== baseRunId
    )?.run_id || defaultCurrent?.run_id || null;
  }
  if (!baseRunId) {
    baseRunId = results.find(
      (result) => result.run_id !== currentRunId
    )?.run_id || currentRunId;
  }
  if (currentRunId) currentSelect.value = currentRunId;
  if (baseRunId) baseSelect.value = baseRunId;

  const missingSelections = [];
  if (preferredBaseRunId && !availableRunIds.has(preferredBaseRunId)) {
    missingSelections.push("이전 결과");
  }
  if (preferredCurrentRunId && !availableRunIds.has(preferredCurrentRunId)) {
    missingSelections.push("현재 결과");
  }
  if (!preferredBaseRunId && !preferredCurrentRunId && results.length) {
    state.comparisonSelectionNotice = "처음 열어 현재 결과와 다른 완료 결과를 기본 선택했습니다.";
  } else if (missingSelections.length) {
    state.comparisonSelectionNotice = `${missingSelections.join("·")}가 새 목록에 없어 남아 있는 완료 결과로 다시 선택했습니다.`;
  } else {
    state.comparisonSelectionNotice = "";
  }
  byId("compareButton").disabled = results.length < 2;
}

function comparisonBasisText(basis) {
  if (basis.code === "selection_scope_changed") {
    const added = changedRowsSummary("추가", basis.selected_rows_added);
    const removed = changedRowsSummary("빠진", basis.selected_rows_removed);
    return `비교 기준 다름 · 선택 범위 변경 · ${added} · ${removed}. 숫자가 달라진 상품은 추가·빠진 원래 거래를 확인하세요.`;
  }
  if (basis.code === "same_input_scope_and_rule") {
    return "비교 기준 같음 · 원본 파일·시트·선택 범위·집계 SQL·원천 해석 규칙이 같습니다.";
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
  const stateText = basis.transformation_rule_unrecorded
    ? "비교 기준 일부 확인 불가"
    : "비교 기준 다름";
  return `${stateText} · ${changed.join(" · ")}. 숫자가 같아도 바뀐 기준과 원래 거래를 확인하세요.`;
}

function metricText(stateName, quantityText, quantity, rowCount) {
  if (stateName === "not_present") return "없음";
  return `${integerText(quantityText, quantity)} / ${rowCount.toLocaleString("ko-KR")}행`;
}

function locationText(prefix, locations) {
  return locations.map((location) => {
    const file = location.source_file_id === state.result?.source.source_file_id
      ? ""
      : ` (${shortId(location.source_file_id, 12)})`;
    return `${prefix} ${location.sheet} ${location.source_row_number}행${file}`;
  });
}

function valueChangeText(group) {
  if (group.base_state === "not_present") return "값: 현재 결과에 새로 나타남";
  if (group.current_state === "not_present") return "값: 현재 결과에 없음(0 아님)";

  const changed = [];
  const quantityChanged = "quantity_sum_changed" in group
    ? group.quantity_sum_changed
    : group.base_sample_quantity_sum !== group.current_sample_quantity_sum;
  const rowCountChanged = "observed_row_count_changed" in group
    ? group.observed_row_count_changed
    : group.base_observed_row_count !== group.current_observed_row_count;
  if (quantityChanged) {
    changed.push("수량 합계");
  }
  if (rowCountChanged) {
    changed.push("관찰 행 수");
  }
  return changed.length ? `값: ${changed.join("·")} 달라짐` : "값: 같음";
}

function basisEvidence(basis) {
  const evidence = [];
  if (basis.source_file_changed) evidence.push("원본 파일");
  if (basis.sheet_changed) evidence.push("시트");
  if (basis.aggregation_rule_changed) evidence.push("집계 SQL");
  if (basis.transformation_rule_changed || basis.transformation_rule_unrecorded) {
    evidence.push("원천 해석 규칙");
  }
  return evidence;
}

function evidenceText(group, basis) {
  const locationsChanged = group.added_source_rows.length || group.removed_source_rows.length;
  const valueChanged = group.base_state !== group.current_state
    || ("quantity_sum_changed" in group
      ? group.quantity_sum_changed
      : group.base_sample_quantity_sum !== group.current_sample_quantity_sum)
    || ("observed_row_count_changed" in group
      ? group.observed_row_count_changed
      : group.base_observed_row_count !== group.current_observed_row_count);
  const evidence = [];

  if (locationsChanged) evidence.push("추가·빠진 원래 거래");
  basisEvidence(basis).forEach((item) => evidence.push(item));
  if (valueChanged && !locationsChanged && !evidence.length) {
    evidence.push("해당 원래 거래 값", "적용 SQL");
  }
  return evidence.length
    ? `다음 확인: ${[...new Set(evidence)].join(" · ")}`
    : "이 상품에서는 값·원본 위치 변화 없음";
}

function appendComparisonLine(container, text, className = "") {
  const line = document.createElement("span");
  line.textContent = text;
  if (className) line.className = className;
  container.appendChild(line);
}

function comparisonMetricCell({side, group, runId}) {
  const cell = document.createElement("td");
  cell.className = "number comparison-result-cell";
  const value = document.createElement("span");
  const isBase = side === "base";
  value.textContent = metricText(
    isBase ? group.base_state : group.current_state,
    isBase ? group.base_sample_quantity_sum_text : group.current_sample_quantity_sum_text,
    isBase ? group.base_sample_quantity_sum : group.current_sample_quantity_sum,
    isBase ? group.base_observed_row_count : group.current_observed_row_count
  );
  const action = document.createElement("button");
  action.type = "button";
  action.className = "comparison-open-button";
  action.textContent = isBase ? "이전 근거" : "이후 근거";
  action.setAttribute(
    "aria-label",
    `${group.observed_date} ${group.stock_code} ${action.textContent} 보기`
  );
  action.addEventListener("click", () => openComparedResult({side, group, runId}));
  cell.append(value, action);
  return cell;
}

async function readStoredResult(runId) {
  try {
    const query = new URLSearchParams({run_id: runId});
    const response = await fetch(`/api/result?${query}`, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
    if (payload.result?.run?.run_id !== runId) {
      throw new Error("요청한 실행과 다른 저장 결과가 반환됐습니다.");
    }
    return {dataState: "available", ...payload};
  } catch (error) {
    return {dataState: "read_error", message: error.message};
  }
}

function findRowContribution(result, focus) {
  if (result.source.source_file_id !== focus.sourceFileId) return null;
  for (const product of result.products) {
    const row = product.contributing_rows.find(
      (candidate) => candidate.sheet === focus.sheet
        && Number(candidate.source_row_number) === focus.sourceRowNumber
    );
    if (row) return {product, row};
  }
  return null;
}

function comparisonRowResultCard({side, runId, read, focus}) {
  const card = document.createElement("article");
  card.className = "comparison-row-result";
  const heading = document.createElement("div");
  heading.className = "comparison-row-result-head";
  const label = document.createElement("strong");
  label.textContent = `${side === "base" ? "이전 결과" : "이후 결과"} · run ${shortId(runId)}`;
  heading.appendChild(label);
  card.appendChild(heading);

  if (read.dataState !== "available") {
    card.classList.add("is-unknown");
    const stateLine = document.createElement("p");
    stateLine.className = "comparison-row-contribution-state";
    stateLine.textContent = "기여 여부 확인 불가";
    const reason = document.createElement("p");
    reason.textContent = `저장 결과를 읽지 못했습니다: ${read.message}`;
    card.append(stateLine, reason);
    return card;
  }

  const result = read.result;
  const contribution = findRowContribution(result, focus);
  const focusProduct = result.products.find(
    (product) => product.observed_date === focus.observedDate
      && product.stock_code === focus.stockCode
  );
  const linkedProduct = contribution?.product || focusProduct;
  const linkedFocus = contribution
    ? {
        observedDate: contribution.product.observed_date,
        stockCode: contribution.product.stock_code,
      }
    : {
        observedDate: focus.observedDate,
        stockCode: focus.stockCode,
      };
  const stateLine = document.createElement("p");
  stateLine.className = "comparison-row-contribution-state";
  if (contribution) {
    stateLine.textContent = `기여함 · 부호 있는 수량 ${integerText(contribution.row.quantity_integer_text, contribution.row.quantity)}`;
    card.classList.add("is-contributing");
  } else {
    stateLine.textContent = "기여하지 않음";
  }
  const reason = document.createElement("p");
  if (result.source.source_file_id !== focus.sourceFileId) {
    reason.textContent = "원본 파일 지문이 달라 같은 Excel 행번호여도 같은 원래 거래로 보지 않습니다.";
  } else if (result.source.sheet !== focus.sheet) {
    reason.textContent = "시트가 달라 같은 Excel 행번호여도 같은 원래 거래로 보지 않습니다.";
  } else if (!contribution) {
    reason.textContent = "이 저장 결과의 기여 행에 해당 원본 위치가 없습니다.";
  } else {
    reason.textContent = `${contribution.product.observed_date} · ${contribution.product.stock_code} 결과에 들어갔습니다.`;
  }

  const productLine = document.createElement("p");
  productLine.className = "comparison-row-product-state";
  productLine.textContent = linkedProduct
    ? `${linkedFocus.observedDate} · ${linkedFocus.stockCode} 결과 ${productQuantityText(linkedProduct)} / ${linkedProduct.observed_row_count.toLocaleString("ko-KR")}행`
    : `${focus.observedDate} · ${focus.stockCode} 결과 없음(0 아님)`;

  const action = document.createElement("button");
  action.type = "button";
  action.className = "comparison-open-button";
  action.textContent = "이 결과의 당시 근거";
  action.addEventListener("click", () => openComparedResult({
    side,
    runId,
    group: {
      observed_date: linkedFocus.observedDate,
      stock_code: linkedFocus.stockCode,
    },
  }));
  card.append(stateLine, reason, productLine, action);
  return card;
}

async function renderComparisonRowTrace(comparison, comparisonVersion) {
  const focus = state.comparisonRowFocus;
  if (!focus) {
    hideComparisonRowTrace();
    return;
  }
  const requestVersion = ++state.comparisonRowRequestVersion;
  const focusSnapshot = {...focus};
  showComparisonRowTrace(focusSnapshot, "두 저장 결과의 기여 행을 읽는 중입니다.");
  const [baseRead, currentRead] = await Promise.all([
    readStoredResult(comparison.base_run_id),
    readStoredResult(comparison.current_run_id),
  ]);
  if (
    comparisonVersion !== state.comparisonRequestVersion
    || requestVersion !== state.comparisonRowRequestVersion
    || state.comparisonRowFocus !== focus
  ) return;

  const results = byId("comparisonRowTraceResults");
  results.replaceChildren(
    comparisonRowResultCard({
      side: "base",
      runId: comparison.base_run_id,
      read: baseRead,
      focus: focusSnapshot,
    }),
    comparisonRowResultCard({
      side: "current",
      runId: comparison.current_run_id,
      read: currentRead,
      focus: focusSnapshot,
    })
  );
  const failedCount = [baseRead, currentRead].filter(
    (read) => read.dataState !== "available"
  ).length;
  setText(
    "comparisonRowTraceState",
    failedCount
      ? `${failedCount}개 저장 결과는 읽지 못해 비기여가 아니라 확인 불가로 남겼습니다.`
      : "각 결과가 저장할 때 남긴 기여 행만 대조했습니다. 한 행만으로 수량 변화의 업무 원인을 확정하지 않습니다."
  );
}

async function openComparedResult({side, group, runId}) {
  finishCurrentResultRequest();
  const requestVersion = ++state.resultRequestVersion;
  state.pendingComparedResultRequest = true;
  const existingContext = state.resultContext;
  const previousComparisonState = existingContext?.comparisonState || {
    value: state.comparisonStableState,
    rowChanges: state.comparisonStableRowChanges,
  };
  const previousDisplayedResult = existingContext?.previousResult || state.result;
  const parentOrigin = existingContext?.previousDisplayedResultOrigin || state.displayedResultOrigin;
  const previousDisplayedResultOrigin = parentOrigin ? {...parentOrigin} : null;
  const parentFocus = existingContext?.previousViewFocus || state.viewFocus;
  const previousViewFocus = parentFocus ? {...parentFocus} : null;
  const previousFilter = existingContext?.previousFilter ?? byId("productFilter").value;
  const previousSort = existingContext?.previousSort ?? byId("productSort").value;
  const previousDateFilter = existingContext?.previousDateFilter || state.viewDateFilter;
  const previousProductQueryError = existingContext?.previousProductQueryError
    ?? state.viewProductQueryError;
  renderComparisonState(
    `${side === "base" ? "이전" : "이후"} 저장 결과의 근거를 읽는 중입니다.`
  );
  try {
    const query = new URLSearchParams({run_id: runId});
    const response = await fetch(`/api/result?${query}`, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
    if (requestVersion !== state.resultRequestVersion) return;
    if (payload.result?.run?.run_id !== runId) {
      throw new Error("요청한 실행과 다른 저장 결과가 반환됐습니다.");
    }
    state.pendingComparedResultRequest = false;

    const focus = {
      observedDate: group.observed_date,
      stockCode: group.stock_code,
    };
    state.result = payload.result;
    state.resultContext = {
      side,
      isCurrent: Boolean(payload.is_current),
      focus,
      previousResult: previousDisplayedResult,
      previousDisplayedResultOrigin,
      previousViewFocus,
      previousFilter,
      previousSort,
      previousDateFilter: {...previousDateFilter},
      previousProductQueryError,
      comparisonState: previousComparisonState,
    };
    state.selected = null;
    state.selectedMarker = null;
    byId("productFilter").value = group.stock_code;
    byId("productFilter").disabled = true;
    renderOverview();
    switchTab("rows");
    switchView("overview", false);
    const product = state.result.products.find(
      (item) => item.observed_date === focus.observedDate
        && item.stock_code === focus.stockCode
    );
    if (product) {
      selectProduct(product);
    } else {
      showMissingComparedProduct(focus);
    }
    const badge = byId("resultBadge");
    badge.textContent = payload.is_current ? "비교의 현재 공개 결과" : "지난 실행 읽는 중";
    badge.className = "status-badge";
  } catch (error) {
    if (requestVersion !== state.resultRequestVersion) return;
    state.pendingComparedResultRequest = false;
    renderComparisonState(`저장 결과의 근거를 읽지 못했습니다: ${error.message}`);
  }
}

function restoreResultContextParent() {
  const context = state.resultContext;
  if (!context) return false;
  invalidatePendingResultRequest();
  state.result = context.previousResult;
  state.displayedResultOrigin = context.previousDisplayedResultOrigin;
  state.viewFocus = context.previousViewFocus;
  state.viewProductQueryError = context.previousProductQueryError;
  state.resultContext = null;
  state.selected = null;
  state.selectedMarker = null;
  byId("productFilter").disabled = false;
  byId("productFilter").value = context.previousFilter;
  byId("productSort").value = context.previousSort;
  state.viewDateFilter = {...context.previousDateFilter};
  byId("observedDateStart").value = state.viewDateFilter.start;
  byId("observedDateEnd").value = state.viewDateFilter.end;
  renderOverview();
  clearProductDetail(undefined, {preserveViewFocus: true});
  setStableComparisonState(
    context.comparisonState.value,
    context.comparisonState.rowChanges
  );
  syncViewUrl();
  return true;
}

function returnToComparison() {
  if (!restoreResultContextParent()) return;
  switchView("runs", false);
}

function renderComparisonGroups(payload) {
  const query = byId("comparisonProductFilter").value.trim().toUpperCase();
  const groups = state.viewDateFilter.error
    ? []
    : payload.groups.filter(
        (group) => group.stock_code.toUpperCase().includes(query)
          && observedDateMatchesView(group.observed_date)
      );
  const body = byId("comparisonRows");
  body.replaceChildren();
  const filterEmpty = byId("comparisonFilterEmpty");
  const dateFiltered = state.viewDateFilter.start || state.viewDateFilter.end;
  filterEmpty.textContent = dateFiltered
    ? `현재 원문 날짜 보기 조건${query ? "과 상품 코드" : ""}에 맞는 비교 결과가 없습니다. 값 0이나 비기여를 뜻하지 않습니다.`
    : "이 비교 결과에는 해당 상품 코드가 없습니다. 값 0이나 비기여를 뜻하지 않습니다.";
  filterEmpty.hidden = Boolean(state.viewDateFilter.error) || groups.length > 0;

  groups.forEach((group) => {
    const row = document.createElement("tr");
    if (group.changed) row.classList.add("is-changed");
    const basisNeedsReview = payload.change_basis.source_file_changed
      || payload.change_basis.sheet_changed
      || payload.change_basis.aggregation_rule_changed
      || payload.change_basis.transformation_rule_changed
      || payload.change_basis.transformation_rule_unrecorded;
    if (!group.changed && basisNeedsReview) row.classList.add("needs-review");
    [group.observed_date, group.stock_code].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    row.appendChild(comparisonMetricCell({
      side: "base",
      group,
      runId: payload.base_run_id,
    }));
    row.appendChild(comparisonMetricCell({
      side: "current",
      group,
      runId: payload.current_run_id,
    }));
    const changeCell = document.createElement("td");
    const changes = [
      ...locationText("+", group.added_source_rows),
      ...locationText("−", group.removed_source_rows),
    ];
    changeCell.className = "comparison-detail";
    appendComparisonLine(changeCell, valueChangeText(group), "comparison-value-state");
    appendComparisonLine(
      changeCell,
      changes.length ? `원본 위치: ${changes.join(" · ")}` : "원본 위치: 같음"
    );
    appendComparisonLine(
      changeCell,
      evidenceText(group, payload.change_basis),
      "comparison-next-evidence"
    );
    row.appendChild(changeCell);
    body.appendChild(row);
  });
}

async function renderComparison() {
  invalidatePendingResultRequest();
  const requestVersion = ++state.comparisonRequestVersion;
  state.comparisonPayload = null;
  byId("comparisonFilterEmpty").hidden = true;
  const baseRunId = byId("baseResult").value;
  const currentRunId = byId("currentResult").value;
  if (!baseRunId || !currentRunId) {
    setStableComparisonState("비교하려면 완료 결과가 두 개 이상 필요합니다.");
    byId("comparisonTable").hidden = true;
    return;
  }

  renderComparisonState("두 완료 결과의 계산값과 포함 행을 읽는 중입니다.");
  byId("comparisonTable").hidden = true;
  try {
    const query = new URLSearchParams({base_run_id: baseRunId, current_run_id: currentRunId});
    const response = await fetch(`/api/comparison?${query}`, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
    if (requestVersion !== state.comparisonRequestVersion) return;
    const selectionNotice = state.comparisonSelectionNotice;
    state.comparisonSelectionNotice = "";
    const comparisonState = selectionNotice
      ? `${selectionNotice} ${comparisonBasisText(payload.change_basis)}`
      : comparisonBasisText(payload.change_basis);
    const rowChanges = payload.change_basis.code === "selection_scope_changed"
      ? {
          added: payload.change_basis.selected_rows_added,
          removed: payload.change_basis.selected_rows_removed,
        }
      : null;
    setStableComparisonState(comparisonState, rowChanges);
    state.comparisonPayload = payload;
    renderComparisonGroups(payload);
    byId("comparisonTable").hidden = false;
    renderComparisonRowTrace(payload, requestVersion);
  } catch (error) {
    if (requestVersion !== state.comparisonRequestVersion) return;
    state.comparisonPayload = null;
    byId("comparisonFilterEmpty").hidden = true;
    renderComparisonState(`결과 비교를 읽지 못했습니다: ${error.message}`);
  }
}

async function renderRuns() {
  const requestVersion = ++state.runsRequestVersion;
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
    if (requestVersion !== state.runsRequestVersion) return;
    runs.forEach((run) => {
      const row = document.createElement("tr");
      const id = document.createElement("td");
      const scope = run.selected_excel_rows ? ` · ${selectedRowsSummary(run.selected_excel_rows)}` : "";
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
      const selectionNotice = state.comparisonSelectionNotice;
      state.comparisonSelectionNotice = "";
      setStableComparisonState(
        `${selectionNotice ? `${selectionNotice} ` : ""}비교하려면 완료 결과가 두 개 이상 필요합니다.`
      );
      if (state.comparisonRowFocus) {
        showComparisonRowTrace(
          state.comparisonRowFocus,
          "이 행을 비교하려면 성공해 공개된 결과가 두 개 이상 필요합니다."
        );
      }
      state.comparisonPayload = null;
      byId("comparisonFilterEmpty").hidden = true;
      byId("comparisonTable").hidden = true;
    }
  } catch (error) {
    if (requestVersion !== state.runsRequestVersion) return;
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = `처리 이력을 읽지 못했습니다: ${error.message}`;
    row.appendChild(cell);
    body.appendChild(row);
    state.comparisonPayload = null;
    byId("comparisonFilterEmpty").hidden = true;
    setStableComparisonState(`완료 결과를 읽지 못했습니다: ${error.message}`);
    byId("comparisonTable").hidden = true;
  }
}

function switchView(name, refreshRuns = true, userInitiated = false) {
  if (userInitiated) {
    if (!restoreResultContextParent()) leavePendingResultContext();
  }
  state.currentView = name;
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === name);
  });
  byId("overviewView").hidden = name !== "overview";
  byId("qualityView").hidden = name !== "quality";
  byId("runsView").hidden = name !== "runs";
  if (name === "runs" && refreshRuns) renderRuns();
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

async function loadCurrentResult({initial = false} = {}) {
  const requestVersion = ++state.resultRequestVersion;
  state.pendingComparedResultRequest = false;
  const refreshButton = byId("refreshCurrentResult");
  const previousFocus = state.viewFocus
    ? {...state.viewFocus}
    : state.selected
      ? {
          observedDate: state.selected.observed_date,
          stockCode: state.selected.stock_code,
        }
      : state.resultContext?.focus || null;
  if (!initial) {
    refreshButton.disabled = true;
    refreshButton.textContent = "최신 결과 확인 중";
    setText("currentResultState", "서버에 현재로 등록된 완료 결과를 확인하는 중입니다.");
  }
  try {
    const response = await fetch("/api/result", {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || `HTTP ${response.status}`);
    }
    if (requestVersion !== state.resultRequestVersion) return;
    state.currentResult = payload;
    state.result = payload;
    state.resultContext = null;
    state.displayedResultOrigin = {kind: "current"};
    state.selected = null;
    state.selectedMarker = null;
    byId("productFilter").disabled = false;
    byId("loadingState").hidden = true;
    byId("errorState").hidden = true;
    const badge = byId("resultBadge");
    badge.textContent = "조회 당시 최신 공개 결과";
    badge.className = "status-badge is-ready";
    renderOverview();
    if (previousFocus) {
      const product = payload.products.find(
        (item) => item.observed_date === previousFocus.observedDate
          && item.stock_code === previousFocus.stockCode
      );
      if (product && observedDateMatchesView(product.observed_date)) {
        selectProduct(product);
      } else if (product) {
        clearProductDetail("새로 읽은 현재 결과에는 이전에 보던 상품·날짜가 있지만 현재 원문 날짜 보기 조건 밖입니다.");
      } else {
        state.viewFocus = {...previousFocus};
        showMissingComparedProduct(previousFocus);
      }
    } else {
      clearProductDetail();
    }
    switchView("overview", false);
    setCurrentLookupState(
      `마지막 현재 결과 조회 run ${shortId(payload.run.run_id)} · ${initial ? "처음 연" : "다시 읽은"} 시점 기준`
    );
    syncViewUrl();
  } catch (error) {
    if (requestVersion !== state.resultRequestVersion) return;
    if (initial) {
      setCurrentLookupState(`현재 공개 결과를 읽지 못했습니다: ${error.message}`);
      showError({data_state: "read_error", message: error.message}, 0);
      renderDisplayedResultState();
    } else {
      const cachedRun = state.currentResult?.run?.run_id;
      setCurrentLookupState(
        `최근 현재 결과 조회 실패 · ${cachedRun ? `마지막 성공 run ${shortId(cachedRun)} 유지` : "성공한 현재 결과 조회 없음"}: ${error.message}`
      );
    }
  } finally {
    if (requestVersion === state.resultRequestVersion) finishCurrentResultRequest();
  }
}

async function loadPinnedResult(view) {
  const requestVersion = ++state.resultRequestVersion;
  state.pendingComparedResultRequest = false;
  const refreshButton = byId("refreshCurrentResult");
  refreshButton.disabled = true;
  refreshButton.textContent = "저장 실행 확인 중";
  setCurrentLookupState(
    `현재 공개 결과는 조회하지 않았습니다. 주소의 run ${shortId(view.runId)}을 확인하는 중입니다.`
  );
  try {
    const query = new URLSearchParams({run_id: view.runId});
    const response = await fetch(`/api/result?${query}`, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload.message || `HTTP ${response.status}`);
      error.payload = payload;
      error.status = response.status;
      throw error;
    }
    if (requestVersion !== state.resultRequestVersion) return;
    if (payload.result?.run?.run_id !== view.runId) {
      throw new Error("요청한 실행과 다른 저장 결과가 반환됐습니다.");
    }

    state.result = payload.result;
    state.currentResult = null;
    state.resultContext = null;
    state.displayedResultOrigin = {
      kind: "pinned",
      isCurrentAtRead: Boolean(payload.is_current),
    };
    state.selected = null;
    state.selectedMarker = null;
    byId("productFilter").disabled = false;
    byId("loadingState").hidden = true;
    byId("errorState").hidden = true;
    renderOverview();
    let focusProduct = null;
    if (view.focus) {
      const product = payload.result.products.find(
        (item) => item.observed_date === view.focus.observedDate
          && item.stock_code === view.focus.stockCode
      );
      if (product) {
        focusProduct = product;
        selectProduct(product);
      } else {
        state.viewFocus = {...view.focus};
        showMissingComparedProduct(view.focus);
      }
    } else {
      clearProductDetail();
    }
    switchView("overview", false);
    setCurrentLookupState(
      `현재 공개 결과는 아직 조회하지 않았습니다. 주소의 저장 run ${shortId(view.runId)}을 고정 표시합니다.`
    );
    syncViewUrl();
    if (focusProduct) revealPinnedFocusOnSingleColumn();
  } catch (error) {
    if (requestVersion !== state.resultRequestVersion) return;
    state.result = null;
    state.displayedResultOrigin = null;
    setCurrentLookupState(
      `현재 공개 결과는 조회하지 않았습니다. 주소의 저장 run ${shortId(view.runId)}을 열지 못했습니다.`
    );
    showError(
      error.payload || {data_state: "read_error", message: error.message},
      error.status || 0
    );
    renderDisplayedResultState();
  } finally {
    if (requestVersion === state.resultRequestVersion) finishCurrentResultRequest();
  }
}

function initializeView() {
  let initialView;
  try {
    initialView = parseInitialViewQuery();
  } catch (error) {
    setCurrentLookupState("현재 공개 결과는 조회하지 않았습니다. 보기 주소의 조건을 확인하세요.");
    showError({data_state: "invalid_view", message: error.message}, 0);
    renderDisplayedResultState();
    finishCurrentResultRequest();
    return;
  }

  if (initialView.kind === "current") {
    readViewDateFilter();
    loadCurrentResult({initial: true});
    return;
  }
  applyPinnedViewInputs(initialView);
  loadPinnedResult(initialView);
}

function applyViewDateFilter() {
  if (state.resultContext) return;
  const wasReadingComparedResult = state.pendingComparedResultRequest;
  invalidatePendingResultRequest();
  if (wasReadingComparedResult) {
    renderComparisonState(
      state.comparisonStableState,
      state.comparisonStableRowChanges
    );
  }
  readViewDateFilter();
  renderViewDateFilterState();
  if (state.viewFocus && (
    state.viewDateFilter.error
    || !observedDateMatchesView(state.viewFocus.observedDate)
  )) {
    clearProductDetail(
      state.viewDateFilter.error
        ? "날짜 보기 조건을 고치면 상품 근거를 다시 선택할 수 있습니다."
        : "선택했던 상품·날짜는 현재 원문 날짜 보기 조건 밖입니다. 0이나 데이터 삭제를 뜻하지 않습니다."
    );
  }
  if (state.result) renderProductRows();
  if (state.comparisonPayload) renderComparisonGroups(state.comparisonPayload);
  syncViewUrl();
}

function applyProductQuery() {
  if (state.resultContext) return;
  state.viewProductQueryError = productQueryValidationError(
    byId("productFilter").value
  );
  renderProductQueryState();
  if (state.viewFocus && !focusMatchesCurrentView(state.viewFocus)) {
    clearProductDetail(
      state.viewProductQueryError
        ? "상품 검색 조건을 고치면 상품 근거를 다시 선택할 수 있습니다."
        : "선택했던 상품·날짜는 현재 상품 검색 조건 밖입니다. 0이나 데이터 삭제를 뜻하지 않습니다."
    );
  }
  if (state.result) renderProductRows();
  syncViewUrl();
}

function applyProductSort() {
  if (state.result) renderProductRows();
  syncViewUrl();
}

document.querySelectorAll(".nav-button").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view, true, true));
});
document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});
byId("productFilter").addEventListener("input", applyProductQuery);
byId("productSort").addEventListener("change", applyProductSort);
byId("baseResult").addEventListener("change", changeComparisonSelection);
byId("currentResult").addEventListener("change", changeComparisonSelection);
byId("comparisonProductFilter").addEventListener("input", () => {
  if (state.comparisonPayload) renderComparisonGroups(state.comparisonPayload);
});
byId("observedDateStart").addEventListener("input", applyViewDateFilter);
byId("observedDateEnd").addEventListener("input", applyViewDateFilter);
byId("clearObservedDateFilter").addEventListener("click", () => {
  byId("observedDateStart").value = "";
  byId("observedDateEnd").value = "";
  applyViewDateFilter();
});
byId("compareButton").addEventListener("click", renderComparison);
byId("backToComparison").addEventListener("click", returnToComparison);
byId("refreshCurrentResult").addEventListener("click", () => loadCurrentResult());

initializeView();
