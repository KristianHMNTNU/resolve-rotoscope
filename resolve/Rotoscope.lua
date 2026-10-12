--[[
Rotoscope for DaVinci Resolve 19 (free version)
Workspace > Scripts > Rotoscope

1. Finds the clip under the playhead.
2. Starts the Rotoscope app with that clip and waits for it to close.
3. If you pressed Done: imports the matte into the "Rotoscope" bin and adds a Fusion
   composition to the clip: MediaIn + Loader (matte) -> MatteControl -> MediaOut,
   so everything outside the matte becomes transparent.

Closing the app without pressing Done leaves the project untouched.
Log: ~/Library/Application Support/Rotoscope/logs/resolve_script.log

Notes on Resolve 19.1 (free) that this script works around:
  * Resolve() returns nil in menu scripts; the global `resolve` works.
  * Lists come back with an extra "__flags" entry, and the *List() calls sometimes
    return {name = index} instead of {index = object}.
  * GetSourceStartFrame() can be one frame early on trimmed clips; GetLeftOffset() is exact.
  * Fusion's Loader cannot read .mov files, so the matte is a PNG sequence.
  * The Loader only reads its file if the comp is unlocked when Clip is set.
  * A clip can end up with two compositions of the same name; name lookups are then ambiguous.
--]]

local HOME = os.getenv("HOME")
local APP_DIR = HOME .. "/Library/Application Support/Rotoscope"
local JOBS_DIR = APP_DIR .. "/jobs"
local LOG_PATH = APP_DIR .. "/logs/resolve_script.log"
local APP_BUNDLE = APP_DIR .. "/Rotoscope.app"
local JOB_PATH = JOBS_DIR .. "/job.json"
local JOB_LUA = JOBS_DIR .. "/job.lua"
local RESULT_JSON = JOBS_DIR .. "/result.json"
local RESULT_LUA = JOBS_DIR .. "/result.lua"
local COMP_NAME = "Rotoscope"
local BIN_NAME = "Rotoscope"
local LOADER_NAME = "RotoscopeMatte"
local MATTE_NAME = "RotoscopeCutout"
local OUR_TOOLS = { [LOADER_NAME] = true, [MATTE_NAME] = true }

-- ── helpers ──────────────────────────────────────────────────────────────────

