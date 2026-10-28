function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function parseRowFontSizePt(rawValue) {
  const value = String(rawValue || "").trim().toLowerCase();
  if (!value) return null;
  const normalized = value.endsWith("pt") ? value.slice(0, -2).trim() : value;
  if (!/^\d+(?:\.\d+)?$/.test(normalized)) return null;
  const parsed = parseFloat(normalized);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.round(parsed * 10) / 10;
}

function formatRowFontSizePt(value) {
  return value == null ? "" : value.toFixed(1);
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function cssStyleText(node) {
  if (!node || node.nodeType !== Node.ELEMENT_NODE) return "";
  const attr = String(node.getAttribute("style") || "");
  const inline = node.style
    ? [node.style.fontStyle, node.style.fontWeight, node.style.textDecoration, node.style.textDecorationLine].join(" ")
    : "";
  return `${attr} ${inline}`.toLowerCase();
}

function styleLooksItalic(node) {
  const fontStyle = String(node?.style?.fontStyle || "").toLowerCase();
  if (fontStyle === "italic" || fontStyle === "oblique") return true;
  return /font-style\s*:\s*(italic|oblique)/.test(cssStyleText(node));
}

function styleLooksBold(node) {
  const fontWeight = String(node?.style?.fontWeight || "").toLowerCase();
  if (fontWeight === "bold" || fontWeight === "bolder") return true;
  const numericWeight = Number(fontWeight);
  if (Number.isFinite(numericWeight) && numericWeight >= 600) return true;
  const css = cssStyleText(node);
  if (/font-weight\s*:\s*(bold|bolder)/.test(css)) return true;
  const weight = /\bfont-weight\s*:\s*(\d{3})\b/.exec(css);
  return weight ? Number(weight[1]) >= 600 : false;
}

function styleLooksUnderline(node) {
  const decoration = `${node?.style?.textDecoration || ""} ${node?.style?.textDecorationLine || ""}`.toLowerCase();
  if (decoration.includes("underline")) return true;
  return /text-decoration(?:-line)?\s*:\s*[^;]*underline/.test(cssStyleText(node));
}

function wrapStyledInline(children, node) {
  let html = children;
  if (styleLooksBold(node)) html = `<strong>${html}</strong>`;
  if (styleLooksItalic(node)) html = `<em>${html}</em>`;
  if (styleLooksUnderline(node)) html = `<u>${html}</u>`;
  return html;
}

function nodeHasInlineFormat(node, tagName) {
  if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
  const tag = node.tagName.toLowerCase();
  if (tagName === "strong") return tag === "strong" || tag === "b" || styleLooksBold(node);
  if (tagName === "em") return tag === "em" || tag === "i" || styleLooksItalic(node);
  if (tagName === "u") return tag === "u" || styleLooksUnderline(node);
  return tag === tagName;
}

function toggleInlineFormat(editor, command) {
  const tagName = { bold: "strong", italic: "em", underline: "u" }[command];
  if (!editor || !tagName) return false;
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return false;
  if (!editor.contains(selection.anchorNode) || !editor.contains(selection.focusNode)) return false;

  let existing = selection.anchorNode;
  if (existing && existing.nodeType !== Node.ELEMENT_NODE) existing = existing.parentElement;
  while (existing && existing !== editor && !nodeHasInlineFormat(existing, tagName)) {
    existing = existing.parentElement;
  }
  if (existing && existing !== editor) {
    const parent = existing.parentNode;
    while (existing.firstChild) parent.insertBefore(existing.firstChild, existing);
    parent.removeChild(existing);
    parent.normalize();
    return true;
  }

  const range = selection.getRangeAt(0);
  const wrapper = document.createElement(tagName);
  wrapper.appendChild(range.extractContents());
  range.insertNode(wrapper);
  const next = document.createRange();
  next.selectNodeContents(wrapper);
  selection.removeAllRanges();
  selection.addRange(next);
  return true;
}

function normalizeEditorHtml(html, options = {}) {
  const preserveEdgeBreaks = !!options.preserveEdgeBreaks;
  const container = document.createElement("div");
  container.innerHTML = html || "";

  const renderNode = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml(node.textContent || "");
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }

    const tag = node.tagName.toLowerCase();
    const children = Array.from(node.childNodes).map(renderNode).join("");

    if (tag === "br") {
      return "<br>";
    }

    if (tag === "b" || tag === "strong") {
      return `<strong>${children}</strong>`;
    }

    if (tag === "i" || tag === "em") {
      return `<em>${children}</em>`;
    }

    if (tag === "u") {
      return `<u>${children}</u>`;
    }

    if (tag === "span" || tag === "font") {
      return wrapStyledInline(children, node);
    }

    if (tag === "div" || tag === "p") {
      if (children === "" || children === "<br>") {
        return "<br><br>";
      }
      return children.endsWith("<br>") ? children : `${children}<br>`;
    }

    return children;
  };

  const childNodes = Array.from(container.childNodes);
  let normalized = childNodes
    .map((node, index) => {
      const rendered = renderNode(node);
      if (index === 0) return rendered;
      const previous = childNodes[index - 1];
      const previousTag =
        previous && previous.nodeType === Node.ELEMENT_NODE
          ? previous.tagName.toLowerCase()
          : "";
      const currentTag =
        node && node.nodeType === Node.ELEMENT_NODE
          ? node.tagName.toLowerCase()
          : "";
      const needsBreakBefore =
        (currentTag === "div" || currentTag === "p") &&
        previousTag !== "div" &&
        previousTag !== "p" &&
        !rendered.startsWith("<br>");
      return needsBreakBefore ? `<br>${rendered}` : rendered;
    })
    .join("");
  if (!preserveEdgeBreaks) {
    normalized = normalized.replace(/^(?:<br>\s*)+|(?:<br>\s*)+$/g, "");
  }
  return normalized;
}

function normalizeFlowEditorHtml(html, options = {}) {
  const preserveEdgeBreaks = !!options.preserveEdgeBreaks;
  const container = document.createElement("div");
  container.innerHTML = html || "";

  const renderInline = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml(node.textContent || "");
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }

    const tag = node.tagName.toLowerCase();
    const children = Array.from(node.childNodes).map(renderInline).join("");

    if (tag === "br") {
      return "<br>";
    }

    if (tag === "b" || tag === "strong") {
      return `<strong>${children}</strong>`;
    }

    if (tag === "i" || tag === "em") {
      return `<em>${children}</em>`;
    }

    if (tag === "u") {
      return `<u>${children}</u>`;
    }

    if (tag === "span" || tag === "font") {
      return wrapStyledInline(children, node);
    }

    return children;
  };

  const lines = [];
  let currentLine = "";
  let endedWithBreak = false;

  const pushLine = (line = currentLine) => {
    lines.push(line);
    currentLine = "";
  };

  Array.from(container.childNodes).forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      currentLine += escapeHtml(node.textContent || "");
      return;
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return;
    }

    const tag = node.tagName.toLowerCase();
    if (tag === "br") {
      pushLine();
      endedWithBreak = true;
      return;
    }

    if (tag === "div" || tag === "p") {
      if (currentLine !== "") {
        pushLine();
      }
      const blockHtml = Array.from(node.childNodes).map(renderInline).join("");
      if (blockHtml === "" || blockHtml === "<br>") {
        lines.push("");
        endedWithBreak = false;
        currentLine = "";
        return;
      }
      const blockLines = blockHtml.split("<br>");
      blockLines.forEach((line) => {
        lines.push(line);
      });
      endedWithBreak = false;
      currentLine = "";
      return;
    }

    currentLine += renderInline(node);
    endedWithBreak = false;
  });

  if (currentLine !== "") {
    pushLine();
    endedWithBreak = false;
  } else if (preserveEdgeBreaks && lines.length > 0 && endedWithBreak) {
    lines.push("");
  }

  let normalized = lines.join("<br>");
  if (!preserveEdgeBreaks) {
    normalized = normalized.replace(/^(?:<br>\s*)+|(?:<br>\s*)+$/g, "");
  }
  return normalized;
}

