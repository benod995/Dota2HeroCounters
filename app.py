import os
import json
from math import floor
from statistics import mean, StatisticsError
from functools import lru_cache

import requests
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify
)

app = Flask(__name__)
app.secret_key = "SOME_RANDOM_SECRET_KEY"

#########################################
# GLOBAL CONSTANTS
#########################################
OPEN_DOTA_BASE   = "https://api.opendota.com/api"
STEAM_ITEM_BASE  = "https://steamcdn-a.akamaihd.net/apps/dota2/images/items/"
MIN_GAMES        = 50     # skip synergy heroes with fewer than 50 games
PRO_MATCH_SAMPLE = 30     # parse up to 30 pro matches
REQUEST_TIMEOUT  = 20     # increased to 20s to reduce item fetch timeouts

ITEM_NAME_FIXES  = {}
ALTERNATIVE_ITEMS_GROUPS = [
    {"manta_style","sange_and_yasha","kaya_and_sange","yasha_and_kaya"},
    {"skadi","heart"}
]

#########################################
# LOAD LOCAL JSON FOR DESCRIPTIONS
#########################################
@lru_cache(maxsize=1)
def load_local_counters():
    json_path = "dota_hero_counters.json"
    if not os.path.exists(json_path):
        print(f"[WARN] {json_path} not found; returning empty dict.")
        return {}
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

def fix_item_name(raw: str):
    corrected = ITEM_NAME_FIXES.get(raw, raw)
    disp = corrected.replace("_", " ").title()
    return corrected, disp

def are_alternative(i1:str, i2:str)->bool:
    for grp in ALTERNATIVE_ITEMS_GROUPS:
        if i1 in grp and i2 in grp:
            return True
    return False