local function log(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
    local line = os.date("%Y-%m-%d %H:%M:%S ") .. table.concat(parts, " ")
    print("[Rotoscope] " .. line)
    local f = io.open(LOG_PATH, "a")
    if f then f:write(line .. "\n") f:close() end
end

local function sh_quote(s)
    return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

local function exec_ok(cmd)
    local r = os.execute(cmd)
    return r == 0 or r == true
end

local function applescript_string(s)
    return '"' .. tostring(s):gsub("\\", "\\\\"):gsub('"', '\\"') .. '"'
end

-- Message boxes go through osascript so they don't block Resolve.
local function alert(msg)
    log("ALERT: " .. msg)
    local script = "display alert " .. applescript_string("Rotoscope") .. " message " .. applescript_string(msg)
    os.execute("osascript -e " .. sh_quote(script) .. " >/dev/null 2>&1 &")
end

local function notify(msg)
    log(msg)
    local script = "display notification " .. applescript_string(msg) .. " with title " .. applescript_string("Rotoscope")
    os.execute("osascript -e " .. sh_quote(script) .. " >/dev/null 2>&1 &")
end

local function file_exists(path)
    local f = io.open(path, "r")
    if f then f:close() return true end
    return false
end

local function sleep(sec)
    os.execute("sleep " .. tostring(sec))
end

local function starts_with(s, prefix)
    return type(s) == "string" and s:sub(1, #prefix) == prefix
end

local function leading_number(s)
    return tonumber(tostring(s or ""):match("[%d%.]+"))
end

local function json_encode(v)
    local t = type(v)
    if t == "nil" then return "null"
    elseif t == "boolean" then return v and "true" or "false"
    elseif t == "number" then
        if v ~= v or v == math.huge or v == -math.huge then return "null" end
        if v == math.floor(v) then return string.format("%d", v) end
        return string.format("%.6f", v)
    elseif t == "string" then
        local s = v:gsub('[%c"\\]', function(c)
            local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
            return map[c] or string.format("\\u%04x", c:byte())
        end)
        return '"' .. s .. '"'
    elseif t == "table" then
        local parts = {}
        if #v > 0 then
            for _, x in ipairs(v) do parts[#parts + 1] = json_encode(x) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        for k, x in pairs(v) do parts[#parts + 1] = json_encode(tostring(k)) .. ":" .. json_encode(x) end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return json_encode(tostring(v))
end

local function lua_serialize(v)
    local t = type(v)
    if t == "table" then
        local parts = {}
        for k, x in pairs(v) do parts[#parts + 1] = "[" .. lua_serialize(k) .. "] = " .. lua_serialize(x) end
        return "{" .. table.concat(parts, ", ") .. "}"
    elseif t == "string" then
        return string.format("%q", v)
    elseif t == "number" or t == "boolean" then
        return tostring(v)
    end
    return "nil"
end

local function tc_to_frames(tc, fps)
    if not tc or tc == "" then return nil end
    local h, m, s, sep, f = tc:match("^(%d+):(%d+):(%d+)([:;%.])(%d+)$")
    if not h then return nil end
    h, m, s, f = tonumber(h), tonumber(m), tonumber(s), tonumber(f)
    local nominal = math.floor(fps + 0.5)
    local total = ((h * 60 + m) * 60 + s) * nominal + f
    if sep == ";" then  -- drop frame
        local dropped = (nominal == 60) and 4 or 2
        local minutes = h * 60 + m
        total = total - dropped * (minutes - math.floor(minutes / 10))
    end
    return total
end

-- ── reading lists from Resolve ───────────────────────────────────────────────

local function is_obj(x)
    local t = type(x)
    if t == "userdata" then return true end
    return t == "table" and (x.GetName ~= nil or x.GetAttrs ~= nil or x.GetClipProperty ~= nil or x.GetStart ~= nil)
end

local function is_internal(k)
    return type(k) == "string" and k:sub(1, 2) == "__"
end

-- Resolve objects in a returned list, whatever shape the list has.
local function objects(t)
    local out = {}
    if type(t) ~= "table" then return out end
    for k, v in pairs(t) do
        if not is_internal(k) then
            if is_obj(v) then out[#out + 1] = v elseif is_obj(k) then out[#out + 1] = k end
        end
    end
    return out
end

-- Strings in a returned list, whatever shape the list has.
local function strings(t)
    local out = {}
    if type(t) ~= "table" then return out end
    for k, v in pairs(t) do
        if not is_internal(k) then
            if type(v) == "string" then out[#out + 1] = v
            elseif type(k) == "string" then out[#out + 1] = k end
        end
    end
    return out
end

-- Call the first of several method names that exists and returns something. The older names
-- (GetSubFolders, GetClips, GetItemsInTrack) return plain lists and are tried first.
local function call_first(obj, names, ...)
    local args = { ... }
    for _, name in ipairs(names) do
        local okf, fn = pcall(function() return obj[name] end)
        if okf and fn then
            local ok, r = pcall(fn, obj, unpack(args))
            if ok and r ~= nil then return r end
        end
    end
    return nil
end

local function track_items(timeline, track)
    return objects(call_first(timeline, { "GetItemsInTrack", "GetItemListInTrack" }, "video", track))
end

-- ── finding the clip ─────────────────────────────────────────────────────────

local function find_item(timeline, fps)
    local playhead
    local now = tc_to_frames(timeline:GetCurrentTimecode(), fps)
    local start = tc_to_frames(timeline:GetStartTimecode(), fps)
    if now and start then playhead = timeline:GetStartFrame() + (now - start) end

    local function under(item)
        return item and playhead and item:GetStart() <= playhead and playhead < item:GetEnd()
    end

    local current = timeline:GetCurrentVideoItem()
    if current and (playhead == nil or under(current)) then return current, playhead end
    if playhead then
        for track = timeline:GetTrackCount("video"), 1, -1 do
            if timeline:GetIsTrackEnabled("video", track) then
                for _, item in ipairs(track_items(timeline, track)) do
                    if under(item) then return item, playhead end
                end
            end
        end
    end
    return nil, playhead
end

-- The clip a job was made for (used by "Apply Last Matte").
local function find_job_item(timeline, job)
    for track = timeline:GetTrackCount("video"), 1, -1 do
        for _, it in ipairs(track_items(timeline, track)) do
            local ok, uid = pcall(function() return it:GetUniqueId() end)
            if job.item_id and ok and uid == job.item_id then return it end
            if job.item_start and it:GetStart() == job.item_start then
                local m = it:GetMediaPoolItem()
                if m and m:GetClipProperty("File Path") == job.file then return it end
            end
        end
    end
    return nil
end

-- ── Fusion ───────────────────────────────────────────────────────────────────

local function set_input(tool, id, value)
    tool:SetInput(id, value)
    if tool:GetInput(id) ~= value then log("could not set", id, "on", tool:GetAttrs().TOOLS_Name) end
end

local function find_tool(comp, name, reg_id)
    local t = comp:FindTool(name)
    if t then return t end
    for _, tool in ipairs(objects(comp:GetToolList(false))) do
        if tool:GetAttrs().TOOLS_RegID == reg_id then return tool end
    end
    return nil
end

local function has_our_tools(comp)
    for _, t in ipairs(objects(comp:GetToolList(false))) do
        if OUR_TOOLS[t:GetAttrs().TOOLS_Name] then return true end
    end
    return false
end

local function comp_names_by_index(item)
    local byidx = {}
    local t = item:GetFusionCompNameList()
    if type(t) == "table" then
        for k, v in pairs(t) do
            if type(k) == "number" and type(v) == "string" then byidx[k] = v
            elseif type(v) == "number" and not is_internal(k) then byidx[v] = k end
        end
    end
    return byidx
end

local function comp_name_set(item)
    local set = {}
    for _, n in ipairs(strings(item:GetFusionCompNameList())) do set[n] = true end
    return set
end

-- All compositions on the clip named "Rotoscope" (normally one), created if there is none.
-- If there are several, all of them get the cut-out, so it doesn't matter which one Resolve shows.
local function rotoscope_comps(item)
    local byidx = comp_names_by_index(item)
    local count = item:GetFusionCompCount() or 0
    local comps = {}
    for idx = 1, count do
        if byidx[idx] == COMP_NAME then
            local ok, c = pcall(function() return item:GetFusionCompByIndex(idx) end)
            if ok and c then comps[#comps + 1] = c end
        end
    end
    if #comps > 0 then
        item:LoadFusionCompByName(COMP_NAME)
    else
        local before = comp_name_set(item)
        local added = item:AddFusionComp()
        for n in pairs(comp_name_set(item)) do
            if not before[n] then item:RenameFusionCompByName(n, COMP_NAME) end
        end
        local c = item:LoadFusionCompByName(COMP_NAME) or added
        if c then comps[1] = c end
    end
    -- Remove compositions with other names that hold our nodes (left over from a failed run).
    for idx, n in pairs(comp_names_by_index(item)) do
        if n ~= COMP_NAME then
            local ok, other = pcall(function() return item:GetFusionCompByIndex(idx) end)
            if ok and other and has_our_tools(other) then item:DeleteFusionCompByName(n) end
        end
    end
    return comps
end

local function build_into(comp, res)
    local media_in = find_tool(comp, "MediaIn1", "MediaIn")
    local media_out = find_tool(comp, "MediaOut1", "MediaOut")
    if not media_in or not media_out then return false, "No MediaIn/MediaOut in the composition." end

    local old_tools = {}
    for _, t in ipairs(objects(comp:GetToolList(false))) do
        if OUR_TOOLS[t:GetAttrs().TOOLS_Name] then old_tools[#old_tools + 1] = t end
    end

    local ok, err = pcall(function()
        -- Composition time is the frame number in the source file, so the matte starts at the
        -- clip's first source frame.
        local start = comp:GetAttrs().COMPN_RenderStart or 0
        local gin, cts = media_in:GetInput("GlobalIn"), media_in:GetInput("ClipTimeStart")
        if type(gin) == "number" and type(cts) == "number" and type(res.start_frame) == "number" then
            start = res.start_frame + (gin - cts)
        end

        -- Lock only while adding the Loader (or Fusion opens a file dialog); set the file unlocked.
        comp:Lock()
        local loader = comp:AddTool("Loader", -32768, -32768)
        comp:Unlock()
        loader.Clip = res.matte
        local attrs = loader:GetAttrs()
        local length = attrs.TOOLIT_Clip_Length and attrs.TOOLIT_Clip_Length[1]
        if length == 0 then
            loader:Delete()
            error("Fusion could not read the matte: " .. res.matte)
        end

        -- The new matte loads, so replace the nodes from an earlier run.
        for _, t in ipairs(old_tools) do t:Delete() end
        loader:SetAttrs({ TOOLS_Name = LOADER_NAME })
        -- Fusion keeps the length when GlobalIn moves: start, trims, then end.
        loader:SetInput("GlobalIn", start)
        loader:SetInput("ClipTimeStart", 0)
        loader:SetInput("ClipTimeEnd", res.frames - 1)
        loader:SetInput("GlobalOut", start + res.frames - 1)

        local matte = comp:AddTool("MatteControl", -32768, -32768)
        matte:SetAttrs({ TOOLS_Name = MATTE_NAME })
        matte.Background = media_in.Output
        matte.Foreground = loader.Output
        set_input(matte, "MatteCombine", 1)       -- Combine Red: the grey matte's red channel becomes alpha
        set_input(matte, "CombineOp", 0)          -- Copy
        set_input(matte, "PostMultiplyImage", 1)
        media_out.Input = matte.Output

        local flow = comp.CurrentFrame and comp.CurrentFrame.FlowView
        if flow then
            local x_in, y_in = flow:GetPos(media_in)
            local x_out = flow:GetPos(media_out)
            if x_in and x_out then
                flow:SetPos(matte, (x_in + x_out) / 2, y_in)
                flow:SetPos(loader, (x_in + x_out) / 2, y_in - 2)
            end
        end
        log("composition ready, matte", res.frames, "frames from comp time", start)
    end)
    if not ok then
        -- Never leave the clip black: if MediaOut lost its input, connect the original again.
        pcall(function()
            if not media_out.Input:GetConnectedOutput() then media_out.Input = media_in.Output end
        end)
        return false, tostring(err)
    end
    return true
end

local function build_comp(item, res)
    local comps = rotoscope_comps(item)
    if #comps == 0 then return false, "Could not add a Fusion composition to the clip." end
    local first_err
    for _, comp in ipairs(comps) do
        local ok, err = build_into(comp, res)
        if not ok and not first_err then first_err = err end
    end
    -- Keep a copy of the compositions for troubleshooting.
    for idx = 1, (item:GetFusionCompCount() or 0) do
        pcall(function() item:ExportFusionComp(APP_DIR .. "/logs/last_comp_" .. idx .. ".comp", idx) end)
    end
    if first_err then return false, first_err end
    return true
end

-- ── media pool ───────────────────────────────────────────────────────────────

local function import_matte(project, res)
    local mp = project:GetMediaPool()
    local root = mp:GetRootFolder()
    local bin
    for _, f in ipairs(objects(call_first(root, { "GetSubFolders", "GetSubFolderList" }))) do
        local ok, name = pcall(function() return f:GetName() end)
        if ok and name == BIN_NAME then bin = f end
    end
    bin = bin or mp:AddSubFolder(root, BIN_NAME)
    if not bin then return false, "Could not create the " .. BIN_NAME .. " bin in the media pool." end

    -- Remove an earlier import of the same matte.
    for _, clip in ipairs(objects(call_first(bin, { "GetClips", "GetClipList" }))) do
        local fp = clip:GetClipProperty("File Path")
        if fp == res.matte or starts_with(fp, res.matte_dir .. "/") then mp:DeleteClips({ clip }) end
    end

    local previous = mp:GetCurrentFolder()
    mp:SetCurrentFolder(bin)
    local items = mp:ImportMedia({ { FilePath = res.matte_pattern, StartIndex = 0, EndIndex = res.frames - 1 } })
    if type(items) ~= "table" or next(items) == nil then items = mp:ImportMedia({ res.matte_dir }) end
    if previous then mp:SetCurrentFolder(previous) end
    if type(items) ~= "table" or next(items) == nil then
        log("matte not added to the media pool; the composition reads the files directly")
    end
    return true
end

-- ── main ─────────────────────────────────────────────────────────────────────

local function get_resolve()
    local env = getfenv and getfenv(1) or _G
    local attempts = {
        function() return _G.resolve or rawget(env, "resolve") end,
        function() return _G.fu and _G.fu:GetResolve() end,
        function() return _G.Resolve and _G.Resolve() end,
        function() return _G.bmd and _G.bmd.scriptapp("Resolve") end,
    }
    for _, get in ipairs(attempts) do
        local ok, r = pcall(get)
        if ok and r then
            local ok2, pm = pcall(function() return r:GetProjectManager() end)
            if ok2 and pm then return r end
        end
    end
    return nil
end

local function open_project()
    local resolve = get_resolve()
    if not resolve then
        alert("Could not connect to Resolve. Start the script from Workspace > Scripts inside Resolve.")
        return nil
    end
    local project = resolve:GetProjectManager():GetCurrentProject()
    if not project then alert("No project is open.") return nil end
    local timeline = project:GetCurrentTimeline()
    if not timeline then alert("Open a timeline first.") return nil end
    return resolve, project, timeline
end

local function read_result()
    local ok, res = pcall(dofile, RESULT_LUA)
    if not ok or type(res) ~= "table" then return nil, tostring(res) end
    return res
end

local function apply_result(project, item, res)
    local ok, err = import_matte(project, res)
    if not ok then alert(err) return end
    ok, err = build_comp(item, res)
    if not ok then
        alert("Could not set up the cut-out in Fusion:\n" .. err .. "\n\nThe matte is saved here:\n" .. res.matte_dir)
        return
    end
    notify("Cut-out added to " .. item:GetName())
end

-- "Rotoscope - Apply Last Matte": apply the last saved matte without opening the app again.
local function apply_last()
    log("---- apply last matte ----")
    local resolve, project, timeline = open_project()
    if not resolve then return end
    local okj, job = pcall(dofile, JOB_LUA)
    local res = read_result()
    if not okj or type(job) ~= "table" or not res or res.status ~= "done" then
        alert("There is no saved matte to apply. Run Rotoscope and press Done first.")
        return
    end
    if job.project ~= project:GetName() then
        alert("The last matte belongs to the project \"" .. tostring(job.project) .. "\". Open that project first.")
        return
    end
    local item = find_job_item(timeline, job)
    if not item then
        alert("Could not find the clip for the last matte in this timeline.\n\nThe matte is saved here:\n"
            .. tostring(res.matte_dir))
        return
    end
    apply_result(project, item, res)
end

local function run()
    log("---- start ----")
    if not file_exists(APP_BUNDLE .. "/Contents/MacOS/rotoscope") then
        alert("The Rotoscope app is not installed. Run install.sh again.")
        return
    end
    local resolve, project, timeline = open_project()
    if not resolve then return end

    local fps = leading_number(timeline:GetSetting("timelineFrameRate"))
        or leading_number(project:GetSetting("timelineFrameRate")) or 25
    local item, playhead = find_item(timeline, fps)
    if not item then
        alert("There is no clip under the playhead. Use the Edit or Cut page and put the playhead over a clip.")
        return
    end

    local mpi = item:GetMediaPoolItem()
    local path = mpi and mpi:GetClipProperty("File Path") or ""
    if path == "" then
        alert("The clip under the playhead has no source file (title, generator, compound clip, ...).")
        return
    end

    local src_start = item:GetSourceStartFrame()
    local src_end = item:GetSourceEndFrame()
    local duration = item:GetDuration()
    local left = item:GetLeftOffset()
    local clip_fps = leading_number(mpi:GetClipProperty("FPS"))

    -- Clips with changed speed (or a different frame rate than the timeline) are not supported.
    local span = (src_end or 0) - (src_start or 0)
    local retimed = math.abs(span - duration) > 1 and math.abs(span - (duration - 1)) > 1
    if clip_fps and math.abs(clip_fps - fps) > 0.01 then retimed = true end
    if retimed then
        alert("This clip has a speed change, or a different frame rate than the timeline. "
            .. "That is not supported yet. Nothing was changed.")
        return
    end

    local first = src_start
    if type(left) == "number" and left >= 0 and math.abs(left - src_start) <= 1 then first = left end

    local width, height = (mpi:GetClipProperty("Resolution") or ""):match("(%d+)%s*[xX]%s*(%d+)")
    local ok_uid, item_id = pcall(function() return item:GetUniqueId() end)
    local job = {
        version = 1,
        file = path,
        start_frame = first,
        end_frame = first + duration - 1,
        fps = mpi:GetClipProperty("FPS"),
        width = tonumber(width), height = tonumber(height),
        clip_name = mpi:GetName(),
        project = project:GetName(),
        timeline = timeline:GetName(),
        item_id = ok_uid and item_id or nil,
        item_start = item:GetStart(),
        result_path = RESULT_JSON,
        result_lua = RESULT_LUA,
        debug = {
            source_start = src_start, source_end = src_end, duration = duration, left_offset = left,
            playhead = playhead, timeline_fps = fps, clip_fps = clip_fps, resolve = resolve:GetVersionString(),
        },
    }
    os.remove(RESULT_JSON)
    os.remove(RESULT_LUA)
    local f = io.open(JOB_PATH, "w")
    if not f then alert("Could not write " .. JOB_PATH) return end
    f:write(json_encode(job))
    f:close()
    f = io.open(JOB_LUA, "w")
    if f then f:write("return " .. lua_serialize(job) .. "\n") f:close() end
    log("job:", json_encode(job))

    if not exec_ok("open -n " .. sh_quote(APP_BUNDLE) .. " --args --job " .. sh_quote(JOB_PATH)) then
        alert("Could not start the Rotoscope app.")
        return
    end

    -- Wait for the app, checking now and then that it is still running.
    local waited, gone = 0, 0
    while not file_exists(RESULT_JSON) do
        sleep(0.5)
        waited = waited + 0.5
        if waited >= 20 and waited % 5 == 0 then
            if exec_ok("pgrep -f 'rotoscope --job' >/dev/null 2>&1") then
                gone = 0
            else
                gone = gone + 1
                if gone >= 3 and not file_exists(RESULT_JSON) then
                    alert("The Rotoscope app closed unexpectedly. Nothing was changed.")
                    return
                end
            end
        end
    end
    sleep(0.2)
    local res, err = read_result()
    if not res then
        alert("Could not read the result from the app:\n" .. tostring(err))
        return
    end
    log("app finished:", tostring(res.status))
    if res.status == "error" then alert("Rotoscope stopped with an error:\n" .. tostring(res.message)) end
    if res.status ~= "done" then return end
    apply_result(project, item, res)
end

local ok, err = pcall(function()
    os.execute("mkdir -p " .. sh_quote(JOBS_DIR) .. " " .. sh_quote(APP_DIR .. "/logs"))
    if ROTOSCOPE_MODE == "apply_last" then apply_last() else run() end
end)
if not ok then
    log("ERROR: " .. tostring(err))
    alert("Something went wrong in the Resolve script:\n" .. tostring(err) .. "\n\nLog: " .. LOG_PATH)
end
