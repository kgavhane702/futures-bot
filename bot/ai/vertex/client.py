from typing import Dict, Any, Optional

from ..config import (
    VERTEX_ENABLED,
    VERTEX_PROJECT,
    VERTEX_LOCATION,
    VERTEX_ENDPOINT_ID,
)


class VertexPredictor:
    def __init__(self):
        self.ready = False
        self.endpoint = None
        if not VERTEX_ENABLED:
            return
        try:
            # Lazy import to avoid dependency unless enabled
            from google.cloud import aiplatform  # type: ignore
            aiplatform.init(project=VERTEX_PROJECT, location=VERTEX_LOCATION)
            if VERTEX_ENDPOINT_ID:
                self.endpoint = aiplatform.Endpoint(endpoint_name=VERTEX_ENDPOINT_ID)
                self.ready = True
        except Exception:
            self.ready = False

    def predict(self, instance: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.ready or not self.endpoint:
            return None
        try:
            # Expecting model to return {"side": "long|short|none", "confidence": 0..1}
            resp = self.endpoint.predict(instances=[instance])
            preds = resp.predictions or []
            if not preds:
                return None
            p = preds[0]
            # Normalize keys
            side = str(p.get("side") or p.get("label") or "none").lower()
            conf = float(p.get("confidence") or p.get("score") or 0.0)
            return {"side": side, "confidence": conf}
        except Exception:
            return None


VERTEX = VertexPredictor()


