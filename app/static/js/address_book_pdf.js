function addressBookPdfNumber(form, name, fallback) {
  const value = parseFloat(form.querySelector(`[name="${name}"]`)?.value || "");
  return Number.isFinite(value) ? value : fallback;
}

function addressBookPdfMeetingLineHeightKey(fieldName, meetingName) {
  return `${String(fieldName || "").trim()}||${String(meetingName || "").trim()}`;
}

function parseAddressBookPdfMeetingLineHeightMap(raw) {
  try {
    const payload = JSON.parse(String(raw || "{}"));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return {};
    const normalized = {};
    Object.entries(payload).forEach(([key, value]) => {
      const numeric = parseFloat(value);
      if (!String(key).includes("||") || !Number.isFinite(numeric) || numeric <= 0) return;
      normalized[key] = Math.max(0.8, Math.min(3, numeric));
    });
    return normalized;
  } catch {
    return {};
  }
}

function serializeAddressBookPdfMeetingLineHeightMap(map) {
  return Object.keys(map).length ? JSON.stringify(map) : "";
}

function addressBookPdfPreviewLineHeight(form) {
  const visible = form.querySelector("[data-address-book-pdf-line-height-input]");
  const value = parseFloat(visible?.value || "");
  if (Number.isFinite(value)) return Math.max(0.5, value);
  return Math.max(0.5, addressBookPdfNumber(form, "line_height", 1.2));
}

function addressBookPdfPreviewFontSize(form) {
  const visible = form.querySelector("[data-address-book-pdf-font-size-input]");
  const value = parseFloat(visible?.value || "");
  if (Number.isFinite(value)) return Math.max(1, value);
  return Math.max(1, addressBookPdfNumber(form, "base_font_size_pt", 7));
}

function parseAddressBookPdfMeetingFontSizeMap(raw) {
  try {
    const payload = JSON.parse(String(raw || "{}"));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return {};
    const normalized = {};
    Object.entries(payload).forEach(([key, value]) => {
      const numeric = parseFloat(value);
      if (!String(key).includes("||") || !Number.isFinite(numeric) || numeric <= 0) return;
      normalized[key] = Math.max(4, Math.min(36, numeric));
    });
    return normalized;
  } catch {
    return {};
  }
}

function serializeAddressBookPdfMeetingFontSizeMap(map) {
  return Object.keys(map).length ? JSON.stringify(map) : "";
}

function syncAddressBookPdfMeetingFontSizeFromSelection(form = document.querySelector("[data-address-book-pdf-form]")) {
  if (!form) return;
  const visible = form.querySelector("[data-address-book-pdf-font-size-input]");
  const bookInput = form.querySelector("[data-address-book-pdf-book-font-size]");
  const jsonInput = form.querySelector("[data-address-book-pdf-meeting-font-size-json]");
  const resetButton = form.querySelector("[data-address-book-pdf-use-book-size]");
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  if (!visible || !bookInput) return;
  const bookValue = parseFloat(bookInput.value || "");
  const bookFontSize = Number.isFinite(bookValue) ? Math.max(1, Math.min(36, bookValue)) : 7;
  const fieldName = fieldSelect?.value || "";
  const meetingName = meetingSelect?.value || "";
  const key = fieldName && meetingName ? addressBookPdfMeetingLineHeightKey(fieldName, meetingName) : "";
  const map = parseAddressBookPdfMeetingFontSizeMap(jsonInput?.value);
  if (!key) {
    visible.value = String(bookFontSize);
    if (resetButton) resetButton.hidden = true;
    return;
  }
  const override = map[key];
  visible.value = String(override || bookFontSize);
  if (resetButton) resetButton.hidden = !override;
}

function persistAddressBookPdfMeetingFontSize(form) {
  const visible = form.querySelector("[data-address-book-pdf-font-size-input]");
  const bookInput = form.querySelector("[data-address-book-pdf-book-font-size]");
  const jsonInput = form.querySelector("[data-address-book-pdf-meeting-font-size-json]");
  const resetButton = form.querySelector("[data-address-book-pdf-use-book-size]");
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  if (!visible || !bookInput) return;
  const value = parseFloat(visible.value || "");
  const bookValue = parseFloat(bookInput.value || "");
  const bookFontSize = Number.isFinite(bookValue) ? Math.max(1, Math.min(36, bookValue)) : 7;
  const fieldName = fieldSelect?.value || "";
  const meetingName = meetingSelect?.value || "";
  const key = fieldName && meetingName ? addressBookPdfMeetingLineHeightKey(fieldName, meetingName) : "";
  if (!key) {
    if (Number.isFinite(value)) bookInput.value = String(Math.max(1, Math.min(36, value)));
    return;
  }
  if (!jsonInput) return;
  const map = parseAddressBookPdfMeetingFontSizeMap(jsonInput.value);
  if (!Number.isFinite(value) || Math.abs(value - bookFontSize) < 0.0001) {
    delete map[key];
  } else {
    map[key] = Math.max(4, Math.min(36, value));
  }
  jsonInput.value = serializeAddressBookPdfMeetingFontSizeMap(map);
  if (resetButton) resetButton.hidden = !map[key];
}

function syncAddressBookPdfTypographyMeetingStatus() {
  const status = document.querySelector("[data-address-book-pdf-typography-meeting-status]");
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  if (!status) return;
  status.hidden = !(fieldSelect?.value && meetingSelect?.value);
}

function syncAddressBookPdfMeetingTypographyFromSelection(form = document.querySelector("[data-address-book-pdf-form]")) {
  syncAddressBookPdfTypographyMeetingStatus();
  syncAddressBookPdfMeetingLineSpacingFromSelection(form);
  syncAddressBookPdfMeetingFontSizeFromSelection(form);
}

function syncAddressBookPdfMeetingLineSpacingFromSelection(form = document.querySelector("[data-address-book-pdf-form]")) {
  if (!form) return;
  const visible = form.querySelector("[data-address-book-pdf-line-height-input]");
  const bookInput = form.querySelector("[data-address-book-pdf-book-line-height]");
  const jsonInput = form.querySelector("[data-address-book-pdf-meeting-line-height-json]");
  const resetButton = form.querySelector("[data-address-book-pdf-use-book-spacing]");
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  if (!visible || !bookInput) return;
  const bookValue = parseFloat(bookInput.value || "");
  const bookLineHeight = Number.isFinite(bookValue) ? Math.max(0.8, Math.min(3, bookValue)) : 1.2;
  const fieldName = fieldSelect?.value || "";
  const meetingName = meetingSelect?.value || "";
  const key = fieldName && meetingName ? addressBookPdfMeetingLineHeightKey(fieldName, meetingName) : "";
  const map = parseAddressBookPdfMeetingLineHeightMap(jsonInput?.value);
  if (!key) {
    visible.value = String(bookLineHeight);
    if (resetButton) resetButton.hidden = true;
    return;
  }
  const override = map[key];
  visible.value = String(override || bookLineHeight);
  if (resetButton) resetButton.hidden = !override;
}

function persistAddressBookPdfMeetingLineSpacing(form) {
  const visible = form.querySelector("[data-address-book-pdf-line-height-input]");
  const bookInput = form.querySelector("[data-address-book-pdf-book-line-height]");
  const jsonInput = form.querySelector("[data-address-book-pdf-meeting-line-height-json]");
  const resetButton = form.querySelector("[data-address-book-pdf-use-book-spacing]");
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  if (!visible || !bookInput) return;
  const value = parseFloat(visible.value || "");
  const bookValue = parseFloat(bookInput.value || "");
  const bookLineHeight = Number.isFinite(bookValue) ? Math.max(0.8, Math.min(3, bookValue)) : 1.2;
  const fieldName = fieldSelect?.value || "";
  const meetingName = meetingSelect?.value || "";
  const key = fieldName && meetingName ? addressBookPdfMeetingLineHeightKey(fieldName, meetingName) : "";
  if (!key) {
    if (Number.isFinite(value)) bookInput.value = String(Math.max(0.8, Math.min(3, value)));
    return;
  }
  if (!jsonInput) return;
  const map = parseAddressBookPdfMeetingLineHeightMap(jsonInput.value);
  if (!Number.isFinite(value) || Math.abs(value - bookLineHeight) < 0.0001) {
    delete map[key];
  } else {
    map[key] = Math.max(0.8, Math.min(3, value));
  }
  jsonInput.value = serializeAddressBookPdfMeetingLineHeightMap(map);
  if (resetButton) resetButton.hidden = !map[key];
}

function installAddressBookPdfTypographyInfo() {
  const infoButton = document.querySelector("[data-address-book-pdf-typography-info-open]");
  const infoModal = document.querySelector("[data-address-book-pdf-typography-info-modal]");
  infoButton?.addEventListener("click", () => {
    if (typeof infoModal?.showModal === "function") infoModal.showModal();
  });
}

function installAddressBookPdfMeetingLineSpacing(form) {
  const visible = form.querySelector("[data-address-book-pdf-line-height-input]");
  const resetButton = form.querySelector("[data-address-book-pdf-use-book-spacing]");
  if (!visible) return;
  visible.addEventListener("input", () => persistAddressBookPdfMeetingLineSpacing(form));
  visible.addEventListener("change", () => persistAddressBookPdfMeetingLineSpacing(form));
  resetButton?.addEventListener("click", () => {
    const jsonInput = form.querySelector("[data-address-book-pdf-meeting-line-height-json]");
    const bookInput = form.querySelector("[data-address-book-pdf-book-line-height]");
    const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
    const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
    const fieldName = fieldSelect?.value || "";
    const meetingName = meetingSelect?.value || "";
    const key = fieldName && meetingName ? addressBookPdfMeetingLineHeightKey(fieldName, meetingName) : "";
    if (!key || !jsonInput) return;
    const map = parseAddressBookPdfMeetingLineHeightMap(jsonInput.value);
    delete map[key];
    jsonInput.value = serializeAddressBookPdfMeetingLineHeightMap(map);
    const bookValue = parseFloat(bookInput?.value || "");
    visible.value = String(Number.isFinite(bookValue) ? Math.max(0.8, Math.min(3, bookValue)) : 1.2);
    resetButton.hidden = true;
    updateAddressBookPdfPreview();
  });
  syncAddressBookPdfMeetingLineSpacingFromSelection(form);
}

function installAddressBookPdfMeetingFontSize(form) {
  const visible = form.querySelector("[data-address-book-pdf-font-size-input]");
  const resetButton = form.querySelector("[data-address-book-pdf-use-book-size]");
  if (!visible) return;
  visible.addEventListener("input", () => persistAddressBookPdfMeetingFontSize(form));
  visible.addEventListener("change", () => persistAddressBookPdfMeetingFontSize(form));
  resetButton?.addEventListener("click", () => {
    const jsonInput = form.querySelector("[data-address-book-pdf-meeting-font-size-json]");
    const bookInput = form.querySelector("[data-address-book-pdf-book-font-size]");
    const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
    const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
    const fieldName = fieldSelect?.value || "";
    const meetingName = meetingSelect?.value || "";
    const key = fieldName && meetingName ? addressBookPdfMeetingLineHeightKey(fieldName, meetingName) : "";
    if (!key || !jsonInput) return;
    const map = parseAddressBookPdfMeetingFontSizeMap(jsonInput.value);
    delete map[key];
    jsonInput.value = serializeAddressBookPdfMeetingFontSizeMap(map);
    const bookValue = parseFloat(bookInput?.value || "");
    visible.value = String(Number.isFinite(bookValue) ? Math.max(1, Math.min(36, bookValue)) : 7);
    resetButton.hidden = true;
    updateAddressBookPdfPreview();
  });
  syncAddressBookPdfMeetingFontSizeFromSelection(form);
}

function addressBookPdfText(form, name, fallback) {
  const value = form.querySelector(`[name="${name}"]`)?.value || "";
  return value.trim() || fallback;
}

function addressBookPdfChoice(form, name, fallback) {
  return form.querySelector(`[name="${name}"]:checked`)?.value || fallback;
}

function addressBookPdfMapProvider() {
  const form = document.querySelector("[data-address-book-pdf-form]");
  return addressBookPdfChoice(form, "address_map_provider", "google") === "apple" ? "apple" : "google";
}

function escapeAddressBookPdfHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function printBookNotePlainText(value) {
  const source = String(value ?? "");
  if (!/[<>]/.test(source)) return source;
  const template = document.createElement("template");
  template.innerHTML = source;
  return template.content.textContent ?? "";
}

function sanitizeAddressBookPdfNoteHtml(value) {
  const source = String(value ?? "");
  if (!/[<>]/.test(source)) return escapeAddressBookPdfHtml(source);
  const template = document.createElement("template");
  template.innerHTML = source;
  const render = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return escapeAddressBookPdfHtml(node.textContent || "");
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const tag = node.tagName.toUpperCase();
    const children = Array.from(node.childNodes).map(render).join("");
    if (tag === "STRONG" || tag === "B") return `<strong>${children}</strong>`;
    if (tag === "EM" || tag === "I") return `<em>${children}</em>`;
    if (tag === "U") return `<u>${children}</u>`;
    if (tag === "SPAN") {
      const raw = node.getAttribute("data-note-size-delta");
      if (raw === null || raw === "") return children;
      const delta = Math.round(Number(raw) * 2) / 2;
      if (!Number.isFinite(delta) || delta === 0) return children;
      const clamped = Math.max(-6, Math.min(8, delta));
      const label = String(clamped);
      return `<span data-note-size-delta="${escapeAddressBookPdfHtml(label)}" style="font-size: calc(1em + ${escapeAddressBookPdfHtml(label)}pt)">${children}</span>`;
    }
    return children;
  };
  return Array.from(template.content.childNodes).map(render).join("");
}

function normalizeAddressBookPdfKey(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\bsouth\b/g, "s")
    .replace(/\bnorth\b/g, "n")
    .trim();
}

function sanitizeAddressBookPdfMeetingHtml(value) {
  const template = document.createElement("template");
  template.innerHTML = String(value || "");
  const allowedTags = new Set(["B", "STRONG", "I", "EM", "U", "BR"]);
  const appendTextWithBreaks = (fragment, text) => {
    String(text || "").split(/\r?\n/).forEach((part, index) => {
      if (index > 0) fragment.appendChild(document.createElement("br"));
      if (part) fragment.appendChild(document.createTextNode(part));
    });
  };
  const sanitizeNode = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const fragment = document.createDocumentFragment();
      appendTextWithBreaks(fragment, node.textContent || "");
      return fragment;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return document.createTextNode("");
    const tagName = node.tagName.toUpperCase();
    if (!allowedTags.has(tagName)) {
      const fragment = document.createDocumentFragment();
      Array.from(node.childNodes).forEach((child) => fragment.appendChild(sanitizeNode(child)));
      return fragment;
    }
    const clone = document.createElement(tagName.toLowerCase());
    if (tagName === "BR") return clone;
    Array.from(node.childNodes).forEach((child) => clone.appendChild(sanitizeNode(child)));
    return clone;
  };
  const fragment = document.createDocumentFragment();
  Array.from(template.content.childNodes).forEach((node) => fragment.appendChild(sanitizeNode(node)));
  const output = document.createElement("div");
  output.appendChild(fragment);
  return output.innerHTML.replace(/<br\s*\/?>/gi, "<br>");
}

function formatNameSuffixForParts(children, otherRelationships) {
  const childNames = [...new Set((children || []).map((child) => String(child || "").trim()).filter(Boolean))];
  const childText = childNames.join(", ");
  const labeledParts = [];
  const seen = new Set();
  (otherRelationships || []).forEach((relation) => {
    const label = String(relation?.relation_type || "").trim();
    const name = String(relation?.relation_value || "").trim();
    if (!name) return;
    const key = `${label.toLowerCase()}|${name.toLowerCase()}`;
    if (seen.has(key)) return;
    seen.add(key);
    labeledParts.push(label ? `${label}: ${name}` : name);
  });
  const labeledText = labeledParts.join(", ");
  if (childText && labeledText) return `; ${childText}, ${labeledText}`;
  if (childText) return `; ${childText}`;
  if (labeledText) return `, ${labeledText}`;
  return "";
}

function contactNameHead(contact, textOverride = null) {
  const text = String(textOverride ?? contact?.name ?? "CONTACT, Name").trim() || "CONTACT, Name";
  const semicolonIndex = text.indexOf("; ");
  if (semicolonIndex >= 0) return text.slice(0, semicolonIndex);
  const suffix = formatNameSuffixForParts(contact?.children, contact?.other_relationships);
  if (suffix && text.endsWith(suffix)) return text.slice(0, -suffix.length);
  return text;
}

function contactNameHtmlForAddressBookPdf(value, contact = null) {
  const text = String(value || contact?.name || "CONTACT, Name").trim() || "CONTACT, Name";
  const renderHead = (head) => {
    const commaIndex = head.indexOf(",");
    if (commaIndex < 0) return escapeAddressBookPdfHtml(head);
    const lastName = head.slice(0, commaIndex);
    const rest = head.slice(commaIndex);
    return `<strong>${escapeAddressBookPdfHtml(lastName)}</strong>${escapeAddressBookPdfHtml(rest)}`;
  };
  const renderSuffixFromContact = (item) => {
    const parts = [];
    (item?.children || []).forEach((child) => {
      const name = String(child || "").trim();
      if (name) parts.push(`<span class="address-book-pdf-relationship-child">${escapeAddressBookPdfHtml(name)}</span>`);
    });
    (item?.other_relationships || []).forEach((relation) => {
      const label = String(relation?.relation_type || "").trim();
      const name = String(relation?.relation_value || "").trim();
      if (!name) return;
      if (label) {
        parts.push(`<span class="address-book-pdf-relationship-pair">${escapeAddressBookPdfHtml(label)}: ${escapeAddressBookPdfHtml(name)}</span>`);
      } else {
        parts.push(escapeAddressBookPdfHtml(name));
      }
    });
    return parts.join(", ");
  };
  const parseSuffixSegmentsFromText = (suffix) => {
    const segments = [];
    let remaining = String(suffix || "").trim();
    while (remaining) {
      const labelMatch = remaining.match(/^([^:,]+):\s*/);
      if (labelMatch) {
        const label = labelMatch[1].trim();
        remaining = remaining.slice(labelMatch[0].length);
        const nextLabel = remaining.search(/,\s*(?:[^:,]+):\s*/);
        const name = (nextLabel >= 0 ? remaining.slice(0, nextLabel) : remaining).trim();
        remaining = nextLabel >= 0 ? remaining.slice(nextLabel + 1).trim() : "";
        segments.push({ type: "pair", label, name });
        continue;
      }
      const nextLabel = remaining.search(/,\s*(?:[^:,]+):\s*/);
      if (nextLabel >= 0) {
        const child = remaining.slice(0, nextLabel).trim().replace(/,$/, "");
        if (child) segments.push({ type: "child", name: child });
        remaining = remaining.slice(nextLabel + 1).trim();
        continue;
      }
      if (remaining) segments.push({ type: "child", name: remaining.trim() });
      remaining = "";
    }
    return segments;
  };
  const renderSuffixFromText = (suffix) => {
    return parseSuffixSegmentsFromText(suffix).map((segment) => {
      if (segment.type === "pair") {
        return `<span class="address-book-pdf-relationship-pair">${escapeAddressBookPdfHtml(segment.label)}: ${escapeAddressBookPdfHtml(segment.name)}</span>`;
      }
      return `<span class="address-book-pdf-relationship-child">${escapeAddressBookPdfHtml(segment.name)}</span>`;
    }).join(", ");
  };
  const renderSuffixHtml = (item, suffixText = "") => {
    const fromContact = renderSuffixFromContact(item);
    return fromContact || renderSuffixFromText(suffixText);
  };
  const head = contactNameHead(contact, text);
  const suffix = text.startsWith(head) ? text.slice(head.length) : "";
  if (suffix.startsWith("; ")) {
    return `${renderHead(head)}; ${renderSuffixHtml(contact, suffix.slice(2))}`;
  }
  if (suffix.startsWith(", ")) {
    return `${renderHead(head)}, ${renderSuffixHtml(contact, suffix.slice(2))}`;
  }
  const semicolonIndex = text.indexOf("; ");
  if (semicolonIndex >= 0) {
    const legacyHead = text.slice(0, semicolonIndex);
    const suffixText = text.slice(semicolonIndex + 2);
    return `${renderHead(legacyHead)}; ${renderSuffixHtml(contact, suffixText)}`;
  }
  return renderHead(text);
}

