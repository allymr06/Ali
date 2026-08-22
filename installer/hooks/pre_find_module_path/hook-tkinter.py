"""Keep tkinter discoverable when the build host cannot initialize Tcl."""


def pre_find_module_path(hook_api) -> None:
    del hook_api
