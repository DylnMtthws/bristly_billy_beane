(function () {
  "use strict";
  var status = document.querySelector("[data-navigation-status]");
  if (!status) return;
  var timer;
  function reset() {
    window.clearTimeout(timer);
    status.hidden = true;
    status.textContent = "";
  }
  window.addEventListener("pageshow", reset);
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
        link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
    var target = new URL(link.href, location.href);
    if (target.origin !== location.origin ||
        (target.pathname === location.pathname && target.search === location.search)) return;
    reset();
    status.textContent = "Loading…";
    status.hidden = false;
    timer = window.setTimeout(function () {
      status.textContent = "Still loading. You can try the link again.";
    }, 8000);
    // Keep normal browser navigation, including Back, keyboard and no-JS use.
  });
})();
