"""Helpers for serving the gRPC stubs with grpcio.

Requires the `grpcio` extra: `arg-services[grpcio]`.
"""

import logging
import multiprocessing as mp
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from concurrent import futures
from typing import Never, Protocol, override, runtime_checkable

import grpc
from google.protobuf.descriptor import FileDescriptor
from grpc_reflection.v1alpha.reflection import SERVICE_NAME, enable_server_reflection

from arg_services.validation import ValidationError

__all__ = [
    "ExceptionInterceptor",
    "GrpcOptions",
    "ValidationInterceptor",
    "serve",
]

type GrpcOptions = Mapping[str, str | int]
"""Channel arguments passed to the server, e.g., `{"grpc.max_receive_message_length": 2**30}`."""

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@runtime_checkable
class _StatusContext(Protocol):
    """The status accessor of grpc's servicer context, missing in the type stubs."""

    def code(self) -> grpc.StatusCode | None: ...


class _UnaryInterceptor(grpc.ServerInterceptor, ABC):
    """Base class for interceptors that handle errors raised by unary RPCs."""

    @abstractmethod
    def _handle(self, error: Exception, ctx: grpc.ServicerContext) -> Never: ...

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
            except Exception as e:  # noqa: BLE001, re-raised or converted by _handle
                self._handle(e, ctx)

        return grpc.unary_unary_rpc_method_handler(
            intercepted,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


class ValidationInterceptor(_UnaryInterceptor):
    """Abort unary RPCs with `INVALID_ARGUMENT` if they raise a `ValidationError`."""

    @override
    def _handle(self, error: Exception, ctx: grpc.ServicerContext) -> Never:
        if isinstance(error, ValidationError):
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))

        raise error


class ExceptionInterceptor(_UnaryInterceptor):
    """Abort unary RPCs with `UNKNOWN` and pass the traceback of unhandled exceptions to the client.

    Validation errors and RPCs aborted by the service are left untouched,
    so the order relative to `ValidationInterceptor` does not matter.
    """

    @override
    def _handle(self, error: Exception, ctx: grpc.ServicerContext) -> Never:
        if isinstance(error, ValidationError) or (
            isinstance(ctx, _StatusContext) and ctx.code() is not None
        ):
            raise error

        ctx.abort(grpc.StatusCode.UNKNOWN, "".join(traceback.format_exception(error)))


def _serve_single(
    address: str,
    add_services: Callable[[grpc.Server], None],
    threads: int,
    reflection_services: Collection[str],
    interceptors: Sequence[grpc.ServerInterceptor],
    options: GrpcOptions | None,
) -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=threads),
        interceptors=list(interceptors),
        options=list(options.items()) if options else None,
    )
    add_services(server)

    if reflection_services:
        enable_server_reflection([*reflection_services, SERVICE_NAME], server)

    server.add_insecure_port(address)
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
    for scheme in ("ipv4:", "ipv6:", "dns:"):
        address = address.removeprefix(scheme)

    urls = [url for url in map(str.strip, address.split(",")) if url]

    if not urls:
        raise ValueError("No address given.")

    services = [
        service.full_name
        for file in reflection
        for service in file.services_by_name.values()
    ]

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

    logger.info("Workers are starting, please connect to 'ipv4:%s'.", address)

    for worker in workers:
        worker.join()
