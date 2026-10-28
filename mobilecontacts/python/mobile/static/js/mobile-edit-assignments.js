(() => {
  const ADD_FIELD = "__add_field__";
  const DELETE_FIELD = "__delete_field__";
  const RENAME_FIELD = "__rename_field__";
  const ADD_MEETING = "__add_meeting__";
  const DELETE_MEETING = "__delete_meeting__";
  const RENAME_MEETING = "__rename_meeting__";

  const FIELD_SENTINELS = [
    { value: ADD_FIELD, label: "+ Add Field" },
    { value: DELETE_FIELD, label: "- Field" },
    { value: RENAME_FIELD, label: "Rename Field" },
  ];

  const MEETING_SENTINELS = [
    { value: ADD_MEETING, label: "+ Add Meeting" },
    { value: DELETE_MEETING, label: "- Meeting" },
    { value: RENAME_MEETING, label: "Rename Meeting" },
  ];

  function createModal() {
    const dialog = document.getElementById("mobile-assignment-modal");
    const form = document.getElementById("mobile-assignment-modal-form");
    const titleEl = document.getElementById("mobile-assignment-modal-title");
    const copyEl = document.getElementById("mobile-assignment-modal-copy");
    const labelEl = document.getElementById("mobile-assignment-modal-label");
    const fieldWrap = document.getElementById("mobile-assignment-modal-field");
    const inputEl = document.getElementById("mobile-assignment-modal-input");
    const errorEl = document.getElementById("mobile-assignment-modal-error");
    const cancelEl = document.getElementById("mobile-assignment-modal-cancel");
    const saveEl = document.getElementById("mobile-assignment-modal-save");

    if (!dialog || !form || !titleEl || !copyEl || !labelEl || !inputEl || !errorEl || !saveEl) {
      return {
        prompt: async () => "",
        message: async () => {},
      };
    }

    const closeDialog = () => {
      if (dialog.open) {
        dialog.close();
      }
    };

    const prompt = ({
      title,
      copy,
      label,
      initialValue = "",
      requireInput = true,
      submitLabel = "Save",
    }) => new Promise((resolve) => {
      titleEl.textContent = title;
      copyEl.textContent = copy;
      labelEl.textContent = label;
      inputEl.value = initialValue;
      errorEl.textContent = "";
      fieldWrap.hidden = !requireInput;
      cancelEl.hidden = false;
      saveEl.textContent = submitLabel;

      const cleanup = () => {
        form.removeEventListener("submit", handleSubmit);
        dialog.removeEventListener("close", handleClose);
        cancelEl.removeEventListener("click", handleCancel);
        fieldWrap.hidden = false;
        cancelEl.hidden = false;
      };

      const handleSubmit = (event) => {
        event.preventDefault();
        const nextValue = requireInput ? inputEl.value.trim() : "__confirmed__";
        if (requireInput && !nextValue) {
          errorEl.textContent = `${label} is required.`;
          inputEl.focus();
          return;
        }
        cleanup();
        resolve(nextValue);
        closeDialog();
      };

      const handleCancel = () => {
        cleanup();
        resolve("");
        closeDialog();
      };

      const handleClose = () => {
        cleanup();
        resolve("");
      };

      form.addEventListener("submit", handleSubmit);
      dialog.addEventListener("close", handleClose, { once: true });
      cancelEl.addEventListener("click", handleCancel, { once: true });
      dialog.showModal();
      if (requireInput) {
        window.setTimeout(() => inputEl.focus(), 0);
      } else {
        window.setTimeout(() => saveEl.focus(), 0);
      }
    });

    const message = ({ title, copy, buttonLabel = "OK" }) => new Promise((resolve) => {
      titleEl.textContent = title;
      copyEl.textContent = copy;
      fieldWrap.hidden = true;
      cancelEl.hidden = true;
      errorEl.textContent = "";
      saveEl.textContent = buttonLabel;

      const cleanup = () => {
        form.removeEventListener("submit", handleSubmit);
        dialog.removeEventListener("close", handleClose);
        fieldWrap.hidden = false;
        cancelEl.hidden = false;
      };

      const handleSubmit = (event) => {
        event.preventDefault();
        cleanup();
        resolve();
        closeDialog();
      };

      const handleClose = () => {
        cleanup();
        resolve();
      };

      form.addEventListener("submit", handleSubmit);
      dialog.addEventListener("close", handleClose, { once: true });
      dialog.showModal();
      window.setTimeout(() => saveEl.focus(), 0);
    });

    return { prompt, message };
  }

  function insertSortedOption(select, value, sentinelOptions, selectedValue) {
    if (!select || !value) return;
    const options = Array.from(select.options)
      .map((option) => ({ value: option.value, label: option.textContent || "" }))
      .filter((option) => !sentinelOptions.some((sentinel) => sentinel.value === option.value));

    if (!options.some((option) => option.value === value)) {
      options.push({ value, label: value });
    }

    const namedOptions = options
      .filter((option) => option.value !== "")
      .sort((left, right) => left.label.localeCompare(right.label, undefined, { sensitivity: "base" }));

    select.innerHTML = "";
    const blankOption = document.createElement("option");
    blankOption.value = "";
    blankOption.textContent = "";
    if (!selectedValue) {
      blankOption.selected = true;
    }
    select.appendChild(blankOption);

    namedOptions.forEach((option) => {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      if (option.value === selectedValue) {
        node.selected = true;
      }
      select.appendChild(node);
    });

    sentinelOptions.forEach((sentinel) => {
      const sentinelOption = document.createElement("option");
      sentinelOption.value = sentinel.value;
      sentinelOption.textContent = sentinel.label;
      select.appendChild(sentinelOption);
    });
  }

  async function postAssignment(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      credentials: "same-origin",
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || "Request failed.");
    }
    return payload;
  }

  window.MobileEditAssignments = {
    init({
      fieldSelect,
      meetingSelect,
      meetingsByField,
      allMeetings,
      initialMeetingValue = "",
    }) {
      if (!fieldSelect || !meetingSelect) {
        return;
      }

      const modal = createModal();
      const meetingsByFieldMap = meetingsByField || {};
      const allMeetingsList = Array.isArray(allMeetings) ? allMeetings.slice() : [];

      const meetingsForField = (fieldName) => {
        if (!fieldName) {
          return allMeetingsList.slice();
        }
        return Array.isArray(meetingsByFieldMap[fieldName]) ? meetingsByFieldMap[fieldName].slice() : [];
      };

      const renderMeetingOptions = (fieldName, preferredMeeting = null) => {
        const meetings = meetingsForField(fieldName);
        const preservedMeeting = preferredMeeting ?? meetingSelect.value ?? initialMeetingValue;
        meetingSelect.innerHTML = "";

        const blankOption = document.createElement("option");
        blankOption.value = "";
        blankOption.textContent = "";
        meetingSelect.appendChild(blankOption);

        meetings.forEach((meetingName) => {
          const option = document.createElement("option");
          option.value = meetingName;
          option.textContent = meetingName;
          if (meetingName === preservedMeeting) {
            option.selected = true;
          }
          meetingSelect.appendChild(option);
        });

        if (preservedMeeting && !meetings.includes(preservedMeeting)) {
          const fallbackOption = document.createElement("option");
          fallbackOption.value = preservedMeeting;
          fallbackOption.textContent = preservedMeeting;
          fallbackOption.selected = true;
          meetingSelect.insertBefore(fallbackOption, meetingSelect.firstChild.nextSibling);
        }

        MEETING_SENTINELS.forEach((sentinel) => {
          const sentinelOption = document.createElement("option");
          sentinelOption.value = sentinel.value;
          sentinelOption.textContent = sentinel.label;
          meetingSelect.appendChild(sentinelOption);
        });
      };

      const createAssignmentOption = async (kind, value, parentValue = "") => {
        const body = new URLSearchParams({ kind, value });
        if (parentValue) {
          body.set("parent_value", parentValue);
        }
        const payload = await postAssignment("/m/contacts/assignment-options", body);
        return String(payload.value || "").trim();
      };

      const removeAssignmentOption = async (kind, value) => {
        const body = new URLSearchParams({ kind, value });
        const payload = await postAssignment("/m/contacts/assignment-options/remove", body);
        return String(payload.value || "").trim();
      };

      const renameAssignmentOption = async (kind, oldValue, newValue) => {
        const body = new URLSearchParams({ kind, old_value: oldValue, new_value: newValue });
        return postAssignment("/m/contacts/assignment-options/rename", body);
      };

      renderMeetingOptions(fieldSelect.value || "", initialMeetingValue);
      fieldSelect.dataset.previousValue = fieldSelect.value;
      meetingSelect.dataset.previousValue = meetingSelect.value;

      fieldSelect.addEventListener("change", async () => {
        const previousValue = fieldSelect.dataset.previousValue ?? "";
        if (fieldSelect.value === ADD_FIELD) {
          const nextValue = await modal.prompt({
            title: "Add Field",
            copy: "Name the new Field. It will be inserted alphabetically into the list.",
            label: "Field name",
          });
          if (!nextValue) {
            fieldSelect.value = previousValue || "";
            return;
          }
          try {
            const savedValue = await createAssignmentOption("field", nextValue);
            insertSortedOption(fieldSelect, savedValue, FIELD_SENTINELS, savedValue);
            meetingsByFieldMap[savedValue] = meetingsByFieldMap[savedValue] || [];
            renderMeetingOptions(savedValue, "");
            fieldSelect.dataset.previousValue = savedValue;
            meetingSelect.dataset.previousValue = "";
          } catch (error) {
            await modal.message({
              title: "Add Field",
              copy: error.message || "Unable to add the new Field.",
            });
            fieldSelect.value = previousValue || "";
          }
          return;
        }
        if (fieldSelect.value === DELETE_FIELD) {
          if (!previousValue) {
            await modal.message({
              title: "Delete Field",
              copy: "Select a Field before using - Field.",
            });
            fieldSelect.value = "";
            return;
          }
          const confirmed = await modal.prompt({
            title: "Delete Field",
            copy: `Delete the Field "${previousValue}"? This only works when no contacts are assigned to that Field.`,
            label: "Field name",
            requireInput: false,
            submitLabel: "Delete",
          });
          if (!confirmed) {
            fieldSelect.value = previousValue || "";
            return;
          }
          try {
            await removeAssignmentOption("field", previousValue);
            Array.from(fieldSelect.options)
              .filter((option) => option.value === previousValue)
              .forEach((option) => option.remove());
            delete meetingsByFieldMap[previousValue];
            fieldSelect.value = "";
            fieldSelect.dataset.previousValue = "";
            renderMeetingOptions("", "");
            meetingSelect.dataset.previousValue = "";
          } catch (error) {
            await modal.message({
              title: "Delete Field",
              copy: error.message || "Unable to delete the Field.",
            });
            fieldSelect.value = previousValue || "";
          }
          return;
        }
        if (fieldSelect.value === RENAME_FIELD) {
          if (!previousValue) {
            await modal.message({
              title: "Rename Field",
              copy: "Select a Field before using Rename Field.",
            });
            fieldSelect.value = "";
            return;
          }
          const nextValue = await modal.prompt({
            title: "Rename Field",
            copy: `Current name: ${previousValue}`,
            label: "New Field name",
            initialValue: previousValue,
            submitLabel: "Rename",
          });
          if (!nextValue) {
            fieldSelect.value = previousValue || "";
            return;
          }
          try {
            const result = await renameAssignmentOption("field", previousValue, nextValue);
            const savedValue = String(result.new_value || nextValue).trim();
            insertSortedOption(fieldSelect, savedValue, FIELD_SENTINELS, savedValue);
            if (meetingsByFieldMap[previousValue]) {
              meetingsByFieldMap[savedValue] = [...meetingsByFieldMap[previousValue]];
              delete meetingsByFieldMap[previousValue];
            }
            renderMeetingOptions(savedValue, meetingSelect.value);
            fieldSelect.dataset.previousValue = savedValue;
            meetingSelect.dataset.previousValue = meetingSelect.value;
          } catch (error) {
            await modal.message({
              title: "Rename Field",
              copy: error.message || "Unable to rename the Field.",
            });
            fieldSelect.value = previousValue || "";
          }
          return;
        }
        renderMeetingOptions(fieldSelect.value);
        fieldSelect.dataset.previousValue = fieldSelect.value;
        meetingSelect.dataset.previousValue = meetingSelect.value;
      });

      meetingSelect.addEventListener("change", async () => {
        const previousValue = meetingSelect.dataset.previousValue ?? "";
        if (meetingSelect.value === ADD_MEETING) {
          const nextValue = await modal.prompt({
            title: "Add Meeting",
            copy: "Name the new Meeting. It will be inserted alphabetically into the list.",
            label: "Meeting name",
          });
          if (!nextValue) {
            meetingSelect.value = previousValue || "";
            return;
          }
          try {
            const savedValue = await createAssignmentOption("meeting", nextValue, fieldSelect.value || "");
            if (!allMeetingsList.includes(savedValue)) {
              allMeetingsList.push(savedValue);
              allMeetingsList.sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
            }
            if (fieldSelect.value) {
              const fieldMeetings = meetingsByFieldMap[fieldSelect.value] || [];
              if (!fieldMeetings.includes(savedValue)) {
                fieldMeetings.push(savedValue);
                fieldMeetings.sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
              }
              meetingsByFieldMap[fieldSelect.value] = fieldMeetings;
            }
            insertSortedOption(meetingSelect, savedValue, MEETING_SENTINELS, savedValue);
            meetingSelect.dataset.previousValue = savedValue;
          } catch (error) {
            await modal.message({
              title: "Add Meeting",
              copy: error.message || "Unable to add the new Meeting.",
            });
            meetingSelect.value = previousValue || "";
          }
          return;
        }
        if (meetingSelect.value === DELETE_MEETING) {
          if (!previousValue) {
            await modal.message({
              title: "Delete Meeting",
              copy: "Select a Meeting before using - Meeting.",
            });
            meetingSelect.value = "";
            return;
          }
          const confirmed = await modal.prompt({
            title: "Delete Meeting",
            copy: `Delete the Meeting "${previousValue}"? This only works when no contacts are assigned to that Meeting.`,
            label: "Meeting name",
            requireInput: false,
            submitLabel: "Delete",
          });
          if (!confirmed) {
            meetingSelect.value = previousValue || "";
            return;
          }
          try {
            await removeAssignmentOption("meeting", previousValue);
            const nextAllMeetings = allMeetingsList.filter((item) => item !== previousValue);
            allMeetingsList.splice(0, allMeetingsList.length, ...nextAllMeetings);
            Object.keys(meetingsByFieldMap).forEach((fieldName) => {
              meetingsByFieldMap[fieldName] = (meetingsByFieldMap[fieldName] || []).filter((item) => item !== previousValue);
            });
            renderMeetingOptions(fieldSelect.value, "");
            meetingSelect.dataset.previousValue = "";
          } catch (error) {
            await modal.message({
              title: "Delete Meeting",
              copy: error.message || "Unable to delete the Meeting.",
            });
            meetingSelect.value = previousValue || "";
          }
          return;
        }
        if (meetingSelect.value === RENAME_MEETING) {
          if (!previousValue) {
            await modal.message({
              title: "Rename Meeting",
              copy: "Select a Meeting before using Rename Meeting.",
            });
            meetingSelect.value = "";
            return;
          }
          const nextValue = await modal.prompt({
            title: "Rename Meeting",
            copy: `Current name: ${previousValue}`,
            label: "New Meeting name",
            initialValue: previousValue,
            submitLabel: "Rename",
          });
          if (!nextValue) {
            meetingSelect.value = previousValue || "";
            return;
          }
          try {
            const result = await renameAssignmentOption("meeting", previousValue, nextValue);
            const savedValue = String(result.new_value || nextValue).trim();
            const nextMeetings = allMeetingsList
              .map((item) => (item === previousValue ? savedValue : item))
              .filter((item, index, items) => items.indexOf(item) === index)
              .sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
            allMeetingsList.splice(0, allMeetingsList.length, ...nextMeetings);
            Object.keys(meetingsByFieldMap).forEach((fieldName) => {
              meetingsByFieldMap[fieldName] = (meetingsByFieldMap[fieldName] || [])
                .map((item) => (item === previousValue ? savedValue : item))
                .filter((item, index, items) => items.indexOf(item) === index)
                .sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
            });
            insertSortedOption(meetingSelect, savedValue, MEETING_SENTINELS, savedValue);
            meetingSelect.dataset.previousValue = savedValue;
          } catch (error) {
            await modal.message({
              title: "Rename Meeting",
              copy: error.message || "Unable to rename the Meeting.",
            });
            meetingSelect.value = previousValue || "";
          }
          return;
        }
        meetingSelect.dataset.previousValue = meetingSelect.value;
      });
    },
  };
})();
