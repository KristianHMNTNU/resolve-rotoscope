-- Minimal fake of the Resolve/Fusion scripting API, enough to run Rotoscope.lua outside Resolve.
-- Scenario values come from the global MOCK table (set by the test runner).

local calls = {}
local function rlist(t)  -- MOCK.weird_lists: {name = index}, as Resolve 19.1's *List() calls can return
    if not MOCK.weird_lists then return t end
    local out = {}
    for i, v in ipairs(t) do
        local ok, name = pcall(function() return v:GetName() end)
        out[ok and name or ("obj" .. i)] = i
    end
    return out
end
local function rec(...) calls[#calls + 1] = table.concat({ ... }, " ") end
MOCK_CALLS = calls

local function make_tool(name, reg)
    local inputs = {}
    local ids = {}
    if reg == "MatteControl" then
        ids = { { "MatteCombine", "Combine" }, { "CombineOp", "Combine Op" }, { "PostMultiplyImage", "Post-Multiply Image" },
                { "Background", "Background" }, { "Foreground", "Foreground" } }
    elseif reg == "Loader" then
        ids = { { "Clip", "Filename" }, { "GlobalIn", "Global In" }, { "GlobalOut", "Global Out" },
                { "ClipTimeStart", "Trim In" }, { "ClipTimeEnd", "Trim Out" } }
    elseif reg == "MediaIn" then
        -- As in Resolve 19.1: comp time = frame number in the source file.
        local last = MOCK.src_start + MOCK.duration - 1
        inputs.GlobalIn, inputs.GlobalOut, inputs.ClipTimeStart, inputs.ClipTimeEnd = 0, last, 0, last
    end
    local tool = { _inputs = inputs, _name = name, _reg = reg, Output = { tool = name } }
    function tool:GetInputList()
        local l = {}
        for i, p in ipairs(ids) do
            l[i] = { GetAttrs = function() return { INPS_ID = p[1], INPS_Name = p[2] } end }
        end
        return l
    end
    function tool:SetInput(id, v)
        rec("SetInput", self._name, id, tostring(v))
        if self._reg == "Loader" and self._length then
            local len = self._length
            if id == "GlobalIn" then
                if v > inputs.GlobalOut then return end
                inputs.ClipTimeStart = math.max(0, math.min(len - 1, inputs.ClipTimeStart + (v - inputs.GlobalIn)))
            elseif id == "GlobalOut" then
                if v < inputs.GlobalIn then return end
                inputs.ClipTimeEnd = math.max(0, math.min(len - 1, inputs.ClipTimeEnd + (v - inputs.GlobalOut)))
            end
        end
        inputs[id] = v
    end
    function tool:GetInput(id) return inputs[id] end
    function tool:SetAttrs(a) if a.TOOLS_Name then self._name = a.TOOLS_Name end end
    function tool:GetAttrs()
        local a = { TOOLS_Name = self._name, TOOLS_RegID = self._reg }
        if self._reg == "Loader" then a.TOOLIT_Clip_Length = { self._length or 0 } end
        return a
    end
    function tool:Delete() rec("DeleteTool", self._name) if self._owner then self._owner[self._key] = nil end end
    return setmetatable(tool, { __newindex = function(t, k, v)
        rec("assign", t._name .. "." .. k, type(v) == "table" and ("<- " .. tostring(v.tool)) or tostring(v))
        inputs[k] = v
        if k == "Clip" and t._reg == "Loader" and not (t._comp and t._comp._locked) and not MOCK.loader_fails then
            local len = MOCK.matte_frames or MOCK.duration
            rawset(t, "_length", len)
            -- A freshly loaded clip sits at comp time 0 with no trim, like in Resolve.
            inputs.GlobalIn, inputs.GlobalOut, inputs.ClipTimeStart, inputs.ClipTimeEnd = 0, len - 1, 0, len - 1
            inputs.HoldFirstFrame, inputs.HoldLastFrame = 0, 0
        end
    end })
end

local function make_comp(with_our_tools)
    local tools = { MediaIn1 = make_tool("MediaIn1", "MediaIn"), MediaOut1 = make_tool("MediaOut1", "MediaOut") }
    local comp = { _locked = false, TIME_UNDEFINED = -2000000000 }
    local n = 0
    local function own(key, t) rawset(t, "_owner", tools) rawset(t, "_key", key) rawset(t, "_comp", comp) tools[key] = t end
    own("MediaIn1", tools.MediaIn1)
    own("MediaOut1", tools.MediaOut1)
    function comp:FindTool(name) return tools[name] end
    function comp:GetToolList() local l = {} for _, t in pairs(tools) do l[#l + 1] = t end return l end
    function comp:AddTool(reg) n = n + 1 local t = make_tool(reg .. n, reg) own(reg .. n, t) rec("AddTool", reg) return t end
    function comp:Lock() self._locked = true end
    function comp:Unlock() self._locked = false end
    if with_our_tools then
        local l = make_tool("RotoscopeMatte", "Loader") own("old_loader", l)
        local m = make_tool("RotoscopeCutout", "MatteControl") own("old_matte", m)
    end
    function comp:GetAttrs()
        return { COMPN_RenderStart = MOCK.src_start, COMPN_RenderEnd = MOCK.src_start + MOCK.duration - 1 }
    end
    MOCK_TOOLS = tools
    return comp
end

-- Compositions as an ordered list, because Resolve 19.1 allows two compositions with the same name.
local comps = {}     -- { {name = ..., comp = ...}, ... }
local active = nil   -- the entry the timeline renders
local added = 0
local function add_comp(name, c) local e = { name = name, comp = c } comps[#comps + 1] = e return e end
local function first_named(n) for _, e in ipairs(comps) do if e.name == n then return e end end end
if MOCK.existing_comp then active = add_comp("Rotoscope", make_comp(true)) end
if MOCK.leftover_comp then
    -- Empty active "Rotoscope" plus a leftover "Composition 2" holding our nodes
    active = add_comp("Rotoscope", make_comp(false))
    add_comp("Composition 2", make_comp(true))
    added = 2
end
if MOCK.duplicate_names then
    -- Two compositions named "Rotoscope": the first (empty) one is active, and loading by name
    -- returns the last one, as seen in Resolve 19.1.
    active = add_comp("Rotoscope", make_comp(false))
    add_comp("Rotoscope", make_comp(true))
    added = 2
end

local item = {}
function item:GetName() return MOCK.clip_name end
function item:GetUniqueId() return "item-uid-1" end
function item:GetStart() return MOCK.item_start end
function item:GetEnd() return MOCK.item_start + MOCK.duration end
function item:GetDuration() return MOCK.duration end
function item:GetSourceStartFrame() return MOCK.src_start end
function item:GetSourceEndFrame() return MOCK.src_start + MOCK.src_span end
function item:GetLeftOffset() return MOCK.left_offset or MOCK.src_start end
function item:GetFusionCompNameList() local l = {} for i, e in ipairs(comps) do l[i] = e.name end return l end
function item:GetFusionCompCount() return #comps end
function item:GetFusionCompByIndex(i) return comps[i] and comps[i].comp end
function item:GetFusionCompByName(n) local e = first_named(n) return e and e.comp end
function item:DeleteFusionCompByName(n)
    local e = first_named(n)
    local ok = e ~= nil and e ~= active
    rec("DeleteComp", n, tostring(ok))
    if ok then for i, x in ipairs(comps) do if x == e then table.remove(comps, i) break end end end
    return ok
end
function item:AddFusionComp()
    added = added + 1
    local e = add_comp("Composition " .. added, make_comp())
    active = e
    rec("AddFusionComp", e.name)
    return e.comp
end
function item:RenameFusionCompByName(a, b)
    local e = first_named(a)
    rec("RenameComp", a, b, tostring(e ~= nil))
    if e then e.name = b end   -- Resolve 19.1 allows duplicate names
    return e ~= nil
end
function item:LoadFusionCompByName(n)
    local last
    for _, e in ipairs(comps) do if e.name == n then last = e end end
    if MOCK.duplicate_names then return last and last.comp end   -- returns one, activates another
    if last then active = last end
    return last and last.comp
end
function item:ExportFusionComp() return true end
function MOCK_LOADER()
    local t = active and active.comp:FindTool("RotoscopeMatte")
    if not t then for _, x in ipairs(active and active.comp:GetToolList() or {}) do
        if x._name == "RotoscopeMatte" then t = x end end end
    if not t then return nil end
    local i = t._inputs
    return { i.GlobalIn, i.GlobalOut, i.ClipTimeStart, i.ClipTimeEnd }
end

-- The composition the timeline actually renders, and whether its MediaOut gets the cut-out.
function MOCK_ACTIVE_IS_CUTOUT()
    local out = active and active.comp:FindTool("MediaOut1")
    local src = out and out._inputs.Input
    return src ~= nil and type(src) == "table" and tostring(src.tool):find("MatteControl") ~= nil
end
function item:GetClipEnabled() return MOCK.clip_enabled ~= false end
function item:GetTrackTypeAndIndex() return { "video", 1 } end
function item:GetMediaPoolItem()
    if MOCK.no_media then return nil end
    return {
        GetClipProperty = function(_, k)
            local p = { ["File Path"] = MOCK.file, FPS = MOCK.clip_fps, Resolution = "3840x2160" }
            return p[k]
        end,
        GetName = function() return MOCK.clip_name end,
    }
end

-- MOCK.v2 = {name = ..., track_enabled = bool, clip_enabled = bool}: another clip on V2, over the one on V1.
local v2item
if MOCK.v2 then
    v2item = {}
    for k, f in pairs(item) do v2item[k] = f end
    function v2item:GetName() return MOCK.v2.name end
    function v2item:GetClipEnabled() return MOCK.v2.clip_enabled ~= false end
    function v2item:GetTrackTypeAndIndex() return { "video", 2 } end
    function v2item:GetMediaPoolItem()
        return { GetClipProperty = function(_, k)
                     local p = { ["File Path"] = "/Volumes/Media/" .. MOCK.v2.name, FPS = MOCK.clip_fps, Resolution = "3840x2160" }
                     return p[k]
                 end,
                 GetName = function() return MOCK.v2.name end }
    end
end
local function track_list(i)
    if i == 1 then return { item } end
    if i == 2 and v2item then return { v2item } end
    return {}
end

local timeline = {
    GetName = function() return "Timeline 1" end,
    GetSetting = function(_, k) if k == "timelineFrameRate" then return MOCK.timeline_fps end end,
    GetStartFrame = function() return 90000 end,
    GetStartTimecode = function() return "01:00:00:00" end,
    GetCurrentTimecode = function() return MOCK.playhead_tc end,
    GetCurrentVideoItem = function() if MOCK.current_item then return (MOCK.current_v2 and v2item) or item end end,
    GetTrackCount = function() return 2 end,
    GetIsTrackEnabled = function(_, _, i) if i == 2 and MOCK.v2 then return MOCK.v2.track_enabled ~= false end return true end,
    GetItemListInTrack = function(_, _, i) return rlist(track_list(i)) end,
    GetItemsInTrack = (not MOCK.no_old_api) and function(_, _, i) return track_list(i) end or nil,
}

local project = {
    GetName = function() return MOCK.project end,
    GetCurrentTimeline = function() return timeline end,
    GetSetting = function() return MOCK.timeline_fps end,
}

local resolve_obj = {
    GetProjectManager = function() return { GetCurrentProject = function() return project end } end,
    GetVersionString = function() return "19.1.4 (mock)" end,
}
if MOCK.like_free_menu_script then
    -- Resolve 19.1 free: Resolve() returns nil, the global "resolve" works.
    function Resolve() return nil end
    resolve = resolve_obj
else
    function Resolve() return resolve_obj end
end
