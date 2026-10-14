"""Helpers for serving the Connect stubs with connectrpc.

Requires the `connectrpc` extra: `arg-services[connectrpc]`.
"""

from collections.abc import Awaitable, Callable, Iterator
from typing import Protocol

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from connectrpc_grpcreflect import (
    ServerReflectionASGIApplication,
    ServerReflectionService,
    ServerReflectionServiceSync,
    ServerReflectionWSGIApplication,
)
from google.protobuf import descriptor_pool
from google.protobuf.descriptor import FileDescriptor
from protobuf import DescService
from protobuf.wkt import FileDescriptorProto, FileDescriptorSet

from arg_services.validation import ValidationError

__all__ = [
    "ConnectApplication",
    "ValidationInterceptor",
    "reflection_app",
    "reflection_app_sync",
]


class ValidationInterceptor:
    """Raise `INVALID_ARGUMENT` for unary RPCs that raise a `ValidationError`.

    Works for both ASGI and WSGI applications.
    """

    async def intercept_unary[Req, Res](
        self,
        call_next: Callable[[Req, RequestContext[Req, Res]], Awaitable[Res]],
        request: Req,
        ctx: RequestContext[Req, Res],
        /,
    ) -> Res:
        """Intercept a unary RPC of an ASGI application."""
        try:
            return await call_next(request, ctx)
        except ValidationError as e:
            raise ConnectError(Code.INVALID_ARGUMENT, str(e)) from e

    def intercept_unary_sync[Req, Res](
        self,
        call_next: Callable[[Req, RequestContext[Req, Res]], Res],
        request: Req,
        ctx: RequestContext[Req, Res],
        /,
    ) -> Res:
        """Intercept a unary RPC of a WSGI application."""
        try:
            return call_next(request, ctx)
        except ValidationError as e:
            raise ConnectError(Code.INVALID_ARGUMENT, str(e)) from e


class ConnectApplication(Protocol):
    """A generated Connect ASGI or WSGI application."""

    @property
    def path(self) -> str:
        """The fully qualified service name prefixed by a slash."""
        ...


def _files(file: FileDescriptor, seen: set[str]) -> Iterator[FileDescriptor]:
    """Yield the file and its transitive dependencies, dependencies first."""
    if file.name in seen:
        return

    seen.add(file.name)

    for dep in file.dependencies:
        yield from _files(dep, seen)

    yield file


def _desc_services(apps: tuple[ConnectApplication, ...]) -> list[DescService]:
    """Convert the google.protobuf descriptors of the served services to protobuf-py ones.

    Required since `connectrpc_grpcreflect` only understands protobuf-py descriptors.
    """
    pool = descriptor_pool.Default()
    names = [app.path.removeprefix("/") for app in apps]
    seen: set[str] = set()
    registry = FileDescriptorSet(
        file=[
            FileDescriptorProto.from_binary(file.serialized_pb)
            for name in names
            for file in _files(pool.FindServiceByName(name).file, seen)
        ]
    ).to_registry()
    services: list[DescService] = []

    for name in names:
        if (desc := registry.service(name)) is None:
            raise LookupError(f"Service '{name}' is missing after the conversion.")

        services.append(desc)

    return services


def reflection_app(*apps: ConnectApplication) -> ServerReflectionASGIApplication:
    """Create a gRPC reflection ASGI application for the given Connect applications.

    Args:
        apps: Generated Connect applications, e.g., `NlpServiceASGIApplication`.

    Returns:
        ASGI application to mount at its `path` next to the given applications.
    """
    return ServerReflectionASGIApplication(
        ServerReflectionService(*_desc_services(apps))
    )


def reflection_app_sync(*apps: ConnectApplication) -> ServerReflectionWSGIApplication:
    """Create a gRPC reflection WSGI application for the given Connect applications.

    Args:
        apps: Generated Connect applications, e.g., `NlpServiceWSGIApplication`.

    Returns:
        WSGI application to mount at its `path` next to the given applications.
    """
    return ServerReflectionWSGIApplication(
        ServerReflectionServiceSync(*_desc_services(apps))
    )