function contactNameSuffixHtmlForAddressBookPdf(value, contact = null) {
  const raw = String(value || "");
  const leadingChildPrefix = raw.startsWith("; ") ? "; " : "";
  const leadingSpace = !leadingChildPrefix && raw.startsWith(" ") ? "&nbsp;" : "";
  let suffixText = raw;
  if (suffixText.startsWith("; ")) suffixText = suffixText.slice(2);
  else if (suffixText.startsWith(" ")) suffixText = suffixText.slice(1);
  suffixText = suffixText.trim();
  const renderSuffixFromText = (suffix) => parseSuffixSegmentsFromTextForWrap(suffix).map((segment) => {
    if (segment.type === "pair") {
      return `<span class="address-book-pdf-relationship-pair">${escapeAddressBookPdfHtml(segment.label)}: ${escapeAddressBookPdfHtml(segment.name)}</span>`;
    }
    return `<span class="address-book-pdf-relationship-child">${escapeAddressBookPdfHtml(segment.name)}</span>`;
  }).join(", ");
  if (suffixText) return `${leadingChildPrefix}${leadingSpace}${renderSuffixFromText(suffixText)}`;
  const parts = [];
  (contact?.children || []).forEach((child) => {
    const name = String(child || "").trim();
    if (name) parts.push(`<span class="address-book-pdf-relationship-child">${escapeAddressBookPdfHtml(name)}</span>`);
  });
  (contact?.other_relationships || []).forEach((relation) => {
    const label = String(relation?.relation_type || "").trim();
    const name = String(relation?.relation_value || "").trim();
    if (!name) return;
    if (label) {
      parts.push(`<span class="address-book-pdf-relationship-pair">${escapeAddressBookPdfHtml(label)}: ${escapeAddressBookPdfHtml(name)}</span>`);
    } else {
      parts.push(escapeAddressBookPdfHtml(name));
    }
  });
  return parts.join(", ");
}

function relationshipSuffixTypedSegments(contact) {
  const segments = [];
  (contact?.children || []).forEach((child) => {
    const name = String(child || "").trim();
    if (name) segments.push({ kind: "child", text: name });
  });
  (contact?.other_relationships || []).forEach((relation) => {
    const label = String(relation?.relation_type || "").trim();
    const name = String(relation?.relation_value || "").trim();
    if (!name) return;
    segments.push({ kind: "labeled", text: label ? `${label}: ${name}` : name });
  });
  if (segments.length) return segments;
  const text = String(contact?.name || "").trim();
  const semicolonIndex = text.indexOf("; ");
  if (semicolonIndex < 0) return segments;
  const suffix = text.slice(semicolonIndex + 2);
  return parseSuffixSegmentsFromTextForWrap(suffix).map((segment) => ({
    kind: segment.type === "pair" ? "labeled" : "child",
    text: segment.type === "pair" ? `${segment.label}: ${segment.name}` : segment.name,
  }));
}

function relationshipSuffixSegments(contact) {
  return relationshipSuffixTypedSegments(contact).map((segment) => segment.text);
}

function parseSuffixSegmentsFromTextForWrap(suffix) {
  const segments = [];
  let remaining = String(suffix || "").trim();
  while (remaining) {
    const labelMatch = remaining.match(/^([^:,]+):\s*/);
    if (labelMatch) {
      const label = labelMatch[1].trim();
      remaining = remaining.slice(labelMatch[0].length);
      const nextLabel = remaining.search(/,\s*(?:[^:,]+):\s*/);
      const name = (nextLabel >= 0 ? remaining.slice(0, nextLabel) : remaining).trim();
      remaining = nextLabel >= 0 ? remaining.slice(nextLabel + 1).trim() : "";
      segments.push({ type: "pair", label, name });
      continue;
    }
    const nextLabel = remaining.search(/,\s*(?:[^:,]+):\s*/);
    if (nextLabel >= 0) {
      const child = remaining.slice(0, nextLabel).trim().replace(/,$/, "");
      if (child) segments.push({ type: "child", name: child });
      remaining = remaining.slice(nextLabel + 1).trim();
      continue;
    }
    if (remaining) segments.push({ type: "child", name: remaining.trim() });
    remaining = "";
  }
  return segments;
}

const ADDRESS_BOOK_PDF_HELVETICA_WIDTHS = {
  " ": 278, "!": 278, '"': 355, "#": 556, $: 556, "%": 889, "&": 667, "'": 191,
  "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
  "0": 556, "1": 556, "2": 556, "3": 556, "4": 556, "5": 556, "6": 556, "7": 556, "8": 556, "9": 556,
  ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015,
  A: 667, B: 667, C: 722, D: 722, E: 667, F: 611, G: 778, H: 722, I: 278,
  J: 500, K: 667, L: 556, M: 833, N: 722, O: 778, P: 667, Q: 778, R: 722,
  S: 667, T: 611, U: 722, V: 667, W: 944, X: 667, Y: 667, Z: 611,
  "[": 278, "\\": 278, "]": 278, "^": 469, _: 556, "`": 333,
  a: 556, b: 556, c: 500, d: 556, e: 556, f: 278, g: 556, h: 556, i: 222,
  j: 222, k: 500, l: 222, m: 833, n: 556, o: 556, p: 556, q: 556, r: 333,
  s: 500, t: 278, u: 556, v: 500, w: 722, x: 500, y: 500, z: 500,
  "{": 334, "|": 260, "}": 334, "~": 584,
};

const ADDRESS_BOOK_PDF_TIMES_WIDTHS = {
  " ": 250, "!": 333, '"': 408, "#": 500, $: 500, "%": 833, "&": 778, "'": 180,
  "(": 333, ")": 333, "*": 500, "+": 564, ",": 250, "-": 333, ".": 250, "/": 278,
  "0": 500, "1": 500, "2": 500, "3": 500, "4": 500, "5": 500, "6": 500, "7": 500, "8": 500, "9": 500,
  ":": 333, ";": 333, "<": 564, "=": 564, ">": 564, "?": 444, "@": 921,
  A: 722, B: 667, C: 667, D: 722, E: 611, F: 556, G: 722, H: 722, I: 333,
  J: 389, K: 722, L: 611, M: 889, N: 722, O: 722, P: 667, Q: 722, R: 722,
  S: 556, T: 611, U: 722, V: 722, W: 944, X: 722, Y: 722, Z: 611,
  "[": 333, "\\": 278, "]": 333, "^": 469, _: 500, "`": 333,
  a: 444, b: 500, c: 444, d: 500, e: 444, f: 333, g: 500, h: 500, i: 278,
  j: 278, k: 500, l: 278, m: 778, n: 500, o: 500, p: 500, q: 500, r: 333,
  s: 389, t: 278, u: 500, v: 500, w: 722, x: 500, y: 500, z: 444,
  "{": 480, "|": 200, "}": 480, "~": 541,
};

const ADDRESS_BOOK_PDF_FONT_WIDTHS = {
  helvetica: ADDRESS_BOOK_PDF_HELVETICA_WIDTHS,
  times: ADDRESS_BOOK_PDF_TIMES_WIDTHS,
};

function addressBookPdfFontGroup(fontFamily = "Arial") {
  const name = String(fontFamily || "Arial").trim().toLowerCase();
  if (name === "courier new" || name === "courier") return "courier";
  if (name === "times new roman" || name === "georgia" || name === "times") return "times";
  return "helvetica";
}

function measureAddressBookPdfTextWidth(text, fontSizePx, fontFamily = "Arial") {
  const value = String(text || "");
  if (!value) return 0;
  const group = addressBookPdfFontGroup(fontFamily);
  if (typeof document !== "undefined") {
    try {
      const canvas = measureAddressBookPdfTextWidth._canvas
        || (measureAddressBookPdfTextWidth._canvas = document.createElement("canvas"));
      const ctx = canvas.getContext("2d");
      if (ctx) {
        const fallback = group === "courier" ? "monospace" : group === "times" ? "serif" : "sans-serif";
        ctx.font = `${fontSizePx}px "${fontFamily}", ${fallback}`;
        const width = ctx.measureText(value).width;
        if (Number.isFinite(width) && width > 0) return width;
      }
    } catch (_error) {
      // Use the built-in width tables when canvas fonts are unavailable.
    }
  }
  if (group === "courier") {
    return value.length * 600 * fontSizePx / 1000;
  }
  const widths = ADDRESS_BOOK_PDF_FONT_WIDTHS[group] || ADDRESS_BOOK_PDF_FONT_WIDTHS.helvetica;
  const fallback = group === "times" ? 444 : 556;
  return [...value].reduce(
    (sum, char) => sum + (widths[char] ?? fallback),
    0,
  ) * fontSizePx / 1000;
}

function addressBookPdfPreviewLayoutMetrics() {
  const form = document.querySelector("[data-address-book-pdf-form]");
  const printable = document.querySelector("[data-address-book-pdf-printable]");
  const trimWidth = Math.max(0.1, addressBookPdfNumber(form, "trim_width_in", 3.5));
  const trimHeight = Math.max(0.1, addressBookPdfNumber(form, "trim_height_in", 5));
  const marginLeft = Math.max(0, addressBookPdfNumber(form, "margin_left_in", 0.14));
  const marginRight = Math.max(0, addressBookPdfNumber(form, "margin_right_in", 0.14));
  const fontSizePt = addressBookPdfPreviewFontSize(form);
  const fontFamily = addressBookPdfText(form, "font_family", "Arial");
  const fitScale = Math.min(430 / trimWidth, 650 / trimHeight, 150);
  const fontSizePx = fontSizePt * (fitScale / 72);
  const printableWidthPx = printable?.clientWidth || Math.max(120, (trimWidth - marginLeft - marginRight) * fitScale);
  return { fontSizePx, printableWidthPx, fontFamily };
}

function addressBookPdfContactNameMaxWidthPx(printableWidthPx, fontSizePx, fontFamily = "Arial") {
  const numberWidth = measureAddressBookPdfTextWidth("000-000-0000", fontSizePx, fontFamily);
  const codeWidth = measureAddressBookPdfTextWidth("MM", fontSizePx, fontFamily);
  const spaceWidth = measureAddressBookPdfTextWidth(" ", fontSizePx, fontFamily);
  return Math.max(120, printableWidthPx - (numberWidth + spaceWidth + codeWidth + spaceWidth + spaceWidth));
}

function wrapAddressBookPdfNameWords(text, measureWidth, maxWidth) {
  const value = String(text || "").trim();
  if (!value) return [];
  if (measureWidth(value) <= maxWidth) return [value];
  const words = value.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [value];
  const lines = [];
  let current = words[0];
  words.slice(1).forEach((word) => {
    const candidate = `${current} ${word}`;
    if (measureWidth(candidate) <= maxWidth) current = candidate;
    else {
      lines.push(current);
      current = word;
    }
  });
  if (current) lines.push(current);
  return lines;
}

function wrapAddressBookPdfRelationshipText(text, measureWidth, maxWidth) {
  const value = String(text || "").trim();
  if (!value) return [];
  if (measureWidth(value) <= maxWidth) return [value];
  const commaParts = value.split(",").map((part) => part.trim()).filter(Boolean);
  if (commaParts.length > 1) {
    const lines = [];
    let current = "";
    commaParts.forEach((part) => {
      const candidate = current ? `${current}, ${part}` : part;
      if (current && measureWidth(candidate) > maxWidth) {
        lines.push(current.endsWith(",") ? current : `${current},`);
        if (measureWidth(part) > maxWidth) {
          lines.push(...wrapAddressBookPdfNameWords(part, measureWidth, maxWidth));
          current = "";
        } else {
          current = part;
        }
      } else if (!current && measureWidth(part) > maxWidth) {
        lines.push(...wrapAddressBookPdfNameWords(part, measureWidth, maxWidth));
        current = "";
      } else {
        current = candidate;
      }
    });
    if (current) lines.push(current);
    if (lines.length) return lines;
  }
  return wrapAddressBookPdfNameWords(value, measureWidth, maxWidth);
}

function packTypedSuffixLinesForAddressBookPdf(
  typedSegments,
  measureWidth,
  maxWidth,
  { semicolonOnPreviousLine = false } = {},
) {
  if (!typedSegments.length) return [];
  const lines = [];
  let current = "";
  let isFirstSuffixLine = true;

  typedSegments.forEach(({ kind, text }) => {
    let piece;
    if (!current) {
      let prefix;
      if (semicolonOnPreviousLine && isFirstSuffixLine) {
        prefix = "";
      } else if (kind === "child" && isFirstSuffixLine) {
        prefix = "; ";
      } else if (isFirstSuffixLine) {
        prefix = " ";
      } else {
        prefix = ", ";
      }
      piece = `${prefix}${text}`;
    } else {
      piece = `${current}, ${text}`;
    }

    if (current && measureWidth(piece) > maxWidth) {
      isFirstSuffixLine = false;
      let nextText;
      if (kind === "child") {
        lines.push(current.endsWith(",") ? current : `${current},`);
        nextText = text;
      } else {
        lines.push(current);
        nextText = ` ${text}`;
      }
      if (measureWidth(nextText) > maxWidth) {
        lines.push(...wrapAddressBookPdfRelationshipText(nextText.trimStart(), measureWidth, maxWidth));
        current = "";
      } else {
        current = nextText;
      }
    } else if (!current) {
      if (measureWidth(piece) > maxWidth) {
        lines.push(...wrapAddressBookPdfRelationshipText(piece.trimStart(), measureWidth, maxWidth));
        isFirstSuffixLine = false;
        current = "";
      } else {
        current = piece;
      }
    } else {
      current = piece;
    }
  });

  if (current) {
    if (measureWidth(current) > maxWidth) {
      lines.push(...wrapAddressBookPdfRelationshipText(current.trimStart(), measureWidth, maxWidth));
    } else {
      lines.push(current);
    }
  }
  return lines;
}

function wrapAddressBookPdfContactNameLines(contact, maxWidthPx, fontSizePx = 10, fontFamily = "Arial") {
  const typedSegments = relationshipSuffixTypedSegments(contact);
  const head = contactNameHead(contact);
  const measureWidth = (text) => measureAddressBookPdfTextWidth(text, fontSizePx, fontFamily);
  if (!typedSegments.length) {
    const text = String(contact?.name || "CONTACT, Name").trim();
    return [text];
  }
  const suffix = formatNameSuffixForParts(contact?.children, contact?.other_relationships);
  const full = `${head}${suffix}`;
  if (measureWidth(full) <= maxWidthPx) return [full];

  let headLine = head;
  let semicolonOnPreviousLine = false;
  if (typedSegments.length && typedSegments[0].kind === "child") {
    const headWithSemi = `${head};`;
    if (measureWidth(headWithSemi) <= maxWidthPx) {
      headLine = headWithSemi;
      semicolonOnPreviousLine = true;
    }
  }

  return [
    headLine,
    ...packTypedSuffixLinesForAddressBookPdf(typedSegments, measureWidth, maxWidthPx, { semicolonOnPreviousLine }),
  ];
}

function splitAddressBookPdfTypePrefix(text) {
  const value = String(text || "");
  if (!value.trim()) return { label: "", remainder: "" };
  const lines = value.replace(/\r\n/g, "\n").split("\n");
  const match = String(lines[0] || "").trim().match(/^([A-Za-z][A-Za-z0-9 /._-]{0,19}):\s*(.*)$/);
  if (!match) return { label: "", remainder: value.trim() };
  const remainderLines = [match[2].trim(), ...lines.slice(1)];
  return {
    label: match[1].trim(),
    remainder: remainderLines.filter((line, index) => line || index === 0).join("\n").trim(),
  };
}

