(function () {
  "use strict";

  var selectSequence = 0;
  var activeSelect = null;
  var selectPopover = document.createElement("div");
  selectPopover.className = "dl-select-popover";
  selectPopover.setAttribute("role", "listbox");
  selectPopover.hidden = true;
  document.body.appendChild(selectPopover);

  function selectName(select) {
    if (select.getAttribute("aria-label")) return select.getAttribute("aria-label");
    if (select.id) {
      var external = document.querySelector("label[for='" + CSS.escape(select.id) + "']");
      if (external) return external.textContent.trim();
    }
    var parent = select.closest("label");
    if (parent) {
      var labelText = Array.from(parent.childNodes).filter(function (node) {
        return node.nodeType === Node.TEXT_NODE;
      }).map(function (node) { return node.textContent.trim(); }).filter(Boolean).join(" ");
      if (labelText) return labelText;
    }
    return "Choose option";
  }

  function selectedOption(select) {
    return select.options[select.selectedIndex] || select.options[0] || null;
  }

  function syncSelect(select) {
    if (!select._dlSelectTrigger) return;
    var option = selectedOption(select);
    var text = option ? option.textContent.trim() : "Choose option";
    select._dlSelectLabel.textContent = text;
    select._dlSelectTrigger.disabled = !!select.disabled;
    select._dlSelectTrigger.setAttribute("aria-label", select._dlSelectName + ": " + text);
    if (activeSelect && activeSelect.select === select) buildSelectOptions(activeSelect);
  }

  function positionSelectPopover(component) {
    if (!component || selectPopover.hidden) return;
    var rect = component.trigger.getBoundingClientRect();
    var width = Math.max(rect.width, Math.min(280, selectPopover.scrollWidth));
    width = Math.min(width, window.innerWidth - 16);
    selectPopover.style.width = width + "px";
    var left = Math.min(rect.left, window.innerWidth - width - 8);
    left = Math.max(8, left);
    var height = Math.min(selectPopover.scrollHeight, 280);
    var below = window.innerHeight - rect.bottom - 8;
    var top = below >= Math.min(height, 180) ? rect.bottom + 6 : rect.top - height - 6;
    selectPopover.style.left = left + "px";
    selectPopover.style.top = Math.max(8, top) + "px";
  }

  function closeSelect(returnFocus) {
    if (!activeSelect) return;
    var component = activeSelect;
    activeSelect = null;
    component.trigger.setAttribute("aria-expanded", "false");
    component.wrapper.classList.remove("open");
    selectPopover.hidden = true;
    selectPopover.replaceChildren();
    if (returnFocus) component.trigger.focus();
  }

  function chooseSelectOption(component, option) {
    component.select.value = option.value;
    component.select.dispatchEvent(new Event("input", { bubbles: true }));
    component.select.dispatchEvent(new Event("change", { bubbles: true }));
    syncSelect(component.select);
    closeSelect(true);
  }

  function buildSelectOptions(component) {
    selectPopover.replaceChildren();
    Array.from(component.select.options).forEach(function (option, index) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "dl-select-option";
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", option.selected ? "true" : "false");
      item.dataset.optionIndex = String(index);
      item.disabled = !!option.disabled;
      var label = document.createElement("span");
      label.textContent = option.textContent.trim();
      var check = document.createElement("span");
      check.className = "dl-select-check";
      check.setAttribute("aria-hidden", "true");
      check.textContent = "✓";
      item.append(label, check);
      item.addEventListener("click", function () { chooseSelectOption(component, option); });
      selectPopover.appendChild(item);
    });
  }

  function openSelect(component, focusEdge) {
    if (activeSelect && activeSelect !== component) closeSelect(false);
    activeSelect = component;
    buildSelectOptions(component);
    selectPopover.id = component.popoverId;
    selectPopover.hidden = false;
    component.trigger.setAttribute("aria-expanded", "true");
    component.wrapper.classList.add("open");
    positionSelectPopover(component);
    if (focusEdge) {
      var options = Array.from(selectPopover.querySelectorAll("[role='option']:not(:disabled)"));
      var selected = selectPopover.querySelector("[aria-selected='true']:not(:disabled)");
      var target = focusEdge === "last" ? options[options.length - 1] : selected || options[0];
      if (target) target.focus();
    }
  }

  function enhanceSelect(select) {
    if (select.dataset.dlSelectEnhanced || select.multiple || Number(select.size) > 1) return;
    select.dataset.dlSelectEnhanced = "true";
    var wrapper = document.createElement("span");
    wrapper.className = "dl-select-control";
    if (select.classList.contains("dl-zone-select")) wrapper.classList.add("dl-select-control-compact");
    if (select.classList.contains("dl-inline-zone-select")) wrapper.classList.add("dl-inline-zone-control");
    if (select.classList.contains("dl-role-select")) wrapper.classList.add("dl-role-control");
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    select.classList.add("dl-native-select");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "dl-select-trigger";
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    var popoverId = "dl-select-options-" + (++selectSequence);
    trigger.setAttribute("aria-controls", popoverId);
    var label = document.createElement("span");
    var chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chevron.setAttribute("aria-hidden", "true");
    chevron.setAttribute("viewBox", "0 0 12 12");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "m3 4.5 3 3 3-3");
    chevron.appendChild(path);
    trigger.append(label, chevron);
    wrapper.appendChild(trigger);

    var component = {
      select: select,
      wrapper: wrapper,
      trigger: trigger,
      popoverId: popoverId
    };
    select._dlSelectName = selectName(select);
    select._dlSelectTrigger = trigger;
    select._dlSelectLabel = label;
    select._dlSelectComponent = component;
    syncSelect(select);

    trigger.addEventListener("click", function () {
      if (activeSelect === component) closeSelect(false);
      else openSelect(component, false);
    });
    trigger.addEventListener("keydown", function (event) {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      openSelect(component, event.key === "ArrowUp" || event.key === "End" ? "last" : "first");
    });
    select.addEventListener("change", function () { syncSelect(select); });
    select.addEventListener("focus", function () { trigger.focus(); });
    var containingForm = select.form;
    if (containingForm && !containingForm.dataset.dlSelectResetBound) {
      containingForm.dataset.dlSelectResetBound = "true";
      containingForm.addEventListener("reset", function () {
        window.setTimeout(function () {
          containingForm.querySelectorAll("select[data-dl-select-enhanced]").forEach(syncSelect);
        });
      });
    }
  }

  function enhanceSelects(root) {
    if (root.matches && root.matches("select")) enhanceSelect(root);
    if (root.querySelectorAll) root.querySelectorAll("select").forEach(enhanceSelect);
  }

  selectPopover.addEventListener("keydown", function (event) {
    if (!activeSelect) return;
    var items = Array.from(selectPopover.querySelectorAll("[role='option']:not(:disabled)"));
    var current = items.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      closeSelect(true);
      return;
    }
    if (event.key === "Tab") {
      closeSelect(false);
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") current = 0;
    else if (event.key === "End") current = items.length - 1;
    else if (event.key === "ArrowDown") current = (current + 1 + items.length) % items.length;
    else current = (current - 1 + items.length) % items.length;
    if (items[current]) items[current].focus();
  });
  document.addEventListener("pointerdown", function (event) {
    if (activeSelect && !activeSelect.wrapper.contains(event.target) && !selectPopover.contains(event.target)) closeSelect(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeSelect) closeSelect(true);
  });
  window.addEventListener("resize", function () { positionSelectPopover(activeSelect); });
  window.addEventListener("scroll", function () { positionSelectPopover(activeSelect); }, true);
  new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      if (mutation.target.matches && mutation.target.matches("select[data-dl-select-enhanced]")) syncSelect(mutation.target);
      mutation.addedNodes.forEach(enhanceSelects);
    });
  }).observe(document.body, { childList: true, subtree: true });
  window.DeckLabSelects = {
    refresh: function (select) {
      if (select) syncSelect(select);
      else document.querySelectorAll("select[data-dl-select-enhanced]").forEach(syncSelect);
    }
  };
  enhanceSelects(document);

  document.querySelectorAll(".dl-account-menu").forEach(function (menu) {
    var summary = menu.querySelector("summary");
    document.addEventListener("click", function (event) { if (!menu.contains(event.target)) menu.open = false; });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && menu.open) { menu.open = false; summary.focus(); } });
  });
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