function syncRichText(inline, textInput) {
  if (!inline || !textInput) return;
  textInput.value = normalizeEditorHtml(inline.innerHTML);
}

function getColumnMetrics(cell) {
  const grid = cell.closest(".wysiwyg-grid");
  if (!grid) {
    return { columns: 28, columnStep: 24, grid: null };
  }
  const styles = getComputedStyle(grid);
  const columns = parseInt(styles.getPropertyValue("--editor-columns") || "28", 10) || 28;
  const gap = parseFloat(styles.columnGap || styles.gap || "6") || 6;
  const trackList = String(styles.gridTemplateColumns || "").trim().split(/\s+/).filter(Boolean);
  const firstTrackWidth = parseFloat(trackList[0] || "");
  const width = grid.clientWidth || grid.getBoundingClientRect().width;
  const calculatedColumnWidth = (width - gap * (columns - 1)) / columns;
  const columnWidth = Number.isFinite(firstTrackWidth) && firstTrackWidth > 0
    ? firstTrackWidth
    : calculatedColumnWidth;
  const columnStep = Math.max(1, columnWidth + gap);
  return {
    columns,
    columnStep,
    grid,
  };
}

function columnFromPointer(event, metrics) {
  const grid = metrics.grid;
  if (!grid) return null;
  const rect = grid.getBoundingClientRect();
  if (!rect.width || !metrics.columnStep) return null;
  const x = clamp(event.clientX - rect.left, 0, rect.width);
  return clamp(Math.floor(x / metrics.columnStep) + 1, 1, metrics.columns);
}

function syncCellFromInputs(cell) {
  const startInput = document.getElementById(cell.dataset.colStartInput);
  const spanInput = document.getElementById(cell.dataset.colSpanInput);
  const alignInput = document.getElementById(cell.dataset.alignInput);
  const boldInput = document.getElementById(cell.dataset.boldInput);
  const italicInput = document.getElementById(cell.dataset.italicInput);
  const underlinedInput = document.getElementById(cell.dataset.underlinedInput);
  if (!startInput || !spanInput) return;

  const metrics = getColumnMetrics(cell);
  const start = clamp(parseInt(startInput.value || "1", 10), 1, metrics.columns);
  const span = clamp(parseInt(spanInput.value || "1", 10), 1, metrics.columns - start + 1);
  const isFullWidth = start === 1 && span === metrics.columns;

  startInput.value = String(start);
  spanInput.value = String(span);
  const isStackedFlow = cell.classList.contains("flow-column-card-stacked");
  const slot = cell.closest(".flow-column-slot");
  if (isStackedFlow) {
    cell.style.gridColumn = `${start} / span ${span}`;
  } else if (slot) {
    slot.style.gridColumn = `${start} / span ${span}`;
  } else {
    cell.style.gridColumn = `${start} / span ${span}`;
  }
  cell.dataset.fullWidth = isFullWidth ? "1" : "0";

  let effectiveAlign = "left";
  if (alignInput) {
    let alignValue = alignInput.value || "";
    if (isFullWidth) {
      if (!cell.dataset.alignTouched && (alignValue === "" || alignValue === "left" || alignValue === "center")) {
        alignValue = "auto";
        alignInput.value = "auto";
      }
      effectiveAlign =
        alignValue === "force-left"
          ? "left"
          : alignValue === "right"
            ? "right"
            : "center";
    } else {
      if (alignValue === "" || alignValue === "auto" || alignValue === "force-left") {
        alignValue = "left";
        alignInput.value = "left";
      }
      effectiveAlign =
        alignValue === "right"
          ? "right"
          : alignValue === "center"
            ? "center"
            : "left";
    }
  }
  cell.dataset.align = effectiveAlign;

  const inline = cell.querySelector(".wysiwyg-inline-text");
  const flowEditor = cell.querySelector(".flow-column-editor");
  if (inline) {
    inline.style.textAlign = effectiveAlign;
  }
  if (flowEditor) {
    flowEditor.style.textAlign = effectiveAlign;
  }

  cell.classList.toggle("wysiwyg-cell-bold", !!(boldInput && parseInt(boldInput.value || "0", 10)));
  cell.classList.toggle("wysiwyg-cell-italic", !!(italicInput && parseInt(italicInput.value || "0", 10)));
  cell.classList.toggle("wysiwyg-cell-underlined", !!(underlinedInput && parseInt(underlinedInput.value || "0", 10)));

    cell.querySelectorAll("[data-align-choice]").forEach((button) => {
      const choice = button.dataset.alignChoice;
      const isActive =
        (effectiveAlign === "left" && choice === "left") ||
        (effectiveAlign === "center" && choice === "center") ||
        (effectiveAlign === "right" && choice === "right");
      button.classList.toggle("is-active", isActive);
    });
  }

function preserveFullWidthCells(previousColumns, nextColumns) {
  if (!previousColumns || previousColumns === nextColumns) return;
  document.querySelectorAll(".wysiwyg-cell").forEach((cell) => {
    const startInput = document.getElementById(cell.dataset.colStartInput);
    const spanInput = document.getElementById(cell.dataset.colSpanInput);
    const alignInput = document.getElementById(cell.dataset.alignInput);
    if (!startInput || !spanInput) return;

    const start = parseInt(startInput.value || "1", 10);
    const span = parseInt(spanInput.value || "1", 10);
    const wasFullWidth = start === 1 && (span === previousColumns || cell.dataset.fullWidth === "1");
    if (!wasFullWidth) return;

    startInput.value = "1";
    spanInput.value = String(nextColumns);
    if (alignInput && ["", "left", "center", "auto"].includes(alignInput.value || "")) {
      alignInput.value = "auto";
    }
  });
}

function syncCellInputMaximums(columns) {
  document.querySelectorAll(".wysiwyg-cell").forEach((cell) => {
    const startInput = document.getElementById(cell.dataset.colStartInput);
    const spanInput = document.getElementById(cell.dataset.colSpanInput);
    if (startInput) startInput.max = String(columns);
    if (spanInput) spanInput.max = String(columns);
  });
}

