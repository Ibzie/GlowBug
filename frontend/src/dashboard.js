import "gridstack/dist/gridstack.min.css";
import "./dashboard.css";

import Chart from "chart.js/auto";
import { GridStack } from "gridstack";

const bootstrapElement = document.getElementById("dashboard-bootstrap");
const page = document.getElementById("dashboard-page");

if (bootstrapElement instanceof HTMLScriptElement && page instanceof HTMLElement) {
    const bootstrap = JSON.parse(bootstrapElement.textContent || "{}");
    const dashboard = bootstrap.dashboard || { id: "", name: "", widgets: [] };
    const catalog = new Map((bootstrap.catalog || []).map((item) => [item.type, item]));
    const widgets = new Map((dashboard.widgets || []).map((widget) => [widget.id, widget]));
    const start = page.dataset.start || bootstrap.start || "";
    const end = page.dataset.end || bootstrap.end || "";
    const dashboardId = page.dataset.dashboardId || dashboard.id;

    const gridElement = document.getElementById("dashboard-grid");
    const grid =
        gridElement instanceof HTMLElement
            ? GridStack.init(
                  {
                      column: 12,
                      cellHeight: 78,
                      margin: 8,
                      float: true,
                      staticGrid: true,
                      draggable: { handle: ".widget-drag-handle" },
                      resizable: { handles: "se" },
                      columnOpts: {
                          breakpointForWindow: true,
                          breakpoints: [{ w: 700, c: 1 }],
                      },
                  },
                  gridElement,
              )
            : null;

    const chartInstances = new Map();
    const statusElement = document.getElementById("dashboard-status");
    const editButton = document.getElementById("dashboard-edit-toggle");
    const addButton = document.getElementById("dashboard-add-widget");
    const emptyAddButton = document.getElementById("dashboard-empty-add");
    const rangeSelect = document.getElementById("dashboard-range");

    const addDialog = document.getElementById("add-widget-dialog");
    const editDialog = document.getElementById("edit-widget-dialog");
    const addForm = document.getElementById("add-widget-form");
    const editForm = document.getElementById("edit-widget-form");
    const addType = document.getElementById("add-widget-type");
    const addTitle = document.getElementById("add-widget-title");
    const addVisualization = document.getElementById("add-widget-visualization");
    const addCurrencyMode = document.getElementById("add-widget-currency-mode");
    const addDescription = document.getElementById("add-widget-description");
    const addError = document.getElementById("add-widget-error");
    const editId = document.getElementById("edit-widget-id");
    const editTypeLabel = document.getElementById("edit-widget-type-label");
    const editTitle = document.getElementById("edit-widget-title");
    const editVisualization = document.getElementById("edit-widget-visualization");
    const editCurrencyMode = document.getElementById("edit-widget-currency-mode");
    const editError = document.getElementById("edit-widget-error");

    let editing = false;
    let layoutTimer = null;
    let statusTimer = null;

    const visualisationLabels = {
        number: "Number",
        bar: "Bar chart",
        line: "Line chart",
        table: "Table",
    };

    const palette = ["#2f6fed", "#0f7b5f", "#b42318", "#8a5a00", "#7a5af8", "#0e7490"];

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (character) => {
            const replacements = {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#39;",
            };
            return replacements[character] || character;
        });
    }

    function formatMoney(value, currency) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return `${value} ${currency}`;
        }
        try {
            return new Intl.NumberFormat(undefined, {
                style: "currency",
                currency,
                maximumFractionDigits: 2,
            }).format(number);
        } catch {
            return `${number} ${currency}`;
        }
    }

    function formatCompact(value, currency) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return String(value);
        }
        try {
            return new Intl.NumberFormat(undefined, {
                style: "currency",
                currency,
                notation: "compact",
                maximumFractionDigits: 1,
            }).format(number);
        } catch {
            return String(number);
        }
    }

    function formatRate(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return String(value);
        }
        return new Intl.NumberFormat(undefined, {
            maximumFractionDigits: 6,
        }).format(number);
    }

    function setStatus(message, tone = "info") {
        if (!(statusElement instanceof HTMLElement)) {
            return;
        }
        statusElement.textContent = message;
        statusElement.dataset.tone = tone;
        window.clearTimeout(statusTimer);
        if (message) {
            statusTimer = window.setTimeout(() => {
                statusElement.textContent = "";
            }, 3000);
        }
    }

    function appendWarnings(body, warnings) {
        const unique = [...new Set(warnings || [])];
        unique.slice(0, 4).forEach((warning) => {
            const element = document.createElement("p");
            element.className = "warning-text";
            element.textContent = warning;
            body.appendChild(element);
        });
    }

    function renderNumber(body, widget, payload) {
        const wrapper = document.createElement("div");
        wrapper.className = "widget-number";

        if (widget.type === "fx_rate") {
            if (payload.value == null) {
                wrapper.innerHTML = `<span class="number-main muted">No rate available</span>`;
            } else {
                wrapper.innerHTML = `<span class="number-main">1 ${escapeHtml(payload.base)} = ${escapeHtml(
                    formatRate(payload.value),
                )} ${escapeHtml(payload.quote)}</span>
                    <span class="number-sub">Rate date ${escapeHtml(payload.rate_date)}</span>`;
            }
        } else {
            const main = formatMoney(payload.value, payload.primary_currency);
            wrapper.innerHTML = `<span class="number-main">${escapeHtml(main)}</span>`;
            if (payload.secondary_value != null && payload.secondary_currency) {
                wrapper.innerHTML += `<span class="number-sub">${escapeHtml(
                    formatMoney(payload.secondary_value, payload.secondary_currency),
                )}</span>`;
            }
            if (payload.subtitle) {
                wrapper.innerHTML += `<span class="number-sub">${escapeHtml(payload.subtitle)}</span>`;
            }
            if (payload.complete === false) {
                wrapper.innerHTML += `<span class="warning-text">Incomplete — missing rate</span>`;
            }
        }

        body.replaceChildren(wrapper);
        appendWarnings(body, payload.warnings);
    }

    function styleDataset(dataset, index, chartType) {
        const color = palette[index % palette.length];
        if (chartType === "doughnut" || chartType === "pie") {
            return {
                ...dataset,
                backgroundColor: (dataset.data || []).map((_, itemIndex) => palette[itemIndex % palette.length]),
                borderColor: "#ffffff",
                borderWidth: 2,
            };
        }
        if (dataset.label === "Income") {
            return {
                ...dataset,
                backgroundColor: "rgba(15, 123, 95, 0.65)",
                borderColor: "#0f7b5f",
                borderWidth: 1,
                borderRadius: chartType === "bar" ? 6 : 0,
                maxBarThickness: 44,
            };
        }
        if (dataset.label === "Expenses") {
            return {
                ...dataset,
                backgroundColor: "rgba(180, 35, 24, 0.55)",
                borderColor: "#b42318",
                borderWidth: 1,
                borderRadius: chartType === "bar" ? 6 : 0,
                maxBarThickness: 44,
            };
        }

        return {
            ...dataset,
            backgroundColor: chartType === "bar" ? `${color}99` : "transparent",
            borderColor: color,
            borderWidth: 2,
            borderRadius: chartType === "bar" ? 6 : 0,
            maxBarThickness: 44,
            tension: 0.3,
            pointRadius: 2,
            pointHoverRadius: 4,
        };
    }

    function chartOptions(payload) {
        if (payload.chart === "doughnut" || payload.chart === "pie") {
            return {
                responsive: true,
                maintainAspectRatio: false,
                cutout: payload.chart === "doughnut" ? "62%" : 0,
                plugins: {
                    legend: {
                        display: true,
                        position: "bottom",
                        labels: { boxWidth: 10, usePointStyle: true, padding: 12 },
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                return `${context.label}: ${formatMoney(context.parsed, payload.currency)}`;
                            },
                        },
                    },
                },
            };
        }

        return {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    display: (payload.datasets || []).length > 1,
                    position: "bottom",
                    labels: { boxWidth: 10, usePointStyle: true, padding: 14 },
                },
                tooltip: {
                    callbacks: {
                        label(context) {
                            return `${context.dataset.label}: ${formatMoney(
                                context.parsed.y,
                                payload.currency,
                            )}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { maxRotation: 0, autoSkip: true },
                },
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback(value) {
                            return formatCompact(value, payload.currency);
                        },
                    },
                },
            },
        };
    }

    function renderChart(body, widget, payload) {
        const wrapper = document.createElement("div");
        wrapper.className = "chart-wrap";
        const canvas = document.createElement("canvas");
        wrapper.appendChild(canvas);
        body.replaceChildren(wrapper);

        const context = canvas.getContext("2d");
        if (!context) {
            return;
        }

        const existing = chartInstances.get(widget.id);
        if (existing) {
            existing.destroy();
        }

        const chart = new Chart(context, {
            type: payload.chart,
            data: {
                labels: payload.labels || [],
                datasets: (payload.datasets || []).map((dataset, index) =>
                    styleDataset(dataset, index, payload.chart),
                ),
            },
            options: chartOptions(payload),
        });

        chartInstances.set(widget.id, chart);
        appendWarnings(body, payload.warnings);
    }

    function renderTable(body, payload) {
        const rows = payload.rows || [];
        if (rows.length === 0) {
            body.innerHTML = `<div class="widget-empty">No transactions in this range.</div>`;
            appendWarnings(body, payload.warnings);
            return;
        }

        const wrapper = document.createElement("div");
        wrapper.className = "widget-table-wrap";
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        (payload.columns || []).forEach((column) => {
            const th = document.createElement("th");
            th.textContent = column;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement("tbody");
        rows.forEach((row) => {
            const tr = document.createElement("tr");
            row.forEach((cell, index) => {
                const td = document.createElement("td");
                td.textContent = cell;
                if (index === row.length - 1) {
                    td.className = "right";
                }
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrapper.appendChild(table);
        body.replaceChildren(wrapper);
        appendWarnings(body, payload.warnings);
    }

    function renderWidget(body, widget, payload) {
        if (payload.kind === "number") {
            renderNumber(body, widget, payload);
            return;
        }
        if (payload.kind === "chart") {
            renderChart(body, widget, payload);
            return;
        }
        if (payload.kind === "table") {
            renderTable(body, payload);
            return;
        }
        body.innerHTML = `<div class="widget-empty">${escapeHtml(
            payload.message || "Could not render this widget.",
        )}</div>`;
    }

    async function loadWidget(widget) {
        const card = document.querySelector(`[data-widget-id="${widget.id}"]`);
        if (!(card instanceof HTMLElement)) {
            return;
        }
        const body = card.querySelector("[data-widget-body]");
        if (!(body instanceof HTMLElement)) {
            return;
        }

        body.innerHTML = `<div class="widget-loading">Loading…</div>`;
        try {
            const response = await fetch(
                `/api/widgets/${widget.id}/data?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
                { headers: { Accept: "application/json" } },
            );
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            renderWidget(body, widget, await response.json());
        } catch (error) {
            body.innerHTML = `<div class="widget-error">Could not load this widget. ${escapeHtml(
                error.message,
            )}</div>`;
        }
    }

    function loadAllWidgets() {
        return Promise.all([...widgets.values()].map((widget) => loadWidget(widget)));
    }

    function updateVisualisationOptions(select, item, current) {
        if (!(select instanceof HTMLSelectElement)) {
            return;
        }
        select.innerHTML = "";
        (item?.visualizations || []).forEach((visualization) => {
            const option = document.createElement("option");
            option.value = visualization;
            option.textContent = visualisationLabels[visualization] || visualization;
            if (visualization === current) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    }

    function updateCurrencyMode(select, item, current) {
        if (!(select instanceof HTMLSelectElement)) {
            return;
        }
        const dual = select.querySelector('option[value="dual"]');
        const allowDual = item?.type === "net_worth";
        if (dual instanceof HTMLOptionElement) {
            dual.disabled = !allowDual;
        }
        select.value = allowDual && current === "dual" ? "dual" : "primary";
    }

    function showFormError(element, message) {
        if (!(element instanceof HTMLElement)) {
            return;
        }
        element.textContent = message;
        element.hidden = !message;
    }

    function toggleEditing() {
        editing = !editing;
        document.body.classList.toggle("dashboard-editing", editing);
        if (grid) {
            grid.setStatic(!editing);
        }
        if (editButton instanceof HTMLButtonElement) {
            editButton.textContent = editing ? "Done" : "Edit dashboard";
            editButton.setAttribute("aria-pressed", String(editing));
        }
        if (addButton instanceof HTMLButtonElement) {
            addButton.hidden = !editing;
        }
        if (!editing) {
            setStatus("");
        }
    }

    async function saveLayout() {
        if (!grid || !editing) {
            return;
        }
        const items = grid.save(false).map((node) => ({
            id: node.id,
            x: node.x,
            y: node.y,
            w: node.w,
            h: node.h,
        }));

        try {
            const response = await fetch(`/api/dashboards/${dashboardId}/layout`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ items }),
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            setStatus("Layout saved.", "success");
        } catch {
            setStatus("Could not save the layout.", "error");
        }
    }

    function scheduleLayoutSave() {
        window.clearTimeout(layoutTimer);
        layoutTimer = window.setTimeout(saveLayout, 600);
    }

    function openAddDialog() {
        if (!(addDialog instanceof HTMLDialogElement)) {
            return;
        }
        showFormError(addError, "");
        updateAddForm();
        addDialog.showModal();
    }

    function updateAddForm() {
        if (!(addType instanceof HTMLSelectElement)) {
            return;
        }
        const item = catalog.get(addType.value);
        if (!item) {
            return;
        }
        if (addDescription instanceof HTMLElement) {
            addDescription.textContent = item.description;
        }
        if (addTitle instanceof HTMLInputElement) {
            addTitle.value = item.default_title;
        }
        updateVisualisationOptions(addVisualization, item, item.default_config?.visualization);
        updateCurrencyMode(addCurrencyMode, item, item.default_config?.currency_mode);
    }

    function openEditDialog(widgetId) {
        const widget = widgets.get(widgetId);
        const item = widget ? catalog.get(widget.type) : null;
        if (!widget || !item || !(editDialog instanceof HTMLDialogElement)) {
            return;
        }

        showFormError(editError, "");
        if (editId instanceof HTMLInputElement) {
            editId.value = widget.id;
        }
        if (editTypeLabel instanceof HTMLElement) {
            editTypeLabel.textContent = item.name;
        }
        if (editTitle instanceof HTMLInputElement) {
            editTitle.value = widget.title;
        }
        updateVisualisationOptions(
            editVisualization,
            item,
            widget.config?.visualization || item.default_config?.visualization,
        );
        updateCurrencyMode(
            editCurrencyMode,
            item,
            widget.config?.currency_mode || item.default_config?.currency_mode,
        );
        editDialog.showModal();
    }

    async function postJson(url, method, body) {
        const response = await fetch(url, {
            method,
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(body),
        });
        const payload = response.status === 204 ? null : await response.json().catch(() => null);
        if (!response.ok) {
            throw new Error(payload?.detail || `HTTP ${response.status}`);
        }
        return payload;
    }

    if (editButton instanceof HTMLButtonElement) {
        editButton.addEventListener("click", toggleEditing);
    }
    if (addButton instanceof HTMLButtonElement) {
        addButton.addEventListener("click", openAddDialog);
    }
    if (emptyAddButton instanceof HTMLButtonElement) {
        emptyAddButton.addEventListener("click", () => {
            if (!editing) {
                toggleEditing();
            }
            openAddDialog();
        });
    }
    if (rangeSelect instanceof HTMLSelectElement) {
        rangeSelect.addEventListener("change", () => {
            window.location.href = `/?range=${encodeURIComponent(rangeSelect.value)}`;
        });
    }

    document.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }

        const closeButton = target.closest("[data-close-dialog]");
        if (closeButton) {
            closeButton.closest("dialog")?.close();
            return;
        }

        const editWidgetButton = target.closest("[data-widget-edit]");
        if (editWidgetButton) {
            const card = editWidgetButton.closest("[data-widget-id]");
            if (card instanceof HTMLElement) {
                openEditDialog(card.dataset.widgetId || "");
            }
            return;
        }

        const deleteWidgetButton = target.closest("[data-widget-delete]");
        if (deleteWidgetButton) {
            const card = deleteWidgetButton.closest("[data-widget-id]");
            if (!(card instanceof HTMLElement)) {
                return;
            }
            const widgetId = card.dataset.widgetId || "";
            if (!window.confirm("Remove this widget from the dashboard?")) {
                return;
            }
            postJson(`/api/widgets/${widgetId}`, "DELETE", {})
                .then(() => window.location.reload())
                .catch((error) => setStatus(`Could not remove widget: ${error.message}`, "error"));
        }
    });

    if (addType instanceof HTMLSelectElement) {
        addType.addEventListener("change", updateAddForm);
    }

    if (addForm instanceof HTMLFormElement) {
        addForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const item = addType instanceof HTMLSelectElement ? catalog.get(addType.value) : null;
            if (!item) {
                return;
            }
            const title = addTitle instanceof HTMLInputElement ? addTitle.value.trim() : "";
            const visualization =
                addVisualization instanceof HTMLSelectElement ? addVisualization.value : "";
            const currencyMode =
                addCurrencyMode instanceof HTMLSelectElement ? addCurrencyMode.value : "primary";
            try {
                await postJson(`/api/dashboards/${dashboardId}/widgets`, "POST", {
                    type: item.type,
                    title: title || item.default_title,
                    config: { visualization, currency_mode: currencyMode },
                });
                window.location.reload();
            } catch (error) {
                showFormError(addError, error.message);
            }
        });
    }

    if (editForm instanceof HTMLFormElement) {
        editForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const widgetId = editId instanceof HTMLInputElement ? editId.value : "";
            const widget = widgets.get(widgetId);
            if (!widget) {
                return;
            }
            const title = editTitle instanceof HTMLInputElement ? editTitle.value.trim() : "";
            const visualization =
                editVisualization instanceof HTMLSelectElement ? editVisualization.value : "";
            const currencyMode =
                editCurrencyMode instanceof HTMLSelectElement ? editCurrencyMode.value : "primary";
            try {
                await postJson(`/api/widgets/${widgetId}`, "PATCH", {
                    title: title || widget.title,
                    config: {
                        ...(widget.config || {}),
                        visualization,
                        currency_mode: currencyMode,
                    },
                });
                window.location.reload();
            } catch (error) {
                showFormError(editError, error.message);
            }
        });
    }

    if (grid) {
        grid.on("change", () => {
            if (editing) {
                scheduleLayoutSave();
            }
        });
    }

    loadAllWidgets();
}
