from datetime import date
import json
from pathlib import Path

import dash
from dash import Input, Output, State, dash_table, dcc, html
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


ROOT = Path(__file__).parent
DB_PATH = ROOT / "Data" / "player_data.duckdb"
DATA_READY = DB_PATH.exists()
INK, LIME, PAPER = "#19352b", "#c6ef5a", "#f3f5ef"
MUTED, GREEN, ORANGE, BLUE = "#68756e", "#71983b", "#e8794b", "#4e7b83"
COLORS = [GREEN, ORANGE, BLUE, "#bdc973", "#dfb056", "#927d68"]
GRAPH_CONFIG = {"displayModeBar": False}
METRICS = {
    "goals": "Goals", "assists": "Assists", "minutes_played": "Minutes",
    "appearances": "Appearances", "clean_sheets": "Clean sheets",
    "yellow_cards": "Yellow cards", "red_cards": "Red cards",
    "goals_per_90": "Goals / 90", "assists_per_90": "Assists / 90",
}
METRIC_SQL = {
    "goals": "SUM(COALESCE(f.goals, 0))",
    "assists": "SUM(COALESCE(f.assists, 0))",
    "minutes_played": "SUM(COALESCE(f.minutes_played, 0))",
    "appearances": "SUM(COALESCE(f.appearances, 0))",
    "clean_sheets": "SUM(COALESCE(f.clean_sheets, 0))",
    "yellow_cards": "SUM(COALESCE(f.yellow_cards, 0))",
    "red_cards": "SUM(COALESCE(f.red_cards, 0))",
    "goals_per_90": "90.0 * SUM(COALESCE(f.goals, 0)) / NULLIF(SUM(COALESCE(f.minutes_played, 0)), 0)",
    "assists_per_90": "90.0 * SUM(COALESCE(f.assists, 0)) / NULLIF(SUM(COALESCE(f.minutes_played, 0)), 0)",
}


def db():
    return duckdb.connect(str(DB_PATH), read_only=True)


def frame(sql, params=()):
    with db() as connection:
        return connection.execute(sql, params).df()


def scalar(sql, params=()):
    with db() as connection:
        return connection.execute(sql, params).fetchone()[0]


def records(data):
    if data.empty:
        return []
    return json.loads(data.to_json(orient="records", date_format="iso"))


def number(value):
    return f"{int(value or 0):,}"


def numeric_or_zero(value, as_float=False):
    if value is None or pd.isna(value):
        return 0.0 if as_float else 0
    return float(value) if as_float else int(value)


def euro(value):
    if value is None or pd.isna(value):
        return "N/A"
    value = float(value)
    for limit, divisor, suffix in ((1e9, 1e9, "bn"), (1e6, 1e6, "m"), (1e3, 1e3, "k")):
        if abs(value) >= limit:
            return f"€{value / divisor:.1f}{suffix}"
    return f"€{value:,.0f}"


def season_key(value):
    start = str(value or "").split("/")[0]
    try:
        year = int(start)
    except ValueError:
        return -1
    if len(start) < 4:
        return 1900 + year if year >= 90 else 2000 + year
    return year


def empty_figure(message):
    figure = go.Figure()
    figure.add_annotation(text=message, x=.5, y=.5, xref="paper", yref="paper", showarrow=False, font={"color": MUTED})
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    figure.update_layout(template="plotly_white", height=370, margin={"l": 12, "r": 12, "t": 12, "b": 12})
    return figure


def finish(figure, height=370):
    figure.update_layout(template="plotly_white", height=height, margin={"l": 12, "r": 12, "t": 18, "b": 30}, font={"family": "Trebuchet MS, Segoe UI, sans-serif", "color": INK}, paper_bgcolor="white", plot_bgcolor="white", transition={"duration": 420, "easing": "cubic-in-out"})
    return figure


def where_for(season, competition, position, player_ids):
    clauses, values = [], []
    if season:
        clauses.append("f.season = ?")
        values.append(season)
    if competition:
        clauses.append("f.competition_name = ?")
        values.append(competition)
    if position:
        clauses.append("p.main_position = ?")
        values.append(position)
    selected_ids = list(dict.fromkeys(str(value) for value in (player_ids or []) if str(value).isdigit()))
    if selected_ids:
        placeholders = ", ".join("?" for _ in selected_ids)
        clauses.append(f"p.player_id IN ({placeholders})")
        values.extend(int(value) for value in selected_ids)
    return (" AND " + " AND ".join(clauses) if clauses else ""), values


def player_aggregates(season, competition, position, player_ids):
    where, params = where_for(season, competition, position, player_ids)
    data = frame(f"""
        SELECT p.player_id, SPLIT_PART(MAX(p.player_name), ' (', 1) AS player_name,
          MAX(p.main_position) AS main_position, MAX(p.citizenship) AS citizenship,
          MAX(p.current_club) AS current_club, SUM(COALESCE(f.appearances,0)) AS appearances,
          SUM(COALESCE(f.minutes_played,0)) AS minutes_played, SUM(COALESCE(f.goals,0)) AS goals,
          SUM(COALESCE(f.assists,0)) AS assists, SUM(COALESCE(f.clean_sheets,0)) AS clean_sheets,
          SUM(COALESCE(f.yellow_cards,0)) AS yellow_cards, SUM(COALESCE(f.red_cards,0)) AS red_cards
        FROM performance f JOIN profile p USING (player_id) WHERE 1=1 {where}
        GROUP BY p.player_id
    """, params)
    if not data.empty:
        minutes = data["minutes_played"].where(data["minutes_played"] > 0)
        data["goals_per_90"] = (data["goals"] * 90 / minutes).fillna(0).astype(float).round(2)
        data["assists_per_90"] = (data["assists"] * 90 / minutes).fillna(0).astype(float).round(2)
    return data


def table(table_id, columns, page_size=12):
    return dcc.Loading(
        dash_table.DataTable(
            id=table_id,
            columns=[{"name": name, "id": key, "type": "numeric" if numeric else "text"} for name, key, numeric in columns],
            data=[], page_action="native", page_size=page_size, sort_action="native",
            style_table={"overflowX": "auto"},
            style_cell={"fontFamily": "Trebuchet MS, Segoe UI, sans-serif", "fontSize": "12px", "padding": "9px", "minWidth": "76px", "maxWidth": "230px", "whiteSpace": "normal", "textAlign": "left"},
            style_header={"backgroundColor": "#edf1e8", "color": INK, "fontWeight": "700", "border": "0"},
            style_data={"border": "0", "borderBottom": "1px solid #edf0ea"},
        ),
        type="circle", color=GREEN, delay_show=250, delay_hide=120,
        overlay_style={"visibility": "visible", "backgroundColor": "rgba(255,255,255,0.62)"},
    )


def card(title, graph_id, height=370):
    graph = dcc.Graph(id=graph_id, config=GRAPH_CONFIG, style={"height": f"{height}px"})
    loading_graph = dcc.Loading(graph, type="circle", color=GREEN, delay_show=250, delay_hide=120)
    return html.Section([html.H3(title, style={"fontSize": "16px", "margin": "0 0 6px"}), loading_graph], className="chart-panel", style={"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px", "padding": "15px", "minWidth": 0})