function preserveFullWidthCellsForCurrentLayout() {
  const frame = document.querySelector(".wysiwyg-frame");
  const columnInput = document.getElementById("meeting_table_column_count");
  const initialColumns = parseInt(frame?.dataset.initialEditorColumns || "", 10);
  const nextColumns = Math.max(1, Math.round(parseFloat(columnInput?.value || "") || initialColumns || 28));
  preserveFullWidthCells(initialColumns, nextColumns);
  syncCellInputMaximums(nextColumns);
}

function restoreScroll() {
  const params = new URLSearchParams(window.location.search);
  const scroll = params.get("scroll") || sessionStorage.getItem("meetingEditorScrollY");
  if (scroll) {
    window.requestAnimationFrame(() => {
      window.scrollTo(0, parseInt(scroll, 10) || 0);
      sessionStorage.removeItem("meetingEditorScrollY");
    });
  }
}

function installSubmitTracking() {
  const form = document.querySelector("form[action*='/meetings/v2/sections/']");
  const scrollInput = document.getElementById("editor_scroll_y");
  if (!form || !scrollInput) return;

  form.addEventListener("submit", () => {
    preserveFullWidthCellsForCurrentLayout();
    document.querySelectorAll(".wysiwyg-cell").forEach((cell) => {
      syncCellFromInputs(cell);
    });
    document.querySelectorAll(".wysiwyg-inline-text").forEach((inline) => {
      const cell = inline.closest(".wysiwyg-cell");
      const textInput = cell ? document.getElementById(cell.dataset.textInput) : null;
      if (textInput) {
        syncRichText(inline, textInput);
      }
    });
    document.querySelectorAll(".flow-column-editor").forEach((editor) => {
      const hiddenInput = editor.nextElementSibling;
      if (hiddenInput) {
        hiddenInput.value = normalizeFlowEditorHtml(editor.innerHTML, { preserveEdgeBreaks: true });
      }
    });
    const scrollY = Math.round(window.scrollY || 0);
    scrollInput.value = String(scrollY);
    sessionStorage.setItem("meetingEditorScrollY", String(scrollY));
  });
}

function installSaveShortcut() {
  const form = document.querySelector("form[action*='/meetings/v2/sections/']");
  if (!form) return;

  window.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      form.requestSubmit();
    }
  });
}

function installInlineEditors() {
  const moveEditorFocus = (currentEditor, backwards = false) => {
    const editors = Array.from(
      document.querySelectorAll(".wysiwyg-inline-text, .flow-column-editor")
    );
    const currentIndex = editors.indexOf(currentEditor);
    if (currentIndex < 0) return false;
    const nextEditor = editors[currentIndex + (backwards ? -1 : 1)];
    if (!nextEditor) return false;
    nextEditor.focus();
    return true;
  };

  document.querySelectorAll(".wysiwyg-cell").forEach((cell) => {
    const textInput = document.getElementById(cell.dataset.textInput);
    const inline = cell.querySelector(".wysiwyg-inline-text");
    const flowEditor = cell.querySelector(".flow-column-editor");
    const alignInput = document.getElementById(cell.dataset.alignInput);
    const boldInput = document.getElementById(cell.dataset.boldInput);
    const italicInput = document.getElementById(cell.dataset.italicInput);
    const underlinedInput = document.getElementById(cell.dataset.underlinedInput);

    if (inline && textInput) {
      inline.addEventListener("input", () => {
        syncRichText(inline, textInput);
      });
      inline.addEventListener("focus", () => {
        cell.classList.add("wysiwyg-cell-active");
      });
      inline.addEventListener("blur", () => {
        const normalized = normalizeEditorHtml(inline.innerHTML);
        inline.innerHTML = normalized;
        textInput.value = normalized;
        cell.classList.remove("wysiwyg-cell-active");
      });
      inline.addEventListener("keydown", (event) => {
        if (event.key === "Tab") {
          event.preventDefault();
          moveEditorFocus(inline, event.shiftKey);
          return;
        }

        if (!(event.metaKey || event.ctrlKey)) return;
        const key = event.key.toLowerCase();
        const selection = window.getSelection();
        const hasSelection =
          selection &&
          selection.rangeCount > 0 &&
          !selection.isCollapsed &&
          inline.contains(selection.anchorNode) &&
          inline.contains(selection.focusNode);

        if (hasSelection && (key === "b" || key === "i" || key === "u")) {
          event.preventDefault();
          // Cell-level style flags override inline rich text styling. If the cell
          // is currently bold/italic/underlined as a whole, toggling the matching
          // shortcut should clear that whole-cell style so it does not snap back
          // when focus moves to another row.
          if (key === "b" && boldInput && parseInt(boldInput.value || "0", 10)) {
            boldInput.value = "0";
            syncCellFromInputs(cell);
            syncRichText(inline, textInput);
            return;
          }
          if (key === "i" && italicInput && parseInt(italicInput.value || "0", 10)) {
            italicInput.value = "0";
            syncCellFromInputs(cell);
            syncRichText(inline, textInput);
            return;
          }
          if (key === "u" && underlinedInput && parseInt(underlinedInput.value || "0", 10)) {
            underlinedInput.value = "0";
            syncCellFromInputs(cell);
            syncRichText(inline, textInput);
            return;
          }
          toggleInlineFormat(inline, key === "b" ? "bold" : key === "i" ? "italic" : "underline");
          syncRichText(inline, textInput);
          return;
        }

        let targetInput = null;
        if (key === "b") targetInput = boldInput;
        if (key === "i") targetInput = italicInput;
        if (key === "u") targetInput = underlinedInput;
        if (!targetInput) return;
        event.preventDefault();
        targetInput.value = parseInt(targetInput.value || "0", 10) ? "0" : "1";
        syncCellFromInputs(cell);
      });
    }

    if (flowEditor) {
      flowEditor.addEventListener("focus", () => {
        cell.classList.add("wysiwyg-cell-active");
      });
      flowEditor.addEventListener("blur", () => {
        cell.classList.remove("wysiwyg-cell-active");
      });
    }

    cell.querySelectorAll("[data-align-choice]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!alignInput) return;
        const choice = button.dataset.alignChoice;
        const isFullWidth = cell.dataset.fullWidth === "1";
        if (choice === "right") {
          alignInput.value = "right";
        } else if (choice === "center") {
          alignInput.value = isFullWidth ? "auto" : "center";
        } else {
          alignInput.value = isFullWidth ? "force-left" : "left";
        }
        cell.dataset.alignTouched = "1";
        syncCellFromInputs(cell);
      });
    });

    syncCellFromInputs(cell);
  });
}

