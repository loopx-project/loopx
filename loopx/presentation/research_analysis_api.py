"""Loopback transport for the toolkit's existing owner-scoped assessment store.

The threaded Chat request owns the synchronous provider invocation. Navigation,
reads and disconnected clients do not create or retry jobs; cancellation still
belongs to the provider. There is no second scheduler or assessment authority.
"""
from urllib.parse import parse_qs, urlparse

from ..capabilities.benchmark_toolkit import trajectory_assessment_store as store
from ..feedback import validate_goal_id
from .goal_source_routing import goal_source_catalog, linked_goal_source

RESEARCH_ANALYSIS_PATH = "/api/chat/research-analysis"


class ResearchAnalysisRequestMixin:
    def _research_analysis_query(self, goal_id=None):
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        namespace = getattr(self.server, "analysis_source_namespace", None)
        if namespace is None:
            selected = goal_id or (query.get("goal_id") or [None])[0]
            resolved = linked_goal_source(self.server, selected) if selected else None
            if resolved is not None:
                namespace = resolved[1].source.namespace
        if namespace is not None and query.pop("source_namespace", []) != [namespace]:
            raise ValueError("Analysis source identity changed")
        return query

    def _research_analysis_verify_source_goal(self, runtime, goal_id, pack_id, pack=None):
        linked = linked_goal_source(self.server, goal_id)
        if getattr(self.server, "analysis_source_namespace", None) is not None or linked is not None:
            if pack is None:
                pack = store.inspect_pack(runtime_root=runtime, owner_goal_id=goal_id, pack_id=pack_id)
            if pack["scope"]["goal_id"] != goal_id:
                raise ValueError("The analysis input belongs to a different source Goal")

    def _research_analysis_scope(self, goal_id):
        validate_goal_id(goal_id)
        selected = getattr(self.server, "selected_goal_id", None)
        if selected and selected != goal_id:
            raise ValueError("Analysis is outside this Goal workspace")
        import subprocess

        catalog = goal_source_catalog(self.server)
        if catalog is not None and catalog.resolve(goal_id) is not None:
            if not self._require_loopback_origin():
                return None
            values = parse_qs(urlparse(self.path).query, keep_blank_values=True).get("source_namespace", [])
            if len(values) != 1:
                raise ValueError("Keep the original source identity")
            try:
                return catalog.analysis_scope(goal_id, namespace=values[0], fresh=self.command == "POST")
            except (OSError, subprocess.SubprocessError):
                self._send_error("The original Goal is unavailable; refresh to retry.", status=503)
                return None
        # Reuse the existing registry, loopback and browser-origin checks.
        return self._goal_result_scope(goal_id)

    def _research_analysis_read(self):
        try:
            query = self._research_analysis_query()
            citation = urlparse(self.path).path.endswith("/citation")
            required = {"goal_id", "pack_id", "analysis_key", "source_id", "first_line", "last_line"} if citation else {"goal_id"}
            allowed = required if citation else {"goal_id", "pack_id"}
            if not required <= query.keys() or query.keys() - allowed or any(len(v) != 1 for v in query.values()):
                raise ValueError("Use the registered Goal and opaque pack identifiers")
            values = {key: value[0] for key, value in query.items()}
            goal_id = values.pop("goal_id")
            runtime = self._research_analysis_scope(goal_id)
            if runtime is None:
                return
            if citation:
                self._research_analysis_verify_source_goal(runtime, goal_id, values["pack_id"])
                first = int(values.pop("first_line"))
                last = int(values.pop("last_line"))
                if first < 1 or last < first:
                    raise ValueError("Invalid citation range")
                result = store.read_citation(runtime_root=runtime, owner_goal_id=goal_id,
                    first_line=first, last_line=last, **values)
            else:
                catalog = store.discover(runtime_root=runtime, owner_goal_id=goal_id)
                pack_id = values.get("pack_id") or catalog["default_pack_id"]
                pack = store.inspect_pack(runtime_root=runtime, owner_goal_id=goal_id, pack_id=pack_id) if pack_id else None
                if pack_id:
                    self._research_analysis_verify_source_goal(runtime, goal_id, pack_id, pack)
                result = {"ok": True, "catalog": catalog, "pack": pack}
                if hasattr(self.server, "analysis_execution_enabled"):
                    result["execution_enabled"] = self.server.analysis_execution_enabled
                elif linked_goal_source(self.server, goal_id) is not None:
                    result["execution_enabled"] = True
            self._send_json(result)
        except store.PackUnavailable as exc:
            self._send_error("The registered analysis source is unavailable.", status=409, error_code=exc.reason_code)
        except (ValueError, TypeError, KeyError):
            self._send_error("Analysis needs a registered source in this Goal.", status=400, error_code="research_analysis_scope_invalid")
        except (OSError, RuntimeError):
            self._send_error("Analysis could not be read. Research history remains available.", status=503, error_code="research_analysis_unavailable")

    def _research_analysis_write(self):
        try:
            if getattr(self.server, "analysis_source_namespace", None) is not None and self._research_analysis_query():
                raise ValueError("Only the bound analysis source selector is accepted")
            body = self._read_json(max_bytes=4096)
            cancel = urlparse(self.path).path.endswith("/cancel")
            required = {"goal_id", "pack_id", "analysis_key"}
            allowed = required if cancel else required | {"retry"}
            if body.keys() - allowed or not required <= body.keys() or any(not isinstance(body[k], str) or not body[k] for k in required):
                raise ValueError("Only registered Goal/pack/key operations are accepted")
            if not cancel and type(body.get("retry", False)) is not bool:
                raise ValueError("Retry must be an explicit boolean")
            if getattr(self.server, "analysis_source_namespace", None) is None:
                if linked_goal_source(self.server, body["goal_id"]) is not None:
                    if self._research_analysis_query(body["goal_id"]):
                        raise ValueError("Only the pinned source selector is accepted")
            runtime = self._research_analysis_scope(body["goal_id"])
            if runtime is None:
                return
            args = dict(runtime_root=runtime, owner_goal_id=body["goal_id"],
                pack_id=body["pack_id"], analysis_key=body["analysis_key"])
            self._research_analysis_verify_source_goal(runtime, body["goal_id"], body["pack_id"])
            # The server's existing threaded request is the asynchronous host.
            # Provider key/claim, source verification and cancellation
            # remain with benchmark-toolkit; nothing executes on GET.
            job = store.cancel_pack(**args) if cancel else store.execute_pack(**args, retry=body.get("retry", False))
            self._send_json({"ok": True, "job": job})
        except store.PackUnavailable as exc:
            self._send_error("Analysis source changed or is unavailable; refresh before retrying.", status=409, error_code=exc.reason_code)
        except (ValueError, TypeError, KeyError):
            self._send_error("Analysis request was not accepted. Refresh its source and retry.", status=400, error_code="research_analysis_request_invalid")
        except (OSError, RuntimeError):
            self._send_error("Analysis did not complete. Read its status before retrying.", status=503, error_code="research_analysis_unavailable")
