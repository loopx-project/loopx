"""Owner Chat model catalog route; no browser-supplied commands or credentials."""
from urllib.parse import parse_qs, urlparse

from .chat_model_catalog import chat_model_catalog

CHAT_MODEL_CATALOG_PATH = "/api/chat/models"


class ModelCatalogRequestMixin:
    def _model_catalog(self) -> None:
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        values = query.get("endpoint_id", [])
        if set(query) != {"endpoint_id"} or len(values) != 1 or not values[0] or len(values[0]) > 80:
            self._send_error("Select one Chat endpoint for model discovery.", status=400,
                             error_code="invalid_chat_model_catalog_request")
            return
        self._send_json(chat_model_catalog(self.server.runtime_controller, values[0]))