function installDragAndResize() {
  let dragState = null;

  const readCellStart = (cell) => {
    const input = document.getElementById(cell.dataset.colStartInput);
    return parseInt(input?.value || "1", 10) || 1;
  };

  const readCellSpan = (cell) => {
    const input = document.getElementById(cell.dataset.colSpanInput);
    return parseInt(input?.value || "1", 10) || 1;
  };

  const orderedFlowCells = (cell) => {
    const grid = cell.closest(".flow-row-grid");
    if (!grid) return [];
    return Array.from(grid.querySelectorAll(".flow-column-card:not(.flow-column-card-stacked)"))
      .sort((left, right) => {
        const startDelta = readCellStart(left) - readCellStart(right);
        if (startDelta) return startDelta;
        return (parseInt(left.dataset.cellId || "0", 10) || 0) - (parseInt(right.dataset.cellId || "0", 10) || 0);
      });
  };

  const orderedRowCells = (cell) => {
    const grid = cell.closest(".wysiwyg-grid");
    if (!grid || grid.classList.contains("flow-row-grid")) return [];
    return Array.from(grid.querySelectorAll(":scope > .wysiwyg-cell:not(.wysiwyg-cell-hidden)"))
      .sort((left, right) => {
        const startDelta = readCellStart(left) - readCellStart(right);
        if (startDelta) return startDelta;
        return (parseInt(left.dataset.cellId || "0", 10) || 0) - (parseInt(right.dataset.cellId || "0", 10) || 0);
      });
  };

  const orderedStackedCells = (cell) => {
    const grid = cell.closest(".flow-row-grid");
    if (!grid) return [];
    return Array.from(grid.querySelectorAll(".flow-column-card-stacked"))
      .sort((left, right) => {
        const startDelta = readCellStart(left) - readCellStart(right);
        if (startDelta) return startDelta;
        return (parseInt(left.dataset.cellId || "0", 10) || 0) - (parseInt(right.dataset.cellId || "0", 10) || 0);
      });
  };

  const stackedResizeBounds = (cell, maxCols) => {
    const peers = orderedStackedCells(cell);
    const index = peers.indexOf(cell);
    const minStart = index > 0 ? readCellStart(peers[index - 1]) + readCellSpan(peers[index - 1]) : 1;
    const maxEnd = index >= 0 && index < peers.length - 1 ? readCellStart(peers[index + 1]) - 1 : maxCols;
    return { minStart, maxEnd: Math.max(minStart, maxEnd) };
  };

  const validStackedStart = (cell, proposedStart, span, maxCols) => {
    const start = clamp(proposedStart, 1, maxCols - span + 1);
    const end = start + span - 1;
    const peers = orderedStackedCells(cell).filter((peer) => peer !== cell);
    for (const peer of peers) {
      const peerStart = readCellStart(peer);
      const peerEnd = peerStart + readCellSpan(peer) - 1;
      if (start <= peerEnd && peerStart <= end) {
        return null;
      }
    }
    return start;
  };

  const readFlowMinLines = (cell) => {
    const input = cell.dataset.flowMinLinesInput ? document.getElementById(cell.dataset.flowMinLinesInput) : null;
    const parsed = parseInt(input?.value || "0", 10);
    if (parsed > 0) return parsed;
    const editor = cell.querySelector(".flow-column-editor");
    const computed = parseInt(getComputedStyle(editor || cell).getPropertyValue("--flow-min-lines") || "2", 10);
    return Number.isFinite(computed) && computed > 0 ? computed : 2;
  };

  const applyFlowMinLines = (cell, lines) => {
    const nextLines = clamp(lines, 2, 24);
    const input = cell.dataset.flowMinLinesInput ? document.getElementById(cell.dataset.flowMinLinesInput) : null;
    if (input) input.value = String(nextLines);
    const editor = cell.querySelector(".flow-column-editor");
    if (editor) {
      editor.style.setProperty("--flow-min-lines", String(nextLines));
      editor.style.height = "auto";
      editor.style.height = `${editor.scrollHeight}px`;
    }
  };

  const resizeBoundsForCell = (cell, maxCols) => {
    if (cell.classList.contains("flow-column-card-stacked")) {
      return stackedResizeBounds(cell, maxCols);
    }
    const peers = cell.classList.contains("flow-column-card") ? orderedFlowCells(cell) : orderedRowCells(cell);
    const index = peers.indexOf(cell);
    const minStart = index > 0 ? readCellStart(peers[index - 1]) + readCellSpan(peers[index - 1]) : 1;
    const maxEnd = index >= 0 && index < peers.length - 1 ? readCellStart(peers[index + 1]) - 1 : maxCols;
    return { minStart, maxEnd: Math.max(minStart, maxEnd) };
  };

  const setHandleActive = (cell, resizeHost, active) => {
    const host = resizeHost || cell;
    host.querySelectorAll(".wysiwyg-resize-handle, .wysiwyg-resize-handle-left, .wysiwyg-resize-handle-bottom").forEach((handle) => {
      handle.classList.toggle("is-active", active);
    });
  };

  const applyFlowReorder = (state, proposedStart) => {
    if (!state.flowCells || !state.flowSlots || state.initialFlowIndex < 0) return false;
    const proposedEnd = proposedStart + state.initialSpan - 1;
    let targetIndex = null;
    let strongestOverlap = 0;
    state.flowSlots.forEach((slot, index) => {
      if (index === state.initialFlowIndex) return;
      const slotEnd = slot.start + slot.span - 1;
      const overlap = Math.max(0, Math.min(proposedEnd, slotEnd) - Math.max(proposedStart, slot.start) + 1);
      if (overlap > strongestOverlap) {
        strongestOverlap = overlap;
        targetIndex = index;
      }
    });

    if (targetIndex == null) {
      const startInput = document.getElementById(state.cell.dataset.colStartInput);
      const spanInput = document.getElementById(state.cell.dataset.colSpanInput);
      if (!startInput || !spanInput) return false;
      startInput.value = String(proposedStart);
      spanInput.value = String(state.initialSpan);
      syncCellFromInputs(state.cell);
      return true;
    }

    const reordered = [...state.flowCells];
    const [moved] = reordered.splice(state.initialFlowIndex, 1);
    reordered.splice(targetIndex, 0, moved);

    reordered.forEach((flowCell, index) => {
      const slot = state.flowSlots[index];
      const startInput = document.getElementById(flowCell.dataset.colStartInput);
      const spanInput = document.getElementById(flowCell.dataset.colSpanInput);
      if (!slot || !startInput || !spanInput) return;
      startInput.value = String(slot.start);
      spanInput.value = String(slot.span);
      syncCellFromInputs(flowCell);
    });
    return true;
  };

  const onMove = (event) => {
    if (!dragState) return;
    const dx = event.clientX - dragState.startX;
    const pointerColumn = columnFromPointer(event, dragState);
    const deltaCols = pointerColumn == null
      ? Math.round(dx / dragState.columnStep)
      : pointerColumn - dragState.initialPointerColumn;
    const maxCols = dragState.columns;

    if (dragState.mode === "move") {
      if (dragState.cell.classList.contains("flow-column-card-stacked")) {
        const rawStart = pointerColumn == null
          ? dragState.initialStart + deltaCols
          : pointerColumn - dragState.pointerColumnOffset;
        const nextStart = validStackedStart(
          dragState.cell,
          rawStart,
          dragState.initialSpan,
          maxCols,
        );
        if (nextStart != null) {
          dragState.startInput.value = String(nextStart);
          syncCellFromInputs(dragState.cell);
        }
        return;
      }
      const rawStart = pointerColumn == null
        ? dragState.initialStart + deltaCols
        : pointerColumn - dragState.pointerColumnOffset;
      const nextStart = clamp(
        rawStart,
        1,
        maxCols - dragState.initialSpan + 1
      );
      if (applyFlowReorder(dragState, nextStart)) {
        return;
      }
      dragState.startInput.value = String(nextStart);
      syncCellFromInputs(dragState.cell);
      return;
    }

    if (dragState.mode === "resize-right") {
      const { maxEnd } = resizeBoundsForCell(dragState.cell, maxCols);
      const maxSpan = Math.max(1, maxEnd - dragState.initialStart + 1);
      const nextSpan = clamp(
        dragState.initialSpan + deltaCols,
        1,
        maxSpan
      );
      dragState.spanInput.value = String(nextSpan);
      syncCellFromInputs(dragState.cell);
      return;
    }

    if (dragState.mode === "resize-left") {
      const { minStart, maxEnd } = resizeBoundsForCell(dragState.cell, maxCols);
      const nextStart = clamp(
        dragState.initialStart + deltaCols,
        minStart,
        dragState.initialStart + dragState.initialSpan - 1
      );
      const nextSpan = clamp(
        dragState.initialSpan - (nextStart - dragState.initialStart),
        1,
        maxEnd - nextStart + 1
      );
      dragState.startInput.value = String(nextStart);
      dragState.spanInput.value = String(nextSpan);
      syncCellFromInputs(dragState.cell);
      return;
    }

    if (dragState.mode === "resize-bottom") {
      const lineStep = parseFloat(getComputedStyle(dragState.cell.querySelector(".flow-column-editor") || dragState.cell)
        .getPropertyValue("--flow-line-step") || "18") || 18;
      const deltaLines = Math.round((event.clientY - dragState.startY) / lineStep);
      applyFlowMinLines(dragState.cell, dragState.initialMinLines + deltaLines);
    }
  };

  const onUp = () => {
    if (dragState) {
      setHandleActive(dragState.cell, dragState.resizeHost, false);
    }
    dragState = null;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };

  const attachCellDragControls = (cell, { resizeHost = null } = {}) => {
    const host = resizeHost || cell;
    const startInput = document.getElementById(cell.dataset.colStartInput);
    const spanInput = document.getElementById(cell.dataset.colSpanInput);
    const moveHandle = cell.querySelector(".wysiwyg-move-handle");
    const resizeRight = host.querySelector(".wysiwyg-resize-handle");
    const resizeLeft = host.querySelector(".wysiwyg-resize-handle-left");
    const resizeBottom = host.querySelector(".wysiwyg-resize-handle-bottom");
    if (!startInput || !spanInput) return;

    const beginDrag = (mode, event) => {
      const metrics = getColumnMetrics(cell);
      const initialStart = parseInt(startInput.value || "1", 10);
      const initialSpan = parseInt(spanInput.value || "1", 10);
      const initialPointerColumn = columnFromPointer(event, metrics) || initialStart;
      const flowCells = mode === "move" && cell.classList.contains("flow-column-card") && !cell.classList.contains("flow-column-card-stacked")
        ? orderedFlowCells(cell)
        : [];
      dragState = {
        mode,
        cell,
        resizeHost: host,
        startInput,
        spanInput,
        startX: event.clientX,
        startY: event.clientY,
        initialStart,
        initialSpan,
        initialMinLines: readFlowMinLines(cell),
        columnStep: metrics.columnStep,
        columns: metrics.columns,
        grid: metrics.grid,
        initialPointerColumn,
        pointerColumnOffset: initialPointerColumn - initialStart,
        flowCells,
        flowSlots: flowCells.map((flowCell) => ({
          start: readCellStart(flowCell),
          span: readCellSpan(flowCell),
        })),
        initialFlowIndex: flowCells.indexOf(cell),
      };
      setHandleActive(cell, host, true);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    };

    if (moveHandle) {
      moveHandle.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        beginDrag("move", event);
      });
    }

    if (resizeRight) {
      resizeRight.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        beginDrag("resize-right", event);
      });
    }

    if (resizeLeft) {
      resizeLeft.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        beginDrag("resize-left", event);
      });
    }

    if (resizeBottom) {
      resizeBottom.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        beginDrag("resize-bottom", event);
      });
    }
  };

  document.querySelectorAll(".flow-column-slot").forEach((slot) => {
    const primary = slot.querySelector(".flow-column-card:not(.flow-column-card-stacked)");
    if (!primary) return;
    attachCellDragControls(primary, { resizeHost: slot });
  });

  document.querySelectorAll(".flow-column-card-stacked").forEach((cell) => {
    attachCellDragControls(cell);
  });

  document.querySelectorAll(".wysiwyg-cell:not(.flow-column-card)").forEach((cell) => {
    attachCellDragControls(cell);
  });
}

