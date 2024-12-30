document.addEventListener("DOMContentLoaded", () => {
  const enemyCont   = document.getElementById("enemy-heroes");
  const teamCont    = document.getElementById("team-heroes");
  const recBtn      = document.getElementById("get-recommendations");
  const recOutput   = document.getElementById("recommendations-output");
  const heroSearch  = document.getElementById("hero-search");

  // NEW: spinner references
  const spinner     = document.getElementById("spinner");

  const enemySelected = new Set();
  const teamSelected  = new Set();

  // Show/hide spinner
  function showSpinner() {
    if(spinner) spinner.style.display = "inline-block";
  }
  function hideSpinner() {
    if(spinner) spinner.style.display = "none";
  }
  // Hide on page load
  hideSpinner();

  // 1) Filter heroes by name
  if(heroSearch){
    heroSearch.addEventListener("input", e => {
      const val = e.target.value.toLowerCase().trim();
      document.querySelectorAll(".hero").forEach(h => {
        const nm = h.dataset.name.toLowerCase();
        h.style.display = nm.includes(val) ? "" : "none";
      });
    });
  }

  // 2) Enemy picks
  enemyCont.addEventListener("click", e => {
    const heroEl = e.target.closest(".hero");
    if(!heroEl) return;
    toggleHeroPick(heroEl, enemySelected, "enemy");
  });

  // 3) Team picks
  teamCont.addEventListener("click", e => {
    const heroEl = e.target.closest(".hero");
    if(!heroEl) return;
    toggleHeroPick(heroEl, teamSelected, "team");
  });

  function toggleHeroPick(heroEl, setObj, side){
    const hId = heroEl.dataset.id;
    if(setObj.has(hId)){
      setObj.delete(hId);
      heroEl.classList.remove("selected");
      syncHero(hId, false, side);
    } else {
      if(setObj.size >= 5){
        alert(`Max 5 picks on ${side} side!`);
        return;
      }
      setObj.add(hId);
      heroEl.classList.add("selected");
      syncHero(hId, true, side);
    }
  }

  function syncHero(heroId, isSelected, side){
    const other = (side==="enemy" ? teamCont : enemyCont);
    const otherHero = other.querySelector(`.hero[data-id='${heroId}']`);
    if(otherHero){
      if(isSelected){
        otherHero.classList.add("unselectable");
      } else {
        otherHero.classList.remove("unselectable");
      }
    }
  }

  // 4) "Get Recommendations"
  recBtn.addEventListener("click", async ()=>{
    if(![2,4].includes(enemySelected.size)){
      alert(`Pick exactly 2 or 4 enemy heroes! (You have ${enemySelected.size} selected)`);
      return;
    }
    // show spinner
    showSpinner();

    const enemyNames = Array.from(enemySelected).map(id => {
      const el = enemyCont.querySelector(`.hero[data-id='${id}']`);
      return el ? el.dataset.name : "";
    });
    const teamNames = Array.from(teamSelected).map(id => {
      const el = teamCont.querySelector(`.hero[data-id='${id}']`);
      return el ? el.dataset.name : "";
    });

    try{
      const resp = await fetch("/recommendations", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          enemy_heroes: enemyNames,
          team_heroes:  teamNames
        })
      });
      const data = await resp.json();
      if(data.error){
        recOutput.innerHTML = `<div class="alert alert-danger">${data.error}</div>`;
        return;
      }
      renderRecs(data.recommendations);
    } catch(err){
      console.error(err);
      recOutput.innerHTML = `<div class="alert alert-danger">Failed synergy fetch. Check console.</div>`;
    } finally {
      hideSpinner();
    }
  });

  // 5) Render synergy results
  function renderRecs(recs){
    recOutput.innerHTML="";
    if(!recs.length){
      recOutput.innerHTML= `<div class="alert alert-info">No synergy found (maybe < ${MIN_GAMES} games?).</div>`;
      return;
    }
    recs.forEach(r=>{
      const div= document.createElement("div");
      div.className = "recommendation border p-2 mb-2";

      const advClass= (r.advantage >=0) ? "text-success":"text-danger";
      const wrClass = (r.win_rate >=50) ? "text-success":"text-danger";

      const summary= `
        <strong>${r.name}</strong>
        <span class="text-muted"> counters </span> 
        <em>${r.enemy_hero}</em><br/>
        Win Rate: <span class="${wrClass}">${r.win_rate.toFixed(2)}%</span> |
        Advantage: <span class="${advClass}">${r.advantage.toFixed(2)}</span> |
        Games: ${r.games_played}
      `;
      div.innerHTML= `
        <div class="d-flex">
          <img src="${r.image}" alt="${r.name}" style="height:64px; margin-right:8px;">
          <div>
            <p class="mb-1">${summary}</p>
            <p class="mb-1">${r.description}</p>
          </div>
        </div>
      `;

      if(r.personal_stats){
        const ps= r.personal_stats;
        div.innerHTML += `
          <p class="mb-1">
            <strong>Your Stats:</strong>
            ${ps.games} games, ${ps.wins} wins (${ps.wr.toFixed(2)}% WR)
          </p>
        `;
      }

      // item button
      const itemBtn= document.createElement("button");
      itemBtn.className= "btn btn-sm btn-info mt-2";
      itemBtn.textContent= "Show Item Build";

      const itemDiv= document.createElement("div");
      itemDiv.className= "item-build-container mt-2";
      itemDiv.style.display= "none";

      itemBtn.addEventListener("click", async ()=>{
        // show spinner
        showSpinner();
        try{
          if(itemDiv.innerHTML===""){
            const iresp = await fetch(`/itembuild/${r.id}`);
            const phases= await iresp.json();
            itemDiv.innerHTML= buildItemsHTML(phases);
          }
          // toggle
          itemDiv.style.display= (itemDiv.style.display==="none")?"block":"none";
        } catch(e){
          console.error(e);
          itemDiv.innerHTML= `<div class="alert alert-danger">Error loading item build!</div>`;
        } finally {
          hideSpinner();
        }
      });

      div.appendChild(itemBtn);
      div.appendChild(itemDiv);

      recOutput.appendChild(div);
    });
  }

  // 6) Build item HTML
  function buildItemsHTML(ph){
    // { start_items:[], early_game:[], mid_game:[], late_game:[] }
    let html="";
    const sections= [
      {key:"start_items", label:"Starting Items"},
      {key:"early_game",  label:"Early Game"},
      {key:"mid_game",    label:"Mid Game"},
      {key:"late_game",   label:"Late Game"}
    ];
    sections.forEach(s=>{
      const arr= ph[s.key]||[];
      if(arr.length>0){
        html+= `<h5>${s.label}</h5><div class="d-flex flex-wrap gap-3 mb-2">`;
        arr.forEach(it=>{
          html+=`
            <div class="text-center">
              <img src="${it.image}" alt="${it.name}" style="max-height:50px;">
              <p class="mb-0">${it.name}</p>
              <small>${it.time}</small>
            </div>
          `;
        });
        html+="</div>";
      }
    });
    if(!html){
      html=`<div class="alert alert-info">No item data from timeline or fallback!</div>`;
    }
    return html;
  }
});