def kpi(title, component_id):
    return html.Div([html.Div(title, style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), html.Div(id=component_id, style={"fontSize": "23px", "fontWeight": "700", "marginTop": "6px", "overflowWrap": "anywhere"})], className="metric-tile", style={"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px", "padding": "14px", "minWidth": 0})


def network_document(payload):
    encoded = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
    *{{box-sizing:border-box}} body{{margin:0;background:#10251e;color:#f2f5ed;font-family:'Trebuchet MS',sans-serif;overflow:hidden}}
      #stage{{position:absolute;inset:0}} canvas{{display:block;width:100%;height:100%}}
      .top{{position:absolute;z-index:2;left:18px;top:15px;pointer-events:none}} .eyebrow{{font-size:10px;letter-spacing:1.5px;color:#c6ef5a;font-weight:700}}
      h2{{font-size:18px;margin:5px 0 3px}} .sub{{font-size:11px;color:#b4c4b7}} .tools{{position:absolute;z-index:3;right:14px;top:14px;display:flex;gap:6px}}
      button{{border:1px solid #486252;background:#1c392c;color:#eaf2e8;padding:7px 9px;border-radius:4px;font:11px inherit;cursor:pointer}} button[aria-pressed=true]{{background:#c6ef5a;color:#173228;border-color:#c6ef5a}}
      #tip{{display:none;position:absolute;z-index:4;min-width:150px;max-width:220px;padding:9px 11px;background:#f6f8f2;color:#19352b;border-radius:4px;font-size:11px;line-height:1.5;pointer-events:none;box-shadow:0 5px 18px #0005}}
      .legend{{position:absolute;left:16px;bottom:14px;font-size:10px;color:#c5d1c8;pointer-events:none}} .hint{{position:absolute;right:15px;bottom:14px;font-size:10px;color:#aebdb2;pointer-events:none}}
      @media(max-width:540px){{.top{{left:12px;top:12px}}h2{{font-size:15px}}.tools{{right:8px;top:8px;gap:4px}}button{{padding:6px;font-size:10px}}.hint{{display:none}}}}
    </style></head><body><div id="stage"></div><div class="top"><div class="eyebrow">TEAMMATE POSITION MAP</div><h2 id="player-name"></h2><div class="sub" id="network-meta"></div></div>
    <div class="tools"><button id="layout" aria-pressed="false">Top-down</button><button id="metric" aria-pressed="false">Joint goals</button><button id="reset">Reset view</button></div>
    <div class="legend">Listed role sets pitch zone · links show recorded teammate connections</div><div class="hint">Drag to orbit · wheel to zoom · click a player to inspect</div><div id="tip"></div>
    <script type="importmap">{{"imports":{{"three":"https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/"}}}}</script>
    <script type="module">
    import * as THREE from 'three'; import {{OrbitControls}} from 'three/addons/controls/OrbitControls.js';
        const data={encoded}; document.getElementById('player-name').textContent=data.player||'Player network';
        document.getElementById('network-meta').textContent=`${{data.nodes.length}} teammate links · ${{data.role||'role'}} zone · not a match lineup`;
        const host=document.getElementById('stage'), scene=new THREE.Scene(); scene.background=new THREE.Color('#163722');
        const camera=new THREE.PerspectiveCamera(44,1,.1,1200); camera.position.set(82,82,92);
        const renderer=new THREE.WebGLRenderer({{antialias:true,alpha:false,preserveDrawingBuffer:true}}); renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.outputColorSpace=THREE.SRGBColorSpace; host.appendChild(renderer.domElement);
        const controls=new OrbitControls(camera,renderer.domElement); controls.enableDamping=true; controls.dampingFactor=.055; controls.minDistance=75; controls.maxDistance=330; controls.maxPolarAngle=Math.PI*.49;
        scene.add(new THREE.HemisphereLight(0xe0f2d6,0x284331,2)); const key=new THREE.DirectionalLight(0xffffff,2.1); key.position.set(-65,110,55); scene.add(key);
        const root=new THREE.Group(); scene.add(root); const raycaster=new THREE.Raycaster(); const pointer=new THREE.Vector2(); const meshes=[]; const lineGroup=new THREE.Group(); root.add(lineGroup);
        const pitch=new THREE.Group(); root.add(pitch);
        const grass=new THREE.Mesh(new THREE.PlaneGeometry(150,96),new THREE.MeshStandardMaterial({{color:0x387a48,roughness:.95}})); grass.rotation.x=-Math.PI/2; grass.position.y=-.4; pitch.add(grass);
        function pitchLine(points,loop=false,opacity=.9){{const vertices=points.map(([x,z])=>new THREE.Vector3(x,.12,z));const geometry=new THREE.BufferGeometry().setFromPoints(vertices);const material=new THREE.LineBasicMaterial({{color:0xe0ead9,transparent:true,opacity}});pitch.add(loop?new THREE.LineLoop(geometry,material):new THREE.Line(geometry,material))}}
        function pitchRect(x1,x2,z1,z2){{pitchLine([[x1,z1],[x2,z1],[x2,z2],[x1,z2]],true)}}
        function zoneTag(label,x,z){{const canvas=document.createElement('canvas');canvas.width=512;canvas.height=128;const context=canvas.getContext('2d');context.fillStyle='rgba(18,43,27,.9)';context.beginPath();context.roundRect(8,8,496,112,18);context.fill();context.fillStyle='#f4f7ee';context.font='bold 40px Trebuchet MS, sans-serif';context.textAlign='center';context.textBaseline='middle';context.fillText(label,256,64);const texture=new THREE.CanvasTexture(canvas);const tag=new THREE.Sprite(new THREE.SpriteMaterial({{map:texture,transparent:true,depthWrite:false}}));tag.position.set(x,.55,z);tag.scale.set(29,7.25,1);root.add(tag)}}
        zoneTag('GOALKEEPER',-65,-42);zoneTag('DEFENCE',-41,-42);zoneTag('MIDFIELD',0,-42);zoneTag('ATTACK',35,-42);zoneTag('FORWARDS',53,-42);
        pitchRect(-75,75,-48,48);pitchLine([[0,-48],[0,48]]);
        pitchLine(Array.from({{length:81}},(_,i)=>[13*Math.cos(i*Math.PI*2/80),13*Math.sin(i*Math.PI*2/80)]),true);
        pitchLine([[-.8,-.8],[.8,-.8],[.8,.8],[-.8,.8]],true);
        for(const side of [-1,1]){{pitchRect(side*75,side*55,-20,20);pitchRect(side*75,side*65,-9,9);pitchRect(side*76,side*75,-3.7,3.7);pitchLine([[side*63,0],[side*64,.8]],false,.85);
            const postMat=new THREE.MeshStandardMaterial({{color:0xf1f3e9,roughness:.35}});for(const z of [-3.7,3.7]){{const post=new THREE.Mesh(new THREE.CylinderGeometry(.28,.28,7.3,8),postMat);post.position.set(side*76.2,3.6,z);pitch.add(post)}}
            const bar=new THREE.Mesh(new THREE.CylinderGeometry(.22,.22,7.4,8),postMat);bar.rotation.z=Math.PI/2;bar.position.set(side*76.2,7.2,0);pitch.add(bar);
        }}
        const centerMat=new THREE.MeshStandardMaterial({{color:0xc6ef5a,emissive:0x365317,emissiveIntensity:.6,roughness:.3,metalness:.12}});
        function roleZone(role){{const value=String(role||'').toLowerCase();if(value.includes('goalkeeper')||value.includes('goalie'))return -65;if(value.includes('centre-back')||value.includes('center-back')||value.includes('defender')||value.includes('full-back')||value.includes('right-back')||value.includes('left-back'))return -41;if(value.includes('defensive midfield'))return -22;if(value.includes('attacking midfield'))return 21;if(value.includes('midfield'))return 0;if(value.includes('wing')||value.includes('forward')||value.includes('striker')||value.includes('attack'))return 44;return 0}}
        const centerPosition=new THREE.Vector3(roleZone(data.role),3.8,0);
        const center=new THREE.Mesh(new THREE.IcosahedronGeometry(3.3,2),centerMat);center.position.copy(centerPosition);center.userData={{player:data.player,role:data.role,root:true}};root.add(center);meshes.push(center);
        const halo=new THREE.Mesh(new THREE.TorusGeometry(5.8,.16,8,100),new THREE.MeshBasicMaterial({{color:0xc6ef5a,transparent:true,opacity:.9}}));halo.rotation.x=Math.PI/2;halo.position.copy(centerPosition);root.add(halo);
        const maxGoals=Math.max(1,...data.nodes.map(n=>n.joint_goals||0)),maxMinutes=Math.max(1,...data.nodes.map(n=>n.minutes||0)),ppgs=data.nodes.map(n=>n.ppg||0),minPpg=Math.min(0,...ppgs),maxPpg=Math.max(1,...ppgs);
        const byRole=new Map();for(const node of data.nodes){{const zone=roleZone(node.role);if(!byRole.has(zone))byRole.set(zone,[]);byRole.get(zone).push(node)}}
        const nodes=[];for(const [zone,items] of byRole)items.forEach((node,index)=>nodes.push({{...node,zone,lane:index,lanes:items.length}}));
        const spheres=[];
        nodes.forEach((node,i)=>{{const spacing=Math.min(7,82/Math.max(node.lanes,1));const z=(node.lane-(node.lanes-1)/2)*spacing;const x=node.zone+((node.lane%3)-1)*1.2;const vector=new THREE.Vector3(x,2.25,z);
            const hue=.08+.25*((node.ppg||0)-minPpg)/(maxPpg-minPpg||1);const color=new THREE.Color().setHSL(hue,.68,.56);
            const size=2.4+2.3*Math.sqrt((node.joint_goals||0)/maxGoals);const mesh=new THREE.Mesh(new THREE.SphereGeometry(size,16,12),new THREE.MeshStandardMaterial({{color,roughness:.3,metalness:.12,emissive:color,emissiveIntensity:.12}}));
            mesh.position.copy(vector);mesh.userData={{...node,index:i}};root.add(mesh);meshes.push(mesh);spheres.push(mesh);
            const curve=new THREE.QuadraticBezierCurve3(centerPosition.clone(),new THREE.Vector3((centerPosition.x+vector.x)/2,5,(centerPosition.z+vector.z)/2),vector.clone());const geometry=new THREE.BufferGeometry().setFromPoints(curve.getPoints(18));const line=new THREE.Line(geometry,new THREE.LineBasicMaterial({{color,transparent:true,opacity:.55}}));lineGroup.add(line);
        }});
        const tip=document.getElementById('tip');let jointMode=true,topMode=false,hovered=null;
    function showTip(object,x,y){{if(!object){{tip.style.display='none';return}}const d=object.userData;tip.innerHTML=`<b>${{escapeHtml(d.player||'Player')}}</b><br>Listed role: ${{escapeHtml(d.role||'Unspecified')}}<br>Shared goals: ${{d.joint_goals??0}}<br>Points per game: ${{Number(d.ppg||0).toFixed(2)}}<br>Minutes together: ${{Number(d.minutes||0).toLocaleString()}}`;tip.style.display='block';tip.style.left=Math.min(x+14,innerWidth-230)+'px';tip.style.top=Math.max(8,Math.min(y+14,innerHeight-130))+'px'}}
    function escapeHtml(value){{const el=document.createElement('span');el.textContent=value;return el.innerHTML}}
    function pick(event){{const rect=renderer.domElement.getBoundingClientRect();pointer.x=((event.clientX-rect.left)/rect.width)*2-1;pointer.y=-((event.clientY-rect.top)/rect.height)*2+1;raycaster.setFromCamera(pointer,camera);return raycaster.intersectObjects(meshes,false)[0]?.object||null}}
    renderer.domElement.addEventListener('pointermove',event=>{{hovered=pick(event);renderer.domElement.style.cursor=hovered?'pointer':'grab';showTip(hovered,event.clientX,event.clientY)}});
    renderer.domElement.addEventListener('pointerleave',()=>showTip(null));
    renderer.domElement.addEventListener('click',event=>{{const hit=pick(event);if(!hit)return;if(hit.userData.root){{controls.target.set(0,0,0);return}}document.querySelector('.sub').textContent=`${{hit.userData.player}} · ${{hit.userData.joint_goals||0}} shared goal involvements`;spheres.forEach(mesh=>mesh.material.emissiveIntensity=mesh===hit?.45:.08);controls.target.copy(hit.position.clone().multiplyScalar(.23))}});
    document.getElementById('metric').onclick=event=>{{jointMode=!jointMode;event.currentTarget.setAttribute('aria-pressed',String(jointMode));event.currentTarget.textContent=jointMode?'Joint goals':'Minutes';document.querySelector('.legend').textContent=jointMode?'Node size = shared goals · links = teammate connection':'Node size = shared minutes · links = teammate connection';spheres.forEach(mesh=>{{const value=jointMode?(mesh.userData.joint_goals||0):(mesh.userData.minutes||0);const scale=jointMode?Math.sqrt(value/maxGoals):Math.sqrt(value/maxMinutes);mesh.scale.setScalar(.72+.72*scale)}})}};
    document.getElementById('layout').onclick=event=>{{topMode=!topMode;event.currentTarget.setAttribute('aria-pressed',String(topMode));event.currentTarget.textContent=topMode?'3D angle':'Top-down';if(topMode){{camera.up.set(0,0,-1);camera.position.set(0,122,.01)}}else{{camera.up.set(0,1,0);camera.position.set(82,82,92)}}controls.target.set(0,0,0);camera.lookAt(0,0,0);controls.update()}};
    document.getElementById('reset').onclick=()=>{{topMode=false;const view=document.getElementById('layout');view.setAttribute('aria-pressed','false');view.textContent='Top-down';camera.up.set(0,1,0);controls.target.set(0,0,0);camera.position.set(82,82,92);controls.update();spheres.forEach(mesh=>mesh.material.emissiveIntensity=.12);document.querySelector('.sub').textContent=`${{data.nodes.length}} teammates · role-based zones · not a match lineup`}};
    function resize(){{const w=host.clientWidth,h=host.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/Math.max(1,h);camera.updateProjectionMatrix()}}new ResizeObserver(resize).observe(host);window.addEventListener('resize',resize);resize();
    function animate(){{requestAnimationFrame(animate);controls.update();renderer.render(scene,camera)}}animate();
    </script></body></html>"""


def startup():
    if not DATA_READY:
        return [], [], [], [], None, 0
    with db() as connection:
        seasons = [row[0] for row in connection.execute("SELECT DISTINCT season FROM performance WHERE season IS NOT NULL").fetchall()]
        seasons.sort(key=season_key, reverse=True)
        competitions = connection.execute("SELECT competition_name,COUNT(*) n FROM performance WHERE competition_name IS NOT NULL GROUP BY 1 ORDER BY n DESC,competition_name").fetchall()
        positions = [row[0] for row in connection.execute("SELECT DISTINCT main_position FROM profile WHERE main_position IS NOT NULL ORDER BY 1").fetchall()]
        players = connection.execute("""
            WITH latest AS (SELECT player_id,ARG_MAX(value_eur,value_date) AS latest_value FROM market_values WHERE value_eur>0 GROUP BY player_id)
            SELECT p.player_id,SPLIT_PART(p.player_name,' (',1),p.citizenship FROM latest m JOIN profile p USING(player_id)
            ORDER BY m.latest_value DESC LIMIT 120
        """).fetchall()
        total = sum(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("player_profiles", "player_performances", "player_market_values", "player_injuries", "player_national_performances", "player_teammates", "player_transfers", "team_details", "team_competitions_seasons", "team_children"))
    options = [{"label": f"{name} · {country or 'Unknown'}", "value": str(player_id)} for player_id, name, country in players]
    return seasons, competitions, positions, options, (options[0]["value"] if options else None), total


SEASONS, COMPETITIONS, POSITIONS, PLAYER_OPTIONS, DEFAULT_PLAYER, ROW_COUNT = startup()
DEFAULT_SEASON = next((season for season in SEASONS if season == "23/24"), SEASONS[0] if SEASONS else None)
app = dash.Dash(__name__)
app.title = "Touchline | Player Intelligence"
server = app.server

PANEL = {"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px", "padding": "15px"}
PAGE = {"fontFamily": "'Trebuchet MS', 'Segoe UI', sans-serif", "backgroundColor": PAPER, "color": INK, "minHeight": "100vh", "paddingBottom": "35px"}

if not DATA_READY:
    app.layout = html.Main(style={**PAGE, "padding": "40px"}, children=[html.H1("Touchline: Player Intelligence"), html.P("Build the local dataset before starting the dashboard."), html.Pre("pip install -r requirements.txt\npython setup_dataset.py\npython app.py")])
else:
    app.layout = html.Div(style=PAGE, children=[
        html.Header(style={"backgroundColor": INK, "color": "white", "padding": "24px 5vw"}, children=[html.Div(style={"maxWidth": "1400px", "margin": "auto"}, children=[html.Div("TOUCHLINE  /  PLAYER INTELLIGENCE", style={"color": LIME, "fontSize": "11px", "fontWeight": "700"}), html.H1("Global football, player by player", style={"fontSize": "32px", "margin": "8px 0"}), html.P(f"{ROW_COUNT:,} records across profiles, seasons, clubs, transfers, valuations and availability", style={"margin": 0, "color": "#d3ddd5"})])]),
        html.Main(style={"maxWidth": "1400px", "margin": "18px auto", "padding": "0 16px"}, children=[
            html.Section(style={**PANEL, "display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(175px,1fr))", "gap": "12px", "alignItems": "end", "marginBottom": "12px"}, children=[
                html.Div([html.Label("SEASON", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), dcc.Dropdown(id="season-filter", options=[{"label": s, "value": s} for s in SEASONS], value=DEFAULT_SEASON, clearable=True, placeholder="All seasons")]),
                html.Div([html.Label("COMPETITION", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), dcc.Dropdown(id="competition-filter", options=[{"label": name, "value": name} for name, _ in COMPETITIONS], searchable=True, clearable=True, placeholder="All competitions")]),
                html.Div([html.Label("POSITION", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), dcc.Dropdown(id="position-filter", options=[{"label": value, "value": value} for value in POSITIONS], clearable=True, placeholder="All positions")]),
                html.Div([html.Label("PLAYER SEARCH", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), dcc.Dropdown(id="player-search", options=PLAYER_OPTIONS, value=[], multi=True, searchable=True, clearable=True, placeholder="Type a name, then select")]),
                html.Div([html.Label("RANK BY", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), dcc.Dropdown(id="metric-filter", options=[{"label": label, "value": key} for key, label in METRICS.items()], value="goals", clearable=False)]),
            ]),
            dcc.Tabs(id="dashboard-tabs", value="performance", children=[
                dcc.Tab(label="Performance", value="performance", children=[
                    html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(170px,1fr))", "gap": "10px", "margin": "12px 0"}, children=[kpi("PLAYERS", "players-count"), kpi("GOALS", "goals-total"), kpi("ASSISTS", "assists-total"), kpi("MINUTES", "minutes-total")]),
                    html.Div(id="metric-leader", style={"fontSize": "13px", "color": MUTED, "padding": "2px 0 10px"}),
                    html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))", "gap": "12px"}, children=[card("Player leaderboard", "metric-leaderboard"), card("Goals and assists", "goal-assist-scatter"), html.Div(card("Across-season trend", "season-trend"), style={"gridColumn": "1 / -1"})]),
                    dcc.Loading(html.Section(style={**PANEL, "marginTop": "12px"}, children=[html.Div([html.H3("Player performance table", style={"fontSize": "16px", "margin": 0}), html.Div(id="table-status", style={"fontSize": "12px", "color": MUTED})], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "10px"}), dash_table.DataTable(id="performance-table", columns=[{"name": label, "id": key, "type": "numeric" if numeric else "text"} for label, key, numeric in [("Player", "player_name", False), ("Position", "main_position", False), ("Nationality", "citizenship", False), ("Current club", "current_club", False), ("Apps", "appearances", True), ("Minutes", "minutes_played", True), ("Goals", "goals", True), ("Assists", "assists", True), ("Goals / 90", "goals_per_90", True), ("Assists / 90", "assists_per_90", True), ("Clean sheets", "clean_sheets", True), ("Yellow", "yellow_cards", True), ("Red", "red_cards", True)]], data=[], page_action="custom", page_current=0, page_size=25, sort_action="custom", sort_by=[{"column_id": "goals", "direction": "desc"}], style_table={"overflowX": "auto"}, style_cell={"fontFamily": "Trebuchet MS, Segoe UI, sans-serif", "fontSize": "12px", "padding": "8px", "minWidth": "76px", "maxWidth": "220px", "whiteSpace": "normal", "textAlign": "left"}, style_header={"backgroundColor": "#edf1e8", "color": INK, "fontWeight": "700", "border": "0"}, style_data={"border": "0", "borderBottom": "1px solid #edf0ea"})]), type="circle", color=GREEN, delay_show=250, delay_hide=120),
                ]),
                dcc.Tab(label="Player 360 + 3D", value="player", children=[
                    html.Section(style={**PANEL, "display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,250px),1fr))", "gap": "14px", "alignItems": "center", "margin": "12px 0"}, children=[html.Div([html.Label("PLAYER", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), dcc.Dropdown(id="player-lookup", options=PLAYER_OPTIONS, value=DEFAULT_PLAYER, searchable=True, placeholder="Search name...")]), html.Div(id="player-identity")]),
                    html.Div(id="player-kpis", style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(145px,1fr))", "gap": "9px", "marginBottom": "12px"}),
                    html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))", "gap": "12px"}, children=[card("Market value history", "player-market"), card("Season-by-season output", "player-seasons"), card("Injury days by cause", "player-injuries")]),
                    html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))", "gap": "12px", "marginTop": "12px"}, children=[html.Div([html.H3("Transfer history", style={"fontSize": "16px"}), table("player-transfers", [("Date", "transfer_date", False), ("From", "from_team", False), ("To", "to_team", False), ("Fee", "transfer_fee", False)])], style=PANEL), html.Div([html.H3("Teammate connections", style={"fontSize": "16px"}), table("player-teammates", [("Player", "teammate_name", False), ("Listed role", "role", False), ("Joint goals", "joint_goal_participation", True), ("Points / game", "points_per_game", True), ("Minutes together", "minutes_played_together", True)])], style=PANEL)]),
                    html.Section(style={**PANEL, "marginTop": "12px"}, children=[html.Div([html.H3("3D teammate network", style={"fontSize": "16px", "margin": "0"}), html.P("Rotate, zoom, inspect teammates, and switch network encodings.", style={"fontSize": "12px", "color": MUTED, "margin": "4px 0 10px"})]), html.Iframe(id="player-network", title="Interactive Three.js teammate network", srcDoc=network_document({"player": "Loading player", "nodes": []}), sandbox="allow-scripts allow-same-origin", style={"width": "100%", "height": "570px", "border": "0", "borderRadius": "4px", "backgroundColor": "#10251e"})]),
                ]),
                dcc.Tab(label="Market & transfers", value="market", children=[html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(180px,1fr))", "gap": "10px", "margin": "12px 0"}, children=[kpi("PLAYERS VALUED", "valued-count"), kpi("TRANSFER RECORDS", "transfer-count"), kpi("TOP VALUE", "market-leader"), kpi("TOP DESTINATION", "transfer-destination")]), html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))", "gap": "12px"}, children=[card("Highest current valuations", "market-top"), card("Median recorded value over time", "market-history"), card("Transfer activity by year", "transfer-years"), card("Most frequent destinations", "transfer-clubs")])]),
                dcc.Tab(label="Injuries & availability", value="injuries", children=[html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(180px,1fr))", "gap": "10px", "margin": "12px 0"}, children=[kpi("INJURY RECORDS", "injury-count"), kpi("DAYS MISSED", "days-missed"), kpi("GAMES MISSED", "games-missed"), kpi("PLAYERS AFFECTED", "players-injured")]), html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))", "gap": "12px"}, children=[card("Days missed by injury type", "injury-types"), card("Availability by year", "injury-history")]), html.Section(style={**PANEL, "marginTop": "12px"}, children=[html.H3("Longest absences", style={"fontSize": "16px"}), table("injury-table", [("Player", "player_name", False), ("Injury", "injury_reason", False), ("Season", "season", False), ("Days", "days_missed", True), ("Games", "games_missed", True)])])]),
                dcc.Tab(label="National & clubs", value="teams", children=[html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))", "gap": "12px", "marginTop": "12px"}, children=[card("International appearances", "national-caps"), card("International goals and caps", "national-goals")]), html.Section(style={**PANEL, "marginTop": "12px"}, children=[html.H3("Club season comparison", style={"fontSize": "16px"}), card("Points by club", "club-points"), table("club-table", [("Rank", "rank", True), ("Club", "team_name", False), ("Competition", "competition_name", False), ("Season", "season", False), ("Played", "played", True), ("Wins", "wins", True), ("Draws", "draws", True), ("Losses", "losses", True), ("GF", "goals_for", True), ("GA", "goals_against", True), ("Points", "points", True)], 15)])]),
            ]),
            html.Footer(["Source: ", html.A("salimt/football-datasets", href="https://github.com/salimt/football-datasets", target="_blank", style={"color": INK}), " · Transfermarkt-derived data. Verify source usage rights before redistribution."], style={"fontSize": "11px", "color": MUTED, "padding": "16px 2px"}),
        ]),
    ])


if DATA_READY:
    @app.callback(
        Output("performance-table", "page_current"),
        Input("season-filter", "value"),
        Input("competition-filter", "value"),
        Input("position-filter", "value"),
        Input("player-search", "value"),
        Input("metric-filter", "value"),
    )
    def reset_performance_page(season, competition, position, player_ids, metric):
        return 0

    @app.callback(
        Output("performance-table", "sort_by"),
        Input("metric-filter", "value"),
    )
    def sync_rank_sort(metric):
        column = metric if metric in METRICS else "goals"
        return [{"column_id": column, "direction": "desc"}]

    @app.callback(
        Output("player-search", "options"),
        Input("player-search", "search_value"),
        State("player-search", "value"),
    )
    def search_filter_players(search_value, selected_ids):
        if search_value and len(search_value.strip()) >= 2:
            pattern = f"%{search_value.strip()}%"
            matches = frame("""
                SELECT player_id, SPLIT_PART(player_name, ' (', 1) AS player_name, citizenship
                FROM profile
                WHERE player_name ILIKE ? OR COALESCE(full_name, '') ILIKE ?
                ORDER BY player_name LIMIT 40
            """, [pattern, pattern])
            options = [{"label": f"{row.player_name} · {row.citizenship or 'Unknown'}", "value": str(row.player_id)} for row in matches.itertuples()]
        else:
            options = PLAYER_OPTIONS

        selected_ids = list(dict.fromkeys(str(value) for value in (selected_ids or []) if str(value).isdigit()))
        missing_ids = [value for value in selected_ids if not any(option["value"] == value for option in options)]
        if missing_ids:
            placeholders = ", ".join("?" for _ in missing_ids)
            selected = frame(f"""
                SELECT player_id, SPLIT_PART(player_name, ' (', 1) AS player_name, citizenship
                FROM profile WHERE player_id IN ({placeholders})
            """, [int(value) for value in missing_ids])
            options = [{"label": f"{row.player_name} · {row.citizenship or 'Unknown'}", "value": str(row.player_id)} for row in selected.itertuples()] + options
        return options

    @app.callback(
        Output("players-count", "children"), Output("goals-total", "children"), Output("assists-total", "children"), Output("minutes-total", "children"),
        Output("metric-leader", "children"), Output("metric-leaderboard", "figure"), Output("goal-assist-scatter", "figure"), Output("season-trend", "figure"),
        Output("performance-table", "data"), Output("table-status", "children"),
        Input("season-filter", "value"), Input("competition-filter", "value"), Input("position-filter", "value"), Input("player-search", "value"), Input("metric-filter", "value"),
        Input("performance-table", "page_current"), Input("performance-table", "page_size"), Input("performance-table", "sort_by"),
    )
    def update_performance(season, competition, position, player_ids, metric, page_current, page_size, sort_by):
        metric = metric if metric in METRICS else "goals"
        data = player_aggregates(season, competition, position, player_ids)
        if data.empty:
            empty = empty_figure("No players match these filters")
            return "0", "0", "0", "0", "No matching players", empty, empty, empty, [], "No rows"
        data[metric] = pd.to_numeric(data[metric], errors="coerce").fillna(0)
        data = data.sort_values([metric, "goals", "minutes_played"], ascending=False)
        leader = data.iloc[0]
        suffix = f"{leader[metric]:,.2f}" if metric.endswith("per_90") else f"{leader[metric]:,.0f}"
        leader_text = f"Top {METRICS[metric].lower()}: {leader['player_name']} · {suffix}"
        top = data.nlargest(14, metric).sort_values(metric)
        ranking = px.bar(top, x=metric, y="player_name", orientation="h", text=metric, color=metric, color_continuous_scale=["#dce8c6", GREEN], labels={metric: METRICS[metric], "player_name": "Player"}, hover_data={"main_position": True, "citizenship": True, "appearances": True})
        ranking.update_traces(textposition="outside")
        ranking.update_layout(showlegend=False, coloraxis_showscale=False, xaxis_rangemode="tozero", yaxis_title=None)
        finish(ranking)
        scatter = px.scatter(data.nlargest(min(700, len(data)), "goals"), x="goals", y="assists", color="main_position", size="minutes_played", hover_name="player_name", hover_data={"citizenship": True, "current_club": True, "goals_per_90": ":.2f", "assists_per_90": ":.2f"}, color_discrete_sequence=COLORS, labels={"goals": "Goals", "assists": "Assists", "main_position": "Position", "minutes_played": "Minutes"})
        finish(scatter)
        trend_where, trend_params = where_for(None, competition, position, player_ids)
        trend = frame(f"SELECT f.season,{METRIC_SQL[metric]} AS metric_value FROM performance f JOIN profile p USING(player_id) WHERE f.season IS NOT NULL {trend_where} GROUP BY f.season", trend_params)
        if trend.empty:
            trend_figure = empty_figure("No season history")
        else:
            trend["order"] = trend["season"].map(season_key)
            trend = trend.sort_values("order")
            trend_figure = px.line(trend, x="season", y="metric_value", markers=True, labels={"season": "Season", "metric_value": METRICS[metric]})
            trend_figure.update_traces(line={"color": GREEN, "width": 3})
            finish(trend_figure, 350)
        columns = ["player_name", "main_position", "citizenship", "current_club", "appearances", "minutes_played", "goals", "assists", "goals_per_90", "assists_per_90", "clean_sheets", "yellow_cards", "red_cards"]
        data = data[columns]
        if sort_by:
            sort_column = sort_by[0].get("column_id", "goals")
            if sort_column not in columns:
                sort_column = "goals"
            ascending = sort_by[0].get("direction") == "asc"
        else:
            sort_column, ascending = metric, False
        data = data.sort_values(sort_column, ascending=ascending, na_position="last")
        page_size = min(100, max(1, int(page_size or 25)))
        last_page = max(0, (len(data) - 1) // page_size)
        page_current = min(last_page, max(0, int(page_current or 0)))
        start = page_current * page_size
        page = data.iloc[start:start + page_size]
        status = f"Rows {start + 1:,}–{min(start + page_size,len(data)):,} of {len(data):,} players" if len(data) else "No rows"
        return number(len(data)), number(data["goals"].sum()), number(data["assists"].sum()), number(data["minutes_played"].sum()), leader_text, ranking, scatter, trend_figure, records(page), status

    @app.callback(Output("player-lookup", "options"), Input("player-lookup", "search_value"), State("player-lookup", "value"))
    def player_search(search, selected):
        if search and len(search.strip()) >= 2:
            pattern = f"%{search.strip()}%"
            matches = frame("SELECT player_id,SPLIT_PART(player_name,' (',1) player_name,citizenship FROM profile WHERE player_name ILIKE ? OR COALESCE(full_name,'') ILIKE ? ORDER BY player_name LIMIT 40", [pattern, pattern])
            options = [{"label": f"{r.player_name} · {r.citizenship or 'Unknown'}", "value": str(r.player_id)} for r in matches.itertuples()]
        else:
            options = PLAYER_OPTIONS
        if selected and not any(item["value"] == str(selected) for item in options):
            row = frame("SELECT player_id,SPLIT_PART(player_name,' (',1) player_name,citizenship FROM profile WHERE player_id=TRY_CAST(? AS BIGINT)", [selected])
            if not row.empty:
                r = row.iloc[0]
                options.insert(0, {"label": f"{r.player_name} · {r.citizenship or 'Unknown'}", "value": str(r.player_id)})
        return options

    @app.callback(
        Output("player-identity", "children"), Output("player-kpis", "children"), Output("player-market", "figure"), Output("player-seasons", "figure"),
        Output("player-injuries", "figure"), Output("player-transfers", "data"), Output("player-teammates", "data"), Output("player-network", "srcDoc"),
        Input("player-lookup", "value"),
    )
    def update_player(player_id):
        if not player_id:
            blank = empty_figure("Select a player")
            return "Choose a player", [], blank, blank, blank, [], [], network_document({"player": "Select a player", "nodes": []})
        profile = frame("SELECT * FROM profile WHERE player_id=TRY_CAST(? AS BIGINT)", [player_id])
        if profile.empty:
            blank = empty_figure("Player not found")
            return "Player not found", [], blank, blank, blank, [], [], network_document({"player": "Player not found", "nodes": []})
        player = profile.iloc[0]
        dob = pd.to_datetime(player["date_of_birth"], errors="coerce")
        age = int((pd.Timestamp(date.today()) - dob).days / 365.2425) if not pd.isna(dob) else None
        facts = [value for value in [player["current_club"], player["citizenship"], player["main_position"]] if value and not pd.isna(value)]
        physical_facts = [value for value in [f"{age} yrs" if age else None, player["height"], player["foot"]] if value and not pd.isna(value)]
        identity = html.Div([html.Img(src=player["player_image_url"], alt=player["player_name"], style={"width": "70px", "height": "70px", "objectFit": "cover", "borderRadius": "50%", "backgroundColor": "#edf1e8"}) if player["player_image_url"] else html.Div(style={"width": "70px", "height": "70px", "borderRadius": "50%", "backgroundColor": LIME}), html.Div([html.H2(str(player["player_name"]).split(" (")[0], style={"fontSize": "22px", "margin": "0 0 3px"}), html.Div(" · ".join(map(str, facts)), style={"fontSize": "12px", "color": MUTED}), html.Div(" · ".join(map(str, physical_facts)), style={"fontSize": "11px", "color": MUTED, "marginTop": "4px"})])], style={"display": "flex", "alignItems": "center", "gap": "14px"})
        career = frame("SELECT COUNT(DISTINCT season) seasons,SUM(COALESCE(goals,0)) goals,SUM(COALESCE(assists,0)) assists,SUM(COALESCE(appearances,0)) appearances,SUM(COALESCE(minutes_played,0)) AS total_minutes FROM performance WHERE player_id=TRY_CAST(? AS BIGINT)", [player_id]).iloc[0]
        latest_value = scalar("SELECT MAX(value_eur) FROM market_values WHERE player_id=TRY_CAST(? AS BIGINT)", [player_id])
        caps = scalar("SELECT SUM(matches) FROM national_performances WHERE player_id=TRY_CAST(? AS BIGINT)", [player_id])
        kpis = [html.Div([html.Div(label, style={"fontSize": "9px", "fontWeight": "700", "color": MUTED}), html.Div(value, style={"fontSize": "18px", "fontWeight": "700", "marginTop": "4px"})], style=PANEL) for label, value in [("SEASONS", number(career["seasons"])), ("GOALS", number(career["goals"])), ("ASSISTS", number(career["assists"])), ("APPEARANCES", number(career["appearances"])), ("LATEST VALUE", euro(latest_value)), ("NATIONAL CAPS", number(caps))]]
        market = frame("SELECT value_date,MAX(value_eur) AS market_value_eur FROM market_values WHERE player_id=TRY_CAST(? AS BIGINT) AND value_date IS NOT NULL GROUP BY value_date ORDER BY value_date", [player_id])
        if market.empty:
            market_fig = empty_figure("No valuation history")
        else:
            market["value_m"] = market["market_value_eur"] / 1e6
            market_fig = px.line(market, x="value_date", y="value_m", markers=True, labels={"value_date": "Date", "value_m": "Market value (€m)"})
            market_fig.update_traces(line={"color": GREEN, "width": 3})
            finish(market_fig)
        seasons = frame("SELECT season,SUM(COALESCE(goals,0)) goals,SUM(COALESCE(assists,0)) assists FROM performance WHERE player_id=TRY_CAST(? AS BIGINT) GROUP BY season", [player_id])
        if seasons.empty:
            season_fig = empty_figure("No performance history")
        else:
            seasons["order"] = seasons["season"].map(season_key)
            long = seasons.sort_values("order").melt(id_vars=["season","order"], value_vars=["goals","assists"], var_name="metric", value_name="total")
            season_fig = px.line(long, x="season", y="total", color="metric", markers=True, color_discrete_sequence=[GREEN, ORANGE], labels={"season": "Season", "total": "Total", "metric": ""})
            season_fig.update_layout(legend_title=None)
            finish(season_fig)
        injury_data = frame("SELECT COALESCE(injury_reason,'Unspecified') reason,SUM(COALESCE(days_missed,0)) AS missed_days FROM injuries WHERE player_id=TRY_CAST(? AS BIGINT) GROUP BY 1 ORDER BY missed_days DESC LIMIT 10", [player_id])
        if injury_data.empty:
            injury_fig = empty_figure("No injury records")
        else:
            injury_fig = px.bar(injury_data.sort_values("missed_days"), x="missed_days", y="reason", orientation="h", color="missed_days", color_continuous_scale=["#f1d7c7",ORANGE], labels={"missed_days":"Days missed","reason":"Injury"})
            injury_fig.update_layout(coloraxis_showscale=False,yaxis_title=None)
            finish(injury_fig)
        transfer_rows = frame("SELECT CAST(transfer_date AS VARCHAR) transfer_date,from_team,to_team,transfer_fee FROM transfers WHERE player_id=TRY_CAST(? AS BIGINT) ORDER BY transfer_date DESC NULLS LAST LIMIT 100", [player_id])
        teammate_rows = frame("""
                 SELECT COALESCE(SPLIT_PART(p.player_name,' (',1),t.teammate_name) AS teammate_name,
                     COALESCE(p.sub_position,p.main_position) AS role,
                     p.main_position,t.joint_goal_participation,t.points_per_game,t.minutes_played_together
            FROM teammate_links t
            LEFT JOIN profile p ON p.player_id=t.teammate_id
            WHERE t.player_id=TRY_CAST(? AS BIGINT)
            ORDER BY t.joint_goal_participation DESC NULLS LAST,t.points_per_game DESC NULLS LAST
            LIMIT 100
        """, [player_id])
        network_nodes = [{"player": row.teammate_name, "role": row.role, "joint_goals": numeric_or_zero(row.joint_goal_participation), "ppg": numeric_or_zero(row.points_per_game, as_float=True), "minutes": numeric_or_zero(row.minutes_played_together)} for row in teammate_rows.itertuples()]
        selected_role = next((str(value) for value in (player["sub_position"], player["main_position"]) if value is not None and not pd.isna(value) and str(value).strip()), "Unspecified")
        return identity, kpis, market_fig, season_fig, injury_fig, records(transfer_rows), records(teammate_rows), network_document({"player": str(player["player_name"]).split(" (")[0], "role": selected_role, "nodes": network_nodes})

    @app.callback(
        Output("valued-count","children"),Output("transfer-count","children"),Output("market-leader","children"),Output("transfer-destination","children"),
        Output("market-top","figure"),Output("market-history","figure"),Output("transfer-years","figure"),Output("transfer-clubs","figure"),
        Input("dashboard-tabs","value"),
    )
    def update_market(tab):
        if tab != "market":
            blank=empty_figure("Open this view to load the data")
            return "—","—","—","—",blank,blank,blank,blank
        latest=frame("WITH v AS (SELECT player_id,ARG_MAX(value_eur,value_date) AS market_value_eur,MAX(value_date) AS value_date FROM market_values WHERE value_eur>0 GROUP BY player_id) SELECT SPLIT_PART(p.player_name,' (',1) player_name,p.main_position,v.market_value_eur,v.value_date FROM v JOIN profile p USING(player_id) ORDER BY v.market_value_eur DESC LIMIT 18")
        destination=frame("SELECT to_team,COUNT(*) moves FROM transfers WHERE to_team IS NOT NULL GROUP BY 1 ORDER BY moves DESC LIMIT 12")
        if latest.empty:
            leader="N/A"; bar=empty_figure("No valuations")
        else:
            leader=f"{latest.iloc[0]['player_name']} · {euro(latest.iloc[0]['market_value_eur'])}"
            bar=px.bar(latest.sort_values("market_value_eur"),x="market_value_eur",y="player_name",orientation="h",color="market_value_eur",color_continuous_scale=["#dce8c6",GREEN],labels={"market_value_eur":"Value (€)","player_name":"Player"},hover_data={"main_position":True,"value_date":True});bar.update_layout(coloraxis_showscale=False,yaxis_title=None,xaxis_tickprefix="€");finish(bar)
        history=frame("SELECT YEAR(value_date) AS calendar_year,QUANTILE_CONT(value_eur,0.5) AS median FROM market_values WHERE value_date IS NOT NULL AND value_eur>0 GROUP BY 1 ORDER BY 1")
        if history.empty:
            history_fig=empty_figure("No valuation timeline")
        else:
            history=history.tail(25);history["median_m"]=history["median"]/1e6;history_fig=px.line(history,x="calendar_year",y="median_m",markers=True,labels={"calendar_year":"Year","median_m":"Median value (€m)"});history_fig.update_traces(line={"color":BLUE,"width":3});finish(history_fig)
        by_year=frame("SELECT YEAR(transfer_date) AS calendar_year,COUNT(*) moves FROM transfers WHERE transfer_date IS NOT NULL GROUP BY 1 ORDER BY 1")
        years=px.area(by_year.tail(35),x="calendar_year",y="moves",labels={"calendar_year":"Year","moves":"Transfers"}) if not by_year.empty else empty_figure("No dated transfers")
        if not by_year.empty:
            years.update_traces(line_color=ORANGE,fillcolor="rgba(232,121,75,.24)");finish(years)
        clubs=px.bar(destination.sort_values("moves"),x="moves",y="to_team",orientation="h",color="moves",color_continuous_scale=["#f1d7c7",ORANGE],labels={"moves":"Records","to_team":"Destination"}) if not destination.empty else empty_figure("No destinations")
        if not destination.empty:
            clubs.update_layout(coloraxis_showscale=False,yaxis_title=None);finish(clubs)
        transfer_n=scalar("SELECT COUNT(*) FROM transfers")
        return number(len(latest)),number(transfer_n),leader,str(destination.iloc[0]["to_team"] if not destination.empty else "N/A"),bar,history_fig,years,clubs

    @app.callback(Output("injury-count","children"),Output("days-missed","children"),Output("games-missed","children"),Output("players-injured","children"),Output("injury-types","figure"),Output("injury-history","figure"),Output("injury-table","data"),Input("dashboard-tabs","value"))
    def update_injuries(tab):
        if tab != "injuries":
            blank=empty_figure("Open this view to load the data")
            return "—","—","—","—",blank,blank,[]
        totals=frame("SELECT COUNT(*) cases,SUM(COALESCE(days_missed,0)) AS missed_days,SUM(COALESCE(games_missed,0)) games,COUNT(DISTINCT player_id) players FROM injuries").iloc[0]
        reasons=frame("SELECT COALESCE(injury_reason,'Unspecified') reason,SUM(COALESCE(days_missed,0)) AS missed_days,COUNT(*) cases FROM injuries GROUP BY 1 ORDER BY missed_days DESC LIMIT 12")
        trend=frame("SELECT YEAR(from_date) AS calendar_year,COUNT(*) cases,SUM(COALESCE(days_missed,0)) AS missed_days FROM injuries WHERE from_date IS NOT NULL GROUP BY 1 ORDER BY 1")
        longest=frame("SELECT SPLIT_PART(p.player_name,' (',1) player_name,i.injury_reason,i.season,i.days_missed,i.games_missed FROM injuries i JOIN profile p USING(player_id) ORDER BY i.days_missed DESC NULLS LAST LIMIT 250")
        rfig=px.bar(reasons.sort_values("missed_days"),x="missed_days",y="reason",orientation="h",color="cases",color_continuous_scale=["#f1d7c7",ORANGE],labels={"missed_days":"Days missed","reason":"Injury","cases":"Cases"}) if not reasons.empty else empty_figure("No injuries")
        if not reasons.empty:rfig.update_layout(coloraxis_showscale=False,yaxis_title=None);finish(rfig)
        tfig=px.bar(trend.tail(30),x="calendar_year",y="missed_days",color="cases",color_continuous_scale="Tealgrn",labels={"calendar_year":"Year","missed_days":"Days missed","cases":"Injury records"}) if not trend.empty else empty_figure("No injury dates")
        if not trend.empty:tfig.update_layout(coloraxis_showscale=False);finish(tfig)
        return number(totals["cases"]),number(totals["missed_days"]),number(totals["games"]),number(totals["players"]),rfig,tfig,records(longest)

    @app.callback(Output("national-caps","figure"),Output("national-goals","figure"),Output("club-points","figure"),Output("club-table","data"),Input("season-filter","value"),Input("competition-filter","value"),Input("dashboard-tabs","value"))
    def update_clubs(season,competition,tab):
        if tab != "teams":
            blank=empty_figure("Open this view to load the data")
            return blank,blank,blank,[]
        national=frame("SELECT p.player_id,SPLIT_PART(p.player_name,' (',1) player_name,MAX(p.citizenship) citizenship,SUM(COALESCE(n.matches,0)) caps,SUM(COALESCE(n.goals,0)) goals FROM national_performances n JOIN profile p USING(player_id) GROUP BY p.player_id,p.player_name")
        top=national.nlargest(16,"caps").sort_values("caps") if not national.empty else national
        cap_fig=px.bar(top,x="caps",y="player_name",orientation="h",color="caps",color_continuous_scale=["#dce8c6",GREEN],labels={"caps":"International appearances","player_name":"Player"},hover_data={"citizenship":True,"goals":True}) if not top.empty else empty_figure("No national records")
        scatter=px.scatter(national.nlargest(min(700,len(national)),"caps"),x="caps",y="goals",color="citizenship",hover_name="player_name",labels={"caps":"Caps","goals":"Goals","citizenship":"Nationality"},color_discrete_sequence=COLORS) if not national.empty else empty_figure("No national records")
        if not top.empty:cap_fig.update_layout(coloraxis_showscale=False,yaxis_title=None);finish(cap_fig)
        if not national.empty:finish(scatter)
        clauses=["points IS NOT NULL"];params=[]
        if season:clauses.append("season=?");params.append(season)
        if competition:clauses.append("competition_name=?");params.append(competition)
        clubs=frame(f"SELECT * FROM club_seasons WHERE {' AND '.join(clauses)} ORDER BY rank ASC NULLS LAST,points DESC LIMIT 500",params)
        if clubs.empty:return cap_fig,scatter,empty_figure("No club seasons for these filters"),[]
        plot=clubs.head(18).sort_values("points")
        club_fig=px.bar(plot,x="points",y="team_name",orientation="h",color="competition_name",hover_data={"rank":True,"season":True,"goals_for":True,"goals_against":True},labels={"points":"Points","team_name":"Club","competition_name":"Competition"},color_discrete_sequence=COLORS);club_fig.update_layout(yaxis_title=None,legend_title=None);finish(club_fig)
        return cap_fig,scatter,club_fig,records(clubs)


if __name__ == "__main__":
    app.run(debug=False)