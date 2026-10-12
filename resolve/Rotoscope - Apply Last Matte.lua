--[[
Rotoscope - Apply Last Matte
Applies the last saved matte to its clip without opening the Rotoscope app again.
Useful if something went wrong in Resolve after you pressed Done.
--]]
ROTOSCOPE_MODE = "apply_last"
local path = os.getenv("HOME") .. "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Rotoscope.lua"
local chunk, err = loadfile(path)
if chunk then
    if setfenv and getfenv then setfenv(chunk, getfenv(1)) end  -- same globals (resolve, fu, ...)
    chunk()
else
    print("[Rotoscope] Could not load " .. path .. ": " .. tostring(err))
end
ROTOSCOPE_MODE = nil