function installInsertMenus() {
  const closeAllMenus = () => {
    document.querySelectorAll("[data-insert-details]").forEach((details) => {
      details.removeAttribute("open");
    });
  };

  document.querySelectorAll("[data-insert-split]").forEach((split) => {
    const details = split.querySelector("[data-insert-details]");
    if (!details) return;

    details.querySelectorAll("[data-insert-choice]").forEach((button) => {
      const closeAfterChoice = () => {
        details.removeAttribute("open");
      };
      button.addEventListener("click", closeAfterChoice);
    });
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-insert-split]")) {
      closeAllMenus();
    }
  });

  document.addEventListener("focusin", (event) => {
    if (!event.target.closest("[data-insert-split]")) {
      closeAllMenus();
    }
  });
}

function installPresetSelectorAutoApply() {
  const form = document.querySelector("form[action*='/meetings/v2/sections/']");
  const presetSelect = document.getElementById("book_layout_preset_id");
  if (!form || !presetSelect) return;

  presetSelect.addEventListener("change", () => {
    form.requestSubmit();
  });
}

function installLayoutGridControls() {
  const frame = document.querySelector(".wysiwyg-frame");
  const columnInput = document.getElementById("meeting_table_column_count");
  const gapInput = document.getElementById("meeting_table_column_gap_px");
  const scaleInput = document.getElementById("screen_preview_scale");
  const fontSizeSelect = document.getElementById("meeting_base_font_size_pt");
  const lineHeightInput = document.getElementById("meeting_line_height");
  if (!frame || !columnInput || !gapInput || !scaleInput) return;

  const usableWidthIn = parseFloat(frame.dataset.usableWidthIn || "3.22") || 3.22;

  const readNumber = (input, fallback) => {
    const value = parseFloat(input.value || "");
    return Number.isFinite(value) ? value : fallback;
  };
  let currentColumns = Math.max(1, Math.round(readNumber(columnInput, 28)));
  frame.dataset.initialEditorColumns = String(currentColumns);

  const syncColumnGuides = (columns) => {
    document.querySelectorAll(".wysiwyg-row-guide").forEach((guide) => {
      const existingCount = guide.querySelectorAll(".wysiwyg-row-guide-col").length;
      if (existingCount === columns) return;
      guide.replaceChildren(
        ...Array.from({ length: columns }, (_, index) => {
          const column = document.createElement("div");
          column.className = "wysiwyg-row-guide-col";
          column.textContent = String(index + 1);
          return column;
        })
      );
    });
  };

  const syncLayout = (options = {}) => {
    const syncCells = !!options.syncCells;
    const columns = Math.max(1, Math.round(readNumber(columnInput, 28)));
    const gap = Math.max(0, readNumber(gapInput, 6));
    const previewScale = Math.max(40, readNumber(scaleInput, 220));
    const baseFontPt = Math.max(1, readNumber(fontSizeSelect, parseFloat(frame.dataset.baseFontPt || "8.5") || 8.5));
    const lineHeight = Math.max(
      0.8,
      Math.min(
        3,
        readNumber(
          lineHeightInput || { value: "" },
          parseFloat(frame.dataset.lineHeight || "1.2") || 1.2
        )
      )
    );
    const previewWidth = usableWidthIn * previewScale;
    const totalGap = gap * Math.max(0, columns - 1);
    const columnWidth = Math.max(8, (previewWidth - totalGap) / columns);
    preserveFullWidthCells(currentColumns, columns);
    syncCellInputMaximums(columns);
    currentColumns = columns;
    frame.dataset.screenPreviewScale = String(previewScale);
    frame.style.setProperty("--editor-columns", String(columns));
    frame.style.setProperty("--editor-column-width", `${columnWidth}px`);
    frame.style.setProperty("--editor-column-gap", `${gap}px`);
    frame.style.setProperty("--editor-preview-width", `${previewWidth}px`);
    frame.style.setProperty("--editor-preview-scale", String(previewScale));
    frame.style.setProperty("--editor-base-font-px", `${(baseFontPt / 72) * previewScale}px`);
    frame.style.setProperty("--editor-line-height", String(lineHeight));
    frame.style.setProperty("--editor-display-line-height", String(lineHeight * 0.9));
    frame.dataset.baseFontPt = String(baseFontPt);
    frame.dataset.lineHeight = String(lineHeight);
    syncColumnGuides(columns);
    if (typeof window.syncMeetingEditorRowFonts === "function") {
      window.syncMeetingEditorRowFonts();
    }
    if (syncCells) {
      document.querySelectorAll(".wysiwyg-cell").forEach((cell) => {
        syncCellFromInputs(cell);
      });
    }
    document.querySelectorAll("[data-inline-editor], [data-flow-editor]").forEach((editor) => {
      if (typeof autosize === "function") {
        autosize(editor);
      }
      if (typeof syncGuides === "function") {
        syncGuides(editor);
      }
    });
  };

  const installLineHeightStepper = (input) => {
    if (!input) return;
    const step = 0.05;
    const min = 0.8;
    const max = 3;
    const snap = (value) => {
      const numeric = Number.parseFloat(value);
      const base = Number.isFinite(numeric) ? numeric : 1.2;
      const snapped = Math.round(base / step) * step;
      return Number(Math.min(max, Math.max(min, snapped)).toFixed(2));
    };
    const applyDelta = (delta) => {
      input.value = snap(parseFloat(input.value) + delta).toFixed(2);
      syncLayout();
    };
    const spinnerWidth = 22;
    const isSpinnerClick = (event) => {
      const bounds = input.getBoundingClientRect();
      return event.clientX >= bounds.right - spinnerWidth;
    };

    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowUp") {
        event.preventDefault();
        applyDelta(step);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        applyDelta(-step);
      }
    });

    let holdTimer = 0;
    let holdInterval = 0;
    const stopHold = () => {
      window.clearTimeout(holdTimer);
      window.clearInterval(holdInterval);
      holdTimer = 0;
      holdInterval = 0;
    };
    input.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !isSpinnerClick(event)) return;
      event.preventDefault();
      input.focus();
      const bounds = input.getBoundingClientRect();
      const delta = event.clientY < bounds.top + bounds.height / 2 ? step : -step;
      applyDelta(delta);
      holdTimer = window.setTimeout(() => {
        holdInterval = window.setInterval(() => applyDelta(delta), 60);
      }, 400);
    });
    window.addEventListener("pointerup", stopHold);
    window.addEventListener("pointercancel", stopHold);
    input.addEventListener("blur", () => {
      input.value = snap(input.value).toFixed(2);
    });
  };

  columnInput.addEventListener("input", syncLayout);
  columnInput.addEventListener("change", syncLayout);
  [gapInput, scaleInput, lineHeightInput].filter(Boolean).forEach((input) => {
    input.addEventListener("input", syncLayout);
    input.addEventListener("change", () => {
      syncLayout({ syncCells: true });
    });
  });
  installLineHeightStepper(lineHeightInput);
  fontSizeSelect?.addEventListener("change", () => {
    if (typeof window.clearMeetingEditorRowFontOverrides === "function") {
      window.clearMeetingEditorRowFontOverrides();
    }
    syncLayout({ syncCells: true });
  });
  syncLayout({ syncCells: true });
  window.syncMeetingEditorColumnGuides = () => {
    syncColumnGuides(Math.max(1, Math.round(readNumber(columnInput, 28))));
  };
}

