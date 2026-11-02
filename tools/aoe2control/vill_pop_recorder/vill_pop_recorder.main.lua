-- vill_pop_recorder: samples villager counts per player every second and
-- sends them over a named pipe as JSON.
--
-- Requirements (CONTROL settings): enable "Modules See Everything" so the
-- module counts villagers for ALL players, not only its own visible ones.
-- Works fine in replay/spectate mode.
--
-- Output envelope (client side, via pipe AoE2_Highlights):
--   {"type":"vill_pop","time":<game seconds>,"counts":{<playerId>:<count>}}
--   {"type":"meta","map":<w x h>,"players":<count>,"sampleInterval":<s>}

local PIPE = "AoE2_Highlights"
local SAMPLE_INTERVAL = 1.0
local nextSampleTime = 0
local lastT = nil
local metaSent = false
local tickCounter = 0

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
    })
end

function Init()
    IPC.StartServer(PIPE)
    Log("vill_pop_recorder: pipe " .. PIPE .. " started")
end

function Update()
    if not IPC.HasMessages() then
        -- consume messages if an external client ever writes to us
        for _, raw in ipairs(IPC.GetMessages()) do
            Log("vill_pop_recorder: got " .. tostring(raw))
        end
    end

    local t = GetGameTime()
    -- time can go BACKWARDS (match replayed/loaded after menu time):
    -- reset the sampling schedule when that happens
    if lastT and t and t < lastT then
        nextSampleTime = 0
    end
    lastT = t
    -- debug telemetry so gating bugs are visible client-side
    tickCounter = tickCounter + 1
    if tickCounter % 5 == 0 then
        IPC.Send({ type = "tick", t = t, ns = nextSampleTime, n = tickCounter })
    end
    if not t or t < nextSampleTime then
        return
    end
    nextSampleTime = nextSampleTime + SAMPLE_INTERVAL
    if nextSampleTime < t then
        nextSampleTime = t + SAMPLE_INTERVAL
    end

    local states = GetObjectStates(GetObjectsByClass(UnitClass.VILLAGER))
    local counts = {}
    for i = 1, #states.playerId do
        local pid = states.playerId[i]
        if pid and pid >= 0 then
            counts[pid] = (counts[pid] or 0) + 1
        end
    end

    if not metaSent then
        sendMeta()
        metaSent = true
    end

    IPC.Send({
        type = "vill_pop",
        time = t,
        counts = counts,
    })
end

function End()
    IPC.Send({ type = "done", time = GetGameTime() })
end
