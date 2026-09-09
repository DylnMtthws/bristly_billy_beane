(function () {
  "use strict";
  var data = document.getElementById("deck-document-data");
  if (!data) return;
  var state = JSON.parse(data.textContent);
  var root = document.querySelector(".dl-builder");
  var shared = root.dataset.shared === "true";
  var playmatEnabled = root.dataset.playmatEnabled === "true";
  var csrf = document.querySelector("meta[name='csrf-token']");
  var saveState = document.getElementById("save-state");
  var narrow = window.matchMedia("(max-width: 767px)");
  var queue = Promise.resolve(), failedSave = null, pendingSaves = 0;
  var recoveryKey = "deck-lab-pending:" + state.id;
  var railsKey = "deck-lab-builder-rails:" + state.id;
  var selectedEntries = new Set(), activeZoneId = null, activeCardType = "";
  var roleOptions = [
    ["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"],
    ["removal", "Removal"], ["protection", "Protection"],
    ["counter", "Counter"], ["free", "Free interaction"],
    ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"],
    ["board_wipe", "Board wipe"], ["recursion", "Recursion"],
    ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]
  ];
  var rails = { left: true, right: true };
  try { rails = Object.assign(rails, JSON.parse(localStorage.getItem(railsKey) || "{}")); } catch (_) {}
  if (narrow.matches) rails.left = false;

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }
  function refreshSelect(select) { if (window.DeckLabSelects) window.DeckLabSelects.refresh(select); }
  function cardImage(card) {
    if (card.image_uri) return card.image_uri;
    return card.name ? "https://api.scryfall.com/cards/named?format=image&version=normal&exact=" + encodeURIComponent(card.name) : "";
  }
  function setSaving(label, error) {
    if (!saveState) return;
    saveState.textContent = label;
    saveState.classList.toggle("error", !!error);
  }
  function mutationId() { return window.crypto && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2); }
  function command(commands, existingId, existingRevision) {
    if (shared || !commands.length) return Promise.resolve();
    var packet = { mutation_id: existingId || mutationId(), commands: commands };
    if (existingRevision !== undefined) packet.expected_revision = existingRevision;
    pendingSaves += 1;
    queue = queue.then(function () {
      if (packet.expected_revision === undefined) packet.expected_revision = state.revision;
      try { localStorage.setItem(recoveryKey, JSON.stringify(packet)); } catch (_) {}
      setSaving("Saving…", false);
      return fetch("/api/decks/" + encodeURIComponent(state.id) + "/commands", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? csrf.content : "" }, body: JSON.stringify(packet)
      }).then(function (response) {
        return response.json().then(function (body) {
          if (response.status === 409) return fetch("/api/decks/" + encodeURIComponent(state.id)).then(function (fresh) { return fresh.json(); }).then(function (latest) { state = latest; render(); throw new Error("A newer version was loaded. Reapply your last change."); });
          if (!response.ok) throw new Error(body.detail || "The save was refused.");
          state = body; failedSave = null;
          try { localStorage.removeItem(recoveryKey); } catch (_) {}
          setSaving("Saved", false); render();
        });
      }).catch(function (error) { failedSave = packet; setSaving("Retry save", true); if (saveState) saveState.title = error.message; })
        .finally(function () { pendingSaves = Math.max(0, pendingSaves - 1); });
    });
    return queue;
  }
  if (saveState) saveState.addEventListener("click", function () { if (failedSave) { var retry = failedSave; failedSave = null; command(retry.commands, retry.mutation_id, retry.expected_revision); } });

  function preference(key, fallback) { return state.preferences && state.preferences[key] || fallback; }
  function activeView() { return narrow.matches || !playmatEnabled ? "table" : preference("view_mode", "playmat"); }
  function zoneEntries(zoneId) { return state.entries.filter(function (entry) { return !entry.is_commander && entry.zone_id === zoneId; }); }
  function qty(entries) { return entries.reduce(function (n, entry) { return n + Number(entry.quantity || 0); }, 0); }
  function zoneName(zoneId) { var zone = state.zones.find(function (item) { return item.id === zoneId; }); return zone ? zone.name : "Unsorted"; }
  function manaToken(symbol) {
    var upper = String(symbol || "").toUpperCase(), token = node("i", "dl-mana-symbol", upper.replace("/", "⁄"));
    if (/^[WUBRGC]$/.test(upper)) token.classList.add("mana", "mana-" + upper);
    else token.classList.add("dl-mana-generic");
    token.setAttribute("aria-hidden", "true");
    return token;
  }
  function appendManaText(container, text) {
    var raw = String(text || ""), last = 0, match, pattern = /\{([^}]+)\}/g;
    function appendPlain(chunk) {
      chunk.split("\n").forEach(function (line, index) {
        if (index) container.appendChild(node("br"));
        if (line) container.appendChild(document.createTextNode(line));
      });
    }
    while ((match = pattern.exec(raw))) {
      if (match.index > last) appendPlain(raw.slice(last, match.index));
      var wrap = node("span", "dl-mana-inline");
      wrap.appendChild(manaToken(match[1]));
      wrap.appendChild(node("span", "dl-visually-hidden", match[0]));
      container.appendChild(wrap);
      last = match.index + match[0].length;
    }
    if (last < raw.length) appendPlain(raw.slice(last));
  }
  function manaCost(entry) {
    var wrap = node("span", "dl-mana-cost"), raw = String(entry.mana_cost || "").trim(), matches = Array.from(raw.matchAll(/\{([^}]+)\}/g));
    wrap.setAttribute("aria-label", raw ? "Mana cost " + raw.replace(/[{}]/g, " ").trim() : "No mana cost");
    if (!matches.length) { wrap.textContent = "—"; return wrap; }
    matches.forEach(function (match) { wrap.appendChild(manaToken(match[1])); });
    return wrap;
  }
  function roleSelect(entry) {
    var select = node("select", "dl-role-select"), current = String(entry.role || "").toLowerCase();
    if (current && !roleOptions.some(function (option) { return option[0] === current; })) roleOptions = roleOptions.concat([[current, current.replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); })]]);
    roleOptions.forEach(function (item) { var option = node("option", "", item[1]); option.value = item[0]; option.selected = item[0] === current; select.appendChild(option); });
    select.setAttribute("aria-label", "Role for " + entry.name);
    select.addEventListener("change", function () { command([{ type: "set_role", entry_id: entry.id, role: select.value }]); });
    return select;
  }
  function groups() {
    var result = [], commanders = state.entries.filter(function (entry) { return !!entry.is_commander; });
    if (commanders.length) result.push({ id: "commander", name: "Commander", entries: commanders, permanent: true });
    if (preference("group_mode", "zone") === "type") {
      var types = {};
      state.entries.filter(function (entry) { return !entry.is_commander; }).forEach(function (entry) { var name = (entry.type_line || "Other").split(/[—-]/)[0].trim() || "Other"; (types[name] || (types[name] = [])).push(entry); });
      Object.keys(types).sort().forEach(function (name) { result.push({ id: "type-" + name, name: name, entries: types[name], permanent: true }); });
    } else state.zones.forEach(function (zone) { result.push({ id: zone.id, name: zone.name, zone: zone, entries: zoneEntries(zone.id) }); });
    var sort = preference("sort_mode", "manual");
    result.forEach(function (group) { group.entries.sort(function (a, b) { if (sort === "name") return a.name.localeCompare(b.name); if (sort === "mana_value") return Number(a.mana_value || 0) - Number(b.mana_value || 0) || a.name.localeCompare(b.name); return Number(a.sort_order || 0) - Number(b.sort_order || 0); }); });
    return result;
  }
  function zoneOptions(selected, className) { var select = node("select", "dl-zone-select" + (className ? " " + className : "")); state.zones.forEach(function (zone) { var option = node("option", "", zone.name); option.value = zone.id; option.selected = zone.id === selected; select.appendChild(option); }); return select; }
  function syncSelection() {
    var live = new Set(state.entries.map(function (entry) { return entry.id; })); selectedEntries.forEach(function (id) { if (!live.has(id)) selectedEntries.delete(id); });
    var count = document.querySelector("[data-selected-count]"), button = document.querySelector("[data-bulk-move]"), controls = document.querySelector("[data-bulk-controls]");
    if (count) count.textContent = selectedEntries.size + (selectedEntries.size === 1 ? " selected" : " selected");
    if (button) button.disabled = !selectedEntries.size;
    if (controls) controls.hidden = !selectedEntries.size;
    document.querySelectorAll(".dl-deck-row[data-entry-id]").forEach(function (row) { row.classList.toggle("selected", selectedEntries.has(row.dataset.entryId)); });
  }

  function renderText(group, container) {
    var rows = node("div", "dl-zone-rows");
    group.entries.forEach(function (entry) {
      var row = node("div", "dl-deck-row"), q = node("div", "dl-qty");
      row.dataset.entryId = entry.id; row.classList.toggle("selected", selectedEntries.has(entry.id));
      if (!shared && !entry.is_commander) { var minus = node("button", "", "−"); minus.type = "button"; minus.setAttribute("aria-label", "Remove one " + entry.name); minus.addEventListener("click", function () { command([{ type: "adjust_quantity", entry_id: entry.id, delta: -1 }]); }); q.appendChild(minus); }
      q.appendChild(node("span", "", entry.quantity));
      if (!shared && !entry.is_commander) { var plus = node("button", "", "+"); plus.type = "button"; plus.setAttribute("aria-label", "Add one " + entry.name); plus.addEventListener("click", function () { command([{ type: "adjust_quantity", entry_id: entry.id, delta: 1 }]); }); q.appendChild(plus); }
      var imageUrl = cardImage(entry), name = imageUrl ? node("a", "dl-card-name", entry.name) : node("span", "dl-card-name", entry.name);
      if (imageUrl) { name.href = imageUrl; name.target = "_blank"; name.rel = "noopener"; name.setAttribute("aria-label", entry.name + ". Open card image"); }
      if (!entry.is_commander && !shared) { var choose = node("input", "dl-row-select"); choose.type = "checkbox"; choose.checked = selectedEntries.has(entry.id); choose.setAttribute("aria-label", "Select " + entry.name); choose.addEventListener("change", function () { if (choose.checked) selectedEntries.add(entry.id); else selectedEntries.delete(entry.id); syncSelection(); }); row.appendChild(choose); } else row.appendChild(node("span", "dl-row-select-space"));
      row.append(q, name, manaCost(entry), node("span", "dl-card-type", entry.type_line || "—"));
      if (entry.is_commander) row.appendChild(node("span", "dl-zone-value", "Commander"));
      else if (!shared) { var select = zoneOptions(entry.zone_id, "dl-inline-zone-select"); select.setAttribute("aria-label", "Move " + entry.name + " to zone"); select.addEventListener("change", function () { command([{ type: "move_entry", entry_id: entry.id, zone_id: select.value, sort_order: 999 }]); }); row.appendChild(select); }
      else row.appendChild(node("span", "dl-zone-value", zoneName(entry.zone_id)));
      if (entry.is_commander) row.appendChild(node("span", "dl-role-empty", "—")); else if (!shared) row.appendChild(roleSelect(entry)); else row.appendChild(node("span", entry.role ? "dl-role-chip" : "dl-role-empty", entry.role || "—"));
      var actions = node("div", "dl-row-actions"), imageUrl = cardImage(entry);
      if (imageUrl) { var preview = node("a", "dl-icon-button dl-card-preview", ""); preview.href = imageUrl; preview.target = "_blank"; preview.rel = "noopener"; preview.setAttribute("aria-label", "Open card image for " + entry.name); preview.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="3" width="10" height="14" rx="1.5"/><path d="M7 6h10v11a1 1 0 0 1-1 1H7z"/></svg>'; actions.appendChild(preview); }
      if (!shared) { var remove = node("button", "dl-icon-button", "×"); remove.type = "button"; remove.setAttribute("aria-label", "Remove " + entry.name); remove.addEventListener("click", function () { command([{ type: "remove_entry", entry_id: entry.id }]); }); actions.appendChild(remove); }
      row.appendChild(actions); rows.appendChild(row);
    }); container.appendChild(rows);
  }
  function renderGrid(group, container) { var grid = node("div", "dl-grid-display"); group.entries.forEach(function (entry) { var card = node("div", "dl-grid-card"); card.title = entry.name; var url = cardImage(entry); if (url) { var img = node("img"); img.src = url; img.alt = entry.name; img.loading = "lazy"; card.appendChild(img); } else card.appendChild(node("div", "fallback", entry.name)); card.appendChild(node("b", "", entry.quantity + "×")); grid.appendChild(card); }); container.appendChild(grid); }
  function renderSpoiler(group, container) { var grid = node("div", "dl-spoiler-display"); group.entries.forEach(function (entry) { var card = node("article", "dl-spoiler-card"), url = cardImage(entry); if (url) { var img = node("img"); img.src = url; img.alt = ""; img.loading = "lazy"; card.appendChild(img); } else card.appendChild(node("div", "dl-card-art")); var body = node("div"), rules = node("p"); appendManaText(rules, entry.oracle_text || entry.type_line || "Card details unavailable."); body.append(node("strong", "", entry.quantity + "× " + entry.name), rules); card.appendChild(body); grid.appendChild(card); }); container.appendChild(grid); }
  function renderTable() {
    var view = document.getElementById("table-view"); if (!view) return; view.replaceChildren();
    var collapsed = []; try { collapsed = JSON.parse(preference("collapsed_json", "[]")); } catch (_) {}
    var display = preference("display_mode", "text"), density = preference("density", "compact"), surface = node("div", "dl-decklist-surface dl-density-" + density + " dl-display-" + display);
    if (display === "text") {
      var columns = node("div", "dl-decklist-columns");
      ["", "Qty", "Name", "Cost", "Type", "Zone", "Role", ""].forEach(function (label, index) { var heading = node("span", index === 0 ? "dl-column-select" : "", label); if (label) heading.setAttribute("role", "columnheader"); columns.appendChild(heading); });
      surface.appendChild(columns);
    }
    groups().forEach(function (group) {
      var isCollapsed = collapsed.indexOf(group.id) >= 0, section = node("section", "dl-zone-section" + (group.id === "commander" ? " commander" : "") + (isCollapsed ? " collapsed" : "")), head = node("div", "dl-zone-heading"), toggle = node("button", "dl-zone-collapse", isCollapsed ? "▸" : "▾"); section.id = "zone-" + group.id; toggle.type = "button"; toggle.setAttribute("aria-label", (isCollapsed ? "Expand " : "Collapse ") + group.name); toggle.setAttribute("aria-expanded", isCollapsed ? "false" : "true"); head.append(toggle, node("h2", "", group.name), node("span", "dl-zone-count", qty(group.entries)));
      var selectable = group.entries.filter(function (entry) { return !entry.is_commander; });
      if (selectable.length && !shared) { var selectAll = node("input", "dl-group-select"); selectAll.type = "checkbox"; selectAll.checked = selectable.every(function (entry) { return selectedEntries.has(entry.id); }); selectAll.indeterminate = !selectAll.checked && selectable.some(function (entry) { return selectedEntries.has(entry.id); }); selectAll.setAttribute("aria-label", "Select all cards in " + group.name); selectAll.addEventListener("click", function (event) { event.stopPropagation(); }); selectAll.addEventListener("change", function () { selectable.forEach(function (entry) { if (selectAll.checked) selectedEntries.add(entry.id); else selectedEntries.delete(entry.id); }); renderTable(); syncSelection(); }); head.appendChild(selectAll); }
      section.appendChild(head); var body = node("div"); body.hidden = collapsed.indexOf(group.id) >= 0; section.appendChild(body);
      toggle.addEventListener("click", function () { var next = collapsed.indexOf(group.id) >= 0 ? collapsed.filter(function (id) { return id !== group.id; }) : collapsed.concat([group.id]); command([{ type: "update_view", collapsed: next }]); });
      if (display === "grid") renderGrid(group, body); else if (display === "spoiler") renderSpoiler(group, body); else renderText(group, body); surface.appendChild(section);
    });
    view.appendChild(surface);
    if (!state.entries.length) view.appendChild(node("div", "dl-empty", "Choose a commander or add cards to begin."));
  }

  function renderStats() {
    var curve = document.getElementById("mana-curve"), colors = document.getElementById("color-stats"), zones = document.getElementById("zone-stats"); if (!curve) return;
    curve.replaceChildren(); colors.replaceChildren(); zones.replaceChildren();
    var bins = [0, 0, 0, 0, 0, 0];
    state.entries.filter(function (entry) { return !entry.is_commander && !/Land/i.test(entry.type_line || ""); }).forEach(function (entry) { var mv = Math.max(0, Math.floor(Number(entry.mana_value || 0))); bins[Math.min(5, mv)] += Number(entry.quantity || 0); });
    var high = Math.max.apply(Math, bins.concat([1])); bins.forEach(function (count, index) { var item = node("div", "dl-curve-bin"); var bar = node("i"); bar.style.height = Math.max(count ? 12 : 2, count / high * 72) + "px"; item.append(bar, node("span", "", index === 5 ? "5+" : String(index))); curve.appendChild(item); });
    var colorCounts = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0 };
    state.entries.filter(function (entry) { return !entry.is_commander; }).forEach(function (entry) { var ids = entry.color_identity || []; if (!ids.length) colorCounts.C += Number(entry.quantity || 0); else ids.forEach(function (color) { if (colorCounts[color] !== undefined) colorCounts[color] += Number(entry.quantity || 0); }); });
    Object.keys(colorCounts).forEach(function (color) { if (!colorCounts[color]) return; var item = node("div"); item.append(node("i", "mana mana-" + color, color), node("span", "", colorCounts[color])); colors.appendChild(item); });
    var commanders = state.entries.filter(function (entry) { return !!entry.is_commander; }); if (commanders.length) zones.appendChild(statRow("Commander", qty(commanders), null));
    state.zones.forEach(function (zone) { zones.appendChild(statRow(zone.name, qty(zoneEntries(zone.id)), zone.id)); });
    var title = document.getElementById("deck-title"); if (title) title.textContent = state.title;
  }
  function statRow(label, count, zoneId) { var button = node("button", "", ""); button.type = "button"; button.append(node("span", "", label), node("b", "", count)); if (zoneId) button.addEventListener("click", function () { activeZoneId = zoneId; renderPlaymat(); focusZone(zoneId); }); else button.disabled = true; return button; }
  function populateTagOptions(suggestions) { var options = document.querySelector("[data-tag-options]"); if (!options) return; options.replaceChildren(); var assigned = new Set((state.tags || []).map(function (tag) { return tag.id; })); (suggestions || []).filter(function (tag) { return !assigned.has(tag.id); }).forEach(function (tag) { var option = node("option"); option.value = tag.name; option.label = tag.usage_count ? tag.usage_count + " decks" : "Available tag"; options.appendChild(option); }); }
  function renderTags() { var tags = state.tags || [], list = document.querySelector("[data-deck-tag-list]"), summary = document.querySelector("[data-tag-summary]"); document.querySelectorAll("[data-tag-count]").forEach(function (count) { count.textContent = tags.length; }); if (list) { list.replaceChildren(); if (!tags.length) list.appendChild(node("p", "dl-muted", "No tags yet.")); tags.forEach(function (tag) { var remove = node("button", "dl-tag-chip", tag.name); remove.type = "button"; remove.setAttribute("aria-label", "Remove " + tag.name + " tag"); remove.appendChild(node("span", "", "×")); remove.addEventListener("click", function () { command([{ type: "remove_tag", tag_id: tag.id }]); }); list.appendChild(remove); }); } if (summary) { summary.replaceChildren(); if (!tags.length) summary.appendChild(node("span", "dl-muted", "No tags added")); tags.forEach(function (tag) { summary.appendChild(node("span", "dl-tag-label", tag.name)); }); } populateTagOptions(state.tag_suggestions || []); }

  function makeCard(entry, index) {
    var card = node("button", "dl-mat-card"), basicQuantity = /\bBasic Land\b/i.test(entry.type_line || "") ? Number(entry.quantity || 0) : 0; card.type = "button"; card.style.setProperty("--card-index", index); card.dataset.entryId = entry.id; card.title = basicQuantity > 1 ? entry.name + " ×" + basicQuantity : entry.name; card.draggable = !shared; var url = cardImage(entry);
    if (url) { var image = node("img"); image.src = url; image.alt = entry.name; image.loading = "lazy"; card.appendChild(image); } else card.appendChild(node("span", "", entry.name));
    if (basicQuantity > 1) { card.setAttribute("aria-label", entry.name + ", " + basicQuantity + " copies"); card.appendChild(node("span", "dl-mat-card-quantity", "×" + basicQuantity)); }
    card.addEventListener("click", function (event) { event.stopPropagation(); if (event.metaKey || event.ctrlKey) { if (selectedEntries.has(entry.id)) selectedEntries.delete(entry.id); else selectedEntries.add(entry.id); } else { selectedEntries.clear(); selectedEntries.add(entry.id); } document.querySelectorAll(".dl-mat-card.selected").forEach(function (el) { el.classList.toggle("selected", selectedEntries.has(el.dataset.entryId)); }); renderSelectionBar(); syncSelection(); });
    card.classList.toggle("selected", selectedEntries.has(entry.id));
    card.addEventListener("dragstart", function (event) { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/deck-entry", entry.id); card.classList.add("drag-source"); });
    card.addEventListener("dragend", clearDropState); return card;
  }
  function clearDropState() { document.querySelectorAll(".drag-source,.drop-target").forEach(function (el) { el.classList.remove("drag-source", "drop-target"); }); }
  function attachZoneDrag(handle, box, zone, presentation) {
    if (shared) return;
    handle.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 || event.target.closest("button,details")) return;
      event.preventDefault(); handle.setPointerCapture(event.pointerId); box.classList.add("moving");
      var startX = event.clientX, startY = event.clientY, left = parseFloat(box.style.left), top = parseFloat(box.style.top), zoom = Number(presentation.zoom || 1);
      function move(pointer) { var x = left + (pointer.clientX - startX) / zoom, y = top + (pointer.clientY - startY) / zoom; x = Math.max(0, Math.min(Number(presentation.canvas_width || 1600) - box.offsetWidth, x)); y = Math.max(0, Math.min(Number(presentation.canvas_height || 900) - box.offsetHeight, y)); box.style.left = x + "px"; box.style.top = y + "px"; }
      function up(pointer) { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); box.classList.remove("moving"); var x = parseFloat(box.style.left), y = parseFloat(box.style.top); if (presentation.snap_to_grid) { x = Math.round(x / 20) * 20; y = Math.round(y / 20) * 20; } command([{ type: "move_zone", zone_id: zone.id, x: x, y: y }]); }
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
    });
  }
  function zoneMenu(zone, entries) {
    var details = node("details", "dl-zone-menu"), summary = node("summary", "", "•••"); summary.setAttribute("aria-label", zone.name + " actions"); details.appendChild(summary); var menu = node("div", "dl-zone-menu-popover");
    function item(label, action, danger) { var button = node("button", danger ? "danger" : "", label); button.type = "button"; button.addEventListener("click", function (event) { event.stopPropagation(); details.open = false; action(); }); menu.appendChild(button); }
    item("Rename zone", function () { openZoneDialog(zone); });
    item("Select all " + entries.length, function () { entries.forEach(function (entry) { selectedEntries.add(entry.id); }); renderPlaymat(); syncSelection(); });
    item("Sort by mana value", function () { var sorted = entries.slice().sort(function (a, b) { return Number(a.mana_value || 0) - Number(b.mana_value || 0) || a.name.localeCompare(b.name); }); command(sorted.map(function (entry, index) { return { type: "move_entry", entry_id: entry.id, zone_id: zone.id, sort_order: index }; })); });
    if (zone.name.toLowerCase() !== "unsorted") item("Delete zone", function () { openDeleteZone(zone); }, true);
    details.appendChild(menu); details.addEventListener("toggle", function () { var box = details.closest(".dl-mat-zone"); if (box) box.classList.toggle("menu-open", details.open); }); return details;
  }
  function zoneLayoutButton(zone) {
    var stacked = zone.layout_mode === "fan", button = node("button", "dl-zone-layout-toggle"), icon = document.createElementNS("http://www.w3.org/2000/svg", "svg"), label = stacked ? "Spread cards" : "Stack cards"; icon.setAttribute("viewBox", "0 0 20 20"); icon.setAttribute("aria-hidden", "true"); icon.innerHTML = stacked ? '<rect x="1.5" y="4" width="5" height="12" rx="1.2"/><rect x="7.5" y="4" width="5" height="12" rx="1.2"/><rect x="13.5" y="4" width="5" height="12" rx="1.2"/>' : '<rect x="2.2" y="5.1" width="7.2" height="11.5" rx="1.3" transform="rotate(-18 5.8 10.8)"/><rect x="6.4" y="2.6" width="7.2" height="12" rx="1.3"/><rect x="10.6" y="5.1" width="7.2" height="11.5" rx="1.3" transform="rotate(18 14.2 10.8)"/>'; button.type = "button"; button.title = label; button.setAttribute("aria-label", label + " in " + zone.name); button.appendChild(icon); button.addEventListener("click", function (event) { event.stopPropagation(); command([{ type: "set_zone_layout", zone_id: zone.id, layout: stacked ? "spread" : "fan" }]); }); return button;
  }
  function renderPlaymat() {
    var mat = document.getElementById("playmat"); if (!mat) return; mat.replaceChildren(); var p = state.presentation || {}, width = Number(p.canvas_width || 1600), height = Number(p.canvas_height || 900);
    mat.className = "dl-playmat " + (p.surface || "slate-grid") + (p.show_zone_outlines ? " show-zone-outlines" : "") + (p.dim_inactive ? " dim-inactive" : ""); mat.style.width = width + "px"; mat.style.height = height + "px"; mat.style.transform = "translate(" + Number(p.pan_x || 0) + "px," + Number(p.pan_y || 0) + "px) scale(" + Number(p.zoom || 1) + ")"; mat.style.backgroundImage = p.surface === "custom" ? "url('" + (shared ? location.pathname + "/playmat" : "/api/decks/" + encodeURIComponent(state.id) + "/playmat") + "')" : ""; mat.style.backgroundSize = p.surface === "custom" ? "cover" : "";
    var commanders = state.entries.filter(function (entry) { return !!entry.is_commander; });
    if (commanders.length) { var commandBox = node("section", "dl-mat-zone dl-mat-command"); commandBox.style.setProperty("--zone-layer", "1"); commandBox.style.left = "18px"; commandBox.style.top = "18px"; commandBox.style.width = Math.max(170, 22 + commanders.length * 132 + Math.max(0, commanders.length - 1) * 7) + "px"; var commandBar = node("div", "dl-mat-zone-bar"); commandBar.append(node("h2", "", "Commander"), node("b", "", qty(commanders))); commandBox.appendChild(commandBar); var commandCards = node("div", "dl-mat-cards"); commanders.forEach(function (entry, index) { commandCards.appendChild(makeCard(entry, index)); }); commandBox.appendChild(commandCards); mat.appendChild(commandBox); }
    state.zones.forEach(function (zone, index) {
      var entries = zoneEntries(zone.id), layout = zone.layout_mode || "spread", box = node("section", "dl-mat-zone " + layout + (activeZoneId === zone.id ? " active" : "")); box.dataset.zoneId = zone.id; box.tabIndex = 0; box.setAttribute("aria-label", zone.name + " zone"); box.style.setProperty("--zone-layer", String(Math.max(2, Number(zone.sort_order == null ? index : zone.sort_order) + 2))); var visibleItems = entries.length + (shared ? 0 : 1), stackDepth = Math.min(Math.max(entries.length - 1, 0), 8), spreadWidth = 22 + visibleItems * 132 + Math.max(0, visibleItems - 1) * 7, fanWidth = 22 + 132 + stackDepth * 4 + (shared ? 0 : 40), dynamicWidth = layout === "fan" ? Math.max(180, fanWidth) : Math.min(720, Math.max(286, spreadWidth)), zoneWidth = layout === "fan" ? dynamicWidth : Number(zone.width || dynamicWidth); box.style.width = zoneWidth + "px"; box.style.left = Number(zone.x == null ? 220 + index * 280 : zone.x) + "px"; box.style.top = Number(zone.y == null ? 18 : zone.y) + "px";
      box.addEventListener("click", function () { activeZoneId = zone.id; document.querySelectorAll(".dl-mat-zone.active").forEach(function (el) { el.classList.remove("active"); }); box.classList.add("active"); });
      var bar = node("div", "dl-mat-zone-bar"); bar.append(node("h2", "", zone.name), node("b", "", qty(entries))); if (!shared) bar.append(zoneLayoutButton(zone), zoneMenu(zone, entries)); box.appendChild(bar); attachZoneDrag(bar, box, zone, p);
      var cards = node("div", "dl-mat-cards"); if (layout === "fan") cards.style.height = 192 + stackDepth * 3 + "px"; entries.forEach(function (entry, cardIndex) { var card = makeCard(entry, cardIndex); if (layout === "fan") { var offset = Math.min(cardIndex, 8); card.style.setProperty("--stack-x", offset * 4 + "px"); card.style.setProperty("--stack-y", offset * 3 + "px"); card.style.zIndex = String(100 - cardIndex); } cards.appendChild(card); }); if (!shared) { var plus = node("button", "dl-mat-add" + (layout === "fan" ? " compact" : ""), "+"); plus.type = "button"; plus.setAttribute("aria-label", "Add a card to " + zone.name); if (layout === "fan") { plus.style.left = 140 + stackDepth * 4 + "px"; plus.style.top = "4px"; } plus.addEventListener("click", function (event) { event.stopPropagation(); activeZoneId = zone.id; setAddDestination(zone.id); setRail("left", true); var input = document.querySelector("[data-card-search]"); if (input) input.focus(); }); cards.appendChild(plus); } if (layout !== "fan" && spreadWidth > zoneWidth) { cards.tabIndex = 0; cards.setAttribute("aria-label", zone.name + " cards. Scroll horizontally to see all cards."); } box.appendChild(cards);
      box.addEventListener("dragover", function (event) { if (event.dataTransfer.types.indexOf("text/deck-entry") >= 0 || event.dataTransfer.types.indexOf("text/card-id") >= 0) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; box.classList.add("drop-target"); } }); box.addEventListener("dragleave", function (event) { if (!box.contains(event.relatedTarget)) box.classList.remove("drop-target"); }); box.addEventListener("drop", function (event) { event.preventDefault(); clearDropState(); var entryId = event.dataTransfer.getData("text/deck-entry"), cardId = event.dataTransfer.getData("text/card-id"); if (entryId) command([{ type: "move_entry", entry_id: entryId, zone_id: zone.id, sort_order: 999 }]); else if (cardId) command([{ type: "add_card", card_id: cardId, zone_id: zone.id, quantity: 1 }]); });
      mat.appendChild(box);
    });
    var label = document.getElementById("zoom-label"); if (label) label.textContent = Math.round(Number(p.zoom || 1) * 100) + "%"; renderSelectionBar();
  }
  function renderSelectionBar() {
    var bar = document.querySelector("[data-playmat-selection]"); if (!bar) return; var selected = state.entries.filter(function (entry) { return selectedEntries.has(entry.id); }); bar.hidden = !selected.length; bar.replaceChildren(); if (!selected.length) return;
    bar.appendChild(node("strong", "", selected.length + " selected")); var movable = selected.filter(function (entry) { return !entry.is_commander; });
    if (movable.length && state.zones.length) { var select = zoneOptions(movable[0].zone_id), move = node("button", "dl-button", "Move"); select.setAttribute("aria-label", "Move selected cards to zone"); move.type = "button"; move.addEventListener("click", function () { command(movable.map(function (entry, index) { return { type: "move_entry", entry_id: entry.id, zone_id: select.value, sort_order: 999 + index }; })); selectedEntries.clear(); }); bar.append(select, move); }
    var remove = node("button", "dl-text-button danger", "Remove"); remove.type = "button"; remove.addEventListener("click", function () { command(selected.map(function (entry) { return { type: "remove_entry", entry_id: entry.id }; })); selectedEntries.clear(); }); var clear = node("button", "dl-icon-button", "×"); clear.type = "button"; clear.setAttribute("aria-label", "Clear selection"); clear.addEventListener("click", function () { selectedEntries.clear(); renderPlaymat(); syncSelection(); }); bar.append(remove, clear);
  }
  function focusZone(zoneId) { if (activeView() !== "playmat") return; var zone = state.zones.find(function (item) { return item.id === zoneId; }); var stage = document.getElementById("playmat-stage"); if (!zone || !stage) return; var p = state.presentation || {}, zoom = Number(p.zoom || 1), x = stage.clientWidth / 2 - (Number(zone.x || 0) + 150) * zoom, y = stage.clientHeight / 2 - (Number(zone.y || 0) + 90) * zoom; command([{ type: "update_presentation", pan_x: x, pan_y: y }]); }

  function syncControls() {
    var view = activeView(), display = preference("display_mode", "text"), density = preference("density", "compact");
    document.querySelectorAll("[data-view]").forEach(function (button) { button.setAttribute("aria-pressed", button.dataset.view === view ? "true" : "false"); }); document.querySelectorAll("[data-display]").forEach(function (button) { button.setAttribute("aria-pressed", button.dataset.display === display ? "true" : "false"); }); document.querySelectorAll("[data-density]").forEach(function (button) { button.setAttribute("aria-pressed", button.dataset.density === density ? "true" : "false"); });
    var group = document.querySelector("[data-group]"), sort = document.querySelector("[data-sort]"); if (group) { group.value = preference("group_mode", "zone"); refreshSelect(group); } if (sort) { sort.value = preference("sort_mode", "manual"); refreshSelect(sort); }
    var table = document.getElementById("table-view"), playmat = document.getElementById("playmat-view"); if (table) table.hidden = view !== "table"; if (playmat) playmat.hidden = view !== "playmat"; document.querySelectorAll("[data-table-only]").forEach(function (el) { el.hidden = view !== "table"; }); document.querySelectorAll("[data-playmat-only]").forEach(function (el) { el.hidden = view !== "playmat"; });
    document.querySelectorAll("[data-add-zone],[data-bulk-zone]").forEach(function (select) { var current = select.value; select.replaceChildren(); state.zones.forEach(function (zone) { var option = node("option", "", zone.name); option.value = zone.id; select.appendChild(option); }); if (state.zones.some(function (zone) { return zone.id === current; })) select.value = current; refreshSelect(select); });
    document.querySelectorAll("[data-surface]").forEach(function (button) { button.classList.toggle("active", button.dataset.surface === (state.presentation || {}).surface); }); document.querySelectorAll("[data-setting]").forEach(function (input) { input.checked = !!(state.presentation || {})[input.dataset.setting]; }); var size = document.querySelector("[data-playmat-size]"); if (size) { size.value = Number((state.presentation || {}).canvas_width || 1600) + "x" + Number((state.presentation || {}).canvas_height || 900); refreshSelect(size); }
    syncRails();
  }
  function render() { syncControls(); renderTable(); renderPlaymat(); renderStats(); renderTags(); syncSelection(); }
  function syncRails() { if (shared) rails.left = false; root.classList.toggle("left-collapsed", !rails.left); root.classList.toggle("right-collapsed", !rails.right); document.querySelectorAll("[data-toggle-rail]").forEach(function (button) { var side = button.dataset.toggleRail; button.setAttribute("aria-expanded", rails[side] ? "true" : "false"); button.setAttribute("aria-label", (rails[side] ? "Collapse " : "Expand ") + (side === "left" ? "Add cards" : "Deck") + " sidebar"); }); try { localStorage.setItem(railsKey, JSON.stringify(rails)); } catch (_) {} }
  function setRail(side, open) { rails[side] = open; syncRails(); }
  document.querySelectorAll("[data-toggle-rail]").forEach(function (button) { button.addEventListener("click", function () { setRail(button.dataset.toggleRail, !rails[button.dataset.toggleRail]); }); });
  document.querySelectorAll("[data-view]").forEach(function (button) { button.addEventListener("click", function () { command([{ type: "update_view", view_mode: button.dataset.view }]); }); });
  document.querySelectorAll("[data-display]").forEach(function (button) { button.addEventListener("click", function () { command([{ type: "update_view", display_mode: button.dataset.display }]); }); });
  document.querySelectorAll("[data-density]").forEach(function (button) { button.addEventListener("click", function () { command([{ type: "update_view", density: button.dataset.density }]); }); });
  document.querySelectorAll(".dl-decklist-more button").forEach(function (button) { button.addEventListener("click", function () { var menu = button.closest("details"); if (menu) menu.open = false; }); });
  var groupControl = document.querySelector("[data-group]"); if (groupControl) groupControl.addEventListener("change", function () { command([{ type: "update_view", group_mode: groupControl.value }]); }); var sortControl = document.querySelector("[data-sort]"); if (sortControl) sortControl.addEventListener("change", function () { command([{ type: "update_view", sort_mode: sortControl.value }]); });
  var bulkMove = document.querySelector("[data-bulk-move]"); if (bulkMove) bulkMove.addEventListener("click", function () { var zone = document.querySelector("[data-bulk-zone]"); if (!zone || !selectedEntries.size) return; var changes = Array.from(selectedEntries).map(function (id, index) { return { type: "move_entry", entry_id: id, zone_id: zone.value, sort_order: 999 + index }; }); selectedEntries.clear(); command(changes); });
  var clearSelection = document.querySelector("[data-clear-selection]"); if (clearSelection) clearSelection.addEventListener("click", function () { selectedEntries.clear(); renderTable(); syncSelection(); });

  var zoneDialog = document.getElementById("zone-dialog"), zoneDialogTarget = null;
  function openZoneDialog(zone) { if (!zoneDialog) return; zoneDialogTarget = zone || null; zoneDialog.querySelector("h2").textContent = zone ? "Rename zone" : "New zone"; var input = zoneDialog.querySelector("[data-zone-name]"); input.value = zone ? zone.name : ""; zoneDialog.showModal(); input.focus(); }
  document.querySelectorAll("[data-new-zone]").forEach(function (button) { button.addEventListener("click", function () { openZoneDialog(null); }); });
  if (zoneDialog) zoneDialog.addEventListener("close", function () { if (zoneDialog.returnValue !== "save") return; var input = zoneDialog.querySelector("[data-zone-name]"), name = input.value.trim(), error = zoneDialog.querySelector("[data-zone-error]"); if (!name) { error.textContent = "Enter a zone name."; openZoneDialog(zoneDialogTarget); return; } command([zoneDialogTarget ? { type: "rename_zone", zone_id: zoneDialogTarget.id, name: name } : { type: "create_zone", name: name }]); zoneDialogTarget = null; });
  var deleteDialog = document.getElementById("delete-zone-dialog"), deleteTarget = null; function openDeleteZone(zone) { deleteTarget = zone; deleteDialog.querySelector("h2").textContent = "Delete " + zone.name + "?"; deleteDialog.showModal(); } if (deleteDialog) deleteDialog.addEventListener("close", function () { if (deleteDialog.returnValue === "delete" && deleteTarget) command([{ type: "delete_zone", zone_id: deleteTarget.id }]); deleteTarget = null; });
  var title = document.getElementById("deck-title"); if (title && !shared) { title.title = "Click to rename"; title.addEventListener("click", function () { var name = window.prompt("Deck name", state.title); if (name && name !== state.title) command([{ type: "rename_deck", title: name }]); }); }

  var stage = document.getElementById("playmat-stage"), viewportSaveTimer = null;
  function viewportState() { state.presentation = state.presentation || {}; return state.presentation; }
  function clampZoom(value) { return Math.max(0.25, Math.min(2.5, value)); }
  function paintViewport(panX, panY, zoom) { var mat = document.getElementById("playmat"), p = viewportState(), label = document.getElementById("zoom-label"); p.pan_x = panX; p.pan_y = panY; p.zoom = zoom; if (mat) mat.style.transform = "translate(" + panX + "px," + panY + "px) scale(" + zoom + ")"; if (label) label.textContent = Math.round(zoom * 100) + "%"; }
  function persistViewport(delay) { if (shared) return; clearTimeout(viewportSaveTimer); viewportSaveTimer = setTimeout(function () { var p = viewportState(); command([{ type: "update_presentation", pan_x: p.pan_x, pan_y: p.pan_y, zoom: p.zoom }]); }, delay == null ? 140 : delay); }
  function zoomViewport(nextZoom, pointX, pointY, delay) { var p = viewportState(), oldZoom = Number(p.zoom || 1), zoom = clampZoom(nextZoom), x = pointX == null ? stage.clientWidth / 2 : pointX, y = pointY == null ? stage.clientHeight / 2 : pointY, panX = Number(p.pan_x || 0), panY = Number(p.pan_y || 0); paintViewport(x - (x - panX) * zoom / oldZoom, y - (y - panY) * zoom / oldZoom, zoom); persistViewport(delay); }
  document.querySelectorAll("[data-zoom]").forEach(function (button) { button.addEventListener("click", function () { var p = viewportState(), zoom = Number(p.zoom || 1); if (button.dataset.zoom === "in") zoomViewport(zoom + 0.1, null, null, 0); else if (button.dataset.zoom === "out") zoomViewport(zoom - 0.1, null, null, 0); else { var width = Number(p.canvas_width || 1600), height = Number(p.canvas_height || 900), fitted = Math.max(0.25, Math.min(1, (stage.clientWidth - 32) / width, (stage.clientHeight - 32) / height)); paintViewport(Math.round((stage.clientWidth - width * fitted) / 2), Math.round((stage.clientHeight - height * fitted) / 2), fitted); persistViewport(0); } }); });
  if (stage) {
    stage.addEventListener("pointerdown", function (event) { if (event.button !== 0 || event.target.closest(".dl-mat-zone,.dl-zoom,.dl-new-zone-floating,.dl-playmat-selection")) return; event.preventDefault(); stage.setPointerCapture(event.pointerId); stage.classList.add("panning"); var p = viewportState(), startX = event.clientX, startY = event.clientY, panX = Number(p.pan_x || 0), panY = Number(p.pan_y || 0); function move(pointer) { paintViewport(panX + pointer.clientX - startX, panY + pointer.clientY - startY, Number(p.zoom || 1)); } function up(pointer) { stage.removeEventListener("pointermove", move); stage.removeEventListener("pointerup", up); stage.removeEventListener("pointercancel", up); stage.classList.remove("panning"); paintViewport(panX + pointer.clientX - startX, panY + pointer.clientY - startY, Number(p.zoom || 1)); persistViewport(0); } stage.addEventListener("pointermove", move); stage.addEventListener("pointerup", up); stage.addEventListener("pointercancel", up); });
    stage.addEventListener("wheel", function (event) { var p = viewportState(), zoom = Number(p.zoom || 1), unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1, strip = event.target.closest(".dl-mat-cards"); if (!(event.metaKey || event.ctrlKey) && strip && strip.scrollWidth > strip.clientWidth && (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY))) return; event.preventDefault(); if (event.metaKey || event.ctrlKey) { var bounds = stage.getBoundingClientRect(), factor = Math.exp(-event.deltaY * unit * 0.0015); zoomViewport(zoom * factor, event.clientX - bounds.left, event.clientY - bounds.top); } else { var horizontal = event.shiftKey && !event.deltaX ? event.deltaY * unit : event.deltaX * unit, vertical = event.shiftKey && !event.deltaX ? 0 : event.deltaY * unit; paintViewport(Number(p.pan_x || 0) - horizontal, Number(p.pan_y || 0) - vertical, zoom); persistViewport(); } }, { passive: false });
  }

  function setAddDestination(id) { var select = document.querySelector("[data-add-zone]"); if (select) { select.value = id; refreshSelect(select); } }
  document.querySelectorAll("[data-add-open]").forEach(function (button) { button.addEventListener("click", function () { setRail("left", true); }); });
  var searchTimer, searchController;
  function addCard(cardId, zoneId) { if (cardId && zoneId) command([{ type: "add_card", card_id: cardId, zone_id: zoneId, quantity: 1 }]); }
  function renderSearchResults(body) { var results = document.querySelector("[data-card-results]"), scope = document.querySelector("[data-search-scope]"), total = document.querySelector("[data-card-total]"); if (scope) scope.textContent = body.scope || "All legal cards"; if (total) total.textContent = body.results.length + (body.results.length === 40 ? "+" : "") + " results"; results.replaceChildren(); body.results.forEach(function (card) { var tile = node("article", "dl-search-result"), art = node("div", "dl-search-result-art"), url = cardImage(card); if (url) { var img = node("img"); img.src = url; img.alt = ""; img.loading = "lazy"; art.appendChild(img); } var copy = node("div"); copy.append(node("strong", "", card.name), node("small", "", card.type_line || "Card")); var add = node("button", "dl-icon-button", "+"); add.type = "button"; add.setAttribute("aria-label", "Add " + card.name); add.addEventListener("click", function () { var zone = document.querySelector("[data-add-zone]"); addCard(card.id, zone.value); }); tile.append(art, copy, add); tile.draggable = true; tile.addEventListener("dragstart", function (event) { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/card-id", card.id); }); tile.addEventListener("dblclick", function () { var zone = document.querySelector("[data-add-zone]"); addCard(card.id, zone.value); }); results.appendChild(tile); }); if (!body.results.length) results.appendChild(node("p", "dl-muted", "No cards match this search.")); }
  function runSearch() { var input = document.querySelector("[data-card-search]"), results = document.querySelector("[data-card-results]"); if (!input || !results) return; var params = new URLSearchParams({ q: input.value, deck_id: state.id }), oracle = document.querySelector("[data-oracle-search]"), type = document.querySelector("[data-type-search]"), mana = document.querySelector("[data-mana-search]"), rarity = document.querySelector("[data-rarity-search]"); if (oracle && oracle.value) params.set("oracle_text", oracle.value); var typeValue = type && type.value || activeCardType; if (typeValue) params.set("type_line", typeValue); if (mana && mana.value) params.set("mana_max", mana.value); if (rarity && rarity.value) params.set("rarity", rarity.value); if (searchController) searchController.abort(); searchController = new AbortController(); results.textContent = "Searching…"; fetch("/api/cards?" + params.toString(), { signal: searchController.signal }).then(function (response) { if (!response.ok) throw new Error(); return response.json(); }).then(renderSearchResults).catch(function (error) { if (error.name !== "AbortError") results.textContent = "Search unavailable."; }); }
  var searchInput = document.querySelector("[data-card-search]"), decklistSearch = document.querySelector("[data-decklist-search]");
  if (searchInput) searchInput.addEventListener("input", function () { if (decklistSearch && decklistSearch.value !== searchInput.value) decklistSearch.value = searchInput.value; clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 180); });
  if (decklistSearch) {
    decklistSearch.addEventListener("focus", function () { setRail("left", true); });
    decklistSearch.addEventListener("input", function () { if (searchInput) searchInput.value = decklistSearch.value; setRail("left", true); clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 180); });
    decklistSearch.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); setRail("left", true); if (searchInput) searchInput.focus(); runSearch(); } });
  }
  document.querySelectorAll("[data-card-type]").forEach(function (button) { button.addEventListener("click", function () { activeCardType = button.dataset.cardType; document.querySelectorAll("[data-card-type]").forEach(function (item) { item.setAttribute("aria-pressed", item === button ? "true" : "false"); }); runSearch(); }); }); document.querySelectorAll("[data-oracle-search],[data-type-search],[data-mana-search],[data-rarity-search]").forEach(function (input) { input.addEventListener("change", runSearch); });

  var commandersDialog = document.getElementById("commanders-dialog");
  document.querySelectorAll("[data-commanders-open]").forEach(function (button) { button.addEventListener("click", function () {
    commandersDialog.querySelector("[data-commander-picker]").dispatchEvent(new CustomEvent("commanders:load", { detail: state.entries.filter(function (entry) { return !!entry.is_commander; }) }));
    commandersDialog.returnValue = ""; commandersDialog.showModal();
  }); });
  if (commandersDialog) commandersDialog.addEventListener("close", function () {
    if (commandersDialog.returnValue !== "save") return;
    var ids = [commandersDialog.querySelector("[data-commander-id]").value, commandersDialog.querySelector("[data-partner-id]").value].filter(Boolean);
    command([{ type: "set_commanders", card_ids: ids }]).then(runSearch);
  });

  var tagsDialog = document.getElementById("deck-tags-dialog"), tagInput = document.querySelector("[data-tag-input]"), tagError = document.querySelector("[data-tag-error]"), tagSearchTimer, tagSearchController; function tagMessage(message) { if (tagError) tagError.textContent = message || ""; } function addTag() { if (!tagInput) return; var name = tagInput.value.normalize("NFKC").trim().replace(/\s+/g, " "); if (name.length < 2 || name.length > 32) return tagMessage("Use between 2 and 32 characters."); if ((state.tags || []).some(function (tag) { return tag.name.toLowerCase() === name.toLowerCase(); })) return tagMessage("That tag is already on this deck."); if ((state.tags || []).length >= 6) return tagMessage("A deck can have up to six tags."); tagMessage(""); tagInput.value = ""; command([{ type: "add_tag", name: name }]); }
  document.querySelectorAll("[data-tags-open]").forEach(function (button) { button.addEventListener("click", function () { tagMessage(""); tagsDialog.showModal(); if (tagInput) tagInput.focus(); }); }); var tagAdd = document.querySelector("[data-tag-add]"); if (tagAdd) tagAdd.addEventListener("click", addTag); if (tagInput) { tagInput.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); addTag(); } }); tagInput.addEventListener("input", function () { clearTimeout(tagSearchTimer); tagSearchTimer = setTimeout(function () { if (tagSearchController) tagSearchController.abort(); tagSearchController = new AbortController(); fetch("/api/deck-tags?q=" + encodeURIComponent(tagInput.value), { signal: tagSearchController.signal }).then(function (response) { return response.json(); }).then(function (body) { populateTagOptions(body.results || []); }); }, 180); }); }

  var picker = document.getElementById("playmat-picker"), pickerOriginal = null, pickerDraft = null, pendingUpload = null, upload = document.querySelector("[data-playmat-upload]"), uploadStatus = document.querySelector("[data-playmat-upload-status]"); function previewPicker() { if (pickerDraft) { state.presentation = Object.assign({}, pickerDraft); render(); } } function uploadPlaymat(file) { pendingSaves += 1; queue = queue.then(function () { var form = new FormData(); form.append("playmat", file); setSaving("Uploading…", false); return fetch("/api/decks/" + encodeURIComponent(state.id) + "/playmat", { method: "POST", headers: { "X-CSRFToken": csrf ? csrf.content : "" }, body: form }).then(function (response) { if (!response.ok) throw new Error("Upload failed"); return fetch("/api/decks/" + encodeURIComponent(state.id)); }).then(function (response) { return response.json(); }).then(function (body) { state = body; setSaving("Saved", false); render(); }).catch(function (error) { setSaving(error.message, true); }).finally(function () { pendingSaves -= 1; }); }); }
  document.querySelectorAll("[data-playmat-picker]").forEach(function (button) { button.addEventListener("click", function () { pickerOriginal = Object.assign({}, state.presentation || {}); pickerDraft = Object.assign({}, pickerOriginal); pendingUpload = null; picker.returnValue = ""; if (upload) upload.value = ""; picker.showModal(); syncControls(); }); }); document.querySelectorAll("[data-surface]").forEach(function (button) { button.addEventListener("click", function () { if (!pickerDraft) return; pickerDraft.surface = button.dataset.surface; pendingUpload = null; previewPicker(); }); }); document.querySelectorAll("[data-setting]").forEach(function (input) { input.addEventListener("change", function () { if (!pickerDraft) return; pickerDraft[input.dataset.setting] = input.checked; previewPicker(); }); }); var sizePicker = document.querySelector("[data-playmat-size]"); if (sizePicker) sizePicker.addEventListener("change", function () { if (!pickerDraft) return; var parts = sizePicker.value.split("x"); pickerDraft.canvas_width = Number(parts[0]); pickerDraft.canvas_height = Number(parts[1]); previewPicker(); }); if (upload) upload.addEventListener("change", function () { pendingUpload = upload.files.length ? upload.files[0] : null; if (uploadStatus) uploadStatus.textContent = pendingUpload ? upload.files[0].name + " is ready to upload." : ""; }); if (picker) picker.addEventListener("close", function () { if (picker.returnValue === "done" && pickerDraft) { var change = { type: "update_presentation", canvas_width: pickerDraft.canvas_width || 1600, canvas_height: pickerDraft.canvas_height || 900 }; ["snap_to_grid", "show_zone_outlines", "dim_inactive"].forEach(function (key) { change[key] = !!pickerDraft[key]; }); if (!pendingUpload) change.surface = pickerDraft.surface || "slate-grid"; command([change]); if (pendingUpload) uploadPlaymat(pendingUpload); } else if (pickerOriginal) { state.presentation = pickerOriginal; render(); } pickerOriginal = pickerDraft = pendingUpload = null; });
  var share = document.querySelector("[data-share]"); if (share) share.addEventListener("click", function () { fetch("/api/decks/" + encodeURIComponent(state.id) + "/share", { method: "POST", headers: { "X-CSRFToken": csrf ? csrf.content : "" } }).then(function (response) { return response.json(); }).then(function (body) { if (navigator.clipboard) navigator.clipboard.writeText(body.url); window.prompt("Read-only link", body.url); }); });
  document.addEventListener("click", function (event) { var anchor = event.target.closest && event.target.closest("a[href]"); if (!anchor || !pendingSaves || event.defaultPrevented || anchor.target || anchor.hasAttribute("download")) return; var target = new URL(anchor.href, location.href); if (target.origin !== location.origin) return; event.preventDefault(); queue.then(function () { if (!failedSave) location.assign(target.href); else if (saveState) saveState.focus(); }); }, true);
  narrow.addEventListener("change", function (event) { if (event.matches) rails.left = false; render(); }); render(); if (!shared) runSearch();
  if (tagsDialog && new URLSearchParams(location.search).get("panel") === "tags") { tagsDialog.showModal(); if (tagInput) tagInput.focus(); }
  try { var pending = JSON.parse(localStorage.getItem(recoveryKey)); if (pending && pending.commands) command(pending.commands, pending.mutation_id, pending.expected_revision); } catch (_) {}
})();
