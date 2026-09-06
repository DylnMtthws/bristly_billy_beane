(function () {
  "use strict";
  var dialog = document.getElementById("new-deck-dialog");
  if (!dialog) return;
  document.querySelectorAll("[data-new-deck]").forEach(function (button) {
    button.addEventListener("click", function () { dialog.showModal(); });
  });
  var close = dialog.querySelector("[data-dialog-close]");
  if (close) close.addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) dialog.close();
  });

  var search = dialog.querySelector("[data-commander-search]");
  var hidden = dialog.querySelector("[data-commander-id]");
  var results = dialog.querySelector("[data-commander-results]");
  var timer;
  var controller;
  if (!search || !hidden || !results) return;
  search.addEventListener("input", function () {
    hidden.value = "";
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (controller) controller.abort();
      controller = new AbortController();
      fetch("/api/cards?commander=1&q=" + encodeURIComponent(search.value), { signal: controller.signal })
        .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
        .then(function (data) {
          results.replaceChildren();
          data.results.slice(0, 8).forEach(function (card) {
            var button = document.createElement("button");
            button.type = "button";
            var name = document.createElement("strong");
            name.textContent = card.name;
            var type = document.createElement("small");
            type.textContent = card.type_line || "Legendary card";
            button.append(name, type);
            button.addEventListener("click", function () {
              search.value = card.name;
              hidden.value = card.id;
              results.replaceChildren();
            });
            results.appendChild(button);
          });
        })
        .catch(function (error) { if (error.name !== "AbortError") results.textContent = "Search unavailable."; });
    }, 180);
  });
})();