function splitAddressBookPdfStreetAndCity(address) {
  const text = String(address || "").trim();
  if (!text) return { street1: "", street2: "", city: "" };
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const cityLineRe = /^(.+?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$/;
  let street = "";
  let city = "";
  if (lines.length >= 2 && cityLineRe.test(lines[lines.length - 1])) {
    street = lines.slice(0, -1).join(", ");
    city = lines[lines.length - 1];
  } else {
    const oneLine = lines.join(", ");
    const match = oneLine.match(/^(.*?),\s*([^,]+,\s*[A-Za-z]{2}\s+\d{5}(?:-\d{4})?)\s*$/);
    if (match) {
      street = match[1].trim();
      city = match[2].trim();
    } else {
      street = oneLine;
    }
  }
  const comma = street.indexOf(",");
  if (comma > 0) {
    return {
      street1: street.slice(0, comma).trim(),
      street2: street.slice(comma + 1).trim(),
      city,
    };
  }
  return { street1: street, street2: "", city };
}

function addressBookPdfIsUnitOrAptPart(part) {
  return /^(?:#\S+|(?:apt|apartment|suite|ste|unit)\b.*)$/i.test(String(part || "").trim());
}

function addressBookPdfWrapAddressWords(text, maxWidthPx, fontSizePx = 10, fontFamily = "Arial") {
  const value = String(text || "").trim();
  if (!value) return [];
  const fits = (line) => measureAddressBookPdfTextWidth(line, fontSizePx, fontFamily) <= maxWidthPx;
  if (fits(value)) return [value];
  const words = value.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [value];
  const lines = [];
  let current = words[0];
  words.slice(1).forEach((word) => {
    const candidate = `${current} ${word}`;
    if (fits(candidate)) current = candidate;
    else {
      lines.push(current);
      current = word;
    }
  });
  if (current) lines.push(current);
  return lines;
}

function addressBookPdfWrapAddressText(text, maxWidthPx, fontSizePx = 10, fontFamily = "Arial") {
  const value = String(text || "").trim();
  if (!value) return [];
  const fits = (line) => measureAddressBookPdfTextWidth(line, fontSizePx, fontFamily) <= maxWidthPx;
  if (fits(value)) return [value];

  const parts = value.split(",").map((part) => part.trim()).filter(Boolean);
  if (parts.length > 1) {
    const lines = [];
    let current = parts[0];
    parts.slice(1).forEach((part) => {
      const candidate = `${current}, ${part}`;
      if (fits(candidate) || addressBookPdfIsUnitOrAptPart(part)) {
        current = candidate;
      } else {
        lines.push(`${current},`);
        current = part;
      }
    });
    if (current) lines.push(current);
    return lines.flatMap((line) => (
      fits(line)
        ? [line]
        : addressBookPdfWrapAddressWords(line.replace(/,$/, "").trim(), maxWidthPx, fontSizePx, fontFamily)
    ));
  }

  return addressBookPdfWrapAddressWords(value, maxWidthPx, fontSizePx, fontFamily);
}

function addressBookPdfAddressDisplayLines(address, maxWidthPx = null, fontSizePx = 10, fontFamily = "Arial") {
  const entry = (address && typeof address === "object") ? address : { text: address };
  const rawText = String(entry.text || address || "").trim();
  const parsedPrefix = splitAddressBookPdfTypePrefix(rawText);
  const text = parsedPrefix.remainder;
  let addressLabel = String(entry.address_type || "").trim();
  if (!addressLabel) addressLabel = parsedPrefix.label;
  if (addressLabel.toLowerCase() === "home") addressLabel = "";
  const prefix = addressLabel ? `${addressLabel}: ` : "";
  if (!text && !entry.street_address && !entry.extended_address) return [];

  let street1 = String(entry.street_address || "").trim();
  const street1Prefix = splitAddressBookPdfTypePrefix(street1);
  if (street1Prefix.label && (!addressLabel || street1Prefix.label.toLowerCase() === addressLabel.toLowerCase())) {
    street1 = street1Prefix.remainder;
  }
  let street2 = String(entry.extended_address || "").trim();
  let city = "";
  const cityFromParts = [entry.city, entry.region, entry.postal_code]
    .map((part) => String(part || "").trim())
    .filter(Boolean);
  if (entry.city && entry.region && entry.postal_code) {
    city = `${String(entry.city).trim()}, ${String(entry.region).trim()} ${String(entry.postal_code).trim()}`;
  } else if (cityFromParts.length) {
    city = cityFromParts.join(", ");
  }

  if (!street1 && !street2) {
    const parsed = splitAddressBookPdfStreetAndCity(text);
    street1 = parsed.street1;
    street2 = parsed.street2;
    if (!city) city = parsed.city;
  } else if (!city) {
    const parsed = splitAddressBookPdfStreetAndCity(text);
    if (!city) city = parsed.city;
  }

  const withPrefix = (line) => {
    if (!prefix || !line) return line;
    if (String(line).toLowerCase().startsWith(prefix.toLowerCase())) return line;
    return `${prefix}${line}`;
  };
  const fits = (line) => (
    maxWidthPx == null || !(maxWidthPx > 0)
    || measureAddressBookPdfTextWidth(line, fontSizePx, fontFamily) <= maxWidthPx
  );
  const fitsFirst = (line) => fits(withPrefix(line));
  const applyPrefix = (lines) => (prefix && lines.length ? [withPrefix(lines[0]), ...lines.slice(1)] : lines);

  const street2Parts = street2.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const streetParts = [street1, ...street2Parts].filter(Boolean);
  const wrap = (value, firstLine = false) => {
    if (maxWidthPx == null || !(maxWidthPx > 0)) return [value];
    if (firstLine && prefix) {
      const prefixed = withPrefix(value);
      if (fits(prefixed)) return [value];
    }
    return addressBookPdfWrapAddressText(value, maxWidthPx, fontSizePx, fontFamily);
  };
  const withCity = (lines) => {
    if (!city) return lines;
    if (!lines.length) return wrap(city, true);
    const combined = `${lines[lines.length - 1]}, ${city}`;
    if (fits(combined)) {
      return [...lines.slice(0, -1), combined];
    }
    return [...lines, city];
  };
  if (!streetParts.length && !city) {
    return applyPrefix(text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean));
  }
  if (!streetParts.length) return applyPrefix(wrap(city, true));
  if (street1 && street2Parts.length) {
    return applyPrefix(withCity([
      ...wrap(street1, true),
      ...street2Parts.flatMap((part) => wrap(part) || [part]),
    ]));
  }
  const street = streetParts.join(", ");
  if (!city) return applyPrefix(wrap(street, true));
  const oneLine = `${street}, ${city}`;
  if (fitsFirst(oneLine)) {
    return [withPrefix(oneLine)];
  }
  if (fitsFirst(street)) {
    return [withPrefix(street), city];
  }
  return applyPrefix(withCity(addressBookPdfWrapAddressText(street, maxWidthPx, fontSizePx, fontFamily)));
}

function addressBookPdfAddressEntry(address) {
  if (address && typeof address === "object") {
    const coordinates = String(address.coordinates || "").trim();
    return {
      text: String(address.text || "").trim(),
      address_type: String(address.address_type || "").trim(),
      street_address: String(address.street_address || "").trim(),
      extended_address: String(address.extended_address || "").trim(),
      city: String(address.city || "").trim(),
      region: String(address.region || "").trim(),
      postal_code: String(address.postal_code || "").trim(),
      coordinates,
      hasCoordinates: Boolean(address.has_coordinates || coordinates),
    };
  }
  return {
    text: String(address || "").trim(),
    address_type: "",
    street_address: "",
    extended_address: "",
    city: "",
    region: "",
    postal_code: "",
    coordinates: "",
    hasCoordinates: false,
  };
}

function addressBookPdfMapUrl(coordinates, addressText) {
  let value = String(coordinates || "").trim().replace(/\s*,\s*/g, ",");
  if (!value) {
    value = String(addressText || "").trim().replace(/\s*\n\s*/g, ", ");
  }
  if (!value) return "";
  const provider = addressBookPdfMapProvider();
  const host = provider === "apple" ? "maps.apple.com" : "maps.google.com";
  return `http://${host}/?q=${encodeURIComponent(value)}`;
}

function getAddressBookPdfPrintOrder() {
  const input = document.querySelector("[data-address-book-pdf-print-order-json]");
  try {
    const payload = JSON.parse(input?.value || "{}");
    return payload && typeof payload === "object" ? payload : { fields: [] };
  } catch {
    return { fields: [] };
  }
}

function setAddressBookPdfPrintOrder(payload) {
  const input = document.querySelector("[data-address-book-pdf-print-order-json]");
  if (!input) return;
  input.value = JSON.stringify(payload || { fields: [] });
}

function persistAddressBookPdfPrintOrder() {
  const input = document.querySelector("[data-address-book-pdf-print-order-json]");
  const customMode = document.querySelector('[name="print_order_mode"][value="custom"]');
  if (customMode) customMode.checked = true;
  if (!input) return Promise.resolve();
  const body = new FormData();
  body.set("print_order_json", input.value || '{"fields":[]}');
  body.set("print_order_mode", "custom");
  return fetch("/address-book/pdf/print-order", {
    method: "POST",
    body,
    credentials: "same-origin",
  }).catch(() => {});
}

function addressBookPdfPrintOrderLookup() {
  const payload = getAddressBookPdfPrintOrder();
  const fields = new Map();
  const meetings = new Map();
  (payload.fields || []).forEach((field, fieldIndex) => {
    const fieldName = String(field.name || "");
    fields.set(fieldName, Number(field.order || fieldIndex + 1));
    (field.meetings || []).forEach((meeting, meetingIndex) => {
      meetings.set(`${fieldName}\n${String(meeting.name || "")}`, Number(meeting.order || meetingIndex + 1));
    });
  });
  return { fields, meetings };
}

function getAddressBookPdfOrderedFields(fields = getAddressBookPdfPickerFields()) {
  const lookup = addressBookPdfPrintOrderLookup();
  return (fields || [])
    .map((field, fieldIndex) => {
      const fieldName = String(field.name || "");
      const meetings = (field.meetings || [])
        .map((meeting, meetingIndex) => ({
          ...meeting,
          name: String(meeting.name || ""),
          order: lookup.meetings.get(`${fieldName}\n${String(meeting.name || "")}`) || meetingIndex + 1_000_000,
        }))
        .sort((left, right) => left.order - right.order || left.name.localeCompare(right.name));
      return {
        ...field,
        name: fieldName,
        order: lookup.fields.get(fieldName) || fieldIndex + 1_000_000,
        meetings,
      };
    })
    .sort((left, right) => left.order - right.order || left.name.localeCompare(right.name));
}

function phoneNumberHtmlForAddressBookPdf(value) {
  const text = String(value || "").trim();
  const digits = text.replace(/\D/g, "");
  if (digits.length !== 10) return escapeAddressBookPdfHtml(text);
  const callPart = `${digits.slice(0, 3)}-${digits.slice(3, 6)}`;
  const messagePart = digits.slice(6);
  return `
    <a class="address-book-pdf-phone-call-link" href="tel:${digits}">${escapeAddressBookPdfHtml(callPart)}</a><span class="address-book-pdf-phone-separator">-</span><a class="address-book-pdf-phone-message-link" href="sms:${digits}">${escapeAddressBookPdfHtml(messagePart)}</a>
  `;
}

function getAddressBookPdfPickerFields() {
  const palette = document.querySelector("[data-address-book-pdf-picker]");
  if (!palette) return [];
  if (Array.isArray(window.addressBookPdfPickerFields)) return window.addressBookPdfPickerFields;
  try {
    const payload = JSON.parse(palette.dataset.addressBookPdfPicker || "{}");
    window.addressBookPdfPickerFields = Array.isArray(payload.fields) ? payload.fields : [];
  } catch {
    window.addressBookPdfPickerFields = [];
  }
  return window.addressBookPdfPickerFields;
}

function getAddressBookPdfMeetingPreviewSections() {
  const palette = document.querySelector("[data-address-book-pdf-picker]");
  if (!palette) return [];
  if (Array.isArray(window.addressBookPdfMeetingPreviewSections)) return window.addressBookPdfMeetingPreviewSections;
  try {
    const payload = JSON.parse(palette.dataset.addressBookPdfMeetingPreview || "{}");
    window.addressBookPdfMeetingPreviewSections = Array.isArray(payload.sections) ? payload.sections : [];
  } catch {
    window.addressBookPdfMeetingPreviewSections = [];
  }
  return window.addressBookPdfMeetingPreviewSections;
}

function addressBookPdfPreviewShowsWholeField() {
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  return Boolean(fieldSelect?.value) && !meetingSelect?.value;
}

function addressBookPdfPreviewIsAllFieldsAndMeetings() {
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  return !String(fieldSelect?.value || "").trim() && !String(meetingSelect?.value || "").trim();
}

function addressBookPdfPreviewHasMeetingSelected() {
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  return Boolean(String(meetingSelect?.value || "").trim());
}

const ADDRESS_BOOK_PDF_LAST_COVER_KEY = "addressBookPdfLastCoverPicture";
let addressBookPdfCoverPreviewWasAll = null;

function readAddressBookPdfLastCover() {
  try {
    return JSON.parse(window.localStorage.getItem(ADDRESS_BOOK_PDF_LAST_COVER_KEY) || "null");
  } catch {
    return null;
  }
}

function fillAddressBookPdfCoverImageFromLastCover() {
  const dataInput = document.querySelector("[data-address-book-pdf-cover-image-data]");
  if (dataInput?.value) return Boolean(dataInput.value);
  const lastCover = readAddressBookPdfLastCover();
  if (!lastCover?.data || !dataInput) return false;
  dataInput.value = lastCover.data;
  const widthInput = document.querySelector("[data-address-book-pdf-cover-image-width]");
  const heightInput = document.querySelector("[data-address-book-pdf-cover-image-height]");
  const labelInput = document.querySelector("[data-address-book-pdf-cover-image-label]");
  if (widthInput) widthInput.value = lastCover.width ? String(lastCover.width) : "";
  if (heightInput) heightInput.value = lastCover.height ? String(lastCover.height) : "";
  if (labelInput) labelInput.value = lastCover.label || "";
  return true;
}

function syncAddressBookPdfCoverChoiceVisibility() {
  const includeCheckbox = document.querySelector("[data-address-book-pdf-cover-include]");
  const openButton = document.querySelector("[data-address-book-pdf-cover-open]");
  const dateRow = document.querySelector(".address-book-pdf-cover-date-row");
  const summaryEl = document.querySelector("[data-address-book-pdf-cover-summary]");
  if (!includeCheckbox) return;
  const showControls = Boolean(includeCheckbox.checked);
  if (openButton) openButton.hidden = !showControls;
  if (dateRow) dateRow.hidden = !showControls;
  if (summaryEl && !showControls) summaryEl.hidden = true;
}

function applyAddressBookPdfCoverIncludeFromCheckbox() {
  const includeCheckbox = document.querySelector("[data-address-book-pdf-cover-include]");
  const includeValue = document.querySelector("[data-address-book-pdf-include-cover-value]");
  if (!includeCheckbox) return;
  if (includeCheckbox.checked) {
    fillAddressBookPdfCoverImageFromLastCover();
  }
  const imageData = document.querySelector("[data-address-book-pdf-cover-image-data]")?.value || "";
  if (includeValue) includeValue.value = includeCheckbox.checked && imageData ? "1" : "0";
  syncAddressBookPdfCoverChoiceVisibility();
  updateAddressBookPdfCoverCanvas();
}

function turnOnAddressBookPdfCoverIfImageAvailable() {
  const includeCheckbox = document.querySelector("[data-address-book-pdf-cover-include]");
  const includeValue = document.querySelector("[data-address-book-pdf-include-cover-value]");
  if (!includeCheckbox) return;
  const hasImage = fillAddressBookPdfCoverImageFromLastCover()
    || Boolean(document.querySelector("[data-address-book-pdf-cover-image-data]")?.value);
  if (hasImage) {
    includeCheckbox.checked = true;
    if (includeValue) includeValue.value = "1";
  }
  syncAddressBookPdfCoverChoiceVisibility();
}

function turnOffAddressBookPdfCoverForMeetingPreview() {
  const includeCheckbox = document.querySelector("[data-address-book-pdf-cover-include]");
  if (!includeCheckbox) return;
  includeCheckbox.checked = false;
  syncAddressBookPdfCoverChoiceVisibility();
}

function syncAddressBookPdfCoverForPreviewPickers() {
  const isAll = addressBookPdfPreviewIsAllFieldsAndMeetings();
  const enteringAllPreview = addressBookPdfCoverPreviewWasAll !== true && isAll;
  addressBookPdfCoverPreviewWasAll = isAll;
  if (enteringAllPreview) {
    turnOnAddressBookPdfCoverIfImageAvailable();
    return;
  }
  syncAddressBookPdfCoverChoiceVisibility();
}

function addressBookPdfMeetingHeading(meetingName, homeLastName = "") {
  const name = String(meetingName || "").trim();
  if (!name) return name;
  const parenMatch = name.match(/^(.+?)\s+\(([^)]+)\)\s*$/);
  if (parenMatch) {
    const base = parenMatch[1].trim();
    const surname = parenMatch[2].trim().toUpperCase();
    return `${base} (${surname})`;
  }
  const homeName = String(homeLastName || "").trim().toUpperCase();
  return homeName ? `${name} (${homeName})` : name;
}

function findAddressBookPdfMeetingPreviewRows(meetingName, fieldName) {
  const selectedMeetingKey = normalizeAddressBookPdfKey(meetingName);
  const selectedFieldKey = normalizeAddressBookPdfKey(fieldName);
  if (!selectedMeetingKey) return [];
  const scored = getAddressBookPdfMeetingPreviewSections()
    .map((section) => {
      const meetingKey = normalizeAddressBookPdfKey(section.meeting || section.meeting_key);
      const fieldKey = normalizeAddressBookPdfKey(section.field || section.field_key);
      const meetingMatches =
        meetingKey === selectedMeetingKey ||
        meetingKey.startsWith(selectedMeetingKey) ||
        selectedMeetingKey.startsWith(meetingKey);
      if (!meetingMatches) return null;
      const fieldMatches =
        fieldKey === selectedFieldKey ||
        (selectedFieldKey && fieldKey.includes(selectedFieldKey)) ||
        (fieldKey && selectedFieldKey.includes(fieldKey));
      return { score: fieldMatches ? 2 : 1, rows: section.rows || [] };
    })
    .filter(Boolean)
    .sort((left, right) => right.score - left.score);
  return scored[0]?.rows || [];
}

function addressBookPdfMeetingPreviewRows() {
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  return findAddressBookPdfMeetingPreviewRows(meetingSelect?.value || "", fieldSelect?.value || "");
}

function getAddressBookPdfPreviewContacts() {
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  const fields = getAddressBookPdfOrderedFields();
  const selectedFieldName = fieldSelect?.value || "";
  const selectedMeetingName = meetingSelect?.value || "";
  const scopedFields = selectedFieldName ? fields.filter((field) => field.name === selectedFieldName) : fields;
  const contacts = [];

  scopedFields.forEach((field) => {
    const meetings = selectedMeetingName
      ? (field.meetings || []).filter((meeting) => meeting.name === selectedMeetingName)
      : (field.meetings || []);
    meetings.forEach((meeting) => {
      (meeting.contacts || []).forEach((contact) => contacts.push(contact));
    });
  });
  return selectedFieldName ? contacts : contacts.slice(0, 10);
}

function meetingPreviewRowFontSizePt(row) {
  const explicit = Number(row?.font_size_pt || 0);
  if (explicit > 0) return explicit;
  const effective = Number(row?.effective_font_size_pt || 0);
  return effective > 0 ? effective : 0;
}

function meetingPreviewGridGapPx(row) {
  const gapPx = Math.max(0, Math.min(48, Number(row?.column_gap_px || 6)));
  const previewWidthPx = Number(row?.preview_printable_width_px || 0);
  if (previewWidthPx > 0) {
    const { printableWidthPx } = addressBookPdfPreviewLayoutMetrics();
    if (printableWidthPx > 0) {
      return Math.max(0, gapPx * (printableWidthPx / previewWidthPx));
    }
  }
  return gapPx;
}

function flowColumnSegments(column) {
  const segments = column?.segments;
  if (Array.isArray(segments) && segments.length) return segments;
  return [{ html: column?.html, align: column?.align }];
}

function flowSegmentAlignClass(align) {
  return align === "right" ? "align-right" : align === "center" || align === "auto" ? "align-center" : "align-left";
}

function flowRowPrintLineCount(row) {
  if (row.flow_row_line_count) return Math.max(1, Number(row.flow_row_line_count));
  const primaries = row.columns || [];
  const stacked = row.stacked_columns || [];
  const primaryCounts = Object.fromEntries(
    primaries.map((column) => [Number(column.cell_id || 0), Math.max(1, flowColumnSegments(column).reduce((sum, segment) => sum + Math.max(1, String(segment.html || "").split(/<br\s*\/?>|\r?\n/i).length), 0))]),
  );
  let extent = Math.max(1, ...Object.values(primaryCounts).filter(Boolean));
  stacked.forEach((column) => {
    const parentLines = primaryCounts[Number(column.parent_cell_id || 0)] || 1;
    const lines = Math.max(1, flowColumnSegments(column).reduce((sum, segment) => sum + Math.max(1, String(segment.html || "").split(/<br\s*\/?>|\r?\n/i).length), 0));
    extent = Math.max(extent, parentLines + lines);
  });
  return extent;
}

