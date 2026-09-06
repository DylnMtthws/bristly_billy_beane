(function () {
  "use strict";
  var trigger = document.querySelector(".dl-menu-button");
  var drawer = document.querySelector(".dl-drawer");
  var backdrop = document.querySelector(".dl-drawer-backdrop");
  if (!trigger || !drawer || !backdrop) return;
  var returnFocus = null;

  function setOpen(open) {
    drawer.classList.toggle("open", open);
    drawer.setAttribute("aria-hidden", open ? "false" : "true");
    backdrop.hidden = !open;
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
    document.documentElement.style.overflow = open ? "hidden" : "";
    if (open) {
      returnFocus = document.activeElement;
      var close = drawer.querySelector("[data-drawer-close]");
      if (close) close.focus();
    } else if (returnFocus && returnFocus.focus) {
      returnFocus.focus();
    }
  }
  trigger.addEventListener("click", function () { setOpen(true); });
  document.querySelectorAll("[data-drawer-close]").forEach(function (el) {
    el.addEventListener("click", function () { setOpen(false); });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && drawer.classList.contains("open")) setOpen(false);
    if (event.key !== "Tab" || !drawer.classList.contains("open")) return;
    var focusable = Array.from(drawer.querySelectorAll("a,button,[tabindex]:not([tabindex='-1'])"));
    if (!focusable.length) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  var csrf = document.querySelector("meta[name='csrf-token']");
  document.querySelectorAll("[data-fav-commander]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      event.preventDefault();
      button.disabled = true;
      fetch("/favorites/commander/" + encodeURIComponent(button.dataset.id) + "/toggle", {
        method: "POST",
        headers: { "X-CSRFToken": csrf ? csrf.content : "", "X-Requested-With": "XMLHttpRequest" }
      }).then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
        .then(function (data) { button.classList.toggle("active", !!data.favorited); button.setAttribute("aria-pressed", data.favorited ? "true" : "false"); })
        .finally(function () { button.disabled = false; });
    });
  });
})();
