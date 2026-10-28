(() => {
  const modal = document.getElementById("mobile-editor-timeout-modal");
  const countdownEl = document.getElementById("mobile-editor-timeout-countdown");
  const extendButton = document.getElementById("mobile-editor-timeout-extend");
  const signoutButton = document.getElementById("mobile-editor-timeout-signout");
  const errorEl = document.getElementById("mobile-editor-timeout-error");

  if (!modal || !countdownEl || !extendButton || !signoutButton || !errorEl) {
    return;
  }

  const warningSeconds = 120;
  const extensionSeconds = 15 * 60;
  let remainingSeconds = null;
  let timeoutShown = false;
  let timeoutArmed = true;
  let signingOut = false;
  let lockWasActive = false;
  let extendInFlight = false;
  let statusPollGeneration = 0;

  const clearError = () => {
    errorEl.hidden = true;
    errorEl.textContent = "";
  };

  const showError = (message) => {
    errorEl.textContent = message || "";
    errorEl.hidden = !message;
  };

  const formatDuration = (seconds) => {
    const safeSeconds = Math.max(0, Number(seconds || 0));
    const minutes = Math.floor(safeSeconds / 60);
    const remainder = Math.floor(safeSeconds % 60);
    return `${minutes}:${String(remainder).padStart(2, "0")}`;
  };

  const submitSignout = () => {
    if (signingOut) return;
    signingOut = true;
    if (window.MobileBusy) {
      window.MobileBusy.show({
        title: "Signing out",
        message: "Releasing the Editor lock. Please wait…",
      });
    }
    window.location.assign("/auth/logout");
  };

  const updateCountdown = () => {
    if (remainingSeconds === null) return;
    countdownEl.textContent = formatDuration(remainingSeconds);
    if (remainingSeconds <= 0) {
      submitSignout();
    }
  };

  const closeTimeoutModal = () => {
    timeoutShown = false;
    clearError();
    if (modal.open) {
      modal.close();
    }
  };

  const showTimeoutModal = () => {
    if (signingOut || extendInFlight) return;
    if (remainingSeconds === null || remainingSeconds > warningSeconds) {
      return;
    }
    timeoutShown = true;
    if (modal.open) return;
    clearError();
    window.MobileBusy?.close?.();
    try {
      modal.showModal();
    } catch (_error) {
      return;
    }
  };

  const syncTimeoutWarning = () => {
    if (remainingSeconds === null || signingOut || extendInFlight) {
      return;
    }
    if (remainingSeconds > warningSeconds) {
      timeoutArmed = true;
      if (timeoutShown || modal.open) {
        closeTimeoutModal();
      }
      return;
    }
    if (timeoutArmed && !timeoutShown) {
      showTimeoutModal();
    }
  };

  const dismissTimeoutModal = (nextRemainingSeconds) => {
    // Ignore any status polls that were already in flight before this extend.
    statusPollGeneration += 1;
    remainingSeconds = Math.max(Number(nextRemainingSeconds || 0), extensionSeconds);
    // Stay disarmed until a later tick/poll sees remaining above the warning window.
    timeoutArmed = false;
    closeTimeoutModal();
    updateCountdown();
  };

  const pollEditorLockStatus = async () => {
    if (extendInFlight || signingOut) return;
    const pollGeneration = statusPollGeneration;
    try {
      const response = await fetch(`/auth/editor/status?_=${Date.now()}`, {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (pollGeneration !== statusPollGeneration) return;
      if (response.status === 401) {
        return;
      }
      if (!response.ok) return;
      const payload = await response.json();
      if (pollGeneration !== statusPollGeneration) return;
      if (!payload.active) {
        const shouldLeave = lockWasActive || remainingSeconds !== null || Boolean(modal.open);
        remainingSeconds = null;
        timeoutArmed = false;
        lockWasActive = false;
        closeTimeoutModal();
        if (shouldLeave && !signingOut) {
          submitSignout();
        }
        return;
      }
      lockWasActive = true;
      remainingSeconds = Number(payload.remaining_seconds || 0);
      updateCountdown();
      syncTimeoutWarning();
    } catch (_error) {
      return;
    }
  };

  modal.addEventListener("cancel", (event) => {
    event.preventDefault();
  });

  extendButton.addEventListener("click", async () => {
    if (extendInFlight || signingOut) return;
    extendInFlight = true;
    clearError();
    extendButton.disabled = true;
    const busy = window.MobileBusy?.show?.({
      title: "Adding Editor Time",
      message: "Adding 15 minutes to the active Editor session.",
    });
    try {
      const response = await fetch("/auth/editor/extend", {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (response.ok && payload.ok) {
        dismissTimeoutModal(payload.remaining_seconds);
        return;
      }
      const statusMessage = {
        failed: "Could not add Editor time on Google Drive. Try again or sign out.",
        lost_lock: "Another Editor now holds the lock. Sign out and check who is editing.",
        not_active: "This Editor session is no longer active. Sign out and sign in again.",
      };
      showError(statusMessage[payload.status] || "Could not add Editor time. Try again or sign out.");
    } catch (_error) {
      showError("Could not add Editor time. Check your connection and try again.");
    } finally {
      extendInFlight = false;
      extendButton.disabled = false;
      busy?.close?.();
      window.MobileBusy?.close?.();
    }
  });

  signoutButton.addEventListener("click", submitSignout);

  pollEditorLockStatus();
  window.setInterval(() => {
    if (remainingSeconds === null || extendInFlight || signingOut) return;
    remainingSeconds -= 1;
    updateCountdown();
    syncTimeoutWarning();
  }, 1000);
  window.setInterval(pollEditorLockStatus, 5000);
  window.addEventListener("focus", pollEditorLockStatus);
  window.addEventListener("pageshow", pollEditorLockStatus);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      pollEditorLockStatus();
    }
  });
})();
