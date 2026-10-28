function presetNumber(form, name, fallback) {
  const input = form.querySelector(`[name="${name}"]`);
  const value = parseFloat(input?.value || "");
  return Number.isFinite(value) ? value : fallback;
}

function updatePresetMetrics() {
  const form = document.querySelector("[data-preset-form]");
  if (!form) return;

  const trimWidth = presetNumber(form, "trim_width_in", 3.5);
  const trimHeight = presetNumber(form, "trim_height_in", 5.0);
  const marginLeft = presetNumber(form, "margin_left_in", 0.14);
  const marginRight = presetNumber(form, "margin_right_in", 0.14);
  const marginTop = presetNumber(form, "margin_top_in", 0.14);
  const marginBottom = presetNumber(form, "margin_bottom_in", 0.14);
  const columnCount = Math.max(1, parseInt(form.querySelector('[name="meeting_table_column_count"]')?.value || "28", 10) || 28);
  const columnGap = Math.max(0, parseInt(form.querySelector('[name="meeting_table_column_gap_px"]')?.value || "6", 10) || 6);
  const previewScale = presetNumber(form, "screen_preview_scale", 220);
  const fontFamily = form.querySelector('[name="font_family"]')?.value || "Arial";

  const printableWidth = Math.max(0.1, trimWidth - marginLeft - marginRight);
  const printableHeight = Math.max(0.1, trimHeight - marginTop - marginBottom);
  const previewWidth = printableWidth * previewScale;
  const approxColumnWidth = printableWidth / columnCount;

  const setText = (selector, text) => {
    const el = document.querySelector(selector);
    if (el) el.textContent = text;
  };

  setText('[data-metric="printable-width"]', `${printableWidth.toFixed(2)} in`);
  setText('[data-metric="printable-height"]', `${printableHeight.toFixed(2)} in`);
  setText('[data-metric="column-width"]', `${approxColumnWidth.toFixed(3)} in`);
  setText('[data-metric="preview-width"]', `${previewWidth.toFixed(0)} px`);
  setText('[data-metric="font-family"]', fontFamily);

  const preview = document.querySelector("[data-preset-preview]");
  const printable = document.querySelector("[data-preset-printable]");
  const columns = document.querySelector("[data-preset-columns]");
  if (!preview || !printable || !columns) return;

  preview.dataset.trimWidth = String(trimWidth);
  preview.dataset.trimHeight = String(trimHeight);
  preview.dataset.leftMargin = String(marginLeft);
  preview.dataset.rightMargin = String(marginRight);
  preview.dataset.topMargin = String(marginTop);
  preview.dataset.bottomMargin = String(marginBottom);
  preview.dataset.columnCount = String(columnCount);

  const widthScale = 180 / Math.max(0.1, trimWidth);
  const heightScale = 240 / Math.max(0.1, trimHeight);
  preview.style.setProperty("--preset-page-width", `${trimWidth * widthScale}px`);
  preview.style.setProperty("--preset-page-height", `${trimHeight * heightScale}px`);
  printable.style.left = `${marginLeft * widthScale}px`;
  printable.style.right = `${marginRight * widthScale}px`;
  printable.style.top = `${marginTop * heightScale}px`;
  printable.style.bottom = `${marginBottom * heightScale}px`;
  columns.style.setProperty("--preset-columns", String(columnCount));
  columns.style.setProperty("--preset-column-gap", `${columnGap}px`);
}

function updateColorPreviewFields() {
  document.querySelectorAll("[data-color-field]").forEach((field) => {
    const input = field.querySelector("[data-color-input]");
    const value = field.querySelector("[data-color-value-input]");
    if (!input) return;
    const color = input.value || "#ffffff";
    field.style.setProperty("--color-preview", color);
    if (value && value !== document.activeElement) value.value = color;
  });
}

function normalizeHexColor(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) return null;
  const withHash = trimmed.startsWith("#") ? trimmed : `#${trimmed}`;
  return /^#[0-9a-fA-F]{6}$/.test(withHash) ? withHash.toLowerCase() : null;
}

window.addEventListener("DOMContentLoaded", () => {
  updatePresetMetrics();
  updateColorPreviewFields();
  const form = document.querySelector("[data-preset-form]");
  if (!form) return;

  form.querySelectorAll("[data-color-field]").forEach((field) => {
    const colorInput = field.querySelector("[data-color-input]");
    const textInput = field.querySelector("[data-color-value-input]");
    if (!colorInput || !textInput) return;

    const commitTextValue = () => {
      const normalized = normalizeHexColor(textInput.value);
      if (!normalized) {
        textInput.value = colorInput.value;
        return;
      }
      if (colorInput.value !== normalized) {
        colorInput.value = normalized;
      }
      updateColorPreviewFields();
    };

    textInput.addEventListener("input", () => {
      const normalized = normalizeHexColor(textInput.value);
      if (!normalized) return;
      colorInput.value = normalized;
      updateColorPreviewFields();
    });
    textInput.addEventListener("blur", commitTextValue);
    textInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      commitTextValue();
    });
  });

  form.addEventListener("input", () => {
    updatePresetMetrics();
    updateColorPreviewFields();
  });
  form.addEventListener("change", () => {
    updatePresetMetrics();
    updateColorPreviewFields();
  });
});