function renderFlowGridColumnHtml(column) {
  const colStart = Math.max(1, Math.min(28, Number(column.col_start || 1)));
  const colSpan = Math.max(1, Math.min(28, Number(column.col_span || 1)));
  const rowStart = Math.max(1, Number(column.grid_row_start || 1));
  const rowSpan = Math.max(1, Number(column.grid_row_span || 1));
  const segmentHtml = flowColumnSegments(column).map((segment, index) => {
    if (!String(segment.html || "").trim()) return "";
    const alignClass = flowSegmentAlignClass(segment.align);
    const stackedClass = index > 0 ? " is-stacked-segment" : "";
    return `<div class="field-list-meeting-preview-flow-text ${alignClass}${stackedClass}">${sanitizeAddressBookPdfMeetingHtml(segment.html)}</div>`;
  }).join("");
  if (!segmentHtml) return "";
  return `
    <div class="field-list-meeting-preview-cell field-list-meeting-preview-flow-column" style="grid-column: ${colStart} / span ${colSpan}; grid-row: ${rowStart} / span ${rowSpan};">
      ${segmentHtml}
    </div>
  `;
}

function renderFlowGridHtml(row) {
  const rowCount = flowRowPrintLineCount(row);
  const cells = [...(row.columns || []), ...(row.stacked_columns || [])]
    .map(renderFlowGridColumnHtml)
    .filter(Boolean)
    .join("");
  if (!cells) return "";
  return `<div class="field-list-meeting-preview-flow-grid" style="grid-template-rows: repeat(${rowCount}, auto);">${cells}</div>`;
}

function renderFlowBandHtml(columns, bandClass = "") {
  const cells = (columns || []).map((column) => {
    const colStart = Math.max(1, Math.min(28, Number(column.col_start || 1)));
    const colSpan = Math.max(1, Math.min(28, Number(column.col_span || 1)));
    return `
      <div class="field-list-meeting-preview-cell field-list-meeting-preview-flow-column" style="grid-column: ${colStart} / span ${colSpan};">
        ${flowColumnSegments(column).map((segment, index) => {
          const alignClass = flowSegmentAlignClass(segment.align);
          const stackedClass = index > 0 ? " is-stacked-segment" : "";
          return `<div class="field-list-meeting-preview-flow-text ${alignClass}${stackedClass}">${sanitizeAddressBookPdfMeetingHtml(segment.html)}</div>`;
        }).join("")}
      </div>
    `;
  }).join("");
  if (!cells) return "";
  return `<div class="field-list-meeting-preview-flow-band ${bandClass}">${cells}</div>`;
}

function renderAddressBookPdfMeetingPreviewRow(row) {
  const rowFontSizePt = meetingPreviewRowFontSizePt(row);
  const rowStyle = [
    rowFontSizePt ? `--meeting-row-font-size: ${rowFontSizePt.toFixed(2)}pt` : "",
    `--meeting-grid-column-gap: ${meetingPreviewGridGapPx(row).toFixed(2)}px`,
  ].filter(Boolean).join("; ");
  const styleAttr = rowStyle ? ` style="${rowStyle}"` : "";
  const rowClass = row.is_underlined_row ? " is-underlined-row" : "";
  if (row.kind === "blank") return `<div class="field-list-meeting-preview-blank${rowClass}"${styleAttr}></div>`;
  if (row.kind === "divider") return `<div class="field-list-meeting-preview-divider${rowClass}"${styleAttr}></div>`;
  if (row.kind === "flow") {
    return `
      <div class="field-list-meeting-preview-flow${rowClass}"${styleAttr}>
        ${renderFlowGridHtml(row)}
      </div>
    `;
  }
  return `
    <div class="field-list-meeting-preview-row${rowClass}"${styleAttr}>
      ${(row.cells || []).map((cell) => {
        const classes = [
          "field-list-meeting-preview-cell",
          cell.align === "right" ? "align-right" : cell.align === "center" || cell.align === "auto" ? "align-center" : "align-left",
          cell.is_bold ? "is-bold" : "",
          cell.is_italic ? "is-italic" : "",
          cell.is_title ? "is-title" : "",
          cell.is_underlined ? "is-underlined" : "",
        ].filter(Boolean).join(" ");
        const colStart = Math.max(1, Math.min(28, Number(cell.col_start || 1)));
        const colSpan = Math.max(1, Math.min(28, Number(cell.col_span || 28)));
        return `<div class="${classes}" style="grid-column: ${colStart} / span ${colSpan};">${sanitizeAddressBookPdfMeetingHtml(cell.html)}</div>`;
      }).join("")}
    </div>
  `;
}

function renderAddressBookPdfMeetingPreviewRows(rows) {
  const chunks = [];
  let currentGroup = "";
  let groupedRows = [];

  const flushGroup = () => {
    if (!groupedRows.length) return;
    chunks.push(`
      <div class="address-book-pdf-keep-together-block">
        ${groupedRows.map(renderAddressBookPdfMeetingPreviewRow).join("")}
      </div>
    `);
    groupedRows = [];
    currentGroup = "";
  };

  rows.forEach((row) => {
    const group = String(row.keep_together_group || "").trim();
    if (!group) {
      flushGroup();
      chunks.push(renderAddressBookPdfMeetingPreviewRow(row));
      return;
    }
    if (currentGroup && group !== currentGroup) {
      flushGroup();
    }
    currentGroup = group;
    groupedRows.push(row);
  });
  flushGroup();
  return chunks.join("");
}

function renderAddressBookPdfBiblePreview() {
  const biblePreview = document.querySelector("[data-address-book-pdf-bible-preview]");
  if (!biblePreview) return;
  const rows = addressBookPdfMeetingPreviewRows();
  biblePreview.innerHTML = rows.length
    ? `<div class="field-list-bible-cluster align-center address-book-pdf-keep-together-block">${renderAddressBookPdfMeetingPreviewRows(rows)}</div>`
    : "";
}

function updateAddressBookPdfPageLabels() {
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  const meetingTitle = document.querySelector("[data-address-book-pdf-meeting-title]");
  const pageFooter = document.querySelector("[data-address-book-pdf-page-footer]");
  const fieldName = fieldSelect?.value || "All Fields";
  const meetingName = meetingSelect?.value || "All Meetings";
  if (addressBookPdfPreviewShowsWholeField()) {
    const field = getAddressBookPdfOrderedFields().find((item) => item.name === fieldName);
    const firstMeeting = (field?.meetings || []).find((meeting) => {
      const contacts = meeting.contacts || [];
      const bibleRows = findAddressBookPdfMeetingPreviewRows(meeting.name, fieldName);
      return contacts.length || bibleRows.length;
    });
    if (meetingTitle && firstMeeting) {
      meetingTitle.textContent = addressBookPdfMeetingHeading(firstMeeting.name, firstMeeting.home_last_name);
    } else if (meetingTitle) {
      meetingTitle.textContent = "All Meetings";
    }
  } else if (meetingTitle) {
    meetingTitle.textContent = meetingName;
  }
  if (pageFooter) pageFooter.textContent = `${fieldName} - Page 1`;
}

function updateAddressBookPdfCoverCanvas() {
  const page = document.querySelector("[data-address-book-pdf-page]");
  const canvasPreview = document.querySelector("[data-address-book-pdf-cover-canvas-preview]");
  const imageEl = document.querySelector("[data-address-book-pdf-cover-canvas-image]");
  const titleEl = document.querySelector("[data-address-book-pdf-cover-canvas-title]");
  const dateEl = document.querySelector("[data-address-book-pdf-cover-canvas-date]");
  const imageData = document.querySelector("[data-address-book-pdf-cover-image-data]")?.value || "";
  const includeCheckbox = document.querySelector("[data-address-book-pdf-cover-include]");
  syncAddressBookPdfCoverChoiceVisibility();
  const showCover = Boolean(includeCheckbox?.checked) && Boolean(imageData);

  if (!page || !canvasPreview || !imageEl || !titleEl || !dateEl) return;
  page.classList.toggle("has-cover-preview", showCover);
  canvasPreview.hidden = !showCover;
  if (!showCover) {
    imageEl.removeAttribute("src");
    titleEl.hidden = true;
    dateEl.hidden = true;
    syncAddressBookPdfPreviewHeight();
    return;
  }

  const titleEnabled = document.querySelector("[data-address-book-pdf-include-cover-title-value]")?.value === "1";
  const dateEnabled = document.querySelector("[data-address-book-pdf-include-cover-date-value]")?.value === "1";
  const titleText = document.querySelector("[data-address-book-pdf-cover-title-text]")?.value || "";
  const dateText = document.querySelector("[data-address-book-pdf-cover-date-text]")?.value || "";
  const titleEffect = document.querySelector("[data-address-book-pdf-cover-title-effect]")?.value || "shadow";

  imageEl.src = imageData;
  titleEl.textContent = titleText;
  titleEl.hidden = !titleEnabled || !titleText.trim();
  titleEl.style.left = `${(parseFloat(document.querySelector("[data-address-book-pdf-cover-title-x]")?.value || "0.5") || 0.5) * 100}%`;
  titleEl.style.top = `${(parseFloat(document.querySelector("[data-address-book-pdf-cover-title-y]")?.value || "0.24") || 0.24) * 100}%`;
  titleEl.style.fontSize = `${Math.max(8, Math.min(72, parseFloat(document.querySelector("[data-address-book-pdf-cover-title-font-size]")?.value || "30") || 30))}px`;
  titleEl.style.color = document.querySelector("[data-address-book-pdf-cover-title-color]")?.value || "#ffffff";
  titleEl.dataset.effect = titleEffect;

  dateEl.textContent = dateText;
  dateEl.hidden = !dateEnabled || !dateText.trim();
  dateEl.style.left = `${(parseFloat(document.querySelector("[data-address-book-pdf-cover-date-x]")?.value || "0.5") || 0.5) * 100}%`;
  dateEl.style.top = `${(parseFloat(document.querySelector("[data-address-book-pdf-cover-date-y]")?.value || "0.82") || 0.82) * 100}%`;
  dateEl.style.fontSize = `${Math.max(8, Math.min(72, parseFloat(document.querySelector("[data-address-book-pdf-cover-date-font-size]")?.value || "18") || 18))}px`;
  dateEl.style.color = document.querySelector("[data-address-book-pdf-cover-date-color]")?.value || "#ffffff";
  syncAddressBookPdfPreviewHeight();
}

function renderAddressBookPdfPhoneLineHtml(phone, index) {
  const useFullPhoneLabel = index > 0 && phone.custom_label;
  const phoneCode = useFullPhoneLabel ? phone.custom_label : (phone.code || "");
  return `
    <div class="address-book-pdf-phone-line ${index === 0 ? "is-primary-phone" : "is-secondary-phone"}">
      <span class="address-book-pdf-phone-value">${phoneNumberHtmlForAddressBookPdf(phone.value || "")}</span>
      <span class="address-book-pdf-phone-code ${useFullPhoneLabel ? "is-full-phone-label" : ""}">${escapeAddressBookPdfHtml(phoneCode)}</span>
    </div>
  `;
}

function renderAddressBookPdfPhoneSpacerHtml() {
  return `
    <div class="address-book-pdf-phone-line address-book-pdf-phone-spacer" aria-hidden="true">
      <span class="address-book-pdf-phone-value">000-000-0000</span>
      <span class="address-book-pdf-phone-code">MM</span>
    </div>
  `;
}

function renderAddressBookPdfAddressLineHtml(address) {
  const lineHtml = escapeAddressBookPdfHtml(address.line);
  const mapUrl = addressBookPdfMapUrl(address.coordinates, address.addressText || address.line);
  return `
    <div class="address-book-pdf-address-line ${address.hasCoordinates ? "has-gps" : "no-gps"}">
      ${mapUrl ? `<a href="${escapeAddressBookPdfHtml(mapUrl)}" target="_blank" rel="noopener">${lineHtml}</a>` : lineHtml}
    </div>
  `;
}

function renderAddressBookPdfContactPreviewRows(contact) {
  const phones = (contact.phones || []).filter((item) => item.value);
  const { fontSizePx, printableWidthPx, fontFamily } = addressBookPdfPreviewLayoutMetrics();
  const maxNameWidthPx = addressBookPdfContactNameMaxWidthPx(printableWidthPx, fontSizePx, fontFamily);
  const addressMaxWidthPx = maxNameWidthPx;
  const addressSource = (Array.isArray(contact.address_entries) && contact.address_entries.length)
    ? contact.address_entries
    : (contact.addresses || []);
  const addressLines = addressSource
    .map(addressBookPdfAddressEntry)
    .filter((address) => address.text || address.street_address || address.extended_address)
    .flatMap((address) => (
      addressBookPdfAddressDisplayLines(address, addressMaxWidthPx, fontSizePx, fontFamily).map((line) => ({
        line,
        coordinates: address.coordinates,
        hasCoordinates: address.hasCoordinates,
        addressText: address.text,
      }))
    ));
  const nameLines = wrapAddressBookPdfContactNameLines(contact, maxNameWidthPx, fontSizePx, fontFamily);
  const pbValues = (contact.print_book_name || []).map((item) => String(item || "").trim()).filter(Boolean);
  const paValues = (contact.print_after_address || []).map((item) => String(item || "").trim()).filter(Boolean);
  const pbText = pbValues.map((value) => `(${printBookNotePlainText(value)})`).join(" ");
  const pbHtml = pbValues.map((value) => `(${sanitizeAddressBookPdfNoteHtml(value)})`).join(" ");
  let pbInline = false;
  let pbOwnLine = "";
  let pbOwnLineHtml = "";
  if (pbText) {
    const firstLine = nameLines[0] || "";
    const fitsOnNameLine = nameLines.length > 0
      && measureAddressBookPdfTextWidth(`${firstLine} ${pbText}`, fontSizePx, fontFamily) <= maxNameWidthPx;
    if (fitsOnNameLine) pbInline = true;
    else {
      pbOwnLine = pbText;
      pbOwnLineHtml = pbHtml;
    }
  }
  const pbInlineHtml = pbInline
    ? `<span class="address-book-pdf-print-book">&nbsp;${pbHtml}</span>`
    : "";
  const singleNameLine = nameLines.length === 1;
  const rows = nameLines.map((line, index) => ({
    phone: phones[index] || null,
    phoneIndex: index,
    mainHtml: index === 0
      ? `<div class="address-book-pdf-contact-name${singleNameLine ? " is-single-line" : ""}">${contactNameHtmlForAddressBookPdf(line, contact)}${pbInlineHtml}</div>`
      : `<div class="address-book-pdf-contact-name is-continuation" style="padding-left: ${index}ch;">${contactNameSuffixHtmlForAddressBookPdf(line, contact)}</div>`,
  }));
  const belowLines = [];
  if (pbOwnLine) belowLines.push({ kind: "pb", text: pbOwnLine, html: pbOwnLineHtml });
  addressLines.forEach((address) => belowLines.push({ kind: "addr", address }));
  paValues.forEach((value) => belowLines.push({ kind: "pa", text: printBookNotePlainText(value), html: sanitizeAddressBookPdfNoteHtml(value) }));
  const phonesUsed = Math.min(phones.length, nameLines.length);
  const continuationCount = Math.max(phones.length - phonesUsed, belowLines.length);
  for (let index = 0; index < continuationCount; index += 1) {
    const below = belowLines[index];
    let mainHtml = "";
    if (below) {
      mainHtml = (below.kind === "pb" || below.kind === "pa")
        ? `<div class="address-book-pdf-address-line address-book-pdf-print-book">${below.html || escapeAddressBookPdfHtml(below.text)}</div>`
        : renderAddressBookPdfAddressLineHtml(below.address);
    }
    rows.push({
      phone: phones[phonesUsed + index] || null,
      phoneIndex: phonesUsed + index,
      mainHtml,
    });
  }
  return rows.map((row) => `
    <div class="address-book-pdf-contact-line">
      ${row.phone ? renderAddressBookPdfPhoneLineHtml(row.phone, row.phoneIndex) : renderAddressBookPdfPhoneSpacerHtml()}
      ${row.mainHtml}
    </div>
  `).join("");
}

function renderAddressBookPdfWholeFieldPreview(list) {
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const fieldName = fieldSelect?.value || "";
  const field = getAddressBookPdfOrderedFields().find((item) => item.name === fieldName);
  const meetings = field?.meetings || [];
  const biblePreview = document.querySelector("[data-address-book-pdf-bible-preview]");
  if (biblePreview) biblePreview.innerHTML = "";

  if (!meetings.length) {
    list.innerHTML = '<div class="address-book-pdf-empty-preview">No meetings found for this field.</div>';
    return;
  }

  let visibleMeetingIndex = 0;
  list.innerHTML = meetings.map((meeting) => {
    const contacts = meeting.contacts || [];
    const meetingHeading = addressBookPdfMeetingHeading(meeting.name, meeting.home_last_name);
    const bibleRows = findAddressBookPdfMeetingPreviewRows(meeting.name, fieldName);
    if (!contacts.length && !bibleRows.length) return "";
    const sectionIndex = visibleMeetingIndex;
    visibleMeetingIndex += 1;
    const contactsHtml = contacts.map((contact) => `
      <div class="address-book-pdf-contact-block">
        ${renderAddressBookPdfContactPreviewRows(contact)}
      </div>
    `).join("");
    const bibleHtml = bibleRows.length
      ? `<div class="field-list-bible-cluster align-center address-book-pdf-keep-together-block address-book-pdf-meeting-bible">${renderAddressBookPdfMeetingPreviewRows(bibleRows)}</div>`
      : "";
    const headingHtml = sectionIndex === 0
      ? ""
      : `
        <div class="address-book-pdf-meeting-heading">${escapeAddressBookPdfHtml(meetingHeading)}</div>
        <div class="address-book-pdf-sample-rule"></div>
      `;
    return `
      <section class="address-book-pdf-meeting-preview-section">
        ${headingHtml}
        ${contactsHtml}
        ${bibleHtml}
      </section>
    `;
  }).filter(Boolean).join("");

  if (!list.innerHTML.trim()) {
    list.innerHTML = '<div class="address-book-pdf-empty-preview">No contacts found for this selection.</div>';
  }
}

function renderAddressBookPdfContactPreview() {
  const list = document.querySelector("[data-address-book-pdf-contact-list]");
  if (!list) return;
  updateAddressBookPdfPageLabels();
  if (addressBookPdfPreviewShowsWholeField()) {
    renderAddressBookPdfWholeFieldPreview(list);
    syncAddressBookPdfPreviewHeight();
    return;
  }
  const contacts = getAddressBookPdfPreviewContacts();
  if (!contacts.length) {
    list.innerHTML = '<div class="address-book-pdf-empty-preview">No contacts found for this selection.</div>';
    syncAddressBookPdfPreviewHeight();
    return;
  }
  list.innerHTML = contacts.map((contact) => `
    <div class="address-book-pdf-contact-block">
      ${renderAddressBookPdfContactPreviewRows(contact)}
    </div>
  `).join("");
  renderAddressBookPdfBiblePreview();
  syncAddressBookPdfPreviewHeight();
}

