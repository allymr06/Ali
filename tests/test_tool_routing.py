from __future__ import annotations

from app.core.models import (
    Request,
    RiskLevel,
    ToolDefinition,
)
from app.tools.executor import ToolExecutor
from app.tools.routing import (
    DeterministicToolRouter,
)


def make_executor(
    *,
    risk_level: RiskLevel = RiskLevel.READ_ONLY,
    requires_confirmation: bool = False,
) -> ToolExecutor:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="get_windows_system_info",
            description="Yerel Windows sistem bilgilerini oku.",
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
        ),
        lambda: {"release": "11"},
    )

    return executor


def make_schema(
    *,
    properties=None,
    required=None,
):
    return [
        {
            "type": "function",
            "function": {
                "name": "get_windows_system_info",
                "description": "Sistem bilgilerini oku.",
                "parameters": {
                    "type": "object",
                    "properties": (
                        {}
                        if properties is None
                        else properties
                    ),
                    "required": (
                        []
                        if required is None
                        else required
                    ),
                },
            },
        }
    ]


def route(text, **kwargs):
    return DeterministicToolRouter().route(
        Request(text),
        provider_name=kwargs.get(
            "provider_name",
            "ollama",
        ),
        tool_executor=kwargs.get(
            "tool_executor",
            make_executor(),
        ),
        tool_schemas=kwargs.get(
            "tool_schemas",
            make_schema(),
        ),
    )


def test_bu_bilgisayarin_sistem_bilgilerini_yonlendirir(
) -> None:
    result = route(
        "Bu bilgisayar\u0131n sistem bilgilerini g\u00f6ster."
    )

    assert result is not None
    assert result.tool_name == "get_windows_system_info"
    assert result.parameters == {}


def test_bilgisayarimin_ozelliklerini_yonlendirir(
) -> None:
    result = route(
        "Bilgisayar\u0131m\u0131n "
        "\u00f6zelliklerini g\u00f6ster."
    )

    assert result is not None


def test_pc_ozelliklerini_yonlendirir(
) -> None:
    result = route(
        "Bu PC'nin \u00f6zelliklerini g\u00f6ster."
    )

    assert result is not None


def test_sistem_bilgilerini_kontrol_et_istegini_yonlendirir(
) -> None:
    result = route(
        "Sistem bilgilerini kontrol et."
    )

    assert result is not None


def test_nasil_sorusunu_yonlendirmez(
) -> None:
    result = route(
        "Windows sistem bilgilerini nas\u0131l g\u00f6rebilirim?"
    )

    assert result is None


def test_nedir_sorusunu_yonlendirmez(
) -> None:
    result = route(
        "Windows sistem bilgisi nedir?"
    )

    assert result is None


def test_alakasiz_windows_sorusunu_yonlendirmez(
) -> None:
    result = route(
        "Windows 11 iyi mi?"
    )

    assert result is None


def test_baska_provider_icin_yonlendirmez(
) -> None:
    result = route(
        "Bu bilgisayar\u0131n sistem bilgilerini g\u00f6ster.",
        provider_name="openai",
    )

    assert result is None


def test_istek_uzerinden_yonlendirme_kapatilabilir(
) -> None:
    request = Request(
        "Bu bilgisayar\u0131n sistem bilgilerini g\u00f6ster.",
        metadata={
            "deterministic_tool_routing": False,
        },
    )

    result = DeterministicToolRouter().route(
        request,
        provider_name="ollama",
        tool_executor=make_executor(),
        tool_schemas=make_schema(),
    )

    assert result is None


def test_expose_edilmeyen_araci_yonlendirmez(
) -> None:
    result = route(
        "Bu bilgisayar\u0131n sistem bilgilerini g\u00f6ster.",
        tool_schemas=[],
    )

    assert result is None


def test_read_only_olmayan_araci_yonlendirmez(
) -> None:
    result = route(
        "Bu bilgisayar\u0131n sistem bilgilerini g\u00f6ster.",
        tool_executor=make_executor(
            risk_level=RiskLevel.LOW,
        ),
    )

    assert result is None


def test_parametreli_araci_yonlendirmez(
) -> None:
    result = route(
        "Bu bilgisayar\u0131n sistem bilgilerini g\u00f6ster.",
        tool_schemas=make_schema(
            properties={
                "hedef": {
                    "type": "string",
                }
            },
        ),
    )

    assert result is None



def test_onay_gerektiren_araci_yonlendirmez(
) -> None:
    result = route(
        "Bu bilgisayar\u0131n "
        "sistem bilgilerini g\u00f6ster.",
        tool_executor=make_executor(
            requires_confirmation=True,
        ),
    )

    assert result is None
