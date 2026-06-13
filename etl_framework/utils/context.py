from contextvars import ContextVar

_run_id_var: ContextVar[str] = ContextVar("run_id", default="")


def set_run_id(run_id: str) -> None:
    _run_id_var.set(run_id)


def get_run_id() -> str:
    return _run_id_var.get()
