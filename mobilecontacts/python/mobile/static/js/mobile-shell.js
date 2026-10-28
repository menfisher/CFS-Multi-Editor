window.addEventListener("DOMContentLoaded", () => {
  const nav = document.getElementById("mobile-bottom-nav");
  const shell = document.body;
  if (!nav || !shell || !shell.classList.contains("mobile-shell")) {
    return;
  }

  const safariBottomInset = () => {
    const viewport = window.visualViewport;
    if (!viewport) {
      return 0;
    }
    return Math.max(0, Math.round(window.innerHeight - viewport.height - viewport.offsetTop));
  };

  const pinBottomNav = () => {
    nav.style.bottom = `${safariBottomInset()}px`;
  };

  const syncNavSpace = () => {
    shell.style.setProperty("--mobile-nav-space", `${nav.offsetHeight}px`);
    nav.classList.remove("is-hidden");
    shell.classList.remove("mobile-shell-nav-hidden");
  };

  const refreshLayout = () => {
    pinBottomNav();
    syncNavSpace();
  };

  refreshLayout();
  window.addEventListener("resize", refreshLayout);

  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", refreshLayout);
    window.visualViewport.addEventListener("scroll", refreshLayout);
  }
});
