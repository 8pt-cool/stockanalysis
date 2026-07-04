const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  secret: localStorage.getItem("tradeReviewSecret") || "",
};

function today() {
  return new Date().toISOString().slice(0, 10);
}

function setOutput(selector, value) {
  const node = $(selector);
  node.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-App-Secret": state.secret,
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function renderTrades(trades) {
  const list = $("#tradeList");
  list.innerHTML = "";
  if (!trades.length) {
    list.innerHTML = '<p class="note">暂无交易记录。</p>';
    return;
  }
  for (const trade of trades) {
    const side = trade.side === "buy" ? "买入" : "卖出";
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `
      <strong>${trade.stock_code} ${trade.stock_name || ""} · ${side}</strong>
      <div class="meta">${trade.trade_date} · ${trade.price} × ${trade.quantity} · 金额 ${Number(trade.amount).toFixed(2)}</div>
      <div class="meta">${trade.reason || "未填写理由"}</div>
    `;
    list.appendChild(item);
  }
}

function renderWatchlist(items) {
  const list = $("#watchList");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<p class="note">暂无自选股。</p>';
    return;
  }
  for (const item of items) {
    const node = document.createElement("div");
    node.className = "item";
    node.innerHTML = `
      <strong>${item.stock_code} ${item.stock_name || ""}</strong>
      <div class="meta">${item.strategy_type || "未设置策略"} · 支撑 ${item.support_price || "-"} · 压力 ${item.resistance_price || "-"} · 止损 ${item.stop_loss || "-"}</div>
      <div class="meta">${item.reason || "未填写关注理由"}</div>
    `;
    list.appendChild(node);
  }
}

function parseJsonBlock(text) {
  if (!text) return null;
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) return null;
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch {
    return null;
  }
}

function normalizeSideLabel(side) {
  if (side === "buy") return "买入";
  if (side === "sell") return "卖出";
  return side || "-";
}

function extractFilledTrades(screenshot) {
  const parsed = parseJsonBlock(screenshot.ocr_json || "");
  if (!parsed || parsed.type !== "broker_records") return [];
  return Array.isArray(parsed.filled_trades) ? parsed.filled_trades : [];
}

function extractWatchItems(screenshot) {
  const parsed = parseJsonBlock(screenshot.ocr_json || "");
  if (!parsed || parsed.type !== "watchlist_snapshot") return [];
  return Array.isArray(parsed.items) ? parsed.items : [];
}

function extractPositions(screenshot) {
  const parsed = parseJsonBlock(screenshot.ocr_json || "");
  if (!parsed || parsed.type !== "position_snapshot") return [];
  return Array.isArray(parsed.positions) ? parsed.positions : [];
}