function installRowKindControls() {
  const form = document.querySelector("form[action*='/meetings/v2/sections/']");
  const ensureSpacer = (grid, kindClass) => {
    if (!grid) return null;
    let spacer = grid.querySelector(`.${kindClass}`);
    if (!spacer) {
      spacer = document.createElement("div");
      spacer.className = kindClass;
      spacer.style.gridColumn = "1 / -1";
      grid.appendChild(spacer);
    }
    return spacer;
  };

  document.querySelectorAll("[data-row-kind-picker]").forEach((picker) => {
    const toolbar = picker.closest(".wysiwyg-row-toolbar");
    const row = picker.closest(".wysiwyg-sheet-row");
    const primaryHidden = picker.querySelector("[data-row-kind-primary-hidden]");
    const primaryInputs = Array.from(picker.querySelectorAll("[data-row-kind-primary]"));
    const modifierInputs = Array.from(picker.querySelectorAll("[data-row-kind-modifier]"));
    const tableStartOptionWraps = Array.from(toolbar?.querySelectorAll("[data-table-start-option-wrap]") || []);
    const tableStartOptions = Array.from(toolbar?.querySelectorAll("[data-table-start-option]") || []);
    const addCellButton = toolbar?.querySelector("[data-editor-action='add-cell']");
    const regularGrid = row?.querySelector(".meeting-sheet-grid.wysiwyg-grid:not(.flow-row-grid)");
    const flowGrid = row?.querySelector(".meeting-sheet-grid.wysiwyg-grid.flow-row-grid");
    if (!toolbar || !row || !primaryHidden) return;

    const syncChipState = () => {
      picker.querySelectorAll(".wysiwyg-kind-chip").forEach((chip) => {
        const input = chip.querySelector("input");
        chip.classList.toggle("is-active", !!input?.checked);
      });
    };

    const syncRowPreview = (primaryKind, selectedKinds) => {
      const isUnderlined = selectedKinds.includes("underlined");
      row.classList.toggle("wysiwyg-row-underlined", isUnderlined);

      const isBlank = primaryKind === "blank";
      const isDivider = primaryKind === "divider";
      const isColumnFlow = primaryKind === "column_flow";

      if (regularGrid) {
        regularGrid.classList.toggle("wysiwyg-grid-blank", isBlank || isDivider);
        regularGrid.querySelectorAll(".wysiwyg-cell").forEach((cell) => {
          cell.classList.toggle("wysiwyg-cell-hidden", isBlank || isDivider);
        });

        const blankSpacer = ensureSpacer(regularGrid, "wysiwyg-blank-row-spacer");
        const dividerSpacer = ensureSpacer(regularGrid, "wysiwyg-divider-row-spacer");
        if (blankSpacer) {
          blankSpacer.style.display = isBlank ? "" : "none";
        }
        if (dividerSpacer) {
          dividerSpacer.style.display = isDivider ? "" : "none";
        }
        regularGrid.style.display = isColumnFlow ? "none" : "";
      }

      if (flowGrid) {
        flowGrid.style.display = isColumnFlow ? "" : "none";
      }

      if (addCellButton) {
        addCellButton.textContent = isColumnFlow ? "Add Column" : "Add Cell";
        addCellButton.dataset.rowMode = isColumnFlow ? "column-flow" : "grid";
      }
    };

    const sync = () => {
      const primary = primaryInputs.find((input) => input.checked)?.value || "content";
      const modifiers = modifierInputs.filter((input) => input.checked).map((input) => input.value);
      const selectedKinds = [primary, ...modifiers];
      const isTableStart = selectedKinds.includes("table_start");

      primaryHidden.value = primary;
      tableStartOptionWraps.forEach((wrap) => {
        wrap.classList.toggle("wysiwyg-row-group-toggle-hidden", !isTableStart);
      });
      tableStartOptions.forEach((input) => {
        input.disabled = !isTableStart;
        if (!isTableStart) input.checked = false;
      });

      syncChipState();
      syncRowPreview(primary, selectedKinds);
      if (window.syncMeetingEditorRowFonts) {
        window.syncMeetingEditorRowFonts();
      }
    };

    sync();
    [...tableStartOptionWraps, ...tableStartOptions].forEach((element) => {
      element.addEventListener("click", (event) => {
        event.stopPropagation();
      });
    });
    primaryInputs.forEach((input) => {
      input.addEventListener("change", () => {
        const previousPrimary = primaryHidden.value || "content";
        sync();
        const nextPrimary = primaryHidden.value || "content";
        const changed = previousPrimary !== nextPrimary;
        const involvesColumnFlow =
          previousPrimary === "column_flow" || nextPrimary === "column_flow";
        if (changed && involvesColumnFlow && form) {
          form.requestSubmit();
        }
      });
    });
    modifierInputs.forEach((input) => {
      input.addEventListener("change", sync);
    });
  });
}

