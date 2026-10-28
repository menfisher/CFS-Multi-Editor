(function () {
  const DIALOG_ID = "mobile-photo-lightbox";
  const IMAGE_ID = "mobile-photo-lightbox-image";
  const CLOSE_ID = "mobile-photo-lightbox-close";

  function bindDialog(dialog) {
    if (dialog.dataset.mobilePhotoLightboxBound === "1") {
      return;
    }
    dialog.dataset.mobilePhotoLightboxBound = "1";
    const closeButton = dialog.querySelector(`#${CLOSE_ID}`);
    const close = () => {
      if (dialog.open) {
        dialog.close();
      }
    };
    closeButton?.addEventListener("click", close);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        close();
      }
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });
  }

  function ensureDialog() {
    let dialog = document.getElementById(DIALOG_ID);
    if (dialog) {
      bindDialog(dialog);
      return dialog;
    }
    dialog = document.createElement("dialog");
    dialog.id = DIALOG_ID;
    dialog.className = "mobile-photo-lightbox";
    dialog.setAttribute("aria-label", "Contact photo");
    dialog.innerHTML = `
      <button type="button" class="mobile-photo-lightbox-close" id="${CLOSE_ID}" aria-label="Close photo">&times;</button>
      <img class="mobile-photo-lightbox-image" id="${IMAGE_ID}" alt="" referrerpolicy="no-referrer">
    `;
    document.body.appendChild(dialog);
    bindDialog(dialog);
    return dialog;
  }

  function bindTrigger(trigger) {
    if (trigger.dataset.mobilePhotoLightboxBound === "1") {
      return;
    }
    trigger.dataset.mobilePhotoLightboxBound = "1";
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const src =
        trigger.dataset.photoSrc ||
        trigger.dataset.photoLarge ||
        trigger.querySelector("img")?.src ||
        "";
      const alt = trigger.dataset.photoAlt || trigger.dataset.photoLabel || "";
      window.MobilePhotoLightbox.open(src, alt);
    });
  }

  window.MobilePhotoLightbox = {
    open(src, alt = "") {
      const normalizedSrc = String(src || "").trim();
      if (!normalizedSrc) {
        return;
      }
      const dialog = ensureDialog();
      const image = dialog.querySelector(`#${IMAGE_ID}`);
      if (image) {
        image.src = normalizedSrc;
        image.alt = alt;
      }
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      }
    },
    init(root = document) {
      ensureDialog();
      root.querySelectorAll("[data-mobile-photo-lightbox]").forEach(bindTrigger);
      root.querySelectorAll(".mobile-list-avatar-photo").forEach((image) => {
        if (image.dataset.mobilePhotoLightboxBound === "1") {
          return;
        }
        image.dataset.mobilePhotoLightboxBound = "1";
        image.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          window.MobilePhotoLightbox.open(
            image.dataset.photoLarge || image.src,
            image.dataset.photoLabel || "",
          );
        });
      });
    },
  };

  document.addEventListener("DOMContentLoaded", () => {
    window.MobilePhotoLightbox.init();
  });
})();
