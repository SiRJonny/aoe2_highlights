-- timeline_recorder: richer highlight-source stream.
--
-- Every game second sends:
--   vill_pop  {"time", "counts": {pid: count}}           (1 Hz villager census)
--   vill_death {"time", "id", "player", "x", "y", ["by_animal", ...],
--             ["by_player", "by_player_name", "by_player_dist",
--              "by_bld", "by_bld_owner", "by_bld_dist"]}
--             villager gone since previous sample (death at last known
--             position, +-1 s / +-1 tile; also fires for fishing ships/
--             converted units, between-sample spawn+death pairs are
--             impossible to see). Heuristic kill attribution at the
--             death position (engine exposes no killer id): by_animal =
--             gaia animal within ~3 tiles; by_player = enemy combat
--             unit within ~5 tiles; by_bld = enemy TC/castle/tower in
--             range. All three may coexist (raid near a boar); absence
--             = no suspect (death by fire ship, decay, trap, etc.).
--   stats     {"time", "stats": [[age,food,wood,stone,gold,pop], ...]}
--             per player index 1..n (age: 0=Dark..3=Imperial, PlayerAttribute ids)
--   bld_*     building lifecycle rows (house_start / house_done / house_lost
--             with isHouse flag; other buildings with isHouse=false,
--             isCastle=true for castle rows, isTC=true for town-center rows)
--   army_pop  {"time", "counts": {pid: count}}           (1 Hz military census;
--             sum of combat UnitClasses — INFANTRY, ARCHERY, CAVALRY,
--             SIEGE_WEAPON and specific combat classes; navy/monks excluded.
--             counts={} + one-time Log warning if no combat class exists)
--   unit_death {"time", "id", "player", "x", "y"}        military unit gone
--             since the previous sample (last known position, +-1 s / +-1 tile;
--             same gap logic as vill_death)
--   bld_event lost rows are also emitted for COMPLETED town centers and
--             castles vanishing (destruction), not just foundations
--   tc_snap   {"owner", "x", "y", "kind"}               one-time snapshot of
--             every completed town center / castle at the first sample
--             (starting TCs have no build events; prio-7 planner needs them)
--   objects   {"b": [[id,owner,unitType,x,y],...], "v": [...], "m": [...]}
--             columnar full-object snapshot (buildings incl. foundations,
--             villagers, military) for the map-animation tool. OFF until
--             the client sends {"action":"map_mode","on":true} — the
--             highlight collect never enables it (rows are big).
--   tick      debug telemetry every 10 updates
--
-- Building lifecycle from Player:GetFoundations() (unfinished buildings are
-- excluded from the normal object lists):
--   - a foundation id not seen before            -> house_start / bld_start
--   - the id disappears from foundations, but
--     GetObjectById(id) still exists             -> house_done / bld_done
--   - the id disappears and the object is gone   -> house_lost / bld_lost
-- Foundation data comes from GetObjectStates: unitType, owner, tile x/y, hp.
--
-- Inbound commands (client -> module, JSON with optional target routing):
--   {"action":"seek","t":<game seconds>,"x":<tx>,"y":<ty>,
--      "pause":true|false,"camera":true|false}
--       rest the replay at fastest speed, then pause + move the camera
--       at game time t (DEFAULT for the highlight seek)
--   {"action":"map_mode","on":true|false}
--       start/stop the per-sample "objects" snapshot row (map animation
--       tool); re-sends the meta row so a late-attaching client gets
--       players/map size/type names
--   {"action":"camera","x":..,"y":..}    move the camera now
--   {"action":"pause"} / {"action":"unpause"}
--   {"action":"set_speed","v":0..3}      replay speed (ReplaySpeed enum)
--   {"action":"restart"}                 DispatchRestartGame (replay/profile)
--   {"action":"ping"}                    -> {"type":"pong",...}
--
-- Requires CONTROL settings: Modules See Everything = true so all players'
-- foundations, stats and objects are readable.

local PIPE = "AoE2_Highlights"
local SAMPLE_INTERVAL = 1.0
local VILL_POS_WINDOW_S = 150.0   -- stream villager positions while the
                                  -- game is young (nomad first-TC phase)

local nextSampleTime = 0
local lastT = nil
local metaSent = false
local tickCounter = 0
local villSeen = {}   -- villager id -> last known {x, y, playerId}
local militarySeen = {} -- military unit id -> last known {x, y, playerId}
local militaryClasses = {} -- list of UnitClass ids counted as army
local armyWarned = false
local tcSeen = {}     -- completed TC/castle id -> {playerId, x, y, kind}
local snapSent = false

local houseTypes = {}     -- [unitType id] = true, built from UnitObjectType names
local typeNames = {}      -- [unitType id] = name, filled with house ids at load
local foundations = {}    -- [objectId] = {unitType, playerId, x, y, hp}
local seek = nil          -- {"t", "x", "y", "pause", "camera", "speedName"}
local pan = nil           -- camera tween {"x0","y0","x1","y1","seconds",
                          -- "t0"}: interpolates SetCameraPosition EVERY
                          -- Update so pans render smoothly (pipe-driven
                          -- camera moves batch in the IPC pump and step)
local mapMode = false     -- objects snapshot row on/off (map animation)

local function round1(v)
    return math.floor(v * 10 + 0.5) / 10
end

local function hp1(v)
    -- hitpoints rounded to an int; nil when the column is missing
    if type(v) ~= "number" then return nil end
    return math.floor(v + 0.5)
end

local function isHouseType(ot)
    return houseTypes[ot] or false
end

