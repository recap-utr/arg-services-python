"""Helpers for serving the gRPC stubs with grpcio.

Requires the `grpcio` extra: `arg-services[grpcio]`.
"""

import logging
import multiprocessing as mp
import traceback
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from concurrent import futures
from typing import Protocol, cast, override, runtime_checkable

import grpc
from google.protobuf.descriptor import FileDescriptor
from grpc_reflection.v1alpha.reflection import SERVICE_NAME, enable_server_reflection

from arg_services.validation import ValidationError

__all__ = [
    "AsyncExceptionInterceptor",
    "AsyncValidationInterceptor",
    "ExceptionInterceptor",
    "GrpcOptions",
    "ValidationInterceptor",
    "serve",
    "serve_async",
]

type GrpcOptions = Mapping[str, str | int]
"""Channel arguments passed to the server, e.g., `{"grpc.max_receive_message_length": 2**30}`."""

type _Status = tuple[grpc.StatusCode, str]

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class _ValidationStatus:
    def _status(self, error: Exception) -> _Status | None:
        if isinstance(error, ValidationError):
            return grpc.StatusCode.INVALID_ARGUMENT, str(error)

        return None


class _ExceptionStatus:
    def _status(self, error: Exception) -> _Status | None:
        if isinstance(error, ValidationError):
            return None

        return grpc.StatusCode.UNKNOWN, "".join(traceback.format_exception(error))


def _unary_handler[Req, Res](
    handler: grpc.RpcMethodHandler[Req, Res],
    intercepted: Callable[[Req, grpc.ServicerContext], Res]
    | Callable[[Req, grpc.aio.ServicerContext[Req, Res]], Awaitable[Res]],
) -> grpc.RpcMethodHandler[Req, Res]:
    return grpc.unary_unary_rpc_method_handler(
        intercepted,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )


@runtime_checkable
class _StatusContext(Protocol):
    """The status accessor of grpc's servicer context, missing in the type stubs."""

    def code(self) -> grpc.StatusCode | None: ...


class _UnaryInterceptor(grpc.ServerInterceptor, ABC):
    """Base class for interceptors that abort unary RPCs based on the raised error."""

    @abstractmethod
    def _status(self, error: Exception) -> _Status | None: ...

    @override
    def intercept_service[Req, Res](
        self,
        continuation: Callable[
            [grpc.HandlerCallDetails], grpc.RpcMethodHandler[Req, Res] | None
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Req, Res] | None:
        handler = continuation(handler_call_details)

        if handler is None or handler.unary_unary is None:
            return handler

        behavior = handler.unary_unary

        def intercepted(request: Req, ctx: grpc.ServicerContext) -> Res:
            try:
                return behavior(request, ctx)
            except Exception as e:
                # an aborted context has a status code, the error must not override it
                aborted = isinstance(ctx, _StatusContext) and ctx.code() is not None

                if aborted or (status := self._status(e)) is None:
                    raise

                ctx.abort(*status)

        return _unary_handler(handler, intercepted)


class _AsyncUnaryInterceptor(grpc.aio.ServerInterceptor, ABC):
    """Base class for async interceptors that abort unary RPCs based on the raised error."""

    @abstractmethod
    def _status(self, error: Exception) -> _Status | None: ...

    @override
    async def intercept_service[Req, Res](
        self,
        continuation: Callable[
            [grpc.HandlerCallDetails],
            Awaitable[grpc.RpcMethodHandler[Req, Res] | None],
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Req, Res] | None:
        handler = await continuation(handler_call_details)

        if handler is None or handler.unary_unary is None:
            return handler

        # the type stubs reuse the sync handler type for async servers
        behavior = cast(
            Callable[[Req, grpc.aio.ServicerContext[Req, Res]], Awaitable[Res]],
            handler.unary_unary,
        )

        async def intercepted(
            request: Req, ctx: grpc.aio.ServicerContext[Req, Res]
        ) -> Res:
            try:
                return await behavior(request, ctx)
            except grpc.aio.AbortError:
                raise
            except Exception as e:
                if (status := self._status(e)) is None:
                    raise

                await ctx.abort(*status)

        return _unary_handler(handler, intercepted)


class ValidationInterceptor(_ValidationStatus, _UnaryInterceptor):
    """Abort unary RPCs with `INVALID_ARGUMENT` if they raise a `ValidationError`."""


class AsyncValidationInterceptor(_ValidationStatus, _AsyncUnaryInterceptor):
    """Async variant of `ValidationInterceptor` for `serve_async`."""


class ExceptionInterceptor(_ExceptionStatus, _UnaryInterceptor):
    """Abort unary RPCs with `UNKNOWN` and pass the traceback of unhandled exceptions to the client.

    Validation errors and RPCs aborted by the service are left untouched,
    so the order relative to `ValidationInterceptor` does not matter.
    """


class AsyncExceptionInterceptor(_ExceptionStatus, _AsyncUnaryInterceptor):
    """Async variant of `ExceptionInterceptor` for `serve_async`."""


def _strip_scheme(address: str) -> str:
    for scheme in ("ipv4:", "ipv6:", "dns:"):
        address = address.removeprefix(scheme)

    return address


def _service_names(files: Iterable[FileDescriptor]) -> list[str]:
    return [
        service.full_name
        for file in files
        for service in file.services_by_name.values()
    ]


def _options(options: GrpcOptions | None) -> list[tuple[str, str | int]] | None:
    return list(options.items()) if options else None


def _configure[S: (grpc.Server, grpc.aio.Server)](
    server: S,
    add_services: Callable[[S], None],
    reflection_services: Sequence[str],
    address: str,
) -> None:
    add_services(server)

    if reflection_services:
        enable_server_reflection([*reflection_services, SERVICE_NAME], server)

    server.add_insecure_port(address)


def _serve_single(
    address: str,
    add_services: Callable[[grpc.Server], None],
    threads: int,
    reflection_services: Sequence[str],
    interceptors: Sequence[grpc.ServerInterceptor],
    options: GrpcOptions | None,
) -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=threads),
        interceptors=list(interceptors),
        options=_options(options),
    )
    _configure(server, add_services, reflection_services, address)
    server.start()

    logger.info("Serving on '%s'.", address)

    server.wait_for_termination()


