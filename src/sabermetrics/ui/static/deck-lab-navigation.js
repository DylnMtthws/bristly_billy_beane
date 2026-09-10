(function () {
  "use strict";
  var status = document.querySelector("[data-navigation-status]");
  if (!status) return;
  var timer;
  function reset() {
    window.clearTimeout(timer);
    timer = undefined;
    status.hidden = true;
    status.textContent = "";
  }
  function show() {
    reset();
    status.textContent = "Loading…";
    status.hidden = false;
    timer = window.setTimeout(function () {
      status.textContent = "Still loading. You can try the link again.";
    }, 8000);
  }
  window.addEventListener("pageshow", reset);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") reset();
  });
  document.addEventListener("click", function (event) {
    var node = event.target;
    if (node && node.nodeType === 3) node = node.parentElement;
    if (!node || typeof node.closest !== "function") return;
    var link = node.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
        link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
    var target = new URL(link.href, location.href);
    if (target.origin !== location.origin ||
        (target.pathname === location.pathname && target.search === location.search)) return;
    show();
    // Keep normal browser navigation, including Back, keyboard and no-JS use.
  });
})();
