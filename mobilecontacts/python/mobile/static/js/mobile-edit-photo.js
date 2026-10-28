(function () {
  function initMobileEditPhoto() {
    const photoPreview = document.getElementById("mobile-edit-photo-preview");
    const photoFileInput = document.querySelector('input[name="contact_photo_upload"]');
    const croppedPhotoInput = document.getElementById("mobile-cropped-contact-photo");
    const photoCropModal = document.getElementById("mobile-contact-photo-crop-modal");
    const photoCropCanvas = document.getElementById("mobile-contact-photo-crop-canvas");
    const photoCropZoom = document.getElementById("mobile-contact-photo-crop-zoom");
    const photoCropCancel = document.getElementById("mobile-contact-photo-crop-cancel");
    const photoCropApply = document.getElementById("mobile-contact-photo-crop-apply");
    if (!photoPreview || !photoFileInput) {
      return;
    }

    const cropState = {
      image: null,
      scale: 1,
      minScale: 1,
      offsetX: 0,
      offsetY: 0,
      dragging: false,
      lastX: 0,
      lastY: 0,
    };
    const cropContext = photoCropCanvas?.getContext("2d");
    const cropSize = () => photoCropCanvas?.width || 320;
    const initialPhotoPreviewHtml = photoPreview.innerHTML;

    const setPhotoPreviewImage = (src) => {
      if (!src) {
        return;
      }
      const image = document.createElement("img");
      image.className = "mobile-edit-photo-image";
      image.alt = "";
      image.loading = "eager";
      image.referrerPolicy = "no-referrer";
      image.src = src;
      if (window.MobilePhotoLightbox) {
        image.dataset.mobilePhotoLightbox = "1";
        image.dataset.photoSrc = src;
      }
      photoPreview.replaceChildren(image);
      window.MobilePhotoLightbox?.init(photoPreview);
    };

    const resetPhotoPreview = () => {
      photoPreview.innerHTML = initialPhotoPreviewHtml;
      window.MobilePhotoLightbox?.init(photoPreview);
    };

    const clampCropOffsets = () => {
      if (!cropState.image) {
        return;
      }
      const size = cropSize();
      const drawWidth = cropState.image.width * cropState.scale;
      const drawHeight = cropState.image.height * cropState.scale;
      const minX = Math.min(0, size - drawWidth);
      const minY = Math.min(0, size - drawHeight);
      cropState.offsetX = Math.max(minX, Math.min(0, cropState.offsetX));
      cropState.offsetY = Math.max(minY, Math.min(0, cropState.offsetY));
    };

    const drawCropCanvas = () => {
      if (!cropContext || !photoCropCanvas || !cropState.image) {
        return;
      }
      const size = cropSize();
      clampCropOffsets();
      cropContext.clearRect(0, 0, size, size);
      cropContext.fillStyle = "#111";
      cropContext.fillRect(0, 0, size, size);
      cropContext.drawImage(
        cropState.image,
        cropState.offsetX,
        cropState.offsetY,
        cropState.image.width * cropState.scale,
        cropState.image.height * cropState.scale,
      );
      cropContext.strokeStyle = "rgba(255, 255, 255, 0.9)";
      cropContext.lineWidth = 2;
      cropContext.strokeRect(1, 1, size - 2, size - 2);
    };

    const openPhotoCropper = (file) => {
      if (!file || !file.type.startsWith("image/")) {
        return;
      }
      if (!photoCropModal || !photoCropCanvas || !photoCropZoom || !croppedPhotoInput) {
        const previewUrl = URL.createObjectURL(file);
        setPhotoPreviewImage(previewUrl);
        window.setTimeout(() => URL.revokeObjectURL(previewUrl), 1000);
        return;
      }
      const image = new Image();
      image.onload = () => {
        URL.revokeObjectURL(image.src);
        const size = cropSize();
        cropState.image = image;
        cropState.minScale = Math.max(size / image.width, size / image.height);
        cropState.scale = cropState.minScale;
        cropState.offsetX = (size - image.width * cropState.scale) / 2;
        cropState.offsetY = (size - image.height * cropState.scale) / 2;
        photoCropZoom.min = String(cropState.minScale);
        photoCropZoom.max = String(cropState.minScale * 3);
        photoCropZoom.step = "0.01";
        photoCropZoom.value = String(cropState.scale);
        croppedPhotoInput.value = "";
        drawCropCanvas();
        if (typeof photoCropModal.showModal === "function") {
          photoCropModal.showModal();
        }
      };
      image.src = URL.createObjectURL(file);
    };

    photoFileInput.addEventListener("change", () => {
      const file = photoFileInput.files?.[0];
      if (file) {
        openPhotoCropper(file);
        return;
      }
      if (croppedPhotoInput) {
        croppedPhotoInput.value = "";
      }
    });

    photoCropZoom?.addEventListener("input", () => {
      if (!cropState.image || !photoCropZoom) {
        return;
      }
      const previousScale = cropState.scale;
      const nextScale = Number(photoCropZoom.value || previousScale);
      const size = cropSize();
      const centerX = size / 2;
      const centerY = size / 2;
      cropState.offsetX = centerX - ((centerX - cropState.offsetX) * nextScale) / previousScale;
      cropState.offsetY = centerY - ((centerY - cropState.offsetY) * nextScale) / previousScale;
      cropState.scale = nextScale;
      drawCropCanvas();
    });

    photoCropCanvas?.addEventListener("pointerdown", (event) => {
      if (!cropState.image) {
        return;
      }
      cropState.dragging = true;
      cropState.lastX = event.clientX;
      cropState.lastY = event.clientY;
      photoCropCanvas.setPointerCapture(event.pointerId);
    });

    photoCropCanvas?.addEventListener("pointermove", (event) => {
      if (!cropState.dragging) {
        return;
      }
      cropState.offsetX += event.clientX - cropState.lastX;
      cropState.offsetY += event.clientY - cropState.lastY;
      cropState.lastX = event.clientX;
      cropState.lastY = event.clientY;
      drawCropCanvas();
    });

    photoCropCanvas?.addEventListener("pointerup", (event) => {
      cropState.dragging = false;
      photoCropCanvas.releasePointerCapture(event.pointerId);
    });

    photoCropCanvas?.addEventListener("pointercancel", () => {
      cropState.dragging = false;
    });

    photoCropCancel?.addEventListener("click", () => {
      photoFileInput.value = "";
      if (croppedPhotoInput) {
        croppedPhotoInput.value = "";
      }
      resetPhotoPreview();
      photoCropModal?.close();
    });

    photoCropApply?.addEventListener("click", () => {
      if (!photoCropCanvas || !croppedPhotoInput || !cropState.image) {
        return;
      }
      croppedPhotoInput.value = photoCropCanvas.toDataURL("image/jpeg", 0.9);
      setPhotoPreviewImage(croppedPhotoInput.value);
      photoCropModal?.close();
    });

    window.MobilePhotoLightbox?.init(photoPreview);
  }

  document.addEventListener("DOMContentLoaded", initMobileEditPhoto);
})();
