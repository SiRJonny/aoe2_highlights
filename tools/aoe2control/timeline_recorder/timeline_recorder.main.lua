-- timeline_recorder: richer highlight-source stream.
--
-- Every game second sends:
--   vill_pop  {"time", "counts": {pid: count}}           (1 Hz villager census)
--   vill_death {"time", "id", "player", "x", "y"}        villager gone since
--             previous sample (death at last known position, +-1 s / +-1 tile;
--             also fires for fishing ships/converted units, between-sample
--             spawn+death pairs are impossible to see)
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

local nextSampleTime = 0
local lastT = nil
local metaSent = false
local tickCounter = 0
local villSeen = {}   -- villager id -> last known {x, y, playerId}
local militarySeen = {} -- military unit id -> last known {x, y, playerId}
local militaryClasses = {} -- list of UnitClass ids counted as army
local armyWarned = false
local tcSeen = {}     -- completed TC/castle id -> {playerId, x, y, kind}

local houseTypes = {}     -- [unitType id] = true, built from UnitObjectType names
local typeNames = {}      -- [unitType id] = name, filled with house ids at load
local foundations = {}    -- [objectId] = {unitType, playerId, x, y, hp}
local seek = nil          -- {"t": number, "x": number, "y": number,
                          --  "pause": bool, "camera": bool}

local function isHouseType(ot)
    return houseTypes[ot] or false
end

local function isTCType(ot)
    local n = typeNames[ot]
    return n ~= nil and string.sub(n, 1, 11) == "TOWN_CENTER"
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
    militaryClasses = findMilitaryClasses()
    if #militaryClasses > 0 then
        Log("timeline_recorder: pipe " .. PIPE .. " started, "
            .. countHouseTypes() .. " house types known, "
            .. #militaryClasses .. " military classes")
    else
        Log("timeline_recorder: pipe " .. PIPE .. " started, "
            .. countHouseTypes() .. " house types known, "
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
            }
        elseif action == "camera" and msg.x then
            SetCameraPosition(Vector2(msg.x, msg.y))
            send("ack", { action = action })
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

    -- new foundations
    for id, f in pairs(seen) do
        if not foundations[id] then
            local n = typeNames[f.unitType]
            send("bld_event", {
                event = "start",
                isHouse = isHouseType(f.unitType),
                isTC = isTCType(f.unitType),
                isCastle = n and string.find(n, "CASTLE", 1, true) and true or false,
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
                        and string.find(typeNames[f.unitType], "CASTLE",
                                        1, true) and true or false,
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
                                      playerId = states.playerId[i] }
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

    for id, f in pairs(tcSeen) do
        if not seen[id] then
            send("bld_event", {
                event = "lost",
                isHouse = false,
                isTC = f.kind == "TOWN_CENTER",
                isCastle = f.kind == "CASTLE",
                name = f.kind,
                id = id, owner = f.playerId,
                x = f.x, y = f.y,
            })
        end
    end
    tcSeen = seen
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
            table.insert(rows, row)
        else
            table.insert(rows, nil)
        end
    end
    send("stats", { stats = rows })
end

local function sample()
    -- villager census (v1 behaviour)
    local states = GetObjectStates(GetObjectsByClass(UnitClass.VILLAGER))
    local counts = {}
    local now = {}
    for i = 1, #states.playerId do
        local pid = states.playerId[i]
        if pid and pid >= 0 then
            counts[pid] = (counts[pid] or 0) + 1
        end
        now[states.id[i]] = { x = states.x[i], y = states.y[i],
                              playerId = states.playerId[i] }
    end
    send("vill_pop", { counts = counts })

    -- villager death: id present last sample, gone now; position is the
    -- last known one (1 game-second resolution, census is game-time
    -- scheduled so it stays 1 Hz in game time at any replay speed)
    for id, v in pairs(villSeen) do
        if not now[id] then
            send("vill_death", { id = id, player = v.playerId,
                                 x = v.x, y = v.y })
        end
    end
    villSeen = now

    sampleStats()
    trackBuildings()
    sampleMilitary()
    trackKeyBuildings()
end

function Update()
    handleCommands()

    local t = GetGameTime()
    -- time can go backwards (replay restart/rewind): reset the schedule
    if lastT and t and t < lastT then
        nextSampleTime = 0
        foundations = {}
        villSeen = {}
        militarySeen = {}
        tcSeen = {}
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
