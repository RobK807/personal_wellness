/* The food picker's cascade: List -> Grouping -> the food, and the macros.
 *
 * Progressive enhancement, all of it. Without this file every picker is still a
 * working control - a full-catalogue autocomplete plus four macro boxes you
 * type into - and the server does exactly the same arithmetic on save either
 * way. What this adds is the narrowing: change the List and the Grouping
 * dropdown follows it, change either and the food list shrinks to what is
 * actually in that corner of the catalogue.
 *
 * The catalogue is read once from the JSON the page carries, rather than each
 * row holding its own copy. See the note in _foodpicker.html for what the
 * alternative costs.
 *
 * Deliberately ASCII throughout: this is served to a phone over Tailscale from
 * a NAS, and one mis-encoded byte here is a picker that silently does nothing.
 */
(function () {
  "use strict";

  var catalogueNode = document.getElementById("food-catalogue");
  var groupingNode = document.getElementById("food-groupings");
  if (!catalogueNode) return;

  var FOODS = JSON.parse(catalogueNode.textContent);
  var GROUPINGS = groupingNode ? JSON.parse(groupingNode.textContent) : {};
  var MACROS = ["calories", "carbs", "fat", "protein"];
  var ANY = "— any —";

  /* Name -> row, for resolving what somebody typed or picked. Lower-cased
     because the catalogue itself matches case-insensitively, and keyed by name
     alone as well as by list so a name typed without touching the List
     dropdown still resolves. */
  var byName = {};
  var byListName = {};
  FOODS.forEach(function (row) {
    var key = row.name.toLowerCase();
    if (!(key in byName)) byName[key] = row;
    byListName[row.list + " " + key] = row;
  });

  function lookup(typed, list) {
    var key = String(typed || "").trim().toLowerCase();
    if (!key) return null;
    return byListName[list + " " + key] || byName[key] || null;
  }

  /* A datalist holding just the foods in this corner of the catalogue. One per
     row, created on demand: a row nobody touches keeps the shared full list. */
  var made = 0;
  function narrow(pick) {
    var list = pick.querySelector("[data-pick-list]");
    var grouping = pick.querySelector("[data-pick-grouping]");
    var nameBox = pick.querySelector("[data-pick-name]");
    if (!nameBox) return;

    var wantList = list ? list.value : "";
    var wantGroup = grouping ? grouping.value : "";
    var matches = FOODS.filter(function (row) {
      if (wantList && row.list !== wantList) return false;
      if (wantGroup && row.grouping !== wantGroup) return false;
      return true;
    });

    var id = pick.getAttribute("data-list-id");
    if (!id) {
      id = "picklist-" + (++made);
      pick.setAttribute("data-list-id", id);
      var node = document.createElement("datalist");
      node.id = id;
      pick.appendChild(node);
    }
    var target = document.getElementById(id);
    target.innerHTML = "";
    matches.forEach(function (row) {
      var option = document.createElement("option");
      option.value = row.name;
      target.appendChild(option);
    });
    nameBox.setAttribute("list", id);
    nameBox.placeholder = matches.length
      ? matches.length + " to choose from, or type something new"
      : "nothing here yet - type a new one";
  }

  /* When the List changes the Grouping has to follow it: a Grouping belongs to
     a List, and leaving "Dinner" showing under Items would filter to nothing.
     The current value is kept if the new list also has it, because moving a
     food between lists usually keeps its kind. */
  function refillGroupings(pick) {
    var list = pick.querySelector("[data-pick-list]");
    var grouping = pick.querySelector("[data-pick-grouping]");
    if (!list || !grouping) return;
    var wanted = grouping.value;
    var available = GROUPINGS[list.value] || [];
    grouping.innerHTML = "";
    var any = document.createElement("option");
    any.value = "";
    any.textContent = ANY;
    grouping.appendChild(any);
    available.forEach(function (label) {
      var option = document.createElement("option");
      option.value = label;
      option.textContent = label;
      if (label === wanted) option.selected = true;
      grouping.appendChild(option);
    });
  }

  /* Choosing a food fills the four macros, scaled to the quantity. A name that
     matches nothing is left alone: those macros are what somebody typed about
     something the catalogue has never heard of, and overwriting them with
     zeros would throw away the only record of it. */
  function fill(pick, nameChanged) {
    var nameBox = pick.querySelector("[data-pick-name]");
    var list = pick.querySelector("[data-pick-list]");
    var row = lookup(nameBox.value, list ? list.value : "");
    if (!row) {
      pick.classList.add("is-new");
      return;
    }
    pick.classList.remove("is-new");

    var qtyBox = pick.querySelector("[data-pick-quantity]");
    var units = pick.querySelector("[data-pick-units]");
    var portion = parseFloat(row.portion) || 1;
    var quantity = parseFloat(qtyBox && qtyBox.value);

    /* A number left over from the food this row used to hold means nothing
       once the food has changed: swapping a "1 Portion" curry for a rice
       recorded per 75 grams reads the 1 as one gram, and 3.55 calories is not
       a thing anybody has eaten. The units box is the tell - if it does not
       already say what this food is measured in, the number was about
       something else. The server applies the same rule on save; see
       food_queries.quantity_for(). */
    var same = units && String(units.value || "").trim().toLowerCase() ===
               String(row.units || "").trim().toLowerCase();
    if (!quantity || (nameChanged && !same)) {
      quantity = portion;
      if (qtyBox) qtyBox.value = portion;
    }
    var factor = quantity / portion;

    if (units) units.value = row.units || "";

    var block = pick.parentNode;
    MACROS.forEach(function (key) {
      var box = block.querySelector('[data-pick-macro="' + key + '"]');
      if (box) box.value = Math.round((row[key] || 0) * factor * 100) / 100;
    });
  }

  document.querySelectorAll("[data-pick]").forEach(function (pick) {
    var list = pick.querySelector("[data-pick-list]");
    var grouping = pick.querySelector("[data-pick-grouping]");
    var nameBox = pick.querySelector("[data-pick-name]");
    var quantity = pick.querySelector("[data-pick-quantity]");

    if (list) {
      list.addEventListener("change", function () {
        refillGroupings(pick);
        narrow(pick);
      });
    }
    if (grouping) {
      grouping.addEventListener("change", function () { narrow(pick); });
    }
    if (nameBox) {
      nameBox.addEventListener("change", function () { fill(pick, true); });
      nameBox.addEventListener("blur", function () { fill(pick, true); });
    }
    /* Re-scales only for a row that names a food the catalogue knows. A
       free-text line's macros are the ones that were typed, and multiplying
       them by a quantity entered afterwards would be a guess. */
    if (quantity) {
      quantity.addEventListener("change", function () { fill(pick, false); });
    }

    narrow(pick);
    /* Not a name change: a saved row's quantity is what was actually eaten. */
    if (nameBox && nameBox.value) fill(pick, false);
  });
})();
