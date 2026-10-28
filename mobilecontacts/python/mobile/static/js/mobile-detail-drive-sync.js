(function () {
  function showSection(section, valueEl, text) {
    const normalized = String(text || "").trim();
    if (!section || !valueEl || !normalized) {
      return;
    }
    valueEl.textContent = normalized;
    section.hidden = false;
  }

  function ensurePhoto(slot, photo, photoLarge, alt) {
    const normalizedPhoto = String(photo || "").trim();
    if (!slot || !normalizedPhoto) {
      return;
    }
    let trigger = slot.querySelector("[data-mobile-photo-lightbox]");
    if (trigger) {
      const image = trigger.querySelector("img");
      if (image && image.getAttribute("src") !== normalizedPhoto) {
        image.setAttribute("src", normalizedPhoto);
      }
      trigger.dataset.photoSrc = String(photoLarge || normalizedPhoto);
      trigger.dataset.photoAlt = alt;
      return;
    }
    trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "mobile-avatar-button";
    trigger.setAttribute("data-mobile-photo-lightbox", "");
    trigger.dataset.photoSrc = String(photoLarge || normalizedPhoto);
    trigger.dataset.photoAlt = alt;
    trigger.setAttribute("aria-label", "View larger photo");
    const image = document.createElement("img");
    image.className = "mobile-avatar";
    image.setAttribute("src", normalizedPhoto);
    image.setAttribute("alt", "");
    image.setAttribute("loading", "eager");
    image.setAttribute("referrerpolicy", "no-referrer");
    trigger.appendChild(image);
    slot.appendChild(trigger);
    if (window.MobilePhotoLightbox) {
      window.MobilePhotoLightbox.init(slot);
    }
  }

  function applyDriveAppFields(data, contactLabel) {
    showSection(
      document.getElementById("mobile-detail-birthday"),
      document.getElementById("mobile-detail-birthday-value"),
      data.birthday,
    );
    showSection(
      document.getElementById("mobile-detail-mtg-home-elder"),
      document.getElementById("mobile-detail-mtg-home-elder-value"),
      data.mtg_home_elder_flag,
    );
    showSection(
      document.getElementById("mobile-detail-meets-in-state"),
      document.getElementById("mobile-detail-meets-in-state-value"),
      data.meets_in_state,
    );
    ensurePhoto(
      document.getElementById("mobile-detail-photo-slot"),
      data.photo,
      data.photo_large,
      contactLabel,
    );
  }

  function syncDriveAppFields(contactId, contactLabel) {
    if (!contactId) {
      return;
    }
    fetch(`/m/contacts/${contactId}/drive-app-fields`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
      },
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data || !data.ok) {
          return;
        }
        applyDriveAppFields(data, contactLabel);
      })
      .catch(() => {});
  }

  window.MobileDetailDriveSync = {
    init(contactId, contactLabel, needsDriveSync) {
      if (needsDriveSync === false) {
        return;
      }
      syncDriveAppFields(contactId, contactLabel);
    },
  };
})();
