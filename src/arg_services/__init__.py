import logging
import multiprocessing as mp
import traceback
from collections.abc import Callable, Collection, Iterable, Mapping
from concurrent import futures
from operator import attrgetter
from typing import Any

import grpc
from grpc_reflection.v1alpha import reflection

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def handle_except(ex: Exception, ctx: grpc.ServicerContext | None) -> None:
    """Handler that can be called when handling an exception.

    It will pass the traceback to the gRPC client and abort the context.

    Args:
        ex: Exception that occured.
        ctx: Current gRPC context.
    """

    msg = "".join(traceback.TracebackException.from_exception(ex).format())

    if ctx is None:
        raise ex
    else:
        ctx.abort(grpc.StatusCode.UNKNOWN, msg)


def require_any(
    attrs: Collection[str],
    obj: Any,
    ctx: grpc.ServicerContext | None = None,
    parent: str = "request",
) -> None:
    """Verify that any of the required arguments are supplied by the client.

    If arguments are missing, the context will be aborted

    Args:
        attrs: Names of the required parameters.
        obj: Current request message (e.g., a subclass of google.protobuf.message.Message).
        ctx: Current gRPC context.
        parent: Name of the parent message. Only used to compose more helpful error.
    """
    func = attrgetter(*attrs)
    attr_result = func(obj)

    if len(attrs) == 1:
        attr_result = [attr_result]

    if not any(attr_result):
        msg = f"The message '{parent}' requires the following attributes: {attrs}."

        if ctx is None:
            raise ValueError(msg)
        else:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, msg)


def require_all(
    attrs: Collection[str],
    obj: Any,
    ctx: grpc.ServicerContext | None = None,
    parent: str = "request",
) -> None:
    """Verify that all required arguments are supplied by the client.

    If arguments are missing, the context will be aborted

    Args:
        attrs: Names of the required parameters.
        obj: Current request message (e.g., a subclass of google.protobuf.message.Message).
        ctx: Current gRPC context.
        parent: Name of the parent message. Only used to compose more helpful error.
    """
    func = attrgetter(*attrs)
    attr_result = func(obj)

    if len(attrs) == 1:
        attr_result = [attr_result]

    if not all(attr_result):
        msg = f"The message '{parent}' requires the following attributes: {attrs}."

        if ctx is None:
            raise ValueError(msg)
        else:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, msg)


def require_all_repeated(
    key: str,
    attrs: Collection[str],
    obj: Any,
    ctx: grpc.ServicerContext | None = None,
) -> None:
    """Verify that all required arguments are supplied by the client.

    If arguments are missing, the context will be aborted

    Args:
        key: Name of repeated attribute.
        attrs: Names of the required parameters.
        obj: Current request message (e.g., a subclass of google.protobuf.message.Message).
        ctx: Current gRPC context.
    """
    func = attrgetter(key)

    for item in func(obj):
        require_all(attrs, item, ctx, key)


def require_any_repeated(
    key: str,
    attrs: Collection[str],
    obj: Any,
    ctx: grpc.ServicerContext | None = None,
) -> None:
    """Verify that any required arguments are supplied by the client.

    If arguments are missing, the context will be aborted

    Args:
        key: Name of repeated attribute.
        attrs: Names of the required parameters.
        obj: Current request message (e.g., a subclass of google.protobuf.message.Message).
        ctx: Current gRPC context.
    """
    func = attrgetter(key)

    for item in func(obj):
        require_any(attrs, item, ctx, key)


def forbid_all(
    attrs: Collection[str],
    obj: Any,
    ctx: grpc.ServicerContext | None = None,
    parent: str = "request",
) -> None:
    """Verify that no illegal combination of arguments is provided by the client.

    Args:
        attrs: Names of parameters which cannot occur at the same time.
        obj: Current request message (e.g., a subclass of google.protobuf.message.Message).
        ctx: Current gRPC context.
        parent: Name of the parent message. Only used to compose more helpful error.
    """
    func = attrgetter(*attrs)
    attr_result = func(obj)

    if len(attrs) == 1:
        attr_result = [attr_result]

    if all(attr_result):
        error = f"The message '{parent}' is not allowed to allowed to have the following parameter combination: {attrs}."

        if ctx is None:
            raise ValueError(error)
        else:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, error)


def full_service_name(pkg, service: str) -> str:
    return pkg.DESCRIPTOR.services_by_name[service].full_name


def _serve_single(
    address: str,
    add_services: Callable[[grpc.Server], None],
    threads: int,
    reflection_services: Iterable[str],
    worker_id: int,
    options: Mapping[str, Any] | None = None,
) -> None:
    """Helper function to start a server for a single process.

    Args:
        address: Complete address consisting of hostname and port (e.g., `127.0.0.1:8000`)
        add_services: Function to inject the gRPC services into the server instance.
        threads: Number of workers for the ThreadPoolExecutor.
        reflection_services: Name of all services this server offers.
    """
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=threads),
        options=list(options.items()) if options else None,
    )
    add_services(server)

    if reflection_services:
        reflection.enable_server_reflection(
            [*reflection_services, reflection.SERVICE_NAME], server
        )

    server.add_insecure_port(address)
    server.start()

    logger.info(f"Worker {worker_id} serving on '{address}'.")

    server.wait_for_termination()


def serve(
    address: str,
    add_services: Callable[[grpc.Server], None],
    reflection_services: Iterable[str],
    threads: int = 1,
    options: Mapping[str, Any] | None = None,
) -> None:
    """Serve one or multiple gRPC services, optionally using multiprocessing.

    Args:
        address: Connection string the server should listen on.
            Should be given in the form `host:port`, example: `127.0.0.1:6789`.
            If multiple processes should be started, use the notation `host:port1,host:port2,...`
        add_services: Function to inject the gRPC services into the server instance.
        reflection_services: List of services this server supports.
            Use the provided function `arg_services_helper.full_service_name` to get the correct names.
        threads: Number of workers in the gRPC thread pool.
            If multiple processes are used, each process uses the assigned number of threads.
    """

    # Remove protocols like' ipv4:' or 'ipv6:' from the address
    if address.count(":") == address.count(",") + 2:
        protocol_end = address.index(":") + 1
        address = address[protocol_end:]

    urls = [url.strip() for url in address.split(",")]

    if len(urls) < 1:
        raise ValueError("No address given.")
    elif len(urls) == 1:
        _serve_single(urls[0], add_services, threads, reflection_services, 1, options)
    else:
        workers = []

        for worker_id, url in enumerate(urls):
            worker = mp.Process(
                target=_serve_single,
                args=(
                    url,
                    add_services,
                    threads,
                    reflection_services,
                    worker_id,
                    options,
                ),
            )
            worker.start()
            workers.append(worker)

        logger.info("Workers are starting, please connect to")
        logger.info(f"ipv4:{address}")

        for worker in workers:
            worker.join()
