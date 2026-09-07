(function () {
  "use strict";
  var dialog = document.getElementById("invite-dialog");
  document.querySelectorAll("[data-invite-open]").forEach(function (button) {
    button.addEventListener("click", function () { if (dialog) dialog.showModal(); });
  });
  var close = document.querySelector("[data-invite-close]");
  if (close) close.addEventListener("click", function () { dialog.close(); });
  var copy = document.querySelector("[data-copy-invite]");
  if (copy) copy.addEventListener("click", function () {
    var value = document.querySelector("[data-invite-link]").textContent;
    if (navigator.clipboard) navigator.clipboard.writeText(value);
    copy.textContent = "Copied";
  });
})();