function installRowFontSizeControls() {
  const frame = document.querySelector(".wysiwyg-frame");
  if (!frame) return;

  const applyRowFontPreview = (row, effectiveFontPt) => {
    if (!row || !Number.isFinite(effectiveFontPt) || effectiveFontPt <= 0) return;
    const screenPreviewScale = parseFloat(frame.dataset.screenPreviewScale || "220") || 220;
    row.style.setProperty("--row-base-font-px", `${(effectiveFontPt / 72) * screenPreviewScale}px`);
    row.dataset.effectiveFontPt = String(effectiveFontPt);
  };

  const syncAllRows = () => {
    const baseFontPt = parseFloat(frame.dataset.baseFontPt || "7") || 7;
    let insideTable = false;
    let inheritedTableFontPt = null;

    document.querySelectorAll(".wysiwyg-sheet-row").forEach((row) => {
      const primaryHidden = row.querySelector("[data-row-kind-primary-hidden]");
      const modifierInputs = Array.from(row.querySelectorAll("[data-row-kind-modifier]"));
      const fontSizeInput = row.querySelector("[data-row-font-size-input]");
      if (!primaryHidden || !fontSizeInput) return;

      const selectedModifiers = modifierInputs
        .filter((input) => input.checked)
        .map((input) => input.value);
      const isTableStart = selectedModifiers.includes("table_start");
      const isTableEnd = selectedModifiers.includes("table_end");
      const overrideFontPt = parseRowFontSizePt(fontSizeInput.value);

      if (isTableStart) {
        insideTable = true;
        inheritedTableFontPt = overrideFontPt;
      }

      const effectiveFontPt =
        overrideFontPt != null
          ? overrideFontPt
          : insideTable && inheritedTableFontPt != null
            ? inheritedTableFontPt
            : baseFontPt;

      const defaultOption = fontSizeInput.options?.[0];
      if (defaultOption) {
        defaultOption.textContent = formatRowFontSizePt(effectiveFontPt);
      }
      applyRowFontPreview(row, effectiveFontPt);

      if (isTableEnd) {
        insideTable = false;
        inheritedTableFontPt = null;
      }
    });
  };

  const commitInput = (input) => {
    const parsed = parseRowFontSizePt(input.value);
    input.value = formatRowFontSizePt(parsed);
    syncAllRows();
  };

  document.querySelectorAll("[data-row-font-size-input]").forEach((input) => {
    input.addEventListener("change", () => {
      commitInput(input);
    });
    input.addEventListener("blur", () => {
      commitInput(input);
    });
  });

  window.syncMeetingEditorRowFonts = syncAllRows;
  window.clearMeetingEditorRowFontOverrides = () => {
    document.querySelectorAll("[data-row-font-size-input]").forEach((input) => {
      input.value = "";
    });
    syncAllRows();
  };
  syncAllRows();
}

function syncFlowStackedPositions(grid) {
  if (!grid || !grid.classList.contains("flow-row-grid")) return;
  const slotHeights = new Map();
  grid.querySelectorAll(".flow-column-slot").forEach((slot) => {
    const card = slot.querySelector('.flow-column-card[data-is-stacked="0"]');
    if (!card) return;
    slotHeights.set(String(card.dataset.cellId || ""), slot.getBoundingClientRect().height);
  });
  const stackGapPx = 2;
  grid.querySelectorAll(".flow-column-stacked-grid-cell").forEach((stacked) => {
    const parentId = String(stacked.dataset.stackUnderCellId || "");
    const parentHeight = slotHeights.get(parentId);
    if (parentHeight == null || parentHeight <= 0) {
      stacked.style.removeProperty("margin-top");
      return;
    }
    stacked.style.marginTop = `${Math.round(parentHeight + stackGapPx)}px`;
  });
}

