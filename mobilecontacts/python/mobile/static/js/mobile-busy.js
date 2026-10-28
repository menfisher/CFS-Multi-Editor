(() => {
  const modal = document.getElementById("mobile-busy-modal");
  const titleEl = document.getElementById("mobile-busy-title");
  const messageEl = document.getElementById("mobile-busy-message");
  const errorEl = document.getElementById("mobile-busy-error");
  const spinnerEl = document.getElementById("mobile-busy-spinner");
  const dismissEl = document.getElementById("mobile-busy-dismiss");

  if (!modal || !titleEl || !messageEl || !errorEl || !spinnerEl || !dismissEl) {
    return;
  }

  let autoHideTimer = null;

  const clearAutoHide = () => {
    if (autoHideTimer) {
      window.clearTimeout(autoHideTimer);
      autoHideTimer = null;
    }
  };

  const setBusyMode = (busy, allowDismiss = false) => {
    modal.classList.toggle("mobile-busy-modal-is-busy", busy);
    modal.classList.toggle("mobile-busy-modal-is-result", !busy);
    spinnerEl.hidden = !busy;
    dismissEl.hidden = busy && !allowDismiss;
    errorEl.hidden = true;
    errorEl.textContent = "";
  };

  const openModal = () => {
    clearAutoHide();
    if (!modal.open) {
      modal.showModal();
    }
  };

  const closeModal = () => {
    clearAutoHide();
    if (modal.open) {
      modal.close();
    }
  };

  dismissEl.addEventListener("click", closeModal);

  modal.addEventListener("cancel", (event) => {
    if (modal.classList.contains("mobile-busy-modal-is-busy")) {
      event.preventDefault();
    }
  });

  window.MobileBusy = {
    show({ title = "Working", message = "Please wait…", allowDismiss = false, onDismiss } = {}) {
      setBusyMode(true, allowDismiss);
      titleEl.textContent = title;
      messageEl.textContent = message;
      if (allowDismiss && typeof onDismiss === "function") {
        const handleDismiss = () => {
          dismissEl.removeEventListener("click", handleDismiss);
          onDismiss();
        };
        dismissEl.addEventListener("click", handleDismiss);
      }
      openModal();
      return {
        updateMessage(nextMessage) {
          if (nextMessage) {
            messageEl.textContent = nextMessage;
          }
        },
        fail(errorMessage) {
          setBusyMode(false);
          titleEl.textContent = "Sync failed";
          messageEl.textContent = "Something went wrong while syncing.";
          errorEl.hidden = false;
          errorEl.textContent = errorMessage || "Try again in a moment.";
          dismissEl.hidden = false;
          openModal();
        },
        close: closeModal,
      };
    },
    showResult({ title = "Done", message = "", type = "success", autoHideMs = 3600 } = {}) {
      clearAutoHide();
      setBusyMode(false);
      modal.classList.toggle("mobile-busy-modal-success", type === "success");
      modal.classList.toggle("mobile-busy-modal-error", type !== "success");
      titleEl.textContent = title;
      messageEl.textContent = message;
      dismissEl.hidden = autoHideMs > 0;
      if (!modal.open) {
        modal.showModal();
      }
      if (autoHideMs > 0) {
        autoHideTimer = window.setTimeout(closeModal, autoHideMs);
      }
    },
    close: closeModal,
  };

  window.addEventListener("DOMContentLoaded", () => {
    const logoutLink = document.getElementById("mobile-logout-link");
    if (!logoutLink) {
      return;
    }

    let leaving = false;
    logoutLink.addEventListener("click", (event) => {
      if (leaving) {
        event.preventDefault();
        return;
      }
      leaving = true;
      event.preventDefault();
      logoutLink.classList.add("is-busy");
      logoutLink.setAttribute("aria-busy", "true");
      const multiEditor = logoutLink.dataset.multiEditor === "true";
      window.MobileBusy.show({
        title: "Signing out",
        message: multiEditor
          ? "Saving your work to Google Drive and releasing the Editor lock. Please wait…"
          : "Signing out. Please wait…",
      });
      window.location.assign(logoutLink.getAttribute("href") || "/auth/logout");
    });
  });
})();
