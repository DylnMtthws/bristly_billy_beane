(function () {
  "use strict";
  var data = document.getElementById("deck-document-data");
  if (!data) return;
  var state = JSON.parse(data.textContent);
  var root = document.querySelector(".dl-builder");
  var shared = root && root.dataset.shared === "true";
  var playmatEnabled = root && root.dataset.playmatEnabled === "true";
  var csrf = document.querySelector("meta[name='csrf-token']");
  var saveState = document.getElementById("save-state");
  var failedSave = null;
  var pendingSaves = 0;
  var queue = Promise.resolve();
  var recoveryKey = "deck-lab-pending:" + state.id;
  var narrow = window.matchMedia("(max-width: 767px)");
  var activeZoneId = null;
  var selectedEntries = new Set();

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }
  function setSaving(label, error) {
    if (!saveState) return;
    saveState.textContent = label;
    saveState.classList.toggle("error", !!error);
  }
  function mutationId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return Date.now().toString(36) + Math.random().toString(36).slice(2);
  }
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
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? csrf.content : "" },
        body: JSON.stringify(packet)
      }).then(function (response) {
        return response.json().then(function (body) {
          if (response.status === 409) {
            return fetch("/api/decks/" + encodeURIComponent(state.id)).then(function (fresh) { return fresh.json(); }).then(function (latest) {
              state = latest; render(); throw new Error("A newer version was loaded. Reapply your last change.");
            });
          }
          if (!response.ok) throw new Error(body.detail || "The save was refused.");
          state = body;
          failedSave = null;
          try { localStorage.removeItem(recoveryKey); } catch (_) {}
          setSaving("Saved", false);
          render();
        });
      }).catch(function (error) {
        failedSave = packet;
        setSaving("Retry save", true);
        if (saveState) saveState.title = error.message;
      }).finally(function () {
        pendingSaves = Math.max(0, pendingSaves - 1);
      });
    });
    return queue;
  }
  if (saveState) saveState.addEventListener("click", function () {
    if (failedSave) { var retry = failedSave; failedSave = null; command(retry.commands, retry.mutation_id, retry.expected_revision); }
  });

  function preference(key, fallback) {
    return state.preferences && state.preferences[key] || fallback;
  }
  function activeView() { return narrow.matches || !playmatEnabled ? "table" : preference("view_mode", "playmat"); }
  function groups() {
    var result = [];
    var commanders = state.entries.filter(function (e) { return !!e.is_commander; });
    if (commanders.length) result.push({ id: "commander", name: "Commander", entries: commanders, permanent: true });
    if (preference("group_mode", "zone") === "type") {
      var types = {};
      state.entries.filter(function (e) { return !e.is_commander; }).forEach(function (entry) {
        var name = (entry.type_line || "Other").split(/[—-]/)[0].trim() || "Other";
        if (!types[name]) types[name] = [];
        types[name].push(entry);
      });
      Object.keys(types).sort().forEach(function (name) { result.push({ id: "type-" + name, name: name, entries: types[name], permanent: true }); });
    } else {
      state.zones.forEach(function (zone) { result.push({ id: zone.id, name: zone.name, zone: zone, entries: state.entries.filter(function (e) { return !e.is_commander && e.zone_id === zone.id; }) }); });
    }
    var sort = preference("sort_mode", "manual");
    result.forEach(function (group) {
      group.entries.sort(function (a, b) {
        if (sort === "name") return a.name.localeCompare(b.name);
        if (sort === "mana_value") return Number(a.mana_value || 0) - Number(b.mana_value || 0) || a.name.localeCompare(b.name);
        return Number(a.sort_order || 0) - Number(b.sort_order || 0);
      });
    });
    return result;
  }
  function qty(group) { return group.entries.reduce(function (n, entry) { return n + Number(entry.quantity); }, 0); }
  function zoneOptions(selected) {
    var select = node("select", "dl-zone-select");
    state.zones.forEach(function (zone) {
      var option = node("option", "", zone.name); option.value = zone.id; option.selected = zone.id === selected; select.appendChild(option);
    });
    return select;
  }
  function syncSelection(){
    var live=new Set(state.entries.map(function(entry){return entry.id;}));selectedEntries.forEach(function(id){if(!live.has(id))selectedEntries.delete(id);});
    var count=document.querySelector("[data-selected-count]"),button=document.querySelector("[data-bulk-move]");if(count)count.textContent=selectedEntries.size+" selected";if(button)button.disabled=!selectedEntries.size;
  }

  function renderText(group, container) {
    var rows = node("div", "dl-zone-rows");
    group.entries.forEach(function (entry) {
      var row = node("div", "dl-deck-row");
      var q = node("div", "dl-qty");
      if (!shared) {
        var minus = node("button", "", "−"); minus.type = "button"; minus.setAttribute("aria-label", "Remove one " + entry.name);
        minus.addEventListener("click", function () { command([{ type: "adjust_quantity", entry_id: entry.id, delta: -1 }]); });
        q.appendChild(minus);
      }
      q.appendChild(node("span", "", entry.quantity));
      if (!shared) {
        var plus = node("button", "", "+"); plus.type = "button"; plus.setAttribute("aria-label", "Add one " + entry.name);
        plus.addEventListener("click", function () { command([{ type: "adjust_quantity", entry_id: entry.id, delta: 1 }]); });
        q.appendChild(plus);
      }
      var name = node("div", "dl-card-name", entry.name);
      name.appendChild(node("small", "", entry.role || entry.type_line || "Card"));
      if(!entry.is_commander&&!shared){var choose=node("input","dl-row-select");choose.type="checkbox";choose.checked=selectedEntries.has(entry.id);choose.setAttribute("aria-label","Select "+entry.name);choose.addEventListener("change",function(){if(choose.checked)selectedEntries.add(entry.id);else selectedEntries.delete(entry.id);syncSelection();});row.appendChild(choose);}else row.appendChild(node("span",""));
      row.append(q, name, node("span", "dl-mono", entry.mana_cost || String(entry.mana_value || "—")), node("span", "dl-muted", entry.type_line || "—"));
      if (!entry.is_commander && !shared) {
        var select = zoneOptions(entry.zone_id);
        select.setAttribute("aria-label", "Move " + entry.name + " to zone");
        select.addEventListener("change", function () { command([{ type: "move_entry", entry_id: entry.id, zone_id: select.value, sort_order: 999 }]); });
        row.appendChild(select);
      } else row.appendChild(node("span", "dl-muted", entry.is_commander ? "Command" : ""));
      var actions = node("div", "dl-row-actions");
      if (entry.image_uri) {
        var preview = node("a", "dl-icon-button", "◫"); preview.href = entry.image_uri; preview.target = "_blank"; preview.rel = "noopener"; preview.setAttribute("aria-label", "Open card image for " + entry.name); actions.appendChild(preview);
      }
      if (!shared) { var remove = node("button", "dl-icon-button", "×"); remove.type="button"; remove.setAttribute("aria-label", "Remove " + entry.name); remove.addEventListener("click", function () { command([{type:"remove_entry",entry_id:entry.id}]); }); actions.appendChild(remove); }
      row.appendChild(actions); rows.appendChild(row);
    });
    container.appendChild(rows);
  }
  function renderGrid(group, container) {
    var grid = node("div", "dl-grid-display");
    group.entries.forEach(function (entry) {
      var card = node("div", "dl-grid-card"); card.title = entry.name;
      if (entry.image_uri) { var img=node("img");img.src=entry.image_uri;img.alt=entry.name;img.loading="lazy";card.appendChild(img); }
      else card.appendChild(node("div","fallback",entry.name));
      card.appendChild(node("b","",entry.quantity+"×")); grid.appendChild(card);
    }); container.appendChild(grid);
  }
  function renderSpoiler(group, container) {
    var grid = node("div", "dl-spoiler-display");
    group.entries.forEach(function (entry) {
      var card=node("article","dl-spoiler-card");
      if(entry.image_uri){var img=node("img");img.src=entry.image_uri;img.alt="";img.loading="lazy";card.appendChild(img);}else card.appendChild(node("div","dl-card-art"));
      var body=node("div");body.append(node("strong","",entry.quantity+"× "+entry.name),node("p","",entry.oracle_text||entry.type_line||"Card details unavailable."));card.appendChild(body);grid.appendChild(card);
    });container.appendChild(grid);
  }
  function renderTable() {
    var view=document.getElementById("table-view"); if(!view)return; view.replaceChildren();
    var collapsed = [];
    try { collapsed=JSON.parse(preference("collapsed_json","[]")); } catch(_){ collapsed=[]; }
    var display=preference("display_mode","text");
    groups().forEach(function(group){
      var section=node("section","dl-zone-section");section.id="zone-"+group.id;
      var head=node("div","dl-zone-heading");var toggle=node("button","",collapsed.indexOf(group.id)>=0?"▸":"▾");toggle.type="button";toggle.setAttribute("aria-label","Collapse "+group.name);head.append(toggle,node("h2","",group.name),node("span","dl-mono",qty(group)));
      if(group.zone&&!shared){var selectAll=node("button","","Select all");selectAll.type="button";selectAll.addEventListener("click",function(){var ids=group.entries.map(function(entry){return entry.id;}),all=ids.length&&ids.every(function(id){return selectedEntries.has(id);});ids.forEach(function(id){if(all)selectedEntries.delete(id);else selectedEntries.add(id);});renderTable();syncSelection();});head.appendChild(selectAll);var layout=node("button","",group.zone.layout_mode==="fan"?"Spread":"Fan");layout.type="button";layout.addEventListener("click",function(){command([{type:"set_zone_layout",zone_id:group.id,layout:group.zone.layout_mode==="fan"?"spread":"fan"}]);});head.appendChild(layout);var rename=node("button","","Rename");rename.type="button";rename.addEventListener("click",function(){var name=prompt("Zone name",group.name);if(name)command([{type:"rename_zone",zone_id:group.id,name:name}]);});head.appendChild(rename);if(group.name.toLowerCase()!=="unsorted"){var del=node("button","","Delete");del.type="button";del.addEventListener("click",function(){if(confirm("Delete "+group.name+"? Its cards will move to Unsorted."))command([{type:"delete_zone",zone_id:group.id}]);});head.appendChild(del);}}
      section.appendChild(head);var body=node("div");body.hidden=collapsed.indexOf(group.id)>=0;section.appendChild(body);
      toggle.addEventListener("click",function(){var next=collapsed.indexOf(group.id)>=0?collapsed.filter(function(id){return id!==group.id;}):collapsed.concat([group.id]);command([{type:"update_view",collapsed:next}]);});
      if(display==="grid")renderGrid(group,body);else if(display==="spoiler")renderSpoiler(group,body);else renderText(group,body);view.appendChild(section);
    });
    if(!state.entries.length)view.appendChild(node("div","dl-empty","Choose a commander or add cards to begin."));
  }
  function renderStats(){
    var stats=document.getElementById("deck-stats"),zoneStats=document.getElementById("zone-stats"),issues=document.getElementById("validation-issues");if(!stats)return;stats.replaceChildren();zoneStats.replaceChildren();issues.replaceChildren();
    [["Library",state.validation.library_count+" / 99"],["Commanders",state.validation.commander_count],["Status",state.validation.legal?"Legal":"Draft"]].forEach(function(pair){var d=node("div");d.append(node("span","",pair[0]),node("b","",pair[1]));stats.appendChild(d);});
    groups().forEach(function(group){var d=node("div");d.append(node("span","",group.name),node("b","",qty(group)));zoneStats.appendChild(d);});
    state.validation.issues.forEach(function(issue){issues.appendChild(node("p","dl-disclosure",issue));});
    var badge=document.getElementById("deck-validation");if(badge)badge.textContent=state.validation.library_count+" + "+state.validation.commander_count+" · "+(state.validation.legal?"legal":"draft");var title=document.getElementById("deck-title");if(title)title.textContent=state.title;
  }
  function renderPlaymat(){
    var mat=document.getElementById("playmat");if(!mat)return;mat.replaceChildren();var p=state.presentation||{};mat.className="dl-playmat "+(p.surface||"slate-grid")+(p.show_zone_outlines?" show-zone-outlines":"")+(p.dim_inactive?" dim-inactive":"");mat.style.transform="translate("+(p.pan_x||0)+"px,"+(p.pan_y||0)+"px) scale("+(p.zoom||1)+")";mat.style.backgroundImage=p.surface==="custom"?"url('"+(shared?location.pathname+"/playmat":"/api/decks/"+encodeURIComponent(state.id)+"/playmat")+"')":"";mat.style.backgroundSize=p.surface==="custom"?"cover":"";
    var commanders=state.entries.filter(function(e){return !!e.is_commander;});if(commanders.length){var commandBox=node("section","dl-mat-zone dl-mat-command");commandBox.style.left="30px";commandBox.style.top="25px";commandBox.appendChild(node("h2","","Commander · "+qty({entries:commanders})));var commandCards=node("div","dl-mat-cards");commanders.forEach(function(entry){var card=node("div","dl-mat-card",entry.image_uri?"":entry.name);if(entry.image_uri){var image=node("img");image.src=entry.image_uri;image.alt=entry.name;card.appendChild(image);}commandCards.appendChild(card);});commandBox.appendChild(commandCards);mat.appendChild(commandBox);}
    state.zones.forEach(function(zone,index){var box=node("section","dl-mat-zone "+(zone.layout_mode||"spread")+(activeZoneId===zone.id?" active":""));box.dataset.zoneId=zone.id;box.tabIndex=0;box.setAttribute("aria-label",zone.name+" zone");box.style.left=(zone.x==null?80+index*270:zone.x)+"px";box.style.top=(zone.y==null?120:zone.y)+"px";box.addEventListener("click",function(){activeZoneId=zone.id;renderPlaymat();});var heading=node("h2","",zone.name+" · "+state.entries.filter(function(e){return e.zone_id===zone.id&&!e.is_commander;}).reduce(function(n,e){return n+Number(e.quantity);},0));box.appendChild(heading);var cards=node("div","dl-mat-cards");state.entries.filter(function(e){return e.zone_id===zone.id&&!e.is_commander;}).forEach(function(entry,cardIndex){var c=node("div","dl-mat-card",entry.image_uri?"":entry.name);c.style.setProperty("--card-index",cardIndex);c.draggable=!shared;c.dataset.entryId=entry.id;c.title=entry.name;if(entry.image_uri){var img=node("img");img.src=entry.image_uri;img.alt=entry.name;img.loading="lazy";c.appendChild(img);}c.addEventListener("dragstart",function(ev){ev.dataTransfer.setData("text/deck-entry",entry.id);c.classList.add("dragging");});c.addEventListener("dragend",function(){c.classList.remove("dragging");document.querySelectorAll(".dl-mat-zone.drop-target").forEach(function(target){target.classList.remove("drop-target");});});cards.appendChild(c);});box.appendChild(cards);box.addEventListener("dragover",function(ev){ev.preventDefault();box.classList.add("drop-target");});box.addEventListener("dragleave",function(){box.classList.remove("drop-target");});box.addEventListener("drop",function(ev){ev.preventDefault();box.classList.remove("drop-target");var id=ev.dataTransfer.getData("text/deck-entry");if(id)command([{type:"move_entry",entry_id:id,zone_id:zone.id,sort_order:999}]);});
      if(!shared){heading.style.cursor="move";heading.addEventListener("pointerdown",function(ev){if(ev.button!==0)return;heading.setPointerCapture(ev.pointerId);var startX=ev.clientX,startY=ev.clientY,left=parseFloat(box.style.left),top=parseFloat(box.style.top);function move(me){box.style.left=(left+(me.clientX-startX)/(p.zoom||1))+"px";box.style.top=(top+(me.clientY-startY)/(p.zoom||1))+"px";}function up(ue){heading.removeEventListener("pointermove",move);heading.removeEventListener("pointerup",up);var x=parseFloat(box.style.left),y=parseFloat(box.style.top);if(p.snap_to_grid){x=Math.round(x/20)*20;y=Math.round(y/20)*20;}command([{type:"move_zone",zone_id:zone.id,x:x,y:y}]);}heading.addEventListener("pointermove",move);heading.addEventListener("pointerup",up);});}
      mat.appendChild(box);
    });var label=document.getElementById("zoom-label");if(label)label.textContent=Math.round((p.zoom||1)*100)+"%";
  }
  function syncControls(){
    var view=activeView(),display=preference("display_mode","text");document.querySelectorAll("[data-view]").forEach(function(b){b.setAttribute("aria-pressed",b.dataset.view===view?"true":"false");});document.querySelectorAll("[data-display]").forEach(function(b){b.setAttribute("aria-pressed",b.dataset.display===display?"true":"false");});var group=document.querySelector("[data-group]"),sort=document.querySelector("[data-sort]");if(group)group.value=preference("group_mode","zone");if(sort)sort.value=preference("sort_mode","manual");var table=document.getElementById("table-view"),playmat=document.getElementById("playmat-view");if(table)table.hidden=view!=="table";if(playmat)playmat.hidden=view!=="playmat";
    document.querySelectorAll("[data-add-zone],[data-bulk-zone]").forEach(function(zoneSelect){var current=zoneSelect.value;zoneSelect.replaceChildren();state.zones.forEach(function(z){var o=node("option","",z.name);o.value=z.id;zoneSelect.appendChild(o);});if(state.zones.some(function(z){return z.id===current;}))zoneSelect.value=current;});
    document.querySelectorAll("[data-surface]").forEach(function(button){button.classList.toggle("active",button.dataset.surface===(state.presentation||{}).surface);});document.querySelectorAll("[data-setting]").forEach(function(input){input.checked=!!(state.presentation||{})[input.dataset.setting];});
  }
  function render(){syncControls();renderTable();renderPlaymat();renderStats();syncSelection();}

  document.querySelectorAll("[data-view]").forEach(function(b){b.addEventListener("click",function(){command([{type:"update_view",view_mode:b.dataset.view}]);});});
  document.querySelectorAll("[data-display]").forEach(function(b){b.addEventListener("click",function(){command([{type:"update_view",display_mode:b.dataset.display}]);});});
  var groupControl=document.querySelector("[data-group]");if(groupControl)groupControl.addEventListener("change",function(){command([{type:"update_view",group_mode:groupControl.value}]);});
  var sortControl=document.querySelector("[data-sort]");if(sortControl)sortControl.addEventListener("change",function(){command([{type:"update_view",sort_mode:sortControl.value}]);});
  var bulkMove=document.querySelector("[data-bulk-move]");if(bulkMove)bulkMove.addEventListener("click",function(){var zone=document.querySelector("[data-bulk-zone]");if(!zone||!selectedEntries.size)return;var changes=Array.from(selectedEntries).map(function(id,index){return {type:"move_entry",entry_id:id,zone_id:zone.value,sort_order:999+index};});selectedEntries.clear();command(changes);});
  document.querySelectorAll("[data-new-zone]").forEach(function(b){b.addEventListener("click",function(){var name=prompt("Name the new zone","New zone");if(name)command([{type:"create_zone",name:name}]);});});
  var title=document.getElementById("deck-title");if(title&&!shared){title.title="Click to rename";title.style.cursor="text";title.addEventListener("click",function(){var name=prompt("Deck name",state.title);if(name&&name!==state.title)command([{type:"rename_deck",title:name}]);});}
  document.querySelectorAll("[data-zoom]").forEach(function(b){b.addEventListener("click",function(){var zoom=Number((state.presentation||{}).zoom||1),change={type:"update_presentation"};if(b.dataset.zoom==="in")zoom+=.1;else if(b.dataset.zoom==="out")zoom-=.1;else{zoom=.8;change.pan_x=0;change.pan_y=0;}change.zoom=zoom;command([change]);});});
  var spaceHeld=false;document.addEventListener("keydown",function(event){if(event.code==="Space"&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)){spaceHeld=true;event.preventDefault();}});document.addEventListener("keyup",function(event){if(event.code==="Space")spaceHeld=false;});var panMat=document.getElementById("playmat");if(panMat&&!shared)panMat.addEventListener("pointerdown",function(event){if(!spaceHeld||event.target!==panMat)return;event.preventDefault();panMat.setPointerCapture(event.pointerId);var p=state.presentation||{},startX=event.clientX,startY=event.clientY,panX=Number(p.pan_x||0),panY=Number(p.pan_y||0);function move(me){panMat.style.transform="translate("+(panX+me.clientX-startX)+"px,"+(panY+me.clientY-startY)+"px) scale("+(p.zoom||1)+")";}function up(ue){panMat.removeEventListener("pointermove",move);panMat.removeEventListener("pointerup",up);command([{type:"update_presentation",pan_x:panX+ue.clientX-startX,pan_y:panY+ue.clientY-startY}]);}panMat.addEventListener("pointermove",move);panMat.addEventListener("pointerup",up);});

  var addPanel=document.getElementById("add-panel");function openAdd(open){if(!addPanel)return;addPanel.classList.toggle("open",open);addPanel.setAttribute("aria-hidden",open?"false":"true");if(open){var input=addPanel.querySelector("[data-card-search]");if(input)input.focus();}}
  if(panMat&&!shared)panMat.addEventListener("dblclick",function(event){if(event.target===panMat||event.target.classList.contains("dl-mat-zone")){var select=document.querySelector("[data-add-zone]");if(select&&activeZoneId)select.value=activeZoneId;openAdd(true);}});
  document.querySelectorAll("[data-add-open]").forEach(function(b){b.addEventListener("click",function(){openAdd(true);});});var addClose=document.querySelector("[data-add-close]");if(addClose)addClose.addEventListener("click",function(){openAdd(false);});
  var searchMode="simple";document.querySelectorAll("[data-search-mode]").forEach(function(b){b.addEventListener("click",function(){searchMode=b.dataset.searchMode;document.querySelectorAll("[data-search-mode]").forEach(function(x){x.setAttribute("aria-pressed",x===b?"true":"false");});var advanced=document.querySelector("[data-advanced]");if(advanced)advanced.hidden=searchMode!=="advanced";});});
  var searchTimer,searchController;function runSearch(){var input=document.querySelector("[data-card-search]"),results=document.querySelector("[data-card-results]");if(!input||!results)return;var params=new URLSearchParams({q:input.value,deck_id:state.id});if(searchMode==="advanced"){var oracle=document.querySelector("[data-oracle-search]"),type=document.querySelector("[data-type-search]"),mana=document.querySelector("[data-mana-search]"),rarity=document.querySelector("[data-rarity-search]");if(oracle&&oracle.value)params.set("oracle_text",oracle.value);if(type&&type.value)params.set("type_line",type.value);if(mana&&mana.value)params.set("mana_max",mana.value);if(rarity&&rarity.value)params.set("rarity",rarity.value);}if(searchController)searchController.abort();searchController=new AbortController();results.textContent="Searching…";fetch("/api/cards?"+params.toString(),{signal:searchController.signal}).then(function(r){if(!r.ok)throw new Error();return r.json();}).then(function(body){var scope=document.querySelector("[data-search-scope]");if(scope)scope.textContent=body.scope;results.replaceChildren();body.results.forEach(function(card){var row=node("div","dl-search-result");var copy=node("div");copy.append(node("strong","",card.name),node("small","",(card.type_line||"Card")+" · MV "+(card.mana_value==null?"—":card.mana_value)));var add=node("button","dl-icon-button","+");add.type="button";add.setAttribute("aria-label","Add "+card.name);add.addEventListener("click",function(){var zone=document.querySelector("[data-add-zone]");command([{type:"add_card",card_id:card.id,zone_id:zone.value,quantity:1}]);});row.append(copy,add);results.appendChild(row);});if(!body.results.length)results.textContent="No legal cards match this search.";}).catch(function(e){if(e.name!=="AbortError")results.textContent="Search unavailable.";});}
  var searchInput=document.querySelector("[data-card-search]");if(searchInput)searchInput.addEventListener("input",function(){clearTimeout(searchTimer);searchTimer=setTimeout(runSearch,180);});var searchSubmit=document.querySelector("[data-card-search-submit]");if(searchSubmit)searchSubmit.addEventListener("click",runSearch);

  var picker=document.getElementById("playmat-picker");
  var pickerButton=document.querySelector("[data-playmat-picker]");
  var pickerOriginal=null,pickerDraft=null,pendingUpload=null;
  var upload=document.querySelector("[data-playmat-upload]");
  var uploadStatus=document.querySelector("[data-playmat-upload-status]");
  function previewPicker(){if(!pickerDraft)return;state.presentation=Object.assign({},pickerDraft);render();}
  function uploadPlaymat(file){
    pendingSaves+=1;
    queue=queue.then(function(){
      if(failedSave){pendingSaves=Math.max(0,pendingSaves-1);return;}
      var form=new FormData();form.append("playmat",file);setSaving("Uploading…",false);
      return fetch("/api/decks/"+encodeURIComponent(state.id)+"/playmat",{method:"POST",headers:{"X-CSRFToken":csrf?csrf.content:""},body:form})
        .then(function(r){return r.json().then(function(body){if(!r.ok)throw new Error(body.error);return fetch("/api/decks/"+encodeURIComponent(state.id));});})
        .then(function(r){return r.json();}).then(function(body){state=body;setSaving("Saved",false);render();})
        .catch(function(error){setSaving(error.message||"Upload failed",true);})
        .finally(function(){pendingSaves=Math.max(0,pendingSaves-1);});
    });
  }
  if(pickerButton&&picker)pickerButton.addEventListener("click",function(){
    pickerOriginal=Object.assign({},state.presentation||{});pickerDraft=Object.assign({},pickerOriginal);pendingUpload=null;picker.returnValue="";
    if(upload)upload.value="";if(uploadStatus)uploadStatus.textContent="";picker.showModal();syncControls();
  });
  document.querySelectorAll("[data-surface]").forEach(function(b){b.addEventListener("click",function(){if(!pickerDraft)return;pickerDraft.surface=b.dataset.surface;pendingUpload=null;if(upload)upload.value="";if(uploadStatus)uploadStatus.textContent="";previewPicker();});});
  document.querySelectorAll("[data-setting]").forEach(function(input){input.addEventListener("change",function(){if(!pickerDraft)return;pickerDraft[input.dataset.setting]=input.checked;previewPicker();});});
  if(upload)upload.addEventListener("change",function(){pendingUpload=upload.files.length?upload.files[0]:null;if(uploadStatus)uploadStatus.textContent=pendingUpload?pendingUpload.name+" is ready to upload.":"";});
  if(picker)picker.addEventListener("close",function(){
    if(picker.returnValue==="done"&&pickerDraft){
      var change={type:"update_presentation"};
      ["snap_to_grid","show_zone_outlines","dim_inactive"].forEach(function(key){change[key]=!!pickerDraft[key];});
      if(!pendingUpload)change.surface=pickerDraft.surface||"slate-grid";
      command([change]);if(pendingUpload)uploadPlaymat(pendingUpload);
    }else if(pickerOriginal){state.presentation=pickerOriginal;render();}
    pickerOriginal=null;pickerDraft=null;pendingUpload=null;
  });
  var share=document.querySelector("[data-share]");if(share)share.addEventListener("click",function(){fetch("/api/decks/"+encodeURIComponent(state.id)+"/share",{method:"POST",headers:{"X-CSRFToken":csrf?csrf.content:""}}).then(function(r){return r.json();}).then(function(body){if(navigator.clipboard)navigator.clipboard.writeText(body.url);prompt("Read-only link (copied when your browser allows it)",body.url);});});
  document.addEventListener("click",function(event){
    var anchor=event.target.closest&&event.target.closest("a[href]");
    if(!anchor||!pendingSaves||event.defaultPrevented||anchor.target||anchor.hasAttribute("download"))return;
    var target=new URL(anchor.href,location.href);if(target.origin!==location.origin)return;
    event.preventDefault();queue.then(function(){if(!failedSave)location.assign(target.href);else if(saveState)saveState.focus();});
  },true);
  narrow.addEventListener("change",render);render();
  try { var pending=JSON.parse(localStorage.getItem(recoveryKey));if(pending&&pending.commands)command(pending.commands,pending.mutation_id,pending.expected_revision); } catch(_) {}
})();
