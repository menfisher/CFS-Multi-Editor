(function () {
  function resolvedLabelValue(select) {
    if (!select) {
      return "";
    }
    const value = select.value;
    if (value === "__blank__" || value === "") {
      return "";
    }
    return value;
  }

  function syncTypeControl(control) {
    const select = control?.querySelector("[data-contact-type-select]");
    const customInput = control?.querySelector("[data-contact-type-custom]");
    const hiddenInput = control?.querySelector("[data-contact-type-hidden]");
    if (!select || !customInput || !hiddenInput) {
      return;
    }

    const value = resolvedLabelValue(select);
    if (value === "Custom") {
      customInput.disabled = false;
      hiddenInput.value = customInput.value.trim() || "Custom";
      return;
    }

    customInput.disabled = true;
    customInput.value = "";
    hiddenInput.value = value;
  }

  function bindTypeControl(control) {
    if (!control || control.dataset.contactTypeBound === "true") {
      return;
    }
    control.dataset.contactTypeBound = "true";

    const select = control.querySelector("[data-contact-type-select]");
    const customInput = control.querySelector("[data-contact-type-custom]");

    const handleSelectUpdate = () => {
      syncTypeControl(control);
      window.setTimeout(() => syncTypeControl(control), 50);
      if (resolvedLabelValue(select) === "Custom") {
        customInput?.focus();
      }
    };

    select?.addEventListener("change", handleSelectUpdate);
    select?.addEventListener("blur", handleSelectUpdate);
    customInput?.addEventListener("input", () => syncTypeControl(control));
    syncTypeControl(control);
  }

  function bindTypeControls(root = document) {
    root.querySelectorAll("[data-contact-type-control]").forEach(bindTypeControl);
  }

  window.MobileEditLabels = {
    bindTypeControls,
    syncTypeControl,
  };
})();
