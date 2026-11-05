# CONTROL Lua Engine Reference

This file describes the Lua API of CONTROL (AoE2Control) 1.1.3 for coding agents that write CONTROL modules. Use only the functions, methods, tables and enums listed here. CONTROL is not the AoE2 `.per` AI scripting language, and it has no other game-engine API. A module can pass values to a computer player's `.per` script through its goals (`SetGoal`, `Player:GetGoal`).

Full documentation: [AoE2Control Documentation](https://aoe2control.github.io/). If this file and the site disagree, the site is correct.

**Signatures:** arguments marked `?` are optional; every other argument is required. When an optional argument has a default, the description gives it. Whole-number parameters (ids, counts, enums) also accept a float with a whole value such as `10.0`, but not `10.5`.

---

## 1. Overview

- Windows only. CONTROL runs inside Age of Empires II: Definitive Edition.
- Multiplayer: modules run only when the match has cheats enabled. Without cheats, `Init()`, `Update()` and `Render()` are not called. A match whose type CONTROL cannot tell counts as multiplayer.
- Config folder: `%appdata%\CONTROL\AoE2Control\`. It holds `settings.ini`, `menu.ini` (menu window layout), `modules\` and `diagnostics\`.
- Each of the 8 player slots can run its own module. Each slot has its own Lua state; two slots that run the same module share no variables.
- Shift opens and closes the CONTROL menu. Delete unloads CONTROL.

---

## 2. Module System

### Entry files

| File | Contents |
|------|----------|
| `{moduleName}.main.lua` | Lua source. |
| `{moduleName}.main.module` | Packaged module. |

- The module name is the part before `.main.`: letters, digits and underscores, starting with a letter or underscore. Other names (`my-bot.main.lua`) are ignored.
- CONTROL looks for entry files in `modules\` and up to three folders below it.
- When two entry files have the same name, the one closest to `modules\` wins. In the same folder, `.main.lua` wins over `.main.module`. Two files with the same name at the same depth make loading fail with an error.
- Put each module in its own folder:

```text
modules/
└── my_module/
    ├── my_module.main.lua
    └── utils/
        └── logger.lua
```

### require

`require` is CONTROL's own function.

- Paths are relative to the folder of the entry file. Each dot is a folder: `require("utils.logger")` loads `utils/logger.lua`, or `utils/logger.module` if the `.lua` file does not exist.
- A name has one to four dot-separated parts and at most 127 characters. Each part uses letters, digits and underscores and starts with a letter or underscore. `require("../x")` and five-part names are refused.
- A file cannot reach outside the module folder.
- The first `require` of a name runs the file and stores its return value; later calls return the stored value. A file that returns nothing gives `true`.
- An invalid name, a missing file, a file with an error, or two files requiring each other make `require` return `nil` and log the reason.
- Reloading the module clears the stored values.

### Sandbox

- Libraries: `base`, `string`, `table`, `math` (Lua 5.4). `io`, `os`, `debug`, `coroutine`, `utf8` and `package` are not available.
- Removed base functions: `load`, `loadfile`, `dofile`, `module`, `collectgarbage`.
- Use `Log()` for output. `print()` does not write to the log window.

---

## 3. Lifecycle

A module defines up to six callbacks. All are optional. CONTROL looks them up after the entry file's top-level code has run, so they must be globals: `function Update()`, not `local function Update()`.

| Callback | When | Use for |
|----------|------|---------|
| `Load(playerId)` | When the slot becomes active: slot on, **Enabled** on, assigned player present (outside a match every player counts as present). Can run in the main menu. `playerId` is `1` to `8`. | `Settings.Add*`, setup that needs only the player id. |
| `Init()` | Once per match, when the match runs and game time is above 0. Again for the next match, and at once when the module is reloaded during a match. | Per-match setup: reset caches, read the map, `IPC.StartServer()`, create strategic components. |
| `Update()` | Every **Update Interval** seconds of game time (default `1.0`) while a match runs. Not while paused. Missed intervals are dropped, not run back to back. | Decisions and game commands. |
| `Render()` | Every frame while a match runs, after `Init()`. Not called with **Multithreading** on. | Drawing with `Render*` functions. |
| `End(hasWon)` | Once per match: when the match or replay ends, or the player leaves a running match. Not when the tech tree, options, hotkeys, save or load screen opens. `hasWon` is `false` when the player leaves and at the end of a replay. | Final reads. Game commands do nothing here. |
| `Unload()` | When the module stops in its slot: slot or **Enabled** turned off, assigned player not in the match, another module selected, the module reloaded or removed, CONTROL unloaded. Can run without `End()`. | Cleanup: `IPC.StopServer()`. |

Rules:

- The top-level code runs when the module is loaded (selected, CONTROL started with it assigned, or reloaded). Top-level `require` calls run then too.
- Most game functions need a running match. Outside one (for example in `Load()` in the main menu) they log `Game API function called before the game started` and return `nil` or a default value.
- **Time limit:** a callback, including top-level code, that runs longer than 1 second is stopped with the error `callback ran longer than the 1000 ms limit`. Time inside a single CONTROL function call is not interrupted. The game waits for callbacks that run on its thread, so keep `Update()` and `Render()` short.
- **Errors:** a Lua error in top-level code, `Load()`, `Update()` or `Render()` is shown in the slot's section of the CONTROL menu and logged. After an error in `Update()` or `Render()`, CONTROL stops calling that function until the next match or a reload. `pcall` catches the time-limit error, but the next 10,000 Lua instructions raise it again, so return right after catching it.
- **Commands outside Update():** game commands belong in `Update()`. From another callback they still run and log a warning once per module load; with **Tournament Mode** on they are refused. During a replay they are refused.
- **Drawing:** `Render*` functions raise an error outside `Render()`.

---

## 4. Modes

### Tournament Mode (menu setting, off by default)

- Game commands outside `Update()` are refused.
- The part of an `Update()` call above 20 ms is added to the next update interval.
- These functions do nothing and return `nil`, `false` or an empty value; CONTROL logs `Tournament Mode blocked ...` once per function and module load:

| Group | Functions |
|-------|-----------|
| Engine | `Log`, `SendChatMessage`, `SetCameraPosition`, `SetEngineUIVisibility`, `UnloadEngine`, `AssignAndLoadModule` |
| Menu and session | `DispatchStartGame`, `DispatchRestartGame`, `DispatchResignGame`, `DispatchQuitGame`, `DispatchLoadGame`, `GetAvailableSaveFiles`, `IsGamePaused`, `IsMenuOpen` |
| Random maps | `GetRandomMapControlCapabilities`, `GetRandomMapStartStatus`, `RefreshRandomMapSources`, `GetAvailableRandomMapSources`, `GetEffectiveRandomMapSeed`, `GetEffectiveRandomMapSource` |
| Replays and speed | `SetGamePaused`, `SetReplaySpeed`, `GetCurrentReplayFileName`, `SetGameSpeedMultiplier` |
| Rendering | All `Render*` functions (`Render()` still runs, but draws nothing), `GetScreenSize`, `IsOnScreen`, `IsWorldPosOnScreen`, `WorldToScreen`, `WorldToMinimap`, `GetZoom`, `GetCameraPosition` |
| Game options | Every `GameOptions` method except `SetAssignedPlayerCivilization` |

`GetAssignedPlayerId` and `GetCurrentGameOptions` stay available.

### Multithreading (menu setting, off by default)

- Each module runs on its own thread. Game commands are queued and run on the game's thread.
- `Render()` is not called.
- These functions do nothing and return `nil`, `false` or an empty value; CONTROL logs `Multithreading blocked ...` once per function and module load: `DispatchStartGame`, `DispatchRestartGame`, `DispatchResignGame`, `DispatchQuitGame`, `DispatchLoadGame`, `RefreshRandomMapSources`, `SetGamePaused`, `SetReplaySpeed`, `SetGameSpeedMultiplier`, `IPC.WaitForMessage`, `GetScreenSize`, `IsOnScreen`, `IsWorldPosOnScreen`, `WorldToScreen`, `WorldToMinimap`, `GetZoom`, `GetCameraPosition`.
- `ResourceTracker`, `VillagerOccupation` and `ConstructionPlacement` need the live game; their `Update()` raises an error.

### Game-state copy (Multithreading on, or the Agent Bridge on)

With **Multithreading** on, and while the Agent Bridge is on for any player, every module reads a copy of the game state taken once per frame instead of the live game. Tournament Mode does not do this.

- `GetFact`, `Player:GetFact`, `Object:GetObjectData` return `nil` for values the copy does not hold.
- `GetObjectTypeData` returns `nil` for every field except `ObjectData.TRAIN_SITE` and `ObjectData.CLASS`.
- `GetObjectTypeAttribute` and `Object:GetAttribute` return `nil` except for `RADIUS_X`, `RADIUS_Y` (and `HITPOINTS` for objects) and these combat values, with the owner's upgrades: `SHOWN_ATTACK`, `SHOWN_MELEE_ARMOR`, `WEAPON_RANGE`, `MINIMUM_WEAPON_RANGE`, `SPEED_OF_ATTACK`, `HIT_CHANCE`, `SPEED`, `LINE_OF_SIGHT`, and `WEAPON` and `ARMOR` per class (`nil` for a class the type has no value for). Types: those the assigned player has, has queued or can make now. Objects: the player's own, and other players' objects it can see.
- `CheckPlacement()`, `CanPlaceObject()` and `MapTile:IsBuildable()` return `nil`.
- `GetObjectsInArea` compares the object's exact position, not its tile.
- Commands go into a queue that the game runs on its next frame. `true` means queued, not succeeded. A command refused at the call returns `false` and logs `<Call> was not sent: <reason>`. A queued command can still fail when it runs.

---

## 5. Engine, Menu and Replay Functions

### Engine (work at any time, in menus and in a match)

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `Log` | `(message)` | `nil` | Writes `[MODULE] <message>` to the CONTROL log window. |
| `SetEngineUIVisibility` | `(visible)` | `nil` | Opens (`true`) or closes (`false`) the CONTROL menu. |
| `UnloadEngine` | `()` | `nil` | Unloads CONTROL from the game. While `DispatchQuitGame()` or a start is in progress, waits until it has finished, for up to 30 seconds. |
| `GetAssignedPlayerId` | `()` | `number` | Player id (`1` to `8`) of this module instance. Works in `Load()`. |
| `AssignAndLoadModule` | `(playerId, moduleName)` | `boolean` | Assigns a module (name without `.main.lua`/`.main.module`) to player `1` to `8` and loads it, or reloads it. Applied after the current callback returns. `false` for a bad player id or malformed name; an unknown name is ignored. |

### Menu and session

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `IsGamePaused` | `()` | `boolean` | Whether the match or replay is paused. Needs a match, replay or end screen; elsewhere logs an error and returns `false`. |
| `IsMenuOpen` | `()` | `boolean` | Whether the game's in-match menu is open. Same requirement as `IsGamePaused`. |
| `DispatchStartGame` | `()` | `boolean` | In the menu: starts a single-player match with the current `GameOptions`. On a single-player end screen: starts a new match with the same options, map source and seed request. On a replay end screen: plays the replay again. `false` while a match runs, in multiplayer, when `GetCurrentGameOptions()` is `nil`, or while a start or quit is in progress. |
| `DispatchRestartGame` | `()` | `boolean` | Restarts the current single-player match or replay. `false` in multiplayer or without a running match, ended match or replay end screen. |
| `DispatchResignGame` | `()` | `boolean` | Resigns the current match. `false` without a running match or during a replay. |
| `DispatchQuitGame` | `()` | `boolean` | Leaves the current single-player match and returns to the menu. `false` in the menu, in multiplayer, in a replay, or while a start or quit is in progress. |
| `DispatchLoadGame` | `(saveGameFileName)` | `boolean` | Loads a save or replay by a file name exactly as `GetAvailableSaveFiles()` returns it (not a path). `.aoe2record` files load as replays. `false` while a match runs, in multiplayer, or for an unknown name. |
| `GetAvailableSaveFiles` | `()` | `string[]` | File names in the game's load list, such as `"MyGame.aoe2spg"` or `"MyMatch.aoe2record"`. |
| `GetCurrentGameOptions` | `()` | `GameOptions` or `nil` | Setup of the next or current match. See [GameOptions](#gameoptions). |

`true` from a `Dispatch*` function means the game accepted the request. The change happens over the next frames; check the result in a later callback.

### Pause, speed and replays

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `SetGamePaused` | `(paused)` | `nil` | Pauses or resumes the match or replay. Needs a match, replay or end screen. |
| `SetGameSpeedMultiplier` | `(multiplier)` | `boolean` | Changes the global **Game Speed Multiplier** setting. Works at any time and stays in effect for later matches and replays. `multiplier <= 0` restores the game's default speed. Returns `true`. |
| `SetReplaySpeed` | `(speed)` | `nil` | Sets replay playback speed to a `ReplaySpeed` value. Does nothing outside a replay. |
| `GetCurrentReplayFileName` | `()` | `string` | File name of the running replay, or `""`. |

### Random map control

Select a map and seed with the `GameOptions` methods `SetRandomMapSource()` and `SetRandomMapSeed()`.

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `GetRandomMapControlCapabilities` | `()` | `table` | Flags for the random-map features that work on this game build (fields below). |
| `GetRandomMapStartStatus` | `()` | `table` | Whether a new match can start now, and why not (fields below). |
| `RefreshRandomMapSources` | `()` | `number` or `nil` | Makes the game reload its random-map list; returns the new catalog generation. Works only in the menus or on an end screen; `nil` while a match runs, with a replay loaded, in multiplayer, during a start or quit, or when the list cannot be read. |
| `GetAvailableRandomMapSources` | `()` | `RandomMapSource[]` | Random maps in the current list. |
| `GetEffectiveRandomMapSeed` | `()` | `number` or `nil` | Map seed of the running match. `nil` without a running match, including on the end screen. |
| `GetEffectiveRandomMapSource` | `()` | `RandomMapSource` or `nil` | Random map of the running match. `nil` without a running match. |

`GetRandomMapControlCapabilities()` fields: `apiVersion` (`1`), `capabilityRevision` (`2`), `freshStart`, `requestedSeed`, `effectiveSeed`, `sourceCatalog`, `refresh`, `selection`, `effectiveSource`, `sourceIdentity`, `authoredSourceHash`, `explicitQuitThenStart`, `structuredStartStatus` (always `true`), `directPath`, `inlineSource`, `atomicFreshStart` (always `false`). A flag is `false` when the feature does not work on this game build. Lua cannot load a map script from a path or a string.

`GetRandomMapStartStatus()` fields: `canStart`, `code`, `optionsAvailable`, `sessionActive`, `multiplayer`, `replay`, `lifecycleTransitionPending`, `cleanStartContract` (`"explicit-quit-then-start"`), `capabilityRevision` (`2`). `code` is `"ready"`, `"fresh_start_capability_unavailable"`, `"game_options_unavailable"`, `"multiplayer_not_supported"`, `"replay_not_supported"`, `"explicit_clean_end_required"` (a match runs: call `DispatchQuitGame()` first), `"lifecycle_transition_pending"`, `"rms_session_safety_unknown"` or `"rms_session_safety_changing"` (try again later).

---

## 6. Commands

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `SetCameraPosition` | `(position)` | `nil` | Moves the camera to a `Vector2` world position. |
| `SendChatMessage` | `(message)` | `nil` | Sends a chat message in the assigned player's color. |
| `TrainUnit` | `(unitId)` | `boolean` | Trains one unit of type `unitId`. Trains nothing while one unit of that type is in production. |
| `TrainUnit` | `(unitId, amount)` | `boolean` | Makes sure `amount` units of type `unitId` are in production: trains the difference, or returns `false` if enough are in production. |
| `TrainUnit` | `(trainSources, unitId)` | `boolean` | Like `TrainUnit(unitId)`, using only buildings whose `UnitObjectType` is in the array `trainSources`. |
| `TrainUnit` | `(trainSources, unitId, amount)` | `boolean` | Like `TrainUnit(unitId, amount)`, using only buildings of the types in `trainSources`. |
| `UnitsTargetObject` | `(units, target, queue?)` | `boolean` | Orders units to act on an object: attack, gather, repair and so on. `queue` `true` adds the order after their current orders, as shift-right-click does. |
| `UnitsBuildStructure` | `(builders, structureId, position)` | `boolean` | Orders builders to build a structure at a `Vector3` position. `false` when the player cannot afford it. Does not check the position; use `CheckPlacement` first. |
| `UnitsMove` | `(units, position, queue?)` | `boolean` | Orders units to move to a `Vector3` position. `queue` `true` adds a waypoint, as shift-click does. |
| `EnableScouting` | `()` | `boolean` | Sets idle Scout Cavalry, Camel Scouts and Eagle Scouts that are not scouting to auto-scout. `false` when there are none. |
| `ResearchTechnology` | `(technology)` | `boolean` | Researches a `Technology` at a building that can research it. `false` when it cannot be researched now or the player cannot afford it. |
| `DeleteUnit` | `(unit)` | `nil` | Deletes one of the player's units. |
| `DestroyBuilding` | `(building)` | `nil` | Deletes one of the player's buildings. |
| `SetGatherPoint` | `(buildings, targetPosition)` | `nil` | Sets the gather point of buildings to a `Vector3` position. |
| `RingTownBell` | `(building, isCallingIn)` | `nil` | `true` rings the town bell so villagers take shelter; `false` sends them back out. |
| `SendBackToWork` | `(building)` | `nil` | Sends the villagers garrisoned in the building back to work. |
| `SendAllBackToWork` | `(building)` | `nil` | Sends the villagers garrisoned in all of the player's buildings back to work; `building` is any of the player's buildings. |
| `SetUnitStanceAutoScout` | `(units)` | `nil` | Sets units to auto-scout. |
| `SetUnitStancePatrol` | `(units, targetPosition)` | `nil` | Orders units to patrol to a `Vector3` position. |
| `SetUnitStanceGuard` | `(units, targetObject)` | `nil` | Orders units to guard an object. |
| `SetUnitStanceFollow` | `(units, targetObject)` | `nil` | Orders units to follow an object. Villagers may stop following once the target moves. |
| `SetUnitStanceAttackMove` | `(units, targetPosition)` | `nil` | Orders units to attack-move to a `Vector3` position. |
| `SetUnitStanceGarrison` | `(units, targetObject)` | `nil` | Orders units to garrison in an object. |
| `SetUnitStanceUngarrison` | `(sourceObjects, unit)` | `nil` | Ungarrisons a unit from the player's buildings in `sourceObjects`. |
| `SetUnitStanceSeekShelter` | `(units)` | `nil` | Orders units to seek shelter. |
| `SetUnitCombatStance` | `(units, stance)` | `nil` | Sets a `UnitCombatStance`. |
| `SetFormation` | `(units, formation)` | `nil` | Sets a `Formation`, as the game's formation buttons do. Takes effect when the units next move together. `ObjectData.FORMATION_ID` does not report it. |
| `QueueFarmReseeds` | `(amount)` | `boolean` | Adds farms to the reseed queue, as the Mill button does. The game takes the cost now and adds as many of `amount` (1–40) as the player can afford; the queue holds 40. Civilizations with pastures queue pastures. `false` when farms are unavailable, the queue is full or the player cannot afford one. |
| `CancelFarmReseeds` | `(amount)` | `boolean` | Removes up to `amount` farms from the queue and refunds them. |
| `SetAutoFarmReseed` | `(enabled)` | `boolean` | Automatic farm reseeding on or off: the game queues a farm when one expires and the player can pay. Sends nothing when already set or a change is on its way. |
| `QueueFishTrapRebuilds` | `(amount)` | `boolean` | Same as `QueueFarmReseeds` for fish traps (Dock button). |
| `CancelFishTrapRebuilds` | `(amount)` | `boolean` | Same as `CancelFarmReseeds` for fish traps. |
| `SetAutoFishTrapRebuild` | `(enabled)` | `boolean` | Same as `SetAutoFarmReseed` for fish traps. |
| `SetGoal` | `(goalId, value)` | `boolean` | Sets goal `goalId` (1–16384) of the player's AI script (`.per`) to a 32-bit whole number. The player must be a computer player whose script runs: turn Suppress Native AI off. `false` for an id out of range, a human player, a replay or multiplayer. Not a game command; not in the recorded game; does not count toward Sequential Actions. |
| `SetGoals` | `(firstGoalId, values)` | `boolean` | Sets the goals from `firstGoalId` on to the list `values`. The script sees all of them in its next rule pass, never part of them. |

Command rules:

- Commands need a running match. Before it starts or after it ends they log an error and return `false` or nothing. In a replay they are refused.
- Call commands from `Update()` (see [Lifecycle](#3-lifecycle)).
- Commands act for the assigned player. Units and buildings this player does not own are dropped from the list; if none are left, the command does nothing and returns `false`.
- **Sequential Actions** (on by default) allows one command per `Update()`. Further commands in that update do nothing and return `false`.
- **Auto Move Camera** (on by default) moves the camera to each command's target. Only one module moves the camera: the one on the local player's slot, or else the one with the lowest player id.
- Object lists are Lua arrays of `Object` values with no gaps, such as `{ villager1, villager2 }`. Anything else raises a Lua error.
- A `nil` return gives no result; read the game state on a later update to see whether the command worked.
- `TrainUnit` uses the building type of which the player owns the most, and returns `false` when the player cannot afford the unit or owns no such building.
- `SetCameraPosition` and `SendChatMessage` also work in replays and on end screens, from any callback, and do not count toward **Sequential Actions**.

---

## 7. Facts and Queries

These functions read the match and do not change it. Call them from `Init`, `Update`, `Render` or `End`. Outside a match or its end screen they log an error and return `nil`, `0`, `false` or an empty list.

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `GetFact` | `(fact, parameter?)` | `number` or `nil` | `Fact` value for the assigned player. `parameter` (default `0`) is the fact's argument, such as a unit type id for `Fact.UNIT_TYPE_COUNT`. |
| `IsObjectTypeAvailable` | `(unitObjectType)` | `boolean` | Whether the assigned player can currently get a unit or building type. |
| `GetUnitTypeCount` | `(unitId)` | `number` | How many objects of a unit type the assigned player has. |
| `GetAttribute` | `(attribute)` | `number` | `PlayerAttribute` value for the assigned player. |
| `CanAfford` | `(unitId, isBuilding?)` | `boolean` | Whether the assigned player has the resources and free population for one unit of the type. `isBuilding = true` skips the population check. Does not check availability. |
| `GetTechCost` | `(technology)` | cost list | Current cost of a technology for the assigned player. |
| `GetObjectCost` | `(unitObjectType, costMultiplier?)` | cost list | Current cost of a unit or building type, times `costMultiplier` (default `1.0`). |
| `CanResearch` | `(technology)` | `boolean` | Whether the technology is available and affordable now. |
| `IsTechnologyResearched` | `(technology)` | `boolean` | Whether the assigned player has researched it. |
| `GetObjectsByType` | `(unitType)` | `Object[]` | Objects of a `UnitObjectType`, any owner. |
| `GetObjectsByTypes` | `(unitTypes)` | `Object[]` | Objects of any type in an array of `UnitObjectType`. |
| `GetObjectsByClass` | `(unitClass)` | `Object[]` | Objects of a `UnitClass`, any owner. |
| `GetObjectsByClasses` | `(unitClasses)` | `Object[]` | Objects of any class in an array of `UnitClass`. |
| `GetObjectsInArea` | `(pos1, pos2)` | `Object[]` | Objects whose tile lies in the rectangle with `Vector2` corners `pos1` and `pos2`, edges included. |
| `GetObjectsInArea` | `(pos1, pos2, unitClass, owner)` | `Object[]` | Same, filtered by `unitClass` and owner player id. Pass `nil` to skip a filter. |
| `GetObjectById` | `(id)` | `Object` or `nil` | Object by id; `nil` if it does not exist or the module cannot see it. |
| `GetObjectStates` | `(objects)` | `table` | Reads several objects in one call (see below). |
| `GetObjectChanges` | `()` | `{ created, destroyed, damaged }` | Ids of the assigned player's objects that appeared, disappeared or lost hitpoints since the last call (see below). |
| `GetProjectileById` | `(id)` | `Object` or `nil` | Projectile by id, if the module can see it. |
| `GetAllProjectiles` | `()` | `Object[]` | Projectiles the module can see. |
| `GetProjectilesByType` | `(projectileType)` | `Object[]` | Visible projectiles of a `ProjectileType`. |
| `GetObjectTypeData` | `(objectTypeId, objectData)` | `number` or `nil` | `ObjectData` value of a unit type with the assigned player's technologies and bonuses. |
| `GetObjectTypeAttribute` | `(objectTypeId, objectAttribute, damageType)` | `number` or `nil` | `ObjectAttribute` value of a unit type for the assigned player. `damageType` is the armor or attack class for `ARMOR` and `WEAPON`; pass `0` otherwise. |
| `CheckPlacement` | `(objectTypeId, position)` | `PlacementResult, number?` | The game's placement check for the assigned player, centred on a `Vector2` or `Vector3`. Second value: the blocking object's id for `PlacementResult.BLOCKED`, only for an object the player knows (see below). Unexplored ground gives `UNEXPLORED` unless **Modules See Everything** is on. `nil` for an unknown type. |
| `CanPlaceObject` | `(objectTypeId, position)` | `boolean` or `nil` | `true` when `CheckPlacement` returns `CAN_PLACE`; `nil` when it returns `nil`. |
| `CalculatePath` | `(startPos, targetPos, collisionRadius?)` | `Vector3[]` | Path between two `Vector3` positions for a unit of `collisionRadius` (default `0`). Empty when there is no path. The game's path with **Modules See Everything** on; otherwise a tile search over what the player knows (see below). |
| `GetGameTime` | `()` | `number` | Match time in seconds. |
| `GetClockMs` | `()` | `number` | Milliseconds on a monotonic clock with an arbitrary start. Subtract two values for a duration. |
| `GetModuleTelemetry` | `()` | `table` or `nil` | This module instance's timing (see below). `nil` outside a module callback. |
| `GetAllChatMessages` | `()` | `string[]` | The newest 50 chat messages, oldest first, game notices included. |
| `GetNewChatMessages` | `()` | `string[]` | Messages that appeared since this module instance last called it. The first call after a load returns the whole buffer. |
| `GetLastChatMessage` | `()` | `string` or `nil` | Newest chat message, or `nil`. |
| `GetAssignedPlayer` | `()` | `Player` | This module instance's player. |
| `GetPlayerById` | `(id)` | `Player` or `nil` | `0` is Gaia, `1` to `8` are players. `nil` for no such player. |
| `GetPlayerCount` | `()` | `number` | Player slots including Gaia. Ids run from `0` to `GetPlayerCount() - 1`. |
| `IsEnemyPlayer` | `(player)` | `boolean` | Whether a `Player` is an enemy of the assigned player. |
| `GetMapWidth` | `()` | `number` | Map width in tiles. |
| `GetMapHeight` | `()` | `number` | Map height in tiles. |
| `GetMapTile` | `(x, y)` | `MapTile` or `nil` | Tile at integer coordinates; `nil` outside the map. |
| `GetMapTile` | `(position)` | `MapTile` or `nil` | Tile at a `Vector2`, rounded down. |
| `GetAllMapTiles` | `()` | `MapTile[]` | Every tile of the map. |
| `GetMapTilesPtr` | `()` | `number, number` | `(pointer, count)` of a packed tile buffer for external readers (see [IPC](#9-ipc)). |
| `GetObjectsPtr` | `()` | `number, number` | `(pointer, count)` of a packed object buffer for external readers. |
| `GetVictoryCondition` | `()` | `VictoryCondition` | The match's victory condition. |
| `GetVictoryPlayer` | `()` | `Player` or `nil` | The winner after the match ends, otherwise `nil`. |

A cost list is an array of `{ resourceId = ResourceType, amount = number }`.

**Object lists.** `GetObjectsByType(s)`, `GetObjectsByClass(es)` and `GetObjectsInArea` return objects of every owner: living units, buildings, resources, and dead animals that still carry food. Unfinished buildings (foundations) are left out; `Player:GetFoundations()` returns them.

**Visibility.** With **Modules See Everything** off (the default), object lookups (including `GetObjectById` and the projectile functions) return what the assigned player can see, plus animals and resources on explored tiles outside its vision. `MapTile` methods and other players' resources, facts and technologies are limited the same way.

**Placement and paths in the fog.** With **Modules See Everything** off, an explored tile out of sight counts as blocked only under an object the player knows there (gaia objects such as trees, mines and cliffs); buildings and walls out of sight do not count, also ones seen before. `MapTile:IsWalkable()` follows this. `CheckPlacement` names a blocking object only when the player knows it (it sees it, or it is a resource, animal or other gaia object on an explored tile); for another object the result is `BLOCKED` without an id when the footprint covers a tile the player knows is blocked, otherwise the result without the object check. `MapTile:IsBuildable()` follows this. `CalculatePath` and `Object:CalculatePath` search the explored walkable tiles from tile centre to tile centre instead of asking the game's pathfinder.

**Keeping references.** An `Object`, `Player` or `MapTile` is valid during the callback that returned it. Keep ids or positions across callbacks and look objects up again.

**GetObjectStates(objects)** takes an array of `Object` values and returns a table of arrays `id`, `unitType`, `class`, `playerId`, `x`, `y`, `hitpoints`, `targetId`, `alive`; index `i` of every array is the same object. `playerId` is `-1` without an owner; `targetId` is `-1` without a visible target. `nil` entries, objects that no longer exist and objects the module cannot see are skipped, so match results by `id`. A value that is not an `Object` raises an error.

**GetObjectChanges()** compares the assigned player's objects (as `GetAssignedPlayer():GetPlayerObjects()` returns them) with the previous call from this module instance. `created`, `destroyed` and `damaged` (lower hitpoints) are integer id arrays sorted by id. The first call after a load reports every object as created. When the player cannot be read, it returns empty lists and keeps the previous state.

**GetModuleTelemetry()** returns `update` and `render` tables (`count`, `totalMs` over all calls; `sampleCount`, `averageMs`, `p50Ms`, `p95Ms`, `maxMs` over the last 256 calls), `lateUpdates`, `skippedIntervals`, `deferredUpdates` (Multithreading only) and `api`. `api` lists `{ name, count, totalMs }` per called function, highest `totalMs` first, when the DEBUG setting **API Profiling** was on when the module loaded; otherwise it is empty.

---

## 8. Render API

- Draw functions (`Render*`) work only inside `Render()`. Elsewhere they raise an error such as `RenderLine can only be called from Render() (called during Update).`
- Query functions (`GetScreenSize`, `IsOnScreen`, `IsWorldPosOnScreen`, `WorldToScreen`, `WorldToMinimap`, `GetZoom`, `GetCameraPosition`) work in any callback.
- With **Multithreading** or **Tournament Mode** on the Render API does not work: queries return zero values (`false` for `IsOnScreen` and `IsWorldPosOnScreen`); with Multithreading `Render()` is not called, with Tournament Mode draw calls draw nothing.
- Colors are `Color(r, g, b, a)` with components `0` to `255` (see [Color](#color)).
- Screen positions are `Vector2` pixels, `(0, 0)` at the top-left of the game window. World positions are `Vector3` in tiles, as `Object:GetPosition()` returns; `z` is height.

### Screen space

| Function | Signature | Description |
|----------|-----------|-------------|
| `GetScreenSize` | `()` | Game window size in pixels as `Vector2`. |
| `IsOnScreen` | `(position)` | Whether a `Vector2` screen position is inside the window. |
| `RenderText` | `(text, position, size, color, center, border?)` | Text of `size` pixels, centered vertically on `position.y`; `center = true` also centers it horizontally. `border` (default `true`) adds a black outline. |
| `RenderLine` | `(from, to, color, thickness)` | Line. |
| `RenderCircle` | `(position, radius, color, thickness, segments?)` | Circle outline. Without `segments`, or with `0`, the count follows the radius. |
| `RenderCircleFilled` | `(position, radius, color, segments?)` | Filled circle. |
| `RenderRect` | `(from, to, color, rounding, roundingCornersFlags, thickness)` | Rectangle outline between two corners. `rounding` is the corner radius in pixels. |
| `RenderRectFilled` | `(from, to, color, rounding, roundingCornersFlags)` | Filled rectangle. |

`roundingCornersFlags`: `0` for all corners, or the sum of `1` (top-left), `2` (top-right), `4` (bottom-left), `8` (bottom-right).

### World and minimap

| Function | Signature | Description |
|----------|-----------|-------------|
| `IsWorldPosOnScreen` | `(worldPos)` | Whether a world position is inside the window. |
| `WorldToScreen` | `(worldPos)` | World position to screen position. |
| `WorldToMinimap` | `(worldPos)` | World position to the screen position of that point on the minimap. Ignores `z`. |
| `GetZoom` | `()` | Camera zoom factor. |
| `GetCameraPosition` | `()` | World position at the screen center as `Vector2` (tiles). |
| `RenderWorldLine` | `(from, to, color, thickness?)` | Line between world positions. `thickness` defaults to `2`. Skipped when both ends are off screen. |
| `RenderWorldRect` | `(worldPos, width, height, color, thickness?)` | Rectangle outline on the ground centered on `worldPos`; `width`, `height` in tiles. `thickness` defaults to `2`. |
| `RenderWorldRectFilled` | `(worldPos, width, height, color)` | Filled ground rectangle. |
| `RenderWorldCircle` | `(worldPos, radius, color, thickness?, segments?)` | Circle outline, round on screen; `radius` in tile widths. Defaults: `thickness` `2`, `segments` `16`. Skipped when the center is off screen. |
| `RenderWorldCircleFilled` | `(worldPos, radius, color, segments?)` | Filled circle. `segments` defaults to `16`. |
| `RenderWorldText` | `(text, worldPos, size, color, center?, border?)` | Text at a world position. `center` and `border` default to `true`. |
| `RenderObjectBounds` | `(object, color, thickness?)` | Outline of the object's footprint (`2 × RADIUS_X` by `2 × RADIUS_Y` tiles). `thickness` defaults to `2`. |
| `RenderObjectBoundsFilled` | `(object, color)` | Filled footprint. |
| `RenderMinimapDot` | `(worldPos, radius, color)` | Dot on the minimap; `radius` in pixels. |
| `RenderMinimapLine` | `(worldPosFrom, worldPosTo, thickness, color)` | Line on the minimap. `thickness` comes before `color`. |
| `RenderMinimapRect` | `(worldPos, width, height, color, thickness)` | Rectangle outline on the minimap, centered on `worldPos`, size in tiles. |
| `RenderMinimapRectFilled` | `(worldPos, width, height, color)` | Filled rectangle on the minimap. |

World line thickness, `RenderWorldCircle` radii and `RenderWorldText` sizes scale with zoom. Minimap sizes do not.

---

## 9. Settings API

Each module instance has its own settings group, shown as a submenu such as `MYBOT [P2]` (or `MYBOT [S1]` for a shared **Sync** profile). Values are saved in `settings.ini`.

Call the `Add` functions in `Load(playerId)`. The menu shows settings in the order they are added. Adding an existing key keeps its saved value.

| Function | Signature | Description |
|----------|-----------|-------------|
| `Settings.AddBool` | `(key, defaultValue)` | Checkbox. |
| `Settings.AddInt` | `(key, defaultValue, minValue, maxValue)` | Integer slider. |
| `Settings.AddFloat` | `(key, defaultValue, minValue, maxValue)` | Float slider. |
| `Settings.AddDropdown` | `(key, defaultValue, options)` | Dropdown. `options` is an array of strings with no gaps; `defaultValue` is one of them. |
| `Settings.AddKeybind` | `(key, defaultVkCode)` | Keybind picker; the value is a virtual-key code such as `Key.Tab`. |
| `Settings.AddTooltip` | `(key, tooltip)` | Tooltip for setting `key`. |
| `Settings.AddColor` | `(key, defaultColor)` | Color picker. `defaultColor` must be a `Color`. |

Get functions work in any callback and return `defaultValue` when the key does not exist. Without `defaultValue` the fallback is `false`, `0`, `0.0`, `""`, `0` or `Color(0, 0, 0, 0)`.

| Function | Signature | Returns |
|----------|-----------|---------|
| `Settings.GetBool` | `(key, defaultValue?)` | `boolean` |
| `Settings.GetInt` | `(key, defaultValue?)` | `number` |
| `Settings.GetFloat` | `(key, defaultValue?)` | `number` |
| `Settings.GetString` | `(key, defaultValue?)` | `string` (selected dropdown option) |
| `Settings.GetKeybind` | `(key, defaultVkCode?)` | `number` |
| `Settings.GetColor` | `(key, defaultColor?)` | `Color` |
| `IsKeyPressed` | `(vkCode)` | `boolean`: key held down now; `false` while the game window is not focused. |

`Update()` runs once per update interval and can miss a short key press; check keys in `Render()`.

---

## 10. IPC

A module runs a Windows named-pipe server; external programs on the same PC, running as the same Windows user, connect as clients.

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `IPC.StartServer` | `(pipeName)` | `boolean` | Opens `\\.\pipe\<pipeName>` for this module instance. `false` for an empty name, more than 200 characters, `\` or `/`, or a name starting with `AoE2Control` (any case). Calling it again with the same name keeps the server; another name stops the old server first. |
| `IPC.StopServer` | `()` | `nil` | Stops the server and drops unread messages. Done for you when the module unloads. |
| `IPC.Send` | `(message)` | `boolean` | Sends to every connected client, wrapped in an envelope. A string is sent as is; other values are converted with `ToJSON`. `false` for `nil`, without a connected client, or when a limit is reached. |
| `IPC.HasMessages` | `()` | `boolean` | Whether messages are waiting. |
| `IPC.GetMessages` | `()` | `string[]` | All waiting messages, oldest first; removes them. |
| `IPC.WaitForMessage` | `(timeoutMs?)` | `string` or `nil` | Oldest waiting message, or waits up to `timeoutMs` (default and maximum `500`) for one. `nil` on timeout, without a server, and with **Multithreading** on. The wait counts toward the 1-second callback limit. |
| `IPC.GetStats` | `()` | `table` or `nil` | Counters: `connectedClients`, `sentMessages`, `sentBytes`, `sendFailedNoClient`, `sendFailedTooLarge`, `sendFailedQueueFull`, `receivedMessages`, `receiveDroppedQueueFull`, `receiveDroppedTooLarge`, `receiveDroppedInvalidRouting`, `slowClientDisconnects`. `nil` without a server. |
| `ParseJSON` | `(str)` | `any` | JSON to Lua values (`null` becomes `nil`). `nil` for invalid JSON. |
| `ToJSON` | `(value)` | `string` | Lua value to JSON. A non-empty table with keys `1` to `n` becomes an array; other tables, including empty ones, become objects. `"{}"` when the value cannot be converted. |

- Several module instances can share a pipe name. An incoming message goes to every instance on the pipe unless it names a target with `instanceId`/`assignedInstanceId`, `assignedPlayerId`/`playerId`, `moduleName` or `settingsGroup`, in a root `target` object or at the root. A message reaches the instances that match every field it sets.
- If the incoming root object has a `payload` field, Lua receives only the payload (a string as is, anything else as JSON text). Otherwise Lua receives the object as JSON text without the routing fields. Non-JSON text is delivered unchanged to every instance.
- `IPC.Send` wraps the message as `{"type": "module_message", "pipeName": ..., "source": {"instanceId", "assignedPlayerId", "moduleName", "settingsGroup"}, "payload": ...}`, followed by `\n`. A string that is valid JSON is sent as parsed JSON in `payload`.
- Limits: outbound envelope 1 MiB; 1024 messages or 8 MiB waiting per client; a client that does not read a message within 5 s is disconnected; inbound message 1 MiB (larger ones are dropped and the client disconnected); 1024 inbound messages waiting per module instance; 16 clients per pipe.

**Packed buffers.** `GetMapTilesPtr()` and `GetObjectsPtr()` return `(ptr, count)`: an address in the game process and an entry count (not bytes), or `0, 0`. A client reads them with `ReadProcessMemory`. Each call builds a new buffer; buffers stay valid until the module instance requests a buffer in a later callback, or unloads. Unexplored tiles are left out. `GetObjectsPtr()` includes dead objects. Entries are packed, little-endian:

| Tile (8 bytes) | Object (12 bytes) |
|----------------|-------------------|
| `uint16 x`, `uint16 y`, `uint8 terrain`, `uint8 elevation`, `uint8 isVisible` (1 visible, 0 fogged), `uint8 flags` (bit 0 walkable, bit 1 passable terrain for land units) | `uint32 id`, `uint16 unitObjectType`, `uint16 x`, `uint16 y` (tile), `uint8 playerId` (`255` without owner), `uint8 flags` (bit 0 alive) |

### Agent Bridge

The Agent Bridge lets an external program play a player over `\\.\pipe\AoE2ControlAgentV3.P<n>`, without a Lua module. CONTROL hosts it, so it has no Lua functions. It is off by default: turn on **Modules > Agent Bridge** and the players below it (`Agent Bridge=true` and `Agent Bridge Player <n>=true` under `[modules]` in `settings.ini`). It does not work in multiplayer, even with cheats. While it is on for any player, all modules read the [game-state copy](#game-state-copy-multithreading-on-or-the-agent-bridge-on).

The pipe protocol (`open`, `observe`, `map`, `act`, `query`) is described at https://aoe2control.github.io/agent-bridge/.

---

## 11. Types

### Vector2, Vector3, Vector4

- Constructors: `Vector3.new()` (zero), `Vector3.new(x, y, z)`, `Vector3.new(value)` (all components). `Vector3(...)` is the same as `Vector3.new(...)`. Same for `Vector2(x, y)` and `Vector4(x, y, z, w)`.
- Fields: `x`, `y` (`z`, `w`).
- Methods: `LengthSqr()`, `Length()`, `Normalize()` (in place; vectors shorter than `0.00001` are left unchanged), `Normalized()`, `Dot(other)`, `IsNearlyZero()` (every component closer to `0` than `0.000001`), `Lerp(target, alpha)`, `Distance(other)`.
- `Cross(other)`: `Vector2` returns a number (`x * other.y - y * other.x`); `Vector3` returns a `Vector3`. `Vector4` has no `Cross`.
- Static calls work: `Vector3.Dot(a, b)`, `Vector3.Cross(a, b)`.
- Operators: `Vector2`: `+`, `-` with a `Vector2`; `*` with a `Vector2` or a number on either side; `/` with a `Vector2` or a number on the right. `Vector3`: `+`, `-`, `*`, `/` with a `Vector3` or a number on either side. `Vector4`: `+`, `-`, `*`, `/` with a `Vector4` or a number on either side. All: unary `-`, `==`.

### Color

| Constructor | Result |
|-------------|--------|
| `Color()` | Transparent black. |
| `Color(r, g, b, a?)` with integers | Every argument an integer (`255`): `0` to `255`; `a` defaults to `255`. |
| `Color(r, g, b, a?)` with floats | Any argument a float (`1.0`, `0.5`, `x / 2`): `0` to `1`; `a` defaults to `1`. |
| `Color(hex)` | `"#RRGGBB"` or `"#RRGGBBAA"`, `#` optional. Any other string gives opaque white. |

- `Color(...)` is the same as `Color.new(...)`.
- `Color(255, 255, 255)` and `Color(1.0, 1.0, 1.0)` are white; `Color(1, 1, 1)` is almost black.
- `Color.Parse(hex)`: same as `Color(hex)`. `Color.HSV(h, s, v, a?)`: hue, saturation, value in `0` to `1`; `a` defaults to `1`.

### Object

| Method | Returns | Description |
|--------|---------|-------------|
| `GetId()` | `number` | Object id. |
| `GetName()` | `string` | Name of the current graphic, such as `Villager Male (Idle)`. Changes with the activity. `""` without a graphic. |
| `GetGraphicFileName()` | `string` | File name of the current graphic, such as `u_vil_male_villager_idleA_x1`. `""` without a graphic. |
| `GetTypeName()` | `string` | In-game name of the type, such as `Villager`, in the game's language. `""` for types without one. |
| `GetObjectType()` | `ObjectType` | Category: resource, building, projectile and so on. |
| `GetOwningPlayer()` | `Player` | Owner. |
| `GetGarrisonObject()` | `Object` or `nil` | Object this one is garrisoned in. |
| `GetTargetPosition()` | `Vector3` | Unit AI target position, `z = 0`. `(0, 0, 0)` without a unit AI. |
| `GetTargetObject()` | `Object` or `nil` | Unit AI target object. |
| `GetActionTargetPosition()` | `Vector3` or `nil` | Target position of the current action, such as where a projectile lands. `nil` without an action. |
| `GetDirection()` | `Vector3` | Facing direction, `z = 0`. |
| `IsVisible()` | `boolean` | Whether the object's tile is visible to the assigned player. Always `true` with Modules See Everything. `false` for an object that no longer exists. Never raises an error. |
| `IsExplored()` | `boolean` | Whether the tile is explored or visible. Never raises an error. |
| `IsAlive()` | `boolean` | Finished and alive. Foundations and dead units return `false`. |
| `IsFoundation()` | `boolean` | A building that is not finished yet. |
| `GetUnitObjectType()` | `UnitObjectType` | Type id. |
| `GetClass()` | `UnitClass` | Class id. |
| `GetAttribute(attribute, damageType?)` | `number` | Current `ObjectAttribute` value. `damageType` defaults to `0`. |
| `GetObjectData(objectData)` | `number` | An `ObjectData` field. |
| `IsIdle()` | `boolean` | The game marks the object idle. |
| `IsMoving()` | `boolean` | The unit follows a path. `false` for buildings. |
| `IsScouting()` | `boolean` | The unit is auto-scouting. |
| `GetHitpoints()` | `number` | Current hit points. |
| `GetMaxHitpoints()` | `number` | Maximum hit points. |
| `GetPosition()` | `Vector3` | World position. |
| `GetCurrentMapTile()` | `MapTile` or `nil` | Tile under the object. |
| `GetPath()` | `Vector3[]` | Waypoints of the current path; empty when not moving. |
| `CalculatePath(targetPos)` | `Vector3[]` | Path from the object to a `Vector3`, sized for the object. |
| `GetSprite()` | `table` or `nil` | Current graphic: `name` (as `GetName()`), `fileName` (as `GetGraphicFileName()`), `facet`, `facetCount`, `frameCount`, `frameDuration` (seconds per frame at normal speed, `0` without animation). `nil` without a graphic. The current frame is not available. |

- Resources and other objects that cannot move (`ObjectType.RESOURCE_OR_EYE_CANDY`, `ObjectType.ANIMATED_MAP_OBJECT`) return `(0, 0, 0)` from `GetDirection()`, an empty list from `GetPath()` and `nil` from `GetActionTargetPosition()`.
- A kept `Object` that went into the fog or stopped existing raises a Lua error on most method calls. Check `IsVisible()` first, or fetch objects again.
- Animals and resources on explored but not visible tiles allow only `IsVisible()`, `IsExplored()`, `GetId()`, `GetPosition()`, `GetClass()` and `GetUnitObjectType()`. Commands can target them, as a right-click in the fog does.
- When CONTROL cannot read a unit's current action, order or path, `GetActionTargetPosition()` and `GetTargetObject()` return `nil`, `GetPath()` returns an empty list, and `IsMoving()` and `IsScouting()` return `false`.

### MapTile

| Method | Returns | Description |
|--------|---------|-------------|
| `GetPosition()` | `Vector2` | Tile coordinates. |
| `GetTerrain()` | `Terrain` | Terrain id; `Terrain.UNKNOWN` (`-1`) for unexplored tiles. |
| `GetElevation()` | `number` | Elevation; `0` for unexplored tiles. |
| `GetTileVisibility()` | `TileVisibility` | The assigned player's visibility of the tile. |
| `IsBuildable()` | `boolean` | Whether a 1x1 building could be placed now (terrain, slope, objects and units checked). `false` when unexplored. For a real building type, use `CheckPlacement`. |
| `IsWalkable()` | `boolean` | Navigatable terrain with no blocking object (buildings, trees, mines); units do not count. `false` when unexplored. On an explored tile out of sight only known objects count (see below). |
| `IsNavigatable()` | `boolean` | Terrain lets land units move, whatever stands on it. Water is `false`. `false` when unexplored. |
| `GetObjectCount()` | `number` | Objects on the tile; `0` unless the tile is `VISIBLE`. |
| `GetObjects()` | `Object[]` | Objects on the tile; empty unless the tile is `VISIBLE`. |

### Player

| Method | Returns | Description |
|--------|---------|-------------|
| `GetId()` | `number` | Player id. |
| `GetPlayerType()` | `PlayerType` | Player type. |
| `GetPlayerObjects()` | `Object[]` | The player's objects. |
| `GetCameraPosition()` | `Vector2` | The player's camera position. |
| `GetMouseHoveredObject()` | `Object` or `nil` | Object under the player's mouse. |
| `GetSelectedObject()` | `Object` or `nil` | Object the player selected last. |
| `GetSelectedObjectCount()` | `number` | Number of selected objects. |
| `GetPlayerName()` | `string` | Player name. |
| `GetCivilizationId()` | `number` | Civilization id, `0` when unknown. |
| `GetCivilizationName()` | `string` | Civilization name, `""` when unknown. |
| `HasWon()` | `boolean` | The game or replay has ended and this player won. |
| `IsAlliedWith(player)` | `boolean` | Allied with another `Player`. |
| `IsEnemyTo(player)` | `boolean` | Enemy of another `Player`. |
| `GetColor()` | `Color` | Player color. |
| `GetAttribute(attribute)` | `number` | `PlayerAttribute` value. |
| `GetFarmReseedQueue()` | `number` | Farms (or pastures) in the reseed queue, 0–40. |
| `GetFishTrapRebuildQueue()` | `number` | Fish traps in the rebuild queue, 0–40. |
| `IsAutoFarmReseedEnabled()` | `boolean` | Automatic farm reseeding is on. |
| `IsAutoFishTrapRebuildEnabled()` | `boolean` | Automatic fish trap rebuilding is on. |
| `GetGoal(goalId)` | `number` or `nil` | Goal `goalId` (1–16384) of the player's AI script; `nil` for a player without a script, a player the module may not read, or an id out of range. |
| `GetGoals(firstGoalId, lastGoalId)` | `table` or `nil` | Goals `firstGoalId` to `lastGoalId` as a list (`values[1]` is goal `firstGoalId`); `nil` as for `GetGoal`. |
| `GetUnitTypeCount(id)` | `number` | Units of a type id. |
| `GetFact(fact, parameter?)` | `number` or `nil` | `Fact` value; `parameter` defaults to `0`. |
| `IsObjectTypeAvailable(unitObjectType)` | `boolean` | The player can currently get the type. |
| `CanAfford(id, isBuilding?)` | `boolean` | Resources and, unless `isBuilding` is `true`, population room. Does not check availability. |
| `GetResearchState(technology)` | `ResearchState` | Research state. |
| `GetTechCost(technology)` | cost list | Current technology cost. |
| `GetObjectCost(unitObjectType, costMultiplier?)` | cost list | Current object cost times `costMultiplier` (default `1.0`). |
| `CanAffordResearch(technology)` | `boolean` | The player can pay for it. |
| `CanResearch(technology)` | `boolean` | Researchable now and affordable. |
| `IsTechnologyResearched(technology)` | `boolean` | Researched. |
| `GetObjectsByTypes(unitTypes)` | `Object[]` | The player's objects of any type in the array. |
| `GetObjectsByMostCommonType(unitTypes)` | `Object[]` | The player's objects of the type in `unitTypes` the player has most of. |
| `GetObjectsByClass(unitClass)` | `Object[]` | The player's objects of a class. |
| `GetObjectsByClassDeadInclusive(unitClass)` | `Object[]` | Same, plus objects of the class that are not alive, such as dead livestock. |
| `GetFoundations()` | `Object[]` | The player's unfinished buildings. Other object lists leave them out. |
| `CountObjectsByTypes(unitTypes)` | `number` | Count of `GetObjectsByTypes(unitTypes)`. |
| `CountObjectsByClass(unitClass)` | `number` | Count of `GetObjectsByClass(unitClass)`. |
| `GetTownCenters()` | `Object[]` | The player's town centers. |

With **Modules See Everything** off, attribute, fact, count, cost, research and availability methods return real data only for the assigned player; for other players they return `0`, `false`, an empty list or `ResearchState.NOT_AVAILABLE`.

### GameOptions

`GetCurrentGameOptions()` returns the setup of the next match (before a match) or of the running match (read-only), or `nil`. Use `:` to call methods: `options:SetLocation(OptionsLocation.ARENA)`.

- Every `Set...` returns `true` when it wrote the value. Setters return `false` while a match runs.
- With **Tournament Mode** on every method except `SetAssignedPlayerCivilization()` is blocked.

| Getter | Setter | Value |
|--------|--------|-------|
| `GetAIDifficulty()` | `SetAIDifficulty(difficulty)` | `OptionsAIDifficulty` |
| `GetCivilizationSet()` | `SetCivilizationSet(civilizationSet)` | `OptionsCivilizationSet` |
| `GetGameMode()` | `SetGameMode(gameMode)` | `OptionsGameMode`. The getter returns `UNAVAILABLE` for a mode without a value; the setter rejects `UNAVAILABLE`. |
| `GetMapSize()` | `SetMapSize(mapSize)` | `OptionsMapSize` |
| `GetStartingAge()` | `SetStartingAge(age)` | `OptionsAge` |
| `GetEndingAge()` | `SetEndingAge(age)` | `OptionsAge` |
| `GetGameSpeed()` | `SetGameSpeed(gameSpeed)` | `number` |
| `GetRevealMap()` | `SetRevealMap(revealMap)` | `OptionsRevealMap` |
| `GetVictory()` | `SetVictory(victory)` | `OptionsVictory` |
| `GetVictoryLimit()` | `SetVictoryLimit(victoryLimit)` | `number` |
| `GetResources()` | `SetResources(resources)` | `OptionsResources` |
| `GetPopulation()` | `SetPopulation(population)` | `number` |
| `GetTreatyLength()` | `SetTreatyLength(treatyLength)` | `number` |
| `GetPlayersCount()` | `SetPlayersCount(playersCount)` | `number` of player slots in use |
| `GetLocation()` | `SetLocation(location)` | `OptionsLocation`; during a match, the map the game started. The setter also clears a custom map pool. |
| — | `SetRandomMapPoolLocations(locations)` | Array of 1 to 256 `OptionsLocation` values. One entry acts like `SetLocation()`; two or more set the location to `OptionsLocation.CUSTOM_MAP_POOL`, which `GetLocation()` then returns. Known issue in 1.1.0 to 1.1.3: `DispatchStartGame()` then starts the game's default map (Coastal), not a pool map. |
| `GetTeamsNotTogether()` | `SetTeamsNotTogether(value)` | `boolean` (`true` when Team Together is off) |
| `GetRecordGame()` | `SetRecordGame(value)` | `boolean` |
| `GetTeamPositions()` | `SetTeamPositions(value)` | `boolean` |
| `GetFullTechTree()` | `SetFullTechTree(value)` | `boolean` |
| `GetLockTeams()` | `SetLockTeams(value)` | `boolean` |
| `GetLockSpeed()` | `SetLockSpeed(value)` | `boolean` |
| `GetTurboMode()` | `SetTurboMode(value)` | `boolean` |
| `GetAntiquityMode()` | `SetAntiquityMode(value)` | `boolean` |
| `GetHandicap()` | `SetHandicap(value)` | `boolean` |
| `GetRandomMapSeed()` | `SetRandomMapSeed(seed)` | `number` or `nil`: fixed seed `0` to `4294967295`; `nil` when the game picks it. `ClearRandomMapSeed()` removes the fixed seed. |
| `GetRandomMapSource()` | `SetRandomMapSource(source)` | `RandomMapSource` or `nil`, from `GetAvailableRandomMapSources()`. |

`GetAvailableAIs()` returns the AI scripts the lobby offers as a list of `{ Name, Source }`; `Source` is `"builtin"`, `"install"` or `"custom"`. With Multithreading on it returns an empty table. The game's default AI is `PromiDE`.

Player slots: `playerIndex` is `0` to `7` (slot `0` is player 1). Out of range: setters return `false`, getters `0`.

| Getter | Setter | Value |
|--------|--------|-------|
| `GetPlayerTeam(playerIndex)` | `SetPlayerTeam(playerIndex, team)` | Team number |
| `GetPlayerHandicapPercentage(playerIndex)` | `SetPlayerHandicapPercentage(playerIndex, handicapPercentage)` | Handicap percentage |
| `GetPlayerColor(playerIndex)` | `SetPlayerColor(playerIndex, color)` | Color index |
| `GetPlayerCivilization(playerIndex)` | `SetPlayerCivilization(playerIndex, civilization)` | `OptionsCivilization`; the setter turns off random civilization |
| `GetPlayerAI(playerIndex)` | `SetPlayerAI(playerIndex, name)` | AI script name. The getter returns `nil` for a slot that is not a computer. The setter makes the slot a computer that runs the script; `false` for a name not in `GetAvailableAIs()` (case-sensitive), during a match and with Multithreading on. |
| — | `SetAssignedPlayerCivilization(civilization)` | Writes the slot of `GetAssignedPlayerId()`. `false` when that id is not `1` to `8`. Not blocked by Tournament Mode. |

`SetRandomMapSeed()`, `ClearRandomMapSeed()` and `SetRandomMapSource()` work only in single-player setup: they return `false` during a match, in multiplayer, in a replay, while a match starts or quits, and when the capability flag (`requestedSeed` or `selection`) is `false`. `SetRandomMapSource()` also rejects a source from an older catalog generation; call `GetAvailableRandomMapSources()` again after `RefreshRandomMapSources()`.

### RandomMapSource

Read-only properties. Each has a matching method: `GetDisplayName()`, `GetNativeMapId()`, `GetSourceKind()`, `GetModIdentity()`, `GetResolvedPath()`, `GetSourceIdentity()`, `GetAuthoredSourceSha256()`, `GetCatalogGeneration()`. `source.DisplayName` and `source:GetDisplayName()` return the same value.

| Property | Type | Meaning |
|----------|------|---------|
| `DisplayName` | `string` | Map name; for a local mod file, the file name without `.rms`. |
| `NativeMapId` | `number` | The game's map id. |
| `SourceKind` | `string` | `"game-catalog"` or `"local-mod"`. |
| `ModIdentity` | `string` or `nil` | Local mod folder name in lower case. |
| `ResolvedPath` | `string` or `nil` | Full path of the `.rms` file, when known. |
| `SourceIdentity` | `string` | Id that stays the same for the same map across refreshes. |
| `AuthoredSourceSha256` | `string` or `nil` | SHA-256 of a local mod's `.rms` file (up to 16 MiB). |
| `CatalogGeneration` | `number` | Catalog generation of this value. |

The list includes `.rms` files in the local mods of the game profiles (`%USERPROFILE%\Games\Age of Empires 2 DE\<profile id>\mods\local\<mod name>\resources\_common\random-map-scripts\`). Save a script there, call `RefreshRandomMapSources()`, then select it.

---

## 12. Strategic Components

`ResourceTracker`, `VillagerOccupation` and `ConstructionPlacement` run a simple economy for the assigned player. Create them with `.new(...)` or `:new(...)` in `Init()`, not at file scope; `ResourceTracker()` without `.new` does not work. Call their `Update()` methods from `Update()`. They need the live game: their `Update()` raises an error with Multithreading on.

### ResourceTracker

Constructor: `ResourceTracker.new()`

| Method | Returns | Description |
|--------|---------|-------------|
| `Update()` | `nil` | Rebuilds the lists below. |
| `GetConvertibleLivestock(position, radius)` | `Object[]` | Visible living Gaia livestock within `radius` tiles of a `Vector3`. |
| `GetOwnedLivestock()` | `Object[]` | The player's livestock, dead or alive. |
| `GetDeadLivestock(position, radius)` | `Object[]` | The player's dead livestock within `radius` tiles. |
| `GetForage()` | `Object[]` | Forage bushes. |
| `GetFarms()` | `Object[]` | The player's farms. |
| `GetTrees()` | `Object[]` | Trees. |
| `GetGold()` | `Object[]` | Gold mines. |
| `GetStone()` | `Object[]` | Stone mines. |

### VillagerOccupation

Constructor: `VillagerOccupation.new(resourceTracker)`

Each profession (`VillagerProfession`) has a weight; its target share is its weight divided by the sum. All four weights start at `1`.

| Method | Returns | Description |
|--------|---------|-------------|
| `Update()` | `nil` | Sends idle villagers to the profession furthest below its share. With **Sequential Actions** on, one villager per call. Call `ResourceTracker:Update()` first. |
| `GetVillagerCount()` | `number` | Villager count. |
| `GetVillagerCount(profession)` | `number` | Villagers in a `VillagerProfession`. |
| `GetIdleVillagerCount()` | `number` | Count of `GetIdleVillagers()`. |
| `GetAllVillagers()` | `Object[]` | The player's villagers. |
| `GetIdleVillagers()` | `Object[]` | Idle villagers, without those kept for construction. |
| `RequestVillagers(amount, position, urgency)` | `Object[]` | Picks up to `amount` villagers for a job at a `Vector3`; gives no order. `UrgencyLevel.HIGH`: closest villagers. `MEDIUM`: prefers professions above their share. `LOW`: only professions above their share. |
| `SetPriorities(wood, food, gold, stone)` | `nil` | Sets the four weights, whole numbers `0` to `1000000`. |
| `GetPriorityPercentage(profession)` | `number` | Target share, whole number `0` to `100`. |
| `SetPriorityPercentage(profession, share)` | `nil` | Sets one share as a fraction `0` to `0.99`; the others keep their ratio. |
| `ResetPriorities()` | `nil` | All weights back to `1`. |
| `RebalanceVillagers()` | `nil` | Moves working villagers between professions to match the shares. |
| `SetLivestockVillagerLimit(limit)` | `nil` | Villagers on livestock before forage and farms. Default `6`. |
| `SetForageVillagerLimit(limit)` | `nil` | Villagers on forage before farms. Default `8`. |
| `SetFarmMaxTownCenterDistance(distance)` | `nil` | Max tiles from a town center's edge for a new farm. Default `1.0`. |
| `SetFarmMaxMillDistance(distance)` | `nil` | Max tiles from a mill's edge for a new farm. Default `1.0`. |
| `SetProfessionBuildingRange(profession, range)` | `nil` | Drop-off range in tiles. Default `8.0`. |
| `AssignVillagers(villagers)` | `nil` | Assigns each villager in the array (`Object` values or ids) to the profession furthest below its share. |
| `AssignVillager(villager)` | `nil` | Assigns one `Object` now. |

Food assignment uses livestock up to its limit, then forage up to its limit, then farms. Wood, gold and stone villagers go to the closest resource with a drop-off building of the player in range; when there is none and a `ConstructionPlacement` was created with this `VillagerOccupation`, it queues a drop-off building.

### ConstructionPlacement

Constructor: `ConstructionPlacement.new(villagerOccupation)`

`structureType` is a `UnitObjectType`. `padding` is the free tiles kept around the building (default `1`). A band around town centers stays free (`SetTownCenterPadding()`) unless `bypassTownCenterPadding` is `true` (default `false`).

| Method | Returns | Description |
|--------|---------|-------------|
| `Update()` | `nil` | Reads the map and calls `ProcessBuildingRequests()`. Call it every update before placing buildings. |
| `SetTownCenterPadding(padding)` | `nil` | Tiles around town centers kept free, `0` to `1024`. Default `3`. |
| `BuildStructure(structureType, builderUnitId, targetPos, direction?, padding?, bypassTownCenterPadding?)` | `boolean` | Finds a free spot near the `Vector3` `targetPos` and orders unit `builderUnitId` to build. `direction` is a `PlacementDirection` (default `Center`) or a `Vector3` to search toward. `true` when the order was sent. |
| `BuildStructure(structureType, targetPos, direction?, padding?, bypassTownCenterPadding?)` | `boolean` | Same, with the villager closest to `targetPos`. |
| `BuildStructureAtTown(structureType, targetPos?, padding?, bypassTownCenterPadding?)` | `boolean` | Sends the closest villager to an unfinished building of the type that nobody works on. Otherwise builds near `targetPos` on the side facing the first town center, or around that town center when `targetPos` is left out. |
| `FindBestPosition(structureType, targetPos, direction, padding, bypassTownCenterPadding?)` | `Vector3` | The spot `BuildStructure` would use; `(-1, -1, -1)` when none. |
| `GetValidFarmPlacementTile()` | `MapTile` or `nil` | A free farm tile near a town center, or else a mill. |
| `QueueBuildingRequest(structureType, targetPosition, priority?, padding?, bypassTownCenterPadding?, builderUnitId?, requireScouting?)` | `nil` | Queues a building near `targetPosition`. `priority` is a `BuildingRequestPriority` (default `MEDIUM`); `builderUnitId` `-1` (default) takes the closest villager; `requireScouting` (default `true`) walks the builder within 5 tiles first. |
| `QueueBuildingRequestAtTown(structureType, priority?, padding?, bypassTownCenterPadding?, builderUnitId?, requireScouting?)` | `nil` | Queues a building near the first town center. `requireScouting` defaults to `false`. |
| `ProcessBuildingRequests()` | `nil` | Works on up to 3 queued requests, highest priority first. |
| `IsStructureTypeQueued(structureType)` | `boolean` | A request for the type is queued or the player has an unfinished building of it. |
| `IsUnitAssignedToBuilding(unitId)` | `boolean` | The unit is the builder of a queued request. |

A request is dropped after 45 seconds of scouting or 60 seconds in total. A new request is ignored when its type is already queued or has an unfinished building (farms excepted), or its builder already serves another request.

---

## 13. Enums

Enums are global tables of names to numbers: `UnitClass.VILLAGER`. Functions return the number; compare with `==`. Never guess a name or value; look it up at https://aoe2control.github.io/enums/ or https://aoe2control.github.io/game-options/#option-enums, or list a table at run time with `for name, value in pairs(UnitObjectType) do ... end`.

Enum tables: `Age`, `BuildingRequestPriority`, `Fact`, `Formation`, `Key`, `ObjectAttribute`, `ObjectData`, `ObjectType`, `OptionsAIDifficulty`, `OptionsAge`, `OptionsCivilization`, `OptionsCivilizationSet`, `OptionsGameMode`, `OptionsLocation`, `OptionsMapSize`, `OptionsResources`, `OptionsRevealMap`, `OptionsVictory`, `PlacementDirection`, `PlacementResult`, `PlayerAttribute`, `PlayerType`, `ProjectileType`, `ReplaySpeed`, `ResearchState`, `ResourceType`, `Technology`, `Terrain`, `TileVisibility`, `UnitClass`, `UnitCombatStance`, `UnitObjectType`, `UrgencyLevel`, `VictoryCondition`, `VillagerProfession`.

| Enum | Values |
|------|--------|
| `PlayerType` | `NON_PLAYER` 0, `HUMAN` 1, `GAIA` 2, `BOT` 3 |
| `VictoryCondition` | `STANDARD` 0, `CONQUEST` 1, `TIME_LIMIT` 2, `SCORE` 3, `CUSTOM` 4 |
| `ResearchState` | `NOT_AVAILABLE` -1, `LOCKED` 0, `RESEARCHABLE` 1, `RESEARCHING` 2, `RESEARCHED` 3 |
| `ReplaySpeed` | `SLOW` 0, `NORMAL` 1, `FAST` 2, `FASTEST` 3 |
| `PlayerAttribute` | `FOOD` 0, `WOOD` 1, `STONE` 2, `GOLD` 3, `POP_SPACE_LEFT` 4, `POP_CURRENT` 11, `AGE` 6 |
| `Age` | `DARK_AGE` 0, `FEUDAL_AGE` 1, `CASTLE_AGE` 2, `IMPERIAL_AGE` 3 |
| `ObjectType` | `RESOURCE_OR_EYE_CANDY` 10, `ANIMATED_MAP_OBJECT` 20, `DEAD_OR_FISH` 30, `PROJECTILE` 60, `NPC` 70, `BUILDING` 80 |
| `TileVisibility` | `UNEXPLORED` 0, `VISIBLE` 15, `EXPLORED` 128 |
| `PlacementResult` | `CAN_PLACE` 0, `TERRAIN_EDGE` 1, `TERRAIN` 2, `SLOPE` 3, `UNEXPLORED` 5, `BLOCKED` 6, `OUTSIDE_MAP` 7, `MAP_BOUNDARY` 11 |
| `UnitCombatStance` | `AGGRESSIVE` 0, `DEFENSIVE` 1, `NO_ATTACK` 2, `STAND_GROUND` 3 |
| `Formation` | `LINE` 2, `BOX` 4, `STAGGERED` 7, `FLANK` 8 |
| `UrgencyLevel` | `LOW` 0, `MEDIUM` 1, `HIGH` 2 |
| `VillagerProfession` | `WOOD` 0, `FOOD` 1, `STONE` 2, `GOLD` 3 |
| `PlacementDirection` | `Center` 0, `North` 1, `NorthEast` 2, `East` 3, `SouthEast` 4, `South` 5, `SouthWest` 6, `West` 7, `NorthWest` 8 |
| `BuildingRequestPriority` | `LOW` 0, `MEDIUM` 1, `HIGH` 2, `CRITICAL` 3 |

Notes:

- `Age` is the in-match enum; `OptionsAge` (match setup) has different values. `VictoryCondition` (`GetVictoryCondition()`) and `OptionsVictory` (`GameOptions`) differ too.
- `ResourceType` ids run `0` to `483`. `FOOD`, `WOOD`, `STONE`, `GOLD`, `POPULATION` (also `AMOUNT_FOOD` ... `AMOUNT_POPULATION_CAP`) are `0` to `4`; the rest start with `AMOUNT_`.
- `Terrain.UNKNOWN` is `-1`.
- `ObjectAttribute` is the attribute enum; there is no `UnitAttribute`. Common values: `HITPOINTS` 0, `LINE_OF_SIGHT` 1, `RADIUS_X` 3, `RADIUS_Y` 4, `SPEED` 5, `ARMOR` 8, `WEAPON` 9, `WEAPON_RANGE` 12, `RESOURCE_COST` 100, `CREATION_TIME` 101.
- `ObjectData` names drop the AI script's `object-data-` prefix: `ObjectData.POINT_X`.
- `Key` names use mixed case: `None`, `LButton`, `RButton`, `MButton`, `Backspace`, `Tab`, `Enter`, `Shift`, `Ctrl`, `Alt`, `Pause`, `CapsLock`, `Escape`, `Space`, `PageUp`, `PageDown`, `End`, `Home`, `Left`, `Up`, `Right`, `Down`, `Insert`, `Delete`, `Num0`-`Num9`, `A`-`Z`, `Numpad0`-`Numpad9`, `Multiply`, `Add`, `Separator`, `Subtract`, `Decimal`, `Divide`, `F1`-`F12`.
- `UnitObjectType`, `Technology`, `Fact`, `UnitClass`, `ProjectileType`, `Terrain`, `OptionsLocation` and `OptionsCivilization` are long; look them up.

---

## 14. Menu Settings That Change Module Behavior

| Setting (submenu) | Default | Effect |
|-------------------|---------|--------|
| **Enabled** (MODULES) | On | Turns all module slots on or off. |
| **Update Interval** (MODULES) | `1.0` | Seconds of game time between `Update()` calls (slider 0.1 to 2.0). |
| **Sequential Actions** (MODULES) | On | One game command per `Update()`. |
| **Auto Move Camera** (MODULES) | On | Camera follows each command's target. |
| **Suppress Native AI** (MODULES) | On | Turns off the built-in AI of a computer player whose slot has a module. |
| **Tournament Mode** (MODULES) | Off | See [Modes](#4-modes). |
| **Multithreading** (MODULES) | Off | See [Modes](#4-modes). Changing it reloads every module. |
| **Modules See Everything** (MODULES) | Off | Modules ignore fog of war when reading tiles, objects and players. |
| **Agent Bridge** (MODULES) | Off | Opens the Agent Bridge pipe for each player turned on below it. |
| **Sync** (player slot) | Off | Stores the module's settings in a shared profile (`S1`, `S2`, ...) instead of per player. |
| **Game Speed Multiplier** (MISC) | `Default` | Speed override; `SetGameSpeedMultiplier()` changes it. Resets to `Default` when CONTROL starts. |
| **API Profiling** (DEBUG) | Off | Fills `GetModuleTelemetry().api` for modules loaded after it is turned on. |

---

## 15. Example

```lua
local announced = false

function Load(playerId)
    Settings.AddBool("Highlight Villagers", true)
    Settings.AddColor("Villager Color", Color(0, 255, 0, 90))
    Log("Loading for player " .. tostring(playerId))
end

function Init()
    announced = false
end

function Update()
    if not announced then
        SendChatMessage("Hello from player " .. tostring(GetAssignedPlayerId()))
        announced = true
    end

    -- GetFact can return nil (game-state copy), so fall back to 0.
    if (GetFact(Fact.HOUSING_HEADROOM) or 0) > 0 then
        TrainUnit(UnitObjectType.VILLAGER_MALE)
    end
end

function Render()
    if not Settings.GetBool("Highlight Villagers", true) then
        return
    end

    local player = GetAssignedPlayer()
    if not player then
        return
    end

    local color = Settings.GetColor("Villager Color", Color(0, 255, 0, 90))
    for _, villager in ipairs(player:GetObjectsByClass(UnitClass.VILLAGER)) do
        RenderObjectBoundsFilled(villager, color)
    end
end
```
