(function () {
  "use strict";
  var filterForm = document.querySelector("[data-library-filters]");
  var deckSearch = document.querySelector("[data-deck-search]");
  var filterTimer;
  if (filterForm && deckSearch) {
    deckSearch.addEventListener("input", function () {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(function () { filterForm.requestSubmit(); }, 400);
    });
    filterForm.addEventListener("submit", function () { clearTimeout(filterTimer); });
  }
  var actionMenus = Array.from(document.querySelectorAll("[data-deck-actions]"));
  var sortMenu = document.querySelector("[data-deck-sort-menu]");
  function menuItems(menu) {
    return Array.from(menu.querySelectorAll("[role='menuitem']"));
  }
  function addMenuKeyboard(menu) {
    var summary = menu.querySelector("summary");
    summary.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      event.stopPropagation();
      menu.open = true;
      var items = menuItems(menu);
      if (items.length) items[event.key === "ArrowDown" ? 0 : items.length - 1].focus();
    });
    menu.addEventListener("keydown", function (event) {
      var items = menuItems(menu);
      var current = items.indexOf(document.activeElement);
      if (event.key === "Escape") {
        event.preventDefault(); menu.open = false; summary.focus(); return;
      }
      if (!items.length || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") current = 0;
      else if (event.key === "End") current = items.length - 1;
      else if (event.key === "ArrowDown") current = (current + 1 + items.length) % items.length;
      else current = (current - 1 + items.length) % items.length;
      items[current].focus();
    });
  }
  if (sortMenu) {
    addMenuKeyboard(sortMenu);
    sortMenu.addEventListener("toggle", function () {
      if (!sortMenu.open) return;
      actionMenus.forEach(function (menu) { menu.open = false; });
    });
  }
  actionMenus.forEach(function (menu) {
    addMenuKeyboard(menu);
    menu.addEventListener("toggle", function () {
      if (!menu.open) return;
      actionMenus.forEach(function (other) { if (other !== menu) other.open = false; });
      if (sortMenu) sortMenu.open = false;
    });
  });
  document.addEventListener("click", function (event) {
    actionMenus.forEach(function (menu) { if (!menu.contains(event.target)) menu.open = false; });
    if (sortMenu && !sortMenu.contains(event.target)) sortMenu.open = false;
  });

  var csrf = document.querySelector("meta[name='csrf-token']");
  document.querySelectorAll("[data-share-deck]").forEach(function (button) {
    button.addEventListener("click", function () {
      var original = button.textContent;
      button.disabled = true;
      fetch("/api/decks/" + encodeURIComponent(button.dataset.deckId) + "/share", {
        method: "POST",
        headers: { "X-CSRFToken": csrf ? csrf.content : "" }
      }).then(function (response) {
        if (!response.ok) throw new Error();
        return response.json();
      }).then(function (body) {
        if (navigator.clipboard) return navigator.clipboard.writeText(body.url).then(function () { return true; }).catch(function () {
          window.prompt("Copy this read-only deck link", body.url);
          return false;
        });
        window.prompt("Copy this read-only deck link", body.url);
        return false;
      }).then(function (copied) {
        if (copied) button.textContent = "Link copied";
      }).catch(function () {
        button.textContent = "Could not share";
      }).finally(function () {
        window.setTimeout(function () { button.textContent = original; button.disabled = false; }, 1800);
      });
    });
  });
  document.querySelectorAll(".dl-deck-delete-form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm("Delete “" + form.dataset.deckTitle + "”? This permanently removes this deck.")) event.preventDefault();
    });
  });

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
