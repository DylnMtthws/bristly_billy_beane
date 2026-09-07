(function () {
  "use strict";
  document.querySelectorAll("[data-commander-picker]").forEach(function (picker) {
    var search = picker.querySelector("[data-commander-search]"), hidden = picker.querySelector("[data-commander-id]"), results = picker.querySelector("[data-commander-results]");
    var partner = picker.querySelector("[data-partner-search]"), partnerId = picker.querySelector("[data-partner-id]"), partnerResults = picker.querySelector("[data-partner-results]");
    var generation = 0, timers = {}, controllers = {};
    function resetPartner() { partner.value = ""; partnerId.value = ""; partnerResults.replaceChildren(); partner.disabled = !hidden.value; partner.placeholder = hidden.value ? "Search compatible partners" : "Choose a commander first"; }
    function searchFor(isPartner) {
      var input = isPartner ? partner : search, target = isPartner ? partnerResults : results, key = isPartner ? "partner" : "commander";
      clearTimeout(timers[key]);
      if (controllers[key]) controllers[key].abort();
      var current = generation;
      timers[key] = setTimeout(function () {
        controllers[key] = new AbortController();
        var url = isPartner ? "/api/commanders/partners?commander_id=" + encodeURIComponent(hidden.value) + "&q=" : "/api/cards?commander=1&q=";
        fetch(url + encodeURIComponent(input.value), { signal: controllers[key].signal })
          .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
          .then(function (data) {
            if (current !== generation) return;
            target.replaceChildren();
            data.results.slice(0, 8).forEach(function (card) {
              var button = document.createElement("button"); button.type = "button"; button.textContent = card.name;
              button.addEventListener("click", function () {
                generation += 1;
                input.value = card.name; (isPartner ? partnerId : hidden).value = card.id; target.replaceChildren();
                if (!isPartner) { resetPartner(); searchFor(true); }
              });
              target.appendChild(button);
            });
            if (!data.results.length) target.textContent = isPartner ? "No compatible partners found." : "No commanders found.";
          }).catch(function (error) { if (current === generation && error.name !== "AbortError") target.textContent = "Search unavailable."; });
      }, 180);
    }
    search.addEventListener("input", function () { generation += 1; hidden.value = ""; results.replaceChildren(); resetPartner(); searchFor(false); });
    partner.addEventListener("input", function () { generation += 1; partnerId.value = ""; partnerResults.replaceChildren(); searchFor(true); });
    partner.addEventListener("focus", function () { searchFor(true); });
    picker.addEventListener("commanders:load", function (event) {
      generation += 1; results.replaceChildren();
      var cards = event.detail || []; search.value = cards[0] ? cards[0].name : ""; hidden.value = cards[0] ? cards[0].card_id : ""; resetPartner();
      if (cards[1]) { partner.value = cards[1].name; partnerId.value = cards[1].card_id; }
    });
    picker.closest("form").addEventListener("submit", function (event) {
      if (event.submitter && event.submitter.value === "cancel") return;
      search.setCustomValidity(search.value && !hidden.value ? "Select a commander from the results." : "");
      partner.setCustomValidity(partner.value && !partnerId.value ? "Select a compatible partner from the results." : "");
      if (!search.reportValidity() || !partner.reportValidity()) event.preventDefault();
    });
    [search, partner].forEach(function (input) { input.addEventListener("input", function () { input.setCustomValidity(""); }); });
  });
})();
