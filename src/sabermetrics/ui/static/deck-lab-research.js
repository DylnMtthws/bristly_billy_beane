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
})();