function renderScreenshots(items) {
  const list = $("#screenshotList");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<p class="note">暂无待确认截图。Telegram 发图后点“刷新待确认”。</p>';
    return;
  }
  for (const shot of items) {
    const trades = extractFilledTrades(shot);
    const watchItems = extractWatchItems(shot);
    const positions = extractPositions(shot);
    const isWatchlistShot = watchItems.length > 0;
    const isPositionShot = positions.length > 0;
    const item = document.createElement("div");
    item.className = "item screenshotItem";
    item.dataset.screenshotId = shot.id;
    const status = shot.imported_at ? `已入库 ${shot.imported_at}` : "待确认";
    const rows = trades.map((trade, index) => `
      <div class="tradeConfirm" data-trade-index="${index}">
        <label>代码<input class="confirmCode" inputmode="numeric" value="${escapeHtml(trade.stock_code)}" placeholder="必填" /></label>
        <label>名称<input class="confirmName" value="${escapeHtml(trade.stock_name)}" /></label>
        <label>方向<input class="confirmSide" value="${escapeHtml(trade.side)}" /></label>
        <label>价格<input class="confirmPrice" type="number" step="0.001" value="${escapeHtml(trade.price)}" /></label>
        <label>数量<input class="confirmQuantity" type="number" step="1" value="${escapeHtml(trade.quantity)}" /></label>
        <label>金额<input class="confirmAmount" type="number" step="0.001" value="${escapeHtml(trade.amount)}" /></label>
        <input class="confirmTime" type="hidden" value="${escapeHtml(trade.time)}" />
        <input class="confirmDate" type="hidden" value="${escapeHtml(trade.trade_date)}" />
      </div>
    `).join("");
    const watchRows = watchItems.map((watch, index) => `
      <div class="tradeConfirm watchConfirm" data-watch-index="${index}">
        <label>代码<input class="watchCode" inputmode="numeric" value="${escapeHtml(watch.stock_code)}" placeholder="必填" /></label>
        <label>名称<input class="watchName" value="${escapeHtml(watch.stock_name)}" /></label>
        <label>涨跌幅<input class="watchPct" value="${escapeHtml(watch.pct_change)}" /></label>
        <label>现价<input class="watchPrice" type="number" step="0.001" value="${escapeHtml(watch.last_price)}" /></label>
      </div>
    `).join("");
    const positionRows = positions.map((position, index) => `
      <div class="tradeConfirm positionConfirm" data-position-index="${index}">
        <label>代码<input class="positionCode" inputmode="numeric" value="${escapeHtml(position.stock_code)}" placeholder="必填" /></label>
        <label>名称<input class="positionName" value="${escapeHtml(position.stock_name)}" /></label>
        <label>数量<input class="positionQuantity" type="number" step="1" value="${escapeHtml(position.quantity)}" placeholder="必填" /></label>
        <label>成本<input class="positionCost" type="number" step="0.001" value="${escapeHtml(position.cost_price)}" /></label>
        <label>现价<input class="positionLast" type="number" step="0.001" value="${escapeHtml(position.last_price)}" /></label>
        <label>市值<input class="positionMarketValue" type="number" step="0.001" value="${escapeHtml(position.market_value)}" /></label>
      </div>
    `).join("");
    item.innerHTML = `
      <strong>${escapeHtml(shot.image_type || "screenshot")} · ${escapeHtml(status)}</strong>
      <div class="meta">${escapeHtml(shot.created_at)} · ${trades.length} 条真实成交 · ${watchItems.length} 条自选股 · ${positions.length} 条持仓</div>
      ${rows || ""}
      ${watchRows || ""}
      ${positionRows || ""}
      ${rows || watchRows || positionRows ? "" : '<p class="note">没有解析到可入库内容。可以展开原文核对。</p>'}
      <div class="actions">
        <button class="confirmImportBtn primary" ${shot.imported_at || !trades.length ? "disabled" : ""}>确认入库</button>
        <button class="watchImportBtn primary" ${shot.imported_at || !isWatchlistShot ? "disabled" : ""}>导入自选</button>
        <button class="positionImportBtn primary" ${shot.imported_at || !isPositionShot ? "disabled" : ""}>导入持仓</button>
        <button class="showRawBtn tiny">查看原文</button>
      </div>
      <pre class="output rawOutput" hidden>${escapeHtml(shot.ocr_json || "")}</pre>
    `;
    list.appendChild(item);
  }
}

async function refreshTrades() {
  const data = await api(`/api/trades?date=${today()}`);
  renderTrades(data.trades);
}

async function refreshWatchlist() {
  const data = await api("/api/watchlist");
  renderWatchlist(data.watchlist);
}

async function refreshScreenshots() {
  const data = await api("/api/screenshots");
  renderScreenshots(data.screenshots);
}

function collectConfirmTrades(item) {
  return Array.from(item.querySelectorAll(".tradeConfirm")).map((row) => ({
    stock_code: row.querySelector(".confirmCode").value.trim(),
    stock_name: row.querySelector(".confirmName").value.trim(),
    side: row.querySelector(".confirmSide").value.trim(),
    price: row.querySelector(".confirmPrice").value,
    quantity: row.querySelector(".confirmQuantity").value,
    amount: row.querySelector(".confirmAmount").value,
    time: row.querySelector(".confirmTime").value,
    trade_date: row.querySelector(".confirmDate").value,
  }));
}

