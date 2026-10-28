window.AppModal = (() => {
  const create = ({
    dialog,
    form,
    titleEl,
    copyEl,
    labelEl,
    inputEl,
    errorEl,
    cancelEl,
    saveEl,
  }) => {
    if (!dialog || !form || !titleEl || !copyEl || !labelEl || !inputEl || !errorEl || !saveEl) {
      return {
        prompt: async () => "",
        message: async () => {},
      };
    }

    const fieldWrap = inputEl.closest(".app-modal-field");

    const cleanupStyleState = () => {
      if (fieldWrap) {
        fieldWrap.style.display = "";
      }
      if (cancelEl) {
        cancelEl.style.display = "";
      }
    };

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
      if (fieldWrap) {
        fieldWrap.style.display = requireInput ? "grid" : "none";
      }
      if (cancelEl) {
        cancelEl.style.display = "";
      }
      saveEl.textContent = submitLabel;

      const cleanup = () => {
        form.removeEventListener("submit", handleSubmit);
        dialog.removeEventListener("close", handleClose);
        if (cancelEl) {
          cancelEl.removeEventListener("click", handleCancel);
        }
        cleanupStyleState();
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
      if (cancelEl) {
        cancelEl.addEventListener("click", handleCancel, { once: true });
      }
      dialog.showModal();
      if (requireInput) {
        window.setTimeout(() => inputEl.focus(), 0);
      } else if (cancelEl) {
        window.setTimeout(() => cancelEl.focus(), 0);
      } else {
        window.setTimeout(() => saveEl.focus(), 0);
      }
    });

    const message = ({
      title,
      copy,
      buttonLabel = "OK",
    }) => new Promise((resolve) => {
      titleEl.textContent = title;
      copyEl.textContent = copy;
      labelEl.textContent = "";
      inputEl.value = "";
      errorEl.textContent = "";
      if (fieldWrap) {
        fieldWrap.style.display = "none";
      }
      if (cancelEl) {
        cancelEl.style.display = "none";
      }
      saveEl.textContent = buttonLabel;

      const cleanup = () => {
        form.removeEventListener("submit", handleSubmit);
        dialog.removeEventListener("close", handleClose);
        cleanupStyleState();
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
  };

  return { create };
})();
