-- timeline_recorder: richer highlight-source stream.
--
-- Every game second sends:
--   vill_pop  {"time", "counts": {pid: count}}           (1 Hz villager census)
--   stats     {"time", "stats": [[age,food,wood,stone,gold,pop], ...]}
--             per player index 1..n (age: 0=Dark..3=Imperial, PlayerAttribute ids)
--   bld_*     building lifecycle rows (house_start / house_done / house_lost
--             with isHouse flag; other buildings with isHouse=false)
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

local houseTypes = {}     -- [unitType id] = true, built from UnitObjectType names
local typeNames = {}      -- [unitType id] = name, filled with house ids at load
local foundations = {}    -- [objectId] = {unitType, playerId, x, y, hp}
local seek = nil          -- {"t": number, "x": number, "y": number,
                          --  "pause": bool, "camera": bool}

local function isHouseType(ot)
    return houseTypes[ot] or false
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

function Init()
    IPC.StartServer(PIPE)
    buildHouseTypeSet()
    Log("timeline_recorder: pipe " .. PIPE .. " started, "
        .. countHouseTypes() .. " house types known")
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
    for j = 1, #states.id do
        if states.playerId[j] == f.playerId
            and math.abs(states.x[j] - f.x) <= 2
            and math.abs(states.y[j] - f.y) <= 2 then
            return true
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
            SetReplaySpeed(msg.v or ReplaySpeed.NORMAL)
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
            send("bld_event", {
                event = "start",
                isHouse = isHouseType(f.unitType),
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
                    name = typeNames[f.unitType],
                    id = id, unitType = f.unitType, owner = f.playerId,
                    x = f.x, y = f.y,
                })
            else
                send("bld_event", {
                    event = "lost",
                    isHouse = isHouseType(f.unitType),
                    name = typeNames[f.unitType],
                    id = id, unitType = f.unitType, owner = f.playerId,
                    x = f.x, y = f.y,
                })
            end
        end
    end

    foundations = seen
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
    for i = 1, #states.playerId do
        local pid = states.playerId[i]
        if pid and pid >= 0 then
            counts[pid] = (counts[pid] or 0) + 1
        end
    end
    send("vill_pop", { counts = counts })

    sampleStats()
    trackBuildings()
end

function Update()
    handleCommands()

    local t = GetGameTime()
    -- time can go backwards (replay restart/rewind): reset the schedule
    if lastT and t and t < lastT then
        nextSampleTime = 0
        foundations = {}
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
