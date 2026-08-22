from __future__ import annotations

from copy import deepcopy

from app.core.models import (
    Request,
    RiskLevel,
    ToolDefinition,
)
from app.tools.executor import ToolExecutor
from app.tools.routing import (
    DeterministicToolRouter,
)


TOOL_SCHEMAS = {
    "list_memories": {
        "active_only": {
            "type": "boolean",
        },
        "limit": {
            "type": "integer",
        },
    },
    "search_memories": {
        "query": {
            "type": "string",
        },
        "limit": {
            "type": "integer",
        },
    },
    "list_windows_applications": {},
    "list_windows_processes": {
        "name": {
            "type": "string",
        },
        "limit": {
            "type": "integer",
        },
    },
    "get_windows_system_info": {},
    "list_tasks": {
        "status": {
            "type": "string",
        },
        "limit": {
            "type": "integer",
        },
    },
    "get_task": {
        "task_id": {
            "type": "string",
        },
    },
    "diagnostics_health": {},
    "diagnostics_events": {
        "limit": {
            "type": "integer",
        },
        "level": {
            "type": "string",
        },
        "component": {
            "type": "string",
        },
    },
    "diagnostics_metrics": {},
}


REQUIRED = {
    "search_memories": [
        "query",
    ],
    "get_task": [
        "task_id",
    ],
}


def make_executor(
    *,
    override_name: str | None = None,
    risk_level: RiskLevel = RiskLevel.READ_ONLY,
    requires_confirmation: bool = False,
) -> ToolExecutor:
    executor = ToolExecutor()

    for name in TOOL_SCHEMAS:
        risk = (
            risk_level
            if name == override_name
            else RiskLevel.READ_ONLY
        )

        confirm = (
            requires_confirmation
            if name == override_name
            else False
        )

        executor.register(
            ToolDefinition(
                name=name,
                description=f"Test tool: {name}",
                risk_level=risk,
                requires_confirmation=confirm,
            ),
            lambda: None,
        )

    return executor


def make_schemas():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Test tool: {name}",
                "parameters": {
                    "type": "object",
                    "properties": deepcopy(
                        properties
                    ),
                    "required": deepcopy(
                        REQUIRED.get(
                            name,
                            [],
                        )
                    ),
                },
            },
        }
        for name, properties
        in TOOL_SCHEMAS.items()
    ]


def route(
    text: str,
    *,
    executor: ToolExecutor | None = None,
    schemas=None,
    metadata=None,
):
    return DeterministicToolRouter().route(
        Request(
            text,
            metadata=dict(
                metadata
                or {}
            ),
        ),
        tool_executor=(
            executor
            or make_executor()
        ),
        tool_schemas=(
            make_schemas()
            if schemas is None
            else schemas
        ),
    )


def test_routes_windows_system_information(
) -> None:
    result = route(
        "Bu bilgisayar\u0131n "
        "sistem bilgilerini g\u00f6ster."
    )

    assert result is not None
    assert (
        result.tool_name
        == "get_windows_system_info"
    )
    assert result.parameters == {}


def test_routes_pc_specifications(
) -> None:
    result = route(
        "Bilgisayar\u0131m\u0131n "
        "\u00f6zelliklerini g\u00f6ster."
    )

    assert result is not None
    assert (
        result.tool_name
        == "get_windows_system_info"
    )


def test_routes_approved_windows_applications(
) -> None:
    result = route(
        "JARVIS'in a\u00e7abildi\u011fi "
        "uygulamalar\u0131 listele."
    )

    assert result is not None
    assert (
        result.tool_name
        == "list_windows_applications"
    )
    assert result.parameters == {}


def test_routes_windows_processes(
) -> None:
    result = route(
        "\u00c7al\u0131\u015fan Windows "
        "i\u015flemlerini g\u00f6ster."
    )

    assert result is not None
    assert (
        result.tool_name
        == "list_windows_processes"
    )
    assert result.parameters == {}


def test_routes_memory_list(
) -> None:
    result = route(
        "Haf\u0131za kay\u0131tlar\u0131n\u0131 "
        "g\u00f6ster."
    )

    assert result is not None
    assert result.tool_name == "list_memories"
    assert result.parameters == {}


def test_routes_all_memory_records(
) -> None:
    result = route(
        "T\u00fcm haf\u0131za "
        "kay\u0131tlar\u0131n\u0131 g\u00f6ster."
    )

    assert result is not None
    assert result.tool_name == "list_memories"
    assert result.parameters == {
        "active_only": False,
    }


def test_routes_explicit_memory_search(
) -> None:
    result = route(
        "Haf\u0131zanda ara: "
        "\u00e7ocuk n\u00f6rolojisi"
    )

    assert result is not None
    assert (
        result.tool_name
        == "search_memories"
    )
    assert result.parameters == {
        "query": (
            "\u00e7ocuk n\u00f6rolojisi"
        ),
    }


def test_routes_task_list(
) -> None:
    result = route(
        "T\u00fcm g\u00f6revleri listele."
    )

    assert result is not None
    assert result.tool_name == "list_tasks"
    assert result.parameters == {}


def test_routes_task_by_uuid(
) -> None:
    task_id = (
        "12345678-1234-"
        "5678-1234-"
        "567812345678"
    )

    result = route(
        (
            "G\u00f6rev "
            f"{task_id} "
            "detaylar\u0131n\u0131 g\u00f6ster."
        )
    )

    assert result is not None
    assert result.tool_name == "get_task"
    assert result.parameters == {
        "task_id": task_id,
    }