function collectWatchItems(item) {
  return Array.from(item.querySelectorAll(".watchConfirm")).map((row) => ({
    stock_code: row.querySelector(".watchCode").value.trim(),
    stock_name: row.querySelector(".watchName").value.trim(),
    pct_change: row.querySelector(".watchPct").value.trim(),
    last_price: row.querySelector(".watchPrice").value,
  }));
}

function collectPositions(item) {
  return Array.from(item.querySelectorAll(".positionConfirm")).map((row) => ({
    stock_code: row.querySelector(".positionCode").value.trim(),
    stock_name: row.querySelector(".positionName").value.trim(),
    quantity: row.querySelector(".positionQuantity").value,
    cost_price: row.querySelector(".positionCost").value,
    last_price: row.querySelector(".positionLast").value,
    market_value: row.querySelector(".positionMarketValue").value,
  }));
}

function initTabs() {
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".tab").forEach((tab) => tab.classList.remove("active"));
      $$(".tabPage").forEach((page) => page.classList.remove("active"));
      button.classList.add("active");
      $(`#${button.dataset.tab}`).classList.add("active");
    });
  });
}

function initForms() {
  $("#secretInput").value = state.secret;
  $("#saveSecretBtn").addEventListener("click", async () => {
    state.secret = $("#secretInput").value.trim();
    localStorage.setItem("tradeReviewSecret", state.secret);
    await Promise.allSettled([refreshTrades(), refreshWatchlist(), refreshScreenshots()]);
  });

  const dateInput = $('#tradeForm input[name="trade_date"]');
  dateInput.value = today();

  $("#tradeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formData(event.currentTarget);
    await api("/api/trades", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    event.currentTarget.reset();
    dateInput.value = today();
    await refreshTrades();
  });

  $("#watchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const payload = formData(form);
    button.disabled = true;
    button.textContent = "保存中...";
    $("#watchStatus").textContent = "";
    try {
      const data = await api("/api/watchlist", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      form.reset();
      $("#watchStatus").textContent = `已保存：${data.watch.stock_code} ${data.watch.stock_name || ""}`;
      await refreshWatchlist();
    } catch (error) {
      $("#watchStatus").textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "保存自选";
    }
  });

  $("#refreshTradesBtn").addEventListener("click", refreshTrades);
  $("#refreshWatchBtn").addEventListener("click", refreshWatchlist);
  $("#refreshScreenshotsBtn").addEventListener("click", refreshScreenshots);

  $("#reviewBtn").addEventListener("click", async () => {
    setOutput("#aiOutput", "生成中...");
    const reportDate = $("#reportDate").value || today();
    const data = await api("/api/review", {
      method: "POST",
      body: JSON.stringify({ trade_date: reportDate }),
    });
    setOutput("#aiOutput", data.review);
  });

  $("#watchReportBtn").addEventListener("click", async () => {
    setOutput("#aiOutput", "生成中...");
    const reportDate = $("#reportDate").value || today();
    const data = await api("/api/watch-report", {
      method: "POST",
      body: JSON.stringify({ report_date: reportDate }),
    });
    setOutput("#aiOutput", data.report);
  });

  $("#uploadBtn").addEventListener("click", async () => {
    const file = $("#screenshotInput").files[0];
    if (!file) {
      setOutput("#uploadResult", "请先选择截图。");
      return;
    }
    setOutput("#uploadResult", "上传识别中...");
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const data = await api("/api/upload-screenshot", {
          method: "POST",
          body: JSON.stringify({
            image: reader.result,
            image_type: $("#screenshotType").value,
          }),
        });
        setOutput(
          "#uploadResult",
          `识别模式：${data.screenshot.image_type}\n\n${data.screenshot.ai_result}`
        );
        await refreshScreenshots();
      } catch (error) {
        setOutput("#uploadResult", error.message);
      }
    };
    reader.readAsDataURL(file);
  });

  $("#screenshotList").addEventListener("click", async (event) => {
    const item = event.target.closest(".screenshotItem");
    if (!item) return;
    if (event.target.classList.contains("showRawBtn")) {
      const raw = item.querySelector(".rawOutput");
      raw.hidden = !raw.hidden;
      return;
    }
    if (event.target.classList.contains("confirmImportBtn")) {
      event.target.disabled = true;
      event.target.textContent = "入库中...";
      try {
        const data = await api("/api/import-screenshot-trades", {
          method: "POST",
          body: JSON.stringify({
            screenshot_id: item.dataset.screenshotId,
            trades: collectConfirmTrades(item),
          }),
        });
        setOutput(
          "#uploadResult",
          `入库完成：${data.imported.length} 条\n跳过：${data.skipped.length} 条\n\n${JSON.stringify(data, null, 2)}`
        );
        await Promise.allSettled([refreshScreenshots(), refreshTrades()]);
      } catch (error) {
        event.target.disabled = false;
        event.target.textContent = "确认入库";
        setOutput("#uploadResult", error.message);
      }
    }
    if (event.target.classList.contains("watchImportBtn")) {
      event.target.disabled = true;
      event.target.textContent = "导入中...";
      let statusNode = item.querySelector(".importStatus");
      if (!statusNode) {
        statusNode = document.createElement("p");
        statusNode.className = "note statusLine importStatus";
        item.appendChild(statusNode);
      }
      statusNode.textContent = "正在导入自选...";
      try {
        const data = await api("/api/import-watchlist-screenshot", {
          method: "POST",
          body: JSON.stringify({
            screenshot_id: item.dataset.screenshotId,
            items: collectWatchItems(item),
          }),
        });
        setOutput(
          "#uploadResult",
          `自选导入完成：${data.imported.length} 条\n跳过：${data.skipped.length} 条\n\n${JSON.stringify(data, null, 2)}`
        );
        statusNode.textContent = `自选导入完成：${data.imported.length} 条，跳过：${data.skipped.length} 条`;
        event.target.textContent = "已导入";
        await Promise.allSettled([refreshScreenshots(), refreshWatchlist()]);
      } catch (error) {
        event.target.disabled = false;
        event.target.textContent = "导入自选";
        statusNode.textContent = `导入失败：${error.message}`;
        setOutput("#uploadResult", error.message);
      }
    }
    if (event.target.classList.contains("positionImportBtn")) {
      event.target.disabled = true;
      event.target.textContent = "导入中...";
      let statusNode = item.querySelector(".importStatus");
      if (!statusNode) {
        statusNode = document.createElement("p");
        statusNode.className = "note statusLine importStatus";
        item.appendChild(statusNode);
      }
      statusNode.textContent = "正在导入持仓...";
      try {
        const data = await api("/api/import-position-screenshot", {
          method: "POST",
          body: JSON.stringify({
            screenshot_id: item.dataset.screenshotId,
            positions: collectPositions(item),
          }),
        });
        setOutput(
          "#uploadResult",
          `持仓导入完成：${data.imported.length} 条\n跳过：${data.skipped.length} 条\n\n${JSON.stringify(data, null, 2)}`
        );
        statusNode.textContent = `持仓导入完成：${data.imported.length} 条，跳过：${data.skipped.length} 条`;
        event.target.textContent = "已导入";
        await refreshScreenshots();
      } catch (error) {
        event.target.disabled = false;
        event.target.textContent = "导入持仓";
        statusNode.textContent = `导入失败：${error.message}`;
        setOutput("#uploadResult", error.message);
      }
    }
  });
}

async function init() {
  initTabs();
  initForms();
  $("#reportDate").value = today();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  if (state.secret) {
    await Promise.allSettled([refreshTrades(), refreshWatchlist(), refreshScreenshots()]);
  }
}

init().catch((error) => {
  setOutput("#aiOutput", error.message);
});