function installFlowStackedLayout() {
  const syncAll = () => {
    document.querySelectorAll(".flow-row-grid").forEach((grid) => syncFlowStackedPositions(grid));
  };

  syncAll();
  requestAnimationFrame(syncAll);
  window.addEventListener("load", syncAll, { once: true });

  if (typeof ResizeObserver === "undefined") return;

  document.querySelectorAll(".flow-row-grid").forEach((grid) => {
    const observer = new ResizeObserver(() => syncFlowStackedPositions(grid));
    grid.querySelectorAll(".flow-column-slot").forEach((slot) => observer.observe(slot));
    grid.querySelectorAll(".flow-column-slot .flow-column-editor").forEach((editor) => {
      observer.observe(editor);
    });
  });
}

function installFlowColumnTextareas() {
  const autosize = (editor) => {
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  };

  const insertFlowRowBreak = (editor) => {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      document.execCommand("insertHTML", false, "<div><br></div>");
      return;
    }

    const range = selection.getRangeAt(0);
    if (!editor.contains(range.startContainer)) {
      document.execCommand("insertHTML", false, "<div><br></div>");
      return;
    }

    let block = range.startContainer.nodeType === Node.ELEMENT_NODE
      ? range.startContainer
      : range.startContainer.parentElement;

    while (block && block !== editor && !(block.tagName && /^(DIV|P)$/i.test(block.tagName))) {
      block = block.parentElement;
    }

    const newLine = document.createElement("div");
    newLine.appendChild(document.createElement("br"));

    if (block && block !== editor) {
      if (block.nextSibling) {
        block.parentNode.insertBefore(newLine, block.nextSibling);
      } else {
        block.parentNode.appendChild(newLine);
      }
    } else {
      range.deleteContents();
      range.insertNode(newLine);
    }

    const caretRange = document.createRange();
    caretRange.setStart(newLine, 0);
    caretRange.collapse(true);
    selection.removeAllRanges();
    selection.addRange(caretRange);
  };

  const normalizedForStorage = (editor) => {
    return normalizeFlowEditorHtml(editor.innerHTML, { preserveEdgeBreaks: true });
  };

  const normalizeEditorInPlace = (editor, hiddenInput = null, updateDom = true) => {
    const normalized = normalizedForStorage(editor);
    if (updateDom) {
      editor.innerHTML = normalized;
    }
    if (hiddenInput) {
      hiddenInput.value = normalized;
    }
    return normalized;
  };

  const getCaretRowIndex = (editor) => {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) return 1;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.startContainer)) return 1;

    const preCaret = range.cloneRange();
    preCaret.selectNodeContents(editor);
    preCaret.setEnd(range.startContainer, range.startOffset);

    const container = document.createElement("div");
    container.appendChild(preCaret.cloneContents());
    const normalized = normalizeFlowEditorHtml(container.innerHTML, { preserveEdgeBreaks: true });
    if (!normalized) return 1;
    return normalized.split("<br>").length;
  };

  const placeCaretAtFlowRow = (editor, rowIndex) => {
    const normalized = normalizeFlowEditorHtml(editor.innerHTML, { preserveEdgeBreaks: true });
    const lines = normalized === "" ? [""] : normalized.split("<br>");
    const targetRow = clamp(rowIndex, 1, lines.length);
    const markerId = `flow-caret-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    lines[targetRow - 1] = `${lines[targetRow - 1]}<span id="${markerId}"></span>`;
    editor.innerHTML = lines.join("<br>");
    editor.focus();

    const marker = editor.querySelector(`#${markerId}`);
    if (marker) {
      const range = document.createRange();
      range.setStartBefore(marker);
      range.collapse(true);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      marker.remove();
    }
  };

  const syncGuides = (editor) => {
    const styles = getComputedStyle(editor);
    const lineHeight = styles.lineHeight;
    if (lineHeight && lineHeight !== "normal") {
      editor.style.setProperty("--flow-actual-line-step", lineHeight);
    }
  };

  document.querySelectorAll(".flow-column-editor").forEach((editor) => {
    const hiddenInput = editor.nextElementSibling;
    if (!hiddenInput) return;

    const syncValue = () => {
      hiddenInput.value = normalizedForStorage(editor);
    };

    syncGuides(editor);
    autosize(editor);
    syncValue();

    editor.addEventListener("input", () => {
      syncGuides(editor);
      autosize(editor);
      syncValue();
      const grid = editor.closest(".flow-row-grid");
      if (grid) syncFlowStackedPositions(grid);
    });

    editor.addEventListener("blur", () => {
      normalizeEditorInPlace(editor, hiddenInput, false);
      syncGuides(editor);
      autosize(editor);
    });

    editor.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        insertFlowRowBreak(editor);
        syncGuides(editor);
        autosize(editor);
        syncValue();
        return;
      }

      if (event.key === "Tab") {
        event.preventDefault();
        const currentRowIndex = getCaretRowIndex(editor);
        normalizeEditorInPlace(editor, hiddenInput, false);
        const editors = Array.from(document.querySelectorAll(".flow-column-editor"));
        const currentIndex = editors.indexOf(editor);
        if (currentIndex >= 0) {
          const delta = event.shiftKey ? -1 : 1;
          const nextEditor = editors[currentIndex + delta];
          if (nextEditor) {
            const nextHiddenInput = nextEditor.nextElementSibling;
            normalizeEditorInPlace(nextEditor, nextHiddenInput);
            placeCaretAtFlowRow(nextEditor, currentRowIndex);
            syncGuides(nextEditor);
            autosize(nextEditor);
          }
        }
        return;
      }

      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      if (!["b", "i", "u"].includes(key)) return;
      event.preventDefault();
      toggleInlineFormat(editor, key === "b" ? "bold" : key === "i" ? "italic" : "underline");
      syncValue();
    });
  });

  document.querySelectorAll("[data-flow-add]").forEach((button) => {
    button.addEventListener("click", () => {
      const flowItems = button.closest(".flow-items");
      if (!flowItems) return;
      const addWrap = flowItems.querySelector(".flow-add-wrap");
      const empty = flowItems.querySelector(".flow-empty");
      const editor = flowItems.querySelector(".flow-column-editor");
      const hiddenInput = flowItems.querySelector("textarea.sr-only");
      if (!editor || !hiddenInput) return;

      addWrap?.classList.add("flow-add-wrap-hidden");
      empty?.classList.add("flow-empty-hidden");
      editor.classList.remove("flow-column-editor-hidden");
      if (!hiddenInput.value) {
        editor.innerHTML = "";
      }
      const styles = getComputedStyle(editor);
      const lineHeight = styles.lineHeight;
      if (lineHeight && lineHeight !== "normal") {
        editor.style.setProperty("--flow-actual-line-step", lineHeight);
      }
      editor.style.height = "auto";
      editor.style.height = `${editor.scrollHeight}px`;
      editor.focus();
    });
  });
}

window.addEventListener("DOMContentLoaded", () => {
  restoreScroll();
  installSubmitTracking();
  installSaveShortcut();
  installInlineEditors();
  installDragAndResize();
  installInsertMenus();
  installPresetSelectorAutoApply();
  installRowKindControls();
  installRowFontSizeControls();
  installLayoutGridControls();
  installFlowColumnTextareas();
  installFlowStackedLayout();
});