function getAddressBookPdfPreviewScaleMetrics(form) {
  const trimWidth = Math.max(0.1, addressBookPdfNumber(form, "trim_width_in", 3.5));
  const trimHeight = Math.max(0.1, addressBookPdfNumber(form, "trim_height_in", 5));
  const marginLeft = Math.max(0, addressBookPdfNumber(form, "margin_left_in", 0.14));
  const marginRight = Math.max(0, addressBookPdfNumber(form, "margin_right_in", 0.14));
  const marginTop = Math.max(0, addressBookPdfNumber(form, "margin_top_in", 0.14));
  const marginBottom = Math.max(0, addressBookPdfNumber(form, "margin_bottom_in", 0.14));
  const printableWidth = Math.max(0.1, trimWidth - marginLeft - marginRight);
  const printableHeight = Math.max(0.1, trimHeight - marginTop - marginBottom);
  const fitScale = Math.min(430 / trimWidth, 650 / trimHeight, 150);
  return {
    trimWidth,
    trimHeight,
    marginLeft,
    marginRight,
    marginTop,
    marginBottom,
    printableWidth,
    printableHeight,
    fitScale,
  };
}

function syncAddressBookPdfPreviewHeight() {
  const form = document.querySelector("[data-address-book-pdf-form]");
  const page = document.querySelector("[data-address-book-pdf-page]");
  const printable = document.querySelector("[data-address-book-pdf-printable]");
  if (!form || !page || !printable) return;

  const { trimHeight, marginTop, marginBottom, printableHeight, fitScale } = getAddressBookPdfPreviewScaleMetrics(form);
  if (page.classList.contains("has-cover-preview")) {
    page.style.height = `${trimHeight * fitScale}px`;
    return;
  }

  const printableMinHeightPx = printableHeight * fitScale;
  printable.style.minHeight = `${printableMinHeightPx}px`;
  const contentHeightPx = Math.ceil(printable.scrollHeight);
  const pageHeightPx = Math.max(
    trimHeight * fitScale,
    (marginTop * fitScale) + contentHeightPx + (marginBottom * fitScale)
  );
  page.style.height = `${Math.ceil(pageHeightPx)}px`;
}

function updateAddressBookPdfPreview() {
  const form = document.querySelector("[data-address-book-pdf-form]");
  if (!form) return;

  const {
    trimWidth,
    trimHeight,
    marginLeft,
    marginRight,
    marginTop,
    marginBottom,
    printableWidth,
    printableHeight,
    fitScale,
  } = getAddressBookPdfPreviewScaleMetrics(form);
  const fontFamily = addressBookPdfText(form, "font_family", "Arial");
  const fontSize = addressBookPdfPreviewFontSize(form);
  const lineHeight = addressBookPdfPreviewLineHeight(form);
  const columnCount = Math.max(1, parseInt(form.querySelector('[name="meeting_table_column_count"]')?.value || "28", 10) || 28);
  const columnGap = Math.max(0, parseInt(form.querySelector('[name="meeting_table_column_gap_px"]')?.value || "6", 10) || 6);
  const previewScale = Math.max(40, addressBookPdfNumber(form, "screen_preview_scale", 220));
  const bookTitle = addressBookPdfText(form, "book_title", "Address Book");
  const addressAlign = addressBookPdfChoice(form, "address_align", "right") === "left" ? "left" : "right";
  const addressItalic = Boolean(form.querySelector('[name="address_italic"]')?.checked);

  const columnWidth = printableWidth / columnCount;

  const page = document.querySelector("[data-address-book-pdf-page]");
  const printable = document.querySelector("[data-address-book-pdf-printable]");
  const bookSize = document.querySelector("[data-address-book-pdf-book-size]");
  const printableSize = document.querySelector("[data-address-book-pdf-printable-size]");

  if (page) {
    page.style.width = `${trimWidth * fitScale}px`;
    page.style.minHeight = `${trimHeight * fitScale}px`;
    page.style.height = `${trimHeight * fitScale}px`;
  }
  if (printable) {
    printable.style.left = `${marginLeft * fitScale}px`;
    printable.style.right = "auto";
    printable.style.top = `${marginTop * fitScale}px`;
    printable.style.bottom = "auto";
    printable.style.width = `${printableWidth * fitScale}px`;
    printable.style.height = "auto";
    printable.style.minHeight = `${printableHeight * fitScale}px`;
    printable.style.fontFamily = fontFamily;
    printable.style.fontSize = `${fontSize * (fitScale / 72)}px`;
    printable.style.lineHeight = String(lineHeight);
    printable.dataset.addressAlign = addressAlign;
    printable.classList.toggle("address-book-pdf-address-italic", addressItalic);
  }
  const previewWidth = printableWidth * previewScale;
  if (bookSize) {
    bookSize.textContent = `${trimWidth.toFixed(2)}" x ${trimHeight.toFixed(2)}"`;
  }
  if (printableSize) {
    printableSize.textContent = `${printableWidth.toFixed(2)}" x ${printableHeight.toFixed(2)}" printable`;
  }

  const setText = (selector, value) => {
    const element = document.querySelector(selector);
    if (element) element.textContent = value;
  };
  setText('[data-metric="printable-width"]', `${printableWidth.toFixed(2)} in`);
  setText('[data-metric="printable-height"]', `${printableHeight.toFixed(2)} in`);
  setText('[data-metric="column-width"]', `${columnWidth.toFixed(3)} in`);
  setText('[data-metric="preview-width"]', `${previewWidth.toFixed(0)} px`);
  setText('[data-metric="font-family"]', fontFamily);

  form.style.setProperty("--address-book-pdf-column-count", String(columnCount));
  form.style.setProperty("--address-book-pdf-column-gap", `${columnGap}px`);
  updateAddressBookPdfPageLabels();
  updateAddressBookPdfCoverCanvas();
  renderAddressBookPdfContactPreview();
}

function installAddressBookPdfTemporaryNotice() {
  const notice = document.querySelector("[data-address-book-pdf-temporary-notice], #contacts-temporary-notice[data-auto-hide-ms]");
  if (!notice) return;
  const delay = Number.parseInt(notice.dataset.autoHideMs || "3600", 10) || 3600;
  window.setTimeout(() => {
    notice.style.transition = "opacity 220ms ease";
    notice.style.opacity = "0";
    window.setTimeout(() => notice.remove(), 240);
  }, delay);
}

function decimalPlaces(value) {
  const text = String(value || "");
  if (!text.includes(".")) return 0;
  return text.split(".")[1].length;
}

function normalizeSteppedValue(value, step) {
  const places = Math.max(decimalPlaces(step), 0);
  return places ? value.toFixed(places) : String(Math.round(value));
}

function stepAddressBookPdfNumber(input, direction) {
  if (!input) return;
  const step = Number.parseFloat(input.getAttribute("step") || "1") || 1;
  const min = Number.parseFloat(input.getAttribute("min") || "");
  const max = Number.parseFloat(input.getAttribute("max") || "");
  const fallback = Number.parseFloat(input.getAttribute("value") || "0") || 0;
  let nextValue = (Number.parseFloat(input.value || "") || fallback) + (step * direction);
  if (Number.isFinite(min)) nextValue = Math.max(min, nextValue);
  if (Number.isFinite(max)) nextValue = Math.min(max, nextValue);
  input.value = normalizeSteppedValue(nextValue, step);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function bindRepeatingAddressBookPdfStep(button, stepFn) {
  let delayTimer = null;
  let repeatTimer = null;

  const stopRepeating = () => {
    if (delayTimer) {
      window.clearTimeout(delayTimer);
      delayTimer = null;
    }
    if (repeatTimer) {
      window.clearInterval(repeatTimer);
      repeatTimer = null;
    }
    if (button.hasPointerCapture) {
      try {
        button.releasePointerCapture(Number(button.dataset.activePointerId || -1));
      } catch {
        // Pointer capture may already be released by the browser.
      }
    }
    delete button.dataset.activePointerId;
  };

  const startRepeating = (event) => {
    event.preventDefault();
    stopRepeating();
    if (event.pointerId !== undefined && button.setPointerCapture) {
      button.dataset.activePointerId = String(event.pointerId);
      button.setPointerCapture(event.pointerId);
    }
    stepFn();
    delayTimer = window.setTimeout(() => {
      repeatTimer = window.setInterval(stepFn, 90);
    }, 320);
  };

  button.addEventListener("pointerdown", startRepeating);
  button.addEventListener("pointerup", stopRepeating);
  button.addEventListener("pointercancel", stopRepeating);
  button.addEventListener("lostpointercapture", stopRepeating);
  button.addEventListener("blur", stopRepeating);
}

function wrapAddressBookPdfStepperControl(control, increaseLabel, decreaseLabel, stepFn) {
  if (control.closest(".address-book-pdf-number-control")) return;
  const wrapper = document.createElement("span");
  wrapper.className = "address-book-pdf-number-control";
  control.parentNode.insertBefore(wrapper, control);
  wrapper.appendChild(control);

  const buttons = document.createElement("span");
  buttons.className = "address-book-pdf-number-buttons";

  const upButton = document.createElement("button");
  upButton.type = "button";
  upButton.className = "address-book-pdf-number-step";
  upButton.setAttribute("aria-label", increaseLabel);
  upButton.textContent = "▲";

  const downButton = document.createElement("button");
  downButton.type = "button";
  downButton.className = "address-book-pdf-number-step";
  downButton.setAttribute("aria-label", decreaseLabel);
  downButton.textContent = "▼";

  bindRepeatingAddressBookPdfStep(upButton, () => stepFn(1));
  bindRepeatingAddressBookPdfStep(downButton, () => stepFn(-1));
  buttons.append(upButton, downButton);
  wrapper.appendChild(buttons);
}

function enhanceAddressBookPdfNumberInputs(form) {
  form.querySelectorAll('input[type="number"]').forEach((input) => {
    wrapAddressBookPdfStepperControl(
      input,
      `Increase ${input.id || input.name || "value"}`,
      `Decrease ${input.id || input.name || "value"}`,
      (direction) => stepAddressBookPdfNumber(input, direction),
    );
  });
}

function installAddressBookPdfMapProviderControls(form) {
  form.querySelectorAll('[name="address_map_provider"]').forEach((input) => {
    input.addEventListener("change", renderAddressBookPdfContactPreview);
  });
}

function installAddressBookPdfPrintOrderModal(form) {
  const modal = document.querySelector("[data-address-book-pdf-order-modal]");
  const openButton = document.querySelector("[data-address-book-pdf-order-open]");
  const applyButton = document.querySelector("[data-address-book-pdf-order-apply]");
  const cancelButtons = Array.from(document.querySelectorAll("[data-address-book-pdf-order-cancel]"));
  const fieldsList = document.querySelector("[data-address-book-pdf-order-fields]");
  const meetingsList = document.querySelector("[data-address-book-pdf-order-meetings]");
  const meetingTitle = document.querySelector("[data-address-book-pdf-order-meeting-title]");
  if (!modal || !openButton || !applyButton || !fieldsList || !meetingsList || !meetingTitle) return;

  let selectedFieldName = "";
  let orderState = [];

  const syncOpenButtonVisibility = () => {
    const customMode = form.querySelector('[name="print_order_mode"][value="custom"]');
    openButton.hidden = !customMode?.checked;
  };

  const orderedFields = () => getAddressBookPdfOrderedFields()
    .map((field) => ({
      name: field.name,
      order: field.order,
      meetings: (field.meetings || []).map((meeting) => ({
        name: meeting.name,
        order: meeting.order,
      })),
    }));

  const dragHandle = '<span class="address-book-pdf-drag-handle" aria-hidden="true">☰</span>';
  const selectedField = () => orderState.find((field) => field.name === selectedFieldName) || orderState[0] || null;

  const renderFields = () => {
    const scrollTop = fieldsList.scrollTop;
    fieldsList.innerHTML = orderState.map((field) => `
      <button type="button" class="address-book-pdf-sortable-item ${field.name === selectedFieldName ? "is-selected" : ""}" draggable="true" data-order-field-name="${escapeAddressBookPdfHtml(field.name)}">
        ${dragHandle}<span>${escapeAddressBookPdfHtml(field.name || "Field")}</span>
      </button>
    `).join("");
    fieldsList.scrollTop = scrollTop;
  };

  const renderMeetings = () => {
    const field = selectedField();
    selectedFieldName = field?.name || "";
    meetingTitle.textContent = selectedFieldName ? `Meetings in ${selectedFieldName}` : "Meetings";
    const scrollTop = meetingsList.scrollTop;
    meetingsList.innerHTML = (field?.meetings || []).map((meeting) => `
      <button type="button" class="address-book-pdf-sortable-item" draggable="true" data-order-meeting-name="${escapeAddressBookPdfHtml(meeting.name)}">
        ${dragHandle}<span>${escapeAddressBookPdfHtml(meeting.name || "Meeting")}</span>
      </button>
    `).join("");
    meetingsList.scrollTop = scrollTop;
  };

  const render = () => {
    orderState = orderedFields();
    if (!orderState.some((field) => field.name === selectedFieldName)) {
      selectedFieldName = orderState[0]?.name || "";
    }
    renderFields();
    renderMeetings();
  };

  const moveItem = (items, fromIndex, toIndex) => {
    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return false;
    const [item] = items.splice(fromIndex, 1);
    items.splice(toIndex, 0, item);
    return true;
  };

  const bindSortableList = (container, selector, getItems, afterMove) => {
    let draggedKey = "";
    container.addEventListener("dragstart", (event) => {
      const item = event.target.closest(selector);
      if (!item) return;
      draggedKey = item.dataset.orderFieldName || item.dataset.orderMeetingName || "";
      item.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
    });
    container.addEventListener("dragend", () => {
      container.querySelectorAll(".is-dragging").forEach((item) => item.classList.remove("is-dragging"));
      draggedKey = "";
    });
    container.addEventListener("dragover", (event) => {
      event.preventDefault();
      const overItem = event.target.closest(selector);
      if (!overItem || !draggedKey) return;
      const items = getItems();
      const overKey = overItem.dataset.orderFieldName || overItem.dataset.orderMeetingName || "";
      const fromIndex = items.findIndex((item) => item.name === draggedKey);
      const toIndex = items.findIndex((item) => item.name === overKey);
      if (moveItem(items, fromIndex, toIndex)) {
        collect();
        afterMove();
      }
    });
  };

  const collect = () => {
    setAddressBookPdfPrintOrder({
      fields: orderState.map((field, fieldIndex) => ({
        name: field.name,
        order: fieldIndex + 1,
        meetings: (field.meetings || []).map((meeting, meetingIndex) => ({
          name: meeting.name,
          order: meetingIndex + 1,
        })),
      })),
    });
    document.dispatchEvent(new CustomEvent("address-book-pdf-print-order-changed"));
    return persistAddressBookPdfPrintOrder();
  };

  fieldsList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-order-field-name]");
    if (!item) return;
    collect();
    selectedFieldName = item.dataset.orderFieldName || "";
    renderFields();
    renderMeetings();
  });

  bindSortableList(fieldsList, "[data-order-field-name]", () => orderState, () => {
    renderFields();
    renderMeetings();
  });
  bindSortableList(meetingsList, "[data-order-meeting-name]", () => selectedField()?.meetings || [], renderMeetings);

  openButton.addEventListener("click", () => {
    render();
    modal.showModal();
  });
  applyButton.addEventListener("click", async () => {
    await collect();
    modal.close();
  });
  cancelButtons.forEach((button) => button.addEventListener("click", () => modal.close()));
  modal.addEventListener("cancel", () => modal.close());
  form.addEventListener("submit", () => {
    if (modal.open) collect();
  });
  form.querySelectorAll('[name="print_order_mode"]').forEach((input) => {
    input.addEventListener("change", syncOpenButtonVisibility);
  });
  syncOpenButtonVisibility();
}

function installAddressBookPdfPrintFieldsModal(form) {
  const modal = document.querySelector("[data-address-book-pdf-field-print-modal]");
  const openButtons = Array.from(document.querySelectorAll("[data-address-book-pdf-print-button], [data-address-book-pdf-print-paper-button]"));
  const list = document.querySelector("[data-address-book-pdf-field-print-list]");
  const confirmButton = document.querySelector("[data-address-book-pdf-field-print-confirm]");
  const cancelButton = document.querySelector("[data-address-book-pdf-field-print-cancel]");
  const selectedInput = document.querySelector("[data-address-book-pdf-print-field-names]");
  if (!modal || !openButtons.length || !list || !confirmButton || !cancelButton || !selectedInput) return;

  let submittingPrint = false;
  let activeSubmitButton = openButtons[0];

  const syncAllCheckbox = () => {
    const allCheckbox = list.querySelector("[data-print-field-all]");
    const fieldCheckboxes = Array.from(list.querySelectorAll("[data-print-field-name]"));
    if (!allCheckbox || !fieldCheckboxes.length) return;
    const checkedCount = fieldCheckboxes.filter((checkbox) => checkbox.checked).length;
    allCheckbox.checked = checkedCount === fieldCheckboxes.length;
    allCheckbox.indeterminate = checkedCount > 0 && checkedCount < fieldCheckboxes.length;
  };

  const render = () => {
    const fields = getAddressBookPdfOrderedFields();
    list.innerHTML = `
      <label class="address-book-pdf-print-field-row address-book-pdf-print-field-row-all">
        <input type="checkbox" data-print-field-all checked>
        <span>All Fields</span>
      </label>
      ${fields.map((field) => `
        <label class="address-book-pdf-print-field-row">
          <input type="checkbox" value="${escapeAddressBookPdfHtml(String(field.name || ""))}" data-print-field-name checked>
          <span>${escapeAddressBookPdfHtml(String(field.name || "Field"))}</span>
        </label>
      `).join("")}
    `;
    syncAllCheckbox();
  };

  list.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.matches("[data-print-field-all]")) {
      list.querySelectorAll("[data-print-field-name]").forEach((checkbox) => {
        checkbox.checked = target.checked;
      });
      syncAllCheckbox();
      return;
    }
    if (target.matches("[data-print-field-name]")) {
      syncAllCheckbox();
    }
  });

  openButtons.forEach((openButton) => {
    openButton.addEventListener("click", (event) => {
      activeSubmitButton = openButton;
      if (submittingPrint) {
        submittingPrint = false;
        return;
      }
      event.preventDefault();
      render();
      modal.showModal();
    });
  });

  confirmButton.addEventListener("click", () => {
    const selectedFields = Array.from(list.querySelectorAll("[data-print-field-name]:checked"))
      .map((checkbox) => checkbox.value)
      .filter(Boolean);
    selectedInput.value = JSON.stringify(selectedFields);
    const includeInsertValue = document.querySelector("[data-address-book-pdf-include-insert-value]");
    const includeInsertCheckbox = document.querySelector("[data-address-book-pdf-insert-include]");
    const insertData = document.querySelector("[data-address-book-pdf-insert-file-data]");
    if (includeInsertValue && includeInsertCheckbox && insertData) {
      includeInsertValue.value = includeInsertCheckbox.checked && insertData.value ? "1" : "0";
    }
    submittingPrint = true;
    modal.close();
    activeSubmitButton.click();
  });

  cancelButton.addEventListener("click", () => modal.close());
  modal.addEventListener("cancel", () => modal.close());
}

