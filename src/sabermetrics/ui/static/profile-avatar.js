(function () {
  "use strict";
  var form = document.querySelector("[data-avatar-form]");
  if (!form) return;
  var preview = form.querySelector("[data-avatar-preview] .account-avatar");
  var saved = preview.cloneNode(true);
  var file = form.querySelector("#avatar-image");
  var emoji = form.querySelector("#avatar-emoji");
  var status = form.querySelector("[data-avatar-status]");
  var objectUrl = null;
  function render() {
    var kind = form.querySelector("[name=avatar_kind]:checked").value;
    form.querySelectorAll("[data-avatar-panel]").forEach(function (panel) { panel.hidden = panel.dataset.avatarPanel !== kind; });
    preview.replaceChildren();
    status.textContent = "Changes are saved when you choose Save picture.";
    if (kind === "emoji") preview.textContent = emoji.value || "🎣";
    if (kind === "icon") {
      var selected = form.querySelector("[name=avatar_icon]:checked");
      if (selected) preview.appendChild(selected.parentElement.querySelector("svg").cloneNode(true));
    }
    if (kind === "image") {
      if (objectUrl) {
        var img = document.createElement("img"); img.alt = ""; img.src = objectUrl; preview.appendChild(img);
      } else if (saved.querySelector("img")) preview.appendChild(saved.querySelector("img").cloneNode(true));
      else preview.textContent = "🎣";
    }
  }
  form.querySelectorAll("[name=avatar_kind],[name=avatar_icon]").forEach(function (input) { input.addEventListener("change", render); });
  form.querySelectorAll("[data-avatar-emoji]").forEach(function (button) { button.addEventListener("click", function () { emoji.value = button.dataset.avatarEmoji; render(); }); });
  emoji.addEventListener("input", render);
  file.addEventListener("change", function () {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    if (file.files[0]) {
      if (file.files[0].size > 10000000) { file.value = ""; render(); status.textContent = "Choose an image no larger than 10 MB."; return; }
      objectUrl = URL.createObjectURL(file.files[0]);
    }
    render();
  });
  window.addEventListener("pagehide", function () { if (objectUrl) URL.revokeObjectURL(objectUrl); });
  render();
})();
