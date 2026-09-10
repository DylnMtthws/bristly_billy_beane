(function () {
  "use strict";
  var page = document.querySelector("[data-research-page]");
  if (!page) return;

  var FETCH_MS = 15000;
  var sequence = 0;
  var controller = null;
  var pollTimer = 0;
  var hydrating = false;
  var lastRequest = {url: new URL(location.href), push: false};

  function live() { return document.querySelector("[data-research-live]"); }
  function statusNode() { return document.querySelector("[data-research-status]"); }
  function filters() { return document.getElementById("research-filters"); }
  function results() { return document.querySelector("[data-research-results]"); }
  function searchForm() { return document.querySelector("[data-research-search]"); }
  function csrfToken() {
    var meta = document.querySelector("meta[name='csrf-token']");
    return meta ? meta.content : "";
  }

  function setStatus(text, retry) {
    var node = statusNode();
    if (!node) return;
    node.textContent = text || "";
    node.hidden = !text;
    if (retry) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "dl-button dl-research-retry";
      button.setAttribute("data-research-retry", "1");
      button.textContent = "Retry";
      node.appendChild(document.createTextNode(" "));
      node.appendChild(button);
    }
  }

  function markPrevious(busy) {
    var node = results();
    if (!node) return;
    node.classList.toggle("is-previous", !!busy);
    if (busy) node.setAttribute("aria-busy", "true");
    else node.removeAttribute("aria-busy");
  }

  function bindImages(root) {
    (root || document).querySelectorAll(".dl-card-result > img").forEach(function (image) {
      image.addEventListener("error", function () { image.hidden = true; });
    });
  }

  function boundLabel(value) {
    return String(value) === "10" ? "10+" : String(value);
  }

  function bindBoundRanges(root) {
    (root || document).querySelectorAll(".dl-bound-range").forEach(function (range) {
      if (range.getAttribute("data-bound-ready")) return;
      var minInput = range.querySelector("[data-bound-min]");
      var maxInput = range.querySelector("[data-bound-max]");
      var selection = range.querySelector("[data-bound-selection]");
      var clearBtn = range.querySelector("[data-bound-clear]");
      if (!minInput || !maxInput) return;
      range.setAttribute("data-bound-ready", "1");

      function apply(source) {
        var lo = Number(minInput.value);
        var hi = Number(maxInput.value);
        if (lo > hi) {
          if (source === minInput) {
            minInput.value = String(hi);
            lo = hi;
          } else {
            maxInput.value = String(lo);
            hi = lo;
          }
        }
        minInput.setAttribute("aria-valuetext", boundLabel(lo));
        maxInput.setAttribute("aria-valuetext", boundLabel(hi));
        if (selection) selection.textContent = boundLabel(lo) + "–" + boundLabel(hi);
        range.style.setProperty("--bound-lo", String(lo));
        range.style.setProperty("--bound-hi", String(hi));
      }

      minInput.addEventListener("input", function () { apply(minInput); });
      maxInput.addEventListener("input", function () { apply(maxInput); });
      if (clearBtn) {
        clearBtn.addEventListener("click", function () {
          minInput.value = "0";
          maxInput.value = "10";
          apply(null);
        });
      }
      apply(null);
    });
  }

  function boundDraggingTarget(node) {
    if (!node || typeof node.closest !== "function") return null;
    return node.closest(".dl-bound-range input[type='range']");
  }

  document.addEventListener("pointerdown", function (event) {
    var input = boundDraggingTarget(event.target);
    if (!input) return;
    var range = input.closest(".dl-bound-range");
    if (!range) return;
    range.setAttribute("data-bound-dragging", input.hasAttribute("data-bound-max") ? "max" : "min");
  }, true);

  function clearBoundDragging() {
    document.querySelectorAll(".dl-bound-range[data-bound-dragging]").forEach(function (range) {
      range.removeAttribute("data-bound-dragging");
    });
  }

  document.addEventListener("pointerup", clearBoundDragging);
  document.addEventListener("pointercancel", clearBoundDragging);

  function syncSearch(url) {
    var form = searchForm();
    if (!form) return;
    var tab = form.querySelector("input[name='tab']");
    if (tab) tab.value = url.searchParams.get("tab") || "commanders";
    var q = form.querySelector("input[name='q']");
    if (q && (document.activeElement !== q || lastRequest.restore)) q.value = url.searchParams.get("q") || "";
    var windowField = form.querySelector("input[name='window']");
    var windowValue = url.searchParams.get("window");
    if (url.searchParams.get("tab") === "metagame") {
      if (!windowField) {
        windowField = document.createElement("input");
        windowField.type = "hidden";
        windowField.name = "window";
        form.appendChild(windowField);
      }
      windowField.value = windowValue || "90";
    } else if (windowField) {
      windowField.remove();
    }
  }

  function parseFragment(html) {
    var template = document.createElement("template");
    template.innerHTML = html;
    if (template.content) return template.content;
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    return wrap;
  }

  function loginNext(url) {
    return "/login?next=" + encodeURIComponent(url.pathname + url.search);
  }

  function fetchFragment(url, options) {
    options = options || {};
    var seq = ++sequence;
    if (controller) controller.abort();
    controller = typeof AbortController === "function" ? new AbortController() : null;
    var ownController = controller;
    var timeout = window.setTimeout(function () {
      if (ownController) ownController.abort();
    }, options.timeoutMs || FETCH_MS);
    var headers = {
      "X-Research-Fragment": "1",
      "X-Requested-With": "XMLHttpRequest",
      "Accept": "text/html"
    };
    markPrevious(true);
    if (!hydrating) setStatus("Updating results… Showing previous results until ready.");
    var init = { credentials: "same-origin", headers: headers, cache: "no-store" };
    if (controller) init.signal = controller.signal;
    return fetch(url.toString(), init).then(function (response) {
      if (seq !== sequence) return null;
      if (response.status === 401 || (response.redirected && new URL(response.url, location.href).pathname === "/login")) {
        window.location.assign(loginNext(url));
        return { auth: true, seq: seq };
      }
      if (response.status === 202) {
        return { pending: true, seq: seq };
      }
      if (!response.ok) throw new Error("unavailable");
      return response.text().then(function (html) {
        return { html: html, seq: seq, url: url, push: options.push };
      });
    }).catch(function (error) {
      if (seq !== sequence) return null;
      if (error && error.name === "AbortError") {
        setStatus("Results took too long to update. Showing previous results.", true);
        markPrevious(false);
        return { failed: true, seq: seq };
      }
      setStatus("Results could not be updated. Showing previous results.", true);
      markPrevious(false);
      return { failed: true, seq: seq };
    }).finally(function () {
      window.clearTimeout(timeout);
    });
  }

  function applyFragment(result) {
    if (!result || result.auth || result.seq !== sequence) return false;
    if (result.pending) return false;
    if (result.failed || !result.html) return false;
    var liveNode = live();
    if (!liveNode) return false;
    var fragment = parseFragment(result.html);
    if (!fragment.querySelector("[data-research-fragment]")) {
      setStatus("Results could not be updated. Showing previous results.", true);
      markPrevious(false); return false;
    }
    cancelDeckSuggest();
    liveNode.replaceChildren(fragment);
    bindImages(liveNode);
    bindBoundRanges(liveNode);
    markPrevious(false);
    syncSearch(result.url);
    var freshness = (liveNode.querySelector("[data-research-freshness]") || {}).getAttribute
      ? liveNode.querySelector("[data-research-freshness]").getAttribute("data-research-freshness")
      : "fresh";
    if (freshness === "stale") {
      setStatus("Updating results. Previous field is still shown.");
    } else {
      setStatus("");
    }
    result.freshness = freshness;
    if (result.push) history.pushState({ research: true }, "", result.url);
    lastRequest = {url: result.url, push: false};
    return true;
  }

  function pollUntilReady(url, started) {
    window.clearTimeout(pollTimer);
    hydrating = true;
    var remaining = FETCH_MS - (Date.now() - started);
    if (remaining <= 0) { hydrating = false; markPrevious(false); setStatus("Results could not be updated. Showing previous results.", true); return; }
    fetchFragment(url, { push: lastRequest.push, timeoutMs: remaining }).then(function (result) {
      if (!result || result.seq !== sequence) return;
      if (result.auth) return;
      if (applyFragment(result) && result.freshness !== "stale") {
        hydrating = false;
        return;
      }
      if (result.failed) {
        hydrating = false;
        return;
      }
      if (Date.now() - started >= FETCH_MS) {
        hydrating = false;
        setStatus("Results could not be updated. Showing previous results.", true);
        markPrevious(false);
        return;
      }
      if (result.pending || result.freshness === "stale") {
        setStatus(result.pending ? "Preparing commander results." : "Updating results. Showing previous results.");
        pollTimer = window.setTimeout(function () { pollUntilReady(url, started); }, 800);
      }
    });
  }

  function navigate(url, push, restore) {
    window.clearTimeout(pollTimer);
    hydrating = false;
    lastRequest = {url: url, push: push, restore: !!restore};
    fetchFragment(url, { push: push }).then(function (result) {
      if (!result || result.seq !== sequence) return;
      if (result.auth) return;
      if (applyFragment(result) && result.freshness !== "stale") return;
      if (result.pending || result.freshness === "stale") pollUntilReady(url, Date.now());
    });
  }

  document.addEventListener("click", function (event) {
    var node = event.target;
    if (node && node.nodeType === 3) node = node.parentElement;
    if (!node || typeof node.closest !== "function") return;
    if (node.closest("[data-research-retry]")) {
      event.preventDefault();
      navigate(lastRequest.url, lastRequest.push, lastRequest.restore);
      return;
    }
    var button = node.closest("[data-filter-open]");
    if (button) {
      var form = filters();
      if (!form) return;
      form.classList.toggle("open");
      if (form.classList.contains("open")) {
        var focusable = form.querySelector("input:not([type=hidden]),select,button");
        if (focusable) focusable.focus();
      }
      return;
    }
    var fav = node.closest("[data-fav-commander]");
    if (fav) {
      event.preventDefault();
      event.stopPropagation();
      if (fav.disabled) return;
      fav.disabled = true;
      fetch("/favorites/commander/" + encodeURIComponent(fav.dataset.id) + "/toggle", {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest"
        }
      }).then(function (response) {
        if (response.status === 401 || (response.redirected && new URL(response.url, location.href).pathname === "/login")) {
          window.location.assign(loginNext(new URL(location.href)));
          return null;
        }
        if (!response.ok) throw new Error();
        return response.json();
      }).then(function (data) {
        if (!data) return;
        fav.classList.toggle("active", !!data.favorited);
        fav.setAttribute("aria-pressed", data.favorited ? "true" : "false");
      }).catch(function () { setStatus("Favorite could not be saved. Please try again."); }).finally(function () { fav.disabled = false; });
      return;
    }
    var link = node.closest("a[href]");
    if (!link || !page.contains(link) || event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
        link.hasAttribute("download") || (link.target && link.target !== "_self") ||
        link.hasAttribute("data-research-full-results")) return;
    var url = new URL(link.href, location.href);
    var indexPath = new URL(
      searchForm() ? searchForm().action : location.href,
      location.href
    ).pathname.replace(/\/$/, "");
    if (url.origin !== location.origin || url.pathname.replace(/\/$/, "") !== indexPath) return;
    if (url.searchParams.get("results") === "full") return;
    if (url.hash && url.pathname === location.pathname && url.search === location.search) return;
    event.preventDefault();
    navigate(url, true);
  }, true);

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !page.contains(form) || (form.method && form.method.toLowerCase() !== "get")) return;
    event.preventDefault();
    var url = new URL(form.action || location.href, location.href);
    var data = new FormData(form);
    url.search = "";
    data.forEach(function (value, key) {
      if (value === "" || value === null) return;
      url.searchParams.append(key, String(value));
    });
    url.searchParams.delete("page");
    navigate(url, true);
  });

  document.addEventListener("change", function (event) {
    var select = event.target && event.target.closest && event.target.closest("[data-scope-window]");
    if (!select) return;
    var url = new URL(location.href);
    url.searchParams.set("window", select.value);
    url.searchParams.delete("page");
    navigate(url, true);
  });

  document.addEventListener("keydown", function (event) {
    var sortMenu = event.target && event.target.closest && event.target.closest("[data-research-sort-menu]");
    if (sortMenu) {
      var sortSummary = sortMenu.querySelector("summary");
      var sortItems = Array.from(sortMenu.querySelectorAll("[role='menuitem']"));
      if (event.target === sortSummary && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
        event.preventDefault();
        sortMenu.open = true;
        sortItems[event.key === "ArrowDown" ? 0 : sortItems.length - 1].focus();
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        sortMenu.open = false;
        sortSummary.focus();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      var current = sortItems.indexOf(document.activeElement);
      if (event.key === "Home") current = 0;
      else if (event.key === "End") current = sortItems.length - 1;
      else if (event.key === "ArrowDown") current = (current + 1 + sortItems.length) % sortItems.length;
      else current = (current - 1 + sortItems.length) % sortItems.length;
      sortItems[current].focus();
      return;
    }
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    var researchSearch = document.querySelector(".dl-research-search input[type='search']");
    if (!researchSearch) return;
    event.preventDefault();
    researchSearch.focus();
  });

  document.addEventListener("click", function (event) {
    var sortMenu = document.querySelector("[data-research-sort-menu]");
    if (sortMenu && !sortMenu.contains(event.target)) sortMenu.open = false;
  });

  window.addEventListener("popstate", function () {
    navigate(new URL(location.href), false, true);
  });

  bindImages(page);
  bindBoundRanges(page);

  var DECK_SUGGEST_MS = 250;
  var deckSuggestTimer = 0;
  var deckSuggestSeq = 0;
  var deckSuggestController = null;

  function cancelDeckSuggest() {
    window.clearTimeout(deckSuggestTimer);
    deckSuggestSeq += 1;
    if (deckSuggestController) deckSuggestController.abort();
    deckSuggestController = null;
  }

  function deckSuggestInput(node) {
    if (!node || typeof node.closest !== "function") return null;
    return node.closest("[data-deck-commander]");
  }

  function deckSuggestList(input) {
    if (!input) return null;
    var wrap = input.closest(".dl-deck-suggest");
    return wrap ? wrap.querySelector("[role='listbox']") : null;
  }

  function closeDeckSuggest(input) {
    var list = deckSuggestList(input);
    if (!list) return;
    list.hidden = true;
    list.innerHTML = "";
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function closeAllDeckSuggest() {
    document.querySelectorAll("[data-deck-commander]").forEach(closeDeckSuggest);
  }

  function setDeckPartnerVisible(show, primaryId) {
    var wrap = document.querySelector("[data-deck-partner-wrap]");
    var primary = document.querySelector("[data-deck-commander='primary']");
    if (primary) {
      if (primaryId) primary.setAttribute("data-commander-id", primaryId);
      else primary.removeAttribute("data-commander-id");
    }
    if (!wrap) return;
    var partner = wrap.querySelector("[data-deck-commander='partner']");
    wrap.hidden = !show;
    if (!partner) return;
    partner.disabled = !show;
    if (!show) {
      partner.value = "";
      partner.removeAttribute("data-commander-id");
      closeDeckSuggest(partner);
    }
  }

  function deckSuggestItems(list) {
    return list ? Array.prototype.slice.call(list.querySelectorAll("[role='option']")) : [];
  }

  function activateDeckSuggest(list, index) {
    var items = deckSuggestItems(list);
    if (!items.length) return;
    var next = (index + items.length) % items.length;
    items.forEach(function (item, itemIndex) {
      item.setAttribute("aria-selected", itemIndex === next ? "true" : "false");
    });
    items[next].scrollIntoView({ block: "nearest" });
    var input = list.parentElement && list.parentElement.querySelector("[data-deck-commander]");
    if (input) input.setAttribute("aria-activedescendant", items[next].id);
  }

  function selectDeckSuggest(input, item) {
    if (!input || !item) return;
    input.value = item.getAttribute("data-name") || item.textContent || "";
    var id = item.getAttribute("data-id") || "";
    if (id) input.setAttribute("data-commander-id", id);
    else input.removeAttribute("data-commander-id");
    if (input.getAttribute("data-deck-commander") === "primary") {
      setDeckPartnerVisible(false);
      setDeckPartnerVisible(item.getAttribute("data-can-pair") === "1", id);
    }
    closeDeckSuggest(input);
  }

  function renderDeckSuggest(input, results) {
    var list = deckSuggestList(input);
    if (!list) return;
    list.innerHTML = "";
    if (!results.length) {
      closeDeckSuggest(input);
      return;
    }
    results.forEach(function (card, index) {
      var option = document.createElement("li");
      option.id = list.id + "-" + index;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      option.setAttribute("data-id", card.id || "");
      option.setAttribute("data-name", card.name || "");
      option.setAttribute("data-can-pair", card.can_pair ? "1" : "0");
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = card.name || "";
      option.appendChild(button);
      list.appendChild(option);
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    activateDeckSuggest(list, 0);
  }

  function syncDeckPrimaryResolution(input, results) {
    if (input.getAttribute("data-deck-commander") !== "primary") return;
    var value = input.value.trim().toLowerCase();
    var exact = (results || []).filter(function (card) {
      return String(card.name || "").toLowerCase() === value;
    });
    if (exact.length === 1) {
      setDeckPartnerVisible(!!exact[0].can_pair, exact[0].id);
    } else {
      setDeckPartnerVisible(false);
    }
  }

  function requestDeckSuggest(input) {
    var query = input.value.trim();
    var role = input.getAttribute("data-deck-commander");
    var url = new URL("/research/deck-commanders", location.href);
    if (role === "partner") {
      var primary = document.querySelector("[data-deck-commander='primary']");
      var partnerOf = primary && primary.getAttribute("data-commander-id");
      if (!partnerOf) {
        closeDeckSuggest(input);
        return;
      }
      url.searchParams.set("partner_of", partnerOf);
      url.searchParams.set("q", query);
    } else if (!query) {
      closeDeckSuggest(input);
      setDeckPartnerVisible(false);
      return;
    } else {
      url.searchParams.set("q", query);
    }
    var seq = ++deckSuggestSeq;
    if (deckSuggestController) deckSuggestController.abort();
    deckSuggestController = typeof AbortController === "function" ? new AbortController() : null;
    var init = {
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json"
      },
      cache: "no-store"
    };
    if (deckSuggestController) init.signal = deckSuggestController.signal;
    fetch(url.toString(), init).then(function (response) {
      if (seq !== deckSuggestSeq) return null;
      if (response.status === 401 || (response.redirected && new URL(response.url, location.href).pathname === "/login")) {
        window.location.assign(loginNext(new URL(location.href)));
        return null;
      }
      if (!response.ok) throw new Error();
      return response.json();
    }).then(function (payload) {
      if (seq !== deckSuggestSeq || !payload || !input.isConnected || input.value.trim() !== query) return;
      if (role === "partner" && (!primary || primary.getAttribute("data-commander-id") !== partnerOf)) return;
      var results = Array.isArray(payload.results) ? payload.results.slice(0, 20) : [];
      renderDeckSuggest(input, results);
      syncDeckPrimaryResolution(input, results);
    }).catch(function (error) {
      if (seq !== deckSuggestSeq) return;
      if (error && error.name === "AbortError") return;
      closeDeckSuggest(input);
    });
  }

  page.addEventListener("input", function (event) {
    var input = deckSuggestInput(event.target);
    if (!input || !page.contains(input)) return;
    cancelDeckSuggest();
    closeDeckSuggest(input);
    input.removeAttribute("data-commander-id");
    if (input.getAttribute("data-deck-commander") === "primary") setDeckPartnerVisible(false);
    deckSuggestTimer = window.setTimeout(function () { requestDeckSuggest(input); }, DECK_SUGGEST_MS);
  });

  page.addEventListener("keydown", function (event) {
    var input = deckSuggestInput(event.target);
    if (!input || !page.contains(input)) return;
    var list = deckSuggestList(input);
    var items = deckSuggestItems(list);
    var open = list && !list.hidden && items.length;
    if (event.key === "Escape") {
      if (open) {
        event.preventDefault();
        closeDeckSuggest(input);
      }
      return;
    }
    if (!open) return;
    var selected = items.findIndex(function (item) {
      return item.getAttribute("aria-selected") === "true";
    });
    if (event.key === "ArrowDown") {
      event.preventDefault();
      activateDeckSuggest(list, selected + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activateDeckSuggest(list, selected - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      activateDeckSuggest(list, 0);
    } else if (event.key === "End") {
      event.preventDefault();
      activateDeckSuggest(list, items.length - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      selectDeckSuggest(input, items[Math.max(0, selected)]);
    }
  });

  page.addEventListener("mousedown", function (event) {
    var option = event.target && event.target.closest && event.target.closest(".dl-deck-suggest-list [role='option']");
    if (!option) return;
    var input = option.closest(".dl-deck-suggest").querySelector("[data-deck-commander]");
    event.preventDefault();
    selectDeckSuggest(input, option);
  });

  page.addEventListener("click", function (event) {
    var option = event.target && event.target.closest && event.target.closest(".dl-deck-suggest-list [role='option']");
    if (!option) return;
    var wrap = option.closest(".dl-deck-suggest");
    if (!wrap) return;
    event.preventDefault();
    selectDeckSuggest(wrap.querySelector("[data-deck-commander]"), option);
  });

  document.addEventListener("click", function (event) {
    if (event.target && event.target.closest && event.target.closest(".dl-deck-suggest")) return;
    closeAllDeckSuggest();
  });

  var initial = results();
  if (initial && initial.getAttribute("data-research-freshness") === "pending") {
    setStatus("Preparing commander results.");
    pollUntilReady(new URL(location.href), Date.now());
  } else if (initial && initial.getAttribute("data-research-freshness") === "stale") {
    setStatus("Updating results. Previous field is still shown.");
    pollUntilReady(new URL(location.href), Date.now());
  }
})();