function installAddressBookPdfCoverModal(form) {
  const modal = document.querySelector("[data-address-book-pdf-cover-modal]");
  const openButton = document.querySelector("[data-address-book-pdf-cover-open]");
  const fileInput = document.querySelector("[data-address-book-pdf-cover-file]");
  const zoomInput = document.querySelector("[data-address-book-pdf-cover-zoom]");
  const preview = document.querySelector("[data-address-book-pdf-cover-preview]");
  const applyButton = document.querySelector("[data-address-book-pdf-cover-apply]");
  const clearButton = document.querySelector("[data-address-book-pdf-cover-clear]");
  const cancelButton = document.querySelector("[data-address-book-pdf-cover-cancel]");
  const includeCheckbox = document.querySelector("[data-address-book-pdf-cover-include]");
  const includeValue = document.querySelector("[data-address-book-pdf-include-cover-value]");
  const includeTitleCheckbox = document.querySelector("[data-address-book-pdf-cover-title-include]");
  const includeTitleValue = document.querySelector("[data-address-book-pdf-include-cover-title-value]");
  const includeDateCheckbox = document.querySelector("[data-address-book-pdf-cover-date-include]");
  const dateRow = includeDateCheckbox?.closest(".address-book-pdf-cover-date-row");
  const includeDateValue = document.querySelector("[data-address-book-pdf-include-cover-date-value]");
  const titleInput = document.querySelector("[data-address-book-pdf-cover-title-input]");
  const titleSizeInput = document.querySelector("[data-address-book-pdf-cover-title-size]");
  const titleColorInput = document.querySelector("[data-address-book-pdf-cover-title-color-input]");
  const titleTextInput = document.querySelector("[data-address-book-pdf-cover-title-text]");
  const titleFontSizeInput = document.querySelector("[data-address-book-pdf-cover-title-font-size]");
  const titleColorHiddenInput = document.querySelector("[data-address-book-pdf-cover-title-color]");
  const titleEffectHiddenInput = document.querySelector("[data-address-book-pdf-cover-title-effect]");
  const titleEffectInputs = Array.from(document.querySelectorAll("[data-address-book-pdf-cover-title-effect-choice]"));
  const titleXInput = document.querySelector("[data-address-book-pdf-cover-title-x]");
  const titleYInput = document.querySelector("[data-address-book-pdf-cover-title-y]");
  const titlePreviewWidthInput = document.querySelector("[data-address-book-pdf-cover-title-preview-width]");
  const dateInput = document.querySelector("[data-address-book-pdf-cover-date-input]");
  const dateSizeInput = document.querySelector("[data-address-book-pdf-cover-date-size]");
  const dateColorInput = document.querySelector("[data-address-book-pdf-cover-date-color-input]");
  const dateTextInput = document.querySelector("[data-address-book-pdf-cover-date-text]");
  const dateFontSizeInput = document.querySelector("[data-address-book-pdf-cover-date-font-size]");
  const dateColorHiddenInput = document.querySelector("[data-address-book-pdf-cover-date-color]");
  const dateXInput = document.querySelector("[data-address-book-pdf-cover-date-x]");
  const dateYInput = document.querySelector("[data-address-book-pdf-cover-date-y]");
  const titleControls = document.querySelector(".address-book-pdf-cover-title-controls");
  const dateFields = document.querySelector(".address-book-pdf-cover-date-fields");
  const savedSelect = document.querySelector("[data-address-book-pdf-cover-saved-select]");
  const saveCoverButton = document.querySelector("[data-address-book-pdf-cover-save]");
  const deleteCoverButton = document.querySelector("[data-address-book-pdf-cover-delete]");
  const dataInput = document.querySelector("[data-address-book-pdf-cover-image-data]");
  const widthInput = document.querySelector("[data-address-book-pdf-cover-image-width]");
  const heightInput = document.querySelector("[data-address-book-pdf-cover-image-height]");
  const labelInput = document.querySelector("[data-address-book-pdf-cover-image-label]");
  const summaryEl = document.querySelector("[data-address-book-pdf-cover-summary]");
  if (!modal || !openButton || !fileInput || !zoomInput || !preview || !applyButton || !clearButton || !cancelButton || !includeCheckbox || !includeValue || !includeTitleCheckbox || !includeTitleValue || !includeDateCheckbox || !includeDateValue || !titleInput || !titleSizeInput || !titleColorInput || !titleTextInput || !titleFontSizeInput || !titleColorHiddenInput || !titleEffectHiddenInput || !titleXInput || !titleYInput || !titlePreviewWidthInput || !dateInput || !dateSizeInput || !dateColorInput || !dateTextInput || !dateFontSizeInput || !dateColorHiddenInput || !dateXInput || !dateYInput || !titleControls || !dateFields || !savedSelect || !saveCoverButton || !deleteCoverButton || !dataInput || !widthInput || !heightInput || !labelInput || !summaryEl) return;

  const savedCoverStorageKey = "addressBookPdfSavedCovers";
  const lastCoverStorageKey = ADDRESS_BOOK_PDF_LAST_COVER_KEY;
  let pendingCover = null;
  let sourceCover = null;
  let cropState = { x: 0.5, y: 0.5, zoom: 1 };
  let titlePosition = {
    x: parseFloat(titleXInput.value || "0.5") || 0.5,
    y: parseFloat(titleYInput.value || "0.24") || 0.24,
  };
  let datePosition = {
    x: parseFloat(dateXInput.value || "0.5") || 0.5,
    y: parseFloat(dateYInput.value || "0.82") || 0.82,
  };

  const todayDateText = () => {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return `${month}/${day}/${now.getFullYear()}`;
  };

  const updateCoverSummary = () => {
    if (!includeCheckbox.checked || !dataInput.value) {
      summaryEl.hidden = true;
      summaryEl.textContent = "";
      return;
    }
    const label = labelInput.value || "Selected picture";
    summaryEl.textContent = `File name: ${label}`;
    summaryEl.hidden = false;
  };

  const readLastCover = () => {
    try {
      return JSON.parse(window.localStorage.getItem(lastCoverStorageKey) || "null");
    } catch {
      return null;
    }
  };

  const writeLastCover = (payload) => {
    window.localStorage.setItem(lastCoverStorageKey, JSON.stringify(payload));
  };

  const syncCoverChoiceVisibility = () => {
    const wantsCover = includeCheckbox.checked;
    openButton.hidden = !wantsCover;
    if (dateRow) dateRow.hidden = !wantsCover;
    if (!wantsCover) {
      includeDateCheckbox.checked = false;
      includeDateValue.value = "0";
      summaryEl.hidden = true;
    } else if (dataInput.value) {
      summaryEl.hidden = false;
    }
    updateCoverSummary();
  };

  const syncCoverTextAccess = () => {
    const titleEnabled = includeTitleCheckbox.checked;
    const dateEnabled = includeDateCheckbox.checked;
    titleControls.classList.toggle("is-disabled", !titleEnabled);
    dateFields.classList.toggle("is-disabled", !dateEnabled);
    [titleInput, titleSizeInput, titleColorInput, ...titleEffectInputs].forEach((input) => {
      input.disabled = !titleEnabled;
    });
    [dateInput, dateSizeInput, dateColorInput].forEach((input) => {
      input.disabled = !dateEnabled;
    });
  };

  const readSavedCovers = () => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(savedCoverStorageKey) || "{}");
      return parsed && typeof parsed === "object" && parsed.items && typeof parsed.items === "object"
        ? parsed.items
        : {};
    } catch {
      return {};
    }
  };

  const writeSavedCovers = (items) => {
    window.localStorage.setItem(savedCoverStorageKey, JSON.stringify({ items: items || {} }));
  };

  const renderSavedCovers = (selectedName = "") => {
    const items = readSavedCovers();
    const names = Object.keys(items).sort((left, right) => left.localeCompare(right));
    savedSelect.replaceChildren();
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "Choose saved cover";
    savedSelect.appendChild(emptyOption);
    names.forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      if (name === selectedName) option.selected = true;
      savedSelect.appendChild(option);
    });
    deleteCoverButton.disabled = !savedSelect.value;
  };

  const coverDimensions = () => {
    const widthIn = Math.max(0.1, addressBookPdfNumber(form, "trim_width_in", 3.5));
    const heightIn = Math.max(0.1, addressBookPdfNumber(form, "trim_height_in", 5.5));
    const scale = Math.min(360, Math.max(180, 300));
    return {
      width: Math.max(1, Math.round(widthIn * scale)),
      height: Math.max(1, Math.round(heightIn * scale)),
    };
  };

  const clampTitlePosition = () => {
    const titleEl = preview.querySelector("[data-address-book-pdf-cover-title-preview]");
    const stage = preview.querySelector(".address-book-pdf-cover-preview-stage");
    if (titleEl && stage) {
      const stageWidth = stage.clientWidth || 1;
      const stageHeight = stage.clientHeight || 1;
      const titleWidthRatio = Math.min(0.98, (titleEl.offsetWidth || 0) / stageWidth);
      const titleHeightRatio = Math.min(0.98, (titleEl.offsetHeight || 0) / stageHeight);
      const minX = titleWidthRatio / 2;
      const maxX = 1 - (titleWidthRatio / 2);
      const minY = titleHeightRatio / 2;
      const maxY = 1 - (titleHeightRatio / 2);
      titlePosition = {
        x: Math.min(maxX, Math.max(minX, titlePosition.x)),
        y: Math.min(maxY, Math.max(minY, titlePosition.y)),
      };
      return;
    }
    titlePosition = {
      x: Math.min(0.95, Math.max(0.05, titlePosition.x)),
      y: Math.min(0.95, Math.max(0.05, titlePosition.y)),
    };
  };

  const clampDatePosition = () => {
    const dateEl = preview.querySelector("[data-address-book-pdf-cover-date-preview]");
    const stage = preview.querySelector(".address-book-pdf-cover-preview-stage");
    if (dateEl && stage) {
      const stageWidth = stage.clientWidth || 1;
      const stageHeight = stage.clientHeight || 1;
      const dateWidthRatio = Math.min(0.98, (dateEl.offsetWidth || 0) / stageWidth);
      const dateHeightRatio = Math.min(0.98, (dateEl.offsetHeight || 0) / stageHeight);
      const minX = dateWidthRatio / 2;
      const maxX = 1 - (dateWidthRatio / 2);
      const minY = dateHeightRatio / 2;
      const maxY = 1 - (dateHeightRatio / 2);
      datePosition = {
        x: Math.min(maxX, Math.max(minX, datePosition.x)),
        y: Math.min(maxY, Math.max(minY, datePosition.y)),
      };
      return;
    }
    datePosition = {
      x: Math.min(0.95, Math.max(0.05, datePosition.x)),
      y: Math.min(0.95, Math.max(0.05, datePosition.y)),
    };
  };

  const updateTitlePreview = () => {
    const titleEl = preview.querySelector("[data-address-book-pdf-cover-title-preview]");
    if (!titleEl) return;
    const selectedEffect = titleEffectInputs.find((input) => input.checked)?.value || "shadow";
    clampTitlePosition();
    titleEl.textContent = titleInput.value || "";
    titleEl.style.left = `${titlePosition.x * 100}%`;
    titleEl.style.top = `${titlePosition.y * 100}%`;
    titleEl.style.fontSize = `${Math.max(8, Math.min(72, parseFloat(titleSizeInput.value || "30") || 30))}px`;
    titleEl.style.color = titleColorInput.value || "#ffffff";
    titleEl.dataset.effect = selectedEffect;
    titleEl.hidden = !includeTitleCheckbox.checked || !titleEl.textContent.trim();
    clampTitlePosition();
    titleEl.style.left = `${titlePosition.x * 100}%`;
    titleEl.style.top = `${titlePosition.y * 100}%`;
  };

  const updateDatePreview = () => {
    const dateEl = preview.querySelector("[data-address-book-pdf-cover-date-preview]");
    if (!dateEl) return;
    clampDatePosition();
    dateEl.textContent = dateInput.value || "";
    dateEl.style.left = `${datePosition.x * 100}%`;
    dateEl.style.top = `${datePosition.y * 100}%`;
    dateEl.style.fontSize = `${Math.max(8, Math.min(72, parseFloat(dateSizeInput.value || "18") || 18))}px`;
    dateEl.style.color = dateColorInput.value || "#ffffff";
    dateEl.hidden = !includeDateCheckbox.checked || !dateEl.textContent.trim();
    clampDatePosition();
    dateEl.style.left = `${datePosition.x * 100}%`;
    dateEl.style.top = `${datePosition.y * 100}%`;
  };

  const clampCropState = () => {
    cropState.zoom = Math.min(3, Math.max(1, parseFloat(cropState.zoom || 1) || 1));
    cropState.x = Math.min(1, Math.max(0, parseFloat(cropState.x || 0.5) || 0.5));
    cropState.y = Math.min(1, Math.max(0, parseFloat(cropState.y || 0.5) || 0.5));
  };

  const updateCropPreview = () => {
    const imageEl = preview.querySelector("[data-address-book-pdf-cover-crop-image]");
    const stage = preview.querySelector(".address-book-pdf-cover-preview-stage");
    if (!imageEl || !stage || !sourceCover?.image) return;
    clampCropState();
    const stageWidth = stage.clientWidth || 1;
    const stageHeight = stage.clientHeight || 1;
    const sourceRatio = sourceCover.image.naturalWidth / sourceCover.image.naturalHeight;
    const stageRatio = stageWidth / stageHeight;
    let baseWidth = stageWidth;
    let baseHeight = stageHeight;
    if (sourceRatio > stageRatio) {
      baseHeight = stageHeight;
      baseWidth = stageHeight * sourceRatio;
    } else {
      baseWidth = stageWidth;
      baseHeight = stageWidth / sourceRatio;
    }
    const drawWidth = baseWidth * cropState.zoom;
    const drawHeight = baseHeight * cropState.zoom;
    const minX = Math.min(0, stageWidth - drawWidth);
    const minY = Math.min(0, stageHeight - drawHeight);
    const x = minX * cropState.x;
    const y = minY * cropState.y;
    imageEl.style.width = `${drawWidth}px`;
    imageEl.style.height = `${drawHeight}px`;
    imageEl.style.transform = `translate(${x}px, ${y}px)`;
    zoomInput.value = String(cropState.zoom);
  };

  const drawPreview = (src) => {
    preview.innerHTML = src
      ? `<div class="address-book-pdf-cover-preview-stage">
          <img src="${src}" alt="Selected cover picture" data-address-book-pdf-cover-crop-image>
          <div class="address-book-pdf-cover-title-preview" data-address-book-pdf-cover-title-preview></div>
          <div class="address-book-pdf-cover-date-preview" data-address-book-pdf-cover-date-preview></div>
        </div>`
      : '<div class="address-book-pdf-cover-empty">No cover picture selected.</div>';
    updateCropPreview();
    updateTitlePreview();
    updateDatePreview();
  };

  const syncTitleHiddenInputs = () => {
    titleTextInput.value = titleInput.value || "";
    titleFontSizeInput.value = titleSizeInput.value || "30";
    titleColorHiddenInput.value = titleColorInput.value || "#ffffff";
    titleEffectHiddenInput.value = titleEffectInputs.find((input) => input.checked)?.value || "shadow";
    titleXInput.value = String(titlePosition.x);
    titleYInput.value = String(titlePosition.y);
    titlePreviewWidthInput.value = String(preview.querySelector(".address-book-pdf-cover-preview-stage")?.clientWidth || preview.clientWidth || "");
    includeTitleValue.value = includeTitleCheckbox.checked ? "1" : "0";
    updateTitlePreview();
    updateAddressBookPdfCoverCanvas();
  };

  const syncDateHiddenInputs = () => {
    dateTextInput.value = dateInput.value || "";
    dateFontSizeInput.value = dateSizeInput.value || "18";
    dateColorHiddenInput.value = dateColorInput.value || "#ffffff";
    dateXInput.value = String(datePosition.x);
    dateYInput.value = String(datePosition.y);
    includeDateValue.value = includeDateCheckbox.checked ? "1" : "0";
    updateDatePreview();
    updateAddressBookPdfCoverCanvas();
  };

  const fileToSourceCover = (file) => new Promise((resolve, reject) => {
    if (!file) {
      resolve(null);
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read cover picture."));
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => reject(new Error("Could not load cover picture."));
      image.onload = () => {
        resolve({
          src: String(reader.result || ""),
          image,
          label: file.name || "Picture",
        });
      };
      image.src = String(reader.result || "");
    };
    reader.readAsDataURL(file);
  });

  const sourceToCover = () => {
    if (!sourceCover?.image) return pendingCover;
    const { width, height } = coverDimensions();
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    const sourceRatio = sourceCover.image.naturalWidth / sourceCover.image.naturalHeight;
    const targetRatio = width / height;
    let sw = sourceCover.image.naturalWidth;
    let sh = sourceCover.image.naturalHeight;
    if (sourceRatio > targetRatio) {
      sh = sourceCover.image.naturalHeight / cropState.zoom;
      sw = sh * targetRatio;
    } else {
      sw = sourceCover.image.naturalWidth / cropState.zoom;
      sh = sw / targetRatio;
    }
    const maxSx = Math.max(0, sourceCover.image.naturalWidth - sw);
    const maxSy = Math.max(0, sourceCover.image.naturalHeight - sh);
    const sx = maxSx * cropState.x;
    const sy = maxSy * cropState.y;
    ctx.drawImage(sourceCover.image, sx, sy, sw, sh, 0, 0, width, height);
    return {
      data: canvas.toDataURL("image/jpeg", 0.9),
      width,
      height,
      label: sourceCover?.label || pendingCover?.label || labelInput.value || "Picture",
    };
  };

  const setStoredCover = (cover) => {
    dataInput.value = cover?.data || "";
    widthInput.value = cover?.width ? String(cover.width) : "";
    heightInput.value = cover?.height ? String(cover.height) : "";
    labelInput.value = cover?.label || "";
    includeCheckbox.checked = Boolean(cover?.data);
    includeValue.value = cover?.data ? "1" : "0";
    syncCoverChoiceVisibility();
    drawPreview(cover?.data || "");
    syncTitleHiddenInputs();
    syncDateHiddenInputs();
    updateAddressBookPdfCoverCanvas();
    updateCoverSummary();
    if (cover?.data) {
      try {
        writeLastCover({
          data: cover.data,
          width: cover.width || "",
          height: cover.height || "",
          label: cover.label || labelInput.value || "Picture",
        });
      } catch {
        // Ignore storage quota errors; the in-page selection still remains.
      }
    } else {
      window.localStorage.removeItem(lastCoverStorageKey);
    }
  };

  const currentSavedCoverPayload = () => {
    const cover = sourceToCover();
    if (!cover?.data) return null;
    cover.label = sourceCover?.label || cover.label || labelInput.value || "Picture";
    return {
      cover,
      title: {
        enabled: includeTitleCheckbox.checked,
        text: titleInput.value || "",
        size: titleSizeInput.value || "30",
        color: titleColorInput.value || "#ffffff",
        effect: titleEffectInputs.find((input) => input.checked)?.value || "shadow",
        x: titlePosition.x,
        y: titlePosition.y,
      },
      date: {
        enabled: includeDateCheckbox.checked,
        text: dateInput.value || todayDateText(),
        size: dateSizeInput.value || "18",
        color: dateColorInput.value || "#ffffff",
        x: datePosition.x,
        y: datePosition.y,
      },
    };
  };

  const applySavedCoverPayload = (payload) => {
    if (!payload?.cover?.data) return;
    const coverLabel = payload.cover.label || savedSelect.value || "Saved cover";
    pendingCover = {
      data: payload.cover.data,
      width: payload.cover.width,
      height: payload.cover.height,
      label: coverLabel,
    };
    dataInput.value = pendingCover.data;
    widthInput.value = pendingCover.width ? String(pendingCover.width) : "";
    heightInput.value = pendingCover.height ? String(pendingCover.height) : "";
    labelInput.value = coverLabel;
    includeCheckbox.checked = true;
    includeValue.value = "1";
    includeTitleCheckbox.checked = Boolean(payload.title?.enabled);
    includeDateCheckbox.checked = Boolean(payload.date?.enabled);
    titleInput.value = payload.title?.text || addressBookPdfText(form, "book_title", "");
    titleSizeInput.value = payload.title?.size || "30";
    titleColorInput.value = payload.title?.color || "#ffffff";
    const effect = payload.title?.effect || "shadow";
    titleEffectInputs.forEach((input) => {
      input.checked = input.value === effect;
    });
    if (!titleEffectInputs.some((input) => input.checked) && titleEffectInputs[0]) {
      titleEffectInputs[0].checked = true;
    }
    dateInput.value = payload.date?.text || todayDateText();
    dateSizeInput.value = payload.date?.size || "18";
    dateColorInput.value = payload.date?.color || "#ffffff";
    titlePosition = {
      x: parseFloat(payload.title?.x || "0.5") || 0.5,
      y: parseFloat(payload.title?.y || "0.24") || 0.24,
    };
    datePosition = {
      x: parseFloat(payload.date?.x || "0.5") || 0.5,
      y: parseFloat(payload.date?.y || "0.82") || 0.82,
    };
    sourceCover = { src: pendingCover.data, image: null, label: coverLabel };
    cropState = { x: 0.5, y: 0.5, zoom: 1 };
    syncCoverChoiceVisibility();
    syncCoverTextAccess();
    syncTitleHiddenInputs();
    syncDateHiddenInputs();
    updateAddressBookPdfCoverCanvas();
    updateCoverSummary();
    try {
      writeLastCover({
        data: pendingCover.data,
        width: pendingCover.width || "",
        height: pendingCover.height || "",
        label: coverLabel,
      });
    } catch {
      // Ignore storage quota errors.
    }
    const image = new Image();
    image.onload = () => {
      sourceCover.image = image;
      drawPreview(sourceCover.src);
      syncTitleHiddenInputs();
      syncDateHiddenInputs();
      syncCoverChoiceVisibility();
      syncCoverTextAccess();
      updateAddressBookPdfCoverCanvas();
      updateCoverSummary();
    };
    image.src = sourceCover.src;
  };

  openButton.addEventListener("click", () => {
    pendingCover = dataInput.value
      ? {
          data: dataInput.value,
          width: widthInput.value,
          height: heightInput.value,
          label: labelInput.value || "Picture",
        }
      : null;
    sourceCover = pendingCover?.data
      ? { src: pendingCover.data, image: null, label: pendingCover.label || "Picture" }
      : null;
    cropState = { x: 0.5, y: 0.5, zoom: 1 };
    includeCheckbox.checked = includeValue.value === "1" && Boolean(dataInput.value);
    includeTitleCheckbox.checked = includeTitleValue.value === "1";
    includeDateCheckbox.checked = includeDateValue.value === "1";
    titleInput.value = titleTextInput.value || addressBookPdfText(form, "book_title", "");
    titleSizeInput.value = titleFontSizeInput.value || "30";
    titleColorInput.value = titleColorHiddenInput.value || "#ffffff";
    dateInput.value = dateTextInput.value || todayDateText();
    dateSizeInput.value = dateFontSizeInput.value || "18";
    dateColorInput.value = dateColorHiddenInput.value || "#ffffff";
    const storedEffect = titleEffectHiddenInput.value || "shadow";
    titleEffectInputs.forEach((input) => {
      input.checked = input.value === storedEffect;
    });
    if (!titleEffectInputs.some((input) => input.checked) && titleEffectInputs[0]) {
      titleEffectInputs[0].checked = true;
    }
    titlePosition = {
      x: parseFloat(titleXInput.value || "0.5") || 0.5,
      y: parseFloat(titleYInput.value || "0.24") || 0.24,
    };
    datePosition = {
      x: parseFloat(dateXInput.value || "0.5") || 0.5,
      y: parseFloat(dateYInput.value || "0.82") || 0.82,
    };
    fileInput.value = "";
    if (sourceCover?.src) {
      const image = new Image();
      image.onload = () => {
        sourceCover.image = image;
        drawPreview(sourceCover.src);
        syncCoverTextAccess();
      };
      image.src = sourceCover.src;
    } else {
      drawPreview("");
    }
    renderSavedCovers(savedSelect.value);
    syncCoverTextAccess();
    modal.showModal();
  });

  fileInput.addEventListener("change", async () => {
    try {
      sourceCover = await fileToSourceCover(fileInput.files?.[0]);
      cropState = { x: 0.5, y: 0.5, zoom: 1 };
      pendingCover = sourceToCover();
      if (pendingCover?.data) includeCheckbox.checked = true;
      drawPreview(sourceCover?.src || "");
    } catch {
      pendingCover = null;
      sourceCover = null;
      drawPreview("");
    }
  });

  applyButton.addEventListener("click", () => {
    const shouldInclude = includeCheckbox.checked;
    pendingCover = sourceToCover();
    if (pendingCover) {
      pendingCover.label = sourceCover?.label || pendingCover.label || labelInput.value || "Picture";
    }
    setStoredCover(pendingCover);
    includeCheckbox.checked = shouldInclude && Boolean(dataInput.value);
    includeValue.value = includeCheckbox.checked ? "1" : "0";
    syncTitleHiddenInputs();
    syncDateHiddenInputs();
    updateCoverSummary();
    modal.close();
  });
  includeCheckbox.addEventListener("input", (event) => {
    event.stopPropagation();
    applyAddressBookPdfCoverIncludeFromCheckbox();
    updateCoverSummary();
  });
  includeCheckbox.addEventListener("change", (event) => {
    event.stopPropagation();
    applyAddressBookPdfCoverIncludeFromCheckbox();
    updateCoverSummary();
  });
  includeTitleCheckbox.addEventListener("change", () => {
    syncTitleHiddenInputs();
    syncCoverTextAccess();
  });
  includeDateCheckbox.addEventListener("change", () => {
    syncDateHiddenInputs();
    syncCoverTextAccess();
  });
  titleInput.addEventListener("input", syncTitleHiddenInputs);
  titleSizeInput.addEventListener("input", syncTitleHiddenInputs);
  titleColorInput.addEventListener("input", syncTitleHiddenInputs);
  titleEffectInputs.forEach((input) => input.addEventListener("change", syncTitleHiddenInputs));
  dateInput.addEventListener("input", syncDateHiddenInputs);
  dateSizeInput.addEventListener("input", syncDateHiddenInputs);
  dateColorInput.addEventListener("input", syncDateHiddenInputs);
  zoomInput.addEventListener("input", () => {
    cropState.zoom = parseFloat(zoomInput.value || "1") || 1;
    pendingCover = sourceToCover();
    updateCropPreview();
  });
  preview.addEventListener("pointerdown", (event) => {
    const cropImage = event.target.closest?.("[data-address-book-pdf-cover-crop-image]");
    if (!cropImage) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const startCrop = { ...cropState };
    const stage = preview.querySelector(".address-book-pdf-cover-preview-stage");
    const moveCrop = (moveEvent) => {
      const rect = stage?.getBoundingClientRect();
      if (!rect?.width || !rect?.height) return;
      const imageEl = preview.querySelector("[data-address-book-pdf-cover-crop-image]");
      const overflowX = Math.max(1, (imageEl?.offsetWidth || rect.width) - rect.width);
      const overflowY = Math.max(1, (imageEl?.offsetHeight || rect.height) - rect.height);
      cropState.x = startCrop.x - ((moveEvent.clientX - startX) / overflowX);
      cropState.y = startCrop.y - ((moveEvent.clientY - startY) / overflowY);
      pendingCover = sourceToCover();
      updateCropPreview();
    };
    const stopCrop = () => {
      window.removeEventListener("pointermove", moveCrop);
      window.removeEventListener("pointerup", stopCrop);
      window.removeEventListener("pointercancel", stopCrop);
    };
    window.addEventListener("pointermove", moveCrop);
    window.addEventListener("pointerup", stopCrop);
    window.addEventListener("pointercancel", stopCrop);
  });
  preview.addEventListener("pointerdown", (event) => {
    const dateEl = event.target.closest?.("[data-address-book-pdf-cover-date-preview]");
    if (!dateEl) return;
    event.preventDefault();
    dateEl.setPointerCapture?.(event.pointerId);
    const moveDate = (moveEvent) => {
      const rect = preview.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      datePosition = {
        x: (moveEvent.clientX - rect.left) / rect.width,
        y: (moveEvent.clientY - rect.top) / rect.height,
      };
      syncDateHiddenInputs();
    };
    const stopMove = () => {
      window.removeEventListener("pointermove", moveDate);
      window.removeEventListener("pointerup", stopMove);
      window.removeEventListener("pointercancel", stopMove);
    };
    window.addEventListener("pointermove", moveDate);
    window.addEventListener("pointerup", stopMove);
    window.addEventListener("pointercancel", stopMove);
  });
  preview.addEventListener("pointerdown", (event) => {
    const titleEl = event.target.closest?.("[data-address-book-pdf-cover-title-preview]");
    if (!titleEl) return;
    event.preventDefault();
    titleEl.setPointerCapture?.(event.pointerId);
    const moveTitle = (moveEvent) => {
      const rect = preview.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      titlePosition = {
        x: (moveEvent.clientX - rect.left) / rect.width,
        y: (moveEvent.clientY - rect.top) / rect.height,
      };
      syncTitleHiddenInputs();
    };
    const stopMove = () => {
      window.removeEventListener("pointermove", moveTitle);
      window.removeEventListener("pointerup", stopMove);
      window.removeEventListener("pointercancel", stopMove);
    };
    window.addEventListener("pointermove", moveTitle);
    window.addEventListener("pointerup", stopMove);
    window.addEventListener("pointercancel", stopMove);
  });
  savedSelect.addEventListener("change", () => {
    const items = readSavedCovers();
    const selected = savedSelect.value || "";
    deleteCoverButton.disabled = !selected;
    if (selected && items[selected]) {
      applySavedCoverPayload(items[selected]);
    }
  });
  saveCoverButton.addEventListener("click", () => {
    const payload = currentSavedCoverPayload();
    if (!payload) {
      window.alert("Choose a cover picture before saving this cover page.");
      return;
    }
    const existingName = savedSelect.value || "";
    const defaultName = existingName || addressBookPdfText(form, "book_title", "Cover Page");
    const name = window.prompt("Save cover page as:", defaultName);
    if (!name) return;
    const cleanName = name.trim();
    if (!cleanName) return;
    const items = readSavedCovers();
    items[cleanName] = payload;
    try {
      writeSavedCovers(items);
      renderSavedCovers(cleanName);
    } catch {
      window.alert("This cover page is too large to save in browser storage.");
    }
  });
  deleteCoverButton.addEventListener("click", () => {
    const selected = savedSelect.value || "";
    if (!selected) return;
    const items = readSavedCovers();
    delete items[selected];
    writeSavedCovers(items);
    renderSavedCovers("");
  });
  if (!dateInput.value) dateInput.value = todayDateText();
  syncDateHiddenInputs();
  renderSavedCovers();
  const lastCover = readLastCover();
  if (lastCover?.data) {
    setStoredCover(lastCover);
  } else {
    syncCoverChoiceVisibility();
    updateCoverSummary();
  }
  syncCoverTextAccess();
  clearButton.addEventListener("click", () => {
    pendingCover = null;
    sourceCover = null;
    fileInput.value = "";
    setStoredCover(null);
    modal.close();
  });
  cancelButton.addEventListener("click", () => modal.close());
  modal.addEventListener("cancel", () => modal.close());
}

