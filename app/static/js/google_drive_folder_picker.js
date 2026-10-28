(function () {
  const PICKER_CONFIG_URL = "/google/picker/config";
  const PICKER_VERIFY_FOLDER_URL = "/google/picker/verify-folder";
  const COOKIE_PICKER_HELP = "If Google says it cannot access your account, close the Google window, allow third-party cookies for ContactsFreeShare, then choose the folder again.";

  let pickerApiPromise = null;
  let loadedApiKey = "";

  const byId = (id) => document.getElementById(id);

  const setStatus = (statusElement, message) => {
    if (!statusElement) return;
    statusElement.textContent = message || "";
  };

  const pickerReasonMessage = (reason, payload = {}) => {
    if (reason === "not_connected") {
      return "Connect Google before choosing a Drive folder.";
    }
    if (reason === "drive_scope_missing" || reason === "picker_scope_missing") {
      return payload.message || "Reconnect Google on the Google Connect page to grant Drive folder access, then try again.";
    }
    if (reason === "picker_not_configured") {
      return "Google Picker is not configured for this app package. Set GOOGLE_PICKER_API_KEY and GOOGLE_PICKER_APP_ID in .env.";
    }
    return "";
  };

  const picker403Help = (config = {}) => {
    const origin = config.origin || window.location.origin;
    const referrerPattern = config.referrer_pattern || `${origin}/*`;
    return [
      "Google returned 403 (forbidden) for the folder picker.",
      `In Google Cloud Console, enable the Google Picker API for project ${config.app_id || "your project"}.`,
      `Edit GOOGLE_PICKER_API_KEY restrictions: allow HTTP referrer ${referrerPattern}.`,
      "If you open the app at localhost, also allow http://localhost:8000/*.",
      "Confirm GOOGLE_PICKER_APP_ID is your Google Cloud project number, then restart the app.",
    ].join(" ");
  };

  const ensureGapiScript = (apiKey) => {
    const normalizedKey = String(apiKey || "").trim();
    if (!normalizedKey) {
      return Promise.reject(new Error("Google Picker API key is missing."));
    }
    const existing = document.querySelector('script[data-google-picker-gapi="1"]');
    if (existing && existing.dataset.apiKey === normalizedKey) {
      return Promise.resolve();
    }
    if (existing) {
      existing.remove();
      pickerApiPromise = null;
      delete window.gapi;
      if (window.google) {
        delete window.google.picker;
      }
    }
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `https://apis.google.com/js/api.js?key=${encodeURIComponent(normalizedKey)}`;
      script.async = true;
      script.defer = true;
      script.dataset.googlePickerGapi = "1";
      script.dataset.apiKey = normalizedKey;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("Google API script could not be loaded. Check GOOGLE_PICKER_API_KEY and HTTP referrer restrictions."));
      document.head.appendChild(script);
    });
  };

  const loadPickerApi = (apiKey) => {
    const normalizedKey = String(apiKey || "").trim();
    if (window.google?.picker && loadedApiKey === normalizedKey) {
      return Promise.resolve();
    }
    if (pickerApiPromise && loadedApiKey === normalizedKey) {
      return pickerApiPromise;
    }
    loadedApiKey = normalizedKey;
    pickerApiPromise = ensureGapiScript(normalizedKey).then(
      () =>
        new Promise((resolve, reject) => {
          const startedAt = Date.now();
          const waitForGapi = () => {
            if (window.google?.picker) {
              resolve();
              return;
            }
            if (!window.gapi?.load) {
              if (Date.now() - startedAt > 10000) {
                reject(new Error("Google Picker library could not be loaded."));
                return;
              }
              window.setTimeout(waitForGapi, 100);
              return;
            }
            window.gapi.load("picker", {
              callback: resolve,
              onerror: () => reject(new Error("Google Picker library could not be loaded.")),
              timeout: 10000,
              ontimeout: () => reject(new Error("Google Picker library timed out.")),
            });
          };
          waitForGapi();
        })
    );
    return pickerApiPromise;
  };

  const fetchPickerConfig = async () => {
    const params = new URLSearchParams({ origin: window.location.origin });
    const response = await fetch(`${PICKER_CONFIG_URL}?${params.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      const reason = payload.reason || "unknown";
      const reasonMessage = pickerReasonMessage(reason, payload);
      if (reasonMessage) {
        throw new Error(reasonMessage);
      }
      throw new Error(payload.message || "Google Picker could not be opened.");
    }
    return payload;
  };

  const verifyPickedFolder = async (folderId) => {
    const params = new URLSearchParams({ folder_id: folderId });
    const response = await fetch(`${PICKER_VERIFY_FOLDER_URL}?${params.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      const reason = payload.reason || "unknown";
      const reasonMessage = pickerReasonMessage(reason, payload);
      throw new Error(payload.message || reasonMessage || "Selected folder could not be verified.");
    }
    return payload;
  };

  const applyFolderSelection = (doc, idInput, nameInput, statusElement) => {
    const folderId = doc[google.picker.Document.ID] || doc.id || "";
    const folderName = doc[google.picker.Document.NAME] || doc.name || "";
    if (!folderId) {
      throw new Error("Google did not return a folder ID.");
    }
    idInput.value = folderId;
    idInput.dispatchEvent(new Event("input", { bubbles: true }));
    idInput.dispatchEvent(new Event("change", { bubbles: true }));
    if (nameInput && folderName) {
      nameInput.value = folderName;
      nameInput.dispatchEvent(new Event("input", { bubbles: true }));
      nameInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
    setStatus(statusElement, folderName ? `Selected ${folderName}.` : "Folder selected.");
  };

  const describePickerError = (data, config) => {
    const errorCode = data[google.picker.Response.ERROR];
    if (!errorCode) {
      return "Google Picker returned an error.";
    }
    if (String(errorCode).includes("403")) {
      return picker403Help(config);
    }
    return `Google Picker error: ${errorCode}. ${picker403Help(config)}`;
  };

  const handlePickedFolder = async (doc, idInput, nameInput, statusElement) => {
    const folderId = doc[google.picker.Document.ID] || doc.id || "";
    if (!folderId) {
      setStatus(statusElement, "No folder was selected.");
      return;
    }
    setStatus(statusElement, "Verifying folder access...");
    try {
      const verified = await verifyPickedFolder(folderId);
      applyFolderSelection(
        {
          [google.picker.Document.ID]: verified.folder_id || folderId,
          [google.picker.Document.NAME]: verified.folder_name || doc[google.picker.Document.NAME] || doc.name || "",
        },
        idInput,
        nameInput,
        statusElement
      );
    } catch (error) {
      setStatus(statusElement, error.message || "Selected folder could not be verified.");
    }
  };

  const openFolderPicker = async (button, idInput, nameInput, statusElement) => {
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Opening...";
    setStatus(statusElement, "");
    let config = null;
    try {
      config = await fetchPickerConfig();
      await loadPickerApi(config.api_key);
      const myDriveFoldersView = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setSelectFolderEnabled(true)
        .setIncludeFolders(true)
        .setMimeTypes("application/vnd.google-apps.folder")
        .setOwnedByMe(true)
        .setMode(google.picker.DocsViewMode.LIST);
      const sharedWithMeFoldersView = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setSelectFolderEnabled(true)
        .setIncludeFolders(true)
        .setMimeTypes("application/vnd.google-apps.folder")
        .setOwnedByMe(false)
        .setMode(google.picker.DocsViewMode.LIST);
      const sharedDrivesFoldersView = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setSelectFolderEnabled(true)
        .setIncludeFolders(true)
        .setMimeTypes("application/vnd.google-apps.folder")
        .setEnableDrives(true)
        .setMode(google.picker.DocsViewMode.LIST);
      const pickerOrigin = window.location.origin || config.origin;
      const pickerBuilder = new google.picker.PickerBuilder()
        .addView(myDriveFoldersView)
        .addView(sharedWithMeFoldersView)
        .addView(sharedDrivesFoldersView)
        .setOAuthToken(config.access_token)
        .setDeveloperKey(config.api_key)
        .setAppId(config.app_id)
        .setOrigin(pickerOrigin)
        .setTitle("Choose shared PDF folder")
        .setCallback((data) => {
          const action = data[google.picker.Response.ACTION];
          if (action === google.picker.Action.CANCEL) {
            setStatus(statusElement, "");
            return;
          }
          if (data[google.picker.Response.ERROR]) {
            setStatus(statusElement, describePickerError(data, config));
            return;
          }
          if (action !== google.picker.Action.PICKED) {
            return;
          }
          const docs = data[google.picker.Response.DOCUMENTS] || [];
          if (!docs.length) {
            setStatus(statusElement, "No folder was selected.");
            return;
          }
          handlePickedFolder(docs[0], idInput, nameInput, statusElement);
        });
      if (google.picker.Feature?.SUPPORT_DRIVES) {
        pickerBuilder.enableFeature(google.picker.Feature.SUPPORT_DRIVES);
      }
      pickerBuilder.build().setVisible(true);
      setStatus(statusElement, COOKIE_PICKER_HELP);
    } catch (error) {
      const message = error.message || "Google Picker could not be opened.";
      const help = config ? picker403Help(config) : picker403Help({ origin: window.location.origin });
      setStatus(statusElement, `${message} ${help}`);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  };

  window.addEventListener("DOMContentLoaded", () => {
    const button = byId("address-book-pdf-share-folder-picker");
    const idInput = byId("address-book-pdf-share-folder-id");
    const nameInput = byId("address-book-pdf-share-folder-name");
    const statusElement = byId("address-book-pdf-share-folder-picker-status");
    if (!button || !idInput) return;
    button.addEventListener("click", () => {
      openFolderPicker(button, idInput, nameInput, statusElement);
    });
  });
})();
