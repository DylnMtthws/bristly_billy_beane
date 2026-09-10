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
  if (dialog) {
    document.querySelectorAll("[data-new-deck]").forEach(function (button) {
      button.addEventListener("click", function () { dialog.showModal(); });
    });
    var close = dialog.querySelector("[data-dialog-close]");
    if (close) close.addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  }

  var importDialog = document.getElementById("import-deck-dialog");
  var importForm = importDialog ? importDialog.querySelector("[data-import-form]") : null;
  if (importDialog && importForm) {
    var importText = importForm.querySelector("[data-import-text]");
    var importErrors = importForm.querySelector("[data-import-errors]");
    var importPreview = importForm.querySelector("[data-import-preview]");
    var importConfirm = importForm.querySelector("[data-import-confirm]");
    var importPending = false, importSequence = 0;
    function selectedCommanderIds() {
      return Array.from(importForm.querySelectorAll("input[name='commander_card_ids']:checked, input[name='commander_card_ids'][type=hidden]")).map(function (input) {
        return input.value;
      });
    }
    function showErrors(errors) {
      importErrors.replaceChildren();
      (errors || []).forEach(function (error) {
        var p = document.createElement("p");
        p.textContent = (error.line ? "Line " + error.line + ": " : "") + (error.message || "This decklist could not be imported.");
        importErrors.appendChild(p);
      });
      importErrors.hidden = !importErrors.childElementCount;
    }
    function renderPreview(preview) {
      importPreview.replaceChildren();
      if (!preview) {
        importPreview.hidden = true;
        importConfirm.hidden = true;
        return;
      }
      function addList(title, items, nameKey) {
        if (!items || !items.length) return;
        var section = document.createElement("section");
        var heading = document.createElement("h3");
        heading.textContent = title;
        var list = document.createElement("ul");
        items.forEach(function (item) {
          var li = document.createElement("li");
          li.textContent = (item.quantity ? item.quantity + " " : "") + (item[nameKey] || item.name || "");
          list.appendChild(li);
        });
        section.appendChild(heading);
        section.appendChild(list);
        importPreview.appendChild(section);
      }
      if (preview.warnings && preview.warnings.length) {
        var warnings = document.createElement("ul");
        warnings.className = "dl-import-warnings";
        preview.warnings.forEach(function (warning) {
          var li = document.createElement("li");
          li.textContent = warning;
          warnings.appendChild(li);
        });
        importPreview.appendChild(warnings);
      }
      (preview.commanders || []).forEach(function (card) {
        var selected = document.createElement("input");
        selected.type = "hidden"; selected.name = "commander_card_ids";
        selected.value = card.card_id; importPreview.appendChild(selected);
      });
      addList("Commanders", preview.commanders, "name");
      (preview.zones || []).forEach(function (zone) {
        addList(zone.name, zone.cards, "name");
      });
      if (preview.validation && preview.validation.issues && preview.validation.issues.length) {
        var issues = document.createElement("ul");
        issues.className = "dl-import-issues";
        preview.validation.issues.forEach(function (issue) {
          var li = document.createElement("li");
          li.textContent = issue;
          issues.appendChild(li);
        });
        importPreview.appendChild(issues);
      }
      if (preview.needs_commander_selection && preview.eligible_commanders && preview.eligible_commanders.length) {
        var fieldset = document.createElement("fieldset");
        fieldset.className = "dl-import-commanders";
        var legend = document.createElement("legend");
        legend.textContent = "Choose commanders from this list";
        fieldset.appendChild(legend);
        preview.eligible_commanders.forEach(function (card) {
          var label = document.createElement("label");
          var input = document.createElement("input");
          input.type = "checkbox";
          input.name = "commander_card_ids";
          input.value = card.id;
          label.appendChild(input);
          label.appendChild(document.createTextNode(" " + card.name));
          fieldset.appendChild(label);
        });
        importPreview.appendChild(fieldset);
      }
      importPreview.hidden = false;
      importConfirm.hidden = false;
    }
    function postImport(url) {
      var payload = {
        title: (importForm.querySelector("[name='title']") || {}).value || "",
        text: importText ? importText.value : "",
        commander_card_ids: selectedCommanderIds()
      };
      return fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": csrf ? csrf.content : ""
        },
        body: JSON.stringify(payload)
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          body.status = response.status;
          body.ok = response.ok;
          return body;
        });
      });
    }
    document.querySelectorAll("[data-import-deck]").forEach(function (button) {
      button.addEventListener("click", function () { importDialog.showModal(); });
    });
    var importClose = importDialog.querySelector("[data-dialog-close]");
    if (importClose) importClose.addEventListener("click", function () { importDialog.close(); });
    importDialog.addEventListener("click", function (event) {
      if (event.target === importDialog) importDialog.close();
    });
    if (importText) {
      importText.addEventListener("input", function () {
        importSequence += 1;
        showErrors([]);
        if (!importPreview.hasAttribute("data-server-preview")) {
          renderPreview(null);
        }
      });
    }
    if (importDialog.hasAttribute("open")) {
      importDialog.removeAttribute("open");
      importDialog.showModal();
    }
    importForm.addEventListener("submit", function (event) {
      if (!window.fetch) return;
      event.preventDefault();
      if (importPending) return;
      importPending = true;
      var requestSequence = ++importSequence;
      var submitButtons = Array.from(importForm.querySelectorAll("button[type=submit]"));
      submitButtons.forEach(function (button) { button.disabled = true; });
      var confirmClicked = event.submitter && event.submitter.hasAttribute("data-import-confirm");
      var url = confirmClicked ? importConfirm.getAttribute("formaction") : importForm.getAttribute("action");
      if (confirmClicked && importText) importText.readOnly = true;
      postImport(url).then(function (body) {
        if (!confirmClicked && requestSequence !== importSequence) return;
        if (!body.ok) {
          showErrors(body.errors || [{ message: "This decklist could not be imported." }]);
          if (!confirmClicked) renderPreview(null);
          return;
        }
        if (confirmClicked && body.url) {
          window.location = body.url;
          return;
        }
        showErrors([]);
        renderPreview(body);
      }).catch(function () {
        if (requestSequence === importSequence) showErrors([{ message: "Import is unavailable right now." }]);
      }).finally(function () {
        importPending = false;
        if (importText) importText.readOnly = false;
        submitButtons.forEach(function (button) { button.disabled = false; });
      });
    });
  }

})();
