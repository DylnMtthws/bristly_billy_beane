(function () {
  "use strict";
  var filters = document.getElementById("research-filters");
  document.querySelectorAll("[data-filter-open]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (filters) { filters.classList.toggle("open"); if (filters.classList.contains("open")) filters.querySelector("input,select,button").focus(); }
    });
  });
  var scope = document.querySelector("[data-scope-window]");
  if (scope) scope.addEventListener("change", function () {
    var url = new URL(location.href);
    url.searchParams.set("window", scope.value);
    url.searchParams.delete("page");
    location.assign(url.toString());
  });

  var sortMenu = document.querySelector("[data-research-sort-menu]");
  if (sortMenu) {
    var sortSummary = sortMenu.querySelector("summary");
    var sortItems = Array.from(sortMenu.querySelectorAll("[role='menuitem']"));
    sortSummary.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      sortMenu.open = true;
      sortItems[event.key === "ArrowDown" ? 0 : sortItems.length - 1].focus();
    });
    sortMenu.addEventListener("keydown", function (event) {
      var current = sortItems.indexOf(document.activeElement);
      if (event.key === "Escape") {
        event.preventDefault();
        sortMenu.open = false;
        sortSummary.focus();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") current = 0;
      else if (event.key === "End") current = sortItems.length - 1;
      else if (event.key === "ArrowDown") current = (current + 1 + sortItems.length) % sortItems.length;
      else current = (current - 1 + sortItems.length) % sortItems.length;
      sortItems[current].focus();
    });
    document.addEventListener("click", function (event) {
      if (!sortMenu.contains(event.target)) sortMenu.open = false;
    });
  }

  var researchSearch = document.querySelector(".dl-research-search input[type='search']");
  document.querySelectorAll(".dl-card-result > img").forEach(function (image) {
    image.addEventListener("error", function () { image.hidden = true; });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    event.preventDefault();
    if (researchSearch) researchSearch.focus();
  });
})();
