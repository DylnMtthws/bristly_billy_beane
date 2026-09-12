(function (root) {
  "use strict";

  var PRIVATE_ZONE_NAMES = {
    sideboard: 1, notes: 1, note: 1, maybeboard: 1, maybe: 1, draft: 1, considering: 1
  };

  function zoneName(document, zoneId) {
    var zones = (document && document.zones) || [];
    for (var i = 0; i < zones.length; i += 1) {
      if (zones[i].id === zoneId) return zones[i].name;
    }
    return "Unsorted";
  }

  function isLibraryZoneName(name) {
    return !Object.prototype.hasOwnProperty.call(PRIVATE_ZONE_NAMES, String(name || "Unsorted").trim().toLowerCase());
  }

  function addQuantity(map, order, name, quantity) {
    if (!map[name]) {
      map[name] = 0;
      order.push(name);
    }
    map[name] += quantity;
  }

  function exportLines(document) {
    var commanderQty = Object.create(null);
    var commanderOrder = [];
    var libraryQty = Object.create(null);
    var entries = (document && document.entries) || [];
    entries.forEach(function (entry) {
      var quantity = Number(entry.quantity || 0);
      var name = entry && entry.name;
      if (!quantity || !name) return;
      if (entry.is_commander) {
        addQuantity(commanderQty, commanderOrder, name, quantity);
        return;
      }
      if (!isLibraryZoneName(zoneName(document, entry.zone_id))) return;
      libraryQty[name] = (libraryQty[name] || 0) + quantity;
    });
    var lines = commanderOrder.map(function (name) {
      var quantity = commanderQty[name] + (libraryQty[name] || 0);
      delete libraryQty[name];
      return quantity + " " + name;
    });
    Object.keys(libraryQty).sort(function (a, b) {
      return a.localeCompare(b, undefined, { sensitivity: "base" });
    }).forEach(function (name) {
      lines.push(libraryQty[name] + " " + name);
    });
    return lines;
  }

  function exportText(document) {
    return exportLines(document).join("\n");
  }

  function utf8ToBase64(text) {
    var bytes = new TextEncoder().encode(text);
    var binary = "";
    for (var i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  function encodeDeckParam(text) {
    return encodeURIComponent(utf8ToBase64(text));
  }

  function manaPoolUrl(text) {
    if (!text) return "";
    return "https://manapool.com/add-deck?deck=" + encodeDeckParam(text);
  }

  var api = {
    exportLines: exportLines,
    exportText: exportText,
    encodeDeckParam: encodeDeckParam,
    manaPoolUrl: manaPoolUrl
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.DeckLabExport = api;
})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
