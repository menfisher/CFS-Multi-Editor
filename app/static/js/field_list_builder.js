window.addEventListener("DOMContentLoaded", () => {
  const stage = document.querySelector("[data-field-list-builder]");
  if (!stage) return;

  const canEdit = stage.dataset.fieldListCanEdit === "1";
  const canvas = document.getElementById("field-list-canvas");
  const marginBox = document.getElementById("field-list-canvas-margin");
  const hiddenInput = document.getElementById("field-list-items-json");
  const addButtons = Array.from(document.querySelectorAll("[data-field-list-add]"));
  const settingInputs = Array.from(document.querySelectorAll("[data-field-list-setting]"));
  const fieldSelect = document.querySelector("[data-field-list-field-picker]");
  const meetingSelect = document.querySelector("[data-field-list-meeting-picker]");
  const contactSelect = document.querySelector("[data-field-list-contact-picker]");
  const saveForm = document.getElementById("field-list-save-form");
  const templateIdInput = saveForm?.querySelector("input[name='template_id']");
  const templateNameInput = document.getElementById("field-list-template-name");
  const templatePicker = document.getElementById("field-list-template-picker");
  const templateToolbar = document.querySelector("[data-field-list-template-toolbar]");
  const userTemplatePlaceholderOption = templatePicker?.querySelector("[data-user-template-placeholder]");
  const settingsForm = document.querySelector(".field-list-settings-form");
  const createForm = document.querySelector("[data-field-list-create-form]");
  const createButton = document.querySelector("[data-field-list-create-button]");
  const deleteForm = document.querySelector("[data-field-list-delete-form]");
  const deleteTemplateIdInput = deleteForm?.querySelector("input[name='template_id']");
  const deleteTemplateButton = document.querySelector(".field-list-delete-template-button");
  const settingsTemplateNameInput = document.querySelector("[data-field-list-settings-template-name]");
  const settingsItemsInput = document.querySelector("[data-field-list-settings-items-json]");
  const createTemplateNameInput = document.querySelector("[data-field-list-create-template-name]");
  const createItemsInput = document.querySelector("[data-field-list-create-items-json]");
  const printForm = document.querySelector("[data-field-list-print-form]");
  const printItemsInput = document.querySelector("[data-field-list-print-items-json]");
  const printButton = document.querySelector("[data-field-list-print-button]");
  const printPickerModal = document.getElementById("field-list-print-picker-modal");
  const printModalFields = document.querySelector("[data-print-modal-fields]");
  const printModalMeetings = document.querySelector("[data-print-modal-meetings]");
  const printModalCancel = document.querySelector("[data-print-modal-cancel]");
  const printModalSubmit = document.querySelector("[data-print-modal-submit]");
  const bibleFontConflictModal = document.getElementById("field-list-bible-font-conflict-modal");
  const bibleFontConflictOk = document.querySelector("[data-bible-font-conflict-ok]");
  const bibleFontConflictCancel = document.querySelector("[data-bible-font-conflict-cancel]");
  const bibleFontResolveModal = document.getElementById("field-list-bible-font-resolve-modal");
  const bibleFontResolveTitle = document.querySelector("[data-bible-font-resolve-title]");
  const bibleFontResolveList = document.querySelector("[data-bible-font-resolve-list]");
  const bibleFontResolveOk = document.querySelector("[data-bible-font-resolve-ok]");
  const bibleFontResolveCancel = document.querySelector("[data-bible-font-resolve-cancel]");
  const separatorVerticalOption = document.querySelector("[data-separator-vertical-option]");
  const columnWidthOption = document.querySelector("[data-column-width-option]");
  const columnWidthInput = document.querySelector("input[name='column_width_in']");
  const columnGapOption = document.querySelector("[data-column-gap-option]");
  const manualPaletteOption = document.querySelector("[data-manual-palette-option]");
  const printAlphabeticalOption = document.querySelector("[data-print-alphabetical-option]");
  const columnModeInputs = Array.from(document.querySelectorAll("input[name='one_column_print_mode'], input[name='two_column_print_mode']"));
  const addressAlignInputs = settingInputs.filter((input) => input.dataset.fieldListSetting === "addressAlign");
  const addressAlignOptionRows = addressAlignInputs.map((input) => input.closest(".field-list-radio-row")).filter(Boolean);
  const bibleControls = document.querySelector(".field-list-bible-controls");
  const bibleIncludeInput = document.querySelector("[data-field-list-setting='includeBibleStudyUnionInfo']");
  const bibleSizeOption = document.querySelector(".field-list-bible-size-option");
  const bibleSizeInput = document.querySelector("[data-field-list-setting='bibleStudyFontSizePt']");
  const bibleAlignOptions = document.querySelector(".field-list-bible-align-options");
  const bibleAlignInputs = settingInputs.filter((input) => input.dataset.fieldListSetting === "bibleStudyUnionAlign");
  const printMeetingHomeOption = document.querySelector(".field-list-print-meeting-home-option");
  const printMeetingHomeInput = document.querySelector("input[name='print_meeting_home_first']");
  const settings = JSON.parse(stage.dataset.fieldListSettings || "{}");
  const itemTypes = JSON.parse(stage.dataset.fieldListItemTypes || "[]");
  const previewContactsPayload = JSON.parse(stage.dataset.fieldListPreviewContacts || "{}");
  const pickerPayload = JSON.parse(stage.dataset.fieldListPicker || "{}");
  const meetingPreviewPayload = JSON.parse(stage.dataset.fieldListMeetingPreview || "{}");
  const previewContacts = Array.isArray(previewContactsPayload.contacts) ? previewContactsPayload.contacts : [];
  const pickerFields = Array.isArray(pickerPayload.fields) ? pickerPayload.fields : [];
  const itemTypeMap = new Map(itemTypes.map((item) => [item.type, item]));
  let items = JSON.parse(stage.dataset.fieldListItems || "[]");
  let activeId = null;
  let selectedFieldName = "";
  let selectedMeetingName = "";
  let selectedContactId = 0;
  let revealedDeleteId = null;
  let lastPrintTextClick = { id: null, time: 0 };
  let alignBibleStudyOnNextRender = false;
  let printModalFieldName = "";
  let printModalMeetingNames = new Set();
  let bibleFontInspection = null;
  let printMeetingHomeFirst = Boolean(printMeetingHomeInput?.checked);
  let hasRevealedCustomTemplateToolbar = !templateToolbar?.hidden;

  const syncTemplatePickerPlaceholderState = () => {
    templatePicker?.classList.toggle("is-placeholder", !templatePicker.value);
  };

  const revealCustomTemplateToolbar = () => {
    if (!templateToolbar || hasRevealedCustomTemplateToolbar) return;
    templateToolbar.hidden = false;
    hasRevealedCustomTemplateToolbar = true;
    if (templateIdInput) templateIdInput.value = "0";
    if (deleteTemplateIdInput) deleteTemplateIdInput.value = "0";
    if (deleteTemplateButton) deleteTemplateButton.disabled = true;
    if (templatePicker && userTemplatePlaceholderOption) {
      userTemplatePlaceholderOption.disabled = false;
      templatePicker.value = "";
      userTemplatePlaceholderOption.disabled = true;
    }
    if (templateNameInput) {
      templateNameInput.value = "";
      templateNameInput.placeholder = "User defined custom name";
    }
    syncTemplatePickerPlaceholderState();
  };

  const shouldRevealTemplateToolbarForSettingInput = (input) => {
    const key = input?.dataset?.fieldListSetting || "";
    if (key === "pageColumnCount") return false;
    return true;
  };

  syncTemplatePickerPlaceholderState();
  const selectionStorageKey = `field-list-selection:${templateIdInput?.value || "default"}`;

  const pageWidthIn = Number(settings.pageWidthIn || 8.5);
  const pageHeightIn = Number(settings.pageHeightIn || 11);
  const gridStepIn = 0.125;
  const fieldListModal = window.AppModal?.create({
    dialog: document.getElementById("field-list-message-modal"),
    form: document.getElementById("field-list-message-modal-form"),
    titleEl: document.getElementById("field-list-message-modal-title"),
    copyEl: document.getElementById("field-list-message-modal-copy"),
    labelEl: document.getElementById("field-list-message-modal-label"),
    inputEl: document.getElementById("field-list-message-modal-input"),
    errorEl: document.getElementById("field-list-message-modal-error"),
    cancelEl: document.getElementById("field-list-message-modal-cancel"),
    saveEl: document.getElementById("field-list-message-modal-save"),
  });

  const snapInches = (value) => Math.max(0, Math.round(value / gridStepIn) * gridStepIn);
  const ceilInches = (value) => Math.max(0, Math.ceil(value / gridStepIn) * gridStepIn);
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
  const showMessage = async (copy, title = "Field List") => {
    if (fieldListModal) {
      await fieldListModal.message({ title, copy, buttonLabel: "Close" });
      return;
    }
    window.alert(copy);
  };
  const normalizeTemplateName = (value) => String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
  const templateNameExistsForAnotherTemplate = (name) => {
    const normalizedName = normalizeTemplateName(name);
    if (!normalizedName || !templatePicker) return false;
    const currentTemplateId = Number(templateIdInput?.value || 0);
    return Array.from(templatePicker.options).some((option) => {
      const optionTemplateId = Number(option.value || 0);
      if (!optionTemplateId || optionTemplateId === currentTemplateId) return false;
      return normalizeTemplateName(option.textContent) === normalizedName;
    });
  };
  const confirmAction = async ({ title, copy, submitLabel = "OK" }) => {
    if (fieldListModal) {
      return Boolean(await fieldListModal.prompt({
        title,
        copy,
        label: "Confirmation",
        requireInput: false,
        submitLabel,
      }));
    }
    return window.confirm(copy);
  };
  const numberSetting = (key, fallback, min, max) => clamp(Number(settings[key] || fallback), min, max);
  const getPreviewScale = () => 1;
  const getPxPerIn = () => 96 * getPreviewScale();
  const getCanvasBounds = () => ({
    minX: numberSetting("marginLeftIn", 0.35, 0.1, 2),
    minY: numberSetting("marginTopIn", 0.35, 0.1, 2),
    maxX: pageWidthIn - numberSetting("marginRightIn", 0.35, 0.1, 2),
    maxY: pageHeightIn - numberSetting("marginBottomIn", 0.35, 0.1, 2),
  });

  const minPhoneWidthIn = (fontSizePt) => Math.max((12 * Math.max(fontSizePt || 10, 8) * 0.58) / 72, 1.2);
  const escapeHtml = (value) => String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
  const printNotePlainText = (value) => {
    const source = String(value ?? "");
    if (!/[<>]/.test(source)) return source;
    const template = document.createElement("template");
    template.innerHTML = source;
    return template.content.textContent ?? "";
  };
  const sanitizePrintNoteHtml = (value) => {
    const source = String(value ?? "");
    if (!/[<>]/.test(source)) return escapeHtml(source);
    const template = document.createElement("template");
    template.innerHTML = source;
    const render = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return escapeHtml(node.textContent || "");
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
        return `<span data-note-size-delta="${escapeHtml(label)}" style="font-size: calc(1em + ${escapeHtml(label)}pt)">${children}</span>`;
      }
      return children;
    };
    return Array.from(template.content.childNodes).map(render).join("");
  };

  const getSelectedField = () => pickerFields.find((field) => field.name === selectedFieldName) || null;
  const getSelectedMeeting = () => {
    const field = getSelectedField();
    if (!field) return null;
    return (field.meetings || []).find((meeting) => meeting.name === selectedMeetingName) || null;
  };
  const shouldPrintMeetingHomeFirst = () => !isAlphabeticalMode() && printMeetingHomeFirst;
  const contactSortLabel = (contact) => String(contact?.sort_label || contact?.name || contact?.label || "").toLowerCase();
  const contactIdValue = (contact) => Number(contact?.id || 0);
  const meetingRoleRank = (contact, hasMeetingHome) => {
    const flag = String(contact?.mtg_home_elder_flag || "").trim();
    if (flag.startsWith("1")) return 0;
    if (flag.startsWith("2")) return hasMeetingHome ? 1 : 0;
    return hasMeetingHome ? 2 : 1;
  };
  const sortMeetingContacts = (contacts) => {
    const ordered = [...(contacts || [])];
    if (shouldPrintMeetingHomeFirst()) {
      const hasMeetingHome = ordered.some((contact) => String(contact?.mtg_home_elder_flag || "").trim().startsWith("1"));
      const hasMeetingRoleFlags = ordered.some((contact) => String(contact?.mtg_home_elder_flag || "").trim());
      if (!hasMeetingRoleFlags) return ordered;
      return ordered.sort((left, right) => (
        meetingRoleRank(left, hasMeetingHome) - meetingRoleRank(right, hasMeetingHome)
        || contactSortLabel(left).localeCompare(contactSortLabel(right))
        || contactIdValue(left) - contactIdValue(right)
      ));
    }
    return ordered.sort((left, right) => (
      contactSortLabel(left).localeCompare(contactSortLabel(right))
      || contactIdValue(left) - contactIdValue(right)
    ));
  };
  const getSelectedContact = () => {
    const meeting = getSelectedMeeting();
    const contacts = sortMeetingContacts(meeting?.contacts || []);
    return contacts.find((contact) => Number(contact.id) === Number(selectedContactId)) || contacts[0] || null;
  };
  const getSelectedMeetingContacts = () => sortMeetingContacts(getSelectedMeeting()?.contacts || []);
  const isClusterType = (type) => ["contacts", "meetings", "bible_study_union"].includes(type);
  const getPageColumnCount = () => {
    const checked = document.querySelector("input[name='page_column_count']:checked");
    return Number(checked?.value || settings.pageColumnCount || 1) === 2 ? 2 : 1;
  };
  const shouldPreviewPageNumbers = () => Boolean(document.querySelector("input[name='print_page_numbers']")?.checked);
  const shouldIncludeBibleStudyUnionInfo = () => Boolean(bibleIncludeInput?.checked);
  const shouldShowSeparatorLines = () => Boolean(settings.separatorLines);
  const shouldShowVerticalSeparatorLines = () => Boolean(settings.separatorVerticalLines);
  const getColumnPrintMode = () => {
    const name = getPageColumnCount() === 2 ? "two_column_print_mode" : "one_column_print_mode";
    return document.querySelector(`input[name='${name}']:checked`)?.value || "meeting";
  };
  const isCompactListMode = () => getPageColumnCount() === 1 && getColumnPrintMode() === "compact";
  const isAlphabeticalMode = () => getColumnPrintMode() === "alphabetical" || isCompactListMode();
  const isOneColumnAlphabeticalMode = () => getPageColumnCount() === 1 && isAlphabeticalMode();
  const syncBibleStudyControls = () => {
    if (!bibleControls) return;
    const disabledByMode = isAlphabeticalMode();
    const disabledByUnchecked = !Boolean(bibleIncludeInput?.checked);
    bibleControls.classList.toggle("is-disabled", disabledByMode);
    bibleControls.classList.toggle("is-bible-off", !disabledByMode && disabledByUnchecked);
    if (bibleIncludeInput) bibleIncludeInput.disabled = disabledByMode;
    bibleSizeOption?.classList.toggle("is-disabled", disabledByMode || disabledByUnchecked);
    bibleAlignOptions?.classList.toggle("is-disabled", disabledByMode || disabledByUnchecked);
    if (bibleSizeInput) bibleSizeInput.disabled = disabledByMode || disabledByUnchecked;
    bibleAlignInputs.forEach((input) => {
      input.disabled = disabledByMode || disabledByUnchecked;
    });
  };
  const syncConditionalSettingsVisibility = () => {
    if (separatorVerticalOption) {
      separatorVerticalOption.hidden = getPageColumnCount() !== 2;
    }
    if (columnWidthOption) {
      columnWidthOption.hidden = false;
    }
    if (columnGapOption) {
      columnGapOption.hidden = false;
    }
    if (manualPaletteOption) {
      manualPaletteOption.hidden = getPageColumnCount() !== 2;
    }
    if (printAlphabeticalOption) {
      printAlphabeticalOption.hidden = getPageColumnCount() === 2;
    }
    syncBibleStudyControls();
    if (printMeetingHomeOption) {
      printMeetingHomeOption.classList.toggle("is-disabled", isAlphabeticalMode());
      printMeetingHomeInput?.toggleAttribute("disabled", isAlphabeticalMode());
    }
    addressAlignInputs.forEach((input) => {
      input.disabled = isCompactListMode();
    });
    addressAlignOptionRows.forEach((row) => {
      row.classList.toggle("is-disabled", isCompactListMode());
    });
    if (printButton) {
      printButton.textContent = usesManualPalettePlacement() ? "Print" : "Next";
    }
  };
  const renderSeparatorLine = () => shouldShowSeparatorLines() ? `<div class="field-list-separator-line"></div>` : "";
  const getBibleStudyUnionAlignClass = () => (
    settings.bibleStudyUnionAlign === "left"
      ? "align-left"
      : settings.bibleStudyUnionAlign === "center"
        ? "align-center"
        : "align-unset"
  );
  const getBibleStudyFontSizePt = () => clamp(Number(settings.bibleStudyFontSizePt || 8.5), 6, 36);
  const bibleStudyStyleAttr = () => `style="font-size: ${getBibleStudyFontSizePt()}pt;"`;
  const getCanvasItemFontSizePt = (item) => {
    if (item?.item_type === "field_name") return numberSetting("titleFontSizePt", 13, 6, 36);
    if (item?.item_type === "meeting_name") return numberSetting("meetingNameFontSizePt", 11, 6, 36);
    if (item?.item_type === "bible_study_union") return getBibleStudyFontSizePt();
    return numberSetting("baseFontSizePt", item?.font_size_pt || 10, 8, 36);
  };
  const clearBibleStudyUnionAlignChoice = () => {
    settings.bibleStudyUnionAlign = "";
    settingInputs
      .filter((input) => input.dataset.fieldListSetting === "bibleStudyUnionAlign")
      .forEach((input) => {
        input.checked = false;
      });
  };
  const getTwoColumnGapCh = () => Math.max(0, Number(settings.twoColumnGapCh ?? 6));
  const usesManualPalettePlacement = () => getPageColumnCount() === 2 && getColumnPrintMode() === "duplicate";
  const getColumnWidthIn = () => clamp(Number(settings.columnWidthIn || 4), 1, pageWidthIn);
  const setColumnWidthIn = (value) => {
    const nextWidth = clamp(Number(value || 4), 1, pageWidthIn);
    settings.columnWidthIn = nextWidth;
    if (columnWidthInput) columnWidthInput.value = String(nextWidth);
  };
  const setDefaultColumnWidthForOneColumnMode = () => {
    if (getPageColumnCount() !== 1) return false;
    const mode = getColumnPrintMode();
    if (mode === "compact") {
      setColumnWidthIn(8.5);
      return true;
    }
    if (mode === "meeting" || mode === "alphabetical") {
      setColumnWidthIn(4);
      return true;
    }
    return false;
  };
  const twoColumnGapChToIn = (gapCh) => (gapCh * numberSetting("baseFontSizePt", 10, 8, 36) * 0.5) / 72;
  const getTwoColumnGapIn = () => twoColumnGapChToIn(getTwoColumnGapCh());
  const getPrintableWidthIn = () => {
    const bounds = getCanvasBounds();
    return Math.max(1, bounds.maxX - bounds.minX);
  };
  const getEffectiveTwoColumnGapIn = () => Math.min(getTwoColumnGapIn(), Math.max(0, getPrintableWidthIn() - 2));
  const getEffectiveTwoColumnGapCh = () => {
    const rawGapCh = getTwoColumnGapCh();
    const rawGapIn = getTwoColumnGapIn();
    const effectiveGapIn = getEffectiveTwoColumnGapIn();
    if (!rawGapIn || effectiveGapIn >= rawGapIn) return rawGapCh;
    return rawGapCh * (effectiveGapIn / rawGapIn);
  };
  const getEffectiveColumnWidthIn = () => {
    if (getPageColumnCount() !== 2) return Math.min(getColumnWidthIn(), getPrintableWidthIn());
    return Math.max(1, (getPrintableWidthIn() - getEffectiveTwoColumnGapIn()) / 2);
  };
  const getCurrentColumnBounds = (item) => {
    const bounds = getCanvasBounds();
    const columnWidth = Math.min(getEffectiveColumnWidthIn(), bounds.maxX - bounds.minX);
    if (getPageColumnCount() !== 2) {
      return {
        left: bounds.minX,
        right: Math.min(bounds.maxX, bounds.minX + columnWidth),
        width: columnWidth,
      };
    }
    const gapIn = getEffectiveTwoColumnGapIn();
    const centerX = bounds.minX + ((bounds.maxX - bounds.minX) / 2);
    const leftColumn = {
      left: bounds.minX,
      right: Math.min(bounds.maxX, centerX - (gapIn / 2)),
      width: columnWidth,
    };
    const rightColumn = {
      left: Math.max(bounds.minX, centerX + (gapIn / 2)),
      right: bounds.maxX,
      width: columnWidth,
    };
    const itemCenter = Number(item.x_in || 0) + (Number(item.width_in || 0) / 2);
    return itemCenter >= rightColumn.left ? rightColumn : leftColumn;
  };
  const alignBibleStudyUnionItem = (item) => {
    if (item.item_type !== "bible_study_union" || !["left", "center"].includes(settings.bibleStudyUnionAlign)) return;
    const column = getCurrentColumnBounds(item);
    const nextX = settings.bibleStudyUnionAlign === "left"
      ? column.left
      : column.left + ((column.width - item.width_in) / 2);
    item.x_in = clamp(snapInches(nextX), column.left, column.right - item.width_in);
  };
  const getAddressAlign = () => settings.addressAlign === "left" ? "left" : "right";
  const isTitleType = (type) => ["field_name", "meeting_name"].includes(type);
  const isPopulatedMeetingBlock = (item) => item.item_type === "meetings" && Boolean(selectedFieldName && selectedMeetingName && getSelectedMeetingContacts().length);
  const isPrintTextType = (item) => isTitleType(item.item_type) || isPopulatedMeetingBlock(item) || ["bible_study_union", "page_number"].includes(item.item_type);
  const textMeasureCanvas = document.createElement("canvas");
  const textMeasureContext = textMeasureCanvas.getContext("2d");

  const appendOption = (select, value, label, selected) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    if (selected) option.selected = true;
    select.appendChild(option);
  };

  const loadSavedSelection = () => {
    if (settings.selectedFieldName || settings.selectedMeetingName || settings.selectedContactId) {
      selectedFieldName = String(settings.selectedFieldName || "");
      selectedMeetingName = String(settings.selectedMeetingName || "");
      selectedContactId = Number(settings.selectedContactId || 0);
      return;
    }
    try {
      const saved = JSON.parse(window.localStorage.getItem(selectionStorageKey) || "{}");
      selectedFieldName = String(saved.fieldName || "");
      selectedMeetingName = String(saved.meetingName || "");
      selectedContactId = Number(saved.contactId || 0);
    } catch (_error) {
      selectedFieldName = "";
      selectedMeetingName = "";
      selectedContactId = 0;
    }
  };

  const saveSelection = () => {
    try {
      window.localStorage.setItem(selectionStorageKey, JSON.stringify({
        fieldName: selectedFieldName,
        meetingName: selectedMeetingName,
        contactId: selectedContactId,
      }));
      settings.selectedFieldName = selectedFieldName;
      settings.selectedMeetingName = selectedMeetingName;
      settings.selectedContactId = selectedContactId;
    } catch (_error) {
      // Ignore storage failures; the template itself still saves normally.
    }
  };

  const populateSelectors = () => {
    if (!fieldSelect || !meetingSelect) return;

    fieldSelect.innerHTML = "";
    appendOption(fieldSelect, "", "All Fields", !selectedFieldName);
    pickerFields.forEach((field) => appendOption(fieldSelect, field.name, field.name, field.name === selectedFieldName));
    if (selectedFieldName && !pickerFields.some((field) => field.name === selectedFieldName)) {
      selectedFieldName = pickerFields[0]?.name || "";
    }
    fieldSelect.value = selectedFieldName;

    const field = getSelectedField();
    const meetings = field?.meetings || [];
    meetingSelect.innerHTML = "";
    appendOption(meetingSelect, "", "All Meetings", !selectedMeetingName);
    meetings.forEach((meeting) => appendOption(meetingSelect, meeting.name, meeting.name, meeting.name === selectedMeetingName));
    if (selectedMeetingName && !meetings.some((meeting) => meeting.name === selectedMeetingName)) {
      selectedMeetingName = meetings[0]?.name || "";
    }
    meetingSelect.value = selectedMeetingName;

    if (!contactSelect) {
      selectedContactId = 0;
      return;
    }
    const meeting = getSelectedMeeting();
    const contacts = meeting?.contacts || [];
    contactSelect.innerHTML = "";
    appendOption(contactSelect, "", "Contacts", !selectedContactId);
    contacts.forEach((contact) => appendOption(contactSelect, String(contact.id), contact.label || contact.name, Number(contact.id) === Number(selectedContactId)));
    if (selectedContactId && !contacts.some((contact) => Number(contact.id) === Number(selectedContactId))) {
      selectedContactId = Number(contacts[0]?.id || 0);
    }
    contactSelect.value = selectedContactId ? String(selectedContactId) : "";
  };

  const previewTextForType = (type) => {
    const selectedContact = getSelectedContact() || previewContacts[0];
    const selectedField = getSelectedField();
    const selectedMeeting = getSelectedMeeting();
    if (selectedContact) {
      if (type === "field_name") return [selectedField?.name || selectedContact.field_name || "Field"];
      if (type === "meeting_name") return [selectedMeeting?.name || selectedContact.meeting_name || "Meeting"];
      if (type === "page_number") return selectedContact.page_number?.length ? selectedContact.page_number : ["Page 1"];
    }
    const samples = {
      field_name: ["AL Central Field List - 4/25/2026"],
      meeting_name: ["Birmingham (GRAHAM)"],
      page_number: ["Page 1"],
    };
    return samples[type] || ["Preview value"];
  };

  const formatTodayMDYyyy = () => {
    const today = new Date();
    return `${today.getMonth() + 1}/${today.getDate()}/${today.getFullYear()}`;
  };

  const fontStyleDeclarations = (prefix, options = {}) => {
    const bold = options.forceBold || Boolean(settings[`${prefix}Bold`]);
    const italic = Boolean(settings[`${prefix}Italic`]);
    const underline = Boolean(settings[`${prefix}Underline`]);
    return [
      `font-weight: ${bold ? 800 : 500}`,
      `font-style: ${italic ? "italic" : "normal"}`,
      `text-decoration: ${underline ? "underline" : "none"}`,
    ].join("; ");
  };

  const renderTitleClusterHtml = () => {
    if (usesManualPalettePlacement()) return "";
    const selectedField = getSelectedField();
    const fieldName = selectedField?.name || getSelectedContact()?.field_name || "Field";
    const align = settings.titleAlign === "left" ? "left" : "center";
    return `
      <div
        class="field-list-title-preview align-${align}"
        style="font-family: ${escapeHtml(settings.titleFontFamily || settings.fontFamily || "Arial")}; ${fontStyleDeclarations("titleFont", { forceBold: true })};"
      >${escapeHtml(fieldName)} Field List - ${formatTodayMDYyyy()}</div>
    `;
  };

  const renderMeetingNameHtml = () => {
    if (!isAlphabeticalMode() && selectedFieldName && !selectedMeetingName) return "";
    const meetingName = getSelectedMeeting()?.name || getSelectedContact()?.meeting_name || "Meeting";
    const align = settings.meetingNameAlign === "left" ? "left" : "center";
    const meetingTitle = usesManualPalettePlacement() ? `${meetingName} - ${formatTodayMDYyyy()}` : meetingName;
    if (usesManualPalettePlacement()) {
      return `
        <div
          class="field-list-duplicate-meeting-name-preview"
          style="--field-list-column-gap-in: ${getEffectiveTwoColumnGapIn()}in; --field-list-column-gap-ch: ${getEffectiveTwoColumnGapCh()}ch; --field-list-column-width-in: ${getEffectiveColumnWidthIn()}in; font-family: ${escapeHtml(settings.meetingNameFontFamily || settings.fontFamily || "Arial")}; ${fontStyleDeclarations("meetingNameFont", { forceBold: true })};"
        >
          <div class="align-${align}">${escapeHtml(meetingTitle)}</div>
          <div class="align-${align}">${escapeHtml(meetingTitle)}</div>
        </div>
      `;
    }
    return `
      <div
        class="field-list-meeting-name-preview align-${align}"
        style="font-family: ${escapeHtml(settings.meetingNameFontFamily || settings.fontFamily || "Arial")}; ${fontStyleDeclarations("meetingNameFont", { forceBold: true })};"
      >${escapeHtml(meetingTitle)}</div>
    `;
  };

  const formatNameSuffixForParts = (children, otherRelationships) => {
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
  };

  const contactNameHead = (contact, textOverride = null) => {
    const text = String(textOverride ?? contact?.name ?? contact?.label ?? "CONTACT, Name").trim() || "CONTACT, Name";
    const semicolonIndex = text.indexOf("; ");
    if (semicolonIndex >= 0) return text.slice(0, semicolonIndex);
    const suffix = formatNameSuffixForParts(contact?.children, contact?.other_relationships);
    if (suffix && text.endsWith(suffix)) return text.slice(0, -suffix.length);
    return text;
  };

  const relationshipSuffixTypedSegments = (contact) => {
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
    const text = String(contact?.name || contact?.label || "").trim();
    const semicolonIndex = text.indexOf("; ");
    if (semicolonIndex < 0) return segments;
    const suffix = text.slice(semicolonIndex + 2);
    let remaining = suffix;
    while (remaining) {
      const labelMatch = remaining.match(/^([^:,]+):\s*/);
      if (labelMatch) {
        const label = labelMatch[1].trim();
        remaining = remaining.slice(labelMatch[0].length);
        const nextLabel = remaining.search(/,\s*(?:[^:,]+):\s*/);
        const name = (nextLabel >= 0 ? remaining.slice(0, nextLabel) : remaining).trim();
        remaining = nextLabel >= 0 ? remaining.slice(nextLabel + 1).trim() : "";
        segments.push({ kind: "labeled", text: `${label}: ${name}` });
        continue;
      }
      const nextLabel = remaining.search(/,\s*(?:[^:,]+):\s*/);
      if (nextLabel >= 0) {
        const child = remaining.slice(0, nextLabel).trim().replace(/,$/, "");
        if (child) segments.push({ kind: "child", text: child });
        remaining = remaining.slice(nextLabel + 1).trim();
        continue;
      }
      if (remaining) segments.push({ kind: "child", text: remaining.trim() });
      remaining = "";
    }
    return segments;
  };

  const relationshipSuffixSegments = (contact) => {
    return relationshipSuffixTypedSegments(contact).map((segment) => segment.text);
  };

  const renderRelationshipSuffixHtml = (contact, suffixText = "") => {
    const text = String(suffixText || "").trim();
    if (text) {
      const colonIndex = text.indexOf(": ");
      if (colonIndex >= 0) {
        const label = text.slice(0, colonIndex);
        const name = text.slice(colonIndex + 2);
        return `<span class="field-list-relationship-pair">${escapeHtml(label)}: ${escapeHtml(name)}</span>`;
      }
      return `<span class="field-list-relationship-child">${escapeHtml(text)}</span>`;
    }
    const segmentsFromContact = [];
    (contact?.children || []).forEach((child) => {
      const name = String(child || "").trim();
      if (name) segmentsFromContact.push(`<span class="field-list-relationship-child">${escapeHtml(name)}</span>`);
    });
    (contact?.other_relationships || []).forEach((relation) => {
      const label = String(relation?.relation_type || "").trim();
      const name = String(relation?.relation_value || "").trim();
      if (!name) return;
      if (label) {
        segmentsFromContact.push(`<span class="field-list-relationship-pair">${escapeHtml(label)}: ${escapeHtml(name)}</span>`);
      } else {
        segmentsFromContact.push(escapeHtml(name));
      }
    });
    if (segmentsFromContact.length) return segmentsFromContact.join(", ");
    return relationshipSuffixSegments(contact).map((segment) => {
      const colonIndex = segment.indexOf(": ");
      if (colonIndex >= 0) {
        const label = segment.slice(0, colonIndex);
        const name = segment.slice(colonIndex + 2);
        return `<span class="field-list-relationship-pair">${escapeHtml(label)}: ${escapeHtml(name)}</span>`;
      }
      return `<span class="field-list-relationship-child">${escapeHtml(segment)}</span>`;
    }).join(", ");
  };

  const renderContactNameHtml = (name, contact = null) => {
    const value = String(name || "CONTACT, Name");
    const lines = wrapContactNameLine(value, contact);
    if (lines.length > 1) {
      return lines
        .map((line, index) => {
          const html = index === 0
            ? renderContactNameLineHtml(line, true, contact)
            : `<span class="field-list-contact-name-continuation">${renderRelationshipSuffixHtml(contact, line.replace(/^;\s*/, "").replace(/^ /, ""))}</span>`;
          const leadingSpace = index > 0 && line.startsWith(" ") ? " " : "";
          const indentStyle = index > 0 ? ` style="padding-left: ${index}ch;"` : "";
          return `<div class="field-list-contact-name-line${index > 0 ? " is-indented" : ""}"${indentStyle}>${leadingSpace}${html}</div>`;
        })
        .join("");
    }
    return renderContactNameLineHtml(value, true, contact);
  };

  const renderContactNameLineHtml = (name, canBoldLastName, contact = null) => {
    const value = String(name || "CONTACT, Name");
    const head = contactNameHead(contact, value);
    const suffix = value.startsWith(head) ? value.slice(head.length) : "";
    if (suffix.startsWith("; ")) {
      const headHtml = renderContactNameLineHtml(head, canBoldLastName, contact);
      return `${headHtml}; ${renderRelationshipSuffixHtml(contact, suffix.slice(2))}`;
    }
    if (suffix.startsWith(", ")) {
      const headHtml = renderContactNameLineHtml(head, canBoldLastName, contact);
      return `${headHtml}, ${renderRelationshipSuffixHtml(contact, suffix.slice(2))}`;
    }
    const semicolonIndex = value.indexOf("; ");
    if (semicolonIndex >= 0) {
      const legacyHead = value.slice(0, semicolonIndex);
      const suffixText = value.slice(semicolonIndex + 2);
      const headHtml = renderContactNameLineHtml(legacyHead, canBoldLastName, contact);
      return `${headHtml}; ${renderRelationshipSuffixHtml(contact, suffixText)}`;
    }
    const commaIndex = value.indexOf(",");
    if (!canBoldLastName || commaIndex < 0) return escapeHtml(value);
    const lastName = value.slice(0, commaIndex);
    const rest = value.slice(commaIndex);
    return `<span class="field-list-contact-last-name">${escapeHtml(lastName)}</span>${escapeHtml(rest)}`;
  };

  const measureTextWidth = (text, style = "normal") => {
    const fontSizePt = Number(settings.baseFontSizePt || 10);
    const fontSizePx = (fontSizePt * 96) / 72;
    const fontFamily = settings.fontFamily || "Arial";
    if (!textMeasureContext) return String(text || "").length * fontSizePx * 0.58;
    textMeasureContext.font = `${style} ${fontSizePx}px ${fontFamily}`;
    return textMeasureContext.measureText(text).width;
  };

  const getTextColumnWidthPx = (contact = null) => {
    const fontSizePt = Number(settings.baseFontSizePt || 10);
    const fontSizePx = (fontSizePt * 96) / 72;
    const columnWidthPx = getEffectiveColumnWidthIn() * 96;
    // Match CSS/PDF: number + space + 2-letter code + space before the name column.
    const fixedPhoneColumnPx =
      measureTextWidth("000-000-0000", "normal") +
      measureTextWidth(" ", "normal") +
      measureTextWidth("MM", "normal");
    const gridGapPx = measureTextWidth(" ", "normal");
    return Math.max(120, columnWidthPx - fixedPhoneColumnPx - gridGapPx);
  };

  const textFitsWidth = (text, widthPx, style = "normal") => {
    return measureTextWidth(text, style) <= widthPx;
  };

  const wrapFieldListNameWords = (text, widthPx) => {
    const value = String(text || "").trim();
    if (!value) return [];
    if (textFitsWidth(value, widthPx, "normal")) return [value];
    const words = value.split(/\s+/).filter(Boolean);
    if (words.length <= 1) return [value];
    const lines = [];
    let current = words[0];
    words.slice(1).forEach((word) => {
      const candidate = `${current} ${word}`;
      if (textFitsWidth(candidate, widthPx, "normal")) current = candidate;
      else {
        lines.push(current);
        current = word;
      }
    });
    if (current) lines.push(current);
    return lines;
  };

  const wrapRelationshipText = (text, widthPx) => {
    const value = String(text || "").trim();
    if (!value) return [];
    if (textFitsWidth(value, widthPx, "normal")) return [value];
    const commaParts = value.split(",").map((part) => part.trim()).filter(Boolean);
    if (commaParts.length > 1) {
      const lines = [];
      let current = "";
      commaParts.forEach((part) => {
        const candidate = current ? `${current}, ${part}` : part;
        if (current && !textFitsWidth(candidate, widthPx, "normal")) {
          lines.push(current.endsWith(",") ? current : `${current},`);
          if (!textFitsWidth(part, widthPx, "normal")) {
            lines.push(...wrapFieldListNameWords(part, widthPx));
            current = "";
          } else {
            current = part;
          }
        } else if (!current && !textFitsWidth(part, widthPx, "normal")) {
          lines.push(...wrapFieldListNameWords(part, widthPx));
          current = "";
        } else {
          current = candidate;
        }
      });
      if (current) lines.push(current);
      if (lines.length) return lines;
    }
    return wrapFieldListNameWords(value, widthPx);
  };

  const packTypedSuffixLines = (typedSegments, widthPx, { semicolonOnPreviousLine = false } = {}) => {
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

      if (current && !textFitsWidth(piece, widthPx, "normal")) {
        isFirstSuffixLine = false;
        let nextText;
        if (kind === "child") {
          lines.push(current.endsWith(",") ? current : `${current},`);
          nextText = text;
        } else {
          lines.push(current);
          nextText = ` ${text}`;
        }
        if (!textFitsWidth(nextText, widthPx, "normal")) {
          lines.push(...wrapRelationshipText(nextText.trimStart(), widthPx));
          current = "";
        } else {
          current = nextText;
        }
      } else if (!current) {
        if (!textFitsWidth(piece, widthPx, "normal")) {
          lines.push(...wrapRelationshipText(piece.trimStart(), widthPx));
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
      if (!textFitsWidth(current, widthPx, "normal")) {
        lines.push(...wrapRelationshipText(current.trimStart(), widthPx));
      } else {
        lines.push(current);
      }
    }
    return lines;
  };

  const wrapContactNameLine = (name, contact = null) => {
    const typedSegments = relationshipSuffixTypedSegments(contact);
    const head = contactNameHead(contact, String(name || "").trim());
    const widthPx = getTextColumnWidthPx(contact);
    if (!typedSegments.length) {
      const value = String(name || "").trim();
      if (textFitsWidth(value, widthPx, "bold")) return [value];
      return [value];
    }
    const suffix = formatNameSuffixForParts(contact?.children, contact?.other_relationships);
    const full = `${head}${suffix}`;
    if (textFitsWidth(full, widthPx, "bold")) return [full];

    let headLine = head;
    let semicolonOnPreviousLine = false;
    if (typedSegments.length && typedSegments[0].kind === "child") {
      const headWithSemi = `${head};`;
      if (textFitsWidth(headWithSemi, widthPx, "bold")) {
        headLine = headWithSemi;
        semicolonOnPreviousLine = true;
      }
    }

    return [
      headLine,
      ...packTypedSuffixLines(typedSegments, widthPx, { semicolonOnPreviousLine }),
    ];
  };

  const getPrintBookNoteValues = (contact) => (
    (contact?.print_book_name || []).map((item) => String(item || "").trim()).filter(Boolean)
  );

  const getPrintAfterAddressValues = (contact) => (
    (contact?.print_after_address || []).map((item) => String(item || "").trim()).filter(Boolean)
  );

  const formatPrintBookNoteText = (values) => values.map((value) => `(${printNotePlainText(value)})`).join(" ");
  const formatPrintBookNoteHtml = (values) => values.map((value) => `(${sanitizePrintNoteHtml(value)})`).join(" ");

  const resolvePrintBookNotePlacement = (contact, nameValue) => {
    const pbText = formatPrintBookNoteText(getPrintBookNoteValues(contact));
    const pbHtml = formatPrintBookNoteHtml(getPrintBookNoteValues(contact));
    if (!pbText) return { inlineHtml: "", ownLineHtml: "" };
    const firstNameLine = wrapContactNameLine(nameValue, contact)[0] || nameValue;
    const textWidthPx = getTextColumnWidthPx(contact);
    const fitsOnNameLine = textFitsWidth(`${firstNameLine} ${pbText}`, textWidthPx, "normal");
    if (fitsOnNameLine) {
      return {
        inlineHtml: `<span class="field-list-print-book-note">&nbsp;${pbHtml}</span>`,
        ownLineHtml: "",
      };
    }
    return {
      inlineHtml: "",
      ownLineHtml: `<div class="field-list-print-book-note field-list-print-note-line">${pbHtml}</div>`,
    };
  };

  const renderPrintAfterAddressHtml = (contact) => {
    const values = getPrintAfterAddressValues(contact);
    if (!values.length) return "";
    return values
      .map((value) => `<div class="field-list-print-after-note field-list-print-note-line">${sanitizePrintNoteHtml(value)}</div>`)
      .join("");
  };

  const renderContactClusterHtml = (contact) => {
    if (!contact) return `<div class="field-list-cluster-empty">Choose a field, meeting, and contact first.</div>`;
    const phoneRows = contact.phones || [];
    const addressAlign = getAddressAlign();
    const nameValue = String(contact.name || contact.label || "CONTACT, Name");
    const printBookPlacement = resolvePrintBookNotePlacement(contact, nameValue);
    const printAfterHtml = renderPrintAfterAddressHtml(contact);
    const compactAddressLines = getCompactAddressLines(contact);
    const addressLines = getContactAddressLines(contact);
    const phoneLineHtml = (phone, index = 0) => `
      <div class="field-list-phone-line ${index === 0 ? "is-primary-phone" : "is-secondary-phone"}">
        <span class="field-list-phone-value">${escapeHtml(phone?.value || "")}</span>
        <span class="field-list-phone-code">${escapeHtml(phone?.code || "")}</span>
      </div>
    `;
    const compactPhoneStackHtml = () => {
      const rows = phoneRows.length ? phoneRows : [{}];
      if (!isCompactListMode() || rows.length < 3) return rows.map((phone, index) => phoneLineHtml(phone, index)).join("");
      return [
        phoneLineHtml(rows[0], 0),
        `<div class="field-list-phone-line is-secondary-phone is-combined-phone-line">
          ${rows.slice(1).map((phone, index) => `
            ${index === 1 ? `<span class="field-list-phone-continuation-gap"></span>` : index > 1 ? `<span class="field-list-phone-comma">,</span>` : ""}
            <span class="field-list-phone-pair">
              <span class="field-list-phone-value">${escapeHtml(phone?.value || "")}</span>
              <span class="field-list-phone-code">${escapeHtml(phone?.code || "")}</span>
            </span>
          `).join("")}
        </div>`,
      ].join("");
    };
    const compactAddressPlacement = () => {
      if (!isCompactListMode() || !compactAddressLines.length) return { inlineHtml: "", blockHtml: "", phoneBlockHtml: "" };
      const textWidthPx = getTextColumnWidthPx(contact);
      const firstAddress = compactAddressLines[0];
      const additionalAddressLines = compactAddressLines.slice(1);
      const hasManyPhones = phoneRows.length >= 3;
      const placeAdditionalWithPhones = hasManyPhones && additionalAddressLines.length > 0;
      const additionalBlock = additionalAddressLines
        .map((line) => `<div class="field-list-contact-extra-address">${escapeHtml(line)}</div>`)
        .join("");
      const inlineWidthPx = measureTextWidth(nameValue, "bold") + measureTextWidth(`, ${firstAddress}`, "italic");
      const inlineFits = inlineWidthPx <= textWidthPx;
      if (!inlineFits) {
        const allAddressBlock = compactAddressLines
          .map((line) => `<div class="field-list-contact-extra-address">${escapeHtml(line)}</div>`)
          .join("");
        return {
          inlineHtml: "",
          blockHtml: hasManyPhones ? "" : allAddressBlock,
          phoneBlockHtml: hasManyPhones ? allAddressBlock : "",
        };
      }
      return {
        inlineHtml: `<span class="field-list-contact-inline-address-group"><span class="field-list-contact-inline-separator">,</span><span class="field-list-contact-inline-address">${escapeHtml(firstAddress)}</span></span>`,
        blockHtml: placeAdditionalWithPhones ? "" : additionalBlock,
        phoneBlockHtml: placeAdditionalWithPhones ? additionalBlock : "",
      };
    };
    const compactAddress = compactAddressPlacement();
    const standardAddressHtml = !isCompactListMode() && (addressLines.length || printAfterHtml)
      ? `<div class="field-list-contact-address">${addressLines.map((line) => `<div>${escapeHtml(line)}</div>`).join("")}${printAfterHtml}</div>`
      : "";
    const compactNoteHtml = isCompactListMode()
      ? `${printBookPlacement.ownLineHtml}${printAfterHtml}`
      : printBookPlacement.ownLineHtml;
    return `
      <div class="field-list-contact-row address-${addressAlign}${isCompactListMode() ? " is-compact-address" : ""}" style="--field-list-column-width-in: ${getEffectiveColumnWidthIn()}in;">
        <div class="field-list-phone-stack">
          ${compactPhoneStackHtml()}
          ${compactAddress.phoneBlockHtml ? `<div class="field-list-phone-address-continuation">${compactAddress.phoneBlockHtml}</div>` : ""}
        </div>
        <div class="field-list-contact-main">
          <div class="field-list-contact-name">${renderContactNameHtml(nameValue, contact)}${printBookPlacement.inlineHtml}${compactAddress.inlineHtml}</div>
          ${compactNoteHtml}
          ${compactAddress.blockHtml ? `<div class="field-list-contact-compact-address-block">${compactAddress.blockHtml}</div>` : ""}
          ${standardAddressHtml}
        </div>
      </div>
    `;
  };

  const splitFieldListTypePrefix = (text) => {
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
  };

  const splitFieldListAddressParts = (address) => {
    const entry = (address && typeof address === "object") ? address : { text: address };
    const parsedPrefix = splitFieldListTypePrefix(String(entry.text || address || "").trim());
    let addressLabel = String(entry.address_type || "").trim();
    if (!addressLabel) addressLabel = parsedPrefix.label;
    if (addressLabel.toLowerCase() === "home") addressLabel = "";
    let street1 = String(entry.street_address || "").trim();
    const street1Prefix = splitFieldListTypePrefix(street1);
    if (street1Prefix.label && (!addressLabel || street1Prefix.label.toLowerCase() === addressLabel.toLowerCase())) {
      street1 = street1Prefix.remainder;
    }
    let street2 = String(entry.extended_address || "").trim();
    let city = "";
    if (entry.city && entry.region && entry.postal_code) {
      city = `${String(entry.city).trim()}, ${String(entry.region).trim()} ${String(entry.postal_code).trim()}`;
    }
    const text = parsedPrefix.remainder;
    if (!street1 && !street2) {
      const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      const cityLineRe = /^(.+?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$/;
      let street = "";
      if (lines.length >= 2 && cityLineRe.test(lines[lines.length - 1])) {
        street = lines.slice(0, -1).join(", ");
        city = city || lines[lines.length - 1];
      } else {
        const oneLine = lines.join(", ");
        const match = oneLine.match(/^(.*?),\s*([^,]+,\s*[A-Za-z]{2}\s+\d{5}(?:-\d{4})?)\s*$/);
        if (match) {
          street = match[1].trim();
          city = city || match[2].trim();
        } else {
          street = oneLine;
        }
      }
      const comma = street.indexOf(",");
      if (comma > 0) {
        street1 = street.slice(0, comma).trim();
        street2 = street.slice(comma + 1).trim();
      } else {
        street1 = street;
      }
    }
    return { street1, street2, city, text, addressLabel };
  };

  const formatFieldListAddressOneLine = (address) => {
    const { street1, street2, city, text, addressLabel } = splitFieldListAddressParts(address);
    const prefix = addressLabel ? `${addressLabel}: ` : "";
    const street = [street1, street2].filter(Boolean).join(", ");
    if (street && city) return `${prefix}${street}, ${city}`;
    const fallback = String(text || address || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .join(", ");
    return prefix && fallback && !fallback.toLowerCase().startsWith(prefix.toLowerCase())
      ? `${prefix}${fallback}`
      : fallback;
  };

  const isFieldListUnitOrAptPart = (part) => (
    /^(?:#\S+|(?:apt|apartment|suite|ste|unit)\b.*)$/i.test(String(part || "").trim())
  );

  const wrapFieldListAddressWords = (text, widthPx) => {
    const value = String(text || "").trim();
    if (!value) return [];
    if (textFitsWidth(value, widthPx, "normal")) return [value];
    const words = value.split(/\s+/).filter(Boolean);
    if (words.length <= 1) return [value];
    const lines = [];
    let current = words[0];
    words.slice(1).forEach((word) => {
      const candidate = `${current} ${word}`;
      if (textFitsWidth(candidate, widthPx, "normal")) current = candidate;
      else {
        lines.push(current);
        current = word;
      }
    });
    if (current) lines.push(current);
    return lines;
  };

  const wrapFieldListAddressText = (text, widthPx) => {
    const value = String(text || "").trim();
    if (!value) return [];
    if (textFitsWidth(value, widthPx, "normal")) return [value];
    const parts = value.split(",").map((part) => part.trim()).filter(Boolean);
    if (parts.length > 1) {
      const lines = [];
      let current = parts[0];
      parts.slice(1).forEach((part) => {
        const candidate = `${current}, ${part}`;
        if (textFitsWidth(candidate, widthPx, "normal") || isFieldListUnitOrAptPart(part)) {
          current = candidate;
        } else {
          lines.push(`${current},`);
          current = part;
        }
      });
      if (current) lines.push(current);
      return lines.flatMap((line) => (
        textFitsWidth(line, widthPx, "normal")
          ? [line]
          : wrapFieldListAddressWords(line.replace(/,$/, "").trim(), widthPx)
      ));
    }
    return wrapFieldListAddressWords(value, widthPx);
  };

  const formatFieldListAddressLines = (address, contact = null) => {
    const { street1, street2, city, text, addressLabel } = splitFieldListAddressParts(address);
    const prefix = addressLabel ? `${addressLabel}: ` : "";
    const street2Parts = String(street2 || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const streetParts = [street1, ...street2Parts].filter(Boolean);
    const widthPx = getTextColumnWidthPx(contact);
    const withPrefix = (line) => {
      if (!prefix || !line) return line;
      if (String(line).toLowerCase().startsWith(prefix.toLowerCase())) return line;
      return `${prefix}${line}`;
    };
    const fitsFirst = (line) => textFitsWidth(withPrefix(line), widthPx, "normal");
    const applyPrefix = (lines) => (prefix && lines.length ? [withPrefix(lines[0]), ...lines.slice(1)] : lines);
    const withCity = (lines) => {
      if (!city) return lines;
      if (!lines.length) return wrapFieldListAddressText(city, widthPx);
      const combined = `${lines[lines.length - 1]}, ${city}`;
      if (textFitsWidth(combined, widthPx, "normal")) {
        return [...lines.slice(0, -1), combined];
      }
      return [...lines, city];
    };
    if (!streetParts.length && !city) {
      const fallback = String(text || address || "").trim();
      return fallback ? applyPrefix(wrapFieldListAddressText(fallback, widthPx)) : [];
    }
    if (!streetParts.length) return applyPrefix(wrapFieldListAddressText(city, widthPx));
    if (street1 && street2Parts.length) {
      return applyPrefix(withCity([
        ...wrapFieldListAddressText(street1, widthPx),
        ...street2Parts.flatMap((part) => wrapFieldListAddressText(part, widthPx) || [part]),
      ]));
    }
    const street = streetParts.join(", ");
    if (!city) return applyPrefix(wrapFieldListAddressText(street, widthPx));
    const oneLine = `${street}, ${city}`;
    if (fitsFirst(oneLine)) return [withPrefix(oneLine)];
    if (fitsFirst(street)) return [withPrefix(street), city];
    return applyPrefix(withCity(wrapFieldListAddressText(street, widthPx)));
  };

  const getContactAddressSource = (contact) => (
    (Array.isArray(contact?.address_entries) && contact.address_entries.length)
      ? contact.address_entries
      : (contact?.addresses || [])
  );

  const getContactAddressLines = (contact) => getContactAddressSource(contact)
    .flatMap((address) => formatFieldListAddressLines(address, contact))
    .filter(Boolean);

  const getCompactAddressLines = (contact) => getContactAddressLines(contact);

  const estimateContactLineCount = (contact) => {
    const phoneCount = Math.max(1, (contact?.phones || []).length);
    const nameValue = String(contact?.name || contact?.label || "").trim();
    const nameLineCount = Math.max(1, wrapContactNameLine(nameValue || "CONTACT, Name", contact).length);
    const addressLineCount = getContactAddressLines(contact).length;
    const printBookOwnLine = resolvePrintBookNotePlacement(contact, nameValue || "CONTACT, Name").ownLineHtml ? 1 : 0;
    const printAfterLineCount = getPrintAfterAddressValues(contact).length;
    const textCount = nameLineCount + printBookOwnLine + addressLineCount + printAfterLineCount;
    return Math.max(phoneCount, textCount);
  };

  const countHtmlLines = (value) => Math.max(1, String(value || "").split(/<br\s*\/?>|\r?\n/i).length);

  const flowColumnSegments = (column) => {
    const segments = column?.segments;
    if (Array.isArray(segments) && segments.length) return segments;
    return [{ html: column?.html, align: column?.align }];
  };

  const flowSegmentAlignClass = (align) => (
    align === "right" ? "align-right" : align === "center" || align === "auto" ? "align-center" : "align-left"
  );

  const flowBandLineCount = (columns) => Math.max(
    0,
    ...(columns || []).map((column) => {
      const segments = flowColumnSegments(column);
      const total = segments.reduce((sum, segment) => sum + countHtmlLines(segment.html), 0);
      return total || 1;
    }),
  );

  const flowRowPrintLineCount = (row) => {
    if (row.flow_row_line_count) return Math.max(1, Number(row.flow_row_line_count));
    const primaries = row.columns || [];
    const stacked = row.stacked_columns || [];
    const primaryCounts = Object.fromEntries(
      primaries.map((column) => [Number(column.cell_id || 0), flowBandLineCount([column])]),
    );
    let extent = Math.max(1, ...Object.values(primaryCounts).filter(Boolean));
    stacked.forEach((column) => {
      const parentLines = primaryCounts[Number(column.parent_cell_id || 0)] || 1;
      const lines = flowBandLineCount([column]);
      extent = Math.max(extent, parentLines + lines);
    });
    return extent;
  };

  const renderFlowGridColumnHtml = (column) => {
    const colStart = Math.max(1, Math.min(28, Number(column.col_start || 1)));
    const colSpan = Math.max(1, Math.min(28, Number(column.col_span || 1)));
    const rowStart = Math.max(1, Number(column.grid_row_start || 1));
    const rowSpan = Math.max(1, Number(column.grid_row_span || 1));
    const segments = flowColumnSegments(column);
    const segmentHtml = segments.map((segment, index) => {
      if (!String(segment.html || "").trim()) return "";
      const alignClass = flowSegmentAlignClass(segment.align);
      const stackedClass = index > 0 ? " is-stacked-segment" : "";
      return `<div class="field-list-meeting-preview-flow-text ${alignClass}${stackedClass}">${sanitizeMeetingHtml(segment.html)}</div>`;
    }).join("");
    if (!segmentHtml) return "";
    return `
      <div class="field-list-meeting-preview-cell field-list-meeting-preview-flow-column" style="grid-column: ${colStart} / span ${colSpan}; grid-row: ${rowStart} / span ${rowSpan};">
        ${segmentHtml}
      </div>
    `;
  };

  const renderFlowGridHtml = (row) => {
    const rowCount = flowRowPrintLineCount(row);
    const cells = [...(row.columns || []), ...(row.stacked_columns || [])]
      .map(renderFlowGridColumnHtml)
      .filter(Boolean)
      .join("");
    if (!cells) return "";
    return `<div class="field-list-meeting-preview-flow-grid" style="grid-template-rows: repeat(${rowCount}, auto);">${cells}</div>`;
  };

  const renderFlowBandHtml = (columns, bandClass = "") => {
    const cells = (columns || []).map((column) => {
      const colStart = Math.max(1, Math.min(28, Number(column.col_start || 1)));
      const colSpan = Math.max(1, Math.min(28, Number(column.col_span || 1)));
      return `
        <div class="field-list-meeting-preview-cell field-list-meeting-preview-flow-column" style="grid-column: ${colStart} / span ${colSpan};">
          ${flowColumnSegments(column).map((segment, index) => {
            const alignClass = flowSegmentAlignClass(segment.align);
            const stackedClass = index > 0 ? " is-stacked-segment" : "";
            return `<div class="field-list-meeting-preview-flow-text ${alignClass}${stackedClass}">${sanitizeMeetingHtml(segment.html)}</div>`;
          }).join("")}
        </div>
      `;
    }).join("");
    if (!cells) return "";
    return `<div class="field-list-meeting-preview-flow-band ${bandClass}">${cells}</div>`;
  };

  const estimateMeetingPreviewRowLineCount = (row) => {
    if (row.kind === "blank" || row.kind === "divider") return 1;
    if (row.kind === "flow") {
      const primaryLines = Math.max(1, flowBandLineCount(row.columns || []));
      const stacked = row.stacked_columns || [];
      if (!stacked.length) return primaryLines;
      const primaryCounts = Object.fromEntries(
        (row.columns || []).map((column) => [Number(column.cell_id || 0), flowBandLineCount([column])]),
      );
      let extent = primaryLines;
      stacked.forEach((column) => {
        const parentLines = primaryCounts[Number(column.parent_cell_id || 0)] || 1;
        const lines = flowBandLineCount([column]);
        extent = Math.max(extent, parentLines + lines);
      });
      return extent;
    }
    const cells = row.cells || [];
    return Math.max(1, ...cells.map((cell) => countHtmlLines(cell.html)));
  };

  const estimateBibleStudyLineCount = (meetingName = selectedMeetingName) => findMeetingPreviewRows(meetingName)
    .reduce((total, row) => total + estimateMeetingPreviewRowLineCount(row), 0);

  const findBalancedSplitIndex = (contacts, appendedLineCount) => {
    if (contacts.length <= 1) return contacts.length;
    const lineCounts = contacts.map(estimateContactLineCount);
    const totalLines = lineCounts.reduce((sum, count) => sum + count, 0);
    let bestIndex = Math.ceil(contacts.length / 2);
    let bestDelta = Number.POSITIVE_INFINITY;
    let bestTallestColumn = Number.POSITIVE_INFINITY;
    const lastIndex = appendedLineCount ? contacts.length : contacts.length - 1;

    for (let index = 1; index <= lastIndex; index++) {
      const leftLines = lineCounts.slice(0, index).reduce((sum, count) => sum + count, 0);
      const rightLines = totalLines - leftLines + appendedLineCount;
      const delta = Math.abs(leftLines - rightLines);
      const tallestColumn = Math.max(leftLines, rightLines);
      if (
        delta < bestDelta
        || (delta === bestDelta && tallestColumn < bestTallestColumn)
        || (appendedLineCount && delta === bestDelta && tallestColumn === bestTallestColumn && index > bestIndex)
      ) {
        bestDelta = delta;
        bestTallestColumn = tallestColumn;
        bestIndex = index;
      }
    }
    return bestIndex;
  };

  const renderMeetingClusterHtml = () => {
    if (isAlphabeticalMode()) return renderFieldAlphabeticalContactsHtml();
    if (getPageColumnCount() === 2 && !usesManualPalettePlacement()) return renderTwoColumnFieldMeetingsPreviewHtml();
    const contacts = getSelectedMeetingContacts();
    if (selectedFieldName && !selectedMeetingName) {
      return `<div class="field-list-cluster-empty">Choose a meeting to see meetings</div>`;
    }
    if (!selectedFieldName || !selectedMeetingName) {
      return `<div class="field-list-cluster-empty">Choose a field and meeting first.</div>`;
    }
    if (!contacts.length) return `<div class="field-list-cluster-empty">No contacts found for this meeting.</div>`;
    const bibleHtml = shouldIncludeBibleStudyUnionInfo() ? renderBibleStudyClusterHtml() : "";
    if (usesManualPalettePlacement()) {
      return `
        <div class="field-list-meeting-contacts field-list-meeting-contacts-2 ${shouldShowVerticalSeparatorLines() ? "has-vertical-separator-lines" : ""}" style="--field-list-column-gap-in: ${getEffectiveTwoColumnGapIn()}in; --field-list-column-gap-ch: ${getEffectiveTwoColumnGapCh()}ch; --field-list-column-width-in: ${getEffectiveColumnWidthIn()}in;">
          ${[0, 1].map(() => `
            <div class="field-list-meeting-contact-column">
              ${contacts.map(renderContactClusterHtml).join("")}
              ${bibleHtml ? `<div class="field-list-meeting-bible-append field-list-bible-cluster ${getBibleStudyUnionAlignClass()}" ${bibleStudyStyleAttr()}>${bibleHtml}</div>` : ""}
            </div>
          `).join("")}
        </div>
      `;
    }
    const columnCount = getPageColumnCount();
    const bibleLineCount = bibleHtml ? estimateBibleStudyLineCount() + 1 : 0;
    const midpoint = columnCount === 2
      ? findBalancedSplitIndex(contacts, bibleLineCount)
      : contacts.length;
    const columns = columnCount === 2
      ? [contacts.slice(0, midpoint), contacts.slice(midpoint)]
      : [contacts];
    const contactsHtml = `
      <div class="field-list-meeting-contacts field-list-meeting-contacts-${columnCount} ${columnCount === 2 && shouldShowVerticalSeparatorLines() ? "has-vertical-separator-lines" : ""}" style="--field-list-column-gap-in: ${getEffectiveTwoColumnGapIn()}in; --field-list-column-gap-ch: ${getEffectiveTwoColumnGapCh()}ch; --field-list-column-width-in: ${getEffectiveColumnWidthIn()}in;">
        ${columns.map((column, index) => `
          <div class="field-list-meeting-contact-column">
            ${column.map(renderContactClusterHtml).join("")}
            ${bibleHtml && (columnCount === 1 || index === 1) ? `<div class="field-list-meeting-bible-append field-list-bible-cluster ${getBibleStudyUnionAlignClass()}" ${bibleStudyStyleAttr()}>${bibleHtml}</div>` : ""}
          </div>
        `).join("")}
      </div>
      ${renderSeparatorLine()}
    `;
    return contactsHtml;
  };

  const renderTwoColumnFieldMeetingsPreviewHtml = () => {
    const field = getSelectedField();
    const meetings = (field?.meetings || []).filter((meeting) => (meeting.contacts || []).length);
    if (selectedFieldName && !selectedMeetingName) {
      return `<div class="field-list-cluster-empty">Choose a meeting to see meetings</div>`;
    }
    if (!selectedFieldName || !meetings.length) {
      return `<div class="field-list-cluster-empty">Choose a field with meetings first.</div>`;
    }
    const selectedIndex = Math.max(0, meetings.findIndex((meeting) => meeting.name === selectedMeetingName));
    const orderedMeetings = [
      ...meetings.slice(selectedIndex),
      ...meetings.slice(0, selectedIndex),
    ];
    return orderedMeetings.map((meeting, index) => {
      const contacts = sortMeetingContacts(meeting.contacts || []);
      const bibleHtml = shouldIncludeBibleStudyUnionInfo() ? renderBibleStudyClusterHtml(meeting.name) : "";
      const bibleLineCount = bibleHtml ? estimateBibleStudyLineCount(meeting.name) + 1 : 0;
      const midpoint = findBalancedSplitIndex(contacts, bibleLineCount);
      const columns = [contacts.slice(0, midpoint), contacts.slice(midpoint)];
      const meetingName = escapeHtml(meeting.name || "Meeting");
      return `
        <section class="field-list-preview-meeting-sample">
          ${index === 0 ? "" : `<div class="field-list-preview-meeting-sample-title">${meetingName}</div>`}
          <div class="field-list-meeting-contacts field-list-meeting-contacts-2 ${shouldShowVerticalSeparatorLines() ? "has-vertical-separator-lines" : ""}" style="--field-list-column-gap-in: ${getEffectiveTwoColumnGapIn()}in; --field-list-column-gap-ch: ${getEffectiveTwoColumnGapCh()}ch; --field-list-column-width-in: ${getEffectiveColumnWidthIn()}in;">
            ${columns.map((column, columnIndex) => `
              <div class="field-list-meeting-contact-column">
                ${column.map(renderContactClusterHtml).join("")}
                ${bibleHtml && columnIndex === 1 ? `<div class="field-list-meeting-bible-append field-list-bible-cluster ${getBibleStudyUnionAlignClass()}" ${bibleStudyStyleAttr()}>${bibleHtml}</div>` : ""}
              </div>
            `).join("")}
          </div>
          ${renderSeparatorLine()}
        </section>
      `;
    }).join("");
  };

  const getSelectedFieldAlphabeticalContacts = () => {
    const field = getSelectedField();
    const contactsByKey = new Map();
    (field?.meetings || []).forEach((meeting) => {
      (meeting.contacts || []).forEach((contact) => {
        const key = contact.id
          ? `id:${Number(contact.id)}`
          : `name:${String(contact.name || contact.label || "").toLowerCase()}|${String((contact.addresses || [])[0] || "").toLowerCase()}`;
        contactsByKey.set(key, contact);
      });
    });
    return Array.from(contactsByKey.values()).sort((left, right) => (
      contactSortLabel(left).localeCompare(contactSortLabel(right))
      || contactIdValue(left) - contactIdValue(right)
    ));
  };

  const renderFieldAlphabeticalContactsHtml = () => {
    if (!selectedFieldName) return `<div class="field-list-cluster-empty">Choose a field first.</div>`;
    const contacts = getSelectedFieldAlphabeticalContacts();
    if (!contacts.length) return `<div class="field-list-cluster-empty">No contacts found for this field.</div>`;
    const columnCount = getPageColumnCount();
    const midpoint = columnCount === 2 ? findBalancedSplitIndex(contacts, 0) : contacts.length;
    const columns = columnCount === 2
      ? [contacts.slice(0, midpoint), contacts.slice(midpoint)]
      : [contacts];
    return `
      <div class="field-list-meeting-contacts field-list-meeting-contacts-${columnCount} ${columnCount === 2 && shouldShowVerticalSeparatorLines() ? "has-vertical-separator-lines" : ""}" style="--field-list-column-gap-in: ${getEffectiveTwoColumnGapIn()}in; --field-list-column-gap-ch: ${getEffectiveTwoColumnGapCh()}ch; --field-list-column-width-in: ${getEffectiveColumnWidthIn()}in;">
        ${columns.map((column) => `
          <div class="field-list-meeting-contact-column">
            ${column.map(renderContactClusterHtml).join("")}
          </div>
        `).join("")}
      </div>
    `;
  };

  const normalizeMeetingName = (value) => String(value || "")
    .toLowerCase()
    .replace(/\([^)]*\)/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

  const normalizeFieldName = (value) => String(value || "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\bsouth\b/g, "s")
    .replace(/\bnorth\b/g, "n")
    .trim();

  const sanitizeMeetingHtml = (value) => {
    const template = document.createElement("template");
    template.innerHTML = String(value || "");
    const allowedTags = new Set(["B", "STRONG", "I", "EM", "U", "BR"]);
    const appendTextWithBreaks = (fragment, text) => {
      String(text || "").split(/\r?\n/).forEach((part, index) => {
        if (index > 0) fragment.appendChild(document.createElement("br"));
        if (part) fragment.appendChild(document.createTextNode(part));
      });
    };
    const styleLooks = (node, kind) => {
      const css = `${node.getAttribute("style") || ""} ${node.style?.fontStyle || ""} ${node.style?.fontWeight || ""} ${node.style?.textDecoration || ""}`.toLowerCase();
      if (kind === "italic") return /font-style\s*:\s*(italic|oblique)/.test(css);
      if (kind === "bold") return /font-weight\s*:\s*(bold|bolder|[6-9]00)/.test(css);
      if (kind === "underline") return /text-decoration(?:-line)?\s*:\s*[^;]*underline/.test(css);
      return false;
    };
    const wrapFragment = (fragment, tagName) => {
      const wrapper = document.createElement(tagName);
      wrapper.appendChild(fragment);
      return wrapper;
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
        if (tagName === "SPAN" || tagName === "FONT") {
          let wrapped = fragment;
          if (styleLooks(node, "bold")) wrapped = wrapFragment(wrapped, "strong");
          if (styleLooks(node, "italic")) wrapped = wrapFragment(wrapped, "em");
          if (styleLooks(node, "underline")) wrapped = wrapFragment(wrapped, "u");
          return wrapped;
        }
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
  };

  const findMeetingPreviewRows = (meetingName = selectedMeetingName) => {
    const selectedMeetingKey = normalizeMeetingName(meetingName);
    const selectedFieldKey = normalizeFieldName(selectedFieldName);
    if (!selectedMeetingKey) return [];
    const sections = Array.isArray(meetingPreviewPayload.sections) ? meetingPreviewPayload.sections : [];
    const scored = sections
      .map((section) => {
        const meetingKey = normalizeMeetingName(section.meeting || section.meeting_key);
        const fieldKey = normalizeFieldName(section.field || section.field_key);
        const meetingMatches =
          meetingKey === selectedMeetingKey ||
          meetingKey.startsWith(selectedMeetingKey) ||
          selectedMeetingKey.startsWith(meetingKey);
        if (!meetingMatches) return null;
        const fieldMatches =
          fieldKey === selectedFieldKey ||
          fieldKey.includes(selectedFieldKey) ||
          selectedFieldKey.includes(fieldKey);
        return { score: fieldMatches ? 2 : 1, rows: section.rows || [] };
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
    return scored[0]?.rows || [];
  };

  const meetingPreviewRowFontSizePt = (row) => {
    const explicit = Number(row?.font_size_pt || 0);
    if (explicit > 0) return explicit;
    const effective = Number(row?.effective_font_size_pt || 0);
    return effective > 0 ? effective : 0;
  };

  const renderMeetingPreviewRow = (row) => {
    const rowFontSizePt = meetingPreviewRowFontSizePt(row);
    const rowStyle = [
      rowFontSizePt ? `--meeting-row-font-size: ${rowFontSizePt.toFixed(2)}pt` : "",
      `--meeting-grid-column-gap: ${Math.max(0, Math.min(48, Number(row.column_gap_px || 6)))}px`,
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
          return `<div class="${classes}" style="grid-column: ${colStart} / span ${colSpan};">${sanitizeMeetingHtml(cell.html)}</div>`;
        }).join("")}
      </div>
    `;
  };

  const renderBibleStudyClusterHtml = (meetingName = selectedMeetingName) => {
    const rows = findMeetingPreviewRows(meetingName);
    if (!rows.length) return "";
    return rows.map(renderMeetingPreviewRow).join("");
  };

  const renderAlignedBibleStudyClusterHtml = () => (
    `<div class="field-list-bible-cluster ${getBibleStudyUnionAlignClass()}" ${bibleStudyStyleAttr()}>${renderBibleStudyClusterHtml()}</div>${renderSeparatorLine()}`
  );

  const renderPreviewHtml = (item) => {
    if (item.item_type === "field_name") return renderTitleClusterHtml();
    if (item.item_type === "meeting_name") return renderMeetingNameHtml();
    if (item.item_type === "contacts") return renderContactClusterHtml(getSelectedContact());
    if (item.item_type === "meetings") return renderMeetingClusterHtml();
    if (item.item_type === "bible_study_union") return renderAlignedBibleStudyClusterHtml();
    const previewLines = previewTextForType(item.item_type).map(escapeHtml);
    return previewLines.map((line) => `<div>${line}</div>`).join("");
  };

  const allowsUnboundedClusterHeight = (item) => (
    item.item_type === "meetings" && !usesManualPalettePlacement()
  );

  const syncCanvasPreviewHeight = (layoutItems) => {
    const pxPerIn = getPxPerIn();
    let maxBottomPx = pageHeightIn * pxPerIn;
    layoutItems.forEach((item) => {
      maxBottomPx = Math.max(maxBottomPx, (item.y_in + item.height_in) * pxPerIn + 12);
    });
    canvas.style.height = `${Math.ceil(maxBottomPx)}px`;
  };

  const applyCanvasSettings = () => {
    const pxPerIn = getPxPerIn();
    canvas.style.width = `${pageWidthIn * pxPerIn}px`;
    canvas.style.height = `${pageHeightIn * pxPerIn}px`;
    canvas.style.setProperty("--field-list-grid-size", `${gridStepIn * pxPerIn}px`);
    marginBox.style.left = `${numberSetting("marginLeftIn", 0.35, 0.1, 2) * pxPerIn}px`;
    marginBox.style.right = `${numberSetting("marginRightIn", 0.35, 0.1, 2) * pxPerIn}px`;
    marginBox.style.top = `${numberSetting("marginTopIn", 0.35, 0.1, 2) * pxPerIn}px`;
    marginBox.style.bottom = `${numberSetting("marginBottomIn", 0.35, 0.1, 2) * pxPerIn}px`;
  };

  const meetingClusterTopOffsetIn = () => {
    if (isAlphabeticalMode()) return 0.48;
    if (usesManualPalettePlacement()) return 0.36;
    return getPageColumnCount() === 2 ? 0.62 : 0.72;
  };

  const buildFixedLayoutItems = () => {
    const bounds = getCanvasBounds();
    const widthIn = Math.min(getEffectiveColumnWidthIn(), bounds.maxX - bounds.minX);
    const rows = [
      {
        item_type: "field_name",
        label_number: 1,
        x_in: bounds.minX,
        y_in: bounds.minY,
        width_in: widthIn,
        height_in: 0.34,
        font_size_pt: Number(settings.titleFontSizePt || 13),
      },
    ];
    if (!isAlphabeticalMode()) {
      rows.push({
        item_type: "meeting_name",
        label_number: 2,
        x_in: bounds.minX,
        y_in: bounds.minY + 0.34,
        width_in: widthIn,
        height_in: 0.34,
        font_size_pt: Number(settings.meetingNameFontSizePt || 11),
      });
    }
    rows.push({
      item_type: "meetings",
      label_number: 4,
      x_in: bounds.minX,
      y_in: bounds.minY + meetingClusterTopOffsetIn(),
      width_in: getPageColumnCount() === 2 ? bounds.maxX - bounds.minX : widthIn,
      height_in: Math.max(1, bounds.maxY - bounds.minY - 0.82),
      font_size_pt: Number(settings.baseFontSizePt || 10),
    });
    return rows.map((item, index) => normalizeItem({ id: index + 1, ...item }, index));
  };

  const normalizeItem = (item, index) => {
    const typeDef = itemTypeMap.get(item.item_type) || itemTypes[0];
    const bounds = getCanvasBounds();
    const fontSizePt = clamp(Number(item.font_size_pt || settings.baseFontSizePt || 10), 8, 36);
    let widthIn = clamp(Number(item.width_in || typeDef.default_width_in || 1.5), 0.4, bounds.maxX - bounds.minX);
    if (item.item_type === "phone_number") {
      widthIn = Math.max(widthIn, minPhoneWidthIn(fontSizePt));
    }
    const heightIn = clamp(Number(item.height_in || typeDef.default_height_in || 0.4), 0.28, bounds.maxY - bounds.minY);
    return {
      id: Number(item.id || Date.now() + index),
      item_type: item.item_type || typeDef.type,
      label_number: Number(item.label_number || typeDef.number || index + 1),
      x_in: clamp(Number(item.x_in || bounds.minX), bounds.minX, bounds.maxX - widthIn),
      y_in: clamp(Number(item.y_in || bounds.minY), bounds.minY, bounds.maxY - heightIn),
      width_in: widthIn,
      height_in: heightIn,
      font_size_pt: fontSizePt,
      sort_order: index + 1,
    };
  };

  items = items.map(normalizeItem);

  const serialize = () => {
    if (!hiddenInput) return;
    const value = JSON.stringify(
      items.map((item, index) => ({
        ...item,
        sort_order: index + 1,
      }))
    );
    hiddenInput.value = value;
    if (settingsItemsInput) settingsItemsInput.value = value;
    if (settingsTemplateNameInput && templateNameInput) settingsTemplateNameInput.value = templateNameInput.value;
    if (createItemsInput) createItemsInput.value = value;
    if (createTemplateNameInput && templateNameInput) createTemplateNameInput.value = templateNameInput.value;
    if (printItemsInput) printItemsInput.value = value;
  };

  const syncSettingsToForm = (form, overrides = {}) => {
    if (!form) return;
    Array.from(form.querySelectorAll("[data-field-list-save-setting]")).forEach((node) => node.remove());
    const appendHidden = (name, value) => {
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      hidden.dataset.fieldListSaveSetting = "1";
      hidden.value = value;
      form.appendChild(hidden);
    };
    const submittedNames = new Set();
    settingInputs.forEach((input) => {
      const name = input.name;
      if (!name || submittedNames.has(name)) return;
      submittedNames.add(name);
      if (input.type === "checkbox") {
        appendHidden(name, input.checked && !input.closest("[hidden]") ? "1" : "");
      } else if (input.type === "radio") {
        const checked = settingInputs.find((candidate) => candidate.name === name && candidate.checked);
        appendHidden(name, checked ? checked.value : "");
      } else {
        appendHidden(name, input.value);
      }
    });
    appendHidden("manual_palette_items", usesManualPalettePlacement() ? "1" : "");
    appendHidden("selected_field_name", overrides.selectedFieldName ?? selectedFieldName);
    appendHidden("selected_meeting_name", overrides.selectedMeetingName ?? selectedMeetingName);
    appendHidden("selected_contact_id", String(overrides.selectedContactId ?? selectedContactId ?? 0));
  };

  const syncPrintOptionsToForm = () => {
    if (!printForm) return;
    Array.from(printForm.querySelectorAll("[data-field-list-print-option]")).forEach((node) => node.remove());
    const appendHidden = (name, value) => {
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      hidden.dataset.fieldListPrintOption = "1";
      hidden.value = value;
      printForm.appendChild(hidden);
    };
    const printMode = isCompactListMode() ? "compact_no_bible" : isAlphabeticalMode() ? "alphabetical_no_bible" : "template";
    const meetingHomeFirst = shouldPrintMeetingHomeFirst();
    const pageNumbers = document.querySelector("input[name='print_page_numbers']")?.checked;
    appendHidden("print_mode", printMode);
    appendHidden("print_field_list_order", "template");
    appendHidden("print_meeting_home_first", meetingHomeFirst ? "1" : "");
    appendHidden("print_page_numbers", pageNumbers ? "1" : "");
    Array.from(printModalMeetingNames).forEach((meetingName) => appendHidden("selected_meeting_names", meetingName));
  };

  const fieldListBibleFontScope = () => ({
    field_name: selectedFieldName || settings.selectedFieldName || "",
    selected_meeting_names: usesManualPalettePlacement() && selectedMeetingName ? [selectedMeetingName] : [],
    font_size_pt: getBibleStudyFontSizePt(),
  });

  const postJson = async (url, payload) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed.");
    return data;
  };

  const normalizeBibleFontScopeValue = (value) => String(value || "")
    .toLowerCase()
    .replace(/\([^)]*\)/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

  const meetingPreviewSectionInScope = (section) => {
    const fieldKey = normalizeBibleFontScopeValue(section.field || section.field_key);
    const selectedFieldKey = normalizeBibleFontScopeValue(selectedFieldName || settings.selectedFieldName);
    const fieldMatches = !selectedFieldKey || fieldKey === selectedFieldKey || fieldKey.startsWith(selectedFieldKey) || selectedFieldKey.startsWith(fieldKey);
    if (!fieldMatches) return false;
    if (!selectedMeetingName) return true;
    const meetingKey = normalizeBibleFontScopeValue(section.meeting || section.meeting_key);
    const selectedMeetingKey = normalizeBibleFontScopeValue(selectedMeetingName);
    return meetingKey === selectedMeetingKey || meetingKey.startsWith(selectedMeetingKey) || selectedMeetingKey.startsWith(meetingKey);
  };

  const updateMeetingPreviewFontPayload = (mode, rowActions = []) => {
    const conflictIds = new Set((bibleFontInspection?.conflicts || []).map((row) => Number(row.row_id)));
    const actionMap = new Map(rowActions.map((action) => [Number(action.row_id), action]));
    const selectedSize = Number(getBibleStudyFontSizePt());
    (meetingPreviewPayload.sections || []).filter(meetingPreviewSectionInScope).forEach((section) => {
      (section.rows || []).forEach((row) => {
        const rowId = Number(row.row_id || 0);
        if (!rowId) return;
        if (mode === "all") {
          row.font_size_pt = null;
          return;
        }
        if (mode === "except_conflicts") {
          if (!conflictIds.has(rowId)) row.font_size_pt = null;
          return;
        }
        if (mode === "resolve") {
          if (!conflictIds.has(rowId)) {
            row.font_size_pt = null;
            return;
          }
          const action = actionMap.get(rowId);
          if (!action || action.choice === "keep") return;
          row.font_size_pt = action.choice === "new" ? Number(action.new_size || selectedSize) : null;
        }
      });
    });
  };

  const applyBibleFontResolution = async (mode, rowActions = []) => {
    const result = await postJson("/field-list/bible-font/apply", {
      ...fieldListBibleFontScope(),
      mode,
      row_actions: rowActions,
      template_id: templateIdInput?.value || "",
      template_name: templateNameInput?.value || "",
      items_json: hiddenInput?.value || "[]",
    });
    updateMeetingPreviewFontPayload(mode, rowActions);
    render();
    return result;
  };

  const openBibleFontConflictModal = (inspection) => new Promise((resolve) => {
    if (!bibleFontConflictModal) {
      resolve("all");
      return;
    }
    bibleFontInspection = inspection;
    const showOption = bibleFontConflictModal.querySelector("input[value='show_conflicts']");
    if (showOption) {
      showOption.disabled = !inspection.has_conflicts;
      showOption.closest("label")?.toggleAttribute("hidden", !inspection.has_conflicts);
    }
    const exceptOption = bibleFontConflictModal.querySelector("input[value='except_conflicts']");
    if (exceptOption) {
      exceptOption.disabled = !inspection.has_conflicts;
      exceptOption.closest("label")?.toggleAttribute("hidden", !inspection.has_conflicts);
    }
    const defaultOption = bibleFontConflictModal.querySelector("input[value='all']");
    if (defaultOption) defaultOption.checked = true;
    const cleanup = (value) => {
      bibleFontConflictOk?.removeEventListener("click", onOk);
      bibleFontConflictCancel?.removeEventListener("click", onCancel);
      bibleFontConflictModal.removeEventListener("cancel", onCancel);
      resolve(value);
    };
    const onOk = () => {
      const selected = bibleFontConflictModal.querySelector("input[name='bible_font_resolution_mode']:checked")?.value || "all";
      bibleFontConflictModal.close();
      cleanup(selected);
    };
    const onCancel = () => {
      bibleFontConflictModal.close();
      cleanup("");
    };
    bibleFontConflictOk?.addEventListener("click", onOk);
    bibleFontConflictCancel?.addEventListener("click", onCancel);
    bibleFontConflictModal.addEventListener("cancel", onCancel);
    bibleFontConflictModal.showModal();
  });

  const openBibleFontResolveModal = (inspection) => new Promise((resolve) => {
    if (!bibleFontResolveModal || !bibleFontResolveList) {
      resolve([]);
      return;
    }
    const selectedSize = inspection.selected_size;
    if (bibleFontResolveTitle) {
      bibleFontResolveTitle.textContent = `Bible study font conflict - selected Field List font ${selectedSize}`;
    }
    const fontSizes = ["6", "6.5", "7", "7.5", "8", "8.5", "9", "9.5", "10", "10.5", "11", "11.5", "12", "12.5", "13", "13.5", "14", "14.5", "15"];
    if (!(inspection.conflicts || []).length) {
      bibleFontResolveList.innerHTML = `<div class="field-list-font-resolve-row">No user defined rows were found for the selected Field and Meeting(s).</div>`;
    } else {
    bibleFontResolveList.innerHTML = (inspection.conflicts || []).map((row) => `
      <section class="field-list-font-resolve-row" data-conflict-row-id="${Number(row.row_id)}">
        <div class="field-list-font-resolve-row-title">${escapeHtml(row.meeting_name)} - Row ${Number(row.row_order)}</div>
        <div class="field-list-font-resolve-row-text">${escapeHtml(row.row_text)}</div>
        <div class="field-list-font-resolve-options">
          <label class="field-list-radio-row">
            <input type="radio" name="font_conflict_${Number(row.row_id)}" value="keep" checked>
            <span>keep ${escapeHtml(row.current_size)}</span>
          </label>
          <label class="field-list-radio-row">
            <input type="radio" name="font_conflict_${Number(row.row_id)}" value="change">
            <span>change to ${escapeHtml(selectedSize)}</span>
          </label>
          <label class="field-list-radio-row">
            <input type="radio" name="font_conflict_${Number(row.row_id)}" value="new">
            <span>New size</span>
            <select class="field-list-font-new-size" data-conflict-new-size>
              ${fontSizes.map((size) => `<option value="${size}" ${Number(size) === Number(row.current_size) ? "selected" : ""}>${Number(size).toFixed(1)}</option>`).join("")}
            </select>
          </label>
        </div>
      </section>
    `).join("");
    }
    const cleanup = (value) => {
      bibleFontResolveOk?.removeEventListener("click", onOk);
      bibleFontResolveCancel?.removeEventListener("click", onCancel);
      bibleFontResolveModal.removeEventListener("cancel", onCancel);
      resolve(value);
    };
    const onOk = () => {
      const actions = Array.from(bibleFontResolveList.querySelectorAll("[data-conflict-row-id]")).map((rowEl) => {
        const rowId = Number(rowEl.dataset.conflictRowId || 0);
        const choice = rowEl.querySelector(`input[name='font_conflict_${rowId}']:checked`)?.value || "keep";
        const newSize = rowEl.querySelector("[data-conflict-new-size]")?.value || selectedSize;
        return { row_id: rowId, choice, new_size: newSize };
      });
      bibleFontResolveModal.close();
      cleanup(actions);
    };
    const onCancel = () => {
      bibleFontResolveModal.close();
      cleanup(null);
    };
    bibleFontResolveOk?.addEventListener("click", onOk);
    bibleFontResolveCancel?.addEventListener("click", onCancel);
    bibleFontResolveModal.addEventListener("cancel", onCancel);
    bibleFontResolveModal.showModal();
  });

  const handleBibleStudyFontSizeChange = async () => {
    const scope = fieldListBibleFontScope();
    if (!scope.field_name) return;
    try {
      const inspection = await postJson("/field-list/bible-font/conflicts", scope);
      if (!inspection.has_differences) return;
      const mode = await openBibleFontConflictModal(inspection);
      if (!mode) return;
      if (mode === "show_conflicts") {
        const actions = await openBibleFontResolveModal(inspection);
        if (!actions) return;
        await applyBibleFontResolution("resolve", actions);
      } else {
        await applyBibleFontResolution(mode);
      }
    } catch (error) {
      await confirmAction({
        title: "Field List",
        copy: error.message || "Unable to update MeetingData font sizes.",
        submitLabel: "OK",
      });
    }
  };

  const getPrintModalField = () => pickerFields.find((field) => field.name === printModalFieldName) || pickerFields[0] || null;

  const setAllPrintModalMeetings = (checked) => {
    const field = getPrintModalField();
    const meetings = field?.meetings || [];
    printModalMeetingNames = new Set(checked ? meetings.map((meeting) => meeting.name) : []);
  };

  const renderPrintModalMeetings = () => {
    if (!printModalMeetings) return;
    const field = getPrintModalField();
    const meetings = field?.meetings || [];
    const allSelected = meetings.length > 0 && meetings.every((meeting) => printModalMeetingNames.has(meeting.name));
    printModalMeetings.innerHTML = `
      <label class="field-list-print-modal-choice field-list-print-modal-all">
        <input type="checkbox" data-print-modal-all-meetings ${allSelected ? "checked" : ""}>
        <span>All Meetings</span>
      </label>
      ${meetings.map((meeting) => `
        <label class="field-list-print-modal-choice">
          <input type="checkbox" value="${escapeHtml(meeting.name)}" data-print-modal-meeting ${printModalMeetingNames.has(meeting.name) ? "checked" : ""}>
          <span>${escapeHtml(meeting.name)}</span>
        </label>
      `).join("")}
    `;
    printModalMeetings.querySelector("[data-print-modal-all-meetings]")?.addEventListener("change", (event) => {
      setAllPrintModalMeetings(event.target.checked);
      renderPrintModalMeetings();
    });
    Array.from(printModalMeetings.querySelectorAll("[data-print-modal-meeting]")).forEach((input) => {
      input.addEventListener("change", () => {
        if (input.checked) {
          printModalMeetingNames.add(input.value);
        } else {
          printModalMeetingNames.delete(input.value);
        }
        const allInput = printModalMeetings.querySelector("[data-print-modal-all-meetings]");
        if (allInput) {
          allInput.checked = meetings.length > 0 && meetings.every((meeting) => printModalMeetingNames.has(meeting.name));
        }
      });
    });
  };

  const renderPrintModalFields = () => {
    if (!printModalFields) return;
    printModalFields.innerHTML = pickerFields.map((field) => `
      <label class="field-list-print-modal-choice">
        <input type="radio" name="print_modal_field" value="${escapeHtml(field.name)}" ${field.name === printModalFieldName ? "checked" : ""}>
        <span>${escapeHtml(field.name)}</span>
      </label>
    `).join("");
    Array.from(printModalFields.querySelectorAll("input[name='print_modal_field']")).forEach((input) => {
      input.addEventListener("change", () => {
        if (!input.checked) return;
        printModalFieldName = input.value;
        setAllPrintModalMeetings(true);
        renderPrintModalFields();
        renderPrintModalMeetings();
      });
    });
  };

  const openPrintPickerModal = async () => {
    if (!pickerFields.length) {
      await showMessage("No fields are available to print.");
      return;
    }
    printModalFieldName = selectedFieldName && pickerFields.some((field) => field.name === selectedFieldName)
      ? selectedFieldName
      : pickerFields[0].name;
    setAllPrintModalMeetings(true);
    renderPrintModalFields();
    renderPrintModalMeetings();
    printPickerModal?.showModal();
    window.setTimeout(() => {
      if (document.activeElement && printPickerModal?.contains(document.activeElement)) {
        document.activeElement.blur();
      }
    }, 0);
  };

  const submitPrintFromModal = async () => {
    if (!printModalFieldName) {
      await showMessage("Choose a field before printing.");
      return;
    }
    if (!printModalMeetingNames.size) {
      await showMessage("Choose at least one meeting to print.");
      return;
    }
    render();
    syncSettingsToForm(printForm, {
      selectedFieldName: printModalFieldName,
      selectedMeetingName: "",
      selectedContactId: 0,
    });
    syncPrintOptionsToForm();
    printPickerModal?.close();
    printForm?.requestSubmit();
  };

  const submitDuplicateMeetingPrint = async () => {
    if (!selectedFieldName || !selectedMeetingName) {
      await showMessage("Choose a field and meeting before printing.");
      return;
    }
    render();
    printModalFieldName = selectedFieldName;
    printModalMeetingNames = new Set([selectedMeetingName]);
    syncSettingsToForm(printForm, {
      selectedFieldName,
      selectedMeetingName,
      selectedContactId: 0,
    });
    syncPrintOptionsToForm();
    printForm?.requestSubmit();
  };

  const render = () => {
    const pxPerIn = getPxPerIn();
    items = buildFixedLayoutItems();
    applyCanvasSettings();
    syncConditionalSettingsVisibility();
    Array.from(canvas.querySelectorAll(".field-list-block, .field-list-page-number-preview")).forEach((node) => node.remove());
    items.forEach((item, index) => {
      const typeDef = itemTypeMap.get(item.item_type) || {};
      const effectiveFontSize = getCanvasItemFontSizePt(item);
      const block = document.createElement("div");
      block.className = "field-list-block";
      block.classList.add(`field-list-block-type-${item.item_type}`);
      if (isTitleType(item.item_type)) block.classList.add("field-list-title-block");
      if (isPrintTextType(item)) block.classList.add("field-list-print-text-block");
      if (isClusterType(item.item_type)) block.classList.add("field-list-cluster-block");
      if (item.id === activeId) block.classList.add("is-active");
      if (item.id === revealedDeleteId) block.classList.add("is-delete-revealed");
      block.dataset.itemId = String(item.id);
      block.style.left = `${item.x_in * pxPerIn}px`;
      block.style.top = `${item.y_in * pxPerIn}px`;
      block.style.width = `${item.width_in * pxPerIn}px`;
      block.style.height = `${item.height_in * pxPerIn}px`;
      block.style.fontFamily = settings.fontFamily || "Arial";
      block.style.fontSize = `${effectiveFontSize}pt`;
      block.style.lineHeight = String(settings.lineHeight || 1.2);
      const standardChrome = false;
      const showDelete = false;
      block.innerHTML = `
        ${standardChrome ? `<div class="field-list-block-label">${item.label_number}. ${escapeHtml(typeDef.label || "Field")}</div>` : ""}
        ${showDelete ? `<button type="button" class="field-list-block-delete" ${canEdit ? "" : "disabled"} aria-label="Delete block">x</button>` : ""}
        <div class="field-list-block-preview">
          ${renderPreviewHtml(item)}
        </div>
      `;
      const preview = block.querySelector(".field-list-block-preview");
      if (preview && !isTitleType(item.item_type) && item.item_type !== "bible_study_union") {
        preview.style.fontWeight = settings.baseFontBold ? "800" : "500";
        preview.style.fontStyle = settings.baseFontItalic ? "italic" : "normal";
        preview.style.textDecoration = settings.baseFontUnderline ? "underline" : "none";
      }
      canvas.appendChild(block);
      fitBlockToContent(block, item);
    });
    syncCanvasPreviewHeight(items);
    if (shouldPreviewPageNumbers()) {
      const pageNumber = document.createElement("div");
      pageNumber.className = "field-list-page-number-preview";
      pageNumber.textContent = "1";
      canvas.appendChild(pageNumber);
    }
    alignBibleStudyOnNextRender = false;
    serialize();
  };

  const fitBlockToContent = (block, item) => {
    if (!isClusterType(item.item_type) && !isTitleType(item.item_type)) return;
    const pxPerIn = getPxPerIn();
    const bounds = getCanvasBounds();
    const canRecenterTwoColumnMeetings = item.item_type === "meetings" && getPageColumnCount() === 2;
    const maxWidthIn = canRecenterTwoColumnMeetings ? (bounds.maxX - bounds.minX) : (bounds.maxX - item.x_in);
    const maxWidthPx = Math.max(1, maxWidthIn * pxPerIn);
    const pageBoundHeightPx = Math.max(1, (bounds.maxY - item.y_in) * pxPerIn);
    const hasEmptyMessage = Boolean(block.querySelector(".field-list-cluster-empty"));
    block.style.width = hasEmptyMessage ? `${maxWidthPx}px` : "max-content";
    block.style.height = "max-content";
    const widthPx = Math.min(maxWidthPx, Math.ceil(block.scrollWidth) + 2);
    const naturalHeightPx = Math.ceil(block.scrollHeight) + 2;
    const heightPx = allowsUnboundedClusterHeight(item)
      ? naturalHeightPx
      : Math.min(pageBoundHeightPx, naturalHeightPx);
    item.width_in = Math.max(0.4, snapInches(widthPx / pxPerIn));
    item.height_in = Math.max(0.28, ceilInches((heightPx + 6) / pxPerIn));
    if (isTitleType(item.item_type) && !usesManualPalettePlacement()) {
      const align = item.item_type === "field_name"
        ? (settings.titleAlign === "left" ? "left" : "center")
        : (settings.meetingNameAlign === "left" ? "left" : "center");
      if (getPageColumnCount() === 1) {
        const column = getCurrentColumnBounds(item);
        const maxTitleX = Math.max(column.left, column.right - item.width_in);
        item.x_in = align === "left"
          ? column.left
          : clamp(snapInches(column.left + ((column.width - item.width_in) / 2)), column.left, maxTitleX);
      } else {
        item.x_in = align === "left"
          ? bounds.minX
          : clamp(snapInches((pageWidthIn - item.width_in) / 2), bounds.minX, bounds.maxX - item.width_in);
      }
    } else if (usesManualPalettePlacement() && item.item_type === "meeting_name") {
      item.y_in = bounds.minY;
    } else if (canRecenterTwoColumnMeetings) {
      item.width_in = Math.min(item.width_in, bounds.maxX - bounds.minX);
      item.x_in = bounds.minX;
    }
    if (alignBibleStudyOnNextRender) {
      alignBibleStudyUnionItem(item);
    }
    block.style.width = `${item.width_in * pxPerIn}px`;
    block.style.height = `${item.height_in * pxPerIn}px`;
    block.style.left = `${item.x_in * pxPerIn}px`;
    block.style.top = `${item.y_in * pxPerIn}px`;
  };

  const updateBlockElement = (block, item) => {
    const pxPerIn = getPxPerIn();
    block.style.left = `${item.x_in * pxPerIn}px`;
    block.style.top = `${item.y_in * pxPerIn}px`;
    block.style.width = `${item.width_in * pxPerIn}px`;
    block.style.height = `${item.height_in * pxPerIn}px`;
    serialize();
  };

  const addItem = (type) => {
    const typeDef = itemTypeMap.get(type);
    if (!typeDef) return;
    const duplicateControlledTypes = new Set(["field_name", "meeting_name", "meetings", "bible_study_union"]);
    const existingItem = items.find((item) => item.item_type === type);
    if (duplicateControlledTypes.has(type) && existingItem && !usesManualPalettePlacement()) {
      activeId = existingItem.id;
      showMessage("Turn on Duplicate in two-column mode to add duplicate palette items.");
      render();
      return;
    }
    if (type === "meetings" && (!selectedFieldName || !selectedMeetingName)) {
      showMessage("Choose a field and meeting first.");
      return;
    }
    if (type === "contacts" && (!selectedFieldName || !selectedMeetingName || !getSelectedContact())) {
      showMessage("Choose a field and meeting first.");
      return;
    }
    const baseFontSize = Number(settings.baseFontSizePt || 10);
    let widthIn = Number(typeDef.default_width_in || 1.5);
    if (type === "phone_number") {
      widthIn = Math.max(widthIn, minPhoneWidthIn(baseFontSize));
    }
    if (type === "bible_study_union") {
      clearBibleStudyUnionAlignChoice();
    }
    items.push(
      normalizeItem(
        {
          id: Date.now() + Math.floor(Math.random() * 1000),
          item_type: type,
          label_number: Number(typeDef.number || items.length + 1),
          x_in: getCanvasBounds().minX + ((items.length % 3) * 0.35),
          y_in: getCanvasBounds().minY + ((items.length % 5) * 0.35),
          width_in: widthIn,
          height_in: Number(typeDef.default_height_in || 0.4),
          font_size_pt: baseFontSize,
        },
        items.length
      )
    );
    activeId = items[items.length - 1].id;
    render();
  };

  if (canEdit) {
    loadSavedSelection();
    populateSelectors();

    [saveForm, settingsForm].forEach((form) => {
      form?.addEventListener("submit", () => {
        saveSelection();
        serialize();
        syncSettingsToForm(form);
      });
    });
    saveForm?.addEventListener("submit", (event) => {
      if (!templateNameExistsForAnotherTemplate(templateNameInput?.value)) return;
      event.preventDefault();
      showMessage("Name is already chosen - pick another.");
      templateNameInput?.focus();
    });
    templatePicker?.addEventListener("change", () => {
      syncTemplatePickerPlaceholderState();
      const templateId = Number(templatePicker.value || 0);
      if (templateId) window.location.href = `/field-list?template_id=${templateId}`;
    });
    createButton?.addEventListener("click", () => {
      items = [];
      activeId = null;
      revealedDeleteId = null;
      lastPrintTextClick = { id: null, time: 0 };
      if (templateIdInput) templateIdInput.value = "0";
      if (templateNameInput) {
        templateNameInput.value = "";
        templateNameInput.focus();
      }
      if (hiddenInput) hiddenInput.value = "[]";
      if (settingsItemsInput) settingsItemsInput.value = "[]";
      if (createItemsInput) createItemsInput.value = "[]";
      render();
    });
    createForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      saveSelection();
      syncSettingsToForm(createForm);
      if (createItemsInput) createItemsInput.value = "[]";
      createForm.submit();
    });
    deleteForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const confirmed = await confirmAction({
        title: "Delete Field List Template?",
        copy: "Are you sure you want to delete this Field List template?",
        submitLabel: "Delete",
      });
      if (confirmed) {
        deleteForm.submit();
      }
    });

    addButtons.forEach((button) => {
      button.addEventListener("click", () => addItem(button.dataset.fieldListAdd));
    });

    canvas.addEventListener("click", (event) => {
      return;
      const block = event.target.closest(".field-list-block");
      if (!block) {
        activeId = null;
        revealedDeleteId = null;
        lastPrintTextClick = { id: null, time: 0 };
        render();
        return;
      }
      activeId = Number(block.dataset.itemId);
      if (event.target.closest(".field-list-block-delete")) {
        items = items.filter((item) => item.id !== activeId);
        activeId = null;
        revealedDeleteId = null;
        lastPrintTextClick = { id: null, time: 0 };
        render();
        return;
      }
      const item = items.find((entry) => entry.id === activeId);
      const now = Date.now();
      if (item && isPrintTextType(item)) {
        revealedDeleteId = activeId;
        lastPrintTextClick = { id: activeId, time: now };
        render();
        return;
      }
      const isDoubleClick =
        event.detail >= 2 ||
        (lastPrintTextClick.id === activeId && now - lastPrintTextClick.time <= 450);
      if (item && isPrintTextType(item) && isDoubleClick) {
        revealedDeleteId = activeId;
        lastPrintTextClick = { id: null, time: 0 };
        event.preventDefault();
        render();
        return;
      }
      lastPrintTextClick = item && isPrintTextType(item)
        ? { id: activeId, time: now }
        : { id: null, time: 0 };
      render();
    });

    canvas.addEventListener("dblclick", (event) => {
      return;
      const block = event.target.closest(".field-list-block");
      if (!block || event.target.closest(".field-list-block-delete")) return;
      const itemId = Number(block.dataset.itemId);
      const item = items.find((entry) => entry.id === itemId);
      if (!item || !isPrintTextType(item)) return;
      event.preventDefault();
      activeId = itemId;
      revealedDeleteId = itemId;
      render();
    });

    canvas.addEventListener("pointerdown", (event) => {
      return;
      if (event.target.closest(".field-list-block-delete")) return;
      const block = event.target.closest(".field-list-block");
      if (!block) return;
      const itemId = Number(block.dataset.itemId);
      const item = items.find((entry) => entry.id === itemId);
      if (!item) return;
      const now = Date.now();
      if (
        isPrintTextType(item) &&
        lastPrintTextClick.id === itemId &&
        now - lastPrintTextClick.time <= 450
      ) {
        activeId = itemId;
        revealedDeleteId = itemId;
        lastPrintTextClick = { id: null, time: 0 };
        event.preventDefault();
        render();
        return;
      }
      const startX = event.clientX;
      const startY = event.clientY;
      const origin = { ...item };
      activeId = itemId;
      Array.from(canvas.querySelectorAll(".field-list-block")).forEach((node) => {
        node.classList.toggle("is-active", node === block);
      });
      const activeBlock = block;
      let didMove = false;

      const onMove = (moveEvent) => {
        const pxPerIn = getPxPerIn();
        const bounds = getCanvasBounds();
        const dxPx = moveEvent.clientX - startX;
        const dyPx = moveEvent.clientY - startY;
        if (Math.abs(dxPx) > 3 || Math.abs(dyPx) > 3) didMove = true;
        const dxIn = (moveEvent.clientX - startX) / pxPerIn;
        const dyIn = (moveEvent.clientY - startY) / pxPerIn;
        if (!isTitleType(item.item_type) || usesManualPalettePlacement()) {
          item.x_in = clamp(snapInches(origin.x_in + dxIn), bounds.minX, bounds.maxX - item.width_in);
        }
        item.y_in = clamp(snapInches(origin.y_in + dyIn), bounds.minY, bounds.maxY - item.height_in);
        updateBlockElement(activeBlock, item);
      };

      const onUp = (upEvent) => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("pointercancel", onUp);
        const dxPx = upEvent.clientX - startX;
        const dyPx = upEvent.clientY - startY;
        const wasTap = !didMove && Math.abs(dxPx) <= 3 && Math.abs(dyPx) <= 3;
        if (wasTap && isPrintTextType(item)) {
          revealedDeleteId = itemId;
          lastPrintTextClick = { id: null, time: 0 };
        }
        if (didMove && item.item_type === "bible_study_union") {
          clearBibleStudyUnionAlignChoice();
        }
        render();
      };

      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
      document.addEventListener("pointercancel", onUp);
    });

    settingInputs.forEach((input) => {
      input.addEventListener("input", () => {
        const key = input.dataset.fieldListSetting;
        if (!key) return;
        if (shouldRevealTemplateToolbarForSettingInput(input)) revealCustomTemplateToolbar();
        if (input.type === "checkbox") {
          settings[key] = input.checked;
          if (key === "includeBibleStudyUnionInfo") syncBibleStudyControls();
        } else if (input.type === "radio") {
          if (!input.checked) return;
          settings[key] = Number.isNaN(Number(input.value)) ? input.value : Number(input.value);
          if (key === "bibleStudyUnionAlign") alignBibleStudyOnNextRender = true;
        } else {
          settings[key] = input.type === "number" ? Number(input.value) : input.value;
        }
        if (key === "pageColumnCount") setDefaultColumnWidthForOneColumnMode();
        items = items.map(normalizeItem);
        render();
      });
      input.addEventListener("change", () => {
        const key = input.dataset.fieldListSetting;
        if (!key) return;
        if (shouldRevealTemplateToolbarForSettingInput(input)) revealCustomTemplateToolbar();
        if (input.type === "checkbox") {
          settings[key] = input.checked;
          if (key === "includeBibleStudyUnionInfo") syncBibleStudyControls();
        } else if (input.type === "number") {
          settings[key] = Number(input.value);
        } else if (input.checked) {
          settings[key] = Number.isNaN(Number(input.value)) ? input.value : Number(input.value);
          if (key === "bibleStudyUnionAlign") alignBibleStudyOnNextRender = true;
        } else if (input.tagName === "SELECT") {
          settings[key] = input.value;
        } else {
          return;
        }
        if (key === "pageColumnCount") setDefaultColumnWidthForOneColumnMode();
        items = items.map(normalizeItem);
        render();
        if (key === "bibleStudyFontSizePt") {
          handleBibleStudyFontSizeChange();
        }
      });
    });

    if (fieldSelect && meetingSelect) {
      fieldSelect.addEventListener("change", () => {
        selectedFieldName = fieldSelect.value;
        selectedMeetingName = "";
        selectedContactId = 0;
        saveSelection();
        populateSelectors();
        render();
      });
      meetingSelect.addEventListener("change", () => {
        selectedMeetingName = meetingSelect.value;
        selectedContactId = 0;
        saveSelection();
        populateSelectors();
        render();
      });
      contactSelect?.addEventListener("change", () => {
        selectedContactId = Number(contactSelect.value || 0);
        saveSelection();
        populateSelectors();
        render();
      });
    }

    document.querySelector("input[name='print_page_numbers']")?.addEventListener("change", () => {
      revealCustomTemplateToolbar();
      render();
    });
    printMeetingHomeInput?.addEventListener("change", () => {
      revealCustomTemplateToolbar();
      printMeetingHomeFirst = Boolean(printMeetingHomeInput.checked);
      render();
    });
    columnModeInputs.forEach((input) => input.addEventListener("change", () => {
      if (setDefaultColumnWidthForOneColumnMode()) {
        items = items.map(normalizeItem);
      }
      render();
    }));
  } else {
    loadSavedSelection();
    populateSelectors();
  }

  printButton?.addEventListener("click", async () => {
    if (usesManualPalettePlacement()) {
      await submitDuplicateMeetingPrint();
      return;
    }
    await openPrintPickerModal();
  });

  printModalCancel?.addEventListener("click", () => {
    printPickerModal?.close();
  });

  printModalSubmit?.addEventListener("click", () => {
    submitPrintFromModal();
  });

  render();
});
