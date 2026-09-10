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
    (root || document).querySelectorAll(".dl-bound-inputs input[type='range']").forEach(function (input) {
      function sync() {
        input.setAttribute("aria-valuetext", boundLabel(input.value));
        var output = input.parentElement.querySelector("[data-bound-value]");
        if (output) output.textContent = boundLabel(input.value);
      }
      input.addEventListener("input", sync);
      sync();
    });
  }

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
  var initial = results();
  if (initial && initial.getAttribute("data-research-freshness") === "pending") {
    setStatus("Preparing commander results.");
    pollUntilReady(new URL(location.href), Date.now());
  } else if (initial && initial.getAttribute("data-research-freshness") === "stale") {
    setStatus("Updating results. Previous field is still shown.");
    pollUntilReady(new URL(location.href), Date.now());
  }
})();
