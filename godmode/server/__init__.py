"""GODMODE local inference/agent server for the Roblox Studio plugin (docs/PLUGIN_PROTOCOL.md)."""

from .app import main, make_server, serve_in_thread
from .model_backend import ModelBackend, render_prompt
from .retrieval import BM25Index, Retriever
from .service import PROTOCOL_VERSION, VERSION, BadRequest, GodmodeService

__all__ = ["main", "make_server", "serve_in_thread", "ModelBackend", "render_prompt", "BM25Index", "Retriever",
           "PROTOCOL_VERSION", "VERSION", "BadRequest", "GodmodeService"]
