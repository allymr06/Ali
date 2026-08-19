from app.core.engine import CoreEngine
from app.core.models import Context, Request


def test_core_engine_handles_request():
    engine = CoreEngine()
    request = Request("Merhaba JARVIS")

    response = engine.handle(request)

    assert response.request_id == request.request_id
    assert "Merhaba JARVIS" in response.text


def test_core_engine_creates_context_when_missing():
    engine = CoreEngine()
    request = Request("Test")

    response = engine.handle(request)

    assert response is not None


def test_core_engine_accepts_existing_context():
    engine = CoreEngine()
    request = Request("Test")
    context = Context()

    response = engine.handle(
        request,
        context,
    )

    assert response.request_id == request.request_id


def test_core_engine_supports_custom_responder():
    def responder(request, context):
        return f"Özel cevap: {request.text}"

    engine = CoreEngine(responder=responder)
    request = Request("Sistem testi")

    response = engine.handle(request)

    assert response.text == "Özel cevap: Sistem testi"
    assert response.request_id == request.request_id