function installAddressBookPdfInsertFileModal(form) {
  const modal = document.querySelector("[data-address-book-pdf-insert-modal]");
  const openButton = document.querySelector("[data-address-book-pdf-insert-open]");
  const fileInput = document.querySelector("[data-address-book-pdf-insert-file]");
  const zoomInput = document.querySelector("[data-address-book-pdf-insert-zoom]");
  const preview = document.querySelector("[data-address-book-pdf-insert-preview]");
  const applyButton = document.querySelector("[data-address-book-pdf-insert-apply]");
  const clearButton = document.querySelector("[data-address-book-pdf-insert-clear]");
  const cancelButton = document.querySelector("[data-address-book-pdf-insert-cancel]");
  const includeCheckbox = document.querySelector("[data-address-book-pdf-insert-include]");
  const includeValue = document.querySelector("[data-address-book-pdf-include-insert-value]");
  const summaryEl = document.querySelector("[data-address-book-pdf-insert-summary]");
  const dataInput = document.querySelector("[data-address-book-pdf-insert-file-data]");
  const mimeInput = document.querySelector("[data-address-book-pdf-insert-file-mime]");
  const widthInput = document.querySelector("[data-address-book-pdf-insert-file-width]");
  const heightInput = document.querySelector("[data-address-book-pdf-insert-file-height]");
  const labelInput = document.querySelector("[data-address-book-pdf-insert-file-label]");
  const cropInput = document.querySelector("[data-address-book-pdf-insert-file-crop]");
  if (
    !modal
    || !openButton
    || !fileInput
    || !zoomInput
    || !preview
    || !applyButton
    || !clearButton
    || !cancelButton
    || !includeCheckbox
    || !includeValue
    || !summaryEl
    || !dataInput
    || !mimeInput
    || !widthInput
    || !heightInput
    || !labelInput
    || !cropInput
  ) {
    return;
  }

  const savedInsertStorageKey = "addressBookPdfSavedInsertFile";
  const maxPayloadChars = 6_000_000;
  let pendingInsert = null;
  let sourceCover = null;
  let cropState = { x: 0.5, y: 0.5, zoom: 1 };

  if (window.pdfjsLib?.GlobalWorkerOptions) {
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js";
  }

  const infoButton = document.querySelector("[data-address-book-pdf-insert-info-open]");
  const infoModal = document.querySelector("[data-address-book-pdf-insert-info-modal]");
  infoButton?.addEventListener("click", () => {
    if (typeof infoModal?.showModal === "function") infoModal.showModal();
  });

  const syncInsertChoiceVisibility = () => {
    const wantsInsert = includeCheckbox.checked;
    openButton.hidden = !wantsInsert;
    if (!wantsInsert) {
      summaryEl.hidden = true;
    } else if (dataInput.value) {
      summaryEl.hidden = false;
    }
  };

  const PRINT_PX_PER_IN = 600;
  const PREVIEW_PX_PER_IN = 144;
  const PRINT_JPEG_QUALITY = 0.95;

  const pageTrimInches = () => ({
    widthIn: Math.max(0.1, addressBookPdfNumber(form, "trim_width_in", 3.5)),
    heightIn: Math.max(0.1, addressBookPdfNumber(form, "trim_height_in", 5.5)),
  });

  const dimensionsAtDpi = (pxPerIn) => {
    const { widthIn, heightIn } = pageTrimInches();
    return {
      width: Math.max(1, Math.round(widthIn * pxPerIn)),
      height: Math.max(1, Math.round(heightIn * pxPerIn)),
    };
  };

  const trimDimensions = () => dimensionsAtDpi(PRINT_PX_PER_IN);
  const previewTrimDimensions = () => dimensionsAtDpi(PREVIEW_PX_PER_IN);

  // On-screen preview size: fit within the modal while keeping the trim page
  // aspect ratio, so the preview mirrors the printed page instead of overflowing.
  const stageDimensions = () => {
    const widthIn = Math.max(0.1, addressBookPdfNumber(form, "trim_width_in", 3.5));
    const heightIn = Math.max(0.1, addressBookPdfNumber(form, "trim_height_in", 5.5));
    const ratio = widthIn / heightIn;
    const availWidth = Math.max(120, preview.clientWidth || 320);
    const availHeight = Math.max(160, Math.min(window.innerHeight * 0.6, 620));
    let width = availWidth;
    let height = width / ratio;
    if (height > availHeight) {
      height = availHeight;
      width = height * ratio;
    }
    return { width: Math.max(1, Math.round(width)), height: Math.max(1, Math.round(height)) };
  };

  const updateSummary = () => {
    if (!includeCheckbox.checked || !dataInput.value) {
      summaryEl.hidden = true;
      summaryEl.textContent = "";
      return;
    }
    const label = labelInput.value || "Selected file";
    summaryEl.textContent = `File name: ${label}`;
    summaryEl.hidden = false;
  };

  const readSavedInsert = () => {
    try {
      return JSON.parse(window.localStorage.getItem(savedInsertStorageKey) || "null");
    } catch {
      return null;
    }
  };

  const writeSavedInsert = (payload) => {
    window.localStorage.setItem(savedInsertStorageKey, JSON.stringify(payload));
  };

  const arrayBufferToBase64 = (buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return btoa(binary);
  };

  const pdfBase64Payload = (value) => {
    const raw = String(value || "").trim();
    const match = raw.match(/^data:application\/pdf;base64,(.+)$/i);
    return match ? match[1] : raw.replace(/^data:[^;]+;base64,/i, "");
  };

  const readStoredCrop = (raw) => {
    try {
      const parsed = JSON.parse(String(raw || "") || "null");
      if (!parsed || typeof parsed !== "object") return { x: 0.5, y: 0.5, zoom: 1 };
      return {
        x: Math.min(1, Math.max(0, parseFloat(parsed.x) || 0.5)),
        y: Math.min(1, Math.max(0, parseFloat(parsed.y) || 0.5)),
        zoom: Math.min(3, Math.max(1, parseFloat(parsed.zoom) || 1)),
      };
    } catch {
      return { x: 0.5, y: 0.5, zoom: 1 };
    }
  };

  const clampCropState = () => {
    cropState.zoom = Math.min(3, Math.max(1, parseFloat(cropState.zoom || 1) || 1));
    cropState.x = Math.min(1, Math.max(0, parseFloat(cropState.x || 0.5) || 0.5));
    cropState.y = Math.min(1, Math.max(0, parseFloat(cropState.y || 0.5) || 0.5));
  };

  const updateCropPreview = () => {
    const imageEl = preview.querySelector("[data-address-book-pdf-insert-crop-image]");
    const stage = preview.querySelector(".address-book-pdf-insert-preview-stage");
    if (!imageEl || !stage || !sourceCover?.image) return;
    clampCropState();
    const stageWidth = stage.clientWidth || 1;
    const stageHeight = stage.clientHeight || 1;
    const sourceRatio = sourceCover.image.naturalWidth / sourceCover.image.naturalHeight;
    const stageRatio = stageWidth / stageHeight;
    let baseWidth = stageWidth;
    let baseHeight = stageHeight;
    if (sourceRatio > stageRatio) {
      baseHeight = stageHeight;
      baseWidth = stageHeight * sourceRatio;
    } else {
      baseWidth = stageWidth;
      baseHeight = stageWidth / sourceRatio;
    }
    const drawWidth = baseWidth * cropState.zoom;
    const drawHeight = baseHeight * cropState.zoom;
    const minX = Math.min(0, stageWidth - drawWidth);
    const minY = Math.min(0, stageHeight - drawHeight);
    const x = minX * cropState.x;
    const y = minY * cropState.y;
    imageEl.style.width = `${drawWidth}px`;
    imageEl.style.height = `${drawHeight}px`;
    imageEl.style.transform = `translate(${x}px, ${y}px)`;
    zoomInput.value = String(cropState.zoom);
  };

  const drawImagePreview = (src) => {
    const { width, height } = stageDimensions();
    preview.innerHTML = src
      ? `<div class="address-book-pdf-insert-preview-stage" style="width:${width}px;height:${height}px;">
          <img src="${src}" alt="Selected insert file" data-address-book-pdf-insert-crop-image>
        </div>`
      : '<div class="address-book-pdf-insert-preview-empty">No file selected.</div>';
    zoomInput.hidden = !src;
    updateCropPreview();
  };

  const loadImageSourceCover = (src, label, extra = {}) => new Promise((resolve, reject) => {
    const image = new Image();
    image.onerror = () => reject(new Error("Could not load file preview."));
    image.onload = () => {
      sourceCover = { src, image, label: label || "File", ...extra };
      cropState = extra.crop ? { ...extra.crop } : { x: 0.5, y: 0.5, zoom: 1 };
      drawImagePreview(src);
      resolve(sourceCover);
    };
    image.src = src;
  });

  const cropWindowForSource = (sourceWidth, sourceHeight, outputWidth, outputHeight, crop = cropState) => {
    const zoom = Math.min(3, Math.max(1, parseFloat(crop?.zoom || 1) || 1));
    const cropX = Math.min(1, Math.max(0, parseFloat(crop?.x || 0.5) || 0.5));
    const cropY = Math.min(1, Math.max(0, parseFloat(crop?.y || 0.5) || 0.5));
    const sourceRatio = sourceWidth / Math.max(sourceHeight, 1);
    const targetRatio = outputWidth / Math.max(outputHeight, 1);
    let sw = sourceWidth;
    let sh = sourceHeight;
    if (sourceRatio > targetRatio) {
      sh = sourceHeight / zoom;
      sw = sh * targetRatio;
    } else {
      sw = sourceWidth / zoom;
      sh = sw / targetRatio;
    }
    const maxSx = Math.max(0, sourceWidth - sw);
    const maxSy = Math.max(0, sourceHeight - sh);
    return {
      sx: maxSx * cropX,
      sy: maxSy * cropY,
      sw,
      sh,
    };
  };

  const rasterizePdfPageToJpegDataUrl = async (base64, { width, height }, { intent = "display", quality = 0.82, crop = cropState } = {}) => {
    if (!window.pdfjsLib?.getDocument) {
      throw new Error("PDF preview is unavailable.");
    }
    const pdfBytes = Uint8Array.from(atob(pdfBase64Payload(base64)), (char) => char.charCodeAt(0));
    const pdf = await window.pdfjsLib.getDocument({ data: pdfBytes }).promise;
    const page = await pdf.getPage(1);
    const baseViewport = page.getViewport({ scale: 1 });
    const sourceCrop = cropWindowForSource(baseViewport.width, baseViewport.height, width, height, crop);
    const scale = Math.min(width / Math.max(sourceCrop.sw, 1), 8192 / Math.max(baseViewport.width, baseViewport.height, 1));
    const scaledViewport = page.getViewport({ scale });
    const renderCanvas = document.createElement("canvas");
    renderCanvas.width = Math.ceil(scaledViewport.width);
    renderCanvas.height = Math.ceil(scaledViewport.height);
    const renderContext = renderCanvas.getContext("2d");
    if (!renderContext) {
      throw new Error("Could not prepare PDF preview.");
    }
    await page.render({
      canvasContext: renderContext,
      viewport: scaledViewport,
      intent,
    }).promise;
    const outputCanvas = document.createElement("canvas");
    outputCanvas.width = width;
    outputCanvas.height = height;
    const outputContext = outputCanvas.getContext("2d");
    if (!outputContext) {
      throw new Error("Could not prepare PDF preview.");
    }
    outputContext.imageSmoothingEnabled = true;
    outputContext.imageSmoothingQuality = "high";
    outputContext.fillStyle = "#ffffff";
    outputContext.fillRect(0, 0, width, height);
    outputContext.drawImage(
      renderCanvas,
      sourceCrop.sx * scale,
      sourceCrop.sy * scale,
      sourceCrop.sw * scale,
      sourceCrop.sh * scale,
      0,
      0,
      width,
      height,
    );
    return outputCanvas.toDataURL("image/jpeg", quality);
  };

  const loadPdfSourceCover = async (base64, label, crop = null) => {
    const payload = pdfBase64Payload(base64);
    const dataUrl = await rasterizePdfPageToJpegDataUrl(
      payload,
      previewTrimDimensions(),
      { crop: { x: 0.5, y: 0.5, zoom: 1 } },
    );
    return loadImageSourceCover(dataUrl, label || "PDF", { pdfBase64: payload, crop });
  };

  const syncInsertForPrint = () => {
    includeValue.value = includeCheckbox.checked && dataInput.value ? "1" : "0";
  };

  const fileToSourceCover = (file) => new Promise((resolve, reject) => {
    if (!file) {
      resolve(null);
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read file."));
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => reject(new Error("Could not load picture."));
      image.onload = () => {
        resolve({
          src: String(reader.result || ""),
          image,
          label: file.name || "Picture",
        });
      };
      image.src = String(reader.result || "");
    };
    reader.readAsDataURL(file);
  });

  const sourceToCover = async () => {
    if (!sourceCover?.image && !sourceCover?.pdfBase64) return pendingInsert;
    const { width, height } = trimDimensions();
    if (sourceCover.pdfBase64) {
      clampCropState();
      return {
        data: `data:application/pdf;base64,${sourceCover.pdfBase64}`,
        width: 0,
        height: 0,
        mime: "application/pdf",
        crop: { x: cropState.x, y: cropState.y, zoom: cropState.zoom },
        label: sourceCover.label || "PDF",
      };
    }
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return pendingInsert;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);
    const crop = cropWindowForSource(
      sourceCover.image.naturalWidth,
      sourceCover.image.naturalHeight,
      width,
      height,
    );
    ctx.drawImage(sourceCover.image, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, width, height);
    return {
      data: canvas.toDataURL("image/jpeg", PRINT_JPEG_QUALITY),
      width,
      height,
      mime: "image/jpeg",
      label: sourceCover.label || "Picture",
    };
  };

  const setStoredInsert = (insert) => {
    dataInput.value = insert?.data || "";
    mimeInput.value = insert?.mime || "image/jpeg";
    widthInput.value = insert?.width ? String(insert.width) : "";
    heightInput.value = insert?.height ? String(insert.height) : "";
    labelInput.value = insert?.label || "";
    cropInput.value = insert?.crop ? JSON.stringify(insert.crop) : "";
    includeCheckbox.checked = Boolean(insert?.data);
    includeValue.value = insert?.data ? "1" : "0";
    syncInsertChoiceVisibility();
    updateSummary();
  };

  const applyCurrentInsert = async () => {
    const cover = await sourceToCover();
    if (!cover?.data) return null;
    if (cover.data.length > maxPayloadChars) {
      window.alert("This file is too large to include in the print request.");
      return null;
    }
    return cover;
  };

  includeCheckbox.addEventListener("change", () => {
    includeValue.value = includeCheckbox.checked && dataInput.value ? "1" : includeCheckbox.checked ? includeValue.value : "0";
    if (!includeCheckbox.checked) {
      includeValue.value = "0";
    }
    syncInsertChoiceVisibility();
    updateSummary();
  });

  openButton.addEventListener("click", async () => {
    pendingInsert = dataInput.value
      ? {
          data: dataInput.value,
          mime: mimeInput.value,
          width: widthInput.value,
          height: heightInput.value,
          label: labelInput.value,
          crop: readStoredCrop(cropInput.value),
        }
      : readSavedInsert();
    sourceCover = null;
    cropState = pendingInsert?.crop ? { ...pendingInsert.crop } : { x: 0.5, y: 0.5, zoom: 1 };
    includeCheckbox.checked = includeValue.value === "1" && Boolean(dataInput.value);
    modal.showModal();
    try {
      if (pendingInsert?.mime === "application/pdf" && pendingInsert.data) {
        await loadPdfSourceCover(pendingInsert.data, pendingInsert.label || "PDF", pendingInsert.crop);
        return;
      }
      if (pendingInsert?.data) {
        await loadImageSourceCover(pendingInsert.data, pendingInsert.label || "Picture");
        return;
      }
      drawImagePreview("");
    } catch {
      preview.innerHTML = '<div class="address-book-pdf-insert-preview-empty">Could not preview this file.</div>';
      zoomInput.hidden = true;
    }
  });

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    const lowerName = String(file.name || "").toLowerCase();
    const isPdf = file.type === "application/pdf" || lowerName.endsWith(".pdf");
    try {
      if (isPdf) {
        const buffer = await file.arrayBuffer();
        const base64 = arrayBufferToBase64(buffer);
        await loadPdfSourceCover(base64, file.name || "PDF");
        return;
      }
      sourceCover = await fileToSourceCover(file);
      cropState = { x: 0.5, y: 0.5, zoom: 1 };
      drawImagePreview(sourceCover?.src || "");
    } catch (error) {
      window.alert(error?.message || "Could not load this file.");
      fileInput.value = "";
    }
  });

  zoomInput.addEventListener("input", () => {
    cropState.zoom = parseFloat(zoomInput.value || "1") || 1;
    updateCropPreview();
  });

  preview.addEventListener("pointerdown", (event) => {
    const cropImage = event.target.closest?.("[data-address-book-pdf-insert-crop-image]");
    if (!cropImage || !sourceCover?.image) return;
    event.preventDefault();
    const stage = preview.querySelector(".address-book-pdf-insert-preview-stage");
    if (!stage) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const startCrop = { ...cropState };
    const onMove = (moveEvent) => {
      const imageEl = preview.querySelector("[data-address-book-pdf-insert-crop-image]");
      if (!imageEl) return;
      const stageWidth = stage.clientWidth || 1;
      const stageHeight = stage.clientHeight || 1;
      const drawWidth = parseFloat(imageEl.style.width || "0") || stageWidth;
      const drawHeight = parseFloat(imageEl.style.height || "0") || stageHeight;
      const rangeX = Math.max(1, drawWidth - stageWidth);
      const rangeY = Math.max(1, drawHeight - stageHeight);
      cropState.x = startCrop.x - ((moveEvent.clientX - startX) / rangeX);
      cropState.y = startCrop.y - ((moveEvent.clientY - startY) / rangeY);
      updateCropPreview();
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });

  applyButton.addEventListener("click", async () => {
    const insert = await applyCurrentInsert();
    if (!insert?.data) {
      window.alert("Choose a PDF or picture before applying this file.");
      return;
    }
    insert.label = sourceCover?.label || labelInput.value || insert.label || "File";
    setStoredInsert(insert);
    try {
      writeSavedInsert(insert);
    } catch {
      window.alert("This file is too large to save in browser storage.");
    }
    modal.close();
  });

  clearButton.addEventListener("click", () => {
    pendingInsert = null;
    sourceCover = null;
    fileInput.value = "";
    setStoredInsert(null);
    window.localStorage.removeItem(savedInsertStorageKey);
    modal.close();
  });

  cancelButton.addEventListener("click", () => modal.close());
  modal.addEventListener("cancel", () => modal.close());

  form.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    if (!(submitter instanceof HTMLButtonElement)) return;
    if (!submitter.matches("[data-address-book-pdf-print-button], [data-address-book-pdf-print-paper-button]")) return;
    syncInsertForPrint();
  });

  const savedInsert = readSavedInsert();
  if (savedInsert?.data) {
    setStoredInsert(savedInsert);
  } else {
    syncInsertChoiceVisibility();
  }
}