def test_routes_diagnostics_health(
) -> None:
    result = route(
        "JARVIS sa\u011fl\u0131\u011f\u0131n\u0131 "
        "kontrol et."
    )

    assert result is not None
    assert (
        result.tool_name
        == "diagnostics_health"
    )


def test_routes_diagnostics_events(
) -> None:
    result = route(
        "Diagnostik olaylar\u0131 "
        "listele."
    )

    assert result is not None
    assert (
        result.tool_name
        == "diagnostics_events"
    )


def test_routes_diagnostics_metrics(
) -> None:
    result = route(
        "JARVIS metriklerini g\u00f6ster."
    )

    assert result is not None
    assert (
        result.tool_name
        == "diagnostics_metrics"
    )


def test_does_not_route_instructional_system_question(
) -> None:
    result = route(
        "Windows sistem bilgilerini "
        "nas\u0131l g\u00f6rebilirim?"
    )

    assert result is None


def test_does_not_route_generic_installed_application_request(
) -> None:
    result = route(
        "Bilgisayardaki uygulamalar\u0131 "
        "g\u00f6ster."
    )

    assert result is None


def test_does_not_route_ambiguous_process_request(
) -> None:
    result = route(
        "\u0130\u015flemleri g\u00f6ster."
    )

    assert result is None


def test_does_not_route_memory_search_without_query(
) -> None:
    result = route(
        "Haf\u0131zanda ara:"
    )

    assert result is None


def test_does_not_route_passive_memory_filter(
) -> None:
    result = route(
        "Pasif haf\u0131za "
        "kay\u0131tlar\u0131n\u0131 g\u00f6ster."
    )

    assert result is None


def test_does_not_route_filtered_task_list(
) -> None:
    result = route(
        "Tamamlanan g\u00f6revleri "
        "g\u00f6ster."
    )

    assert result is None


def test_does_not_route_task_without_valid_uuid(
) -> None:
    result = route(
        "G\u00f6rev abc123 "
        "detaylar\u0131n\u0131 g\u00f6ster."
    )

    assert result is None


def test_does_not_route_filtered_diagnostic_events(
) -> None:
    result = route(
        "Hata diagnostik olaylar\u0131n\u0131 "
        "g\u00f6ster."
    )

    assert result is None


def test_routes_independently_of_the_active_provider(
) -> None:
    # Every deterministic candidate is a READ_ONLY observation tool and
    # the permission engine still authorizes the call, so routing is not
    # tied to which model provider happens to be active.
    result = route(
        "JARVIS metriklerini g\u00f6ster.",
    )

    assert result is not None
    assert result.tool_name == "diagnostics_metrics"


def test_request_can_disable_routing(
) -> None:
    result = route(
        "JARVIS metriklerini g\u00f6ster.",
        metadata={
            "deterministic_tool_routing": False,
        },
    )

    assert result is None


def test_does_not_route_unexposed_tool(
) -> None:
    schemas = [
        schema
        for schema in make_schemas()
        if (
            schema["function"]["name"]
            != "diagnostics_metrics"
        )
    ]

    result = route(
        "JARVIS metriklerini g\u00f6ster.",
        schemas=schemas,
    )

    assert result is None


def test_does_not_route_non_read_only_tool(
) -> None:
    executor = make_executor(
        override_name="diagnostics_metrics",
        risk_level=RiskLevel.LOW,
    )

    result = route(
        "JARVIS metriklerini g\u00f6ster.",
        executor=executor,
    )

    assert result is None


def test_does_not_route_confirmation_required_tool(
) -> None:
    executor = make_executor(
        override_name="diagnostics_health",
        requires_confirmation=True,
    )

    result = route(
        "JARVIS sa\u011fl\u0131\u011f\u0131n\u0131 "
        "kontrol et.",
        executor=executor,
    )

    assert result is None


def test_rejects_schema_missing_required_parameter(
) -> None:
    schemas = make_schemas()

    for schema in schemas:
        function = schema["function"]

        if (
            function["name"]
            == "search_memories"
        ):
            function[
                "parameters"
            ]["required"] = [
                "query",
                "limit",
            ]

    result = route(
        "Haf\u0131zanda ara: JARVIS",
        schemas=schemas,
    )

    assert result is None


def test_rejects_candidate_parameter_not_in_schema(
) -> None:
    schemas = make_schemas()

    for schema in schemas:
        function = schema["function"]

        if (
            function["name"]
            == "search_memories"
        ):
            function[
                "parameters"
            ]["properties"].pop(
                "query"
            )
            function[
                "parameters"
            ]["required"] = []

    result = route(
        "Haf\u0131zanda ara: JARVIS",
        schemas=schemas,
    )

    assert result is None


def test_rejects_wrong_schema_parameter_type(
) -> None:
    schemas = make_schemas()

    for schema in schemas:
        function = schema["function"]

        if (
            function["name"]
            == "search_memories"
        ):
            function[
                "parameters"
            ]["properties"][
                "query"
            ]["type"] = "integer"

    result = route(
        "Haf\u0131zanda ara: JARVIS",
        schemas=schemas,
    )

    assert result is None