local function isTCType(ot)
    local n = typeNames[ot]
    return n ~= nil and string.sub(n, 1, 11) == "TOWN_CENTER"
end

local animalTypes = {}    -- [unitType id] = name, aggressive gaia animals

local function buildAnimalTypeSet()
    -- aggressive/hunt animals that can kill villagers; substring match on
    -- the UnitObjectType names (DE: WOLF, BOAR, BROWN_BEAR, JAGUAR, ...).
    -- Player-owned war elephants are excluded later by the owner==0 test.
    local keywords = { "WOLF", "JAGUAR", "BEAR", "BOAR", "LION", "TIGER",
                       "LEOPARD", "CROCODILE", "ALLIGATOR", "ELEPHANT" }
    for value, name in pairs(typeNames) do
        for i = 1, #keywords do
            if string.find(name, keywords[i]) then
                animalTypes[value] = name
                break
            end
        end
    end
end

local function animalNear(x, y)
    -- nearest gaia-owned animal within ~3 tiles of a death position (the
    -- villager's last-known position is up to 1 sample stale)
    local best, bd = nil, 3.5
    for ot, name in pairs(animalTypes) do
        local ok, states = pcall(function()
            return GetObjectStates(GetObjectsByType(ot))
        end)
        if ok and states and states.id then
            for i = 1, #states.id do
                local pid = states.playerId[i]
                if pid == 0 or pid == -1 then
                    local d = math.abs(states.x[i] - x)
                        + math.abs(states.y[i] - y)
                    if d < bd then
                        bd = d
                        best = { name = name, id = states.id[i],
                                 x = states.x[i], y = states.y[i] }
                    end
                end
            end
        end
    end
    return best
end

local function unitNear(x, y, victimPid)
    -- nearest ENEMY combat-class unit within ~5 tiles of a death position:
    -- the surviving attacker is almost always adjacent at the next 1 Hz
    -- sample (heuristic attribution; the engine exposes no killer id)
    local best, bd = nil, 5.0
    for c = 1, #militaryClasses do
        local ok, states = pcall(function()
            return GetObjectStates(GetObjectsByClass(militaryClasses[c]))
        end)
        if ok and states and states.id then
            for i = 1, #states.id do
                local pid = states.playerId[i]
                if pid and pid >= 1 and pid ~= victimPid then
                    local d = math.abs(states.x[i] - x)
                        + math.abs(states.y[i] - y)
                    if d < bd then
                        bd = d
                        best = { player = pid, dist = d,
                                 name = typeNames[states.unitType[i]],
                                 id = states.id[i],
                                 x = states.x[i], y = states.y[i] }
                    end
                end
            end
        end
    end
    return best
end

local function bldNear(x, y, victimPid)
    -- nearest enemy defensive building in attack range of a death
    -- position (TC fire, castle fire, tower fire)
    local ranges = { TOWN_CENTER = 7.0, CASTLE = 11.0, TOWER = 8.0 }
    local best, bd = nil, 1e9
    local ok, states = pcall(function()
        return GetObjectStates(GetObjectsByClass(UnitClass.BUILDING))
    end)
    if ok and states and states.id then
        for i = 1, #states.id do
            local pid = states.playerId[i]
            if pid and pid >= 1 and pid ~= victimPid then
                local name = typeNames[states.unitType[i]] or ""
                for pat, range in pairs(ranges) do
                    if string.find(name, pat) then
                        local d = math.abs(states.x[i] - x)
                            + math.abs(states.y[i] - y)
                        if d <= range and d < bd then
                            bd = d
                            best = { player = pid, dist = d, name = name,
                                     id = states.id[i],
                                     x = states.x[i], y = states.y[i] }
                        end
                        break
                    end
                end
            end
        end
    end
    return best
end

local function buildHouseTypeSet()
    local name, value
    for name, value in pairs(UnitObjectType) do
        if type(value) == "number" then
            typeNames[value] = name
            if string.sub(name, 1, 5) == "HOUSE" then
                houseTypes[value] = true
            end
        end
    end
end

local function sendMeta()
    local players = {}
    local count = GetPlayerCount()
    for i = 0, count - 1 do
        local p = GetPlayerById(i)
        if p then
            local name = nil
            if p.GetName then
                local ok, n = pcall(p.GetName, p)
                if ok then
                    name = n
                end
            end
            table.insert(players, { id = i, name = name })
        end
    end
    IPC.Send({
        type = "meta",
        mapWidth = GetMapWidth(),
        mapHeight = GetMapHeight(),
        players = players,
        sampleInterval = SAMPLE_INTERVAL,
        houseTypes = houseTypes,
        typeNames = typeNames,
    })
end

local function send(type, payload)
    payload.type = type
    payload.time = GetGameTime()
    IPC.Send(payload)
end

local function countHouseTypes()
    local n = 0
    for _ in pairs(houseTypes) do
        n = n + 1
    end
    return n
end

local function findMilitaryClasses()
    -- UnitClass has no MILITARY member (verified via probe_classes): every
    -- unit belongs to exactly ONE class, so summing all unambiguously
    -- combat classes cannot double-count. Navy/monks/king excluded.
    local names = {
        "INFANTRY", "ARCHERY", "CAVALRY", "SIEGE_WEAPON", "HERO",
        "PETARD", "SCORPION", "RAIDER", "CAVALRY_RAIDER", "WAR_ELEPHANT",
        "ELEPHANT_ARCHER", "CAVALRY_ARCHER", "PHALANX", "PIKEMAN",
        "SPEARMAN", "TWO_HANDED_SWORDSMAN", "SCOUT_CAVALRY",
    }
    local out = {}
    for i = 1, #names do
        if UnitClass[names[i]] then
            table.insert(out, UnitClass[names[i]])
        end
    end
    return out
end

function Init()
    IPC.StartServer(PIPE)
    buildHouseTypeSet()
    buildAnimalTypeSet()
    militaryClasses = findMilitaryClasses()
    if #militaryClasses > 0 then
        Log("timeline_recorder: pipe " .. PIPE .. " started, "
            .. countHouseTypes() .. " house types known, "
            .. #animalTypes .. " animal types known, "
            .. #militaryClasses .. " military classes")
    else
        Log("timeline_recorder: pipe " .. PIPE .. " started, "
            .. countHouseTypes() .. " house types known, "
            .. #animalTypes .. " animal types known, "
            .. "NO military classes found (army census disabled)")
    end
end

local function runSeek()
    if not seek then
        return
    end
    local t = GetGameTime()
    if t >= seek.t then
        if seek.camera then
            SetCameraPosition(Vector2(seek.x, seek.y))
        end
        -- land the requested speed while STILL RUNNING: the engine
        -- ignores SetReplaySpeed while paused (the Python side cannot
        -- set it after this pause), but here the game runs at chase
        -- speed and the call is accepted. F3 resumes at this speed.
        if seek.speedName and ReplaySpeed[seek.speedName] then
            SetReplaySpeed(ReplaySpeed[seek.speedName])
        end
        if seek.pause then
            SetGamePaused(true)
        end
        send("ack", { action = "seek_reached", x = seek.x, y = seek.y,
                      paused = seek.pause })
        seek = nil
    else
        if seek.pause then
            SetGamePaused(false)
        end
        SetReplaySpeed(ReplaySpeed.FASTEST)
    end
end

-- A completed building re-registers under its own unit type (GetObjectById
-- on the foundation id returns nil then, e.g. for town centers); find an
-- owner+tile match of the completed type instead. "_FOUNDATION" suffix
-- mapping: TOWN_CENTER_FOUNDATION -> TOWN_CENTER.
local function completedTypeOf(unitType)
    local n = typeNames[unitType]
    if n and string.sub(n, -11) == "_FOUNDATION" then
        return UnitObjectType[string.sub(n, 1, #n - 11)]
    end
    return nil
end

local function buildingAtSameTile(f, p)
    -- Player:GetTownCenters() fallback: works whatever type id the
    -- completed town center registers under
    local ok, list = pcall(function() return p:GetTownCenters() end)
    if ok and list then
        local states = GetObjectStates(list)
        if states and states.id then
            for j = 1, #states.id do
                if states.playerId[j] == f.playerId
                    and math.abs(states.x[j] - f.x) <= 3
                    and math.abs(states.y[j] - f.y) <= 3 then
                    return true
                end
            end
        end
    end
    -- generic completed-type match by name mapping
    local ct = completedTypeOf(f.unitType)
    if not ct then
        return false
    end
    local ok2, states = pcall(function()
        return GetObjectStates(GetObjectsByType(ct))
    end)
    if not ok2 or not states then
        return false
    end
    -- last resort: any building owned by the player at that tile (covers
    -- completed types whose name mapping is missing, e.g. castles)
    local ok3, bst = pcall(function()
        return GetObjectStates(GetObjectsByClass(UnitClass.BUILDING))
    end)
    if ok3 and bst and bst.id then
        for j = 1, #bst.id do
            if bst.playerId[j] == f.playerId
                and math.abs(bst.x[j] - f.x) <= 2
                and math.abs(bst.y[j] - f.y) <= 2 then
                return true
            end
        end
    end
    return false
end

local function handleCommands()
    local msgs = IPC.GetMessages()
    if #msgs == 0 then
        return
    end
    for i = 1, #msgs do
        local msg = ParseJSON(msgs[i])
        local action = msg and msg.action
        if action == "seek" then
            seek = {
                t = tonumber(msg.t) or 0,
                x = tonumber(msg.x) or 0,
                y = tonumber(msg.y) or 0,
                pause = msg.pause ~= false,
                camera = msg.camera ~= false,
                speedName = msg.speedName,
            }
            send("ack", { action = "seek", t = seek.t })
        elseif action == "map_mode" then
            mapMode = msg.on ~= false
            -- (re)send meta + tc snapshots: the map client may attach
            -- mid-replay and would otherwise never see players, map
            -- size, type names or the starting TCs/castles
            sendMeta()
            for _, f in pairs(tcSeen) do
                send("tc_snap", { owner = f.playerId, x = f.x, y = f.y,
                                  kind = f.kind })
            end
            -- one-time API probe: PlayerAttribute + UnitClass member
            -- names, every global function/table, and player 1 object
            -- keys — lets the client hunt for score-ish APIs offline
            local attrNames = {}
            pcall(function()
                for k, _ in pairs(PlayerAttribute) do
                    table.insert(attrNames, k)
                end
            end)
            local classNames = {}
            pcall(function()
                for k, _ in pairs(UnitClass) do
                    table.insert(classNames, k)
                end
            end)
            table.sort(classNames)
            local globals = {}
            pcall(function()
                for k, v in pairs(_G) do
                    local tv = type(v)
                    if tv == "function" or tv == "table" then
                        table.insert(globals, k .. ":" .. tv)
                    end
                end
                table.sort(globals)
            end)
            local pkeys = {}
            pcall(function()
                local p1 = GetPlayerById(1)
                if p1 then
                    for k, v in pairs(p1) do
                        table.insert(pkeys, k .. ":" .. type(v))
                    end
                    table.sort(pkeys)
                end
            end)
            send("api", { playerAttributes = attrNames,
                          unitClasses = classNames,
                          globals = globals, playerKeys = pkeys })

            -- second probe, one row per target so a failure in one part
            -- cannot swallow the others (the api row arrived but api2
            -- vanished last run -> something here threw outside a pcall)
            local function keys(t)
                local out = {}
                pcall(function()
                    for k, v in pairs(t) do
                        table.insert(out, k .. ":" .. type(v))
                    end
                    table.sort(out)
                end)
                return out
            end
            local function probeRow(name, fn)
                local ok, res = pcall(fn)
                if ok and type(res) == "table" then
                    send(name, res)
                else
                    send(name, { error = tostring(res) })
                end
            end
            probeRow("api2_fact", function()
                return { keys = keys(Fact) }
            end)
            probeRow("api2_rt", function()
                return { keys = keys(ResourceTracker) }
            end)
            probeRow("api2_sp", function()
                return { keys = keys(SnapshotPlayer) }
            end)
            probeRow("api2_player", function()
                return { keys = keys(Player) }
            end)
            probeRow("api2_named", function()
                local out = {}
                local p1 = GetPlayerById(1)
                if p1 then
                    for _, name in ipairs({ "Score", "Scores", "GetScore",
                                            "GetFacts", "Facts", "Resources",
                                            "GetResources", "Gathered",
                                            "Stats", "GetStats", "GetFact",
                                            "ScoreValue", "GetAttributes" }) do
                        local ok, v = pcall(function() return p1[name] end)
                        if ok and v ~= nil then
                            table.insert(out, name .. ":" .. type(v))
                        end
                    end
                end
                table.sort(out)
                return { named = out }
            end)
            probeRow("api2_utc", function()
                local out = {}
                table.insert(out, "p1t83="
                    .. tostring(GetUnitTypeCount(1, 83)))
                table.insert(out, "t83=" .. tostring(GetUnitTypeCount(83)))
                table.insert(out, "p1villager="
                    .. tostring(GetUnitTypeCount(1, UnitObjectType.VILLAGER)))
                return { counts = out }
            end)
            probeRow("api2_getfact", function()
                local out = {}
                for _, name in ipairs({ "PlayerScore", "Score", "Scores",
                                        "ResourcesGathered", "GatheredFood",
                                        "PlayerKills", "MilitaryScore" }) do
                    local ok, v = pcall(function() return GetFact(name) end)
                    if ok then
                        table.insert(out, name .. "=" .. tostring(v))
                    end
                end
                return { samples = out }
            end)
            send("ack", { action = action, on = mapMode })
        elseif action == "camera" and msg.x then
            SetCameraPosition(Vector2(msg.x, msg.y))
            send("ack", { action = action })
        elseif action == "pan" then
            -- engine-side camera tween: interpolates EVERY Update (no
            -- pipe batching) between the endpoints over `seconds` of
            -- game time; cancels any running tween
            pan = {
                x0 = tonumber(msg.x0) or 0,
                y0 = tonumber(msg.y0) or 0,
                x1 = tonumber(msg.x1) or 0,
                y1 = tonumber(msg.y1) or 0,
                seconds = math.max(0.1, tonumber(msg.seconds) or 1),
                t0 = GetGameTime(),
            }
            send("ack", { action = action, seconds = pan.seconds })
        elseif action == "pause" then
            SetGamePaused(true)
            send("ack", { action = action })
        elseif action == "unpause" then
            SetGamePaused(false)
            send("ack", { action = action })
        elseif action == "set_speed" then
            -- v (numeric) or speedName ("SLOW"/"NORMAL"/"FAST"/"FASTEST")
            local v = msg.v
            if msg.speedName and ReplaySpeed[msg.speedName] then
                v = ReplaySpeed[msg.speedName]
            end
            SetReplaySpeed(v or ReplaySpeed.NORMAL)
            send("ack", { action = action })
        elseif action == "set_game_speed" then
            -- MISC "Game Speed Multiplier": overrides the game's own
            -- speed (Default/1/1.5/2/5/10/20/30) on top of the replay
            -- speed preset; "Default" restores the game's own speed
            local v = msg.v
            if v == "Default" or v == "default" then
                local ok, err = pcall(SetGameSpeedMultiplier, 0)
                send("ack", { action = action, v = "Default",
                              ok = ok, err = err })
            else
                v = tonumber(v)
                if v then
                    local ok, err = pcall(SetGameSpeedMultiplier, v)
                    send("ack", { action = action, v = v,
                                  ok = ok, err = err })
                end
            end
        elseif action == "restart" then
            DispatchRestartGame()
        elseif action == "probe" then
            local body = { action = "probe" }
            local ok, err = pcall(function()
                local pt = tonumber(msg.unitType)
                body.probeType = pt
                body.name = pt and typeNames[pt]
                local nm = body.name or (pt and tostring(pt))
                body.completedType = pt and completedTypeOf(pt)
                -- list objects by the foundation type ...
                local ok2, st = pcall(function()
                    return GetObjectStates(GetObjectsByType(pt))
                end)
                if ok2 and st and st.id and #st.id > 0 then
                    body.byType = { count = #st.id }
                    local sample = {}
                    for j = 1, math.min(#st.id, 8) do
                        table.insert(sample, { id = st.id[j],
                            owner = st.playerId[j],
                            x = st.x[j], y = st.y[j] })
                    end
                    body.byType.sample = sample
                else
                    body.byType = { error = ok2 and "empty" or "nil" }
                end
                -- ... and by the likely completed type
                local ct = pt and completedTypeOf(pt)
                if ct then
                    local ok3, st3 = pcall(function()
                        return GetObjectStates(GetObjectsByType(ct))
                    end)
                    if ok3 and st3 and st3.id and #st3.id > 0 then
                        body.byCompleted = { count = #st3.id,
                            typeName = typeNames[ct] }
                        local s3 = {}
                        for j = 1, math.min(#st3.id, 8) do
                            table.insert(s3, { id = st3.id[j],
                                owner = st3.playerId[j],
                                x = st3.x[j], y = st3.y[j] })
                        end
                        body.byCompleted.sample = s3
                    else
                        body.byCompleted = { typeName = typeNames[ct],
                            error = ok3 and "empty" or "nil" }
                    end
                end
                -- UnitObjectType enum names that could be TC-like
                local hints = {}
                for hName, _ in pairs(UnitObjectType) do
                    if string.find(string.upper(hName), "TOWN") then
                        table.insert(hints, hName)
                    end
                end
                table.sort(hints)
                body.townHints = hints
                body.label = nm
            end)
            body.ok = ok
            if not ok then
                body.error = tostring(err)
            end
            send("probe_result", body)
        elseif action == "object_pos" then
            -- live position of one object (the nomad first-TC builder
            -- villager that the record flow's camera follows)
            local oid = tonumber(msg.id)
            local ok, res = pcall(function()
                local obj = GetObjectById(oid)
                if not obj then
                    return nil
                end
                local p = obj:GetPosition()
                return { x = p.x, y = p.y, z = p.z }
            end)
            send("ack", { action = "object_pos", id = oid,
                          ok = ok,
                          pos = res,
                          err = ok and nil or tostring(res) })
        elseif action == "probe_env" then
            -- introspection: every global the injector exposes (docs
            -- list the API, but the runtime may have undocumented
            -- functions - e.g. anything HUD/UI related)
            local body = { action = "probe_env", globals = {} }
            local ok, err = pcall(function()
                for name, value in pairs(_G) do
                    table.insert(body.globals, name .. ":" .. type(value))
                end
                table.sort(body.globals)
            end)
            body.ok = ok
            if not ok then
                body.err = tostring(err)
            end
            send("ack", body)
        elseif action == "probe_classes" then
            -- introspection: which UnitClass members exist, which yield
            -- objects (military class discovery for army_pop)
            local body = { action = "probe_classes", classes = {} }
            local ok, err = pcall(function()
                for name, value in pairs(UnitClass) do
                    if type(value) == "number" then
                        table.insert(body.classes, name .. "=" .. value)
                    end
                end
                table.sort(body.classes)
                local tries = { "MILITARY", "MILITARY_UNIT", "SOLDIER" }
                for i = 1, #tries do
                    local v = UnitClass[tries[i]]
                    if v then
                        local ok2, st = pcall(function()
                            return GetObjectStates(GetObjectsByClass(v))
                        end)
                        local n = ok2 and st and st.id and #st.id or -1
                        body["try_" .. tries[i]] = n
                    end
                end
            end)
            body.ok = ok
            if not ok then
                body.error = tostring(err)
            end
            send("probe_result", body)
        elseif action == "ping" then
            send("pong", { })
        end
    end
end

local function trackBuildings()
    local seen = {}
    local count = GetPlayerCount()
    for i = 0, count - 1 do
        local p = GetPlayerById(i)
        if p then
            local ok, found = pcall(p.GetFoundations, p)
            if ok and found then
                local states = GetObjectStates(found)
                for j = 1, #states.id do
                    local id = states.id[j]
                    seen[id] = { unitType = states.unitType[j],
                                 playerId = states.playerId[j],
                                 x = states.x[j], y = states.y[j],
                                 hp = states.hitpoints[j] }
                end
            end
        end
    end

    -- new foundations (or a same-ID type swap: a deleted foundation's id
    -- can be reused for a NEW building in the same sample — e.g. a
    -- misplaced house lot replaced by a TC on nomad — which would
    -- otherwise swallow the new building's start row)
    for id, f in pairs(seen) do
        local prev = foundations[id]
        if prev and prev.unitType ~= f.unitType then
            send("bld_event", {
                event = "lost",
                isHouse = isHouseType(prev.unitType),
                isTC = isTCType(prev.unitType),
                isCastle = false,
                name = typeNames[prev.unitType],
                id = id, unitType = prev.unitType, owner = prev.playerId,
                x = prev.x, y = prev.y,
            })
            prev = nil
        end
        if not prev then
            local n = typeNames[f.unitType]
            send("bld_event", {
                event = "start",
                isHouse = isHouseType(f.unitType),
                isTC = isTCType(f.unitType),
                isCastle = n and (n == "CASTLE" or n == "KREPOST")
                    and true or false,
                name = typeNames[f.unitType],
                id = id, unitType = f.unitType, owner = f.playerId,
                x = f.x, y = f.y, hp = f.hp,
            })
        end
    end

    -- disappeared foundations: finished (object still exists, or the
    -- completed building sits on the tile under its own unit type) or lost
    for id, f in pairs(foundations) do
        if not seen[id] then
            local obj = GetObjectById(id)
            if obj or buildingAtSameTile(f, GetPlayerById(f.playerId)) then
                send("bld_event", {
                    event = "done",
                    isHouse = isHouseType(f.unitType),
                    isTC = isTCType(f.unitType),
                    isCastle = typeNames[f.unitType]
                        and (typeNames[f.unitType] == "CASTLE"
                             or typeNames[f.unitType] == "KREPOST")
                        and true or false,
                    name = typeNames[f.unitType],
                    id = id, unitType = f.unitType, owner = f.playerId,
                    x = f.x, y = f.y,
                })
            else
                send("bld_event", {
                    event = "lost",
                    isHouse = isHouseType(f.unitType),
                    isTC = isTCType(f.unitType),
                    name = typeNames[f.unitType],
                    id = id, unitType = f.unitType, owner = f.playerId,
                    x = f.x, y = f.y,
                })
            end
        end
    end

    foundations = seen
end

local function sampleMilitary()
    if #militaryClasses == 0 then
        if not armyWarned then
            armyWarned = true
            send("army_pop", { counts = {} })
        end
        return
    end
    local counts = {}
    local now = {}
    for c = 1, #militaryClasses do
        local ok, states = pcall(function()
            return GetObjectStates(GetObjectsByClass(militaryClasses[c]))
        end)
        if ok and states and states.id then
            for i = 1, #states.playerId do
                local pid = states.playerId[i]
                if pid and pid >= 0 then
                    counts[pid] = (counts[pid] or 0) + 1
                end
                now[states.id[i]] = { x = states.x[i], y = states.y[i],
                                      playerId = states.playerId[i],
                                      unitType = states.unitType[i] }
            end
        end
    end
    send("army_pop", { counts = counts })
    for id, v in pairs(militarySeen) do
        if not now[id] then
            send("unit_death", { id = id, player = v.playerId,
                                 x = v.x, y = v.y })
        end
    end
    militarySeen = now
    return now
end

local function trackKeyBuildings()
    -- completed town centers and castles: disappearance = destroyed
    local seen = {}
    local count = GetPlayerCount()
    for i = 0, count - 1 do
        local p = GetPlayerById(i)
        if p then
            local ok, list = pcall(p.GetTownCenters, p)
            if ok and list then
                local states = GetObjectStates(list)
                for j = 1, #states.id do
                    seen[states.id[j]] = { playerId = states.playerId[j],
                                           x = states.x[j], y = states.y[j],
                                           kind = "TOWN_CENTER" }
                end
            end
        end
    end
    if UnitObjectType.CASTLE then
        local ok, states = pcall(function()
            return GetObjectStates(GetObjectsByType(UnitObjectType.CASTLE))
        end)
        if ok and states and states.id then
            for j = 1, #states.id do
                if seen[states.id[j]] == nil then
                    seen[states.id[j]] = { playerId = states.playerId[j],
                                           x = states.x[j], y = states.y[j],
                                           kind = "CASTLE" }
                end
            end
        end
    end
    if UnitObjectType.KREPOST then
        -- Krepost = castle category (Bulgarians): destroyed completed
        -- Kreposts fire the same castle_fall events
        local ok, states = pcall(function()
            return GetObjectStates(GetObjectsByType(UnitObjectType.KREPOST))
        end)
        if ok and states and states.id then
            for j = 1, #states.id do
                if seen[states.id[j]] == nil then
                    seen[states.id[j]] = { playerId = states.playerId[j],
                                           x = states.x[j], y = states.y[j],
                                           kind = "KREPOST" }
                end
            end
        end
    end

    for id, f in pairs(tcSeen) do
        if not seen[id] then
            send("bld_event", {
                event = "lost",
                isHouse = false,
                isTC = f.kind == "TOWN_CENTER",
                isCastle = f.kind == "CASTLE" or f.kind == "KREPOST",
                name = f.kind,
                id = id, owner = f.playerId,
                x = f.x, y = f.y,
            })
        end
    end
    tcSeen = seen
end

-- score capture: auto-detect the GetFact signature that returns a
-- number for Fact.CURRENT_SCORE ("method" = player:GetFact(fact),
-- "fp" = GetFact(fact, pid), "pf" = GetFact(pid, fact)); "none" when
-- nothing works -> the client falls back to its eco count
local scoreSig = nil
local chatSeen = 0
local function scoreCall(pid)
    local factIdx = Fact.CURRENT_SCORE
    if scoreSig == "method" then
        local p = GetPlayerById(pid)
        return p:GetFact(factIdx)
    elseif scoreSig == "fp" then
        return GetFact(factIdx, pid)
    elseif scoreSig == "pf" then
        return GetFact(pid, factIdx)
    end
    error("no signature")
end

local function detectScoreSig()
    if scoreSig then return end
    local ok, factIdx = pcall(function() return Fact.CURRENT_SCORE end)
    if not ok or type(factIdx) ~= "number" then
        scoreSig = "none"
        return
    end
    for _, sig in ipairs({ "method", "fp", "pf" }) do
        scoreSig = sig
        local ok2, v = pcall(scoreCall, 1)
        if ok2 and type(v) == "number" then
            return
        end
    end
    scoreSig = "none"
end

local function sampleStats()
    -- per player: age, food, wood, stone, gold, pop current
    local rows = {}
    local count = GetPlayerCount()
    for i = 0, count - 1 do
        local p = GetPlayerById(i)
        if p then
            local row = { -1, -1, -1, -1, -1, -1 }
            local ok, pattr = pcall(function()
                row[1] = p:GetAttribute(PlayerAttribute.AGE)
                row[2] = p:GetAttribute(PlayerAttribute.FOOD)
                row[3] = p:GetAttribute(PlayerAttribute.WOOD)
                row[4] = p:GetAttribute(PlayerAttribute.STONE)
                row[5] = p:GetAttribute(PlayerAttribute.GOLD)
                row[6] = p:GetAttribute(PlayerAttribute.POP_CURRENT)
            end)
            if not ok then
                row = nil
            end
            if ok then
                -- engine score (top-bar points); guarded: older APIs may
                -- lack PlayerAttribute.SCORE -> row stays 6 long
                local ok2, score = pcall(function()
                    return p:GetAttribute(PlayerAttribute.SCORE)
                end)
                if ok2 and score then
                    row[7] = score
                end
            end
            table.insert(rows, row)
        else
            table.insert(rows, nil)
        end
    end
    send("stats", { stats = rows })

    -- chat capture: resign/defeat announcements arrive as system chat
    -- messages; forward each message with the game time so the client
    -- can timestamp resignations exactly (unknown message shape is
    -- sanitized: strings/numbers/booleans pass, rest is stringified)
    pcall(function()
        local msgs = GetAllChatMessages()
        if type(msgs) ~= "table" or #msgs < chatSeen then
            chatSeen = 0
        end
        if #msgs > chatSeen then
            local out = {}
            for i = chatSeen + 1, #msgs do
                local m = msgs[i]
                if type(m) == "table" then
                    local clean = {}
                    for k, v in pairs(m) do
                        local tv = type(v)
                        if tv == "string" or tv == "number"
                            or tv == "boolean" then
                            clean[k] = v
                        else
                            clean[k] = tostring(v)
                        end
                    end
                    table.insert(out, clean)
                else
                    table.insert(out, { text = tostring(m) })
                end
            end
            chatSeen = #msgs
            send("chat", { time = GetGameTime(), msgs = out })
        end
    end)

    -- real score per player when the Fact API cooperates (sent at the
    -- stats cadence so the client can align both by time)
    detectScoreSig()
    if scoreSig ~= "none" then
        local scores = {}
        local inGame = {}
        for i = 1, count - 1 do
            local ok, v = pcall(scoreCall, i)
            if ok and type(v) == "number" then
                scores[i] = math.floor(v + 0.5)
            end
            -- PLAYER_IN_GAME fact: 1 while the player is still in the
            -- game, 0 after resigning/defeat (client crosses them out)
            local ok2, ing = pcall(function()
                local p = GetPlayerById(i)
                return p:GetFact(Fact.PLAYER_IN_GAME)
            end)
            if ok2 and ing ~= nil then
                inGame[i] = (ing == true or ing == 1) and 1 or 0
            end
        end
        if next(scores) then
            send("scores", { scores = scores, inGame = inGame,
                             sig = scoreSig })
        end
    end
end

local function sample()
    -- villager census (v1 behaviour); fishing ships and trade carts/
    -- cogs join the census (flagged eco=true) so the client can show an
    -- exact "eco count" per player
    local states = GetObjectStates(GetObjectsByClass(UnitClass.VILLAGER))
    local counts = {}
    local now = {}
    for i = 1, #states.playerId do
        local pid = states.playerId[i]
        if pid and pid >= 0 then
            counts[pid] = (counts[pid] or 0) + 1
        end
        now[states.id[i]] = { x = states.x[i], y = states.y[i],
                              playerId = states.playerId[i],
                              unitType = states.unitType[i] }
    end
    pcall(function()
        local es = GetObjectStates(GetObjectsByClasses({
            UnitClass.FISHING_SHIP, UnitClass.TRADE_CART,
            UnitClass.TRADE_COG }))
        if es and es.id then
            for i = 1, #es.id do
                local pid = es.playerId[i]
                if pid and pid >= 0 then
                    counts[pid] = (counts[pid] or 0) + 1
                end
                now[es.id[i]] = { x = es.x[i], y = es.y[i],
                                  playerId = pid,
                                  unitType = es.unitType[i],
                                  eco = true }
            end
        end
    end)
    send("vill_pop", { counts = counts })

    -- villager death: id present last sample, gone now; position is the
    -- last known one (1 game-second resolution, census is game-time
    -- scheduled so it stays 1 Hz in game time at any replay speed)
    for id, v in pairs(villSeen) do
        if not now[id] then
            local row = { id = id, player = v.playerId, x = v.x, y = v.y }
            local a = animalNear(v.x, v.y)
            if a then
                row.by_animal = a.name
                row.animal_id = a.id
                row.animal_x = a.x
                row.animal_y = a.y
            end
            local u = unitNear(v.x, v.y, v.playerId)
            if u then
                row.by_player = u.player
                row.by_player_name = u.name
                row.by_player_dist = u.dist
                row.by_player_id = u.id
            end
            local b = bldNear(v.x, v.y, v.playerId)
            if b then
                row.by_bld = b.name
                row.by_bld_owner = b.player
                row.by_bld_dist = b.dist
            end
            send("vill_death", row)
        end
    end
    villSeen = now

    -- nomad window: stream every villager position while the game is
    -- young (nomad maps start TC-less with 3 villagers per player, so
    -- this is light) — the planner uses it to pick each player's
    -- first-TC builder and the record flow follows that villager
    local gt = GetGameTime()
    if gt and gt < VILL_POS_WINDOW_S then
        local pos = {}
        for id, v in pairs(now) do
            table.insert(pos, { id = id, player = v.playerId,
                                x = v.x, y = v.y })
        end
        send("vill_pos", { positions = pos })
    end

    -- TC foundations under construction: who is building (villagers
    -- within 3 tiles)? The planner picks the FIRST builder per player;
    -- the record flow follows them via the object_pos action.
    for id, f in pairs(foundations) do
        if isTCType(f.unitType) then
            local builders = {}
            for vid, v in pairs(now) do
                if v.playerId == f.playerId
                        and math.abs(v.x - f.x) <= 3.0
                        and math.abs(v.y - f.y) <= 3.0 then
                    table.insert(builders, vid)
                end
            end
            table.sort(builders)
            send("tc_build", { id = id, owner = f.playerId,
                               x = f.x, y = f.y, builders = builders })
        end
    end

    sampleStats()
    trackBuildings()
    local milNow = sampleMilitary() or {}
    trackKeyBuildings()
    if mapMode then
        -- columnar full-object snapshot for the map animation: buildings
        -- (completed + foundations), villagers, military; player-owned
        -- only (gaia skipped); positions rounded to 0.1 tile
        local b, bSeen = {}, {}
        local ok, cst = pcall(function()
            return GetObjectStates(GetObjectsByClass(UnitClass.BUILDING))
        end)
        if ok and cst and cst.id then
            for i = 1, #cst.id do
                local pid = cst.playerId[i]
                if pid and pid >= 1 then
                    local id = cst.id[i]
                    bSeen[id] = true
                    table.insert(b, { id, pid, cst.unitType[i],
                                      round1(cst.x[i]), round1(cst.y[i]),
                                      hp1(cst.hitpoints[i]) })
                end
            end
        end
        -- walls live in their own UnitClass.WALL (not BUILDING) and only
        -- leak into the BUILDING query intermittently -> built walls
        -- vanished from snapshots; query them explicitly
        local okw, wst = pcall(function()
            return GetObjectStates(GetObjectsByClass(UnitClass.WALL))
        end)
        if okw and wst and wst.id then
            for i = 1, #wst.id do
                local pid = wst.playerId[i]
                if pid and pid >= 1 and not bSeen[wst.id[i]] then
                    bSeen[wst.id[i]] = true
                    table.insert(b, { wst.id[i], pid, wst.unitType[i],
                                      round1(wst.x[i]), round1(wst.y[i]),
                                      hp1(wst.hitpoints[i]) })
                end
            end
        end
        for id, f in pairs(foundations) do
            if not bSeen[id] and f.playerId and f.playerId >= 1 then
                table.insert(b, { id, f.playerId, f.unitType,
                                  round1(f.x), round1(f.y),
                                  f.hp and hp1(f.hp) or nil })
            end
        end
        local v, m = {}, {}
        -- military class tags for the map animation: infantry/cavalry
        -- get distinct dot sizes client-side (guarded: members may be
        -- absent in older APIs). Scouts / cav archers / raiders live in
        -- their own classes but play as cavalry -> tagged "c" too.
        local tag = {}
        local tagClasses = {
            i = { "INFANTRY" },
            c = { "CAVALRY", "SCOUT_CAVALRY", "CAVALRY_ARCHER",
                  "CAVALRY_RAIDER" },
        }
        for t, names in pairs(tagClasses) do
            for _, cn in ipairs(names) do
                if UnitClass[cn] then
                    pcall(function()
                        local st = GetObjectStates(
                            GetObjectsByClass(UnitClass[cn]))
                        if st and st.id then
                            for i = 1, #st.id do tag[st.id[i]] = t end
                        end
                    end)
                end
            end
        end
        for id, u in pairs(now) do
            if u.playerId and u.playerId >= 1 then
                table.insert(v, { id, u.playerId, u.unitType,
                                  round1(u.x), round1(u.y),
                                  u.eco and "e" or nil })
            end
        end
        for id, u in pairs(milNow) do
            if u.playerId and u.playerId >= 1 then
                table.insert(m, { id, u.playerId, u.unitType,
                                  round1(u.x), round1(u.y), tag[id] })
            end
        end
        -- warships: navy is excluded from the army census, so capture
        -- them here for the map (tag "s" -> ship silhouette client-side)
        pcall(function()
            local nst = GetObjectStates(GetObjectsByClass(UnitClass.WARSHIP))
            if nst and nst.id then
                for i = 1, #nst.id do
                    local pid = nst.playerId[i]
                    if pid and pid >= 1 then
                        table.insert(m, { nst.id[i], pid, nst.unitType[i],
                                          round1(nst.x[i]), round1(nst.y[i]),
                                          "s" })
                    end
                end
            end
        end)
        send("objects", { b = b, v = v, m = m })
    end
    if not snapSent then
        snapSent = true
        for _, f in pairs(tcSeen) do
            send("tc_snap", { owner = f.playerId, x = f.x, y = f.y,
                              kind = f.kind })
        end
    end
end

function Update()
    handleCommands()

    if pan then
        -- apply the camera tween every Update (render-smooth; game time
        -- drives it so the pan ends exactly when the window starts)
        local t = GetGameTime()
        local f = math.min(1.0, math.max(0.0,
            (t - pan.t0) / pan.seconds))
        SetCameraPosition(Vector2(pan.x0 + (pan.x1 - pan.x0) * f,
                                  pan.y0 + (pan.y1 - pan.y0) * f))
        if f >= 1.0 then
            pan = nil
        end
    end

    local t = GetGameTime()
    -- time can go backwards (replay restart/rewind): reset the schedule
    if lastT and t and t < lastT then
        nextSampleTime = 0
        foundations = {}
        villSeen = {}
        militarySeen = {}
        tcSeen = {}
        snapSent = false
        pan = nil
    end
    lastT = t

    tickCounter = tickCounter + 1
    if tickCounter % 10 == 0 then
        send("tick", { t = t, ns = nextSampleTime, n = tickCounter })
    end

    if not t or t < nextSampleTime then
        return
    end
    nextSampleTime = t + SAMPLE_INTERVAL

    if not metaSent then
        sendMeta()
        metaSent = true
    end

    sample()
    runSeek()
end

function End()
    send("done", {})
end
