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
  document.querySelectorAll("[data-quota-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var label = form.querySelector("[data-save-label]");
      label.textContent = "Saving…";
      fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "XMLHttpRequest" }
      }).then(function (response) {
        return response.json().then(function (body) { if (!response.ok) throw new Error(body.error); return body; });
      }).then(function () { label.textContent = "Saved"; })
        .catch(function (error) { label.textContent = error.message || "Error"; });
    });
  });
})();