#########################################
# FETCH HERO LIST FROM OPENDOTA
#########################################
@lru_cache(maxsize=1)
def fetch_raw_heroes():
    try:
        r = requests.get(f"{OPEN_DOTA_BASE}/heroes", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except:
        return []

@lru_cache(maxsize=1)
def build_hero_map():
    raw = fetch_raw_heroes()
    out = {}
    for h in raw:
        hid   = h["id"]
        short = h["name"].replace("npc_dota_hero_", "")
        out[hid] = {
            "id": hid,
            "localized_name": h["localized_name"],
            "image": f"https://steamcdn-a.akamaihd.net/apps/dota2/images/heroes/{short}_lg.png"
        }
    return out

@lru_cache(maxsize=1)
def fetch_hero_list():
    # For listing heroes in the front-end
    hero_map = build_hero_map()
    return list(hero_map.values())

#########################################
# PERSONAL STATS
#########################################
def fetch_personal_stats(account_id: str):
    if not account_id:
        return {}
    try:
        url = f"{OPEN_DOTA_BASE}/players/{account_id}/heroes"
        r   = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data= r.json()
        ret={}
        for row in data:
            hid   = row["hero_id"]
            gm    = row["games"]
            w     = row["win"]
            wr    = round((w/gm)*100, 2) if gm>0 else 0
            ret[hid] = {"games": gm, "wins": w, "wr": wr}
        return ret
    except:
        return {}

#########################################
# MATCHUPS / SYNERGY
#########################################
@lru_cache(maxsize=128)
def fetch_matchups(hero_id: int):
    try:
        url = f"{OPEN_DOTA_BASE}/heroes/{hero_id}/matchups"
        r   = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except:
        return []

def synergy_from_opendota(enemy_id: int, personal_map: dict)->list:
    matchups = fetch_matchups(enemy_id)
    synergy=[]
    for row in matchups:
        gp = row["games_played"]
        if gp < MIN_GAMES:
            continue
        w  = row["wins"]
        wr = (w/gp)*100
        adv= wr - 50
        synergy.append((row["hero_id"], gp, wr, adv))
    synergy.sort(key=lambda x:x[3], reverse=True)
    synergy = synergy[:2]

    hero_map = build_hero_map()
    results = []
    for (cid, gp, wr, adv) in synergy:
        synergy_hero = hero_map.get(cid)
        if not synergy_hero:
            continue

        synergy_name = synergy_hero["localized_name"]
        local_data   = load_local_counters()
        enemy_name   = hero_map[enemy_id]["localized_name"]

        base_desc = local_data.get(synergy_name, {}).get(
            enemy_name,
            f"{synergy_name} counters {enemy_name} effectively."
        )
        final_desc= f"{synergy_name} vs. {enemy_name}: {base_desc}"

        # personal stats => fallback 0
        pstats = personal_map.get(cid, {"games":0,"wins":0,"wr":0.0})

        results.append({
            "id": cid,
            "name": synergy_name,
            "image": synergy_hero["image"],
            "win_rate": round(wr,2),
            "advantage": round(adv,2),
            "games_played": gp,
            "description": final_desc,
            "personal_stats": pstats
        })

    return results

@app.route("/recommendations", methods=["POST"])
def recommendations():
    data= request.json
    if not data:
        return jsonify({"error":"No JSON payload"}),400

    enemy_heroes= data.get("enemy_heroes", [])
    if len(enemy_heroes)==0:
        return jsonify({"error":"No enemy heroes selected."}),400
    if len(enemy_heroes) not in [2,4]:
        return jsonify({"error":"Pick exactly 2 or 4 enemy heroes."}),400

    hero_map    = build_hero_map()
    od_id       = session.get("opendota_id")
    personal_map= fetch_personal_stats(od_id) if od_id else {}

    final_list= []
    for ehero_name in enemy_heroes:
        # find matching hero ID
        enemy_id= None
        for hid, obj in hero_map.items():
            if obj["localized_name"] == ehero_name:
                enemy_id = hid
                break
        if not enemy_id:
            continue
        synergy_list = synergy_from_opendota(enemy_id, personal_map)
        for s in synergy_list:
            s["enemy_hero"] = ehero_name
        final_list.extend(synergy_list)

    return jsonify({"recommendations": final_list})

#########################################
# PRO MATCHES / ITEM BUILDS
#########################################
@lru_cache(maxsize=128)
def fetch_pro_matches(hero_id:int):
    try:
        url= f"{OPEN_DOTA_BASE}/proMatches?hero_id={hero_id}"
        r= requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data= r.json()
        return data[:PRO_MATCH_SAMPLE]
    except:
        return []

def gather_timeline(hero_id:int):
    pm= fetch_pro_matches(hero_id)
    purchase_map={}
    for match_info in pm:
        mid= match_info.get("match_id")
        if not mid: 
            continue
        try:
            detail_url= f"{OPEN_DOTA_BASE}/matches/{mid}"
            rr= requests.get(detail_url, timeout=REQUEST_TIMEOUT)
            rr.raise_for_status()
            data= rr.json()
            hply= None
            for p in data.get("players", []):
                if p.get("hero_id")== hero_id:
                    hply= p
                    break
            if hply and "purchase_log" in hply:
                for evt in hply["purchase_log"]:
                    k= evt["key"]
                    if k.startswith("recipe_"):
                        continue
                    if k in ["tpscroll","town_portal_scroll"]:
                        continue
                    purchase_map.setdefault(k,[]).append(evt["time"])
        except:
            continue

    items=[]
    for (k,v) in purchase_map.items():
        if len(v)>0:
            try:
                avg_t= mean(v)
                items.append((k,avg_t))
            except StatisticsError:
                pass
    items.sort(key=lambda x:x[1])
    return items

def group_alternatives(lst):
    used=set()
    res=[]
    i=0
    while i<len(lst):
        nm1,t1= lst[i]
        if i+1<len(lst):
            nm2,t2= lst[i+1]
            if abs(t1-t2)<120 and are_alternative(nm1,nm2):
                c= f"{nm1}|{nm2}"
                at=(t1+t2)/2
                res.append((c,at))
                used.add(i)
                used.add(i+1)
                i+=2
                continue
        if i not in used:
            res.append((nm1,t1))
        i+=1
    res.sort(key=lambda x:x[1])
    return res

def parse_phases(lst):
    start=[]
    early=[]
    mid=[]
    late=[]
    for (raw,tv) in lst:
        if tv<180:     # up to 3m => start
            start.append((raw,tv))
        elif tv<900:   # up to 15m => early
            early.append((raw,tv))
        elif tv<1800:  # up to 30m => mid
            mid.append((raw,tv))
        else:
            late.append((raw,tv))

    return {
      "start_items": parse_phase_list(start),
      "early_game":  parse_phase_list(early),
      "mid_game":    parse_phase_list(mid),
      "late_game":   parse_phase_list(late)
    }

def parse_phase_list(arr):
    out=[]
    for (raw,valSec) in arr:
        if "|" in raw:
            i1,i2= raw.split("|")
            c1,d1= fix_item_name(i1)
            c2,d2= fix_item_name(i2)
            disp= f"{d1} OR {d2}"
            img= f"{STEAM_ITEM_BASE}{c1}_lg.png"
        else:
            ck,dd= fix_item_name(raw)
            disp= dd
            img= f"{STEAM_ITEM_BASE}{ck}_lg.png"
        mm= floor(valSec/60)
        ss= int(valSec%60)
        out.append({
            "name": disp,
            "image": img,
            "time": f"{mm}m {ss}s",
            "sort_time": valSec
        })
    out.sort(key=lambda x:x["sort_time"])
    return out

def fallback_item_popularity(hero_id:int):
    try:
        url= f"{OPEN_DOTA_BASE}/heroes/{hero_id}/itemPopularity"
        r= requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data= r.json()
        return {
          "start_items": parse_pop_block(data,"start_items"),
          "early_game":  parse_pop_block(data,"early_game_items"),
          "mid_game":    parse_pop_block(data,"mid_game_items"),
          "late_game":   parse_pop_block(data,"late_game_items")
        }
    except:
        return {
          "start_items":[], "early_game":[], "mid_game":[], "late_game":[]
        }

def parse_pop_block(data, key):
    block= data.get(key,{})
    arr=[]
    for (k,v) in block.items():
        if k.startswith("recipe_"):
            continue
        if k in ["tpscroll","town_portal_scroll"]:
            continue
        t=999999
        if isinstance(v,dict) and "time" in v:
            t= v["time"]
        c,d= fix_item_name(k)
        mm= floor(t/60)
        ss= int(t%60)
        arr.append({
            "name": d,
            "image": f"{STEAM_ITEM_BASE}{c}_lg.png",
            "time": f"{mm}m {ss}s",
            "sort_time": t
        })
    arr.sort(key=lambda x:x["sort_time"])
    return arr

@app.route("/itembuild/<int:hero_id>")
def itembuild(hero_id):
    arr= gather_timeline(hero_id)
    # If we fail or it's empty => fallback
    if not arr:
        fb= fallback_item_popularity(hero_id)
        return jsonify(fb)

    alt= group_alternatives(arr)
    phases= parse_phases(alt)
    return jsonify(phases)

#########################################
# OPTIONAL: /login_opendota, /logout
#########################################
@app.route("/login_opendota", methods=["GET","POST"])
def login_opendota():
    if request.method=="POST":
        acc= request.form.get("account_id")
        if acc:
            session["opendota_id"]= acc
            return redirect(url_for("index"))
        else:
            return "No account ID provided!",400
    else:
        return """
        <h2>Link OpenDota Account</h2>
        <form method="POST">
          <label>Numeric ID:</label><br>
          <input type="text" name="account_id"><br><br>
          <button type="submit">Link</button>
        </form>
        <p>Find it at https://www.opendota.com/players/&lt;id&gt;</p>
        """

@app.route("/logout")
def logout():
    session.pop("opendota_id", None)
    return redirect(url_for("index"))

#########################################
# MAIN PAGE
#########################################
@app.route("/")
def index():
    od_id= session.get("opendota_id")
    heroes= fetch_hero_list()
    return render_template("index.html", od_id=od_id, heroes=heroes)

if __name__=="__main__":
    app.run(debug=True)