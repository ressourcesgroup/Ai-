"""Run Lua 5.4 code in-process through the system liblua (ctypes), for plugin tests.

This is the execution half of the plugin test harness: ``to_lua.translate`` turns
the plugin's Luau into Lua 5.4, and this module runs it inside a real Lua VM
together with ``roblox_mock.lua`` (a strict, hand-written mock of the Roblox API
surface the plugin uses). It is NOT Roblox Studio and NOT the Luau VM: the
harness proves the plugin's own logic runs and calls the API the way it should,
against our mock's model of that API.

``available()`` is False when liblua 5.4 is not installed; tests skip then.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
from pathlib import Path
from typing import Any

from .to_lua import translate

_LIB_NAMES = ("liblua5.4.so.0", "liblua5.4.so", "liblua.so.5.4", "liblua-5.4.so")
_lib: ctypes.CDLL | None = None
_lib_error: str | None = None

LUA_OK = 0
MOCK_PATH = Path(__file__).with_name("roblox_mock.lua")


def _load_lib() -> ctypes.CDLL | None:
    global _lib, _lib_error
    if _lib is not None or _lib_error is not None:
        return _lib
    names = list(_LIB_NAMES)
    found = ctypes.util.find_library("lua5.4")
    if found:
        names.insert(0, found)
    for name in names:
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.luaL_newstate.restype = ctypes.c_void_p
        lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        lib.luaL_loadbufferx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p]
        lib.luaL_loadbufferx.restype = ctypes.c_int
        lib.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        lib.lua_pcallk.restype = ctypes.c_int
        lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        lib.lua_tolstring.restype = ctypes.c_void_p
        lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.lua_gettop.argtypes = [ctypes.c_void_p]
        lib.lua_gettop.restype = ctypes.c_int
        lib.lua_pushlstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        lib.lua_pushlstring.restype = ctypes.c_void_p
        lib.lua_setglobal.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.lua_close.argtypes = [ctypes.c_void_p]
        _lib = lib
        return lib
    _lib_error = "liblua 5.4 not found"
    return None


def available() -> bool:
    return _load_lib() is not None


class LuaError(RuntimeError):
    pass


class LuaState:
    """One Lua 5.4 state with the standard libraries opened."""

    def __init__(self) -> None:
        lib = _load_lib()
        if lib is None:
            raise LuaError(_lib_error or "liblua 5.4 unavailable")
        self._lib = lib
        self._L = lib.luaL_newstate()
        if not self._L:
            raise LuaError("luaL_newstate failed")
        lib.luaL_openlibs(self._L)

    def close(self) -> None:
        if self._L:
            self._lib.lua_close(self._L)
            self._L = None

    def __enter__(self) -> "LuaState":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _top_string(self) -> str | None:
        size = ctypes.c_size_t(0)
        ptr = self._lib.lua_tolstring(self._L, -1, ctypes.byref(size))
        if not ptr:
            return None
        return ctypes.string_at(ptr, size.value).decode("utf-8", errors="replace")

    def set_string(self, name: str, value: str) -> None:
        data = value.encode("utf-8")
        self._lib.lua_pushlstring(self._L, data, len(data))
        self._lib.lua_setglobal(self._L, name.encode("utf-8"))

    def run(self, code: str, chunkname: str = "=harness") -> str | None:
        """Execute `code`; return its first result converted with tostring (or None)."""
        data = code.encode("utf-8")
        lib, L = self._lib, self._L
        base = lib.lua_gettop(L)
        status = lib.luaL_loadbufferx(L, data, len(data), chunkname.encode("utf-8"), b"t")
        if status != LUA_OK:
            msg = self._top_string()
            lib.lua_settop(L, base)
            raise LuaError(f"load error: {msg}")
        status = lib.lua_pcallk(L, 0, 1, 0, None, None)
        result = self._top_string()
        lib.lua_settop(L, base)
        if status != LUA_OK:
            raise LuaError(result or f"lua error status {status}")
        return result


class PluginRuntime:
    """The plugin's modules, translated to Lua 5.4, loaded into a fresh mock Studio."""

    def __init__(self, src_dir: Path, plugin_script: str = "GodmodePlugin") -> None:
        self.src_dir = Path(src_dir)
        self.lua = LuaState()
        self.lua.run(MOCK_PATH.read_text(encoding="utf-8"), "@roblox_mock.lua")
        self.translated: dict[str, str] = {}
        for f in sorted(self.src_dir.glob("*.luau")):
            name = f.stem
            lua_src = translate(f.read_text(encoding="utf-8"))
            self.translated[name] = lua_src
            self.lua.set_string("__pending_source", lua_src)
            self.lua.run(f"__register_module({json.dumps(name)}, __pending_source)")
        self.lua.run(f"__build_plugin_tree({json.dumps(plugin_script)})")

    def close(self) -> None:
        self.lua.close()

    def __enter__(self) -> "PluginRuntime":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def run(self, code: str) -> str | None:
        return self.lua.run(code, "=test")

    def json(self, code: str) -> Any:
        """Run `code` (which must return a value) and decode it via the mock's JSONEncode."""
        out = self.lua.run(f"return __json_encode((function() {code} end)())", "=test")
        return json.loads(out) if out is not None else None