def serve(
    address: str,
    add_services: Callable[[grpc.Server], None],
    reflection: Iterable[FileDescriptor] = (),
    threads: int = 1,
    interceptors: Sequence[grpc.ServerInterceptor] = (),
    options: GrpcOptions | None = None,
) -> None:
    """Serve one or multiple gRPC services, optionally using multiprocessing.

    Args:
        address: Connection string the server should listen on.
            Should be given in the form `host:port`, example: `127.0.0.1:6789`.
            If multiple processes should be started, use the notation `host:port1,host:port2,...`
        add_services: Function to inject the gRPC services into the server instance.
            Must be picklable (e.g., a module-level function) if multiple processes are used.
        reflection: Generated modules whose services are exposed via reflection, e.g., `[nlp_pb2.DESCRIPTOR]`.
        threads: Number of workers in the gRPC thread pool.
            If multiple processes are used, each process uses the assigned number of threads.
        interceptors: Server interceptors, e.g., `ValidationInterceptor()` and `ExceptionInterceptor()`.
            Must be picklable if multiple processes are used.
        options: Channel arguments passed to the server.
    """
    urls = [url for url in map(str.strip, _strip_scheme(address).split(",")) if url]

    if not urls:
        raise ValueError("No address given.")

    services = _service_names(reflection)

    if len(urls) == 1:
        _serve_single(urls[0], add_services, threads, services, interceptors, options)
        return

    workers = [
        mp.Process(
            target=_serve_single,
            args=(url, add_services, threads, services, interceptors, options),
        )
        for url in urls
    ]

    for worker in workers:
        worker.start()

    logger.info("Workers are starting, please connect to '%s'.", address)

    for worker in workers:
        worker.join()


async def serve_async(
    address: str,
    add_services: Callable[[grpc.aio.Server], None],
    reflection: Iterable[FileDescriptor] = (),
    interceptors: Sequence[grpc.aio.ServerInterceptor] = (),
    options: GrpcOptions | None = None,
) -> None:
    """Serve one or multiple gRPC services with asyncio in the current process.

    The servicers must implement their methods with `async def`.

    Args:
        address: Connection string the server should listen on, e.g., `127.0.0.1:6789`.
        add_services: Function to inject the gRPC services into the server instance.
        reflection: Generated modules whose services are exposed via reflection, e.g., `[nlp_pb2.DESCRIPTOR]`.
        interceptors: Server interceptors, e.g., `AsyncValidationInterceptor()` and `AsyncExceptionInterceptor()`.
        options: Channel arguments passed to the server.
    """
    address = _strip_scheme(address).strip()
    server = grpc.aio.server(interceptors=interceptors, options=_options(options))
    _configure(server, add_services, _service_names(reflection), address)
    await server.start()

    logger.info("Serving on '%s'.", address)

    await server.wait_for_termination()
