"""Runtime-neutral validation of request messages.

The checks raise a `ValidationError` that the interceptors in `arg_services.grpcio`
and `arg_services.connectrpc` turn into an `INVALID_ARGUMENT` error.
"""

from collections.abc import Callable, Collection, Iterable, Mapping

from google.protobuf.message import Message

__all__ = ["ValidationError", "forbid_all", "require_all", "require_any"]


class ValidationError(ValueError):
    """A message violates the requirements of a validation check."""


def _set_fields(msg: Message) -> dict[str, object]:
    """Map the names of all set fields to their values, respecting field presence."""
    return {field.name: value for field, value in msg.ListFields()}


def _is_set(set_fields: Mapping[str, object], path: str) -> bool:
    name, _, rest = path.partition(".")

    if name not in set_fields:
        return False

    if not rest:
        return True

    value = set_fields[name]

    return isinstance(value, Message) and _is_set(_set_fields(value), rest)


def _validate(
    fields: Collection[str],
    msgs: Iterable[Message],
    predicate: Callable[[Iterable[bool]], bool],
    requirement: str,
) -> None:
    for msg in msgs:
        set_fields = _set_fields(msg)

        if not predicate(_is_set(set_fields, field) for field in fields):
            raise ValidationError(
                f"'{msg.DESCRIPTOR.full_name}' {requirement} {list(fields)}."
            )


def _not_all(values: Iterable[bool]) -> bool:
    return not all(values)


def require_all(fields: Collection[str], *msgs: Message) -> None:
    """Verify that all given fields are set.

    Args:
        fields: Names of the required fields, may be dotted paths (e.g., `config.language`).
        msgs: Messages to check, e.g., the request or `*request.items` for repeated fields.

    Raises:
        ValidationError: If a field is missing.
    """
    _validate(fields, msgs, all, "requires all of the fields")


def require_any(fields: Collection[str], *msgs: Message) -> None:
    """Verify that at least one of the given fields is set.

    See `require_all` for details.
    """
    _validate(fields, msgs, any, "requires any of the fields")


def forbid_all(fields: Collection[str], *msgs: Message) -> None:
    """Verify that the given fields are not all set at the same time.

    See `require_all` for details.
    """
    _validate(fields, msgs, _not_all, "must not set all of the fields")