function installAddressBookPdfPickers() {
  const palette = document.querySelector("[data-address-book-pdf-picker]");
  const fieldSelect = document.querySelector("[data-address-book-pdf-field-picker]");
  const meetingSelect = document.querySelector("[data-address-book-pdf-meeting-picker]");
  if (!palette || !fieldSelect || !meetingSelect) return;

  const orderedFields = () => getAddressBookPdfOrderedFields();

  const renderFields = () => {
    const previousFieldName = fieldSelect.value || fieldSelect.dataset.selectedFieldName || "";
    const fields = orderedFields();
    fieldSelect.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "All Fields";
    fieldSelect.appendChild(allOption);
    fields.forEach((field) => {
      const option = document.createElement("option");
      option.value = field.name || "";
      option.textContent = field.name || "";
      if (option.value === previousFieldName) option.selected = true;
      fieldSelect.appendChild(option);
    });
    if (!fields.some((field) => field.name === previousFieldName)) {
      fieldSelect.value = "";
    }
    fieldSelect.dataset.selectedFieldName = fieldSelect.value;
  };

  const refreshMeetings = () => {
    const selectedFieldName = fieldSelect.value || "";
    const previousMeetingName = meetingSelect.value || meetingSelect.dataset.selectedMeetingName || "";
    const fields = orderedFields();
    const field = fields.find((item) => item.name === selectedFieldName);
    const meetings = field?.meetings || [];

    meetingSelect.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "All Meetings";
    meetingSelect.appendChild(allOption);

    meetings.forEach((meeting) => {
      const option = document.createElement("option");
      option.value = meeting.name || "";
      option.textContent = meeting.name || "";
      if (option.value === previousMeetingName) option.selected = true;
      meetingSelect.appendChild(option);
    });
    if (!meetings.some((meeting) => meeting.name === previousMeetingName)) {
      meetingSelect.value = "";
    }
    meetingSelect.dataset.selectedMeetingName = meetingSelect.value;
    renderAddressBookPdfContactPreview();
  };

  fieldSelect.addEventListener("change", () => {
    fieldSelect.dataset.selectedFieldName = fieldSelect.value;
    meetingSelect.dataset.selectedMeetingName = "";
    refreshMeetings();
    syncAddressBookPdfMeetingTypographyFromSelection();
    syncAddressBookPdfCoverForPreviewPickers();
    updateAddressBookPdfPreview();
  });
  meetingSelect.addEventListener("change", () => {
    meetingSelect.dataset.selectedMeetingName = meetingSelect.value;
    if (addressBookPdfPreviewHasMeetingSelected()) {
      turnOffAddressBookPdfCoverForMeetingPreview();
    } else {
      syncAddressBookPdfCoverForPreviewPickers();
    }
    syncAddressBookPdfMeetingTypographyFromSelection();
    renderAddressBookPdfContactPreview();
    updateAddressBookPdfPreview();
  });
  document.addEventListener("address-book-pdf-print-order-changed", () => {
    renderFields();
    refreshMeetings();
    syncAddressBookPdfMeetingTypographyFromSelection();
    updateAddressBookPdfPreview();
  });
  renderFields();
  refreshMeetings();
}

function installAddressBookPdfDeleteLayoutModal(form) {
  const deleteButton = document.querySelector("[data-address-book-delete-layout]");
  if (!deleteButton) return;
  const deleteModal = window.AppModal?.create({
    dialog: document.getElementById("address-book-delete-layout-modal"),
    form: document.getElementById("address-book-delete-layout-modal-form"),
    titleEl: document.getElementById("address-book-delete-layout-modal-title"),
    copyEl: document.getElementById("address-book-delete-layout-modal-copy"),
    labelEl: document.getElementById("address-book-delete-layout-modal-label"),
    inputEl: document.getElementById("address-book-delete-layout-modal-input"),
    errorEl: document.getElementById("address-book-delete-layout-modal-error"),
    cancelEl: document.getElementById("address-book-delete-layout-modal-cancel"),
    saveEl: document.getElementById("address-book-delete-layout-modal-confirm"),
  });

  deleteButton.addEventListener("click", async (event) => {
    event.preventDefault();
    const confirmed = await deleteModal.prompt({
      title: "Delete Layout?",
      copy: "Meetings using this layout will be reassigned to another layout.",
      label: "Layout",
      requireInput: false,
      submitLabel: "Delete",
    });
    if (!confirmed) return;
    form.action = deleteButton.getAttribute("formaction") || form.action;
    form.method = deleteButton.getAttribute("formmethod") || "post";
    form.submit();
  });
}

window.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-address-book-pdf-form]");
  const presetPicker = document.querySelector("[data-address-book-pdf-preset-picker]");
  if (!form) return;
  installAddressBookPdfTemporaryNotice();
  installAddressBookPdfPickers();
  installAddressBookPdfTypographyInfo();
  installAddressBookPdfMeetingLineSpacing(form);
  installAddressBookPdfMeetingFontSize(form);
  syncAddressBookPdfMeetingTypographyFromSelection(form);
  enhanceAddressBookPdfNumberInputs(form);
  installAddressBookPdfMapProviderControls(form);
  installAddressBookPdfPrintOrderModal(form);
  installAddressBookPdfPrintFieldsModal(form);
  installAddressBookPdfCoverModal(form);
  installAddressBookPdfInsertFileModal(form);
  installAddressBookPdfDeleteLayoutModal(form);
  const coverIncludeCheckbox = document.querySelector("[data-address-book-pdf-cover-include]");
  coverIncludeCheckbox?.addEventListener("click", (event) => event.stopPropagation());
  coverIncludeCheckbox?.addEventListener("input", (event) => {
    event.stopPropagation();
    applyAddressBookPdfCoverIncludeFromCheckbox();
  });
  coverIncludeCheckbox?.addEventListener("change", (event) => {
    event.stopPropagation();
    applyAddressBookPdfCoverIncludeFromCheckbox();
  });
  if (addressBookPdfPreviewHasMeetingSelected()) {
    turnOffAddressBookPdfCoverForMeetingPreview();
  } else {
    syncAddressBookPdfCoverForPreviewPickers();
  }
  updateAddressBookPdfPreview();

  form.addEventListener("input", updateAddressBookPdfPreview);
  form.addEventListener("change", updateAddressBookPdfPreview);

  presetPicker?.addEventListener("change", () => {
    if (!presetPicker.value) return;
    if (presetPicker.value === "__default__") {
      form.action = "/address-book/pdf/set-default";
      form.method = "post";
      form.submit();
      return;
    }
    window.location.href = `/address-book/pdf?preset_id=${encodeURIComponent(presetPicker.value)}`;
  });
});
