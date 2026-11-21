-- roblox_mock.lua: a strict, DB-driven model of the Roblox Studio API surface,
-- for running the GODMODE plugin's (translated) Luau inside a Lua 5.4 VM.
--
-- THIS IS NOT ROBLOX STUDIO. Classes, properties, methods, events and enums are
-- checked against roblox_api.lua (generated from Roblox's own API dump), so a
-- misspelt member, a wrong property type or a call to an API that does not exist
-- fails loudly, the way it would in Studio. Behaviour, however, is our model:
--   * signals fire immediately (Roblox defaults to Deferred signal behaviour);
--   * the task scheduler runs on a virtual clock advanced by __mock.advance(dt);
--   * layout (AbsoluteSize/AbsolutePosition/TextBounds) is an approximation, and
--     AbsoluteSize/AbsoluteCanvasSize change signals fire only via __mock.relayout;
--   * tweens jump to their end values when they finish (no intermediate frames);
--   * methods we did not model raise "mock: ... not implemented" instead of
--     pretending to work.
-- The host (lua54.py) sets __roblox_api_source before running this file.

local API = assert(load(assert(__roblox_api_source, "__roblox_api_source not set"), "=roblox_api", "t", {}))()
__roblox_api_source = nil
local CLASSES, ENUMS = API.classes, API.enums

local raw_tostring, raw_type, raw_pairs, raw_ipairs, raw_next = tostring, type, pairs, ipairs, next
local raw_rawget, raw_rawset, raw_rawlen, raw_select, raw_error = rawget, rawset, rawlen, select, error
local raw_format, raw_concat = string.format, table.concat
local unpack = table.unpack

__mock = {
  output = {},        -- { {kind="print"|"warn"|"error", text=...} }
  errors = {},        -- uncaught errors from scheduled threads
  deprecated = {},    -- [ "Class.Member" ] = count
  http_log = {},
  http_handler = nil, -- function(options) -> response table, or error
  http_latency = 0,
  settings = {},      -- plugin:GetSetting/SetSetting storage (JSON-safe values)
  restored_widget_state = {},
  editor_buffers = setmetatable({}, { __mode = "k" }),
  keys_down = {},
  user_id = 0,
  widget_size = { 380, 560 },
  unrecorded = {},    -- DataModel changes made outside any ChangeHistory recording
  stats = { connections = 0, instances = 0, threads = 0 },
  now = 0,
}

---------------------------------------------------------------------------
-- Number formatting / tostring / concat (Luau prints 1.0 as "1")
---------------------------------------------------------------------------

local function num2str(n)
  if math.type(n) == "integer" then return raw_format("%d", n) end
  if n ~= n then return "nan" end
  if n == math.huge then return "inf" elseif n == -math.huge then return "-inf" end
  if n == math.floor(n) and math.abs(n) < 1e15 then
    if n == 0 and 1 / n < 0 then return "-0" end
    return raw_format("%d", math.tointeger(n))
  end
  local s = raw_format("%.14g", n)
  if tonumber(s) ~= n then s = raw_format("%.17g", n) end
  return s
end
__mock.num2str = num2str

function tostring(v)
  if raw_type(v) == "number" then return num2str(v) end
  return raw_tostring(v)
end

function __cat(a, b)
  local ta, tb = raw_type(a), raw_type(b)
  if ta == "number" then a = num2str(a); ta = "string" end
  if tb == "number" then b = num2str(b); tb = "string" end
  if ta == "string" and tb == "string" then return a .. b end
  return a .. b -- raises Lua's own "attempt to concatenate" error
end

function table.concat(t, sep, i, j)
  sep = sep or ""; i = i or 1; j = j or #t
  local parts = {}
  for k = i, j do
    local v = t[k]
    local tv = raw_type(v)
    if tv == "number" then parts[#parts + 1] = num2str(v)
    elseif tv == "string" then parts[#parts + 1] = v
    else raw_error("invalid value (at index " .. k .. ") in table for 'concat'", 2) end
  end
  return raw_concat(parts, sep)
end

function string.format(fmt, ...)
  local args = { ... }
  local n = raw_select("#", ...)
  local idx = 0
  for spec, conv in fmt:gmatch("%%([%-+ #0]*%d*%.?%d*)([%a%%])") do
    if conv ~= "%" then
      idx = idx + 1
      local v = args[idx]
      if conv == "d" or conv == "i" or conv == "x" or conv == "X" or conv == "o" or conv == "c" then
        if raw_type(v) == "number" then
          if v ~= v or v == math.huge or v == -math.huge then raw_error("bad argument #" .. idx .. " to 'format' (number has no integer representation)", 2) end
          args[idx] = math.tointeger(v >= 0 and math.floor(v) or math.ceil(v))
        end
      elseif conv == "s" then
        if idx <= n then args[idx] = tostring(v) end
      end
    end
  end
  return raw_format(fmt, unpack(args, 1, n))
end
string.format = string.format

local function out(kind, ...)
  local parts = {}
  for i = 1, raw_select("#", ...) do parts[i] = tostring((raw_select(i, ...))) end
  local text = raw_concat(parts, "\t")
  __mock.output[#__mock.output + 1] = { kind = kind, text = text }
end
function print(...) out("print", ...) end
function warn(...) out("warn", ...) end

function string.split(s, sep)
  sep = sep or ","
  local result = {}
  if sep == "" then
    for i = 1, #s do result[i] = s:sub(i, i) end
    return result
  end
  local start = 1
  while true do
    local a, b = s:find(sep, start, true)
    if not a then result[#result + 1] = s:sub(start); break end
    result[#result + 1] = s:sub(start, a - 1)
    start = b + 1
  end
  return result
end

function math.clamp(x, lo, hi)
  if lo > hi then raw_error("max must be greater than or equal to min", 2) end
  if x < lo then return lo elseif x > hi then return hi end
  return x
end
function math.round(x)
  if x >= 0 then return math.floor(x + 0.5) end
  return math.ceil(x - 0.5)
end
function math.sign(x) if x > 0 then return 1 elseif x < 0 then return -1 end return 0 end

function table.clone(t)
  if raw_type(t) ~= "table" then raw_error("invalid argument #1 to 'clone' (table expected)", 2) end
  local c = {}
  for k, v in raw_next, t do c[k] = v end
  return setmetatable(c, getmetatable(t))
end
function table.clear(t) for k in raw_next, t do t[k] = nil end end
function table.find(t, v, init)
  for i = init or 1, #t do if t[i] == v then return i end end
  return nil
end
function table.create(n, v) local t = {} for i = 1, n do t[i] = v end return t end
_G.unpack = table.unpack

local raw_running = coroutine.running
function coroutine.running() return (raw_running()) end

---------------------------------------------------------------------------
-- Userdata-like proxies: Luau userdata cannot be iterated or rawget'd
---------------------------------------------------------------------------

local USERDATA = setmetatable({}, { __mode = "k" })
local TYPEOF = setmetatable({}, { __mode = "k" }) -- proxy -> typeof name

function type(v)
  if USERDATA[v] then return "userdata" end
  return raw_type(v)
end
local function check_table(v, fname)
  if USERDATA[v] then raw_error("invalid argument #1 to '" .. fname .. "' (table expected, got userdata)", 3) end
end
function pairs(t) check_table(t, "pairs") return raw_pairs(t) end
function ipairs(t) check_table(t, "ipairs") return raw_ipairs(t) end
function next(t, k) check_table(t, "next") return raw_next(t, k) end
function rawget(t, k) check_table(t, "rawget") return raw_rawget(t, k) end
function rawset(t, k, v) check_table(t, "rawset") return raw_rawset(t, k, v) end

function __iter(x, ...)
  if raw_type(x) == "function" then return x, ... end
  if USERDATA[x] then raw_error("attempt to iterate over a userdata value", 2) end
  if raw_type(x) ~= "table" then raw_error("attempt to iterate over a " .. type(x) .. " value", 2) end
  local mt = getmetatable(x)
  if raw_type(mt) == "table" and mt.__iter then return mt.__iter(x) end
  return raw_next, x, nil
end

function typeof(v)
  local t = TYPEOF[v]
  if t then return t end
  return type(v)
end

local function make_proxy(kind, mt)
  local p = setmetatable({}, mt)
  USERDATA[p] = true
  TYPEOF[p] = kind
  return p
end

---------------------------------------------------------------------------
-- Task scheduler on a virtual clock
---------------------------------------------------------------------------

local ready = {}     -- { {co=, args=} } for task.defer
local sleepers = {}  -- { {co=, wake=, seq=, args=fn?} }
local sleep_seq = 0
local cancelled = setmetatable({}, { __mode = "k" })

local function report_error(co, err)
  local tb = debug.traceback(co, tostring(err))
  __mock.errors[#__mock.errors + 1] = tb
  out("error", tb)
end

local function resume(co, ...)
  if coroutine.status(co) ~= "suspended" or cancelled[co] then return end
  local ok, err = coroutine.resume(co, ...)
  if not ok then report_error(co, err) end
end
__mock.resume = resume

local function to_thread(f)
  if raw_type(f) == "thread" then return f end
  if raw_type(f) ~= "function" then raw_error("invalid argument #1 (function or thread expected)", 3) end
  __mock.stats.threads = __mock.stats.threads + 1
  return coroutine.create(f)
end

task = {}
function task.spawn(f, ...)
  local co = to_thread(f)
  resume(co, ...)
  return co
end
function task.defer(f, ...)
  local co = to_thread(f)
  ready[#ready + 1] = { co = co, args = table.pack(...) }
  return co
end
local function add_sleeper(co, dt, args)
  sleep_seq = sleep_seq + 1
  local entry = { co = co, wake = __mock.now + math.max(dt or 0, 0), seq = sleep_seq, start = __mock.now, args = args }
  sleepers[#sleepers + 1] = entry
  return entry
end
function task.delay(dt, f, ...)
  local co = to_thread(f)
  add_sleeper(co, dt, table.pack(...))
  return co
end
function task.wait(dt)
  local co, main = raw_running()
  if main then raw_error("mock: task.wait called outside a coroutine", 2) end
  add_sleeper(co, dt or 0, nil)
  return coroutine.yield()
end
function task.cancel(co)
  if raw_type(co) ~= "thread" then raw_error("invalid argument #1 to 'cancel' (thread expected)", 2) end
  if co == raw_running() then raw_error("cannot cancel the running thread", 2) end
  cancelled[co] = true
  local st = coroutine.status(co)
  if st == "suspended" then
    local ok, err = coroutine.close(co)
    if not ok and err then report_error(co, err) end
  elseif st == "normal" then
    raw_error("cannot cancel thread", 2)
  end
end
function task.synchronize() end
function task.desynchronize() end
wait = function(dt) return task.wait(dt or 0.03), __mock.now end
spawn = function(f) return task.defer(f) end
delay = function(dt, f) return task.delay(dt, f) end

os.clock = function() return __mock.now end
local EPOCH = 1793000000
os.time = function(t) if t then return 0 end return EPOCH + math.floor(__mock.now) end
tick = function() return EPOCH + __mock.now end
time = function() return __mock.now end
elapsedTime = time

local function run_ready()
  local guard = 0
  while #ready > 0 do
    local batch = ready
    ready = {}
    for _, item in raw_ipairs(batch) do resume(item.co, unpack(item.args, 1, item.args.n)) end
    guard = guard + 1
    if guard > 10000 then raw_error("mock: runaway task.defer loop") end
  end
end

function __mock.flush() run_ready() end

-- Advance the virtual clock by dt seconds, waking sleepers in order.
function __mock.advance(dt)
  local target = __mock.now + (dt or 0)
  run_ready()
  local guard = 0
  while true do
    local best, best_i
    for i, s in raw_ipairs(sleepers) do
      if s.wake <= target and (not best or s.wake < best.wake or (s.wake == best.wake and s.seq < best.seq)) then
        best, best_i = s, i
      end
    end
    if not best then break end
    table.remove(sleepers, best_i)
    if best.wake > __mock.now then __mock.now = best.wake end
    if best.args then
      resume(best.co, unpack(best.args, 1, best.args.n))
    else
      resume(best.co, __mock.now - best.start, __mock.now)
    end
    run_ready()
    guard = guard + 1
    if guard > 100000 then raw_error("mock: runaway scheduler loop") end
  end
  __mock.now = target
  run_ready()
end

function __mock.pending_sleepers()
  local n = 0
  for _, s in raw_ipairs(sleepers) do
    if coroutine.status(s.co) == "suspended" and not cancelled[s.co] then n = n + 1 end
  end
  return n
end

---------------------------------------------------------------------------
-- Signals and connections
---------------------------------------------------------------------------

local SIGNAL = setmetatable({}, { __mode = "k" }) -- proxy -> state
local CONN = setmetatable({}, { __mode = "k" })

local function method_guard(self, store, name)
  if not store[self] then raw_error("Expected ':' not '.' calling member function " .. name, 3) end
end

local conn_mt = {}
local function disconnect(c)
  local st = CONN[c]
  if st.connected then
    st.connected = false
    __mock.stats.connections = __mock.stats.connections - 1
    local list = st.signal.conns
    for i = #list, 1, -1 do if list[i] == c then table.remove(list, i) end end
  end
end
conn_mt.__index = function(c, k)
  if k == "Connected" then return CONN[c].connected end
  if k == "Disconnect" then
    return function(self) method_guard(self, CONN, "Disconnect") disconnect(self) end
  end
  raw_error(tostring(k) .. " is not a valid member of RBXScriptConnection", 2)
end
conn_mt.__newindex = function(_, k) raw_error(tostring(k) .. " cannot be assigned to", 2) end
conn_mt.__tostring = function() return "Connection" end

local signal_mt = {}
local function new_signal(name, owner)
  local p = make_proxy("RBXScriptSignal", signal_mt)
  SIGNAL[p] = { name = name, conns = {}, waiters = {}, owner = owner }
  return p
end
__mock.new_signal = new_signal

local function connect(sig, fn, once)
  if raw_type(fn) ~= "function" then raw_error("Attempt to connect failed: Passed value is not a function", 3) end
  local st = SIGNAL[sig]
  local c = make_proxy("RBXScriptConnection", conn_mt)
  CONN[c] = { connected = true, fn = fn, once = once, signal = st }
  st.conns[#st.conns + 1] = c
  __mock.stats.connections = __mock.stats.connections + 1
  return c
end

signal_mt.__index = function(s, k)
  if k == "Connect" then return function(self, fn) method_guard(self, SIGNAL, "Connect") return connect(self, fn, false) end end
  if k == "Once" then return function(self, fn) method_guard(self, SIGNAL, "Once") return connect(self, fn, true) end end
  if k == "Wait" then
    return function(self)
      method_guard(self, SIGNAL, "Wait")
      local co, main = raw_running()
      if main then raw_error("mock: Wait called outside a coroutine", 2) end
      local st = SIGNAL[self]
      st.waiters[#st.waiters + 1] = co
      return coroutine.yield()
    end
  end
  if k == "ConnectParallel" then raw_error("mock: ConnectParallel not implemented", 2) end
  raw_error(tostring(k) .. " is not a valid member of RBXScriptSignal", 2)
end
signal_mt.__newindex = function(_, k) raw_error(tostring(k) .. " cannot be assigned to", 2) end
signal_mt.__tostring = function(s) return "Signal " .. SIGNAL[s].name end

local function fire(sig, ...)
  local st = SIGNAL[sig]
  local snapshot = table.move(st.conns, 1, #st.conns, 1, {})
  for _, c in raw_ipairs(snapshot) do
    local cs = CONN[c]
    if cs.connected then
      if cs.once then disconnect(c) end
      local co = coroutine.create(cs.fn)
      resume(co, ...)
    end
  end
  local waiters = st.waiters
  st.waiters = {}
  for _, co in raw_ipairs(waiters) do resume(co, ...) end
end
__mock.fire = fire

local function disconnect_all(sig)
  local st = SIGNAL[sig]
  for _, c in raw_ipairs(table.move(st.conns, 1, #st.conns, 1, {})) do disconnect(c) end
end

function __mock.signal_connections(sig) return #SIGNAL[sig].conns end

---------------------------------------------------------------------------
-- Datatypes (immutable value types)
---------------------------------------------------------------------------

local DATA = setmetatable({}, { __mode = "k" }) -- proxy -> fields

local function datatype(kind, fields, mt)
  local p = make_proxy(kind, mt)
  DATA[p] = fields
  return p
end
local function is_kind(v, kind) return TYPEOF[v] == kind end
local function arg_error(fname, i, expected, got)
  raw_error("invalid argument #" .. i .. " to '" .. fname .. "' (" .. expected .. " expected, got " .. typeof(got) .. ")", 3)
end
local function num_arg(v, fname, i, default)
  if v == nil then return default or 0 end
  if raw_type(v) ~= "number" then
    if raw_type(v) == "string" and tonumber(v) then return tonumber(v) end
    arg_error(fname, i, "number", v)
  end
  return v
end

local function make_type(kind, getters, methods, extra_mt)
  local mt = extra_mt or {}
  mt.__index = function(self, k)
    local g = getters[k]
    if g then return g(DATA[self], self) end
    local m = methods[k]
    if m then return m end
    raw_error(tostring(k) .. " is not a valid member of " .. kind, 2)
  end
  mt.__newindex = function(_, k) raw_error(tostring(k) .. " cannot be assigned to", 2) end
  -- Lua 5.4 calls __eq whenever either operand has one, even across types;
  -- Luau only compares values of the same type, so different kinds are unequal.
  local eq = mt.__eq
  if eq then
    mt.__eq = function(a, b)
      if TYPEOF[a] ~= kind or TYPEOF[b] ~= kind then return false end
      return eq(a, b)
    end
  end
  return mt
end

-- Vector2 / Vector3 ---------------------------------------------------------
local V2mt, V3mt
local function V2(x, y) return datatype("Vector2", { x, y }, V2mt) end
local function V3(x, y, z) return datatype("Vector3", { x, y, z }, V3mt) end

local function vec_binop(ctor, n, kind)
  return function(op)
    return function(a, b)
      local A = is_kind(a, kind) and DATA[a] or nil
      local B = is_kind(b, kind) and DATA[b] or nil
      local r = {}
      for i = 1, n do
        local x = A and A[i] or a
        local y = B and B[i] or b
        if raw_type(x) ~= "number" or raw_type(y) ~= "number" then
          raw_error("invalid argument to " .. kind .. " arithmetic", 2)
        end
        r[i] = op(x, y)
      end
      return ctor(unpack(r, 1, n))
    end
  end
end

do
  local function mag(d) local s = 0 for i = 1, #d do s = s + d[i] * d[i] end return math.sqrt(s) end
  local ops2 = vec_binop(V2, 2, "Vector2")
  V2mt = make_type("Vector2", {
    X = function(d) return d[1] end, Y = function(d) return d[2] end,
    Magnitude = function(d) return mag(d) end,
    Unit = function(d) local m = mag(d) if m == 0 then return V2(0 / 0, 0 / 0) end return V2(d[1] / m, d[2] / m) end,
  }, {
    Dot = function(a, b) return DATA[a][1] * DATA[b][1] + DATA[a][2] * DATA[b][2] end,
    Lerp = function(a, b, t) local A, B = DATA[a], DATA[b] return V2(A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t) end,
    Cross = function(a, b) return DATA[a][1] * DATA[b][2] - DATA[a][2] * DATA[b][1] end,
  }, {
    __add = ops2(function(x, y) return x + y end), __sub = ops2(function(x, y) return x - y end),
    __mul = ops2(function(x, y) return x * y end), __div = ops2(function(x, y) return x / y end),
    __unm = function(a) return V2(-DATA[a][1], -DATA[a][2]) end,
    __eq = function(a, b) local A, B = DATA[a], DATA[b] return A[1] == B[1] and A[2] == B[2] end,
    __tostring = function(a) return num2str(DATA[a][1]) .. ", " .. num2str(DATA[a][2]) end,
  })
  local ops3 = vec_binop(V3, 3, "Vector3")
  V3mt = make_type("Vector3", {
    X = function(d) return d[1] end, Y = function(d) return d[2] end, Z = function(d) return d[3] end,
    Magnitude = function(d) return mag(d) end,
    Unit = function(d) local m = mag(d) if m == 0 then return V3(0 / 0, 0 / 0, 0 / 0) end return V3(d[1] / m, d[2] / m, d[3] / m) end,
  }, {
    Dot = function(a, b) local A, B = DATA[a], DATA[b] return A[1] * B[1] + A[2] * B[2] + A[3] * B[3] end,
    Cross = function(a, b) local A, B = DATA[a], DATA[b]
      return V3(A[2] * B[3] - A[3] * B[2], A[3] * B[1] - A[1] * B[3], A[1] * B[2] - A[2] * B[1]) end,
    Lerp = function(a, b, t) local A, B = DATA[a], DATA[b]
      return V3(A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t, A[3] + (B[3] - A[3]) * t) end,
  }, {
    __add = ops3(function(x, y) return x + y end), __sub = ops3(function(x, y) return x - y end),
    __mul = ops3(function(x, y) return x * y end), __div = ops3(function(x, y) return x / y end),
    __unm = function(a) local A = DATA[a] return V3(-A[1], -A[2], -A[3]) end,
    __eq = function(a, b) local A, B = DATA[a], DATA[b] return A[1] == B[1] and A[2] == B[2] and A[3] == B[3] end,
    __tostring = function(a) local A = DATA[a] return num2str(A[1]) .. ", " .. num2str(A[2]) .. ", " .. num2str(A[3]) end,
  })
end
Vector2 = { new = function(x, y) return V2(num_arg(x, "new", 1), num_arg(y, "new", 2)) end }
Vector2.zero = V2(0, 0); Vector2.one = V2(1, 1); Vector2.xAxis = V2(1, 0); Vector2.yAxis = V2(0, 1)
Vector3 = { new = function(x, y, z) return V3(num_arg(x, "new", 1), num_arg(y, "new", 2), num_arg(z, "new", 3)) end }
Vector3.zero = V3(0, 0, 0); Vector3.one = V3(1, 1, 1)
Vector3.xAxis = V3(1, 0, 0); Vector3.yAxis = V3(0, 1, 0); Vector3.zAxis = V3(0, 0, 1)

-- UDim / UDim2 ---------------------------------------------------------------
local UDmt, UD2mt
local function UD(s, o) return datatype("UDim", { s, o }, UDmt) end
local function UD2(xs, xo, ys, yo) return datatype("UDim2", { xs, xo, ys, yo }, UD2mt) end
UDmt = make_type("UDim", {
  Scale = function(d) return d[1] end, Offset = function(d) return d[2] end,
}, {}, {
  __add = function(a, b) return UD(DATA[a][1] + DATA[b][1], DATA[a][2] + DATA[b][2]) end,
  __sub = function(a, b) return UD(DATA[a][1] - DATA[b][1], DATA[a][2] - DATA[b][2]) end,
  __unm = function(a) return UD(-DATA[a][1], -DATA[a][2]) end,
  __eq = function(a, b) return DATA[a][1] == DATA[b][1] and DATA[a][2] == DATA[b][2] end,
  __tostring = function(a) return num2str(DATA[a][1]) .. ", " .. num2str(DATA[a][2]) end,
})
UD2mt = make_type("UDim2", {
  X = function(d) return UD(d[1], d[2]) end, Y = function(d) return UD(d[3], d[4]) end,
  Width = function(d) return UD(d[1], d[2]) end, Height = function(d) return UD(d[3], d[4]) end,
}, {
  Lerp = function(a, b, t)
    local A, B = DATA[a], DATA[b]
    local r = {}
    for i = 1, 4 do r[i] = A[i] + (B[i] - A[i]) * t end
    return UD2(r[1], r[2], r[3], r[4])
  end,
}, {
  __add = function(a, b) local A, B = DATA[a], DATA[b] return UD2(A[1] + B[1], A[2] + B[2], A[3] + B[3], A[4] + B[4]) end,
  __sub = function(a, b) local A, B = DATA[a], DATA[b] return UD2(A[1] - B[1], A[2] - B[2], A[3] - B[3], A[4] - B[4]) end,
  __unm = function(a) local A = DATA[a] return UD2(-A[1], -A[2], -A[3], -A[4]) end,
  __eq = function(a, b) local A, B = DATA[a], DATA[b] return A[1] == B[1] and A[2] == B[2] and A[3] == B[3] and A[4] == B[4] end,
  __tostring = function(a) local A = DATA[a]
    return "{" .. num2str(A[1]) .. ", " .. num2str(A[2]) .. "}, {" .. num2str(A[3]) .. ", " .. num2str(A[4]) .. "}" end,
})
-- Roblox stores UDim offsets as int32.
local function int_offset(v) v = math.tointeger(v >= 0 and math.floor(v) or math.ceil(v)) return v end
UDim = { new = function(s, o) return UD(num_arg(s, "new", 1), int_offset(num_arg(o, "new", 2))) end }
UDim2 = {
  new = function(xs, xo, ys, yo)
    if is_kind(xs, "UDim") and is_kind(xo, "UDim") then
      return UD2(DATA[xs][1], DATA[xs][2], DATA[xo][1], DATA[xo][2])
    end
    return UD2(num_arg(xs, "new", 1), int_offset(num_arg(xo, "new", 2)), num_arg(ys, "new", 3), int_offset(num_arg(yo, "new", 4)))
  end,
  fromOffset = function(x, y) return UD2(0, int_offset(num_arg(x, "fromOffset", 1)), 0, int_offset(num_arg(y, "fromOffset", 2))) end,
  fromScale = function(x, y) return UD2(num_arg(x, "fromScale", 1), 0, num_arg(y, "fromScale", 2), 0) end,
}

-- Color3 ----------------------------------------------------------------------
local C3mt
local function C3(r, g, b) return datatype("Color3", { r, g, b }, C3mt) end
local function to_hsv(r, g, b)
  local mx, mn = math.max(r, g, b), math.min(r, g, b)
  local d = mx - mn
  local h = 0
  if d ~= 0 then
    if mx == r then h = ((g - b) / d) % 6
    elseif mx == g then h = (b - r) / d + 2
    else h = (r - g) / d + 4 end
    h = h / 6
  end
  local s = mx == 0 and 0 or d / mx
  return h, s, mx
end
C3mt = make_type("Color3", {
  R = function(d) return d[1] end, G = function(d) return d[2] end, B = function(d) return d[3] end,
}, {
  Lerp = function(a, b, t) local A, B = DATA[a], DATA[b]
    return C3(A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t, A[3] + (B[3] - A[3]) * t) end,
  ToHex = function(a) local A = DATA[a]
    local function c(x) return math.floor(math.clamp(x, 0, 1) * 255 + 0.5) end
    return raw_format("%02X%02X%02X", c(A[1]), c(A[2]), c(A[3])) end,
  ToHSV = function(a) local A = DATA[a] return to_hsv(A[1], A[2], A[3]) end,
}, {
  __eq = function(a, b) local A, B = DATA[a], DATA[b] return A[1] == B[1] and A[2] == B[2] and A[3] == B[3] end,
  __tostring = function(a) local A = DATA[a] return num2str(A[1]) .. ", " .. num2str(A[2]) .. ", " .. num2str(A[3]) end,
})
Color3 = {
  new = function(r, g, b) return C3(num_arg(r, "new", 1), num_arg(g, "new", 2), num_arg(b, "new", 3)) end,
  fromRGB = function(r, g, b)
    return C3(num_arg(r, "fromRGB", 1) / 255, num_arg(g, "fromRGB", 2) / 255, num_arg(b, "fromRGB", 3) / 255)
  end,
  fromHSV = function(h, s, v)
    h, s, v = num_arg(h, "fromHSV", 1), num_arg(s, "fromHSV", 2), num_arg(v, "fromHSV", 3)
    local i = math.floor(h * 6)
    local f = h * 6 - i
    local p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    i = i % 6
    if i == 0 then return C3(v, t, p) elseif i == 1 then return C3(q, v, p) elseif i == 2 then return C3(p, v, t)
    elseif i == 3 then return C3(p, q, v) elseif i == 4 then return C3(t, p, v) end
    return C3(v, p, q)
  end,
  fromHex = function(hex)
    if raw_type(hex) ~= "string" then arg_error("fromHex", 1, "string", hex) end
    local h = hex:gsub("^#", "")
    if #h == 3 then h = h:gsub(".", "%0%0") end
    if not h:match("^%x%x%x%x%x%x$") then raw_error("Unable to convert characters to hex value", 2) end
    return C3(tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255)
  end,
  toHSV = function(c) return to_hsv(DATA[c][1], DATA[c][2], DATA[c][3]) end,
}

-- CFrame (3x4 matrix: position p, rotation rows r[1..9]) ----------------------
local CFmt
local function CF(px, py, pz, r) return datatype("CFrame", { px, py, pz, r }, CFmt) end
local IDENT = { 1, 0, 0, 0, 1, 0, 0, 0, 1 }
local function matmul(a, b)
  local r = {}
  for i = 0, 2 do for j = 1, 3 do
    r[i * 3 + j] = a[i * 3 + 1] * b[j] + a[i * 3 + 2] * b[3 + j] + a[i * 3 + 3] * b[6 + j]
  end end
  return r
end
local function rotx(t) local c, s = math.cos(t), math.sin(t) return { 1, 0, 0, 0, c, -s, 0, s, c } end
local function roty(t) local c, s = math.cos(t), math.sin(t) return { c, 0, s, 0, 1, 0, -s, 0, c } end
local function rotz(t) local c, s = math.cos(t), math.sin(t) return { c, -s, 0, s, c, 0, 0, 0, 1 } end
local function cf_mul_point(d, x, y, z)
  local r = d[4]
  return r[1] * x + r[2] * y + r[3] * z + d[1], r[4] * x + r[5] * y + r[6] * z + d[2], r[7] * x + r[8] * y + r[9] * z + d[3]
end
local function cf_inverse(d)
  local r = d[4]
  local t = { r[1], r[4], r[7], r[2], r[5], r[8], r[3], r[6], r[9] }
  local px, py, pz = d[1], d[2], d[3]
  return CF(-(t[1] * px + t[2] * py + t[3] * pz), -(t[4] * px + t[5] * py + t[6] * pz), -(t[7] * px + t[8] * py + t[9] * pz), t)
end
local function euler_xyz(r)
  -- R = Rx(a) * Ry(b) * Rz(c)
  local b = math.asin(math.clamp(r[3], -1, 1))
  local a, c
  if math.abs(r[3]) < 0.9999999 then
    a = math.atan(-r[6], r[9]); c = math.atan(-r[2], r[1])
  else
    a = math.atan(r[8], r[5]); c = 0
  end
  return a, b, c
end
local function euler_yxz(r)
  -- R = Ry(y) * Rx(x) * Rz(z)
  local x = math.asin(math.clamp(-r[6], -1, 1))
  local y, z
  if math.abs(r[6]) < 0.9999999 then
    y = math.atan(r[3], r[9]); z = math.atan(r[4], r[5])
  else
    y = math.atan(-r[7], r[1]); z = 0
  end
  return x, y, z
end
CFmt = make_type("CFrame", {
  Position = function(d) return V3(d[1], d[2], d[3]) end, p = function(d) return V3(d[1], d[2], d[3]) end,
  X = function(d) return d[1] end, Y = function(d) return d[2] end, Z = function(d) return d[3] end,
  LookVector = function(d) local r = d[4] return V3(-r[3], -r[6], -r[9]) end,
  RightVector = function(d) local r = d[4] return V3(r[1], r[4], r[7]) end,
  UpVector = function(d) local r = d[4] return V3(r[2], r[5], r[8]) end,
  Rotation = function(d) return CF(0, 0, 0, d[4]) end,
}, {
  Inverse = function(a) return cf_inverse(DATA[a]) end,
  GetComponents = function(a) local d = DATA[a] local r = d[4]
    return d[1], d[2], d[3], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9] end,
  ToEulerAnglesXYZ = function(a) return euler_xyz(DATA[a][4]) end,
  ToOrientation = function(a) return euler_yxz(DATA[a][4]) end,
  PointToWorldSpace = function(a, v) return V3(cf_mul_point(DATA[a], DATA[v][1], DATA[v][2], DATA[v][3])) end,
  Lerp = function(a, b, t) -- position lerp + nearest rotation snap (approximation)
    local A, B = DATA[a], DATA[b]
    return CF(A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t, A[3] + (B[3] - A[3]) * t, t < 0.5 and A[4] or B[4])
  end,
}, {
  __mul = function(a, b)
    local A = DATA[a]
    if is_kind(b, "CFrame") then
      local B = DATA[b]
      local x, y, z = cf_mul_point(A, B[1], B[2], B[3])
      return CF(x, y, z, matmul(A[4], B[4]))
    elseif is_kind(b, "Vector3") then
      return V3(cf_mul_point(A, DATA[b][1], DATA[b][2], DATA[b][3]))
    end
    raw_error("invalid argument #2 to CFrame multiplication", 2)
  end,
  __add = function(a, b)
    if not is_kind(b, "Vector3") then raw_error("invalid argument #2 to CFrame addition (Vector3 expected)", 2) end
    local A, B = DATA[a], DATA[b]
    return CF(A[1] + B[1], A[2] + B[2], A[3] + B[3], A[4])
  end,
  __sub = function(a, b)
    if not is_kind(b, "Vector3") then raw_error("invalid argument #2 to CFrame subtraction (Vector3 expected)", 2) end
    local A, B = DATA[a], DATA[b]
    return CF(A[1] - B[1], A[2] - B[2], A[3] - B[3], A[4])
  end,
  __eq = function(a, b)
    local A, B = DATA[a], DATA[b]
    if A[1] ~= B[1] or A[2] ~= B[2] or A[3] ~= B[3] then return false end
    for i = 1, 9 do if A[4][i] ~= B[4][i] then return false end end
    return true
  end,
  __tostring = function(a)
    local p = { a:GetComponents() }
    for i = 1, 12 do p[i] = num2str(p[i]) end
    return raw_concat(p, ", ")
  end,
})
CFrame = {}
function CFrame.new(...)
  local n = raw_select("#", ...)
  local a = { ... }
  if n == 0 then return CF(0, 0, 0, IDENT) end
  if n == 1 then
    if not is_kind(a[1], "Vector3") then arg_error("new", 1, "Vector3", a[1]) end
    local d = DATA[a[1]] return CF(d[1], d[2], d[3], IDENT)
  end
  if n == 2 and is_kind(a[1], "Vector3") and is_kind(a[2], "Vector3") then
    -- CFrame.new(pos, lookAt)
    local p, t = DATA[a[1]], DATA[a[2]]
    local fx, fy, fz = t[1] - p[1], t[2] - p[2], t[3] - p[3]
    local m = math.sqrt(fx * fx + fy * fy + fz * fz)
    if m == 0 then return CF(p[1], p[2], p[3], IDENT) end
    fx, fy, fz = fx / m, fy / m, fz / m
    local ux, uy, uz = 0, 1, 0
    local rx, ry, rz = fy * uz - fz * uy, fz * ux - fx * uz, fx * uy - fy * ux
    local rm = math.sqrt(rx * rx + ry * ry + rz * rz)
    if rm < 1e-9 then rx, ry, rz = 1, 0, 0 else rx, ry, rz = rx / rm, ry / rm, rz / rm end
    local vx, vy, vz = ry * fz - rz * fy, rz * fx - rx * fz, rx * fy - ry * fx
    return CF(p[1], p[2], p[3], { rx, vx, -fx, ry, vy, -fy, rz, vz, -fz })
  end
  if n == 3 then
    return CF(num_arg(a[1], "new", 1), num_arg(a[2], "new", 2), num_arg(a[3], "new", 3), IDENT)
  end
  if n == 7 then -- position + quaternion
    local x, y, z, w = a[4], a[5], a[6], a[7]
    return CF(a[1], a[2], a[3], {
      1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
      2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
      2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y) })
  end
  if n == 12 then
    for i = 1, 12 do num_arg(a[i], "new", i) end
    return CF(a[1], a[2], a[3], { a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12] })
  end
  raw_error("Invalid number of arguments: " .. n, 2)
end
function CFrame.Angles(rx, ry, rz)
  return CF(0, 0, 0, matmul(matmul(rotx(num_arg(rx, "Angles", 1)), roty(num_arg(ry, "Angles", 2))), rotz(num_arg(rz, "Angles", 3))))
end
CFrame.fromEulerAnglesXYZ = CFrame.Angles
function CFrame.fromOrientation(rx, ry, rz)
  return CF(0, 0, 0, matmul(matmul(roty(num_arg(ry, "fromOrientation", 2)), rotx(num_arg(rx, "fromOrientation", 1))), rotz(num_arg(rz, "fromOrientation", 3))))
end
CFrame.fromEulerAnglesYXZ = CFrame.fromOrientation
CFrame.identity = CF(0, 0, 0, IDENT)

-- NumberRange / TweenInfo / DockWidgetPluginGuiInfo / BrickColor ---------------
local NRmt = make_type("NumberRange", {
  Min = function(d) return d[1] end, Max = function(d) return d[2] end,
}, {}, {
  __eq = function(a, b) return DATA[a][1] == DATA[b][1] and DATA[a][2] == DATA[b][2] end,
  __tostring = function(a) return num2str(DATA[a][1]) .. " " .. num2str(DATA[a][2]) end,
})
NumberRange = {
  new = function(mn, mx)
    mn = num_arg(mn, "new", 1)
    if mx == nil then mx = mn else mx = num_arg(mx, "new", 2) end
    if mx < mn then raw_error("NumberRange: invalid range", 2) end
    return datatype("NumberRange", { mn, mx }, NRmt)
  end,
}

---------------------------------------------------------------------------
-- Cross-section helpers live in one table to stay under Lua's 200-local limit
---------------------------------------------------------------------------
local X = {}

-- Enums ---------------------------------------------------------------------
do
  local ITEM = setmetatable({}, { __mode = "k" })  -- proxy -> {name, value, enum}
  local ENUMOBJ = setmetatable({}, { __mode = "k" }) -- proxy -> {name, items, byvalue, order}
  local enum_cache = {}
  local item_mt, enum_mt = {}, {}

  item_mt.__index = function(self, k)
    local st = ITEM[self]
    if k == "Name" then return st.name end
    if k == "Value" then return st.value end
    if k == "EnumType" then return st.enum end
    if k == "IsA" then
      return function(s, name) method_guard(s, ITEM, "IsA") return ENUMOBJ[ITEM[s].enum].name == name end
    end
    raw_error(tostring(k) .. " is not a valid member of \"Enum." .. ENUMOBJ[st.enum].name .. "." .. st.name .. "\"", 2)
  end
  item_mt.__newindex = function(_, k) raw_error(tostring(k) .. " cannot be assigned to", 2) end
  item_mt.__tostring = function(self) local st = ITEM[self] return "Enum." .. ENUMOBJ[st.enum].name .. "." .. st.name end

  local function get_enum(name)
    local e = enum_cache[name]
    if e then return e end
    local def = ENUMS[name]
    if not def then return nil end
    e = make_proxy("Enum", enum_mt)
    local st = { name = name, items = {}, byvalue = {}, order = {} }
    ENUMOBJ[e] = st
    for iname, v in raw_pairs(def) do
      local it = make_proxy("EnumItem", item_mt)
      ITEM[it] = { name = iname, value = v, enum = e }
      st.items[iname] = it
      st.byvalue[v] = st.byvalue[v] or it
      st.order[#st.order + 1] = it
    end
    table.sort(st.order, function(a, b) return ITEM[a].value < ITEM[b].value end)
    enum_cache[name] = e
    return e
  end
  X.get_enum = get_enum

  enum_mt.__index = function(self, k)
    local st = ENUMOBJ[self]
    local it = st.items[k]
    if it then return it end
    if k == "GetEnumItems" then
      return function(s) method_guard(s, ENUMOBJ, "GetEnumItems") return table.move(ENUMOBJ[s].order, 1, #ENUMOBJ[s].order, 1, {}) end
    end
    if k == "FromName" then return function(s, n) method_guard(s, ENUMOBJ, "FromName") return ENUMOBJ[s].items[n] end end
    if k == "FromValue" then return function(s, v) method_guard(s, ENUMOBJ, "FromValue") return ENUMOBJ[s].byvalue[v] end end
    raw_error(tostring(k) .. " is not a valid member of \"Enum." .. st.name .. "\"", 2)
  end
  enum_mt.__newindex = function(_, k) raw_error(tostring(k) .. " cannot be assigned to", 2) end
  enum_mt.__tostring = function(self) return ENUMOBJ[self].name end

  local enums_mt = {}
  Enum = make_proxy("Enums", enums_mt)
  enums_mt.__index = function(_, k)
    if k == "GetEnums" then
      return function()
        local r = {}
        for n in raw_pairs(ENUMS) do r[#r + 1] = get_enum(n) end
        return r
      end
    end
    local e = get_enum(k)
    if e then return e end
    raw_error(tostring(k) .. " is not a valid member of \"Enum\"", 2)
  end
  enums_mt.__newindex = function(_, k) raw_error(tostring(k) .. " cannot be assigned to", 2) end
  enums_mt.__tostring = function() return "Enums" end

  -- Returns the EnumItem for enum `ename` given an item, name or number; nil if invalid.
  function X.coerce_enum(ename, v)
    local e = get_enum(ename)
    if not e then return nil end
    local st = ENUMOBJ[e]
    if ITEM[v] then return ITEM[v].enum == e and v or nil end
    if raw_type(v) == "string" then return st.items[v] end
    if raw_type(v) == "number" then return st.byvalue[v] end
    return nil
  end
  function X.enum_first(ename)
    local e = get_enum(ename)
    return e and ENUMOBJ[e].order[1]
  end
  X.is_enum_item = function(v) return ITEM[v] ~= nil end
end

-- TweenInfo / DockWidgetPluginGuiInfo / BrickColor / Rect / Font ---------------
do
  local TImt = make_type("TweenInfo", {
    Time = function(d) return d[1] end, EasingStyle = function(d) return d[2] end,
    EasingDirection = function(d) return d[3] end, RepeatCount = function(d) return d[4] end,
    Reverses = function(d) return d[5] end, DelayTime = function(d) return d[6] end,
  }, {})
  TweenInfo = {
    new = function(t, style, dir, rep, rev, delay_)
      t = num_arg(t, "new", 1, 1)
      local s = style == nil and Enum.EasingStyle.Quad or X.coerce_enum("EasingStyle", style)
      if not s then arg_error("new", 2, "EnumItem", style) end
      local d = dir == nil and Enum.EasingDirection.Out or X.coerce_enum("EasingDirection", dir)
      if not d then arg_error("new", 3, "EnumItem", dir) end
      return datatype("TweenInfo", { t, s, d, num_arg(rep, "new", 4, 0), rev and true or false, num_arg(delay_, "new", 6, 0) }, TImt)
    end,
  }

  local DWmt = make_type("DockWidgetPluginGuiInfo", {
    InitialDockState = function(d) return d[1] end, InitialEnabled = function(d) return d[2] end,
    InitialEnabledShouldOverrideRestore = function(d) return d[3] end,
    FloatingXSize = function(d) return d[4] end, FloatingYSize = function(d) return d[5] end,
    MinWidth = function(d) return d[6] end, MinHeight = function(d) return d[7] end,
  }, {})
  DockWidgetPluginGuiInfo = {
    new = function(state, enabled, override, fx, fy, mw, mh)
      local s = state == nil and Enum.InitialDockState.Right or X.coerce_enum("InitialDockState", state)
      if not s then arg_error("new", 1, "EnumItem", state) end
      return datatype("DockWidgetPluginGuiInfo", { s, enabled and true or false, override and true or false,
        num_arg(fx, "new", 4, 0), num_arg(fy, "new", 5, 0), num_arg(mw, "new", 6, 0), num_arg(mh, "new", 7, 0) }, DWmt)
    end,
  }

  local PALETTE = {
    ["White"] = { 242, 243, 243 }, ["Black"] = { 27, 42, 53 }, ["Bright red"] = { 196, 40, 28 },
    ["Bright blue"] = { 13, 105, 172 }, ["Bright green"] = { 75, 151, 75 }, ["Bright yellow"] = { 245, 205, 48 },
    ["Medium stone grey"] = { 163, 162, 165 }, ["Dark stone grey"] = { 99, 95, 98 },
    ["Really red"] = { 255, 0, 0 }, ["Really blue"] = { 0, 0, 255 }, ["Lime green"] = { 0, 255, 0 },
    ["Institutional white"] = { 248, 248, 248 }, ["Really black"] = { 17, 17, 17 },
  }
  local BCmt = make_type("BrickColor", {
    Name = function(d) return d[1] end, name = function(d) return d[1] end,
    Color = function(d) local c = PALETTE[d[1]] return C3(c[1] / 255, c[2] / 255, c[3] / 255) end,
  }, {}, {
    __eq = function(a, b) return DATA[a][1] == DATA[b][1] end,
    __tostring = function(a) return DATA[a][1] end,
  })
  BrickColor = {
    new = function(name)
      if raw_type(name) ~= "string" or not PALETTE[name] then name = "Medium stone grey" end
      return datatype("BrickColor", { name }, BCmt)
    end,
  }
  X.BrickColor_new = BrickColor.new

  local Rmt = make_type("Rect", {
    Min = function(d) return V2(d[1], d[2]) end, Max = function(d) return V2(d[3], d[4]) end,
    Width = function(d) return d[3] - d[1] end, Height = function(d) return d[4] - d[2] end,
  }, {}, { __tostring = function(a) local d = DATA[a] return raw_concat({ num2str(d[1]), num2str(d[2]), num2str(d[3]), num2str(d[4]) }, ", ") end })
  Rect = { new = function(a, b, c, d) return datatype("Rect", { num_arg(a, "new", 1), num_arg(b, "new", 2), num_arg(c, "new", 3), num_arg(d, "new", 4) }, Rmt) end }
  X.Rect_new = Rect.new

  local Fmt = make_type("Font", {
    Family = function(d) return d[1] end, Weight = function(d) return d[2] end, Style = function(d) return d[3] end,
    Bold = function(d) return d[2].Value >= 600 end,
  }, {}, {
    __eq = function(a, b) local A, B = DATA[a], DATA[b] return A[1] == B[1] and A[2] == B[2] and A[3] == B[3] end,
    __tostring = function(a) local A = DATA[a]
      return "Font { Family = " .. A[1] .. ", Weight = " .. A[2].Name .. ", Style = " .. A[3].Name .. " }" end,
  })
  Font = {
    new = function(family, weight, style)
      if raw_type(family) ~= "string" then arg_error("new", 1, "string", family) end
      return datatype("Font", { family, weight or Enum.FontWeight.Regular, style or Enum.FontStyle.Normal }, Fmt)
    end,
    fromEnum = function(e)
      if not X.coerce_enum("Font", e) then arg_error("fromEnum", 1, "EnumItem", e) end
      return datatype("Font", { "rbxasset://fonts/families/" .. e.Name .. ".json", Enum.FontWeight.Regular, Enum.FontStyle.Normal }, Fmt)
    end,
  }
end

---------------------------------------------------------------------------
-- Instances (class model driven by roblox_api.lua)
---------------------------------------------------------------------------
local INST = setmetatable({}, { __mode = "k" }) -- proxy -> state
X.INST = INST
do
  local NIL = {}
  local function pnum(s)
    if s == "INF" or s == "inf" then return math.huge end
    if s == "-INF" or s == "-inf" then return -math.huge end
    return tonumber(s)
  end
  local function nums(s)
    local r = {}
    for t in string.gmatch(s, "[^,%s{}]+") do r[#r + 1] = pnum(t) or 0 end
    return r
  end
  local NUMERIC = { double = true, float = true, int = true, int64 = true }
  X.NUMERIC = NUMERIC

  local function parse_default(ty, s)
    if s == nil then return nil end
    if ty == "bool" then return s == "true" end
    if NUMERIC[ty] then return pnum(s) or 0 end
    if ty == "string" then return s end
    local pre, name = string.match(ty, "^(%a):(.+)$")
    if pre == "E" then return X.coerce_enum(name, s) end
    if pre == "C" then return nil end
    if pre == "D" then
      local n = nums(s)
      if name == "Vector2" then return V2(n[1] or 0, n[2] or 0) end
      if name == "Vector3" then return V3(n[1] or 0, n[2] or 0, n[3] or 0) end
      if name == "UDim" then return UD(n[1] or 0, n[2] or 0) end
      if name == "UDim2" then return UD2(n[1] or 0, n[2] or 0, n[3] or 0, n[4] or 0) end
      if name == "Color3" then return C3(n[1] or 0, n[2] or 0, n[3] or 0) end
      if name == "Rect" then return X.Rect_new(n[1] or 0, n[2] or 0, n[3] or 0, n[4] or 0) end
      if name == "NumberRange" then return NumberRange.new(n[1] or 0, n[2] or n[1] or 0) end
      if name == "CFrame" and #n >= 12 then
        return CF(n[1], n[2], n[3], { n[4], n[5], n[6], n[7], n[8], n[9], n[10], n[11], n[12] })
      end
      if name == "ProtectedString" or name == "ContentId" or name == "Content" or name == "BinaryString" then return s end
      return nil
    end
    return nil
  end

  -- Per-class merged info -----------------------------------------------------
  local info_cache = {}
  local function strip(n) local base = string.gsub(n, "!$", "") return base, base ~= n end
  local function class_info(name)
    local c = info_cache[name]
    if c then return c end
    local raw = CLASSES[name]
    if not raw then return nil end
    local sup = raw.super and class_info(raw.super) or nil
    c = { name = name, props = {}, funcs = {}, events = {}, callbacks = {}, isa = {}, tags = {}, chain = {} }
    if sup then
      for k, v in raw_pairs(sup.props) do c.props[k] = v end
      for k, v in raw_pairs(sup.funcs) do c.funcs[k] = v end
      for k, v in raw_pairs(sup.events) do c.events[k] = v end
      for k, v in raw_pairs(sup.callbacks) do c.callbacks[k] = v end
      for k in raw_pairs(sup.isa) do c.isa[k] = true end
      for i, v in raw_ipairs(sup.chain) do c.chain[i + 1] = v end
    end
    c.chain[1] = name
    c.isa[name] = true
    for _, t in raw_ipairs(raw.tags or {}) do c.tags[t] = true end
    for pname, p in raw_pairs(raw.props or {}) do
      local flags = p[2] or ""
      c.props[pname] = {
        type = p[1], ro = string.find(flags, "r", 1, true) ~= nil, dep = string.find(flags, "d", 1, true) ~= nil,
        raw_default = p[3], owner = name,
      }
    end
    for _, f in raw_ipairs(raw.funcs or {}) do local n, d = strip(f) c.funcs[n] = { dep = d, owner = name } end
    for _, f in raw_ipairs(raw.events or {}) do local n, d = strip(f) c.events[n] = { dep = d, owner = name } end
    for _, f in raw_ipairs(raw.callbacks or {}) do local n, d = strip(f) c.callbacks[n] = { dep = d, owner = name } end
    info_cache[name] = c
    return c
  end
  X.class_info = class_info

  local function note_deprecated(cls, member)
    local key = cls .. "." .. member
    __mock.deprecated[key] = (__mock.deprecated[key] or 0) + 1
  end

  -- Defaults ---------------------------------------------------------------
  local FALLBACK_NAME = {
    Size = function() return UD2(0, 100, 0, 100) end,
    BackgroundColor3 = function() return C3(163 / 255, 162 / 255, 165 / 255) end,
    BorderColor3 = function() return C3(27 / 255, 42 / 255, 53 / 255) end,
    BorderSizePixel = function() return 1 end,
    Visible = function() return true end, Interactable = function() return true end,
    Enabled = function() return true end, AutoLocalize = function() return true end,
    Archivable = function() return true end, ResetOnSpawn = function() return true end,
    AutoButtonColor = function() return true end, ZIndex = function() return 1 end,
    FillDirection = function() return Enum.FillDirection.Vertical end,
    HorizontalAlignment = function() return Enum.HorizontalAlignment.Left end,
    VerticalAlignment = function() return Enum.VerticalAlignment.Top end,
    SortOrder = function() return Enum.SortOrder.LayoutOrder end,
    ZIndexBehavior = function() return Enum.ZIndexBehavior.Sibling end,
    TextSize = function() return 14 end,
    TextColor3 = function() return C3(27 / 255, 42 / 255, 53 / 255) end,
  }
  local FALLBACK_TYPE = {
    bool = function() return false end, string = function() return "" end,
    ["D:Vector2"] = function() return V2(0, 0) end, ["D:Vector3"] = function() return V3(0, 0, 0) end,
    ["D:UDim"] = function() return UD(0, 0) end, ["D:UDim2"] = function() return UD2(0, 0, 0, 0) end,
    ["D:Color3"] = function() return C3(0, 0, 0) end, ["D:Rect"] = function() return X.Rect_new(0, 0, 0, 0) end,
    ["D:Font"] = function() return Font.fromEnum(Enum.Font.Legacy) end,
    ["D:CFrame"] = function() return CFrame.identity end,
  }
  local default_cache = {} -- "Class.Prop" -> value (NIL sentinel for nil)
  local function default_of(cls, pname, p)
    local key = cls .. "." .. pname
    local v = default_cache[key]
    if v ~= nil then if v == NIL then return nil end return v end
    v = parse_default(p.type, p.raw_default)
    if v == nil and p.raw_default == nil then
      local fb = FALLBACK_NAME[pname] or FALLBACK_TYPE[p.type]
      if fb then v = fb()
      elseif NUMERIC[p.type] then v = 0
      else
        local en = string.match(p.type, "^E:(.+)$")
        if en then v = X.enum_first(en) end
      end
    end
    default_cache[key] = v == nil and NIL or v
    return v
  end

  -- Type checking for assignment --------------------------------------------
  local function coerce(cls, pname, p, v)
    local ty = p.type
    if ty == "bool" then
      if raw_type(v) ~= "boolean" then return nil, "bool expected" end
      return v
    end
    if NUMERIC[ty] then
      if raw_type(v) == "string" and tonumber(v) then v = tonumber(v) end
      if raw_type(v) ~= "number" then return nil, "number expected" end
      if ty == "int" or ty == "int64" then
        if v ~= v then return nil, "number expected" end
        v = v >= 0 and math.floor(v) or math.ceil(v)
      end
      return v
    end
    if ty == "string" then
      if raw_type(v) == "number" then return num2str(v) end
      if raw_type(v) ~= "string" then return nil, "string expected" end
      return v
    end
    local pre, name = string.match(ty, "^(%a):(.+)$")
    if pre == "E" then
      local it = X.coerce_enum(name, v)
      if not it then return nil, "EnumItem of " .. name .. " expected" end
      return it
    end
    if pre == "C" then
      if v == nil then return NIL end
      if not INST[v] then return nil, name .. " expected" end
      if not INST[v].info.isa[name] then return nil, name .. " expected" end
      return v
    end
    if pre == "D" then
      if name == "ProtectedString" or name == "ContentId" or name == "Content" or name == "BinaryString" then
        if raw_type(v) ~= "string" then return nil, "string expected" end
        return v
      end
      if TYPEOF[v] ~= name then return nil, name .. " expected" end
      return v
    end
    return v
  end

  -- Instance proxy -----------------------------------------------------------
  local inst_mt = {}
  local next_id = 0
  local IMPL = {} -- IMPL[ClassName][Method] = function(self, st, ...)
  X.IMPL = IMPL
  local COMPUTED = {
    AbsoluteSize = true, AbsolutePosition = true, AbsoluteCanvasSize = true,
    AbsoluteWindowSize = true, TextBounds = true, AbsoluteContentSize = true,
  }
  X.COMPUTED = COMPUTED
  X.layout = setmetatable({}, { __mode = "k" }) -- proxy -> { prop = value }
  local func_cache = setmetatable({}, { __mode = "k" })

  local function full_name(st)
    local parts = {}
    local cur = st
    while cur do
      table.insert(parts, 1, cur.props.Name)
      cur = cur.parent and INST[cur.parent]
      if cur and cur.is_root_service then break end
    end
    return raw_concat(parts, ".")
  end

  local function new_instance(cn, force)
    local info = class_info(cn)
    if not info then raw_error("Unable to create an Instance of type \"" .. tostring(cn) .. "\"", 3) end
    if not force and (info.tags.NotCreatable or info.tags.Service) then
      raw_error("Unable to create an Instance of type \"" .. tostring(cn) .. "\"", 3)
    end
    local p = make_proxy("Instance", inst_mt)
    next_id = next_id + 1
    INST[p] = {
      class = cn, info = info, props = { Name = cn }, children = {}, parent = nil,
      signals = {}, prop_signals = {}, attrs = {}, attr_signals = {}, tags = {},
      destroyed = false, parent_locked = false, id = next_id,
    }
    __mock.stats.instances = __mock.stats.instances + 1
    return p
  end
  X.new_instance = new_instance

  local function get_signal(p, st, name)
    local s = st.signals[name]
    if not s then s = new_signal(name, p) st.signals[name] = s end
    return s
  end
  X.get_signal = function(p, name) return get_signal(p, INST[p], name) end

  local function read_prop(p, st, k)
    local pi = st.info.props[k]
    if COMPUTED[k] then
      local lay = X.layout[p]
      local v = lay and lay[k]
      if v ~= nil then return v end
      return V2(0, 0)
    end
    if k == "ContentText" then
      local t = st.props.Text
      if t == nil then t = default_of(st.class, "Text", st.info.props.Text) end
      return t
    end
    local v = st.props[k]
    if v ~= nil then if v == NIL then return nil end return v end
    return default_of(st.class, k, pi)
  end
  X.read_prop = function(p, k) return read_prop(p, INST[p], k) end

  local function prop_changed(p, st, k)
    fire(get_signal(p, st, "Changed"), k)
    local ps = st.prop_signals[k]
    if ps then fire(ps) end
  end
  X.prop_changed = function(p, k) prop_changed(p, INST[p], k) end

  -- Hierarchy ---------------------------------------------------------------
  local function is_ancestor(a, b) -- a is an ancestor of b
    local cur = INST[b].parent
    while cur do
      if cur == a then return true end
      cur = INST[cur].parent
    end
    return false
  end

  local function fire_descendants(sig_name, root, list)
    for _, d in raw_ipairs(list) do fire(get_signal(root, INST[root], sig_name), d) end
  end
  local function descendants(p, out)
    out = out or {}
    for _, c in raw_ipairs(INST[p].children) do
      out[#out + 1] = c
      descendants(c, out)
    end
    return out
  end

  local function set_parent(p, st, newp)
    if st.parent_locked then
      raw_error("The Parent property of " .. st.props.Name .. " is locked, current parent: NULL, new parent " .. (newp and INST[newp].props.Name or "nil"), 3)
    end
    if newp == st.parent then return end
    if newp ~= nil then
      if not INST[newp] then raw_error("Parent must be an Instance", 3) end
      if newp == p or is_ancestor(p, newp) then
        raw_error("Attempt to set " .. full_name(st) .. " as its own parent", 3)
      end
    end
    local old = st.parent
    local moved = { p }
    descendants(p, moved)
    if old then
      local ost = INST[old]
      for i = #ost.children, 1, -1 do if ost.children[i] == p then table.remove(ost.children, i) end end
      fire(get_signal(old, ost, "ChildRemoved"), p)
      local a = old
      while a do fire_descendants("DescendantRemoving", a, moved) a = INST[a].parent end
    end
    st.parent = newp
    if newp then
      local nst = INST[newp]
      nst.children[#nst.children + 1] = p
    end
    prop_changed(p, st, "Parent")
    for _, d in raw_ipairs(moved) do fire(get_signal(d, INST[d], "AncestryChanged"), p, newp) end
    if newp then
      fire(get_signal(newp, INST[newp], "ChildAdded"), p)
      local a = newp
      while a do fire_descendants("DescendantAdded", a, moved) a = INST[a].parent end
    end
  end

  local function destroy(p)
    local st = INST[p]
    if st.destroyed then return end
    fire(get_signal(p, st, "Destroying"))
    st.destroyed = true
    for _, c in raw_ipairs(table.move(st.children, 1, #st.children, 1, {})) do destroy(c) end
    if st.parent then set_parent(p, st, nil) end
    st.parent_locked = true
    for _, s in raw_pairs(st.signals) do disconnect_all(s) end
    for _, s in raw_pairs(st.prop_signals) do disconnect_all(s) end
    for _, s in raw_pairs(st.attr_signals) do disconnect_all(s) end
    __mock.stats.instances = __mock.stats.instances - 1
  end
  X.destroy = destroy

  local function clone(p)
    local st = INST[p]
    if st.props.Archivable == false then return nil end
    local c = new_instance(st.class, true)
    local cst = INST[c]
    for k, v in raw_pairs(st.props) do cst.props[k] = v end
    for k, v in raw_pairs(st.attrs) do cst.attrs[k] = v end
    for k, v in raw_pairs(st.tags) do cst.tags[k] = v end
    if st.source ~= nil then cst.source = st.source end
    for _, ch in raw_ipairs(st.children) do
      local cc = clone(ch)
      if cc then set_parent(cc, INST[cc], c) end
    end
    return c
  end

  local function find_child(p, name, recursive)
    for _, c in raw_ipairs(INST[p].children) do
      if INST[c].props.Name == name then return c end
    end
    if recursive then
      for _, c in raw_ipairs(INST[p].children) do
        local r = find_child(c, name, true)
        if r then return r end
      end
    end
    return nil
  end
  X.find_child = find_child

  local function str_arg(v, fname, i)
    if raw_type(v) ~= "string" then arg_error(fname, i, "string", v) end
    return v
  end

  -- Instance methods (shared by every class) ----------------------------------
  local BASE = {}
  function BASE.Destroy(p) destroy(p) end
  function BASE.Clone(p) return clone(p) end
  function BASE.ClearAllChildren(p)
    for _, c in raw_ipairs(table.move(INST[p].children, 1, #INST[p].children, 1, {})) do destroy(c) end
  end
  function BASE.FindFirstChild(p, name, recursive) return find_child(p, str_arg(name, "FindFirstChild", 1), recursive) end
  function BASE.FindFirstChildOfClass(p, cn)
    for _, c in raw_ipairs(INST[p].children) do if INST[c].class == cn then return c end end
    return nil
  end
  function BASE.FindFirstChildWhichIsA(p, cn, recursive)
    local list = recursive and descendants(p) or INST[p].children
    for _, c in raw_ipairs(list) do if INST[c].info.isa[cn] then return c end end
    return nil
  end
  function BASE.FindFirstAncestor(p, name)
    local cur = INST[p].parent
    while cur do if INST[cur].props.Name == name then return cur end cur = INST[cur].parent end
    return nil
  end
  function BASE.FindFirstAncestorOfClass(p, cn)
    local cur = INST[p].parent
    while cur do if INST[cur].class == cn then return cur end cur = INST[cur].parent end
    return nil
  end
  function BASE.FindFirstAncestorWhichIsA(p, cn)
    local cur = INST[p].parent
    while cur do if INST[cur].info.isa[cn] then return cur end cur = INST[cur].parent end
    return nil
  end
  function BASE.FindFirstDescendant(p, name)
    for _, d in raw_ipairs(descendants(p)) do if INST[d].props.Name == name then return d end end
    return nil
  end
  function BASE.GetChildren(p) return table.move(INST[p].children, 1, #INST[p].children, 1, {}) end
  function BASE.GetDescendants(p) return descendants(p) end
  function BASE.IsA(p, cn) return INST[p].info.isa[cn] == true end
  function BASE.IsDescendantOf(p, a)
    if a == nil then return false end
    return is_ancestor(a, p)
  end
  function BASE.IsAncestorOf(p, d)
    if d == nil or not INST[d] then return false end
    return is_ancestor(p, d)
  end
  function BASE.GetFullName(p) return full_name(INST[p]) end
  function BASE.GetAttribute(p, name) return INST[p].attrs[str_arg(name, "GetAttribute", 1)] end
  function BASE.GetAttributes(p) local r = {} for k, v in raw_pairs(INST[p].attrs) do r[k] = v end return r end
  function BASE.SetAttribute(p, name, v)
    str_arg(name, "SetAttribute", 1)
    local tv = typeof(v)
    if not (v == nil or tv == "string" or tv == "number" or tv == "boolean" or tv == "Vector2" or tv == "Vector3"
      or tv == "UDim" or tv == "UDim2" or tv == "Color3" or tv == "CFrame" or tv == "NumberRange" or tv == "Rect"
      or tv == "BrickColor" or tv == "EnumItem" or tv == "Font") then
      raw_error(tv .. " is not a supported attribute type", 2)
    end
    local st = INST[p]
    if st.attrs[name] == v then return end
    st.attrs[name] = v
    fire(get_signal(p, st, "AttributeChanged"), name)
    local s = st.attr_signals[name]
    if s then fire(s) end
  end
  function BASE.GetAttributeChangedSignal(p, name)
    local st = INST[p]
    local s = st.attr_signals[name]
    if not s then s = new_signal("AttributeChanged:" .. name, p) st.attr_signals[name] = s end
    return s
  end
  function BASE.AddTag(p, t) INST[p].tags[str_arg(t, "AddTag", 1)] = true end
  function BASE.RemoveTag(p, t) INST[p].tags[str_arg(t, "RemoveTag", 1)] = nil end
  function BASE.HasTag(p, t) return INST[p].tags[t] == true end
  function BASE.GetTags(p) local r = {} for k in raw_pairs(INST[p].tags) do r[#r + 1] = k end table.sort(r) return r end
  function BASE.WaitForChild(p, name, timeout)
    str_arg(name, "WaitForChild", 1)
    local c = find_child(p, name)
    if c then return c end
    local co, main = raw_running()
    if main then raw_error("mock: WaitForChild would block the main thread", 2) end
    local deadline = timeout and (__mock.now + timeout) or nil
    local warned = false
    while true do
      task.wait()
      c = find_child(p, name)
      if c then return c end
      if deadline and __mock.now >= deadline then return nil end
      if not timeout and not warned and __mock.now >= 5 then
        warned = true
        warn("Infinite yield possible on '" .. full_name(INST[p]) .. ":WaitForChild(\"" .. name .. "\")'")
      end
    end
  end
  function BASE.GetPropertyChangedSignal(p, pname)
    local st = INST[p]
    if raw_type(pname) ~= "string" or not st.info.props[pname] then
      raw_error(tostring(pname) .. " is not a valid property name.", 2)
    end
    local s = st.prop_signals[pname]
    if not s then s = new_signal("Changed:" .. pname, p) st.prop_signals[pname] = s end
    return s
  end
  function BASE.ResetPropertyToDefault(p, pname)
    local st = INST[p]
    if st.props[pname] ~= nil then st.props[pname] = nil prop_changed(p, st, pname) end
  end
  IMPL.Instance = BASE
  IMPL.Object = BASE

  inst_mt.__index = function(p, k)
    local st = INST[p]
    if k == "Parent" then return st.parent end
    if k == "ClassName" then return st.class end
    local info = st.info
    local pi = info.props[k]
    if pi then
      if pi.dep then note_deprecated(st.class, k) end
      return read_prop(p, st, k)
    end
    local fi = info.funcs[k]
    if fi then
      if fi.dep then note_deprecated(st.class, k) end
      local cache = func_cache[p]
      if not cache then cache = {} func_cache[p] = cache end
      local f = cache[k]
      if f then return f end
      local impl
      for _, cn in raw_ipairs(info.chain) do
        local t = IMPL[cn]
        if t and t[k] then impl = t[k] break end
      end
      if not impl and BASE[k] then impl = BASE[k] end
      local cls = st.class
      f = function(self, ...)
        if not INST[self] then raw_error("Expected ':' not '.' calling member function " .. k, 2) end
        if not impl then raw_error("mock: " .. cls .. ":" .. k .. " not implemented", 2) end
        return impl(self, ...)
      end
      cache[k] = f
      return f
    end
    local ei = info.events[k]
    if ei then
      if ei.dep then note_deprecated(st.class, k) end
      return get_signal(p, st, k)
    end
    if info.callbacks[k] then return st.props[k] end
    local c = find_child(p, k)
    if c then return c end
    raw_error(tostring(k) .. " is not a valid member of " .. st.class .. " \"" .. full_name(st) .. "\"", 2)
  end

  inst_mt.__newindex = function(p, k, v)
    local st = INST[p]
    if k == "Parent" then set_parent(p, st, v) return end
    local info = st.info
    if info.callbacks[k] then
      if v ~= nil and raw_type(v) ~= "function" then raw_error(k .. " must be a function", 2) end
      st.props[k] = v
      return
    end
    local pi = info.props[k]
    if not pi then
      raw_error(tostring(k) .. " is not a valid member of " .. st.class .. " \"" .. full_name(st) .. "\"", 2)
    end
    if pi.ro or COMPUTED[k] then
      raw_error("Unable to assign property " .. k .. ". Property is read only", 2)
    end
    if pi.dep then note_deprecated(st.class, k) end
    if k == "Name" and v == nil then raw_error("Name cannot be nil", 2) end
    local cv, err = coerce(st.class, k, pi, v)
    if cv == nil then
      raw_error("Unable to assign property " .. k .. ". " .. err .. ", got " .. typeof(v), 2)
    end
    local old = read_prop(p, st, k)
    st.props[k] = cv
    local newv = cv == NIL and nil or cv
    if old ~= newv then
      prop_changed(p, st, k)
      if k == "Text" and st.info.props.ContentText then
        local ps = st.prop_signals.ContentText
        if ps then fire(ps) end
      end
    end
  end
  inst_mt.__tostring = function(p) return INST[p].props.Name end
  inst_mt.__eq = nil

  Instance = {
    new = function(cn, parent)
      if raw_type(cn) ~= "string" then arg_error("new", 1, "string", cn) end
      local p = new_instance(cn, false)
      if parent ~= nil then p.Parent = parent end
      return p
    end,
  }
  X.NIL = NIL
  X.descendants = descendants
  X.set_parent = function(p, newp) set_parent(p, INST[p], newp) end

  -- Class-specific methods ---------------------------------------------------
  IMPL.BindableEvent = {
    Fire = function(p, ...) fire(get_signal(p, INST[p], "Event"), ...) end,
  }
  IMPL.KeyframeSequence = {
    AddKeyframe = function(p, kf) kf.Parent = p end,
    GetKeyframes = function(p)
      local r = {}
      for _, c in raw_ipairs(INST[p].children) do if INST[c].info.isa.Keyframe then r[#r + 1] = c end end
      return r
    end,
    RemoveKeyframe = function(p, kf) if kf.Parent == p then kf.Parent = nil end end,
  }
  local function poses_of(p)
    local r = {}
    for _, c in raw_ipairs(INST[p].children) do if INST[c].info.isa.PoseBase then r[#r + 1] = c end end
    return r
  end
  IMPL.Keyframe = {
    AddPose = function(p, pose) pose.Parent = p end,
    GetPoses = poses_of,
    RemovePose = function(p, pose) if pose.Parent == p then pose.Parent = nil end end,
    AddMarker = function(p, m) m.Parent = p end,
    GetMarkers = function(p)
      local r = {}
      for _, c in raw_ipairs(INST[p].children) do if INST[c].class == "KeyframeMarker" then r[#r + 1] = c end end
      return r
    end,
    RemoveMarker = function(p, m) if m.Parent == p then m.Parent = nil end end,
  }
  IMPL.Pose = {
    AddSubPose = function(p, pose) pose.Parent = p end,
    GetSubPoses = poses_of,
    RemoveSubPose = function(p, pose) if pose.Parent == p then pose.Parent = nil end end,
  }
  IMPL.TextBox = {
    CaptureFocus = function(p)
      local cur = __mock.focused
      if cur == p then return end
      if cur then IMPL.TextBox.ReleaseFocus(cur) end
      __mock.focused = p
      fire(get_signal(p, INST[p], "Focused"))
    end,
    ReleaseFocus = function(p, submitted)
      if __mock.focused ~= p then return end
      __mock.focused = nil
      fire(get_signal(p, INST[p], "FocusLost"), submitted and true or false, nil)
    end,
    IsFocused = function(p) return __mock.focused == p end,
  }
end

---------------------------------------------------------------------------
-- Host hooks: services, the `plugin` global, require and the plugin tree.
-- These model what the plugin touches; they are NOT Studio. Notably the
-- ChangeHistoryService model tracks instances added/removed between
-- TryBeginRecording and FinishRecording, not property edits.
---------------------------------------------------------------------------
do
  local IMPL, INST = X.IMPL, X.INST
  local function new(cn) return X.new_instance(cn, true) end
  local function set_ro(p, k, v) INST[p].props[k] = v end

  -- JSON ----------------------------------------------------------------
  local function esc(s)
    return '"' .. string.gsub(s, '[%c"\\]', function(c)
      local m = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
      return m[c] or string.format("\\u%04x", string.byte(c))
    end) .. '"'
  end
  local function encode(v, seen)
    local t = raw_type(v)
    if v == nil then return "null" end
    if t == "boolean" then return v and "true" or "false" end
    if t == "number" then
      if v ~= v or v == math.huge or v == -math.huge then return "null" end
      if math.type(v) == "integer" or v == math.floor(v) and math.abs(v) < 2^53 then return string.format("%d", v) end
      return string.format("%.17g", v)
    end
    if t == "string" then return esc(v) end
    if t == "table" then
      seen = seen or {}
      if seen[v] then raw_error("tables cannot be cyclic", 2) end
      seen[v] = true
      local n = #v
      local parts = {}
      local is_arr = n > 0 or raw_next(v) == nil
      if is_arr then for k in raw_pairs(v) do if math.type(k) ~= "integer" or k < 1 or k > n then is_arr = false break end end end
      if is_arr then
        for i = 1, n do parts[i] = encode(v[i], seen) end
        seen[v] = nil
        return "[" .. table.concat(parts, ",") .. "]"
      end
      local keys = {}
      for k in raw_pairs(v) do keys[#keys + 1] = k end
      table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
      for _, k in raw_ipairs(keys) do parts[#parts + 1] = esc(tostring(k)) .. ":" .. encode(v[k], seen) end
      seen[v] = nil
      return "{" .. table.concat(parts, ",") .. "}"
    end
    return esc(tostring(v))
  end
  local function decode(s)
    local i = 1
    local function ws() i = string.find(s, "[^ \t\r\n]", i) or #s + 1 end
    local function fail(m) raw_error("Can't parse JSON: " .. m .. " at " .. i, 0) end
    local val
    local function str()
      i = i + 1
      local buf = {}
      while true do
        local c = string.sub(s, i, i)
        if c == "" then fail("unterminated string") end
        if c == '"' then i = i + 1 break end
        if c == "\\" then
          local e = string.sub(s, i + 1, i + 1)
          local m = { n = "\n", t = "\t", r = "\r", b = "\b", f = "\f", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }
          if e == "u" then buf[#buf + 1] = utf8.char(tonumber(string.sub(s, i + 2, i + 5), 16)) i = i + 6
          elseif m[e] then buf[#buf + 1] = m[e] i = i + 2
          else fail("bad escape") end
        else buf[#buf + 1] = c i = i + 1 end
      end
      return table.concat(buf)
    end
    function val()
      ws()
      local c = string.sub(s, i, i)
      if c == "{" then
        i = i + 1 local r = {} ws()
        if string.sub(s, i, i) == "}" then i = i + 1 return r end
        while true do
          ws() if string.sub(s, i, i) ~= '"' then fail("expected key") end
          local k = str() ws()
          if string.sub(s, i, i) ~= ":" then fail("expected ':'") end
          i = i + 1 r[k] = val() ws()
          local d = string.sub(s, i, i) i = i + 1
          if d == "}" then return r elseif d ~= "," then fail("expected ',' or '}'") end
        end
      elseif c == "[" then
        i = i + 1 local r = {} ws()
        if string.sub(s, i, i) == "]" then i = i + 1 return r end
        while true do
          r[#r + 1] = val() ws()
          local d = string.sub(s, i, i) i = i + 1
          if d == "]" then return r elseif d ~= "," then fail("expected ',' or ']'") end
        end
      elseif c == '"' then return str()
      elseif string.sub(s, i, i + 3) == "true" then i = i + 4 return true
      elseif string.sub(s, i, i + 4) == "false" then i = i + 5 return false
      elseif string.sub(s, i, i + 3) == "null" then i = i + 4 return nil
      else
        local num = string.match(s, "^-?%d+%.?%d*[eE]?[-+]?%d*", i)
        if not num or num == "" then fail("unexpected character") end
        i = i + #num
        return tonumber(num)
      end
    end
    if raw_type(s) ~= "string" then raw_error("Can't parse JSON", 2) end
    local r = val() ws()
    if i <= #s then fail("trailing characters") end
    return r
  end
  __json_encode = function(v) return encode(v) end
  __mock.json_decode = decode

  -- game / services ------------------------------------------------------
  local game_ = new("DataModel")
  INST[game_].is_root_service = true
  set_ro(game_, "Name", "Game")
  local services = {}
  local function get_service(_, name)
    local s = services[name]
    if s then return s end
    local info = X.class_info(name)
    if not info or not info.tags.Service then raw_error("'" .. tostring(name) .. "' is not a valid Service name", 2) end
    s = new(name)
    INST[s].is_root_service = true
    services[name] = s
    X.set_parent(s, game_)
    if name == "StarterPlayer" then
      -- Studio places these two containers in every new place
      for _, cn in ipairs({ "StarterPlayerScripts", "StarterCharacterScripts" }) do
        local c = new(cn); set_ro(c, "Name", cn); X.set_parent(c, s)
      end
    end
    return s
  end
  IMPL.ServiceProvider = {
    GetService = get_service,
    FindService = function(_, name) return services[name] end,
  }
  IMPL.ServiceProvider.getService = IMPL.ServiceProvider.GetService
  IMPL.ServiceProvider.service = IMPL.ServiceProvider.GetService
  game = game_
  Game = game_
  workspace = get_service(game_, "Workspace")
  Workspace = workspace

  -- ChangeHistoryService -------------------------------------------------
  __mock.history = {}
  local undo_stack, redo_stack, recordings, rec_seq = {}, {}, {}, 0
  local function snapshot()
    local set = {}
    for _, d in raw_ipairs(X.descendants(game_)) do set[d] = INST[d].parent end
    return set
  end
  local kfs_n = 0
  IMPL.KeyframeSequenceProvider = {
    RegisterKeyframeSequence = function(_, seq)
      if TYPEOF[seq] ~= "Instance" or INST[seq].class ~= "KeyframeSequence" then
        error("mock: RegisterKeyframeSequence expects a KeyframeSequence", 2)
      end
      kfs_n = kfs_n + 1
      return "rbxassetid://mock-kfs-" .. kfs_n
    end,
  }
  IMPL.ChangeHistoryService = {
    TryBeginRecording = function(_, name, display)
      rec_seq = rec_seq + 1
      local id = "rec" .. rec_seq
      recordings[id] = { name = name, snap = snapshot() }
      return id
    end,
    IsRecordingInProgress = function(_, id)
      if id == nil then return next(recordings) ~= nil end
      return recordings[id] ~= nil
    end,
    FinishRecording = function(_, id, op)
      local r = recordings[id]
      if not r then raw_error("FinishRecording: unknown recording " .. tostring(id), 2) end
      recordings[id] = nil
      local now = snapshot()
      local added, removed = {}, {}
      for d, par in raw_pairs(now) do if r.snap[d] == nil then added[#added + 1] = { d, par } end end
      for d, par in raw_pairs(r.snap) do if now[d] == nil then removed[#removed + 1] = { d, par } end end
      local opname = op and op.Name or tostring(op)
      if opname == "Cancel" then
        for _, a in raw_ipairs(added) do if not INST[a[1]].destroyed then a[1].Parent = nil end end
        for _, a in raw_ipairs(removed) do a[1].Parent = a[2] end
      elseif opname == "Commit" or opname == "Append" then
        if #added > 0 or #removed > 0 then
          undo_stack[#undo_stack + 1] = { name = r.name, added = added, removed = removed }
          redo_stack = {}
        end
      end
      __mock.history[#__mock.history + 1] = { name = r.name, op = opname, added = #added, removed = #removed }
    end,
    SetWaypoint = function(_, name)
      __mock.history[#__mock.history + 1] = { name = name, op = "SetWaypoint!", added = 0, removed = 0 }
    end,
    GetCanUndo = function() return #undo_stack > 0, undo_stack[#undo_stack] and undo_stack[#undo_stack].name end,
    GetCanRedo = function() return #redo_stack > 0, redo_stack[#redo_stack] and redo_stack[#redo_stack].name end,
    Undo = function()
      local w = table.remove(undo_stack)
      if not w then return end
      -- reparent the root-most first so children follow their parents
      for _, a in raw_ipairs(w.added) do a[1].Parent = nil end
      for _, a in raw_ipairs(w.removed) do a[1].Parent = a[2] end
      redo_stack[#redo_stack + 1] = w
    end,
    Redo = function()
      local w = table.remove(redo_stack)
      if not w then return end
      for _, a in raw_ipairs(w.added) do a[1].Parent = a[2] end
      for _, a in raw_ipairs(w.removed) do a[1].Parent = nil end
      undo_stack[#undo_stack + 1] = w
    end,
    ResetWaypoints = function() undo_stack, redo_stack = {}, {} end,
  }

  -- HttpService ----------------------------------------------------------
  local guid = 0
  IMPL.HttpService = {
    JSONEncode = function(_, v) return encode(v) end,
    JSONDecode = function(_, s) return decode(s) end,
    GenerateGUID = function(_, braces)
      guid = guid + 1
      local g = string.format("00000000-0000-4000-8000-%012d", guid)
      if braces == false then return g end
      return "{" .. g .. "}"
    end,
    UrlEncode = function(_, s)
      return (string.gsub(s, "[^%w%-_%.~]", function(c) return string.format("%%%02X", string.byte(c)) end))
    end,
    RequestAsync = function(_, req)
      __mock.http_log[#__mock.http_log + 1] = req
      if (__mock.http_latency or 0) > 0 then task.wait(__mock.http_latency) end
      local h = __mock.http_handler
      if not h then raw_error("HttpError: ConnectFail", 2) end
      local res = h(req)
      if raw_type(res) == "string" then raw_error(res, 2) end
      return res
    end,
  }

  -- Text / Tween / Input / Collection / Selection / ScriptEditor ---------
  IMPL.TextService = {
    GetTextSize = function(_, text, size, font, frame)
      local lines, longest = 0, 0
      for l in string.gmatch(text .. "\n", "(.-)\n") do lines = lines + 1 if #l > longest then longest = #l end end
      local w = longest * size * 0.5
      if frame and frame.X < w then
        local per = math.max(1, math.floor(frame.X / (size * 0.5)))
        lines = 0
        for l in string.gmatch(text .. "\n", "(.-)\n") do lines = lines + math.max(1, math.ceil(#l / per)) end
        w = frame.X
      end
      return V2(w, size * math.max(lines, 1))
    end,
  }
  IMPL.TweenService = {
    Create = function(_, inst, info, goals)
      local tw = new("Tween")
      set_ro(tw, "Instance", inst)
      set_ro(tw, "TweenInfo", info)
      INST[tw].goals = goals
      return tw
    end,
  }
  IMPL.TweenBase = {
    Play = function(tw)
      local st = INST[tw]
      local inst = st.props.Instance
      for k, v in raw_pairs(st.goals or {}) do inst[k] = v end
      st.props.PlaybackState = Enum.PlaybackState.Completed
      task.defer(function() fire(X.get_signal(tw, "Completed"), Enum.PlaybackState.Completed) end)
    end,
    Cancel = function(tw)
      INST[tw].props.PlaybackState = Enum.PlaybackState.Cancelled
      task.defer(function() fire(X.get_signal(tw, "Completed"), Enum.PlaybackState.Cancelled) end)
    end,
    Pause = function(tw) INST[tw].props.PlaybackState = Enum.PlaybackState.Paused end,
  }
  IMPL.UserInputService = {
    IsKeyDown = function(_, kc) return (__mock.keys_down or {})[kc.Name] == true end,
    GetFocusedTextBox = function() return __mock.focused end,
  }
  IMPL.CollectionService = {
    AddTag = function(_, p, tag)
      if not INST[p].tags[tag] then INST[p].tags[tag] = true fire(X.get_signal(services.CollectionService, "TagAdded"), tag) end
    end,
    RemoveTag = function(_, p, tag) INST[p].tags[tag] = nil end,
    HasTag = function(_, p, tag) return INST[p].tags[tag] == true end,
    GetTags = function(_, p)
      local r = {} for t in raw_pairs(INST[p].tags) do r[#r + 1] = t end table.sort(r) return r
    end,
    GetTagged = function(_, tag)
      local r = {}
      for _, d in raw_ipairs(X.descendants(game_)) do if INST[d].tags[tag] then r[#r + 1] = d end end
      return r
    end,
  }
  __mock.selection = {}
  IMPL.Selection = {
    Get = function() return table.clone(__mock.selection) end,
    Set = function(sel, list)
      __mock.selection = table.clone(list)
      fire(X.get_signal(sel, "SelectionChanged"))
    end,
    Add = function(sel, list)
      for _, v in raw_ipairs(list) do __mock.selection[#__mock.selection + 1] = v end
      fire(X.get_signal(sel, "SelectionChanged"))
    end,
    Remove = function(sel, list)
      local drop = {} for _, v in raw_ipairs(list) do drop[v] = true end
      local keep = {} for _, v in raw_ipairs(__mock.selection) do if not drop[v] then keep[#keep + 1] = v end end
      __mock.selection = keep
      fire(X.get_signal(sel, "SelectionChanged"))
    end,
  }
  IMPL.ScriptEditorService = {
    GetEditorSource = function(_, s)
      local b = __mock.editor_buffers[s]
      if b ~= nil then return b end
      return s.Source
    end,
    UpdateSourceAsync = function(_, s, fn)
      local old = IMPL.ScriptEditorService.GetEditorSource(nil, s)
      local new_src = fn(old)
      if new_src == nil then raw_error("UpdateSourceAsync: callback returned nil", 2) end
      if __mock.editor_buffers[s] ~= nil then __mock.editor_buffers[s] = new_src end
      s.Source = new_src
    end,
  }

  -- settings().Studio ----------------------------------------------------
  local studio = new("Studio")
  local theme = new("StudioTheme")
  set_ro(theme, "Name", "Dark")
  set_ro(studio, "Theme", theme)
  IMPL.StudioTheme = {
    GetColor = function(_, guide, modifier)
      local n = 0
      for c in string.gmatch(tostring(guide) .. tostring(modifier), ".") do n = (n * 31 + string.byte(c)) % 1000003 end
      return C3((n % 97) / 255 + 0.1, (n % 89) / 255 + 0.1, (n % 83) / 255 + 0.1)
    end,
  }
  local settings_obj = { Studio = studio }
  settings = function() return settings_obj end
  __mock.studio = studio

  -- plugin -----------------------------------------------------------------
  __mock.toolbars, __mock.widgets = {}, {}
  local plugin_ = new("Plugin")
  set_ro(plugin_, "Name", "GodmodePlugin")
  local function make_widget(id, info)
    local w = new("DockWidgetPluginGui")
    set_ro(w, "Name", id)
    w.Enabled = info.InitialEnabled
    X.layout[w] = { AbsoluteSize = V2(__mock.widget_size[1], __mock.widget_size[2]) }
    __mock.widgets[#__mock.widgets + 1] = w
    local restored = __mock.restored_widget_state[id]
    if restored ~= nil then
      set_ro(w, "HostWidgetWasRestored", true)
      if not info.InitialEnabledShouldOverrideRestore then w.Enabled = restored end
    end
    return w
  end
  IMPL.Plugin = {
    CreateToolbar = function(_, name)
      local tb = new("PluginToolbar")
      set_ro(tb, "Name", name)
      __mock.toolbars[#__mock.toolbars + 1] = tb
      return tb
    end,
    CreateDockWidgetPluginGui = function(_, id, info) return make_widget(id, info) end,
    CreateDockWidgetPluginGuiAsync = function(_, id, info) return make_widget(id, info) end,
    GetSetting = function(_, k) return __mock.settings[k] end,
    SetSetting = function(_, k, v) __mock.settings[k] = v end,
    Activate = function() end,
    Deactivate = function() end,
  }
  IMPL.PluginToolbar = {
    CreateButton = function(tb, id, tooltip, icon, text)
      local b = new("PluginToolbarButton")
      set_ro(b, "Name", id)
      INST[b].tooltip, INST[b].text = tooltip, text
      b.Icon = icon or ""
      INST[b].parent_locked = false
      X.set_parent(b, tb)
      return b
    end,
  }
  IMPL.PluginToolbarButton = {
    SetActive = function(b, v) INST[b].active = v and true or false end,
  }
  __mock.click = function(b) fire(X.get_signal(b, "Click")) end
  __mock.button_active = function(b) return INST[b].active end
  __mock.unload = function() fire(X.get_signal(plugin_, "Unloading")) end
  plugin = plugin_

  -- require / module tree --------------------------------------------------
  local sources, loaded, loading = {}, {}, {}
  __mock.module_env = {}
  function __register_module(name, src) sources[name] = src end
  local function load_module(ms)
    local name = INST[ms] and INST[ms].props.Name
    local src = INST[ms] and INST[ms].module_name and sources[INST[ms].module_name]
    if not src then raw_error("require: no source for " .. tostring(name), 3) end
    if loaded[ms] ~= nil then return loaded[ms] end
    if loading[ms] then raw_error("Requested module was required recursively", 3) end
    loading[ms] = true
    local env = setmetatable({ script = ms }, { __index = _G, __newindex = function(t, k, v) rawset(t, k, v) end })
    __mock.module_env[name] = env
    local fn, err = load(src, "@" .. INST[ms].module_name, "t", env)
    if not fn then loading[ms] = nil raw_error(err, 0) end
    local ok, res = pcall(fn)
    loading[ms] = nil
    if not ok then raw_error(res, 0) end
    if res == nil then raw_error("Module code did not return exactly one value", 3) end
    loaded[ms] = res
    return res
  end
  function require(ms)
    if TYPEOF[ms] ~= "Instance" or INST[ms].class ~= "ModuleScript" then
      raw_error("Attempted to call require with invalid argument(s).", 2)
    end
    return load_module(ms)
  end
  function __build_plugin_tree(root_name)
    local root = new("Script")
    set_ro(root, "Name", root_name)
    __mock.plugin_root = root
    local main_src
    local names = {}
    for n in raw_pairs(sources) do names[#names + 1] = n end
    table.sort(names)
    for _, n in raw_ipairs(names) do
      if n == "Main" then main_src = sources[n]
      else
        local ms = new("ModuleScript")
        set_ro(ms, "Name", n)
        INST[ms].module_name = n
        X.set_parent(ms, root)
      end
    end
    if not main_src then raw_error("no Main module", 0) end
    local env = setmetatable({ script = root }, { __index = _G })
    __mock.module_env.Main = env
    local fn, err = load(main_src, "@Main", "t", env)
    if not fn then raw_error(err, 0) end
    resume(coroutine.create(fn))
    run_ready()
  end
end
