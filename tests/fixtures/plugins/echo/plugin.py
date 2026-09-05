"""Safe sample plugin used by the test suite and as a template.

``create_plugin`` receives a PluginContext (plugin id, version, a private
data directory, and a log callable) and returns a mapping from every tool
name declared in plugin.json to a callable. Arguments arrive as keywords
matching the declared parameters; the return value must be JSON.
"""


def create_plugin(context):
    context.log("echo plugin ready")

    def echo(text, repeat=None):
        count = 1 if repeat is None else max(1, min(int(repeat), 5))
        return {"echo": text[:200] * count, "plugin": context.plugin_id}

    return {"echo": echo}
